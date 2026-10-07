"""Partnership boundaries and saved stacked-bar chart semantics."""

from types import SimpleNamespace

from cricket.variety_partnership import innings_partnerships, partnership_chart

NAMES = {"a": "Alice", "b": "Beth", "c": "Cara", "d": "Dee"}


def ball(number, striker, non_striker, batter_runs=0, extras=0):
    return SimpleNamespace(
        id=number,
        sequence=number,
        batter_id=striker,
        non_striker_id=non_striker,
        batter_runs=batter_runs,
        extras_runs=extras,
        total_runs=batter_runs + extras,
        data_complete=True,
    )


def wicket(ball_id, player_id, kind="caught", ordinal=1):
    return SimpleNamespace(
        delivery_id=ball_id,
        player_out_id=player_id,
        kind=kind,
        ordinal=ordinal,
    )


def dataset(stands=None, *, error=None, incomplete=False, super_over=False):
    total = sum(stand["runs"] for stand in stands) if stands else 0
    first = {
        "number": 1,
        "team": "India",
        "data_complete": True,
        "super_over": False,
        "partnerships": stands,
        "overs": [{"runs": total}],
    }
    if error:
        first["partnerships_error"] = error
    return SimpleNamespace(
        data={
            "match": {
                "id": "990001",
                "date_start": "2026-01-01",
                "teams": ["India", "Test XI"],
            },
            "innings": [
                first,
                {
                    "number": 2,
                    "team": "Test XI",
                    "data_complete": not incomplete,
                    "super_over": super_over,
                    "partnerships": stands,
                    "overs": [{"runs": total}],
                },
            ],
        }
    )


def test_stand_survives_strike_switch_and_allocates_extras_once():
    balls = [ball(1, "a", "b", 1, 1), ball(2, "b", "a", 4, 2), ball(3, "a", "b", 2)]
    stands, error = innings_partnerships(balls, {}, NAMES)
    assert error is None
    assert stands == [
        {
            "number": 1,
            "batter_a_id": "a",
            "batter_a_name": "Alice",
            "batter_a_runs": 3,
            "batter_b_id": "b",
            "batter_b_name": "Beth",
            "batter_b_runs": 4,
            "extras": 3,
            "runs": 10,
            "deliveries": 3,
            "ended_by": [],
        }
    ]


def test_wicket_and_retirement_close_stands_after_delivery():
    balls = [
        ball(1, "a", "b", 1),
        ball(2, "b", "a", 2, 1),
        ball(3, "c", "a", 4),
        ball(4, "a", "d", 3),
    ]
    wickets = {
        2: [wicket(2, "b")],
        3: [wicket(3, "c", "retired hurt")],
    }
    stands, error = innings_partnerships(balls, wickets, NAMES)
    assert error is None
    assert [(stand["runs"], stand["ended_by"]) for stand in stands] == [
        (4, [{"player_id": "b", "kind": "caught"}]),
        (4, [{"player_id": "c", "kind": "retired hurt"}]),
        (3, []),
    ]
    assert sum(stand["runs"] for stand in stands) == sum(ball.total_runs for ball in balls)


def test_multiple_wickets_on_ball_close_only_one_stand():
    balls = [ball(1, "a", "b", 1), ball(2, "c", "d", 2)]
    wickets = {1: [wicket(1, "a", ordinal=1), wicket(1, "b", "run out", ordinal=2)]}
    stands, error = innings_partnerships(balls, wickets, NAMES)
    assert error is None
    assert len(stands) == 2
    assert len(stands[0]["ended_by"]) == 2


def test_unresolved_pair_or_wicket_returns_reason_and_no_partial_stands():
    for balls, wickets in (
        ([ball(1, "a", "b"), ball(2, "a", "c")], {}),
        ([ball(1, "a", "b")], {1: [wicket(1, "c")]}),
        ([ball(1, "a", "b"), ball(3, "a", "b")], {}),
        ([ball(1, "a", "b")], {999: [wicket(999, "a")]}),
        ([ball(1, "a", "b")], {}),
    ):
        names = NAMES if len(balls) != 1 or wickets else {"a": "Alice"}
        stands, reason = innings_partnerships(balls, wickets, names)
        assert stands == [] and reason

    missing_sequence = ball(1, "a", "b")
    missing_sequence.sequence = None
    stands, reason = innings_partnerships([missing_sequence], {}, NAMES)
    assert stands == [] and "sequence" in reason

    invalid_runs = ball(1, "a", "b", 2)
    invalid_runs.total_runs = 1
    stands, reason = innings_partnerships([invalid_runs], {}, NAMES)
    assert stands == [] and "runs" in reason


def test_chart_contains_per_innings_stacks_names_and_match_context():
    stands, error = innings_partnerships([ball(1, "a", "b", 1, 2), ball(2, "b", "a", 4)], {}, NAMES)
    assert error is None
    chart, plotted = partnership_chart(dataset(stands))
    assert chart["metric"] == "partnership_runs"
    assert (chart["group_by"], chart["chart_type"]) == ("stand", "stacked_bar")
    assert "990001" in chart["title"] and "India" in chart["title"]
    assert [series["component"] for series in chart["series"]] == [
        "batter_a",
        "batter_b",
        "extras",
        "batter_a",
        "batter_b",
        "extras",
    ]
    assert [series["points"][0]["y"] for series in chart["series"][:3]] == [1, 4, 2]
    assert chart["series"][0]["points"][0]["batter_name"] == "Alice"
    assert chart["series"][1]["points"][0]["batter_id"] == "b"
    assert chart["series"][3]["points"][0]["x"] == "I2 · Stand 1"
    assert plotted["stands"] == 2 and plotted["data_points"] == 6


def test_incomplete_and_super_overs_excluded_and_legacy_returns_no_chart():
    stands, _ = innings_partnerships([ball(1, "a", "b", 1)], {}, NAMES)
    for options in ({"incomplete": True}, {"super_over": True}):
        chart, plotted = partnership_chart(dataset(stands, **options))
        assert chart is not None
        assert plotted["innings"] == 1
        assert plotted["excluded_incomplete_or_super_over_innings"] == 1
    chart, plotted = partnership_chart(dataset(None))
    assert chart is None and "predates" in plotted["insufficient_data_reason"]
    chart, plotted = partnership_chart(dataset(stands, error="Unresolved pair"))
    assert chart is None and plotted["insufficient_data_reason"] == "Unresolved pair"
