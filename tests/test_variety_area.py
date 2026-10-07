"""Delivery reconciliation and saved stacked-area chart contract."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from cricket.variety_area import COMPONENTS, area_chart, over_components


def ball(over, batter, extras=0, *, boundary=False, complete=True):
    return SimpleNamespace(
        over_number=over,
        batter_runs=batter,
        extras_runs=extras,
        total_runs=batter + extras,
        is_boundary=boundary,
        data_complete=complete,
    )


def dataset(innings):
    return SimpleNamespace(
        data={
            "match": {
                "id": "990001",
                "teams": ["India", "Test XI"],
                "date_start": "2026-01-01",
            },
            "innings": innings,
        }
    )


def entry(number, team, overs, *, complete=True, super_over=False):
    return {
        "number": number,
        "team": team,
        "overs": overs,
        "data_complete": complete,
        "super_over": super_over,
        "penalty_pre": 5,
        "penalty_post": 1,
    }


def test_delivery_components_reconcile_without_misclassifying_nonboundary_four():
    deliveries = [
        ball(0, 1),
        ball(0, 2),
        ball(0, 3),
        ball(0, 4, boundary=True),
        ball(0, 6, boundary=True),
        ball(0, 4),  # Cricsheet non_boundary=true (for example, overthrows).
        ball(0, 0, extras=2),
        ball(1, 0, extras=1),  # Wide/no-ball extras still belong to their over.
    ]
    result = over_components(deliveries)
    assert result == {
        0: {"running": 6, "boundary": 10, "other_batter": 4, "extras": 2},
        1: {"running": 0, "boundary": 0, "other_batter": 0, "extras": 1},
    }
    assert sum(sum(values.values()) for values in result.values()) == sum(
        delivery.total_runs for delivery in deliveries
    )
    assert tuple(result[0]) == COMPONENTS


@pytest.mark.parametrize(
    "change",
    [
        {"batter_runs": None},
        {"extras_runs": -1},
        {"total_runs": 3},
        {"is_boundary": None},
        {"is_boundary": True},
        {"data_complete": False},
        {"over_number": -1},
    ],
)
def test_invalid_delivery_is_rejected(change):
    delivery = ball(0, 1)
    for key, value in change.items():
        setattr(delivery, key, value)
    with pytest.raises(ValueError, match="Invalid delivery"):
        over_components([delivery])


def test_area_chart_separates_innings_and_preserves_one_based_overs():
    first = [
        ball(0, 4, boundary=True),
        ball(0, 1, extras=1),
        ball(1, 4),
    ]
    second = [ball(0, 3), ball(0, 0, extras=2)]
    first_parts = over_components(first)
    second_parts = over_components(second)
    saved = dataset(
        [
            entry(
                1,
                "India",
                [
                    {"over_number": 0, "runs": 6, "legal_balls": 2, "components": first_parts[0]},
                    {"over_number": 1, "runs": 4, "legal_balls": 1, "components": first_parts[1]},
                ],
            ),
            entry(
                2,
                "Test XI",
                [{"over_number": 0, "runs": 5, "legal_balls": 2, "components": second_parts[0]}],
            ),
            entry(3, "India", [], complete=False),
            entry(4, "India", [], super_over=True),
        ]
    )
    chart, plotted = area_chart(saved)
    assert chart["metric"] == "run_components"
    assert chart["group_by"] == "over"
    assert chart["chart_type"] == "stacked_area"
    assert "990001" in chart["title"]
    assert plotted["innings"] == 2
    assert plotted["excluded_incomplete_or_super_over_innings"] == 2
    assert plotted["data_points"] == 12
    assert len(chart["series"]) == 8
    assert {series["innings_number"] for series in chart["series"]} == {1, 2}
    assert [series["component"] for series in chart["series"][:4]] == list(COMPONENTS)
    assert [point["x"] for point in chart["series"][0]["points"]] == [1, 2]
    assert [point["y"] for point in chart["series"][2]["points"]] == [0, 4]
    assert [point["y"] for point in chart["series"][7]["points"]] == [2]
    assert "penalty" in plotted["method"].lower()


def test_area_chart_does_not_plot_legacy_or_inconsistent_components():
    over = {
        "over_number": 0,
        "runs": 5,
        "legal_balls": 6,
        "components": {"running": 1, "boundary": 4, "other_batter": 0, "extras": 0},
    }
    saved = dataset([entry(1, "India", [over])])
    chart, _ = area_chart(saved)
    assert chart is not None
    legacy = deepcopy(saved)
    del legacy.data["innings"][0]["overs"][0]["components"]
    chart, plotted = area_chart(legacy)
    assert chart is None and plotted["legacy_missing_components"] is True
    invalid = deepcopy(saved)
    invalid.data["innings"][0]["overs"][0]["components"]["extras"] = 1
    with pytest.raises(ValueError, match="Invalid saved over components"):
        area_chart(invalid)


def test_area_chart_has_no_series_for_only_incomplete_or_super_overs():
    chart, plotted = area_chart(
        dataset([entry(1, "India", [], complete=False), entry(2, "India", [], super_over=True)])
    )
    assert chart is None
    assert plotted["innings"] == 0
    assert plotted["excluded_incomplete_or_super_over_innings"] == 2
