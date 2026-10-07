"""Descriptive scoring around teammate dismissals in complete Cricsheet innings."""

import uuid
from collections import Counter, defaultdict

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

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
from cricket.player_tool import parse_date
from cricket.queries import NOT_DISMISSALS, match_filters
from cricket.results import error_result

WINDOW = 12
MIN_SAMPLE = 10


def evaluate_event(deliveries, wickets, focal, player_id):
    """Return one exclusion reason or the selected batter's two legal-ball samples."""
    position = next(index for index, ball in enumerate(deliveries) if ball.id == focal.delivery_id)
    before = [ball for ball in deliveries[:position] if ball.is_legal is True][-WINDOW:]
    after = [ball for ball in deliveries[position + 1 :] if ball.is_legal is True][:WINDOW]
    if len(before) != WINDOW or len(after) != WINDOW:
        return "incomplete_window", None

    start, end = before[0].sequence, after[-1].sequence
    span = [ball for ball in deliveries if start <= ball.sequence <= end]
    if any(
        not ball.data_complete
        or ball.is_legal is None
        or ball.batter_runs is None
        or ball.is_boundary is None
        for ball in span
    ):
        return "incomplete_delivery", None
    if any(wicket.id != focal.id for ball in span for wicket in wickets.get(ball.id, ())):
        return "another_wicket", None
    if any(player_id not in (ball.batter_id, ball.non_striker_id) for ball in span):
        return "batter_left_crease", None

    faced_before = [ball for ball in before if ball.batter_id == player_id]
    faced_after = [ball for ball in after if ball.batter_id == player_id]
    if not faced_before:
        return "no_faced_ball_before", None
    if not faced_after:
        return "no_faced_ball_after", None
    return None, (faced_before, faced_after)


def analyze_wicket_response(
    session: Session,
    conversation_id: uuid.UUID,
    *,
    player_id: str,
    format: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Aggregate selected-batter legal balls around eligible teammate wickets."""
    if format not in {"odi", "t20i"}:
        return error_result("invalid_arguments", "Format must be odi or t20i.")
    try:
        start, end = parse_date(start_date), parse_date(end_date)
    except (TypeError, ValueError):
        return error_result("invalid_arguments", "Use dates in YYYY-MM-DD format.")
    if start and end and start > end:
        return error_result("invalid_arguments", "Start date must not follow end date.")
    player = session.get(Player, player_id)
    if player is None:
        return error_result("player_not_found", "Use a resolved Cricsheet player_id.")

    # Roster identity establishes team membership; only that team's innings can qualify.
    matches = list(
        session.scalars(
            select(Match)
            .join(MatchPlayer, MatchPlayer.match_id == Match.id)
            .where(*match_filters(player_id, format, start, end))
            .distinct()
            .order_by(Match.date_start, Match.id)
        )
    )
    match_ids = [match.id for match in matches]
    teammate = aliased(MatchPlayer)
    candidates = list(
        session.execute(
            select(Wicket, Delivery.innings_id, Innings.data_complete)
            .join(Delivery, Delivery.id == Wicket.delivery_id)
            .join(Innings, Innings.id == Delivery.innings_id)
            .join(
                MatchPlayer,
                and_(
                    MatchPlayer.match_id == Innings.match_id,
                    MatchPlayer.player_id == player_id,
                    MatchPlayer.team == Innings.team,
                ),
            )
            .join(
                teammate,
                and_(
                    teammate.match_id == Innings.match_id,
                    teammate.player_id == Wicket.player_out_id,
                    teammate.team == Innings.team,
                ),
            )
            .where(
                Innings.match_id.in_(match_ids),
                Innings.super_over.is_(False),
                Wicket.player_out_id != player_id,
                Wicket.kind.not_in(NOT_DISMISSALS),
                or_(Delivery.batter_id == player_id, Delivery.non_striker_id == player_id),
            )
            .order_by(Innings.match_id, Innings.number, Delivery.sequence, Wicket.ordinal)
        )
    )
    complete_ids = {innings_id for _, innings_id, complete in candidates if complete}
    deliveries = defaultdict(list)
    for ball in session.scalars(
        select(Delivery)
        .where(Delivery.innings_id.in_(complete_ids))
        .order_by(Delivery.innings_id, Delivery.sequence)
    ):
        deliveries[ball.innings_id].append(ball)
    wickets = defaultdict(lambda: defaultdict(list))
    for wicket, innings_id in session.execute(
        select(Wicket, Delivery.innings_id)
        .join(Delivery, Delivery.id == Wicket.delivery_id)
        .where(Delivery.innings_id.in_(complete_ids), Wicket.kind.not_in(NOT_DISMISSALS))
    ):
        wickets[innings_id][wicket.delivery_id].append(wicket)

    excluded = Counter()
    totals = {
        phase: {"runs": 0, "legal_balls": 0, "boundary_balls": 0} for phase in ("before", "after")
    }
    eligible = 0
    for focal, innings_id, complete in candidates:
        if not complete:
            excluded["incomplete_innings"] += 1
            continue
        reason, samples = evaluate_event(
            deliveries[innings_id], wickets[innings_id], focal, player_id
        )
        if reason:
            excluded[reason] += 1
            continue
        eligible += 1
        for phase, balls in zip(("before", "after"), samples, strict=True):
            totals[phase]["runs"] += sum(ball.batter_runs for ball in balls)
            totals[phase]["legal_balls"] += len(balls)
            totals[phase]["boundary_balls"] += sum(ball.is_boundary for ball in balls)

    rates = {
        phase: {
            "runs_per_100_balls": round(100 * values["runs"] / values["legal_balls"], 2)
            if values["legal_balls"]
            else None,
            "boundary_ball_percentage": round(
                100 * values["boundary_balls"] / values["legal_balls"], 2
            )
            if values["legal_balls"]
            else None,
        }
        for phase, values in totals.items()
    }
    imports = list(
        session.scalars(
            select(SourceImport).where(
                SourceImport.id.in_({match.source_import_id for match in matches})
            )
        )
    )
    provenance = [
        {
            "provider": "cricsheet",
            "import_id": str(item.id),
            "url": item.dataset_url,
            "checksum": item.checksum,
            "revision": item.source_revision,
            "downloaded_at": item.downloaded_at.isoformat() if item.downloaded_at else None,
            "imported_at": item.imported_at.isoformat(),
            "partial_archive_import": item.details.get("partial", False),
        }
        for item in sorted(imports, key=lambda item: str(item.id))
    ]
    coverage = {
        "candidate_events": len(candidates),
        "eligible_events": eligible,
        "excluded_events": sum(excluded.values()),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "insufficient_sample": eligible < MIN_SAMPLE,
        "sample_size": {"matches": len(matches)},
        "date_start": matches[0].date_start.isoformat() if matches else None,
        "date_end": max(match.date_end for match in matches).isoformat() if matches else None,
        "scope": "Available imported Cricsheet matches; descriptive custom metric, not an official statistic or causal estimate.",
        "method": "Twelve legal team deliveries on each side, excluding the wicket delivery; rates use only legal balls faced by this batter.",
        "selection_bias": "Requiring complete windows and the batter to remain at the crease favors longer batting stays and may omit difficult situations.",
    }
    data = {
        "player_id": player.id,
        "player_name": player.name,
        "filters": {
            "format": format,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
        },
        "window_legal_team_deliveries": WINDOW,
        "totals": totals,
        "rates": rates,
    }
    dataset = Dataset(
        conversation_id=conversation_id,
        player_id=player.id,
        kind="wicket_response",
        data=data,
        provenance={"sources": provenance},
        coverage=coverage,
    )
    session.add(dataset)
    session.flush()
    return {
        "ok": True,
        "data": {"dataset_id": str(dataset.id), **data},
        "error": None,
        "provenance": provenance,
        "coverage": coverage,
    }
