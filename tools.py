"""Live ESPN-only tools advertised to the model and executed by the harness."""

from cricket.espn_live import (
    analyze_espn_batting_contributions,
    find_espn_player,
    get_espn_player_profile,
    get_espn_scorecard,
    list_espn_matches,
)
from cricket.results import error_result


def _tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_ID = {
    "type": "string",
    "pattern": "^[0-9]{1,12}$",
    "description": "ESPN numeric ID as a digit string.",
}
TOOLS = [
    _tool(
        "find_espn_player",
        "Search live ESPN cricket player identities. Returns candidates, not career statistics.",
        {"query": {"type": "string", "minLength": 2, "maxLength": 100}},
        ["query"],
    ),
    _tool(
        "get_espn_player_profile",
        "Fetch one ESPN cricket player's identity, team, role, batting style, and bowling style by player_id from search. No career totals.",
        {"player_id": _ID},
        ["player_id"],
    ),
    _tool(
        "list_espn_matches",
        "List up to 30 cricket matches currently exposed in ESPN's header, optionally filtered by team. This is not a historical archive.",
        {"team": {"type": "string", "minLength": 1, "maxLength": 100}},
        [],
    ),
    _tool(
        "get_espn_scorecard",
        "Fetch live ESPN batting and bowling scorecard for a numeric league_id and event_id. These IDs can come from list_espn_matches or the user.",
        {"league_id": _ID, "event_id": _ID},
        ["league_id", "event_id"],
    ),
    _tool(
        "analyze_espn_batting_contributions",
        "Compute each innings' top-three batter share of all listed batter runs on the live ESPN scorecard. Excludes extras; unavailable without scored batting rows.",
        {"league_id": _ID, "event_id": _ID},
        ["league_id", "event_id"],
    ),
]

TOOL_MAP = {
    "find_espn_player": find_espn_player,
    "get_espn_player_profile": get_espn_player_profile,
    "list_espn_matches": list_espn_matches,
    "get_espn_scorecard": get_espn_scorecard,
    "analyze_espn_batting_contributions": analyze_espn_batting_contributions,
}


def run_tool(name: str, args: dict, repo=None) -> dict:
    """Execute an advertised read-only API call; repo is deliberately unused."""
    function = TOOL_MAP.get(name)
    if function is None:
        return error_result("unknown_tool", "Use an advertised live ESPN tool.")
    try:
        return function(**args)
    except TypeError:
        return error_result("invalid_arguments", "Check the tool's required arguments.")
