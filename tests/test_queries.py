import pytest

from cricket.ingest import import_archive
from cricket.queries import player_records
from tests.helpers import archive, synthetic_match


@pytest.mark.parametrize("kind", ["timed out", "hit the ball twice"])
def test_non_bowler_dismissals_do_not_receive_bowler_credit(session, tmp_path, kind):
    payload = synthetic_match()
    delivery = payload["innings"][0]["overs"][0]["deliveries"][0]
    delivery["wickets"] = [
        {
            "player_out": "New Batter" if kind == "timed out" else "V Kohli",
            "kind": kind,
        }
    ]
    import_archive(session, archive(tmp_path, payload), "odi")
    assert player_records(session, "462411b3", "odi")["bowling"]["wickets"] == 0
    assert player_records(session, "ba607b88", "odi")["bowling"]["wickets"] == 1


@pytest.mark.parametrize("mode", ["complete", "incomplete", "super_over"])
def test_wicket_only_batting_innings_are_counted_with_existing_coverage_rules(
    session, tmp_path, mode
):
    payload = synthetic_match()
    first = payload["innings"][0]
    first["overs"][0]["deliveries"][0]["wickets"] = [
        {"player_out": "V Kohli", "kind": "caught"},
        {"player_out": "New Batter", "kind": "timed out"},
    ]
    # The timed-out batter never reaches either end of the crease in this innings.
    for over in first["overs"]:
        for delivery in over["deliveries"]:
            if delivery["non_striker"] == "New Batter":
                delivery["non_striker"] = "RG Sharma"
    if mode == "incomplete":
        first["missing"] = ["deliveries"]
    elif mode == "super_over":
        first["super_over"] = True
    import_archive(session, archive(tmp_path, payload), "odi")
    result = player_records(session, "new00001", "odi")
    assert result["coverage"]["matches"] == 1
    assert result["coverage"]["batting_innings"] == (1 if mode == "complete" else 0)
    assert result["batting"] == {
        "runs": 0,
        "legal_balls": 0,
        "dismissals": 1 if mode == "complete" else 0,
    }
    if mode == "super_over":
        assert result["innings"] == []
    else:
        assert len(result["innings"]) == 1
        assert result["innings"][0]["dismissed"]
        assert result["innings"][0]["fours"] == result["innings"][0]["sixes"] == 0
        assert result["coverage"]["excluded_incomplete_batting_innings"] == (
            1 if mode == "incomplete" else 0
        )


def test_roster_player_who_did_not_bat_has_no_batting_innings(session, tmp_path):
    payload = synthetic_match()
    for over in payload["innings"][0]["overs"]:
        for delivery in over["deliveries"]:
            if delivery["non_striker"] == "New Batter":
                delivery["non_striker"] = "RG Sharma"
    import_archive(session, archive(tmp_path, payload), "odi")
    result = player_records(session, "new00001", "odi")
    assert result["coverage"]["matches"] == 1
    assert result["coverage"]["batting_innings"] == 0
    assert result["innings"] == []
    assert result["batting"] == {"runs": 0, "legal_balls": 0, "dismissals": 0}


def test_multiple_wickets_on_one_delivery_do_not_duplicate_batting_runs(session, tmp_path):
    payload = synthetic_match()
    first = payload["innings"][0]["overs"][0]["deliveries"][0]
    first["wickets"] = [
        {"player_out": "V Kohli", "kind": "caught"},
        {"player_out": "New Batter", "kind": "timed out"},
    ]
    import_archive(session, archive(tmp_path, payload), "odi")
    result = player_records(session, "ba607b88", "odi")
    assert result["batting"] == {"runs": 17, "legal_balls": 8, "dismissals": 1}
    assert result["coverage"]["batting_innings"] == 1
