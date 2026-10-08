"""Match resolution and over snapshots for Manhattan/worm chart datasets."""

import json
import uuid

from cricket.chart_tool import create_cricket_chart
from cricket.chart_view import normalize_chart_spec
from cricket.conversations import ConversationRepository
from cricket.harness import SYSTEM_PROMPT, AgentHarness
from cricket.ingest import import_archive
from cricket.match_tool import get_match_data
from cricket.models import ChartSpec, Dataset
from tests.helpers import archive, synthetic_match
from tests.test_harness import FakeModel, tool_reply


def _session(session, tmp_path):
    import_archive(session, archive(tmp_path, synthetic_match()), "odi")
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    return repo


def test_match_snapshot_excludes_penalties_and_non_scoreboard_dismissals(session, tmp_path):
    repo = _session(session, tmp_path)
    result = get_match_data(
        session,
        repo.session_id,
        team="india",
        opponent="Test XI",
        format="odi",
        date="2026-01-01",
    )
    assert result["ok"]
    assert result["data"]["match"]["match_id"] == "990001"
    assert result["coverage"]["complete_main_innings"] == 2
    assert "penalty runs are excluded" in result["coverage"]["method"]
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    assert dataset.kind == "match_data" and dataset.conversation_id == repo.session_id
    first, second = dataset.data["innings"]
    assert first["team"] == "India"
    assert (first["penalty_pre"], first["penalty_post"]) == (5, 1)
    assert [
        {key: over[key] for key in ("over_number", "runs", "wickets", "legal_balls")}
        for over in first["overs"]
    ] == [
        {"over_number": 0, "runs": 20, "wickets": 1, "legal_balls": 6},
        {"over_number": 1, "runs": 2, "wickets": 0, "legal_balls": 2},
    ]
    assert all(sum(over["components"].values()) == over["runs"] for over in first["overs"])
    assert isinstance(first["partnerships"], list)
    assert second["overs"][0]["wickets"] == 1
    assert result["provenance"][0]["provider"] == "cricsheet"


def test_match_filters_return_candidates_without_saving_guess(session, tmp_path):
    repo = _session(session, tmp_path)
    another = synthetic_match()
    another["info"]["dates"] = ["2026-02-02"]
    import_archive(session, archive(tmp_path, another, "990002"), "odi")
    ambiguous = get_match_data(session, repo.session_id, team="India", opponent="Test XI")
    assert ambiguous["error"]["code"] == "ambiguous_match"
    assert {match["match_id"] for match in ambiguous["data"]["matches"]} == {"990001", "990002"}
    assert session.query(Dataset).count() == 0
    selected = get_match_data(session, repo.session_id, match_id="990002")
    assert selected["ok"] and selected["data"]["match"]["date"] == "2026-02-02"
    assert session.query(Dataset).count() == 1


def test_match_invalid_filters_and_incomplete_innings(session, tmp_path):
    payload = synthetic_match()
    payload["innings"][0]["overs"] = []
    import_archive(session, archive(tmp_path, payload), "odi")
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    for options in (
        {},
        {"match_id": "990001", "team": "India"},
        {"team": "India", "opponent": "India"},
        {"team": "India", "format": "test"},
        {"team": "India", "date": "2026-02-30"},
    ):
        assert get_match_data(session, repo.session_id, **options)["error"]["code"] == (
            "invalid_arguments"
        )
    assert get_match_data(session, repo.session_id, match_id="unknown")["error"]["code"] == (
        "match_not_found"
    )
    result = get_match_data(session, repo.session_id, match_id="990001")
    assert result["ok"] and result["coverage"]["excluded_incomplete_innings"] == 1
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    assert dataset.data["innings"][0]["overs"] == []


def test_legacy_match_result_is_compact_but_not_model_callable(session, tmp_path):
    repo = _session(session, tmp_path)
    result = get_match_data(session, repo.session_id, match_id="990001")
    assert result["ok"] and result["data"]["match"]["match_id"] == "990001"
    assert "overs" not in result["data"]
    assert session.get(Dataset, uuid.UUID(result["data"]["dataset_id"])) is not None
    model = FakeModel(
        tool_reply(json.dumps({"match_id": "990001"}), name="get_match_data"),
        {"content": "Live ESPN tools cannot load that archived match."},
    )
    answer, traces = AgentHarness(completion=model).run(repo, "Load match 990001")
    assert answer == "Live ESPN tools cannot load that archived match."
    assert len(traces) == 1
    assert json.loads(traces[0]["result"])["error"]["code"] == "unknown_tool"
    assert session.query(Dataset).count() == 1


def test_persisted_match_dataset_creates_manhattan_and_worm(session, tmp_path):
    repo = _session(session, tmp_path)
    match = get_match_data(session, repo.session_id, match_id="990001")
    dataset_id = match["data"]["dataset_id"]
    for metric, chart_type, expected in (
        ("runs_per_over", "bar", [20, 2]),
        ("cumulative_runs", "line", [20, 22]),
    ):
        result = create_cricket_chart(
            session,
            repo.session_id,
            dataset_id=dataset_id,
            metric=metric,
            group_by="over",
            chart_type=chart_type,
        )
        assert result["ok"]
        saved = session.get(ChartSpec, uuid.UUID(result["data"]["chart_id"]))
        chart = normalize_chart_spec(saved.spec)["chart"]
        first = chart["series"][0]["points"]
        assert [point["x"] for point in first] == [1, 2]
        assert [point["y"] for point in first] == expected
        assert [point["wickets"] for point in first] == [1, 0]
        assert "India" in chart["title"] and "990001" in chart["title"]
    for metric, group_by, chart_type in (
        ("run_components", "over", "stacked_area"),
        ("partnership_runs", "stand", "stacked_bar"),
    ):
        result = create_cricket_chart(
            session,
            repo.session_id,
            dataset_id=dataset_id,
            metric=metric,
            group_by=group_by,
            chart_type=chart_type,
        )
        assert result["ok"], result
        chart = normalize_chart_spec(
            session.get(ChartSpec, uuid.UUID(result["data"]["chart_id"])).spec
        )["chart"]
        assert chart["series"] and chart["chart_type"] == chart_type
    other = ConversationRepository(session, uuid.uuid4())
    other.create(SYSTEM_PROMPT)
    denied = create_cricket_chart(
        session,
        other.session_id,
        dataset_id=dataset_id,
        metric="runs_per_over",
        group_by="over",
        chart_type="bar",
    )
    assert denied["error"]["code"] == "dataset_not_found"
