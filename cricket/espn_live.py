"""Small, read-only ESPN cricket adapters. No imported cricket tables are consulted."""

import json
import re
from urllib.parse import urlencode

import requests

from cricket.results import error_result

SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
PROFILE_URL = "https://site.web.api.espn.com/apis/common/v3/sports/cricket/athletes/{player_id}"
HEADER_URL = "https://site.api.espn.com/apis/personalized/v2/scoreboard/header"
SUMMARY_URL = "https://site.web.api.espn.com/apis/site/v2/sports/cricket/{league_id}/summary"
MAX_RESPONSE_BYTES = 2_000_000
ID = re.compile(r"[0-9]{1,12}\Z")


def _fetch(url: str, params: dict) -> dict:
    """Only fixed HTTPS hosts/paths are used; cap provider response and latency."""
    try:
        with requests.get(url, params=params, timeout=(3, 8), stream=True) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("ESPN response exceeds size limit")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise TypeError("ESPN returned an unexpected response")
        return payload
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise ESPNUnavailable from exc


class ESPNUnavailable(Exception):
    pass


def _text(value, limit=200) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _id(value) -> str:
    return str(value) if isinstance(value, (int, str)) and ID.fullmatch(str(value)) else ""


def _nonnegative_int(value):
    try:
        number = int(value)
        return number if str(number) == str(value) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _ok(data: dict, url: str, scope: str) -> dict:
    return {
        "ok": True,
        "data": data,
        "error": None,
        "provenance": [{"provider": "espn", "url": url, "retrieval": "live"}],
        "coverage": {"scope": scope, "complete_history": False},
    }


def _unavailable() -> dict:
    return error_result(
        "provider_unavailable", "ESPN cricket data is unavailable. Please retry later."
    )


def find_espn_player(query: str) -> dict:
    """Search ESPN cricket athletes; do not infer profile statistics from identity."""
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 100:
        return error_result("invalid_query", "Enter a player name of 2–100 characters.")
    try:
        payload = _fetch(SEARCH_URL, {"query": query.strip(), "type": "player", "limit": 10})
    except ESPNUnavailable:
        return _unavailable()
    players = []
    items = payload.get("items")
    for item in items[:50] if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("sport") != "cricket" or not _id(item.get("id")):
            continue
        teams = item.get("teamRelationships")
        teams = teams if isinstance(teams, list) else []
        players.append(
            {
                "player_id": _id(item["id"]),
                "name": _text(item.get("displayName")),
                "team": _text(teams[0].get("displayName"))
                if teams and isinstance(teams[0], dict)
                else "",
            }
        )
        if len(players) >= 10:
            break
    return _ok(
        {"query": query.strip(), "players": players},
        SEARCH_URL + "?" + urlencode({"query": query.strip(), "type": "player", "limit": 10}),
        "ESPN cricket search results; not career statistics",
    )


def get_espn_player_profile(player_id: str) -> dict:
    """Retrieve one ESPN athlete identity and playing style; no career totals."""
    if not isinstance(player_id, str) or not ID.fullmatch(player_id):
        return error_result(
            "invalid_player_id", "Use a numeric ESPN player_id from find_espn_player."
        )
    url = PROFILE_URL.format(player_id=player_id)
    try:
        payload = _fetch(url, {})
    except ESPNUnavailable:
        return _unavailable()
    athlete = payload.get("athlete")
    if not isinstance(athlete, dict) or _id(athlete.get("id")) != player_id:
        return error_result("player_not_found", "ESPN did not return that player identity.")
    team = athlete.get("team")
    position = athlete.get("position")

    def style(name):
        entries = athlete.get(name)
        return (
            [_text(item.get("description"), 100) for item in entries[:3] if isinstance(item, dict)]
            if isinstance(entries, list)
            else []
        )

    return _ok(
        {
            "player_id": player_id,
            "name": _text(athlete.get("displayName")),
            "full_name": _text(athlete.get("fullName")),
            "team": _text(team.get("displayName")) if isinstance(team, dict) else "",
            "role": _text(position.get("name")) if isinstance(position, dict) else "",
            "batting_styles": style("batStyle"),
            "bowling_styles": style("bowlStyle"),
        },
        url,
        "ESPN player identity and playing style only; no career statistics",
    )


def list_espn_matches(team: str | None = None) -> dict:
    """List the matches exposed by ESPN's current cricket header, not an archive."""
    if team is not None and (not isinstance(team, str) or not 1 <= len(team.strip()) <= 100):
        return error_result("invalid_team", "Enter a team filter of 1–100 characters.")
    team = team.strip() if team is not None else None
    try:
        payload = _fetch(HEADER_URL, {"sport": "cricket"})
    except ESPNUnavailable:
        return _unavailable()
    matches = []
    sports = payload.get("sports")
    for sport in sports if isinstance(sports, list) else []:
        if not isinstance(sport, dict) or sport.get("slug") != "cricket":
            continue
        leagues = sport.get("leagues")
        for league in leagues if isinstance(leagues, list) else []:
            if not isinstance(league, dict) or not _id(league.get("id")):
                continue
            events = league.get("events")
            for event in events if isinstance(events, list) else []:
                if not isinstance(event, dict) or not _id(event.get("id")):
                    continue
                competitors = event.get("competitors")
                teams = (
                    [
                        {"name": _text(c.get("displayName")), "score": _text(c.get("score"))}
                        for c in competitors[:4]
                        if isinstance(c, dict)
                    ]
                    if isinstance(competitors, list)
                    else []
                )
                if team and team.casefold() not in " ".join(t["name"] for t in teams).casefold():
                    continue
                status = event.get("fullStatus")
                status = status if isinstance(status, dict) else {}
                matches.append(
                    {
                        "league_id": _id(league["id"]),
                        "league": _text(league.get("name")),
                        "event_id": _id(event["id"]),
                        "name": _text(event.get("name")),
                        "date": _text(event.get("date"), 40),
                        "status": _text(event.get("summary")),
                        "result": _text(status.get("longSummary")),
                        "teams": teams,
                    }
                )
                if len(matches) >= 30:
                    break
            if len(matches) >= 30:
                break
        if len(matches) >= 30:
            break
    return _ok(
        {"matches": matches, "team_filter": team or "", "limit": 30},
        HEADER_URL + "?sport=cricket",
        "Only matches in ESPN's current header, at most 30",
    )


def _scorecard(league_id: str, event_id: str) -> tuple[dict, str] | dict:
    if (
        not isinstance(league_id, str)
        or not ID.fullmatch(league_id)
        or not isinstance(event_id, str)
        or not ID.fullmatch(event_id)
    ):
        return error_result(
            "invalid_match_id", "Use numeric ESPN league_id and event_id from list_espn_matches."
        )
    url = SUMMARY_URL.format(league_id=league_id)
    try:
        payload = _fetch(url, {"event": event_id})
    except ESPNUnavailable:
        return _unavailable()
    header = payload.get("header")
    if (
        not isinstance(header, dict)
        or _id(header.get("id")) != event_id
        or not isinstance(header.get("league"), dict)
        or _id(header["league"].get("id")) != league_id
    ):
        return error_result(
            "match_not_found", "ESPN did not return that match. Check the league and event IDs."
        )
    cards = payload.get("matchcards")
    innings = {}
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict) or card.get("headline") not in {"Batting", "Bowling"}:
            continue
        number = _id(card.get("inningsNumber"))
        if not number:
            continue
        target = innings.setdefault(
            number,
            {
                "innings": int(number),
                "batting_team": "",
                "bowling_team": "",
                "runs": None,
                "total": "",
                "batting": [],
                "bowling": [],
            },
        )
        players = card.get("playerDetails")
        for player in players[:20] if isinstance(players, list) else []:
            if not isinstance(player, dict):
                continue
            base = {
                "player_id": _id(player.get("playerID")),
                "name": _text(player.get("playerName")),
            }
            if card["headline"] == "Batting":
                base.update(
                    {
                        "runs": _nonnegative_int(player.get("runs")),
                        "balls": _nonnegative_int(player.get("ballsFaced")),
                        "dismissal": _text(player.get("dismissal"), 100),
                    }
                )
                target["batting"].append(base)
            else:
                base.update(
                    {
                        "overs": _text(player.get("overs"), 20),
                        "runs_conceded": _nonnegative_int(player.get("conceded")),
                        "wickets": _nonnegative_int(player.get("wickets")),
                    }
                )
                target["bowling"].append(base)
        if card["headline"] == "Batting":
            target.update(
                {
                    "batting_team": _text(card.get("teamName")),
                    "runs": _nonnegative_int(card.get("runs")),
                    "total": _text(card.get("total")),
                }
            )
        else:
            target["bowling_team"] = _text(card.get("teamName"))
    competition = header.get("competitions")
    competition = (
        competition[0]
        if isinstance(competition, list) and competition and isinstance(competition[0], dict)
        else {}
    )
    status = competition.get("status")
    state = (
        status.get("type", {}).get("state")
        if isinstance(status, dict) and isinstance(status.get("type"), dict)
        else ""
    )
    return (
        {
            "league_id": league_id,
            "event_id": event_id,
            "name": _text(header.get("name")),
            "description": _text(header.get("description")),
            "date": _text(competition.get("date"), 40),
            "state": _text(state, 20),
            "format": _text(competition.get("class", {}).get("name"))
            if isinstance(competition.get("class"), dict)
            else "",
            "innings": sorted(innings.values(), key=lambda item: item["innings"]),
        },
        url + "?event=" + event_id,
    )


def get_espn_scorecard(league_id: str, event_id: str) -> dict:
    result = _scorecard(league_id, event_id)
    if isinstance(result, dict):
        return result
    data, url = result
    if not data["innings"]:
        return error_result(
            "scorecard_unavailable", "ESPN has no batting or bowling scorecard for this match yet."
        )
    return _ok(
        data, url, "ESPN scorecard as currently published; may be incomplete for live matches"
    )


def analyze_espn_batting_contributions(league_id: str, event_id: str) -> dict:
    """Original derived metric: top-three share of listed batter runs, excluding extras."""
    result = _scorecard(league_id, event_id)
    if isinstance(result, dict):
        return result
    card, url = result
    if card["state"] != "post":
        return error_result(
            "analysis_unavailable",
            "Wait until ESPN marks the match final before analyzing batting contributions.",
        )
    analysis = []
    for innings in card["innings"]:
        batters = [p for p in innings["batting"] if p["runs"] is not None and p["name"]]
        listed_runs = sum(p["runs"] for p in batters)
        if not listed_runs:
            continue
        leaders = sorted(batters, key=lambda p: p["runs"], reverse=True)[:3]
        leaders_runs = sum(p["runs"] for p in leaders)
        analysis.append(
            {
                "innings": innings["innings"],
                "team": innings["batting_team"],
                "listed_batter_runs": listed_runs,
                "top_three_runs": leaders_runs,
                "top_three_share_percent": round(100 * leaders_runs / listed_runs, 1),
                "top_three": [{"name": p["name"], "runs": p["runs"]} for p in leaders],
            }
        )
    if not analysis:
        return error_result(
            "analysis_unavailable", "ESPN has no scored batter rows for this match yet."
        )
    return _ok(
        {
            "league_id": league_id,
            "event_id": event_id,
            "name": card["name"],
            "innings": analysis,
            "denominator": "Sum of listed batter runs in each innings; excludes extras",
        },
        url,
        "Derived from currently available ESPN scorecard batter rows, not ball-by-ball or career data",
    )
