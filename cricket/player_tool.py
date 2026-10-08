"""Session-owned player lookup backed by Register identities and Cricsheet statistics."""

import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from cricket import espn
from cricket.models import Dataset, ExternalPlayerID, Player, utcnow
from cricket.queries import player_records
from cricket.results import error_result


def profile_ttl() -> timedelta:
    """Use the documented 24-hour default and reject invalid cache settings."""
    try:
        hours = int(os.environ.get("PROFILE_CACHE_HOURS", "24"))
        if hours <= 0:
            raise ValueError
    except ValueError:
        raise espn.PlayerResolutionError(
            "PROFILE_CACHE_HOURS must be a positive integer."
        ) from None
    return timedelta(hours=hours)


def parse_date(value: str | None) -> date | None:
    """Accept only canonical ISO calendar dates, so filters remain unambiguous."""
    if value is None:
        return None
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Use dates in YYYY-MM-DD format.")
    return parsed


def iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def cached_profile(mapping: ExternalPlayerID) -> dict | None:
    """Ignore cached JSON whose athlete ID disagrees with the Register mapping."""
    if not mapping.profile or not mapping.profile_retrieved_at:
        return None
    try:
        if espn.profile_identity(mapping.profile)[0] == mapping.external_id:
            return mapping.profile
    except espn.PlayerResolutionError:
        pass
    return None


def profile_source(mapping: ExternalPlayerID, status: str, retrieved: datetime | None) -> dict:
    return {
        "provider": "espn",
        "external_id": mapping.external_id,
        "url": espn.PROFILE_URL.format(player_id=mapping.external_id),
        "status": status,
        "retrieved_at": iso(retrieved),
    }


def load_profile(session: Session, player_id: str) -> tuple[dict | None, dict]:
    """Try Register-linked IDs, preferring fresh cache and retaining a dated fallback."""
    mappings = list(
        session.scalars(
            select(ExternalPlayerID)
            .where(ExternalPlayerID.player_id == player_id, ExternalPlayerID.provider == "espn")
            .order_by(ExternalPlayerID.external_id)
        )
    )
    if not mappings:
        return None, {"status": "unmapped", "provider": "espn"}

    now = utcnow()
    ttl = profile_ttl()
    saved_profiles = [(mapping, cached_profile(mapping)) for mapping in mappings]
    fresh = [
        (mapping, saved)
        for mapping, saved in saved_profiles
        if saved
        and timedelta(0) <= now - mapping.profile_retrieved_at.astimezone(timezone.utc) < ttl
    ]
    if fresh:
        mapping, saved = max(fresh, key=lambda pair: pair[0].profile_retrieved_at)
        return saved, profile_source(mapping, "fresh_cache", mapping.profile_retrieved_at)

    for mapping in mappings:
        try:
            fetched = espn.fetch_profile(mapping.external_id)
            if espn.profile_identity(fetched)[0] != mapping.external_id:
                raise espn.PlayerResolutionError("ESPN returned an unexpected athlete identity.")
        except espn.PlayerResolutionError:
            continue
        mapping.profile = fetched
        mapping.profile_retrieved_at = now
        return fetched, profile_source(mapping, "refreshed", now)

    stale = [(mapping, saved) for mapping, saved in saved_profiles if saved]
    if stale:
        mapping, saved = max(stale, key=lambda pair: pair[0].profile_retrieved_at)
        return saved, profile_source(mapping, "stale_cache", mapping.profile_retrieved_at)
    return None, profile_source(mappings[0], "unavailable", None)


def get_player_data(
    session: Session,
    conversation_id,
    *,
    player_name: str | None = None,
    player_id: str | None = None,
    format: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Resolve one player and persist filtered innings for later chart requests."""
    if (player_name is None) == (player_id is None):
        return error_result("invalid_arguments", "Supply either player_name or player_id.")
    if format not in (None, "odi"):
        return error_result("invalid_arguments", "Only ODI matches are supported.")
    try:
        start, end = parse_date(start_date), parse_date(end_date)
    except (TypeError, ValueError):
        return error_result("invalid_arguments", "Use dates in YYYY-MM-DD format.")
    if start and end and start > end:
        return error_result("invalid_arguments", "Start date must not follow end date.")

    if player_name is not None:
        candidates = espn.find_players(session, player_name)
        if not candidates:
            return error_result("player_not_found", "No Register player matches that name.")
        if len(candidates) > 1:
            result = error_result(
                "ambiguous_player", "Choose a Cricsheet player_id from the candidates."
            )
            result["data"] = {
                "candidates": [
                    {"player_id": item.id, "name": item.name, "unique_name": item.unique_name}
                    for item in candidates
                ]
            }
            return result
        player = candidates[0]
    else:
        player = session.get(Player, player_id)
        if player is None:
            return error_result("player_not_found", "Unknown Cricsheet player_id.")

    profile, profile_source = load_profile(session, player.id)
    athlete = profile.get("athlete", profile) if profile else {}
    identity = {
        "player_id": player.id,
        "name": player.name,
        "unique_name": player.unique_name,
        "espn_id": profile_source.get("external_id"),
        "espn_display_name": athlete.get("displayName"),
        "profile_status": profile_source["status"],
        "profile_retrieved_at": profile_source.get("retrieved_at"),
    }

    formats = ["odi"]
    records = {kind: player_records(session, player.id, kind, start, end) for kind in formats}
    stats = {
        kind: {
            "batting": record["batting"],
            "bowling": record["bowling"],
            "dismissal_counts": record["dismissal_counts"],
        }
        for kind, record in records.items()
    }
    coverage_by_format = {
        kind: {
            **record["coverage"],
            "date_start": iso(record["coverage"]["date_start"]),
            "date_end": iso(record["coverage"]["date_end"]),
        }
        for kind, record in records.items()
    }
    coverage = {
        "formats": coverage_by_format,
        "sample_size": {
            "matches": sum(value["matches"] for value in coverage_by_format.values()),
            "batting_innings": sum(
                value["batting_innings"] for value in coverage_by_format.values()
            ),
        },
        "scope": "Available imported Cricsheet matches; not complete official career totals.",
        "method": next(iter(records.values()))["method"],
    }
    sources = {
        item["import_id"]: {
            "provider": "cricsheet",
            **item,
            "downloaded_at": iso(item["downloaded_at"]),
            "imported_at": iso(item["imported_at"]),
        }
        for record in records.values()
        for item in record["provenance"]
    }
    provenance = [profile_source, *sorted(sources.values(), key=lambda item: item["import_id"])]
    filters = {"format": format, "start_date": iso(start), "end_date": iso(end)}
    dataset = Dataset(
        conversation_id=conversation_id,
        player_id=player.id,
        kind="player_data",
        data={
            "identity": identity,
            "filters": filters,
            "stats": stats,
            "innings": [row for record in records.values() for row in record["innings"]],
        },
        provenance={"sources": provenance},
        coverage=coverage,
    )
    session.add(dataset)
    session.flush()
    return {
        "ok": True,
        "data": {
            "dataset_id": str(dataset.id),
            "identity": identity,
            "filters": filters,
            "stats": stats,
        },
        "error": None,
        "provenance": provenance,
        "coverage": coverage,
    }
