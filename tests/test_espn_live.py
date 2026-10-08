"""Live ESPN tool contracts with deterministic provider responses."""

from cricket import espn_live
from cricket.harness import AgentHarness
from tools import TOOL_MAP, TOOLS, run_tool


def test_only_live_espn_tools_are_advertised():
    expected = {
        "find_espn_player",
        "get_espn_player_profile",
        "list_espn_matches",
        "get_espn_scorecard",
        "analyze_espn_batting_contributions",
    }
    assert {tool["function"]["name"] for tool in TOOLS} == expected
    assert set(TOOL_MAP) == expected
    assert run_tool("get_match_data", {"match_id": "123"})["error"]["code"] == "unknown_tool"


def test_player_search_filters_non_cricket_and_bounds_results(monkeypatch):
    monkeypatch.setattr(
        espn_live,
        "_fetch",
        lambda *_: {
            "items": [
                {"id": "1", "displayName": "Other", "sport": "football"},
                {
                    "id": "253802",
                    "displayName": "Virat Kohli",
                    "sport": "cricket",
                    "teamRelationships": [{"displayName": "India"}],
                },
            ]
        },
    )
    result = run_tool("find_espn_player", {"query": "Virat Kohli"})
    assert result["ok"]
    assert result["data"]["players"] == [
        {"player_id": "253802", "name": "Virat Kohli", "team": "India"}
    ]
    assert result["provenance"][0]["provider"] == "espn"
    assert run_tool("find_espn_player", {"query": " "})["error"]["code"] == "invalid_query"


def test_profile_returns_identity_and_style_without_career_totals(monkeypatch):
    monkeypatch.setattr(
        espn_live,
        "_fetch",
        lambda *_: {
            "athlete": {
                "id": "253802",
                "displayName": "Virat Kohli",
                "fullName": "Virat Kohli",
                "team": {"displayName": "India"},
                "position": {"name": "Top-order batter"},
                "batStyle": [{"description": "Right-hand bat"}],
                "bowlStyle": [{"description": "Right-arm medium"}],
                "statistics": {"runs": 99999},
            }
        },
    )
    result = run_tool("get_espn_player_profile", {"player_id": "253802"})
    assert result["ok"]
    assert result["data"]["batting_styles"] == ["Right-hand bat"]
    assert result["data"]["role"] == "Top-order batter"
    assert "statistics" not in result["data"]
    assert (
        run_tool("get_espn_player_profile", {"player_id": "../1"})["error"]["code"]
        == "invalid_player_id"
    )


def test_header_match_list_and_filter(monkeypatch):
    monkeypatch.setattr(
        espn_live,
        "_fetch",
        lambda *_: {
            "sports": [
                {
                    "slug": "cricket",
                    "leagues": [
                        {
                            "id": "19439",
                            "name": "League",
                            "events": [
                                {
                                    "id": "1554411",
                                    "name": "USA v NAM",
                                    "date": "2026-10-07T15:00Z",
                                    "summary": "Result",
                                    "fullStatus": {"longSummary": "USA won"},
                                    "competitors": [
                                        {
                                            "displayName": "United States of America",
                                            "score": "299/6",
                                        },
                                        {"displayName": "Namibia", "score": "298/10"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    result = run_tool("list_espn_matches", {"team": "Namibia"})
    assert result["data"]["matches"][0]["event_id"] == "1554411"
    assert result["data"]["matches"][0]["league_id"] == "19439"
    assert run_tool("list_espn_matches", {"team": "India"})["data"]["matches"] == []


def _summary(state="post"):
    return {
        "header": {
            "id": "1554411",
            "name": "USA v NAM",
            "description": "Match",
            "league": {"id": "19439"},
            "competitions": [
                {
                    "date": "2026-10-07T15:00Z",
                    "status": {"type": {"state": state}},
                    "class": {"name": "One-Day Internationals"},
                }
            ],
        },
        "matchcards": [
            {
                "headline": "Batting",
                "inningsNumber": "1",
                "teamName": "Namibia",
                "runs": "120",
                "total": "(all out)",
                "playerDetails": [
                    {"playerID": "1", "playerName": "A", "runs": "50", "ballsFaced": "60"},
                    {"playerID": "2", "playerName": "B", "runs": "30", "ballsFaced": "40"},
                    {"playerID": "3", "playerName": "C", "runs": "20", "ballsFaced": "30"},
                    {"playerID": "4", "playerName": "D", "runs": "10", "ballsFaced": "20"},
                ],
            },
            {
                "headline": "Bowling",
                "inningsNumber": "1",
                "teamName": "U.S.A.",
                "playerDetails": [
                    {
                        "playerID": "5",
                        "playerName": "E",
                        "overs": "10.0",
                        "conceded": "20",
                        "wickets": "3",
                    }
                ],
            },
        ],
    }


def test_scorecard_and_original_metric_excludes_extras(monkeypatch):
    monkeypatch.setattr(espn_live, "_fetch", lambda *_: _summary())
    scorecard = run_tool("get_espn_scorecard", {"league_id": "19439", "event_id": "1554411"})
    assert scorecard["ok"]
    assert scorecard["data"]["innings"][0]["bowling"][0]["wickets"] == 3
    analysis = run_tool(
        "analyze_espn_batting_contributions", {"league_id": "19439", "event_id": "1554411"}
    )
    assert analysis["ok"]
    innings = analysis["data"]["innings"][0]
    assert innings["listed_batter_runs"] == 110
    assert innings["top_three_runs"] == 100
    assert innings["top_three_share_percent"] == 90.9
    assert "excludes extras" in analysis["data"]["denominator"]


def test_live_match_analysis_waits_until_final(monkeypatch):
    monkeypatch.setattr(espn_live, "_fetch", lambda *_: _summary("in"))
    result = run_tool(
        "analyze_espn_batting_contributions", {"league_id": "19439", "event_id": "1554411"}
    )
    assert result["error"]["code"] == "analysis_unavailable"


def test_unexpected_identity_and_provider_errors(monkeypatch):
    monkeypatch.setattr(espn_live, "_fetch", lambda *_: {"header": {"id": "wrong"}})
    result = run_tool("get_espn_scorecard", {"league_id": "19439", "event_id": "1554411"})
    assert result["error"]["code"] == "match_not_found"
    assert (
        run_tool("get_espn_scorecard", {"league_id": "../1", "event_id": "1554411"})["error"][
            "code"
        ]
        == "invalid_match_id"
    )

    def unavailable(*_):
        raise espn_live.ESPNUnavailable()

    monkeypatch.setattr(espn_live, "_fetch", unavailable)
    assert run_tool("list_espn_matches", {})["error"]["code"] == "provider_unavailable"


def test_old_imported_conversation_cannot_be_reused_as_espn_evidence():
    class OldSession:
        def __init__(self):
            self.saved = None

        def recover_interrupted_turns(self):
            pass

        def messages(self):
            return [
                type("Message", (), {"role": "system", "payload": {"content": "Use Cricsheet"}})()
            ]

        def tool_calls(self):
            return []

        def start_turn(self, message):
            self.saved = message
            return 1

        def finish_turn(self, number, response):
            self.response = response

    session = OldSession()
    harness = AgentHarness(
        completion=lambda **_: (_ for _ in ()).throw(AssertionError("model called"))
    )
    response, traces = harness.run(session, "Now use ESPN")
    assert session.saved == "Now use ESPN"
    assert "Start a new conversation" in response
    assert traces == []
