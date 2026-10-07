"""Resolve one imported match and save its over-by-over data for chart tools."""

import uuid
from collections import defaultdict
from datetime import date as date_class

from sqlalchemy import select
from sqlalchemy.orm import Session

from cricket.models import Dataset, Delivery, Innings, Match, Player, SourceImport, Wicket
from cricket.queries import NOT_DISMISSALS
from cricket.results import error_result
from cricket.variety_area import over_components
from cricket.variety_partnership import innings_partnerships


def _date(value: str | None) -> date_class | None:
    if value is None:
        return None
    parsed = date_class.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError
    return parsed


def _match_summary(match: Match) -> dict:
    event = match.info.get("event")
    return {
        "match_id": match.id,
        "format": match.format,
        "date": match.date_start.isoformat(),
        "teams": match.teams,
        "venue": match.venue,
        "event": event.get("name") if isinstance(event, dict) else None,
    }


def get_match_data(
    session: Session,
    conversation_id: uuid.UUID,
    *,
    match_id: str | None = None,
    team: str | None = None,
    opponent: str | None = None,
    format: str | None = None,
    date: str | None = None,
    year: int | None = None,
    event: str | None = None,
) -> dict:
    """Resolve by stable ID or explicit filters; never silently select an ambiguous match."""
    if bool(match_id) == bool(team):
        return error_result("invalid_arguments", "Supply either match_id or team filters.")
    if any(value is not None and not value.strip() for value in (match_id, team, opponent, event)):
        return error_result("invalid_arguments", "Match filters must contain text.")
    if format not in {None, "odi", "t20i"} or (
        year is not None and (type(year) is not int or not 1900 <= year <= 2100)
    ):
        return error_result("invalid_arguments", "Use ODI/T20I and a valid calendar year.")
    try:
        selected_date = _date(date)
    except (TypeError, ValueError):
        return error_result("invalid_arguments", "Use dates in YYYY-MM-DD format.")
    if selected_date and year is not None and selected_date.year != year:
        return error_result("invalid_arguments", "Date and year disagree.")
    if opponent and (not team or opponent.casefold() == team.casefold()):
        return error_result("invalid_arguments", "Choose two different teams.")
    if match_id:
        if any(value is not None for value in (opponent, format, date, year, event)):
            return error_result("invalid_arguments", "Use match_id alone to select a match.")
        match = session.get(Match, match_id)
        candidates = [match] if match else []
    else:
        statement = select(Match)
        if format:
            statement = statement.where(Match.format == format)
        if selected_date:
            statement = statement.where(Match.date_start == selected_date)
        elif year is not None:
            statement = statement.where(
                Match.date_start >= date_class(year, 1, 1),
                Match.date_start < date_class(year + 1, 1, 1),
            )
        candidates = []
        for match in session.scalars(statement.order_by(Match.date_start.desc(), Match.id)):
            names = {name.casefold() for name in match.teams}
            if team.strip().casefold() not in names or (
                opponent and opponent.strip().casefold() not in names
            ):
                continue
            event_info = match.info.get("event")
            event_name = event_info.get("name", "") if isinstance(event_info, dict) else ""
            if event and event.strip().casefold() not in event_name.casefold():
                continue
            candidates.append(match)
    if not candidates:
        return error_result("match_not_found", "No imported ODI/T20I match matches those filters.")
    if len(candidates) > 1:
        result = error_result(
            "ambiguous_match", "Choose a match_id from the candidates or narrow the filters."
        )
        result["data"] = {
            "matches": [_match_summary(match) for match in candidates[:20]],
            "total_matches": len(candidates),
        }
        return result

    match = candidates[0]
    source = session.get(SourceImport, match.source_import_id)
    if source is None:
        return error_result("invalid_dataset", "The imported match has no source record.")
    innings_rows = []
    for innings in session.scalars(
        select(Innings).where(Innings.match_id == match.id).order_by(Innings.number)
    ):
        row = {
            "number": innings.number,
            "team": innings.team,
            "data_complete": innings.data_complete,
            "super_over": innings.super_over,
            "penalty_pre": innings.penalty_pre,
            "penalty_post": innings.penalty_post,
            "overs": [],
        }
        if innings.data_complete and not innings.super_over:
            wicket_rows = list(
                session.scalars(
                    select(Wicket)
                    .join(Delivery, Wicket.delivery_id == Delivery.id)
                    .where(Delivery.innings_id == innings.id)
                    .order_by(Delivery.sequence, Wicket.ordinal)
                )
            )
            wickets_by_delivery = defaultdict(list)
            wickets = defaultdict(int)
            for wicket in wicket_rows:
                wickets_by_delivery[wicket.delivery_id].append(wicket)
                if wicket.kind not in NOT_DISMISSALS:
                    wickets[wicket.delivery_id] += 1
            overs = defaultdict(lambda: {"runs": 0, "wickets": 0, "legal_balls": 0})
            deliveries = list(
                session.scalars(
                    select(Delivery)
                    .where(Delivery.innings_id == innings.id)
                    .order_by(Delivery.sequence)
                )
            )
            try:
                components = over_components(deliveries)
            except ValueError:
                return error_result(
                    "invalid_dataset", "Complete innings has invalid run components."
                )
            player_ids = {
                identity
                for delivery in deliveries
                for identity in (delivery.batter_id, delivery.non_striker_id)
                if identity
            }
            player_names = dict(
                session.execute(
                    select(Player.id, Player.name).where(Player.id.in_(player_ids))
                ).all()
            )
            stands, stand_error = innings_partnerships(
                deliveries, wickets_by_delivery, player_names
            )
            row["partnerships"] = stands
            if stand_error:
                row["partnerships_error"] = stand_error
            for delivery in deliveries:
                if (
                    type(delivery.total_runs) is not int
                    or delivery.total_runs < 0
                    or delivery.is_legal is None
                ):
                    return error_result(
                        "invalid_dataset", "Complete innings contains an invalid delivery."
                    )
                over = overs[delivery.over_number]
                over["runs"] += delivery.total_runs
                over["wickets"] += wickets.get(delivery.id, 0)
                over["legal_balls"] += int(delivery.is_legal)
            row["overs"] = [
                {"over_number": number, **overs[number], "components": components[number]}
                for number in sorted(overs)
            ]
        innings_rows.append(row)
    provenance = [
        {
            "provider": "cricsheet",
            "url": source.dataset_url,
            "import_id": str(source.id),
            "checksum": source.checksum,
            "revision": source.source_revision,
            "imported_at": source.imported_at.isoformat(),
        }
    ]
    coverage = {
        "match_id": match.id,
        "date_start": match.date_start.isoformat(),
        "date_end": match.date_end.isoformat(),
        "complete_main_innings": sum(
            row["data_complete"] and not row["super_over"] for row in innings_rows
        ),
        "excluded_incomplete_innings": sum(not row["data_complete"] for row in innings_rows),
        "excluded_super_overs": sum(row["super_over"] for row in innings_rows),
        "method": "Over runs sum recorded delivery totals; pre/post innings penalty runs are excluded. Wicket markers count scoreboard dismissals, excluding retired hurt/not out. Incomplete innings and super overs are excluded.",
        "scope": "Available imported Cricsheet match; not an official live score.",
    }
    data = {
        "match": {
            "id": match.id,
            "format": match.format,
            "date_start": match.date_start.isoformat(),
            "date_end": match.date_end.isoformat(),
            "teams": match.teams,
            "venue": match.venue,
            "balls_per_over": match.balls_per_over,
        },
        "innings": innings_rows,
    }
    dataset = Dataset(
        conversation_id=conversation_id,
        kind="match_data",
        data=data,
        provenance={"sources": provenance},
        coverage=coverage,
    )
    session.add(dataset)
    session.flush()
    return {
        "ok": True,
        "data": {
            "dataset_id": str(dataset.id),
            "match": _match_summary(match),
            "innings": [
                {
                    "number": row["number"],
                    "team": row["team"],
                    "complete": row["data_complete"],
                    "super_over": row["super_over"],
                }
                for row in innings_rows
            ],
        },
        "error": None,
        "provenance": provenance,
        "coverage": coverage,
    }
