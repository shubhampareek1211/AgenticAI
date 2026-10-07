import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from cricket.chart_tool import (
    create_cricket_chart,
    dismissal_chart,
    match_chart,
    player_chart,
    rolling_chart,
)
from cricket.chart_view import normalize_chart_spec
from cricket.conversations import ConversationRepository
from cricket.harness import SYSTEM_PROMPT, AgentHarness
from cricket.models import ChartSpec, Dataset
from cricket.player_tool import get_player_data
from tests.test_harness import FakeModel, tool_reply
from tests.test_player_tool import seed_player


def test_legacy_plotly_values_are_read_without_recalculation():
    spec = {
        "figure": {
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": "ODI",
                    "x": ["2025-01-01", "2026-01-01"],
                    "y": [4, 17],
                    "customdata": [["match-1", 1], ["match-2", 2]],
                }
            ],
            "layout": {"title": {"text": "Runs"}},
        },
        "metric": "runs",
        "group_by": "innings_date",
        "chart_type": "line",
        "coverage": {"plotted": {"batting_innings": 2}},
        "provenance": [{"provider": "cricsheet"}],
    }
    normalized = normalize_chart_spec(spec)
    assert normalized["schema_version"] == 2
    assert normalized["coverage"] == spec["coverage"]
    assert normalized["provenance"] == spec["provenance"]
    assert normalized["chart"]["series"][0]["points"] == [
        {"x": "2025-01-01", "y": 4, "match_id": "match-1", "innings_number": 1},
        {"x": "2026-01-01", "y": 17, "match_id": "match-2", "innings_number": 2},
    ]
    sparse = normalize_chart_spec(
        {"figure": {"data": [{"type": "bar", "x": ["2026"], "y": [17]}], "layout": {}}}
    )
    assert sparse["chart"]["series"][0]["points"] == [{"x": "2026", "y": 17}]
    with pytest.raises(ValueError, match="Unsupported saved chart trace"):
        normalize_chart_spec({"figure": {"data": [{"type": "heatmap", "x": [1], "y": [2]}]}})


def test_annual_metrics_from_complete_innings_without_database():
    base = {
        "format": "odi",
        "match_id": "m1",
        "innings_number": 1,
        "data_complete": True,
        "date": "2025-01-01",
        "runs": 30,
        "legal_balls": 10,
        "fours": 2,
        "sixes": 1,
        "dismissed": False,
    }
    rows = [
        base,
        {
            **base,
            "match_id": "m2",
            "date": "2025-02-01",
            "runs": 20,
            "legal_balls": 0,
            "fours": 1,
            "sixes": 0,
            "dismissed": True,
        },
        {
            **base,
            "match_id": "m3",
            "date": "2026-01-01",
            "runs": 9,
            "legal_balls": 0,
            "fours": 0,
            "sixes": 0,
            "dismissed": False,
        },
        {
            **base,
            "format": "t20i",
            "match_id": "m4",
            "runs": 10,
            "legal_balls": 5,
            "fours": 1,
            "sixes": 1,
            "dismissed": True,
        },
        {**base, "match_id": "m5", "runs": 999, "data_complete": False},
    ]
    dataset = SimpleNamespace(
        data={"identity": {"name": "Example"}, "innings": rows}, player_id="p"
    )
    expected = {
        "runs": ([50, 9], [10]),
        "batting_average": ([50, None], [10]),
        "mean_runs_per_innings": ([25, 9], [10]),
        "runs_per_100_legal_balls": ([500, None], [200]),
        "boundaries": ([4, 0], [2]),
    }
    for metric, (odi_values, t20i_values) in expected.items():
        chart, plotted = player_chart(dataset, metric, "year", "line")
        odi, t20i = chart["series"]
        assert [point["x"] for point in odi["points"]] == ["2025", "2026"]
        assert [point["y"] for point in odi["points"]] == odi_values
        assert [point["y"] for point in t20i["points"]] == t20i_values
        assert [point["sample_size"] for point in odi["points"]] == [2, 1]
        assert plotted["excluded_incomplete_batting_innings"] == 1
    assert player_chart(dataset, "batting_average", "year", "bar")[0]["series"][0]["points"][1][
        "note"
    ].startswith("No dismissals")
    assert (
        player_chart(dataset, "boundaries", "year", "bar")[0]["series"][0]["points"][0]["fours"]
        == 3
    )


def test_rolling_windows_use_complete_innings_and_imported_baseline():
    base = {
        "format": "odi",
        "innings_number": 1,
        "data_complete": True,
        "fours": 0,
        "sixes": 0,
    }
    observations = [
        (10, 10, False),
        (20, 20, False),
        (30, 30, True),
        (40, 40, False),
        (50, 50, True),
    ]
    rows = [
        {
            **base,
            "match_id": f"m{index}",
            "date": f"2025-01-0{index}",
            "runs": runs,
            "legal_balls": balls,
            "dismissed": dismissed,
        }
        for index, (runs, balls, dismissed) in enumerate(observations, 1)
    ]
    rows.extend(
        [
            {**rows[0], "match_id": "partial", "runs": 1000, "data_complete": False},
            {**rows[0], "format": "t20i", "match_id": "short"},
        ]
    )
    dataset = SimpleNamespace(data={"innings": rows})
    chart, plotted = rolling_chart(dataset, "batting_average", 3)
    odi, baseline = chart["series"]
    assert [point["x"] for point in odi["points"]] == [3, 4, 5]
    assert [point["y"] for point in odi["points"]] == [60, 90, 60]
    assert [point["y"] for point in baseline["points"]] == [75, 75, 75]
    assert all(point["sample_size"] == 3 for point in odi["points"])
    assert plotted["excluded_incomplete_batting_innings"] == 1
    assert plotted["data_points"] == 3
    rate, _ = rolling_chart(dataset, "runs_per_100_legal_balls", 3)
    assert rate["series"][0]["points"][0]["y"] == 100
    assert rate["series"][1]["points"][0]["y"] == 100
    assert rolling_chart(dataset, "batting_average", 6)[0] is None


def test_rolling_zero_denominators_are_null_not_zero():
    rows = [
        {
            "format": "odi",
            "match_id": str(index),
            "innings_number": 1,
            "date": f"2025-01-0{index}",
            "runs": index,
            "legal_balls": 0,
            "fours": 0,
            "sixes": 0,
            "dismissed": False,
            "data_complete": True,
        }
        for index in range(1, 4)
    ]
    dataset = SimpleNamespace(data={"innings": rows})
    for metric in ("batting_average", "runs_per_100_legal_balls"):
        chart, _ = rolling_chart(dataset, metric, 3)
        rolling, baseline = chart["series"]
        assert rolling["points"][0]["y"] is None
        assert baseline["points"][0]["y"] is None
        assert "No " in rolling["points"][0]["note"]


def test_dismissal_breakdown_excludes_incomplete_and_requires_saved_kinds():
    base = {
        "format": "odi",
        "date": "2025-01-01",
        "match_id": "a",
        "innings_number": 1,
        "runs": 10,
        "legal_balls": 4,
        "fours": 1,
        "sixes": 0,
        "dismissed": True,
        "data_complete": True,
    }
    rows = [
        {**base, "dismissal_kinds": [{"dismissal_id": "a:1:1", "kind": "caught"}]},
        {
            **base,
            "match_id": "b",
            "dismissal_kinds": [{"dismissal_id": "b:1:1", "kind": "run out"}],
        },
        {
            **base,
            "format": "t20i",
            "match_id": "c",
            "dismissal_kinds": [{"dismissal_id": "c:1:1", "kind": "caught"}],
        },
        {
            **base,
            "match_id": "partial",
            "data_complete": False,
            "dismissal_kinds": [{"dismissal_id": "partial:1:1", "kind": "bowled"}],
        },
    ]
    chart, plotted = dismissal_chart(SimpleNamespace(data={"innings": rows}))
    assert [
        (item["name"], [(point["x"], point["y"]) for point in item["points"]])
        for item in chart["series"]
    ] == [("ODI", [("caught", 1), ("run out", 1)]), ("T20I", [("caught", 1)])]
    assert plotted["dismissals"] == 3
    assert plotted["excluded_incomplete_batting_innings"] == 1
    assert dismissal_chart(SimpleNamespace(data={"innings": [{**base}]}))[1][
        "legacy_missing_dismissal_kinds"
    ]


def test_match_charts_preserve_wicket_overs_and_exclude_partial_innings():
    data = {
        "match": {
            "id": "m1",
            "format": "odi",
            "teams": ["India", "Other"],
            "date_start": "2025-01-01",
        },
        "innings": [
            {
                "number": 1,
                "team": "India",
                "data_complete": True,
                "super_over": False,
                "overs": [
                    {"over_number": 0, "runs": 8, "wickets": 1, "legal_balls": 6},
                    {"over_number": 1, "runs": 0, "wickets": 0, "legal_balls": 6},
                    {"over_number": 2, "runs": 10, "wickets": 2, "legal_balls": 5},
                ],
            },
            {
                "number": 2,
                "team": "Other",
                "data_complete": False,
                "super_over": False,
                "overs": [{"over_number": 0, "runs": 999, "wickets": 0, "legal_balls": 6}],
            },
        ],
    }
    dataset = SimpleNamespace(data=data)
    manhattan, plotted = match_chart(dataset, "runs_per_over")
    worm, _ = match_chart(dataset, "cumulative_runs")
    assert manhattan["series"][0]["points"] == [
        {"x": 1, "y": 8, "wickets": 1, "legal_balls": 6, "sample_size": 6},
        {"x": 2, "y": 0, "wickets": 0, "legal_balls": 6, "sample_size": 6},
        {"x": 3, "y": 10, "wickets": 2, "legal_balls": 5, "sample_size": 5},
    ]
    assert [point["y"] for point in worm["series"][0]["points"]] == [8, 8, 18]
    assert [point["wickets"] for point in worm["series"][0]["points"]] == [1, 0, 2]
    assert plotted["excluded_incomplete_or_super_over_innings"] == 1
    assert "India vs Other, 2025-01-01 (match m1)" in worm["title"]


def test_match_chart_saved_contract_ownership_and_window_validation(session):
    owner = ConversationRepository(session, uuid.uuid4())
    owner.create(SYSTEM_PROMPT)
    other = ConversationRepository(session, uuid.uuid4())
    other.create(SYSTEM_PROMPT)
    dataset = Dataset(
        conversation_id=owner.session_id,
        kind="match_data",
        data={
            "match": {"id": "m1", "format": "odi"},
            "innings": [
                {
                    "number": 1,
                    "team": "India",
                    "data_complete": True,
                    "super_over": False,
                    "overs": [{"over_number": 0, "runs": 8, "wickets": 1, "legal_balls": 6}],
                }
            ],
        },
        provenance={"sources": [{"provider": "cricsheet", "url": "fixture"}]},
        coverage={"match_id": "m1", "method": "Penalty runs excluded."},
    )
    session.add(dataset)
    session.flush()
    arguments = {
        "dataset_id": str(dataset.id),
        "chart_type": "bar",
        "metric": "runs_per_over",
        "group_by": "over",
    }
    assert create_cricket_chart(session, other.session_id, **arguments)["error"]["code"] == (
        "dataset_not_found"
    )
    assert (
        create_cricket_chart(session, owner.session_id, **{**arguments, "window_size": 5})["error"][
            "code"
        ]
        == "invalid_arguments"
    )
    assert (
        create_cricket_chart(session, owner.session_id, **{**arguments, "metric": "runs"})["error"][
            "code"
        ]
        == "invalid_chart"
    )
    result = create_cricket_chart(session, owner.session_id, **arguments)
    assert result["ok"]
    saved = session.get(ChartSpec, uuid.UUID(result["data"]["chart_id"]))
    assert saved.spec["schema_version"] == 2
    assert saved.spec["chart"]["series"][0]["points"][0]["wickets"] == 1
    assert normalize_chart_spec(saved.spec)["chart"] == saved.spec["chart"]
    assert saved.spec["coverage"]["match_id"] == "m1"
    assert saved.spec["provenance"] == dataset.provenance["sources"]
    dataset.data = {**dataset.data, "match": {}}
    invalid = create_cricket_chart(session, owner.session_id, **arguments)
    assert invalid["error"]["code"] == "invalid_dataset"
    assert session.query(ChartSpec).count() == 1
    dataset.data = {
        **dataset.data,
        "match": {"id": "m1"},
        "innings": [
            {"number": 1, "team": "India", "data_complete": True, "super_over": False, "overs": []}
        ],
    }
    empty = create_cricket_chart(session, owner.session_id, **arguments)
    assert empty["error"]["code"] == "insufficient_data"
    assert session.query(ChartSpec).count() == 1


def test_rolling_window_argument_bounds(session, tmp_path, monkeypatch):
    repo, dataset_id = player_dataset(session, tmp_path, monkeypatch)
    arguments = {
        "dataset_id": dataset_id,
        "chart_type": "line",
        "metric": "batting_average",
        "group_by": "rolling_innings",
    }
    for value in (True, 2, 21, "5"):
        assert (
            create_cricket_chart(session, repo.session_id, **arguments, window_size=value)["error"][
                "code"
            ]
            == "invalid_arguments"
        )
    insufficient = create_cricket_chart(session, repo.session_id, **arguments)
    assert insufficient["error"]["code"] == "insufficient_data"
    assert "5 complete" in insufficient["error"]["message"]


def player_dataset(session, tmp_path, monkeypatch):
    seed_player(session, tmp_path)
    monkeypatch.setattr(
        "cricket.espn.fetch_profile",
        lambda external_id: {"athlete": {"id": external_id, "displayName": "Virat Kohli"}},
    )
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    result = get_player_data(session, repo.session_id, player_id="ba607b88", format="odi")
    return repo, result["data"]["dataset_id"]


@pytest.mark.parametrize(
    ("chart_type", "group_by", "expected_x", "expected_y"),
    [
        ("bar", "year", ["2026"], [17]),
        ("line", "innings_date", ["2026-01-01"], [17]),
        ("scatter", "innings_date", ["2026-01-01"], [17]),
        ("scatter", "balls_faced", [8], [17]),
    ],
)
def test_player_chart_values_come_from_saved_complete_innings(
    session, tmp_path, monkeypatch, chart_type, group_by, expected_x, expected_y
):
    repo, dataset_id = player_dataset(session, tmp_path, monkeypatch)
    result = create_cricket_chart(
        session,
        repo.session_id,
        dataset_id=dataset_id,
        chart_type=chart_type,
        metric="runs",
        group_by=group_by,
    )
    assert result["ok"]
    assert result["data"]["spec"]["data_ref"]["chart_id"] == result["data"]["chart_id"]
    assert "figure" not in result["data"]
    saved = session.get(ChartSpec, uuid.UUID(result["data"]["chart_id"]))
    assert saved.conversation_id == repo.session_id
    assert saved.dataset_id == uuid.UUID(dataset_id)
    assert saved.spec["schema_version"] == 2
    chart = normalize_chart_spec(saved.spec)["chart"]
    points = chart["series"][0]["points"]
    assert [point["x"] for point in points] == expected_x
    assert [point["y"] for point in points] == expected_y
    assert chart["title"].startswith("Virat Kohli:")
    assert result["coverage"]["plotted"]["batting_innings"] == 1
    assert result["coverage"]["plotted"]["formats"] == ["odi"]
    assert result["provenance"][1]["provider"] == "cricsheet"


def test_chart_is_model_callable_and_trace_stays_compact(session, tmp_path, monkeypatch):
    repo, dataset_id = player_dataset(session, tmp_path, monkeypatch)
    dataset = session.get(Dataset, uuid.UUID(dataset_id))
    dataset.data = {
        **dataset.data,
        "innings": [
            {**dataset.data["innings"][0], "match_id": str(number)} for number in range(500)
        ],
    }
    args = json.dumps(
        {
            "dataset_id": dataset_id,
            "metric": "runs",
            "group_by": "innings_date",
            "chart_type": "scatter",
        }
    )
    model = FakeModel(tool_reply(args, name="create_cricket_chart"), {"content": "Chart ready."})
    response, traces = AgentHarness(completion=model).run(repo, "Graph those scores")
    assert response == "Chart ready."
    assert len(traces[0]["result"]) < 2500
    chart_id = json.loads(traces[0]["result"])["data"]["chart_id"]
    chart = session.get(ChartSpec, uuid.UUID(chart_id))
    assert len(chart.spec["chart"]["series"][0]["points"]) == 500
    assert repo.transcript()["charts"] == [chart_id]


def test_chart_rejects_foreign_dataset_and_invalid_combinations(session, tmp_path, monkeypatch):
    owner, dataset_id = player_dataset(session, tmp_path, monkeypatch)
    other = ConversationRepository(session, uuid.uuid4())
    other.create(SYSTEM_PROMPT)
    options = {"dataset_id": dataset_id, "chart_type": "bar", "metric": "runs", "group_by": "year"}
    assert create_cricket_chart(session, other.session_id, **options)["error"]["code"] == (
        "dataset_not_found"
    )
    assert (
        create_cricket_chart(session, owner.session_id, **{**options, "dataset_id": "bad"})[
            "error"
        ]["code"]
        == "invalid_arguments"
    )
    assert (
        create_cricket_chart(session, owner.session_id, **{**options, "chart_type": "scatter"})[
            "error"
        ]["code"]
        == "invalid_chart"
    )
    assert (
        create_cricket_chart(
            session,
            owner.session_id,
            **{**options, "metric": "runs_per_100_balls", "group_by": "wicket_phase"},
        )["error"]["code"]
        == "invalid_chart"
    )
    assert list(session.scalars(select(ChartSpec))) == []


def test_chart_excludes_incomplete_innings_and_reports_empty_selection(
    session, tmp_path, monkeypatch
):
    repo, dataset_id = player_dataset(session, tmp_path, monkeypatch)
    dataset = session.get(Dataset, uuid.UUID(dataset_id))
    innings = dataset.data["innings"][0]
    dataset.data = {
        **dataset.data,
        "innings": [innings, {**innings, "data_complete": False, "runs": 999}],
    }
    options = {"dataset_id": dataset_id, "chart_type": "bar", "metric": "runs", "group_by": "year"}
    result = create_cricket_chart(session, repo.session_id, **options)
    chart = session.get(ChartSpec, uuid.UUID(result["data"]["chart_id"])).spec["chart"]
    assert [point["y"] for point in chart["series"][0]["points"]] == [17]
    assert result["coverage"]["plotted"]["excluded_incomplete_batting_innings"] == 1
    dataset.data = {**dataset.data, "innings": [{**innings, "data_complete": False}]}
    empty = create_cricket_chart(session, repo.session_id, **options)
    assert empty["error"]["code"] == "insufficient_data"
    assert empty["provenance"] and empty["coverage"]


@pytest.mark.parametrize(
    ("metric", "expected_odi", "expected_t20i"),
    [
        ("runs", [50, 9], [10]),
        ("batting_average", [50, None], [10]),
        ("mean_runs_per_innings", [25, 9], [10]),
        ("runs_per_100_legal_balls", [500, None], [200]),
        ("boundaries", [4, 0], [2]),
    ],
)
def test_annual_metrics_keep_formats_and_denominators_separate(
    session, tmp_path, monkeypatch, metric, expected_odi, expected_t20i
):
    repo, dataset_id = player_dataset(session, tmp_path, monkeypatch)
    dataset = session.get(Dataset, uuid.UUID(dataset_id))
    base = dataset.data["innings"][0]
    rows = [
        {
            **base,
            "date": "2025-01-01",
            "match_id": "a",
            "runs": 30,
            "legal_balls": 10,
            "fours": 2,
            "sixes": 1,
            "dismissed": False,
        },
        {
            **base,
            "date": "2025-02-01",
            "match_id": "b",
            "runs": 20,
            "legal_balls": 0,
            "fours": 1,
            "sixes": 0,
            "dismissed": True,
        },
        {
            **base,
            "date": "2026-01-01",
            "match_id": "c",
            "runs": 9,
            "legal_balls": 0,
            "fours": 0,
            "sixes": 0,
            "dismissed": False,
        },
        {
            **base,
            "format": "t20i",
            "date": "2025-01-01",
            "match_id": "d",
            "runs": 10,
            "legal_balls": 5,
            "fours": 1,
            "sixes": 1,
            "dismissed": True,
        },
        {**base, "date": "2025-03-01", "match_id": "e", "runs": 999, "data_complete": False},
    ]
    dataset.data = {**dataset.data, "innings": rows}
    result = create_cricket_chart(
        session,
        repo.session_id,
        dataset_id=dataset_id,
        chart_type="line",
        metric=metric,
        group_by="year",
    )
    assert result["ok"]
    saved = session.get(ChartSpec, uuid.UUID(result["data"]["chart_id"]))
    odi, t20i = saved.spec["chart"]["series"]
    assert [point["x"] for point in odi["points"]] == ["2025", "2026"]
    assert [point["y"] for point in odi["points"]] == expected_odi
    assert [point["y"] for point in t20i["points"]] == expected_t20i
    assert [point["sample_size"] for point in odi["points"]] == [2, 1]
    assert saved.spec["coverage"]["plotted"]["excluded_incomplete_batting_innings"] == 1
    if metric == "batting_average":
        assert odi["points"][0]["dismissals"] == 1
        assert "No dismissals" in odi["points"][1]["note"]
    if metric == "runs_per_100_legal_balls":
        assert odi["points"][0]["legal_balls"] == 10
        assert "No legal balls" in odi["points"][1]["note"]
    if metric == "boundaries":
        assert (odi["points"][0]["fours"], odi["points"][0]["sixes"]) == (3, 1)


def test_wicket_response_chart_uses_saved_rates(session):
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    dataset = Dataset(
        conversation_id=repo.session_id,
        kind="wicket_response",
        data={
            "rates": {
                "before": {"runs_per_100_balls": 80.0, "boundary_ball_percentage": 15.0},
                "after": {"runs_per_100_balls": 100.0, "boundary_ball_percentage": 25.0},
            }
        },
        provenance={"sources": [{"provider": "cricsheet", "url": "fixture"}]},
        coverage={"eligible_events": 12, "scope": "fixture"},
    )
    session.add(dataset)
    session.flush()
    result = create_cricket_chart(
        session,
        repo.session_id,
        dataset_id=str(dataset.id),
        chart_type="bar",
        metric="runs_per_100_balls",
        group_by="wicket_phase",
    )
    assert result["ok"]
    chart = session.get(ChartSpec, uuid.UUID(result["data"]["chart_id"]))
    assert [item["points"][0]["y"] for item in chart.spec["chart"]["series"]] == [80.0, 100.0]
    assert result["coverage"]["plotted"]["eligible_events"] == 12
    dataset.coverage = {**dataset.coverage, "eligible_events": 5}
    small = create_cricket_chart(
        session,
        repo.session_id,
        dataset_id=str(dataset.id),
        chart_type="bar",
        metric="boundary_ball_percentage",
        group_by="wicket_phase",
    )
    assert small["coverage"]["plotted"]["insufficient_sample"]
    saved = session.get(ChartSpec, uuid.UUID(small["data"]["chart_id"]))
    assert "insufficient sample" in saved.spec["chart"]["title"]
