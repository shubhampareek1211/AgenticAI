import json
import uuid
import zipfile

import pytest

from cricket.chart_tool import create_cricket_chart
from cricket.conversations import ConversationRepository
from cricket.harness import SYSTEM_PROMPT, AgentHarness
from cricket.ingest import import_archive, import_register
from cricket.models import ChartSpec, Dataset
from cricket.wicket_tool import analyze_wicket_response
from tests.conftest import FIXTURES
from tests.helpers import archive, synthetic_match
from tests.test_harness import FakeModel, tool_reply

PLAYER_ID = "ba607b88"


def match_with_wicket():
    """Thirty legal balls give one wicket exactly two complete 12-ball windows."""
    payload = synthetic_match()
    innings = payload["innings"][0]
    innings.pop("penalty_runs", None)
    innings["overs"] = []
    for over in range(5):
        balls = []
        for ball in range(6):
            number = 6 * over + ball + 1
            partner = "RG Sharma" if number <= 15 else "New Batter"
            kohli_faces = number % 2 == (1 if number < 15 else 0)
            if number == 15:
                kohli_faces = False
            runs = (2 if number < 15 else 4) if kohli_faces else 0
            delivery = {
                "batter": "V Kohli" if kohli_faces else partner,
                "non_striker": partner if kohli_faces else "V Kohli",
                "bowler": "JJ Bumrah",
                "runs": {"batter": runs, "extras": 0, "total": runs},
            }
            if number == 15:
                delivery["wickets"] = [{"player_out": "RG Sharma", "kind": "caught"}]
            balls.append(delivery)
        innings["overs"].append({"over": over, "deliveries": balls})
    payload["innings"] = [innings]
    return payload


def prepared(session, tmp_path, payload):
    import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    import_archive(session, archive(tmp_path, payload), "odi")
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    return repo


def analyze(session, repo):
    return analyze_wicket_response(session, repo.session_id, player_id=PLAYER_ID, format="odi")


def test_wicket_response_aggregates_legal_balls_and_plots_both_rates(session, tmp_path):
    repo = prepared(session, tmp_path, match_with_wicket())
    result = analyze(session, repo)
    assert result["ok"]
    assert result["data"]["totals"] == {
        "before": {"runs": 12, "legal_balls": 6, "boundary_balls": 0},
        "after": {"runs": 24, "legal_balls": 6, "boundary_balls": 6},
    }
    assert result["data"]["rates"] == {
        "before": {"runs_per_100_balls": 200.0, "boundary_ball_percentage": 0.0},
        "after": {"runs_per_100_balls": 400.0, "boundary_ball_percentage": 100.0},
    }
    assert result["coverage"]["candidate_events"] == 1
    assert result["coverage"]["eligible_events"] == 1
    assert result["coverage"]["insufficient_sample"]
    assert result["provenance"][0]["provider"] == "cricsheet"
    assert "selection_bias" in result["coverage"]
    dataset_id = result["data"]["dataset_id"]
    dataset = session.get(Dataset, uuid.UUID(dataset_id))
    assert dataset.kind == "wicket_response" and dataset.conversation_id == repo.session_id
    for metric, expected in (
        ("runs_per_100_balls", [200.0, 400.0]),
        ("boundary_ball_percentage", [0.0, 100.0]),
    ):
        chart = create_cricket_chart(
            session,
            repo.session_id,
            dataset_id=dataset_id,
            chart_type="bar",
            metric=metric,
            group_by="wicket_phase",
        )
        assert chart["ok"]
        saved = session.get(ChartSpec, uuid.UUID(chart["data"]["chart_id"]))
        assert saved.spec["schema_version"] == 2
        assert [series["points"][0]["y"] for series in saved.spec["chart"]["series"]] == expected
        assert "insufficient sample" in saved.spec["chart"]["title"]


def test_illegal_batter_runs_do_not_enter_rates_but_illegal_wickets_exclude(session, tmp_path):
    payload = match_with_wicket()
    no_ball = {
        "batter": "V Kohli",
        "non_striker": "RG Sharma",
        "bowler": "JJ Bumrah",
        "runs": {"batter": 6, "extras": 1, "total": 7},
        "extras": {"noballs": 1},
    }
    payload["innings"][0]["overs"][1]["deliveries"].insert(2, no_ball)
    repo = prepared(session, tmp_path, payload)
    result = analyze(session, repo)
    assert result["coverage"]["eligible_events"] == 1
    assert result["data"]["totals"]["before"] == {
        "runs": 12,
        "legal_balls": 6,
        "boundary_balls": 0,
    }

    no_ball["wickets"] = [{"player_out": "RG Sharma", "kind": "run out"}]
    # Reimport the changed source into a fresh match; the original match is replaced.
    import_archive(session, archive(tmp_path, payload), "odi")
    changed = analyze(session, repo)
    assert changed["coverage"]["eligible_events"] == 0
    assert changed["coverage"]["excluded_by_reason"]["another_wicket"] >= 1


def test_multiple_events_use_pooled_runs_and_balls_not_mean_event_rates(session, tmp_path):
    first, second = match_with_wicket(), match_with_wicket()
    balls = [ball for over in second["innings"][0]["overs"] for ball in over["deliveries"]]
    for number in (*range(3, 15), *range(16, 28)):
        selected = number in {3, 4, 5} if number < 15 else number <= 24
        partner = "RG Sharma" if number < 15 else "New Batter"
        ball = balls[number - 1]
        ball["batter"], ball["non_striker"] = (
            ("V Kohli", partner) if selected else (partner, "V Kohli")
        )
        runs = (4 if number < 15 else 1) if selected else 0
        ball["runs"] = {"batter": runs, "extras": 0, "total": runs}
    path = tmp_path / "two_matches.zip"
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("990001.json", json.dumps(first))
        output.writestr("990002.json", json.dumps(second))
    import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    import_archive(session, path, "odi")
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    result = analyze(session, repo)
    assert result["coverage"]["eligible_events"] == 2
    assert result["data"]["totals"]["before"] == {
        "runs": 24,
        "legal_balls": 9,
        "boundary_balls": 3,
    }
    assert result["data"]["totals"]["after"] == {
        "runs": 33,
        "legal_balls": 15,
        "boundary_balls": 6,
    }
    assert result["data"]["rates"]["before"]["runs_per_100_balls"] == 266.67
    assert result["data"]["rates"]["after"]["runs_per_100_balls"] == 220.0


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("short_after", "incomplete_window"),
        ("other_wicket", "another_wicket"),
        ("left_crease", "batter_left_crease"),
        ("no_before", "no_faced_ball_before"),
        ("no_after", "no_faced_ball_after"),
        ("incomplete_innings", "incomplete_innings"),
    ],
)
def test_wicket_response_reports_exclusion_reasons(session, tmp_path, change, reason):
    payload = match_with_wicket()
    balls = [ball for over in payload["innings"][0]["overs"] for ball in over["deliveries"]]
    if change == "short_after":
        payload["innings"][0]["overs"].pop()
    elif change == "other_wicket":
        balls[19]["wickets"] = [{"player_out": "New Batter", "kind": "run out"}]
    elif change == "left_crease":
        balls[19]["batter"] = "New Batter"
        balls[19]["non_striker"] = "RG Sharma"
    elif change == "no_before":
        for ball in balls[:14]:
            ball["batter"], ball["non_striker"] = "RG Sharma", "V Kohli"
            ball["runs"] = {"batter": 0, "extras": 0, "total": 0}
    elif change == "no_after":
        for ball in balls[15:]:
            ball["batter"], ball["non_striker"] = "New Batter", "V Kohli"
            ball["runs"] = {"batter": 0, "extras": 0, "total": 0}
    elif change == "incomplete_innings":
        payload["innings"][0]["missing"] = ["missing ball"]
    repo = prepared(session, tmp_path, payload)
    result = analyze(session, repo)
    assert result["ok"] and result["coverage"]["eligible_events"] == 0
    assert result["coverage"]["excluded_by_reason"][reason] >= 1
    assert result["coverage"]["candidate_events"] == (
        result["coverage"]["eligible_events"] + result["coverage"]["excluded_events"]
    )
    chart = create_cricket_chart(
        session,
        repo.session_id,
        dataset_id=result["data"]["dataset_id"],
        chart_type="bar",
        metric="runs_per_100_balls",
        group_by="wicket_phase",
    )
    assert chart["error"]["code"] == "insufficient_data"


def test_wicket_response_validates_identity_format_dates_and_empty_sample(session, tmp_path):
    payload = match_with_wicket()
    payload["innings"][0]["overs"][2]["deliveries"][2].pop("wickets")
    repo = prepared(session, tmp_path, payload)
    empty = analyze(session, repo)
    assert empty["ok"] and empty["coverage"]["candidate_events"] == 0
    assert empty["data"]["rates"]["before"]["runs_per_100_balls"] is None
    assert empty["coverage"]["insufficient_sample"]
    for options in (
        {"player_id": "missing", "format": "odi"},
        {"player_id": PLAYER_ID, "format": "test"},
        {"player_id": PLAYER_ID, "format": "odi", "start_date": "2026-02-30"},
        {
            "player_id": PLAYER_ID,
            "format": "odi",
            "start_date": "2027-01-01",
            "end_date": "2026-01-01",
        },
    ):
        assert not analyze_wicket_response(session, repo.session_id, **options)["ok"]


def test_wicket_response_runs_through_harness_and_saves_trace(session, tmp_path):
    repo = prepared(session, tmp_path, match_with_wicket())
    args = json.dumps({"player_id": PLAYER_ID, "format": "odi"})
    model = FakeModel(
        tool_reply(args, name="analyze_wicket_response"),
        {"content": "One eligible event; insufficient sample."},
    )
    answer, traces = AgentHarness(completion=model).run(repo, "How did Kohli respond to wickets?")
    assert "insufficient sample" in answer
    assert traces[0]["name"] == "analyze_wicket_response"
    result = json.loads(traces[0]["result"])
    assert result["ok"] and result["coverage"]["eligible_events"] == 1
    assert repo.transcript()["datasets"] == [result["data"]["dataset_id"]]
