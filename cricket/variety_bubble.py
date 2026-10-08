"""Imported-match squad batting comparison, with one bubble per eligible batter."""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from cricket.models import (
    Dataset,
    Delivery,
    Innings,
    Match,
    MatchPlayer,
    Player,
    SourceImport,
    Wicket,
)
from cricket.queries import NOT_DISMISSALS
from cricket.results import error_result

MIN_LEGAL_BALLS = 60
MIN_BATTING_INNINGS = 5
MAX_BATTERS = 20


def get_squad_comparison(
    session: Session, conversation_id: uuid.UUID, *, team: str, format: str
) -> dict:
    """Save batting aggregates for one exact team and format from complete regular innings."""
    if (
        not isinstance(team, str)
        or not team.strip()
        or not isinstance(format, str)
        or format not in {"odi", "t20i"}
    ):
        return error_result("invalid_arguments", "Supply a team name and ODI or T20I format.")
    requested_team = team.strip().casefold()
    matches = [
        match
        for match in session.scalars(select(Match).where(Match.format == format))
        if isinstance(match.teams, list)
        and any(isinstance(name, str) and name.casefold() == requested_team for name in match.teams)
    ]
    if not matches:
        return error_result(
            "team_not_found", "No imported matches have that exact team and format."
        )
    match_by_id = {match.id: match for match in matches}
    match_ids = list(match_by_id)
    roster = {
        (row.match_id, row.player_id)
        for row in session.scalars(
            select(MatchPlayer).where(
                MatchPlayer.match_id.in_(match_ids),
            )
        )
        if row.team.casefold() == requested_team
    }
    # Roster rows are used only as membership checks. Joining them to deliveries would
    # multiply runs when an identity has conflicting source roster memberships.
    all_innings = list(session.scalars(select(Innings).where(Innings.match_id.in_(match_ids))))
    selected = [
        innings
        for innings in all_innings
        if innings.team.casefold() == requested_team
        and innings.data_complete
        and not innings.super_over
    ]
    innings_by_id = {innings.id: innings for innings in selected}
    excluded_incomplete = sum(
        innings.team.casefold() == requested_team and not innings.data_complete
        for innings in all_innings
    )
    excluded_super = sum(
        innings.team.casefold() == requested_team and innings.super_over for innings in all_innings
    )

    # Each (person, innings) pair is one batting innings, including a non-striker who
    # faced no balls or a timed-out batter identified only by a wicket record.
    records = defaultdict(lambda: {"runs": 0, "legal_balls": 0, "dismissals": 0})
    if innings_by_id:
        deliveries = session.execute(
            select(
                Delivery.innings_id,
                Delivery.batter_id,
                Delivery.non_striker_id,
                Delivery.batter_runs,
                Delivery.is_legal,
            ).where(Delivery.innings_id.in_(innings_by_id))
        ).all()
        for delivery in deliveries:
            innings = innings_by_id[delivery.innings_id]
            for player_id in (delivery.batter_id, delivery.non_striker_id):
                if player_id and (innings.match_id, player_id) in roster:
                    records[player_id, innings.id]
            if delivery.batter_id and (innings.match_id, delivery.batter_id) in roster:
                if type(delivery.batter_runs) is not int or delivery.batter_runs < 0:
                    return error_result(
                        "invalid_dataset", "A complete innings has invalid batter runs."
                    )
                if not isinstance(delivery.is_legal, bool):
                    return error_result("invalid_dataset", "A complete innings has invalid balls.")
                batting = records[delivery.batter_id, innings.id]
                batting["runs"] += delivery.batter_runs
                batting["legal_balls"] += int(delivery.is_legal)
        wickets = session.execute(
            select(Delivery.innings_id, Wicket.player_out_id, Wicket.kind)
            .join(Delivery, Wicket.delivery_id == Delivery.id)
            .where(Delivery.innings_id.in_(innings_by_id))
        ).all()
        for wicket in wickets:
            if wicket.kind in NOT_DISMISSALS:
                continue
            innings_id = wicket.innings_id
            innings = innings_by_id[innings_id]
            if (innings.match_id, wicket.player_out_id) in roster:
                records[wicket.player_out_id, innings_id]["dismissals"] += 1

    totals = defaultdict(lambda: {"runs": 0, "legal_balls": 0, "dismissals": 0, "innings": 0})
    for (player_id, _), record in records.items():
        aggregate = totals[player_id]
        for key in ("runs", "legal_balls", "dismissals"):
            aggregate[key] += record[key]
        aggregate["innings"] += 1
    eligible = [
        (player_id, record)
        for player_id, record in totals.items()
        if record["legal_balls"] >= MIN_LEGAL_BALLS and record["innings"] >= MIN_BATTING_INNINGS
    ]
    eligible.sort(key=lambda pair: (-pair[1]["legal_balls"], pair[0]))
    chosen = eligible[:MAX_BATTERS]
    players = {
        player.id: player
        for player in session.scalars(
            select(Player).where(Player.id.in_([row[0] for row in chosen]))
        )
    }
    rows = [
        {"player_id": player_id, "name": players[player_id].name, **record}
        for player_id, record in chosen
        if player_id in players
    ]
    sources = list(
        session.scalars(
            select(SourceImport).where(
                SourceImport.id.in_({match.source_import_id for match in matches})
            )
        )
    )
    provenance = [
        {
            "provider": source.provider,
            "url": source.dataset_url,
            "import_id": str(source.id),
            "checksum": source.checksum,
            "revision": source.source_revision,
            "imported_at": source.imported_at.isoformat(),
        }
        for source in sources
    ]
    coverage = {
        "team": next(name for name in matches[0].teams if name.casefold() == requested_team),
        "format": format,
        "matches": len(matches),
        "complete_batting_team_innings": len(selected),
        "excluded_incomplete_innings": excluded_incomplete,
        "excluded_super_overs": excluded_super,
        "date_start": min(match.date_start for match in matches).isoformat(),
        "date_end": max(match.date_end for match in matches).isoformat(),
        "eligible_batters": len(eligible),
        "plotted_batters": len(rows),
        "minimum_legal_balls": MIN_LEGAL_BALLS,
        "minimum_batting_innings": MIN_BATTING_INNINGS,
        "scope": "Available imported Cricsheet matches only; not official career totals.",
        "method": "Batting average is batter runs per scoreboard dismissal; scoring rate is batter runs per 100 legal balls. Retired hurt/not out, incomplete innings, and super overs are excluded.",
    }
    dataset = Dataset(
        conversation_id=conversation_id,
        kind="squad_comparison",
        data={"team": coverage["team"], "format": format, "players": rows},
        provenance={"sources": provenance},
        coverage=coverage,
    )
    session.add(dataset)
    session.flush()
    return {
        "ok": True,
        "data": {
            "dataset_id": str(dataset.id),
            "team": coverage["team"],
            "format": format,
            "eligible_batters": len(eligible),
            "plotted_batters": len(rows),
        },
        "error": None,
        "provenance": provenance,
        "coverage": coverage,
    }


def bubble_chart(dataset: Dataset):
    """Map saved aggregate values into the shared renderer-independent chart shape."""
    data = dataset.data
    players = data.get("players") if isinstance(data, dict) else None
    if (
        not isinstance(players, list)
        or data.get("format") not in {"odi", "t20i"}
        or not isinstance(data.get("team"), str)
        or not data["team"]
    ):
        raise TypeError("Invalid squad comparison dataset.")
    seen = set()
    points = []
    for player in players:
        if not isinstance(player, dict):
            raise TypeError("Invalid squad comparison player.")
        player_id, name = player.get("player_id"), player.get("name")
        if (
            not isinstance(player_id, str)
            or not player_id
            or player_id in seen
            or not isinstance(name, str)
            or not name
            or any(
                type(player.get(key)) is not int or player[key] < 0
                for key in ("runs", "legal_balls", "dismissals", "innings")
            )
            or player["legal_balls"] < MIN_LEGAL_BALLS
            or player["innings"] < MIN_BATTING_INNINGS
        ):
            raise ValueError("Invalid squad comparison player.")
        seen.add(player_id)
        points.append(
            {
                "x": player["runs"] / player["dismissals"] if player["dismissals"] else None,
                "y": 100 * player["runs"] / player["legal_balls"],
                "size": player["legal_balls"],
                "sample_size": player["innings"],
                "player_id": player_id,
                "name": name,
                "runs": player["runs"],
                "dismissals": player["dismissals"],
            }
        )
    plotted = {
        "data_points": len(points),
        "undefined_average": sum(point["x"] is None for point in points),
        "method": "Bubble size is legal balls faced; an average with zero dismissals is undefined.",
    }
    if not points:
        return None, plotted
    return {
        "metric": "batting_average",
        "group_by": "strike_rate",
        "chart_type": "bubble",
        "title": f"{data['team']} {data['format'].upper()}: imported-match batting comparison",
        "x_label": "Runs per dismissal",
        "y_label": "Runs per 100 legal balls",
        "series": [{"name": data["team"], "points": points}],
    }, plotted
