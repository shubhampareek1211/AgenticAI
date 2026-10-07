"""Explicit download/import/query commands. Migrations never run implicitly."""

import argparse
import json
import sys
import zipfile
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from cricket.db import ConfigurationError, make_engine, session_factory
from cricket.espn import PlayerResolutionError, fetch_profile, resolve_espn_profile
from cricket.ingest import ImportError, import_archive, import_register
from cricket.models import Base
from cricket.queries import innings_totals, player_records
from cricket.sources import FORMAT_FILES, SourceError, download
from cricket.validate import validate


def parser():
    argument_parser = argparse.ArgumentParser(description=__doc__)
    commands = argument_parser.add_subparsers(dest="command", required=True)
    for name in ("download", "import"):
        command = commands.add_parser(name)
        command.add_argument("--source-dir", type=Path, default=Path(".local/sources"))
        command.add_argument(
            "--formats", nargs="+", choices=list(FORMAT_FILES), default=list(FORMAT_FILES)
        )
        if name == "download":
            command.add_argument("--refresh", action="store_true")
        else:
            command.add_argument("--limit", type=int)
            command.add_argument("--match-ids", nargs="+")
    command = commands.add_parser("player")
    identity = command.add_mutually_exclusive_group(required=True)
    identity.add_argument("--espn-id")
    identity.add_argument("--profile-json", type=Path)
    identity.add_argument("--cricsheet-id")
    command.add_argument("--format", choices=list(FORMAT_FILES))
    command = commands.add_parser("totals")
    command.add_argument("match_id")
    commands.add_parser("status")
    command = commands.add_parser("validate")
    command.add_argument("--source-dir", type=Path, default=Path(".local/sources"))
    command.add_argument("--live-espn", action="store_true")
    command.add_argument("--rerun-import", action="store_true")
    command.add_argument("--report", type=Path)
    return argument_parser


def run(args):
    if args.command == "download":
        return {
            "artifacts": download(args.source_dir, list(dict.fromkeys(args.formats)), args.refresh)
        }
    engine = make_engine()
    try:
        with session_factory(engine).begin() as session:
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if not revision:
                raise ConfigurationError(
                    "Apply migrations with `uv run alembic upgrade head` before importing."
                )
        if args.command == "import":
            results = {}
            with session_factory(engine).begin() as session:
                results["register"] = import_register(
                    session, args.source_dir / "people.csv", args.source_dir / "names.csv"
                )
            for cricket_format in dict.fromkeys(args.formats):
                with session_factory(engine).begin() as session:
                    results[cricket_format] = import_archive(
                        session,
                        args.source_dir / FORMAT_FILES[cricket_format],
                        cricket_format,
                        args.match_ids,
                        args.limit,
                        progress=lambda done, total, fmt=cricket_format: print(
                            f"{fmt}: {done}/{total}", file=sys.stderr
                        ),
                    )
            return results
        if args.command == "validate":
            report = validate(engine, args.source_dir, args.live_espn, args.rerun_import)
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, default=str, indent=2) + "\n")
            return report
        with session_factory(engine).begin() as session:
            if args.command == "player":
                player_id = args.cricsheet_id
                if not player_id:
                    profile = (
                        json.loads(args.profile_json.read_text())
                        if args.profile_json
                        else fetch_profile(args.espn_id)
                    )
                    player_id = resolve_espn_profile(session, profile).id
                return player_records(session, player_id, args.format)
            if args.command == "totals":
                return {
                    "match_id": args.match_id,
                    "innings": innings_totals(session, args.match_id),
                }
            return {
                "migration": revision,
                "counts": {
                    name: session.scalar(select(func.count()).select_from(table))
                    for name, table in Base.metadata.tables.items()
                },
            }
    finally:
        engine.dispose()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(json.dumps(run(args), default=str, indent=2))
        return 0
    except (
        ConfigurationError,
        SourceError,
        ImportError,
        PlayerResolutionError,
        OSError,
        ValueError,
        AssertionError,
        zipfile.BadZipFile,
    ) as exc:
        # These source/configuration errors contain only curated messages or local paths.
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            json.dumps(
                {
                    "error": "Database operation failed. Check DATABASE_URL and migrations; the current artifact transaction was rolled back."
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
