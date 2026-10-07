import pytest
from sqlalchemy import delete, func, select

from cricket.ingest import import_register
from cricket.models import ExternalPlayerID, Player, SourceImport
from tests.conftest import FIXTURES


def write_register(tmp_path, rows):
    people, names = tmp_path / "people.csv", tmp_path / "names.csv"
    people.write_text("identifier,name,unique_name,key_cricinfo,key_cricinfo_2\n" + rows)
    names.write_text("identifier,name\nperson01,Alias\n")
    return people, names


def test_subset_register_preserves_omitted_players_and_cached_profiles(session, tmp_path):
    first = import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    rohit = session.get(ExternalPlayerID, ("espn", "34102"))
    rohit.profile = {"athlete": {"id": "34102"}}
    session.flush()
    people, names = write_register(tmp_path, "person01,Subset,Subset,101,\n")
    import_register(session, people, names)
    session.expire_all()
    rohit = session.get(ExternalPlayerID, ("espn", "34102"))
    assert rohit is not None
    assert rohit.profile == {"athlete": {"id": "34102"}}
    assert str(rohit.source_import_id) == first["import_id"]
    assert session.get(ExternalPlayerID, ("espn", "253802")) is not None


def test_historical_register_restores_removed_ids_and_player_metadata(session, tmp_path):
    people, names = write_register(tmp_path, "person01,A,A,101,102\n")
    first = import_register(session, people, names)
    mapping = session.get(ExternalPlayerID, ("espn", "101"))
    mapping.profile = {"athlete": {"id": "101"}}
    session.flush()
    people.write_text(people.read_text().replace("person01,A,A,101,102", "person01,B,B,101,"))
    names.write_text("identifier,name\nperson01,New Alias\n")
    import_register(session, people, names)
    assert session.get(ExternalPlayerID, ("espn", "102")) is None
    write_register(tmp_path, "person01,A,A,101,102\n")
    restored = import_register(session, people, names)
    session.expire_all()
    assert not restored["unchanged"] and restored["import_id"] == first["import_id"]
    assert session.get(Player, "person01").name == "A"
    assert session.get(Player, "person01").aliases == ["Alias"]
    assert session.get(ExternalPlayerID, ("espn", "102")).player_id == "person01"
    assert session.get(ExternalPlayerID, ("espn", "101")).profile == {"athlete": {"id": "101"}}
    assert import_register(session, people, names)["unchanged"]
    assert session.scalar(select(func.count(SourceImport.id))) == 2


def test_register_removal_preserves_other_provider_and_non_register_mappings(session, tmp_path):
    people, names = write_register(tmp_path, "person01,A,A,101,102\nperson02,B,B,103,\n")
    first = import_register(session, people, names)
    retained = session.get(ExternalPlayerID, ("espn", "101"))
    manual = SourceImport(
        provider="manual",
        dataset_url="local:reviewed",
        checksum="a" * 64,
        selection_key="manual",
        source_revision="1",
        counts={},
        details={},
    )
    session.add(manual)
    session.flush()
    session.add_all(
        [
            ExternalPlayerID(
                provider="espn", external_id="999", player_id="person01", source_import_id=manual.id
            ),
            ExternalPlayerID(
                provider="other",
                external_id="888",
                player_id="person01",
                source_import_id=retained.source_import_id,
            ),
        ]
    )
    session.flush()
    people.write_text("identifier,name,unique_name,key_cricinfo\nperson01,A,A,101\n")
    import_register(session, people, names)
    session.expire_all()
    assert session.get(ExternalPlayerID, ("espn", "102")) is None
    assert session.get(ExternalPlayerID, ("espn", "103")) is not None
    assert session.get(ExternalPlayerID, ("other", "888")) is not None
    assert session.get(ExternalPlayerID, ("espn", "999")).source_import_id == manual.id
    assert str(session.get(Player, "person02").register_import_id) == first["import_id"]


@pytest.mark.parametrize("missing", ["mapping", "player_metadata"])
def test_seen_register_reconciles_database_changes(session, tmp_path, missing):
    people, names = write_register(tmp_path, "person01,A,A,101,\n")
    first = import_register(session, people, names)
    if missing == "mapping":
        session.execute(delete(ExternalPlayerID).where(ExternalPlayerID.external_id == "101"))
    else:
        session.get(Player, "person01").aliases = []
        session.flush()
    result = import_register(session, people, names)
    session.expire_all()
    assert not result["unchanged"] and result["import_id"] == first["import_id"]
    assert session.get(ExternalPlayerID, ("espn", "101")) is not None
    assert session.get(Player, "person01").aliases == ["Alias"]
    assert import_register(session, people, names)["unchanged"]
