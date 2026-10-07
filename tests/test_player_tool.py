import json
import uuid
from datetime import timedelta

from cricket.conversations import ConversationRepository
from cricket.espn import PlayerResolutionError
from cricket.harness import SYSTEM_PROMPT, AgentHarness
from cricket.ingest import import_archive, import_register
from cricket.models import Dataset, ExternalPlayerID, utcnow
from cricket.player_tool import get_player_data
from tests.conftest import FIXTURES
from tests.helpers import archive, synthetic_match
from tests.test_harness import FakeModel, tool_reply


def seed_player(session, tmp_path):
    import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    import_archive(session, archive(tmp_path, synthetic_match()), "odi")


def test_player_tool_harness_persists_filtered_dataset_and_trace(session, tmp_path, monkeypatch):
    seed_player(session, tmp_path)
    fetched = []

    def profile(external_id):
        fetched.append(external_id)
        return {"athlete": {"id": external_id, "displayName": "Virat Kohli"}}

    monkeypatch.setattr("cricket.espn.fetch_profile", profile)
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    args = {"player_name": "Virat Kohli", "format": "odi", "start_date": "2026-01-01"}
    model = FakeModel(tool_reply(json.dumps(args), name="get_player_data"), {"content": "17 runs."})
    answer, traces = AgentHarness(completion=model).run(repo, "Kohli ODI runs since 2026?")

    assert answer == "17 runs."
    assert fetched == ["253802"]
    result = json.loads(traces[0]["result"])
    assert result["ok"] and result["data"]["identity"]["player_id"] == "ba607b88"
    assert result["data"]["stats"]["odi"]["batting"]["runs"] == 17
    assert result["coverage"]["sample_size"] == {"matches": 1, "batting_innings": 1}
    assert result["provenance"][0]["status"] == "refreshed"
    assert result["provenance"][1]["provider"] == "cricsheet"
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    assert dataset.conversation_id == repo.session_id
    assert dataset.data["innings"][0]["runs"] == 17
    assert repo.transcript()["datasets"] == [str(dataset.id)]
    assert json.loads(model.requests[1]["messages"][-1]["content"]) == result


def test_player_dataset_records_stable_dismissal_kinds_and_excludes_retired_hurt(
    session, tmp_path, monkeypatch
):
    seed_player(session, tmp_path)
    monkeypatch.setattr(
        "cricket.espn.fetch_profile",
        lambda external_id: {"athlete": {"id": external_id, "displayName": "Virat Kohli"}},
    )
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    result = get_player_data(session, repo.session_id, player_id="ba607b88", format="odi")
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    row = dataset.data["innings"][0]
    assert uuid.UUID(row["innings_id"])
    assert row["dismissal_kinds"] == []
    assert result["data"]["stats"]["odi"]["dismissal_counts"] == {}


def test_player_dataset_counts_source_dismissal_kind(session, tmp_path, monkeypatch):
    import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    payload = synthetic_match()
    payload["innings"][0]["overs"][1]["deliveries"][1]["wickets"][0]["kind"] = "caught"
    import_archive(session, archive(tmp_path, payload), "odi")
    monkeypatch.setattr(
        "cricket.espn.fetch_profile",
        lambda external_id: {"athlete": {"id": external_id, "displayName": "Virat Kohli"}},
    )
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    result = get_player_data(session, repo.session_id, player_id="ba607b88", format="odi")
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    row = dataset.data["innings"][0]
    assert row["dismissed"] is True
    assert [
        (event["kind"], event["dismissal_id"].startswith(row["innings_id"] + ":"))
        for event in row["dismissal_kinds"]
    ] == [("caught", True)]
    assert result["data"]["stats"]["odi"]["dismissal_counts"] == {"caught": 1}


def test_player_tool_ambiguity_and_bad_filters_create_no_dataset(session, tmp_path):
    people, names = tmp_path / "people.csv", tmp_path / "names.csv"
    people.write_text(
        "identifier,name,unique_name,key_cricinfo\n"
        "person01,Same Name,Same Name One,101\n"
        "person02,Same Name,Same Name Two,102\n"
    )
    names.write_text("identifier,name\nperson01,Alternative Name\n")
    import_register(session, people, names)
    conversation_id = uuid.uuid4()
    ambiguous = get_player_data(session, conversation_id, player_name="same name")
    assert ambiguous["error"]["code"] == "ambiguous_player"
    assert {item["player_id"] for item in ambiguous["data"]["candidates"]} == {
        "person01",
        "person02",
    }
    assert get_player_data(session, conversation_id, player_id="missing")["error"]["code"] == (
        "player_not_found"
    )
    for options in (
        {"player_id": "person01", "start_date": "2026-02-30"},
        {"player_id": "person01", "start_date": "2026-02-01", "end_date": "2026-01-01"},
        {"player_id": "person01", "format": "test"},
    ):
        assert get_player_data(session, conversation_id, **options)["error"]["code"] == (
            "invalid_arguments"
        )
    assert session.query(Dataset).count() == 0


def test_player_tool_uses_fresh_cache_and_dated_stale_fallback(session, tmp_path, monkeypatch):
    seed_player(session, tmp_path)
    mapping = session.get(ExternalPlayerID, ("espn", "253802"))
    mapping.profile = {"athlete": {"id": "253802", "displayName": "Cached Kohli"}}
    mapping.profile_retrieved_at = utcnow()
    calls = []

    def unavailable(external_id):
        calls.append(external_id)
        raise PlayerResolutionError("Provider unavailable")

    monkeypatch.setattr("cricket.espn.fetch_profile", unavailable)
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    fresh = get_player_data(session, repo.session_id, player_id="ba607b88", format="odi")
    assert fresh["data"]["identity"]["profile_status"] == "fresh_cache"
    assert calls == []

    mapping.profile_retrieved_at = utcnow() - timedelta(hours=25)
    stale = get_player_data(session, repo.session_id, player_id="ba607b88", format="odi")
    assert calls == ["253802"]
    assert stale["data"]["identity"]["profile_status"] == "stale_cache"
    assert stale["data"]["identity"]["profile_retrieved_at"] == (
        mapping.profile_retrieved_at.isoformat()
    )
    assert stale["data"]["stats"]["odi"]["batting"]["runs"] == 17


def test_player_tool_unavailable_profile_keeps_source_backed_stats(session, tmp_path, monkeypatch):
    seed_player(session, tmp_path)
    monkeypatch.setattr(
        "cricket.espn.fetch_profile",
        lambda external_id: (_ for _ in ()).throw(PlayerResolutionError("Unavailable")),
    )
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    result = get_player_data(
        session, repo.session_id, player_id="ba607b88", start_date="2027-01-01"
    )
    assert result["ok"]
    assert result["data"]["identity"]["profile_status"] == "unavailable"
    assert result["coverage"]["sample_size"]["matches"] == 0
    assert set(result["data"]["stats"]) == {"odi", "t20i"}
    assert result["provenance"] == [result["provenance"][0]]


def test_player_tool_tries_another_register_id_after_provider_failure(
    session, tmp_path, monkeypatch
):
    seed_player(session, tmp_path)
    first = session.get(ExternalPlayerID, ("espn", "253802"))
    session.add(
        ExternalPlayerID(
            provider="espn",
            external_id="999999",
            player_id=first.player_id,
            source_import_id=first.source_import_id,
        )
    )
    attempted = []

    def fetch(external_id):
        attempted.append(external_id)
        if external_id == "253802":
            raise PlayerResolutionError("Unavailable")
        return {"athlete": {"id": external_id, "displayName": "Alternate ID"}}

    monkeypatch.setattr("cricket.espn.fetch_profile", fetch)
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    result = get_player_data(session, repo.session_id, player_id="ba607b88")
    assert attempted == ["253802", "999999"]
    assert result["data"]["identity"]["espn_id"] == "999999"
    assert result["data"]["identity"]["profile_status"] == "refreshed"
