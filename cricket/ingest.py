"""Idempotent, transactionally imported Cricsheet Register and JSON archives."""

import csv
import hashlib
import json
import re
import uuid
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cricket.models import (
    Delivery,
    ExternalPlayerID,
    Innings,
    Match,
    MatchPlayer,
    Player,
    SourceImport,
    Wicket,
    utcnow,
)
from cricket.sources import artifact_metadata


class ImportError(ValueError):
    """Invalid source data; the caller must roll back the containing artifact transaction."""


def import_lock(session: Session):
    """Acquire an import lock that PostgreSQL releases on commit or rollback."""
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(728194601)")):
        raise ImportError("Another cricket import is running; retry after it completes.")


def chunks(rows, size=1000):
    """Yield bounded batches for bulk SQL inserts."""
    for offset in range(0, len(rows), size):
        yield rows[offset : offset + size]


def new_import(session, provider, metadata, selection, details) -> tuple[SourceImport, bool]:
    """Reuse or create provenance for a provider, artifact checksum, and selection."""
    selection_key = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()
    record = session.scalar(
        select(SourceImport).where(
            SourceImport.provider == provider,
            SourceImport.checksum == metadata["checksum"],
            SourceImport.selection_key == selection_key,
        )
    )
    if record:
        return record, False
    downloaded = metadata.get("downloaded_at")
    record = SourceImport(
        provider=provider,
        dataset_url=metadata["source_url"],
        checksum=metadata["checksum"],
        selection_key=selection_key,
        source_revision=metadata["source_revision"],
        downloaded_at=datetime.fromisoformat(downloaded) if downloaded else None,
        counts={},
        details=details,
    )
    session.add(record)
    session.flush()
    return record, True


def read_csv(path: Path, required: set[str]) -> list[dict]:
    """Read a nonempty Register CSV after validating its required columns."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or []):
            raise ImportError(f"Invalid Register columns in {path.name}.")
        rows = list(reader)
    if not rows:
        raise ImportError(f"Empty Register file: {path.name}.")
    return rows


def register_provider(column: str) -> str:
    """Normalize repeated Register key columns to one external provider name."""
    provider = re.sub(r"_\d+$", "", column[4:])
    return "espn" if provider == "cricinfo" else provider


def import_register(session: Session, people: Path, names: Path) -> dict:
    """Reconcile supplied players and provider columns within the caller's transaction."""
    import_lock(session)
    people_meta, names_meta = artifact_metadata(people), artifact_metadata(names)
    metadata = dict(people_meta)
    metadata["checksum"] = hashlib.sha256(
        (people_meta["checksum"] + names_meta["checksum"]).encode()
    ).hexdigest()
    metadata["source_revision"] = f"sha256:{metadata['checksum']}"
    record, created = new_import(
        session, "cricsheet_register", metadata, "all", {"artifacts": [people_meta, names_meta]}
    )
    people_rows = read_csv(people, {"identifier", "name", "unique_name"})
    providers = {register_provider(key) for key in people_rows[0] if key.startswith("key_")}
    identifiers = [row["identifier"] for row in people_rows]
    if any(not value or len(value) > 36 for value in identifiers) or len(set(identifiers)) != len(
        identifiers
    ):
        raise ImportError("Register contains invalid or duplicate stable identifiers.")
    known_ids = set(identifiers)
    aliases = defaultdict(set)
    for row in read_csv(names, {"identifier", "name"}):
        if row["identifier"] not in known_ids:
            raise ImportError("A Register alias refers to an unknown identifier.")
        if row["name"]:
            aliases[row["identifier"]].add(row["name"])
    players, external = [], {}
    for row in people_rows:
        if not row["name"] or not row["unique_name"]:
            raise ImportError("Register player is missing its display or unique name.")
        player_id = row["identifier"]
        players.append(
            {
                "id": player_id,
                "name": row["name"],
                "unique_name": row["unique_name"],
                "aliases": sorted(aliases[player_id]),
                "register_import_id": record.id,
            }
        )
        for key, value in row.items():
            if key.startswith("key_") and value:
                provider = register_provider(key)
                identity = (provider, value)
                if identity in external and external[identity] != player_id:
                    raise ImportError(
                        f"Ambiguous {provider} identifier in Register; no mapping guessed."
                    )
                external[identity] = player_id
    current = {
        (row.provider, row.external_id): row
        for row in session.execute(
            select(
                ExternalPlayerID.provider,
                ExternalPlayerID.external_id,
                ExternalPlayerID.player_id,
                ExternalPlayerID.source_import_id,
                SourceImport.provider.label("source_provider"),
            ).join(SourceImport, SourceImport.id == ExternalPlayerID.source_import_id)
        )
    }
    for identity, player_id in external.items():
        if identity in current and current[identity].player_id != player_id:
            raise ImportError(
                "Register attempts to reassign a stable external identifier; review it manually."
            )
    # A subset cannot revoke mappings for omitted people, providers, or other sources.
    obsolete = [
        identity
        for identity, row in current.items()
        if identity not in external
        and row.player_id in known_ids
        and row.provider in providers
        and row.source_provider == "cricsheet_register"
    ]
    player_fields = ("name", "unique_name", "aliases", "register_import_id")
    current_players = {
        row.id: row
        for row in session.execute(
            select(Player.id, *(getattr(Player, key) for key in player_fields)).where(
                Player.id.in_(known_ids)
            )
        )
    }
    players_changed = any(
        row["id"] not in current_players
        or any(getattr(current_players[row["id"]], key) != row[key] for key in player_fields)
        for row in players
    )
    mappings_changed = any(
        identity not in current or current[identity].source_import_id != record.id
        for identity in external
    )
    # Historical provenance proves the file was seen, not that its rows still match.
    if not created and not (players_changed or mappings_changed or obsolete):
        return {"import_id": str(record.id), "unchanged": True, **record.counts}
    for batch in chunks(players):
        statement = insert(Player).values(batch)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[Player.id],
                set_={key: getattr(statement.excluded, key) for key in player_fields},
            )
        )
    mappings = [
        {
            "provider": provider,
            "external_id": external_id,
            "player_id": player_id,
            "source_import_id": record.id,
        }
        for (provider, external_id), player_id in external.items()
    ]
    for provider, external_id in obsolete:
        session.execute(
            delete(ExternalPlayerID).where(
                ExternalPlayerID.provider == provider, ExternalPlayerID.external_id == external_id
            )
        )
    for batch in chunks(mappings):
        statement = insert(ExternalPlayerID).values(batch)
        # Retained mappings keep their cached ESPN profiles and retrieval timestamps.
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[ExternalPlayerID.provider, ExternalPlayerID.external_id],
                set_={"source_import_id": record.id},
            )
        )
    record.counts = {
        "people": len(players),
        "external_ids": len(mappings),
        "aliases": sum(len(values) for values in aliases.values()),
    }
    return {"import_id": str(record.id), "unchanged": False, **record.counts}


def missing_deliveries(value) -> bool:
    """Find nested source markers that make delivery-based calculations incomplete."""
    if isinstance(value, str):
        return any(word in value.lower() for word in ("deliver", "inning", "over", "ball"))
    if isinstance(value, dict):
        return any(missing_deliveries(key) for key in value)
    if isinstance(value, list):
        return any(missing_deliveries(item) for item in value)
    return False


def nonnegative(value) -> bool:
    """Accept nonnegative integer counts, excluding booleans."""
    return type(value) is int and value >= 0


def parse_match(payload: dict, match_id: str) -> dict:
    """Validate and normalize a match into row dictionaries without writing to the database."""
    try:
        info, meta = payload["info"], payload["meta"]
        version = meta["data_version"]
        if version not in {"1.0.0", "1.1.0", "1.2.0"}:
            raise ImportError("Unsupported Cricsheet JSON version; update the parser first.")
        if info["gender"] != "male" or info["team_type"] != "international":
            raise ImportError("Only men's international matches are supported.")
        # Cricsheet permits both T20 and IT20 for international T20 matches.
        cricket_format = {"ODI": "odi", "T20": "t20i", "IT20": "t20i"}.get(info["match_type"])
        if not cricket_format:
            raise ImportError("Only ODI and T20I matches are supported.")
        dates = [date.fromisoformat(value) for value in info["dates"]]
        if not dates or len(info["teams"]) != 2 or len(set(info["teams"])) != 2:
            raise ImportError("Match dates or teams are invalid.")
        registry = info["registry"]["people"]
        if not registry or any(not value or len(value) > 36 for value in registry.values()):
            raise ImportError("Match requires valid stable registry identifiers.")
        balls_per_over = info["balls_per_over"]
        if not nonnegative(balls_per_over) or balls_per_over == 0:
            raise ImportError("Invalid balls_per_over.")

        def player_id(name):
            """Resolve a source name only through this match's stable registry."""
            if name is None:
                return None
            if name not in registry:
                raise ImportError(
                    "Delivery player is absent from the stable registry; no name join allowed."
                )
            return registry[name]

        roster = []
        for team, players in info["players"].items():
            if team not in info["teams"]:
                raise ImportError("Player roster has an unknown team.")
            for name in players:
                roster.append(
                    {
                        "match_id": match_id,
                        "player_id": player_id(name),
                        "team": team,
                        "source_name": name,
                    }
                )
        # Retain conflicting roster memberships, but exclude their innings from statistics.
        teams_by_id = defaultdict(set)
        for row in roster:
            teams_by_id[row["player_id"]].add(row["team"])
        ambiguous = [
            {"player_id": identity, "teams": sorted(teams)}
            for identity, teams in teams_by_id.items()
            if len(teams) > 1
        ]
        identity_markers = [{"ambiguous_player_identity": ambiguous}] if ambiguous else []
        roster = list({(row["player_id"], row["team"]): row for row in roster}.values())
        innings_rows, delivery_rows, wickets = [], [], []
        for number, innings in enumerate(payload["innings"], 1):
            # Deterministic IDs let corrected imports replace the same innings safely.
            inning_id = uuid.uuid5(uuid.NAMESPACE_URL, f"cricsheet:{match_id}:innings:{number}")
            if innings["team"] not in info["teams"]:
                raise ImportError("Innings team is absent from the match teams.")
            missing = list(info.get("missing", []))
            missing.extend(innings.get("missing", []))
            missing.extend(identity_markers)
            overs = innings["overs"]
            sequence = legal_index = 0
            incomplete = bool(ambiguous) or missing_deliveries(missing)
            previous_over = -1
            for over_position, over in enumerate(overs):
                over_number = over["over"]
                if not nonnegative(over_number) or over_number <= previous_over:
                    raise ImportError("Overs must have strictly increasing nonnegative numbers.")
                if over_number != previous_over + 1:
                    missing.append({"delivery_gap_before_over": over_number})
                    incomplete = True
                previous_over = over_number
                over_legal = 0
                for delivery_number, delivery in enumerate(over["deliveries"], 1):
                    sequence += 1
                    runs, extras = delivery.get("runs", {}), delivery.get("extras", {})
                    if not isinstance(runs, dict) or not isinstance(extras, dict):
                        raise ImportError("Delivery runs/extras must be objects.")
                    amounts = [runs.get(key) for key in ("batter", "extras", "total")]
                    valid_runs = all(nonnegative(amount) for amount in amounts)
                    if any(value is not None and not nonnegative(value) for value in amounts):
                        raise ImportError("Delivery runs must be nonnegative integers.")
                    if any(not nonnegative(value) for value in extras.values()):
                        raise ImportError("Delivery extras must be nonnegative integers.")
                    if valid_runs and (
                        amounts[2] != amounts[0] + amounts[1] or amounts[1] != sum(extras.values())
                    ):
                        raise ImportError("Delivery run totals disagree with batter runs/extras.")
                    batter = player_id(delivery.get("batter"))
                    non_striker = player_id(delivery.get("non_striker"))
                    bowler = player_id(delivery.get("bowler"))
                    complete = valid_runs and all((batter, non_striker, bowler))
                    legal = (
                        not (extras.get("wides", 0) or extras.get("noballs", 0))
                        if valid_runs
                        else None
                    )
                    if legal:
                        legal_index += 1
                        over_legal += 1
                    if not complete:
                        # Preserve the source omission; never invent missing players or runs.
                        incomplete = True
                        missing.append({"incomplete_delivery": sequence})
                    delivery_rows.append(
                        {
                            "innings_id": inning_id,
                            "sequence": sequence,
                            "over_number": over_number,
                            "delivery_number": delivery_number,
                            "legal_index": legal_index if legal else None,
                            "batter_id": batter,
                            "non_striker_id": non_striker,
                            "bowler_id": bowler,
                            "batter_runs": amounts[0],
                            "extras_runs": amounts[1],
                            "total_runs": amounts[2],
                            "extras": extras,
                            "is_legal": legal,
                            "is_boundary": (
                                amounts[0] in (4, 6) and not runs.get("non_boundary", False)
                            )
                            if valid_runs
                            else None,
                            "data_complete": bool(complete),
                            "source_data": delivery,
                        }
                    )
                    for ordinal, wicket in enumerate(delivery.get("wickets", []), 1):
                        wickets.append(
                            {
                                "innings_id": inning_id,
                                "sequence": sequence,
                                "ordinal": ordinal,
                                "player_out_id": player_id(wicket["player_out"]),
                                "kind": wicket["kind"],
                                "fielders": wicket.get("fielders", []),
                            }
                        )
                miscounted = innings.get("miscounted_overs", {}).get(str(over_number), {})
                expected = miscounted.get("balls", balls_per_over)
                # The final over may end early; internal overs must match the source count.
                if over_position < len(overs) - 1 and over_legal != expected:
                    incomplete = True
                    missing.append(
                        {
                            "unexpected_legal_ball_count": {
                                "over": over_number,
                                "observed": over_legal,
                                "expected": expected,
                            }
                        }
                    )
            if not overs and not innings.get("forfeited", False):
                incomplete = True
                missing.append("no_deliveries")
            penalty = innings.get("penalty_runs", {})
            if not all(nonnegative(value) for value in penalty.values()):
                raise ImportError("Invalid innings penalty runs.")
            innings_rows.append(
                {
                    "id": inning_id,
                    "match_id": match_id,
                    "number": number,
                    "team": innings["team"],
                    "super_over": innings.get("super_over", False),
                    "data_complete": not incomplete,
                    "missing": missing,
                    "penalty_pre": penalty.get("pre", 0),
                    "penalty_post": penalty.get("post", 0),
                    "details": {key: value for key, value in innings.items() if key != "overs"},
                }
            )
        return {
            "match": {
                "id": match_id,
                "format": cricket_format,
                "date_start": min(dates),
                "date_end": max(dates),
                "teams": info["teams"],
                "venue": info.get("venue"),
                "balls_per_over": balls_per_over,
                "source_revision": meta["revision"],
                "data_version": version,
                "missing": info.get("missing", []),
                "info": info,
            },
            "registry": registry,
            "roster": roster,
            "innings": innings_rows,
            "deliveries": delivery_rows,
            "wickets": wickets,
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, ImportError):
            raise
        raise ImportError(
            f"Malformed Cricsheet match {match_id}; required fields are invalid."
        ) from None


def import_match(
    session: Session, content: bytes, match_id: str, import_id: uuid.UUID, expected_format: str
) -> tuple[bool, dict]:
    """Upsert one stable match and replace changed children; return whether it changed."""
    digest = hashlib.sha256(content).hexdigest()
    existing = session.get(Match, match_id)
    if existing and existing.format != expected_format:
        raise ImportError("Match format disagrees with its archive.")
    if existing and existing.source_checksum == digest:
        return False, {"date_start": existing.date_start, "date_end": existing.date_end}
    parsed = parse_match(json.loads(content), match_id)
    match_values = parsed["match"]
    if match_values["format"] != expected_format:
        raise ImportError("Match format disagrees with its archive.")
    if (
        existing
        and existing.data_version == match_values["data_version"]
        and existing.source_revision > match_values["source_revision"]
    ):
        raise ImportError("Source would downgrade a match revision; use the latest archive.")
    placeholders = [
        {"id": player_id, "name": name, "unique_name": name, "aliases": []}
        for name, player_id in parsed["registry"].items()
    ]
    # A person absent from this Register revision still has its match-supplied stable ID.
    # Deduplicate aliases before the upsert; never replace the Register's canonical name.
    placeholders = list({row["id"]: row for row in placeholders}.values())
    session.execute(
        insert(Player).values(placeholders).on_conflict_do_nothing(index_elements=[Player.id])
    )
    values = {**match_values, "source_import_id": import_id, "source_checksum": digest}
    statement = insert(Match).values(values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[Match.id],
            set_={key: getattr(statement.excluded, key) for key in values if key != "id"},
        )
    )
    if existing:
        # Child replacement stays atomic within the containing artifact transaction.
        session.expire(existing)
        session.execute(delete(Innings).where(Innings.match_id == match_id))
        session.execute(delete(MatchPlayer).where(MatchPlayer.match_id == match_id))
    if parsed["roster"]:
        session.execute(insert(MatchPlayer), parsed["roster"])
    if parsed["innings"]:
        session.execute(insert(Innings), parsed["innings"])
    delivery_ids = {}
    for batch in chunks(parsed["deliveries"]):
        result = session.execute(
            insert(Delivery).returning(Delivery.id, Delivery.innings_id, Delivery.sequence), batch
        )
        delivery_ids.update({(row.innings_id, row.sequence): row.id for row in result})
    wickets = [
        {
            "delivery_id": delivery_ids[(row["innings_id"], row["sequence"])],
            **{key: value for key, value in row.items() if key not in ("innings_id", "sequence")},
        }
        for row in parsed["wickets"]
    ]
    if wickets:
        session.execute(insert(Wicket), wickets)
    return True, match_values


def import_archive(
    session: Session,
    path: Path,
    cricket_format: str,
    match_ids: list[str] | None = None,
    limit: int | None = None,
    progress=None,
) -> dict:
    """Import selected ZIP matches and record coverage; the caller owns commit or rollback."""
    import_lock(session)
    if cricket_format not in {"odi", "t20i"} or (limit is not None and limit < 1):
        raise ImportError("Select odi/t20i and a positive optional match limit.")
    metadata = artifact_metadata(path)
    with zipfile.ZipFile(path) as archive:
        entries = {
            Path(name).stem: name for name in archive.namelist() if re.fullmatch(r"\d+\.json", name)
        }
        if not entries:
            raise ImportError("Archive has no numeric Cricsheet JSON match files.")
        if match_ids and not set(match_ids).issubset(entries):
            raise ImportError("Requested match IDs are absent from the archive.")
        selected = sorted(set(match_ids) if match_ids else entries, key=int)
        if limit:
            selected = selected[:limit]
        record, created = new_import(
            session,
            f"cricsheet_{cricket_format}",
            metadata,
            selected,
            {
                "artifact": metadata,
                "selected_match_ids": selected,
                "archive_matches": len(entries),
                "partial": len(selected) != len(entries),
            },
        )
        changed = skipped = 0
        start_dates, end_dates = [], []
        for position, match_id in enumerate(selected, 1):
            updated, match = import_match(
                session, archive.read(entries[match_id]), match_id, record.id, cricket_format
            )
            changed += updated
            skipped += not updated
            start_dates.append(match["date_start"])
            end_dates.append(match["date_end"])
            if progress and (position % 100 == 0 or position == len(selected)):
                progress(position, len(selected))
        if created:
            record.date_start, record.date_end = min(start_dates), max(end_dates)
            innings_count = session.scalar(
                select(func.count(Innings.id)).where(Innings.match_id.in_(selected))
            )
            delivery_count = session.scalar(
                select(func.count(Delivery.id)).join(Innings).where(Innings.match_id.in_(selected))
            )
            wicket_count = session.scalar(
                select(func.count(Wicket.id))
                .join(Delivery)
                .join(Innings)
                .where(Innings.match_id.in_(selected))
            )
            record.counts = {
                "matches": len(selected),
                "innings": innings_count,
                "deliveries": delivery_count,
                "wickets": wicket_count,
                "changed": changed,
                "unchanged": skipped,
            }
            record.imported_at = utcnow()
        return {
            "import_id": str(record.id),
            "matches": len(selected),
            "changed": changed,
            "unchanged": skipped,
            "partial": record.details["partial"],
            "date_start": min(start_dates).isoformat(),
            "date_end": max(end_dates).isoformat(),
        }
