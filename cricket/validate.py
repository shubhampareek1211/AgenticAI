"""Reviewable Phase 1 verification against the retained downloaded source artifacts."""

import json
import zipfile
from pathlib import Path

from sqlalchemy import func, select, text

from cricket.db import session_factory
from cricket.espn import fetch_profile, resolve_espn_profile
from cricket.ingest import import_archive, import_register
from cricket.models import Base, Innings, SourceImport, utcnow
from cricket.queries import innings_totals, player_records
from cricket.sources import FORMAT_FILES

VALIDATION_PLAYERS = (("253802", "ba607b88"), ("34102", "740742ef"), ("625383", "462411b3"))
VALIDATION_MATCHES = (("odi", "1022353"), ("t20i", "1041615"))


def table_counts(session):
    return {
        name: session.scalar(select(func.count()).select_from(table))
        for name, table in Base.metadata.tables.items()
    }


def validate(engine, directory: Path, live_espn: bool, rerun_import: bool) -> dict:
    factory = session_factory(engine)
    with factory.begin() as session:
        before = table_counts(session)
    repeats = {}
    if rerun_import:
        with factory.begin() as session:
            repeats["register"] = import_register(
                session, directory / "people.csv", directory / "names.csv"
            )
            assert repeats["register"]["unchanged"], (
                "Register content changed; this is not a repeat-import check."
            )
        for cricket_format, filename in FORMAT_FILES.items():
            with factory.begin() as session:
                repeats[cricket_format] = import_archive(
                    session, directory / filename, cricket_format
                )
                assert repeats[cricket_format]["changed"] == 0, "Repeat import changed a match."
    with factory.begin() as session:
        after = table_counts(session)
        if rerun_import:
            assert after == before, "Repeat import changed table counts."
        report = {
            "verified_at": utcnow().isoformat(),
            "database_version": session.scalar(text("SELECT version()")),
            "migration": session.scalar(text("SELECT version_num FROM alembic_version")),
            "counts": after,
            "repeat_import": repeats,
            "source_totals": [],
            "players": [],
            "incomplete_innings": session.scalar(
                select(func.count(Innings.id)).where(Innings.data_complete.is_(False))
            ),
        }
        for cricket_format, match_id in VALIDATION_MATCHES:
            with zipfile.ZipFile(directory / FORMAT_FILES[cricket_format]) as archive:
                payload = json.loads(archive.read(f"{match_id}.json"))
            stored = innings_totals(session, match_id)
            assert len(stored) == len(payload["innings"]), "Source/database innings count mismatch."
            expected = []
            for source_innings, total in zip(payload["innings"], stored, strict=True):
                runs = sum(
                    delivery["runs"]["total"]
                    for over in source_innings["overs"]
                    for delivery in over["deliveries"]
                )
                runs += sum(source_innings.get("penalty_runs", {}).values())
                assert total["data_complete"] and total["runs"] == runs, (
                    "Source/database run total mismatch."
                )
                expected.append(runs)
            report["source_totals"].append(
                {"match_id": match_id, "format": cricket_format, "runs": expected}
            )
        for espn_id, stable_id in VALIDATION_PLAYERS:
            if live_espn:
                profile = fetch_profile(espn_id)
                player = resolve_espn_profile(session, profile)
                assert player.id == stable_id, "ESPN/Register identity mismatch."
            player_report = {
                "espn_id": espn_id,
                "cricsheet_id": stable_id,
                "live_profile_verified": live_espn,
            }
            for cricket_format in FORMAT_FILES:
                result = player_records(session, stable_id, cricket_format)
                assert result["coverage"]["matches"] > 0, (
                    "Validation player has no imported matches."
                )
                assert result["provenance"], "Validation player is missing source provenance."
                player_report[cricket_format] = {
                    "coverage": result["coverage"],
                    "batting": result["batting"],
                    "bowling": result["bowling"],
                }
            report["players"].append(player_report)
        report["imports"] = [
            {
                "id": str(record.id),
                "provider": record.provider,
                "url": record.dataset_url,
                "checksum": record.checksum,
                "revision": record.source_revision,
                "downloaded_at": record.downloaded_at,
                "imported_at": record.imported_at,
                "date_start": record.date_start,
                "date_end": record.date_end,
                "counts": record.counts,
                "partial": record.details.get("partial", False),
            }
            for record in session.scalars(select(SourceImport).order_by(SourceImport.provider))
        ]
        report["passed"] = True
        return report
