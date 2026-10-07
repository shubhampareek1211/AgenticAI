"""Heatmap calculations from imported delivery grain and saved datasets."""

import uuid

import pytest

from cricket.conversations import ConversationRepository
from cricket.harness import SYSTEM_PROMPT
from cricket.ingest import import_archive, import_register
from cricket.models import Dataset
from cricket.variety_heatmap import get_batter_bowler_data, heatmap_chart
from tests.conftest import FIXTURES
from tests.helpers import archive, synthetic_match


def ball(runs=0, *, batter="V Kohli", bowler="JJ Bumrah", extras=None):
    extras = extras or {}
    return {
        "batter": batter,
        "non_striker": "RG Sharma" if batter == "V Kohli" else "V Kohli",
        "bowler": bowler,
        "runs": {
            "batter": runs,
            "extras": sum(extras.values()),
            "total": runs + sum(extras.values()),
        },
        **({"extras": extras} if extras else {}),
    }


def phase_match(cricket_format="ODI"):
    payload = synthetic_match()
    payload["info"]["match_type"] = cricket_format
    death = 40 if cricket_format == "ODI" else 15
    middle = 10 if cricket_format == "ODI" else 6
    overs = []
    for number in range(death + 1):
        deliveries = [ball(batter="RG Sharma") for _ in range(6)]
        if number == 0:
            deliveries = [ball(1) for _ in range(6)]
            deliveries.extend((ball(4, extras={"noballs": 1}), ball(extras={"wides": 1})))
        elif number == middle:
            deliveries = [ball(2) for _ in range(6)]
        elif number == death:
            deliveries = [ball(4) for _ in range(6)]
        overs.append({"over": number, "deliveries": deliveries})
    payload["innings"][0]["overs"] = overs
    return payload


def seed(session, tmp_path, payload, match_id="990020"):
    import_register(session, FIXTURES / "people.csv", FIXTURES / "names.csv")
    import_archive(
        session,
        archive(tmp_path, payload, match_id),
        "odi" if payload["info"]["match_type"] == "ODI" else "t20i",
    )
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    return repo.session_id


@pytest.mark.parametrize(("cricket_format", "format_key"), (("ODI", "odi"), ("T20", "t20i")))
def test_heatmap_phase_rates_include_batter_runs_on_illegal_balls(
    session, tmp_path, cricket_format, format_key
):
    conversation_id = seed(session, tmp_path, phase_match(cricket_format))
    result = get_batter_bowler_data(
        session, conversation_id, player_id="ba607b88", format=format_key
    )
    assert result["ok"]
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    assert dataset.kind == "batter_bowler_data"
    assert dataset.conversation_id == conversation_id
    assert result["coverage"]["matches"] == 1
    assert result["coverage"]["batting_innings"] == 1
    assert result["provenance"][0]["provider"] == "cricsheet"
    chart, plotted = heatmap_chart(dataset)
    assert chart["chart_type"] == "heatmap"
    assert chart["row_categories"] == ["Powerplay", "Middle", "Death"]
    assert chart["x_categories"] == ["JJ Bumrah"]
    assert plotted["data_points"] == 3
    assert chart["series"][0]["points"] == [
        {
            "x": "JJ Bumrah",
            "row": "Powerplay",
            "y": 166.67,
            "sample_size": 6,
            "legal_balls": 6,
            "runs": 10,
            "bowler_id": "462411b3",
        },
        {
            "x": "JJ Bumrah",
            "row": "Middle",
            "y": 200.0,
            "sample_size": 6,
            "legal_balls": 6,
            "runs": 12,
            "bowler_id": "462411b3",
        },
        {
            "x": "JJ Bumrah",
            "row": "Death",
            "y": 400.0,
            "sample_size": 6,
            "legal_balls": 6,
            "runs": 24,
            "bowler_id": "462411b3",
        },
    ]


def test_heatmap_excludes_incomplete_and_super_over_and_blanks_small_cells(session, tmp_path):
    payload = phase_match()
    payload["innings"].append(
        {
            "team": "India",
            "super_over": True,
            "overs": [{"over": 0, "deliveries": [ball(6) for _ in range(6)]}],
        }
    )
    payload["innings"].append(
        {
            "team": "India",
            "missing": [{"overs": "unknown"}],
            "overs": [{"over": 0, "deliveries": [ball(6) for _ in range(6)]}],
        }
    )
    # Add five legal balls against a second stable bowler, plus a no-ball run.
    payload["innings"][0]["overs"][1]["deliveries"] = [
        ball(1, bowler="Other Batter") for _ in range(5)
    ] + [ball(batter="RG Sharma")]
    payload["innings"][0]["overs"][1]["deliveries"].append(
        ball(4, bowler="Other Batter", extras={"noballs": 1})
    )
    conversation_id = seed(session, tmp_path, payload)
    result = get_batter_bowler_data(
        session, conversation_id, player_name="Virat Kohli", format="odi"
    )
    assert result["ok"]
    assert result["coverage"]["excluded_super_overs"] == 1
    assert result["coverage"]["excluded_incomplete_innings"] == 1
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    chart, plotted = heatmap_chart(dataset)
    assert plotted["data_points"] == 3
    assert plotted["blank_cells"] == 3
    second = [point for point in chart["series"][0]["points"] if point["bowler_id"] == "other001"]
    assert second[0]["y"] is None
    assert second[0]["sample_size"] == 5
    assert second[0]["runs"] == 9
    assert all(point["y"] is None for point in second)


def test_heatmap_rejects_bad_filters_without_dataset(session, tmp_path):
    conversation_id = seed(session, tmp_path, phase_match())
    for options, code in (
        ({"player_id": "ba607b88", "format": "test"}, "invalid_arguments"),
        ({"player_id": "ba607b88", "format": []}, "invalid_arguments"),
        (
            {"player_id": "ba607b88", "player_name": "Virat Kohli", "format": "odi"},
            "invalid_arguments",
        ),
        ({"player_id": "missing", "format": "odi"}, "player_not_found"),
    ):
        assert get_batter_bowler_data(session, conversation_id, **options)["error"]["code"] == code
    assert session.query(Dataset).count() == 0


def test_heatmap_chart_rejects_missing_denominator_and_duplicate_cells():
    data = {
        "identity": {"player_id": "b1", "name": "Batter"},
        "format": "odi",
        "phases": ["Powerplay", "Middle", "Death"],
        "bowlers": [{"bowler_id": "w1", "name": "Bowler", "label": "Bowler", "legal_balls": 6}],
        "cells": [
            {"bowler_id": "w1", "phase": phase, "runs": 0, "legal_balls": 0}
            for phase in ("Powerplay", "Middle", "Death")
        ],
    }
    chart, plotted = heatmap_chart(Dataset(data=data))
    assert chart is None
    assert plotted["blank_cells"] == 3
    data["cells"][0]["legal_balls"] = -1
    with pytest.raises(ValueError, match="Invalid batter-bowler dataset"):
        heatmap_chart(Dataset(data=data))
    data["cells"][0]["legal_balls"] = 0
    data["cells"][1]["phase"] = "Powerplay"
    with pytest.raises(ValueError, match="Invalid batter-bowler dataset"):
        heatmap_chart(Dataset(data=data))
