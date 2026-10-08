"""The tools the harness can run, and the JSON that describes them to the model."""

import json

import requests

from cricket.career_tool import get_career_stats
from cricket.chart_tool import create_cricket_chart
from cricket.match_tool import get_match_data
from cricket.player_tool import get_player_data
from cricket.results import error_result
from cricket.variety_bubble import get_squad_comparison
from cricket.variety_heatmap import get_batter_bowler_data
from cricket.wicket_tool import analyze_wicket_response

# Open-Meteo is free and needs no API key.
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def get_weather(location: str) -> str:
    """Get the current weather for a location."""
    try:
        places = requests.get(GEOCODE_URL, params={"name": location, "count": 1}, timeout=10).json()
        if not places.get("results"):
            return json.dumps(
                error_result(
                    "city_not_found", f"City '{location}' was not found. Try another city."
                )
            )
        place = places["results"][0]

        current = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
            timeout=10,
        ).json()["current"]
    except requests.RequestException:
        return json.dumps(
            error_result("provider_unavailable", "The weather service is unavailable. Retry later.")
        )

    return json.dumps(
        {
            "location": place["name"],
            "temp_f": current["temperature_2m"],
            "humidity": current["relative_humidity_2m"],
            "wind_mph": current["wind_speed_10m"],
        }
    )


# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather (temperature, humidity, wind) for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "description": "City name, e.g. 'New York'",
                    },
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_career_stats",
            "description": (
                "Get a player's ODI and T20I career batting and bowling totals (runs, "
                "average, strike rate, centuries, wickets) computed live from public "
                "Cricsheet match files. Use this for career or past-run questions. "
                "Excludes Tests; ask the user to pick if the name is ambiguous."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "player_name": {"type": "string", "minLength": 2, "maxLength": 100},
                    "format": {"type": "string", "enum": ["odi", "t20i"]},
                },
                "required": ["player_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_player_data",
            "description": (
                "Find a Cricsheet player, get their ESPN profile and ODI/T20I statistics "
                "from imported matches, and save a dataset for charts. Resolve ambiguous "
                "names by asking for one returned player_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "player_name": {"type": "string", "minLength": 1, "maxLength": 255},
                    "player_id": {"type": "string", "minLength": 1, "maxLength": 36},
                    "format": {"type": "string", "enum": ["odi", "t20i"]},
                    "start_date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                    "end_date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                },
                "oneOf": [
                    {"required": ["player_name"]},
                    {"required": ["player_id"]},
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_match_data",
            "description": (
                "Find one imported ODI/T20I match by Cricsheet match_id or team filters, "
                "and save its complete delivery-derived data for Manhattan, worm, "
                "run-component area, and partnership charts. "
                "Ambiguous filters return candidate match_ids; ask the user to choose or narrow them. "
                "This is historical imported data, not a live score."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "match_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    "team": {"type": "string", "minLength": 1, "maxLength": 255},
                    "opponent": {"type": "string", "minLength": 1, "maxLength": 255},
                    "format": {"type": "string", "enum": ["odi", "t20i"]},
                    "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                    "year": {"type": "integer", "minimum": 1900, "maximum": 2100},
                    "event": {"type": "string", "minLength": 1, "maxLength": 255},
                },
                "oneOf": [{"required": ["match_id"]}, {"required": ["team"]}],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_batter_bowler_data",
            "description": (
                "Save one batter's imported ODI or T20I delivery matchups against bowlers by "
                "powerplay, middle, and death phase for a heatmap. Use a resolved player_id "
                "or an unambiguous player_name. This is not complete official career data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "player_id": {"type": "string", "minLength": 1, "maxLength": 36},
                    "player_name": {"type": "string", "minLength": 1, "maxLength": 255},
                    "format": {"type": "string", "enum": ["odi", "t20i"]},
                },
                "required": ["format"],
                "oneOf": [{"required": ["player_id"]}, {"required": ["player_name"]}],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_squad_comparison",
            "description": (
                "Save imported-match batting averages, runs per 100 legal balls, and balls "
                "faced for up to 20 qualifying players on one exact team in ODI or T20I. "
                "Use this dataset for a squad bubble chart."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team": {"type": "string", "minLength": 1, "maxLength": 255},
                    "format": {"type": "string", "enum": ["odi", "t20i"]},
                },
                "required": ["team", "format"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_cricket_chart",
            "description": (
                "Create a chart from a dataset_id returned by a cricket data tool in this "
                "conversation. Allowed combinations: "
                "runs, batting_average, mean_runs_per_innings, runs_per_100_legal_balls, "
                "or boundaries grouped by year as bar or line; "
                "runs/innings_date/line or scatter, "
                "runs/balls_faced/scatter, runs_per_100_balls/wicket_phase/bar, "
                "boundary_ball_percentage/wicket_phase/bar; "
                "runs_per_over/over/bar for Manhattan and cumulative_runs/over/line for worm "
                "from a match dataset; batting_average or runs_per_100_legal_balls grouped "
                "by rolling_innings as line (window_size defaults to 5, allowed 3–20); "
                "dismissals/kind/bar or donut; run_components/over/stacked_area and "
                "partnership_runs/stand/stacked_bar from a match dataset; "
                "runs_per_100_legal_balls/bowler_phase/heatmap from batter-bowler data; "
                "batting_average/strike_rate/bubble from squad comparison. "
                "Rolling baseline and batting "
                "average use available imported matches, not official career totals. "
                "Clarify an ambiguous average request before choosing a metric."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "A dataset_id returned earlier in this conversation.",
                    },
                    "chart_type": {
                        "type": "string",
                        "enum": [
                            "bar",
                            "line",
                            "scatter",
                            "donut",
                            "heatmap",
                            "bubble",
                            "stacked_area",
                            "stacked_bar",
                        ],
                    },
                    "metric": {
                        "type": "string",
                        "enum": [
                            "runs",
                            "batting_average",
                            "mean_runs_per_innings",
                            "runs_per_100_legal_balls",
                            "boundaries",
                            "runs_per_100_balls",
                            "boundary_ball_percentage",
                            "runs_per_over",
                            "cumulative_runs",
                            "dismissals",
                            "run_components",
                            "partnership_runs",
                        ],
                    },
                    "group_by": {
                        "type": "string",
                        "enum": [
                            "year",
                            "innings_date",
                            "balls_faced",
                            "wicket_phase",
                            "over",
                            "rolling_innings",
                            "kind",
                            "bowler_phase",
                            "strike_rate",
                            "stand",
                        ],
                    },
                    "window_size": {"type": "integer", "minimum": 3, "maximum": 20},
                },
                "required": ["dataset_id", "chart_type", "metric", "group_by"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_wicket_response",
            "description": (
                "Compare a resolved batter's scoring in 12 legal team deliveries before and "
                "after a teammate dismissal in imported ODI/T20I matches. This is a "
                "descriptive custom metric, not an official statistic or causal result. "
                "Use a player_id returned by get_player_data; below 10 eligible events, "
                "report insufficient sample without directional interpretation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "player_id": {"type": "string", "minLength": 1, "maxLength": 36},
                    "format": {"type": "string", "enum": ["odi", "t20i"]},
                    "start_date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                    "end_date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                },
                "required": ["player_id", "format"],
                "additionalProperties": False,
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_weather": get_weather,
    "get_career_stats": get_career_stats,
    "get_player_data": get_player_data,
    "get_match_data": get_match_data,
    "get_batter_bowler_data": get_batter_bowler_data,
    "get_squad_comparison": get_squad_comparison,
    "create_cricket_chart": create_cricket_chart,
    "analyze_wicket_response": analyze_wicket_response,
}


def run_tool(name: str, args: dict, repo=None) -> str | dict:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps(error_result("unknown_tool", "Use an advertised tool."))
    try:
        if name in {
            "get_player_data",
            "get_match_data",
            "get_batter_bowler_data",
            "get_squad_comparison",
            "create_cricket_chart",
            "analyze_wicket_response",
        }:
            if repo is None:
                return error_result(
                    "session_required", "Start a conversation before using this tool."
                )
            return TOOL_MAP[name](repo.session, repo.session_id, **args)
        return TOOL_MAP[name](**args)
    except TypeError:
        return json.dumps(error_result("invalid_arguments", "Check the tool's required arguments."))
