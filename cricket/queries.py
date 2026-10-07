"""Source-backed summaries computed by PostgreSQL/Python, without model arithmetic."""

from collections import defaultdict
from datetime import date

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from cricket.models import Delivery, Innings, Match, MatchPlayer, Player, SourceImport, Wicket

NOT_DISMISSALS = {"retired hurt", "retired not out"}
# MCC Laws 34.5 and 40.2 deny bowler credit for hit-the-ball-twice and timed-out wickets.
NOT_BOWLER_WICKETS = NOT_DISMISSALS | {
    "run out",
    "retired out",
    "obstructing the field",
    "handled the ball",
    "hit the ball twice",
    "timed out",
}


def match_filters(player_id: str, cricket_format: str | None, start: date | None, end: date | None):
    """Build validated roster, format, and inclusive date predicates."""
    if cricket_format not in {None, "odi", "t20i"}:
        raise ValueError("Format must be odi or t20i.")
    if start and end and start > end:
        raise ValueError("Start date must not follow end date.")
    filters = [MatchPlayer.player_id == player_id]
    if cricket_format:
        filters.append(Match.format == cricket_format)
    if start:
        filters.append(Match.date_start >= start)
    if end:
        filters.append(Match.date_start <= end)
    return filters


def innings_totals(session: Session, match_id: str) -> list[dict]:
    """Sum deliveries and penalties, distinguishing partial observations from complete totals."""
    rows = session.execute(
        select(
            Innings.number,
            Innings.team,
            Innings.super_over,
            Innings.data_complete,
            Innings.penalty_pre,
            Innings.penalty_post,
            func.coalesce(func.sum(Delivery.total_runs), 0).label("delivery_runs"),
            func.coalesce(func.sum(Delivery.extras_runs), 0).label("extras"),
            func.count(Delivery.id).label("deliveries"),
            func.count(Delivery.id).filter(Delivery.is_legal.is_(True)).label("legal_balls"),
        )
        .outerjoin(Delivery, Delivery.innings_id == Innings.id)
        .where(Innings.match_id == match_id)
        .group_by(Innings.id)
        .order_by(Innings.number)
    )
    return [
        {
            "number": row.number,
            "team": row.team,
            "super_over": row.super_over,
            "data_complete": row.data_complete,
            "observed_runs": row.delivery_runs + row.penalty_pre + row.penalty_post,
            "runs": row.delivery_runs + row.penalty_pre + row.penalty_post
            if row.data_complete
            else None,
            "extras": row.extras + row.penalty_pre + row.penalty_post,
            "deliveries": row.deliveries,
            "legal_balls": row.legal_balls,
        }
        for row in rows
    ]


def player_records(
    session: Session,
    player_id: str,
    cricket_format: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Return player statistics with available-match coverage and source provenance."""
    player = session.get(Player, player_id)
    if not player:
        raise ValueError("Unknown Cricsheet player ID.")
    # Roster membership counts matches even when the player did not bat or bowl.
    matches = list(
        session.scalars(
            select(Match)
            .join(MatchPlayer)
            .where(*match_filters(player_id, cricket_format, start, end))
            .distinct()
            .order_by(Match.date_start, Match.id)
        )
    )
    match_ids = [match.id for match in matches]
    # A timed-out batter can have a dismissal without appearing on any delivery.
    dismissal_rows = session.execute(
        select(Delivery.innings_id, Delivery.sequence, Wicket.ordinal, Wicket.kind)
        .join(Delivery, Wicket.delivery_id == Delivery.id)
        .join(Innings, Delivery.innings_id == Innings.id)
        .where(
            Innings.match_id.in_(match_ids),
            Wicket.player_out_id == player_id,
            Wicket.kind.not_in(NOT_DISMISSALS),
        )
        .order_by(Delivery.innings_id, Delivery.sequence, Wicket.ordinal)
    ).all()
    dismissal_kinds = defaultdict(list)
    for innings_id, sequence, ordinal, kind in dismissal_rows:
        dismissal_kinds[innings_id].append(
            {"dismissal_id": f"{innings_id}:{sequence}:{ordinal}", "kind": kind}
        )
    dismissals = set(dismissal_kinds)
    # Keep wickets out of this join so multiple dismissals cannot duplicate batting runs.
    rows = session.execute(
        select(
            Match.id.label("match_id"),
            Match.format,
            Match.date_start,
            Innings.id.label("innings_id"),
            Innings.number,
            Innings.data_complete,
            func.coalesce(
                func.sum(case((Delivery.batter_id == player_id, Delivery.batter_runs), else_=0)), 0
            ).label("runs"),
            func.count(Delivery.id)
            .filter(Delivery.batter_id == player_id, Delivery.is_legal.is_(True))
            .label("legal_balls"),
            func.count(Delivery.id)
            .filter(
                Delivery.batter_id == player_id,
                Delivery.is_boundary.is_(True),
                Delivery.batter_runs == 4,
            )
            .label("fours"),
            func.count(Delivery.id)
            .filter(
                Delivery.batter_id == player_id,
                Delivery.is_boundary.is_(True),
                Delivery.batter_runs == 6,
            )
            .label("sixes"),
        )
        .join(Innings, Innings.match_id == Match.id)
        .join(Delivery, Delivery.innings_id == Innings.id)
        .where(
            Match.id.in_(match_ids),
            or_(
                Delivery.batter_id == player_id,
                Delivery.non_striker_id == player_id,
                Innings.id.in_(dismissals),
            ),
            Innings.super_over.is_(False),
        )
        .group_by(Match.id, Innings.id)
        .order_by(Match.date_start, Match.id, Innings.number)
    )
    batting = [
        {
            "innings_id": str(row.innings_id),
            "match_id": row.match_id,
            "format": row.format,
            "date": row.date_start.isoformat(),
            "innings_number": row.number,
            "runs": row.runs,
            "legal_balls": row.legal_balls,
            "fours": row.fours,
            "sixes": row.sixes,
            "dismissed": row.innings_id in dismissals,
            "dismissal_kinds": dismissal_kinds.get(row.innings_id, []),
            "data_complete": row.data_complete,
        }
        for row in rows
    ]
    # Byes, leg-byes, and team penalty runs are not charged to the bowler.
    bowling = session.execute(
        select(
            func.count(Delivery.id).label("deliveries"),
            func.count(Delivery.id).filter(Delivery.is_legal.is_(True)).label("legal_balls"),
            func.coalesce(
                func.sum(
                    Delivery.total_runs
                    - func.coalesce(Delivery.extras["byes"].as_integer(), 0)
                    - func.coalesce(Delivery.extras["legbyes"].as_integer(), 0)
                    - func.coalesce(Delivery.extras["penalty"].as_integer(), 0)
                ),
                0,
            ).label("runs_conceded"),
        )
        .join(Innings)
        .where(
            Innings.match_id.in_(match_ids),
            Innings.super_over.is_(False),
            Innings.data_complete.is_(True),
            Delivery.bowler_id == player_id,
        )
    ).one()
    bowling_wickets = session.scalar(
        select(func.count(Wicket.id))
        .join(Delivery)
        .join(Innings)
        .where(
            Innings.match_id.in_(match_ids),
            Innings.super_over.is_(False),
            Innings.data_complete.is_(True),
            Delivery.bowler_id == player_id,
            Wicket.kind.not_in(NOT_BOWLER_WICKETS),
        )
    )
    imports = list(
        session.scalars(
            select(SourceImport).where(
                SourceImport.id.in_({match.source_import_id for match in matches})
            )
        )
    )
    # Retain incomplete innings for inspection while excluding them from aggregates.
    eligible = [row for row in batting if row["data_complete"]]
    dismissal_counts = defaultdict(int)
    for row in eligible:
        for event in row["dismissal_kinds"]:
            dismissal_counts[event["kind"]] += 1
    return {
        "player": {"cricsheet_id": player.id, "name": player.name},
        "coverage": {
            "format": cricket_format,
            "matches": len(matches),
            "date_start": min((m.date_start for m in matches), default=None),
            "date_end": max((m.date_end for m in matches), default=None),
            "batting_innings": len(eligible),
            "excluded_incomplete_batting_innings": len(batting) - len(eligible),
            "scope": "available Cricsheet matches; not complete official career totals",
        },
        "batting": {
            "runs": sum(row["runs"] for row in eligible),
            "legal_balls": sum(row["legal_balls"] for row in eligible),
            "dismissals": sum(row["dismissed"] for row in eligible),
        },
        "dismissal_counts": dict(sorted(dismissal_counts.items())),
        "bowling": {
            "deliveries": bowling.deliveries,
            "legal_balls": bowling.legal_balls,
            "runs_conceded": bowling.runs_conceded,
            "wickets": bowling_wickets,
        },
        "innings": batting,
        "provenance": [
            {
                "import_id": str(record.id),
                "url": record.dataset_url,
                "checksum": record.checksum,
                "revision": record.source_revision,
                "downloaded_at": record.downloaded_at,
                "imported_at": record.imported_at,
                "partial_archive_import": record.details.get("partial", False),
            }
            for record in imports
        ],
        "method": "Legal balls exclude wides and no-balls. Super overs and incomplete innings are excluded from aggregates.",
    }
