"""Read-only career statistics computed on demand from public Cricsheet archives; no database."""

import csv
import io
import json
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests

from cricket.results import error_result
from cricket.sources import FORMAT_FILES, SOURCE_URLS

CACHE_DIR = Path(tempfile.gettempdir()) / "cricsheet-cache"
CACHE_SECONDS = 24 * 3600
ESPN_SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
# Retirements are not dismissals; run outs and similar are not credited to bowlers.
NOT_DISMISSALS = {"retired hurt", "retired not out"}
NOT_BOWLER_WICKETS = NOT_DISMISSALS | {
    "run out",
    "retired out",
    "obstructing the field",
    "handled the ball",
    "hit the ball twice",
    "timed out",
}
_lock = threading.Lock()
_state: dict = {"loaded_at": 0.0, "stats": None, "people": None}


def _fetch(name: str) -> Path:
    """Download one public artifact unless a fresh copy is already on local disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_SECONDS:
        return path
    temporary = path.with_name(name + ".part")
    with requests.get(SOURCE_URLS[name], stream=True, timeout=(10, 60)) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                stream.write(chunk)
    temporary.replace(path)
    return path


def _blank() -> dict:
    return {
        "matches": 0,
        "innings": 0,
        "not_outs": 0,
        "runs": 0,
        "balls_faced": 0,
        "fours": 0,
        "sixes": 0,
        "highest": 0,
        "highest_not_out": False,
        "fifties": 0,
        "hundreds": 0,
        "balls_bowled": 0,
        "runs_conceded": 0,
        "wickets": 0,
    }


def _aggregate(archive: Path, stats: dict, fmt: str) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            if not member.endswith(".json"):
                continue
            match = json.loads(bundle.read(member))
            people = match.get("info", {}).get("registry", {}).get("people", {})
            rows = {}

            def row(pid):
                return rows.setdefault(pid, _blank())

            for squad in match.get("info", {}).get("players", {}).values():
                for name in squad:
                    if name in people:
                        row(people[name])["matches"] = 1
            for innings in match.get("innings", []):
                if innings.get("super_over"):
                    continue
                runs, balls, fours, sixes, seen, out = {}, {}, {}, {}, set(), set()
                for over in innings.get("overs", []):
                    for ball in over.get("deliveries", []):
                        extras = ball.get("extras", {})
                        batter, bowler = ball.get("batter"), ball.get("bowler")
                        scored = ball.get("runs", {})
                        seen.update((batter, ball.get("non_striker")))
                        runs[batter] = runs.get(batter, 0) + scored.get("batter", 0)
                        if "wides" not in extras:
                            balls[batter] = balls.get(batter, 0) + 1
                        if scored.get("batter") == 4 and not scored.get("non_boundary"):
                            fours[batter] = fours.get(batter, 0) + 1
                        if scored.get("batter") == 6:
                            sixes[batter] = sixes.get(batter, 0) + 1
                        if bowler in people:
                            b = row(people[bowler])
                            if "wides" not in extras and "noballs" not in extras:
                                b["balls_bowled"] += 1
                            b["runs_conceded"] += (
                                scored.get("total", 0)
                                - extras.get("byes", 0)
                                - extras.get("legbyes", 0)
                                - extras.get("penalty", 0)
                            )
                        for wicket in ball.get("wickets", []):
                            kind = wicket.get("kind")
                            if kind not in NOT_DISMISSALS:
                                out.add(wicket.get("player_out"))
                            if kind not in NOT_BOWLER_WICKETS and bowler in people:
                                row(people[bowler])["wickets"] += 1
                for name in seen | out:
                    if name not in people:
                        continue
                    a = row(people[name])
                    total = runs.get(name, 0)
                    a["innings"] += 1
                    a["runs"] += total
                    a["balls_faced"] += balls.get(name, 0)
                    a["fours"] += fours.get(name, 0)
                    a["sixes"] += sixes.get(name, 0)
                    a["not_outs"] += name not in out
                    a["hundreds"] += total >= 100
                    a["fifties"] += 50 <= total < 100
                    if total > a["highest"] or (total == a["highest"] and name not in out):
                        a["highest"], a["highest_not_out"] = total, name not in out
            for pid, values in rows.items():
                target = stats.setdefault((pid, fmt), _blank())
                for key, value in values.items():
                    if key == "highest":
                        if value > target["highest"]:
                            target["highest"] = value
                            target["highest_not_out"] = values["highest_not_out"]
                    elif key != "highest_not_out":
                        target[key] += value


def _load() -> tuple[dict, list[dict]]:
    """Build the in-memory index once per day per instance."""
    with _lock:
        if _state["stats"] is None or time.time() - _state["loaded_at"] > CACHE_SECONDS:
            stats: dict = {}
            for fmt, name in FORMAT_FILES.items():
                _aggregate(_fetch(name), stats, fmt)
            text = _fetch("people.csv").read_text(encoding="utf-8")
            _state.update(
                stats=stats,
                people=list(csv.DictReader(io.StringIO(text))),
                loaded_at=time.time(),
            )
        return _state["stats"], _state["people"]


def _espn_ids(query: str) -> list[str]:
    """Best-effort ESPN IDs, which match Cricsheet's key_cricinfo column."""
    try:
        payload = requests.get(
            ESPN_SEARCH_URL, params={"query": query, "type": "player", "limit": 10}, timeout=8
        ).json()
        return [
            str(item["id"])
            for item in payload.get("items", [])
            if isinstance(item, dict) and item.get("sport") == "cricket" and "id" in item
        ]
    except (requests.RequestException, ValueError):
        return []


def _summary(values: dict) -> dict:
    dismissals = values["innings"] - values["not_outs"]
    overs, balls = divmod(values["balls_bowled"], 6)
    return {
        "matches": values["matches"],
        "batting": {
            "innings": values["innings"],
            "not_outs": values["not_outs"],
            "runs": values["runs"],
            "highest_score": f"{values['highest']}{'*' if values['highest_not_out'] else ''}",
            "average": round(values["runs"] / dismissals, 2) if dismissals else None,
            "strike_rate": round(100 * values["runs"] / values["balls_faced"], 2)
            if values["balls_faced"]
            else None,
            "hundreds": values["hundreds"],
            "fifties": values["fifties"],
            "fours": values["fours"],
            "sixes": values["sixes"],
            "balls_faced": values["balls_faced"],
        },
        "bowling": {
            "overs": f"{overs}.{balls}",
            "runs_conceded": values["runs_conceded"],
            "wickets": values["wickets"],
            "average": round(values["runs_conceded"] / values["wickets"], 2)
            if values["wickets"]
            else None,
            "economy": round(6 * values["runs_conceded"] / values["balls_bowled"], 2)
            if values["balls_bowled"]
            else None,
        },
    }


def get_career_stats(player_name: str, format: str | None = None) -> dict:
    """Return ODI/T20I career statistics for one player, computed live from Cricsheet files."""
    if format not in (None, "odi", "t20i"):
        return error_result("invalid_arguments", "Format must be odi or t20i.")
    try:
        stats, people = _load()
    except (requests.RequestException, OSError, zipfile.BadZipFile, ValueError):
        return error_result(
            "provider_unavailable", "Cricsheet data is unavailable. Please retry later."
        )
    played = {pid for pid, _ in stats}
    words = player_name.casefold().split()
    # Cricsheet names are initials plus surname ("V Kohli"), so match on the surname
    # and let ESPN's full-name search pick the right person via key_cricinfo.
    surname = words[-1] if words else ""
    if not surname:
        return error_result("invalid_arguments", "Enter a player name.")
    matches = [
        person
        for person in people
        if person["identifier"] in played
        and surname in f"{person['name']} {person['unique_name']}".casefold().split()
    ]
    espn = _espn_ids(player_name)
    narrowed = sorted(
        (p for p in matches if p["key_cricinfo"] in espn),
        key=lambda p: espn.index(p["key_cricinfo"]),
    )[:1]
    exact = [
        p
        for p in matches
        if player_name.casefold() in {p["name"].casefold(), p["unique_name"].casefold()}
    ]
    matches = narrowed or exact or matches
    if not matches:
        return error_result("player_not_found", "No ODI/T20I player matches that name.")
    if len(matches) > 1:
        result = error_result("ambiguous_player", "Ask the user which player they mean.")
        result["data"] = {
            "candidates": [
                {"name": p["name"], "unique_name": p["unique_name"], "espn_id": p["key_cricinfo"]}
                for p in matches[:10]
            ]
        }
        return result
    person = matches[0]
    formats = [format] if format else ["odi", "t20i"]
    data = {
        "player": {"name": person["name"], "unique_name": person["unique_name"]},
        "formats": {
            fmt: _summary(stats[(person["identifier"], fmt)])
            for fmt in formats
            if (person["identifier"], fmt) in stats
        },
    }
    return {
        "ok": True,
        "data": data,
        "error": None,
        "provenance": [
            {"provider": "cricsheet", "url": SOURCE_URLS[FORMAT_FILES[fmt]], "retrieval": "live"}
            for fmt in formats
        ],
        "coverage": {
            "scope": "Men's ODI/T20I matches in Cricsheet; excludes Tests and domestic cricket.",
            "complete_history": False,
        },
    }
