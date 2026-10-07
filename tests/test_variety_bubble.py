"""Squad bubble charts derive from deduplicated complete batting innings."""

import uuid

from cricket.conversations import ConversationRepository
from cricket.harness import SYSTEM_PROMPT
from cricket.ingest import import_archive
from cricket.models import Dataset
from cricket.variety_bubble import bubble_chart, get_squad_comparison
from tests.helpers import archive, synthetic_match


def _match(date, *, incomplete=False):
    payload = synthetic_match()
    payload["info"]["dates"] = [date]
    if incomplete:
        payload["innings"][0]["overs"] = []
        return payload
    deliveries = []
    for ball in range(36):
        batter = "V Kohli" if ball % 2 == 0 else "RG Sharma"
        deliveries.append(
            {
                "batter": batter,
                "non_striker": "RG Sharma" if ball % 2 == 0 else "V Kohli",
                "bowler": "JJ Bumrah",
                "runs": {
                    "batter": 2 if ball % 2 == 0 else 1,
                    "extras": 0,
                    "total": 2 if ball % 2 == 0 else 1,
                },
            }
        )
    deliveries[-1]["wickets"] = [{"player_out": "RG Sharma", "kind": "caught"}]
    payload["innings"][0]["overs"] = [
        {"over": over, "deliveries": deliveries[over * 6 : (over + 1) * 6]} for over in range(6)
    ]
    return payload


def _load(session, tmp_path):
    for index in range(1, 7):
        import_archive(
            session,
            archive(
                tmp_path,
                _match(f"2026-01-{index:02d}", incomplete=index == 6),
                str(990000 + index),
            ),
            "odi",
        )
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    return repo


def test_squad_comparison_uses_exact_team_complete_innings_and_denominators(session, tmp_path):
    repo = _load(session, tmp_path)
    result = get_squad_comparison(session, repo.session_id, team="india", format="odi")
    assert result["ok"]
    assert result["coverage"]["matches"] == 6
    assert result["coverage"]["complete_batting_team_innings"] == 5
    assert result["coverage"]["excluded_incomplete_innings"] == 1
    assert result["coverage"]["eligible_batters"] == 2
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    assert dataset.conversation_id == repo.session_id
    assert dataset.kind == "squad_comparison"
    chart, plotted = bubble_chart(dataset)
    assert plotted["undefined_average"] == 1
    points = {point["name"]: point for point in chart["series"][0]["points"]}
    assert points["V Kohli"]["x"] is None
    assert points["V Kohli"]["y"] == 200
    assert points["V Kohli"]["size"] == 90
    assert points["V Kohli"]["sample_size"] == 5
    assert points["RG Sharma"]["x"] == 18
    assert points["RG Sharma"]["y"] == 100
    assert points["RG Sharma"]["dismissals"] == 5
    assert len(result["provenance"]) >= 1


def test_squad_comparison_requires_exact_team_and_format(session, tmp_path):
    repo = _load(session, tmp_path)
    assert (
        get_squad_comparison(session, repo.session_id, team="Ind", format="odi")["error"]["code"]
        == "team_not_found"
    )
    assert (
        get_squad_comparison(session, repo.session_id, team="India", format="test")["error"]["code"]
        == "invalid_arguments"
    )
    assert (
        get_squad_comparison(session, repo.session_id, team="Test XI", format="t20i")["error"][
            "code"
        ]
        == "team_not_found"
    )


def test_bubble_chart_rejects_malformed_saved_aggregates(session, tmp_path):
    repo = _load(session, tmp_path)
    result = get_squad_comparison(session, repo.session_id, team="India", format="odi")
    dataset = session.get(Dataset, uuid.UUID(result["data"]["dataset_id"]))
    row = dict(dataset.data["players"][0])
    row["legal_balls"] = -1
    dataset.data = {**dataset.data, "players": [row]}
    import pytest

    with pytest.raises(ValueError, match="Invalid squad comparison player"):
        bubble_chart(dataset)
