"""Focused checks for dismissal donut calculation from saved player innings."""

from types import SimpleNamespace

import pytest

from cricket.variety_donut import donut_chart


def innings(match_id, cricket_format="odi", *, complete=True, events=None):
    row = {
        "format": cricket_format,
        "date": "2025-01-01",
        "match_id": match_id,
        "innings_number": 1,
        "runs": 20,
        "legal_balls": 12,
        "fours": 2,
        "sixes": 1,
        "dismissed": bool(events),
        "data_complete": complete,
    }
    if events is not None:
        row["dismissal_kinds"] = events
    return row


def event(identifier, kind):
    return {"dismissal_id": identifier, "kind": kind}


def dataset(rows):
    return SimpleNamespace(data={"innings": rows})


def test_donut_uses_separate_format_rings_and_complete_innings():
    rows = [
        innings("odi-1", events=[event("odi-1:1", "caught")]),
        innings("odi-2", events=[event("odi-2:1", "bowled")]),
        innings("odi-3", events=[event("odi-3:1", "caught")]),
        innings("t20i-1", "t20i", events=[event("t20i-1:1", "run out")]),
        innings("partial", complete=False, events=[event("partial:1", "stumped")]),
    ]

    chart, plotted = donut_chart(dataset(rows))

    assert (chart["metric"], chart["group_by"], chart["chart_type"]) == (
        "dismissals",
        "kind",
        "donut",
    )
    assert [
        (item["name"], [(point["x"], point["y"]) for point in item["points"]])
        for item in chart["series"]
    ] == [
        ("ODI", [("bowled", 1), ("caught", 2)]),
        ("T20I", [("run out", 1)]),
    ]
    assert chart["series"][0]["points"][0]["sample_size"] == 3
    assert plotted["batting_innings"] == 4
    assert plotted["dismissals"] == 4
    assert plotted["excluded_incomplete_batting_innings"] == 1
    assert plotted["formats"] == ["odi", "t20i"]
    assert plotted["data_points"] == 3


def test_donut_rejects_duplicate_or_invalid_saved_dismissal_ids():
    repeated = [
        innings("odi-1", events=[event("same-id", "caught")]),
        innings("t20i-1", "t20i", events=[event("same-id", "bowled")]),
    ]
    with pytest.raises(ValueError, match="Invalid saved dismissal data"):
        donut_chart(dataset(repeated))
    with pytest.raises(ValueError, match="Invalid saved dismissal data"):
        donut_chart(dataset([innings("odi-1", events=[event("", "caught")])]))


def test_donut_legacy_missing_kinds_returns_refresh_flag():
    rows = [
        innings("older-record"),
        innings("new-record", events=[event("new:1", "caught")]),
    ]
    chart, plotted = donut_chart(dataset(rows))
    assert chart is None
    assert plotted["legacy_missing_dismissal_kinds"] is True
    assert plotted["dismissals"] == 1


def test_donut_excludes_retirement_and_not_out_events():
    rows = [
        innings(
            "odi-1",
            events=[
                event("odi-1:1", "retired hurt"),
                event("odi-1:2", "retired not out"),
                event("odi-1:3", "not out"),
                event("odi-1:4", "caught"),
            ],
        )
    ]
    chart, plotted = donut_chart(dataset(rows))
    assert chart["series"][0]["points"] == [{"x": "caught", "y": 1, "sample_size": 1}]
    assert plotted["dismissals"] == 1


def test_donut_without_scoreboard_dismissals_has_no_chart():
    chart, plotted = donut_chart(
        dataset([innings("odi-1", events=[event("odi-1:1", "retired hurt")])])
    )
    assert chart is None
    assert plotted["dismissals"] == 0
    assert plotted["legacy_missing_dismissal_kinds"] is False
