import hashlib
import json
import uuid
import zipfile

import pytest
from sqlalchemy import func, select

from cricket.espn import PlayerResolutionError, find_players, resolve_espn_profile
from cricket.ingest import ImportError, import_archive, import_register, parse_match
from cricket.models import Base, Delivery, ExternalPlayerID, Innings, Match, SourceImport, Wicket
from cricket.queries import innings_totals, player_records
from tests.conftest import FIXTURES
from tests.helpers import archive, synthetic_match


def counts(session):
    return {
        name: session.scalar(select(func.count()).select_from(table))
        for name, table in Base.metadata.tables.items()
    }


def import_fixture(session, tmp_path, match_id, cricket_format):
    path = tmp_path / f"{match_id}.zip"
    with zipfile.ZipFile(path, "w") as output:
        output.write(FIXTURES / f"{match_id}.json", f"{match_id}.json")
    return import_archive(session, path, cricket_format)


def test_real_sources_idempotency_identity_and_totals(session, tmp_path):
    register = import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    results = [
        import_fixture(session, tmp_path, "1022353", "odi"),
        import_fixture(session, tmp_path, "1041615", "t20i"),
    ]
    before = counts(session)
    again = import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    assert again["unchanged"] and register["import_id"] == again["import_id"]
    for match_id, cricket_format in (("1022353", "odi"), ("1041615", "t20i")):
        assert import_fixture(session, tmp_path, match_id, cricket_format)["changed"] == 0
        payload = json.loads((FIXTURES / f"{match_id}.json").read_text())
        observed = innings_totals(session, match_id)
        for source_innings, total in zip(payload["innings"], observed, strict=True):
            source_total = sum(
                ball["runs"]["total"]
                for over in source_innings["overs"]
                for ball in over["deliveries"]
            )
            source_total += sum(source_innings.get("penalty_runs", {}).values())
            assert total["data_complete"] and total["runs"] == source_total
    assert counts(session) == before
    for espn_id, stable_id in (
        ("253802", "ba607b88"),
        ("34102", "740742ef"),
        ("625383", "462411b3"),
    ):
        player = resolve_espn_profile(
            session, {"athlete": {"id": espn_id, "displayName": "ignored name"}}
        )
        assert player.id == stable_id
        for cricket_format in ("odi", "t20i"):
            result = player_records(session, stable_id, cricket_format)
            assert result["coverage"]["matches"] == 1
            assert result["innings"] or result["bowling"]["deliveries"] > 0
            assert result["provenance"][0]["checksum"]
            assert result["coverage"]["scope"].startswith("available Cricsheet")
    for result in results:
        record = session.get(SourceImport, uuid.UUID(result["import_id"]))
        assert (
            record.source_revision and record.imported_at and record.date_start and record.date_end
        )


def test_fixture_checksums():
    for entry in json.loads((FIXTURES / "manifest.json").read_text()):
        assert (
            hashlib.sha256((FIXTURES / f"{entry['match_id']}.json").read_bytes()).hexdigest()
            == entry["file_checksum"]
        )


def test_extras_penalties_boundaries_runout_and_notout(session, tmp_path):
    result = import_archive(session, archive(tmp_path, synthetic_match()), "odi")
    assert result["changed"] == 1
    first, second = innings_totals(session, "990001")
    assert first["runs"] == 28 and first["extras"] == 11
    assert first["legal_balls"] == 8 and first["deliveries"] == 10
    assert second["runs"] == 6
    kohli = player_records(session, "ba607b88", "odi")
    assert kohli["batting"] == {"runs": 17, "legal_balls": 8, "dismissals": 0}
    assert kohli["innings"][0]["fours"] == 1 and kohli["innings"][0]["sixes"] == 1
    assert kohli["bowling"]["wickets"] == 1
    rohit = player_records(session, "740742ef", "odi")
    assert rohit["batting"] == {"runs": 0, "legal_balls": 0, "dismissals": 1}
    bumrah = player_records(session, "462411b3", "odi")
    assert bumrah["bowling"] == {
        "deliveries": 10,
        "legal_balls": 8,
        "runs_conceded": 19,
        "wickets": 0,
    }
    assert session.scalar(select(func.count(Wicket.id))) == 3
    assert session.scalar(select(func.count(Delivery.id)).where(Delivery.is_legal.is_(False))) == 2


def test_changed_match_replaces_children_without_duplicate_rows(session, tmp_path):
    payload = synthetic_match()
    import_archive(session, archive(tmp_path, payload), "odi")
    before = counts(session)
    payload["meta"]["revision"] = 2
    payload["innings"][0]["overs"][1]["deliveries"][0]["runs"] = {
        "batter": 3,
        "extras": 0,
        "total": 3,
    }
    result = import_archive(session, archive(tmp_path, payload), "odi")
    assert result["changed"] == 1
    after = counts(session)
    for table in ("matches", "players", "innings", "deliveries", "wickets", "match_players"):
        assert after[table] == before[table]
    assert after["source_imports"] == before["source_imports"] + 1
    assert innings_totals(session, "990001")[0]["runs"] == 29
    assert session.get(Match, "990001").source_revision == 2


@pytest.mark.parametrize("missing_mode", ["gap", "runs", "marker"])
def test_missing_deliveries_retained_and_excluded(session, tmp_path, missing_mode):
    payload = synthetic_match()
    if missing_mode == "gap":
        payload["innings"][0]["overs"][1]["over"] = 2
    elif missing_mode == "runs":
        payload["innings"][0]["overs"][0]["deliveries"][0].pop("runs")
    else:
        payload["info"]["missing"].append("deliveries")
    import_archive(session, archive(tmp_path, payload), "odi")
    inning = session.scalar(select(Innings).where(Innings.number == 1))
    assert not inning.data_complete and inning.missing
    assert session.get(Match, "990001").missing == payload["info"]["missing"]
    assert innings_totals(session, "990001")[0]["runs"] is None
    result = player_records(session, "ba607b88", "odi")
    assert result["coverage"]["excluded_incomplete_batting_innings"] == 1
    assert result["batting"]["runs"] == 0


def test_super_over_stored_separately_and_excluded_from_normal_statistics(session, tmp_path):
    payload = synthetic_match()
    payload["innings"].append(
        {
            "team": "India",
            "super_over": True,
            "overs": [
                {
                    "over": 0,
                    "deliveries": [
                        {
                            "batter": "V Kohli",
                            "non_striker": "RG Sharma",
                            "bowler": "JJ Bumrah",
                            "runs": {"batter": 6, "extras": 0, "total": 6},
                        }
                    ],
                }
            ],
        }
    )
    import_archive(session, archive(tmp_path, payload), "odi")
    totals = innings_totals(session, "990001")
    assert len(totals) == 3 and totals[-1]["super_over"] and totals[-1]["runs"] == 6
    assert player_records(session, "ba607b88")["batting"]["runs"] == 17


def test_miscounted_over_does_not_invent_missing_deliveries():
    payload = synthetic_match()
    payload["innings"][0]["overs"][0]["deliveries"].pop(0)
    payload["innings"][0]["miscounted_overs"] = {"0": {"balls": 5, "umpire": "Known Umpire"}}
    assert parse_match(payload, "123")["innings"][0]["data_complete"]


@pytest.mark.parametrize("match_type", ["T20", "IT20"])
def test_international_t20_match_types_import_and_query(session, tmp_path, match_type):
    payload = json.loads((FIXTURES / "1041615.json").read_text())
    payload["info"]["match_type"] = match_type
    path = archive(tmp_path, payload)
    assert import_archive(session, path, "t20i")["changed"] == 1
    assert import_archive(session, path, "t20i")["changed"] == 0
    match = session.get(Match, "990001")
    assert match.format == "t20i" and match.info["match_type"] == match_type
    assert [row["runs"] for row in innings_totals(session, match.id)] == [245, 244]
    assert player_records(session, "740742ef", "t20i")["coverage"]["matches"] == 1


@pytest.mark.parametrize(
    "change", ["unknown_player", "wrong_sum", "wrong_gender", "club", "version"]
)
def test_invalid_source_rolls_back_artifact(session, tmp_path, change):
    payload = synthetic_match()
    if change == "unknown_player":
        payload["innings"][0]["overs"][0]["deliveries"][0]["batter"] = "Unmapped Name"
    elif change == "wrong_sum":
        payload["innings"][0]["overs"][0]["deliveries"][0]["runs"]["total"] = 99
    elif change == "wrong_gender":
        payload["info"]["gender"] = "female"
    elif change == "club":
        payload["info"]["team_type"] = "club"
    else:
        payload["meta"]["data_version"] = "2.0.0"
    before = counts(session)
    with pytest.raises(ImportError), session.begin_nested():
        import_archive(session, archive(tmp_path, payload), "odi")
    assert counts(session) == before


def test_no_external_mapping_is_guessed_from_a_name(session, tmp_path):
    import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    with pytest.raises(PlayerResolutionError, match="no name match guessed"):
        resolve_espn_profile(
            session, {"athlete": {"id": "999999999", "displayName": "Virat Kohli"}}
        )


def test_register_multiple_provider_ids_and_ambiguous_names(session, tmp_path):
    people, names = tmp_path / "people.csv", tmp_path / "names.csv"
    people.write_text(
        "identifier,name,unique_name,key_cricinfo,key_cricinfo_2\n"
        "person01,Same Name,Same Name One,101,102\n"
        "person02,Same Name,Same Name Two,103,\n"
    )
    names.write_text("identifier,name\nperson01,Alternative Name\n")
    import_register(session, people, names)
    assert len(find_players(session, "same name")) == 2
    assert session.get(ExternalPlayerID, ("espn", "101")).player_id == "person01"
    assert session.get(ExternalPlayerID, ("espn", "102")).player_id == "person01"
    assert len(find_players(session, "alternative name")) == 1
    people.write_text(people.read_text().replace("103,", "101,"))
    before = counts(session)
    with pytest.raises(ImportError, match="Ambiguous"), session.begin_nested():
        import_register(session, people, names)
    assert counts(session) == before


def test_external_identifier_cannot_be_reassigned(session, tmp_path):
    people, names = tmp_path / "people.csv", tmp_path / "names.csv"
    people.write_text(
        "identifier,name,unique_name,key_cricinfo\nperson01,A,A,101\nperson02,B,B,102\n"
    )
    names.write_text("identifier,name\nperson01,Alias\n")
    import_register(session, people, names)
    people.write_text("identifier,name,unique_name,key_cricinfo\nperson01,A,A,\nperson02,B,B,101\n")
    with pytest.raises(ImportError, match="reassign"), session.begin_nested():
        import_register(session, people, names)
    assert session.get(ExternalPlayerID, ("espn", "101")).player_id == "person01"


def test_subset_provenance_and_missing_match_selection(session, tmp_path):
    payload = synthetic_match()
    path = tmp_path / "matches.zip"
    with zipfile.ZipFile(path, "w") as output:
        for match_id in ("990001", "990002"):
            output.writestr(f"{match_id}.json", json.dumps(payload))
    selected = import_archive(session, path, "odi", limit=1)
    assert selected["partial"] and selected["matches"] == 1
    assert (
        session.get(SourceImport, uuid.UUID(selected["import_id"])).details["archive_matches"] == 2
    )
    with pytest.raises(ImportError, match="absent"):
        import_archive(session, path, "odi", match_ids=["123456"])


def test_late_invalid_match_rolls_back_earlier_matches_in_the_artifact(session, tmp_path):
    payload = synthetic_match()
    invalid = synthetic_match()
    invalid["innings"][0]["overs"][0]["deliveries"][0]["runs"]["total"] = 999
    path = tmp_path / "late-invalid.zip"
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("990001.json", json.dumps(payload))
        output.writestr("990002.json", json.dumps(invalid))
    before = counts(session)
    with pytest.raises(ImportError), session.begin_nested():
        import_archive(session, path, "odi")
    assert counts(session) == before


def test_match_revision_cannot_be_downgraded(session, tmp_path):
    payload = synthetic_match()
    payload["meta"]["revision"] = 2
    import_archive(session, archive(tmp_path, payload), "odi")
    payload["meta"]["revision"] = 1
    with pytest.raises(ImportError, match="downgrade"), session.begin_nested():
        import_archive(session, archive(tmp_path, payload), "odi")
    assert session.get(Match, "990001").source_revision == 2


def test_source_identity_on_both_teams_is_preserved_without_guessing_and_excluded(
    session, tmp_path
):
    payload = json.loads((FIXTURES / "1229824.json").read_text())
    import_fixture(session, tmp_path, "1229824", "t20i")
    assert all(not row.data_complete for row in session.scalars(select(Innings)))
    assert any(
        "ambiguous_player_identity" in marker
        for marker in session.scalar(select(Innings).where(Innings.number == 1)).missing
        if isinstance(marker, dict)
    )
    result = player_records(session, "efcb778e", "t20i")
    assert result["coverage"]["matches"] == 1
    assert result["coverage"]["excluded_incomplete_batting_innings"] >= 1
    assert result["batting"]["runs"] == 0 and result["bowling"]["wickets"] == 0
    assert session.get(Match, "1229824").info == payload["info"]
