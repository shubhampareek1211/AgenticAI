"""Source-backed batter versus bowler phase heatmap for imported matches."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cricket.espn import find_players
from cricket.models import Dataset, Delivery, Innings, Match, Player, SourceImport
from cricket.results import error_result

PHASES = {
    "t20i": (("Powerplay", 1, 6), ("Middle", 7, 15), ("Death", 16, None)),
    "odi": (("Powerplay", 1, 10), ("Middle", 11, 40), ("Death", 41, None)),
}
MIN_CELL_BALLS = 6
MAX_BOWLERS = 10


def _phase(cricket_format: str, over_number: int) -> str:
    over = over_number + 1  # Cricsheet overs are zero based.
    for label, first, last in PHASES[cricket_format]:
        if over >= first and (last is None or over <= last):
            return label
    raise ValueError("Invalid over number.")


def get_batter_bowler_data(
    session: Session,
    conversation_id: UUID,
    *,
    player_id: str | None = None,
    player_name: str | None = None,
    format: str | None = None,
) -> dict:
    """Persist observed batter runs and legal balls against bowlers by match phase."""
    if not isinstance(format, str) or format not in PHASES:
        return error_result("invalid_arguments", "Format must be odi or t20i.")
    if (player_id is None) == (player_name is None):
        return error_result("invalid_arguments", "Supply either player_id or player_name.")
    if player_id is not None:
        if not isinstance(player_id, str) or not player_id.strip():
            return error_result("invalid_arguments", "player_id must contain text.")
        player = session.get(Player, player_id)
        if player is None:
            return error_result("player_not_found", "Unknown Cricsheet player_id.")
    else:
        if not isinstance(player_name, str) or not player_name.strip():
            return error_result("invalid_arguments", "player_name must contain text.")
        candidates = find_players(session, player_name)
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

    # One query spans all selected deliveries and source rows; no per-bowler lookups.
    rows = session.execute(
        select(
            Innings.id.label("innings_id"),
            Innings.data_complete,
            Innings.super_over,
            Match.id.label("match_id"),
            Match.date_start,
            Delivery.over_number,
            Delivery.bowler_id,
            Player.name.label("bowler_name"),
            Delivery.batter_runs,
            Delivery.is_legal,
            SourceImport.id.label("import_id"),
            SourceImport.dataset_url,
            SourceImport.checksum,
            SourceImport.source_revision,
            SourceImport.imported_at,
        )
        .join(Innings, Delivery.innings_id == Innings.id)
        .join(Match, Innings.match_id == Match.id)
        .outerjoin(Player, Delivery.bowler_id == Player.id)
        .join(SourceImport, Match.source_import_id == SourceImport.id)
        .where(Delivery.batter_id == player.id, Match.format == format)
        .order_by(Match.date_start, Match.id, Innings.number, Delivery.sequence)
    ).all()

    counts = defaultdict(lambda: {"runs": 0, "legal_balls": 0})
    bowler_names = {}
    sources = {}
    included_matches, included_innings = set(), set()
    incomplete_innings, super_overs = set(), set()
    unknown_bowler_deliveries = 0
    date_start = date_end = None
    for row in rows:
        if row.super_over:
            super_overs.add(row.innings_id)
            continue
        if not row.data_complete:
            incomplete_innings.add(row.innings_id)
            continue
        if (
            type(row.batter_runs) is not int
            or row.batter_runs < 0
            or type(row.over_number) is not int
            or row.over_number < 0
            or type(row.is_legal) is not bool
        ):
            return error_result("invalid_dataset", "Complete innings contains an invalid delivery.")
        if not row.bowler_id or not row.bowler_name:
            unknown_bowler_deliveries += 1
            continue
        phase = _phase(format, row.over_number)
        cell = counts[(row.bowler_id, phase)]
        cell["runs"] += row.batter_runs
        cell["legal_balls"] += int(row.is_legal)
        bowler_names[row.bowler_id] = row.bowler_name
        included_matches.add(row.match_id)
        included_innings.add(row.innings_id)
        date_start = min(date_start, row.date_start) if date_start else row.date_start
        date_end = max(date_end, row.date_start) if date_end else row.date_start
        sources[str(row.import_id)] = {
            "provider": "cricsheet",
            "url": row.dataset_url,
            "import_id": str(row.import_id),
            "checksum": row.checksum,
            "revision": row.source_revision,
            "imported_at": row.imported_at.isoformat(),
        }

    bowler_balls = defaultdict(int)
    for (bowler_id, _phase_name), count in counts.items():
        bowler_balls[bowler_id] += count["legal_balls"]
    ordered_ids = sorted(
        bowler_balls,
        key=lambda bowler_id: (-bowler_balls[bowler_id], bowler_names[bowler_id], bowler_id),
    )[:MAX_BOWLERS]
    name_frequency = defaultdict(int)
    for bowler_id in ordered_ids:
        name_frequency[bowler_names[bowler_id]] += 1
    bowlers = [
        {
            "bowler_id": bowler_id,
            "name": bowler_names[bowler_id],
            "label": (
                f"{bowler_names[bowler_id]} ({bowler_id})"
                if name_frequency[bowler_names[bowler_id]] > 1
                else bowler_names[bowler_id]
            ),
            "legal_balls": bowler_balls[bowler_id],
        }
        for bowler_id in ordered_ids
    ]
    phase_names = [phase[0] for phase in PHASES[format]]
    cells = [
        {
            "bowler_id": bowler["bowler_id"],
            "phase": phase,
            "runs": counts[(bowler["bowler_id"], phase)]["runs"],
            "legal_balls": counts[(bowler["bowler_id"], phase)]["legal_balls"],
        }
        for bowler in bowlers
        for phase in phase_names
    ]
    provenance = [sources[key] for key in sorted(sources)]
    coverage = {
        "format": format,
        "matches": len(included_matches),
        "batting_innings": len(included_innings),
        "date_start": date_start.isoformat() if date_start else None,
        "date_end": date_end.isoformat() if date_end else None,
        "excluded_incomplete_innings": len(incomplete_innings),
        "excluded_super_overs": len(super_overs),
        "excluded_unknown_bowler_deliveries": unknown_bowler_deliveries,
        "selected_bowlers": len(bowlers),
        "total_bowlers": len(bowler_balls),
        "minimum_legal_balls_per_cell": MIN_CELL_BALLS,
        "scope": "Available imported Cricsheet matches; not complete official career totals.",
        "method": "Batter runs divided by legal balls faced, times 100. Wides and no-balls contribute batter runs but not legal balls. Only complete regular innings are used. Cells below six legal balls are left blank.",
    }
    dataset = Dataset(
        conversation_id=conversation_id,
        player_id=player.id,
        kind="batter_bowler_data",
        data={
            "identity": {"player_id": player.id, "name": player.name},
            "format": format,
            "phases": phase_names,
            "bowlers": bowlers,
            "cells": cells,
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
            "identity": dataset.data["identity"],
            "format": format,
            "bowlers": bowlers,
            "cells_with_six_balls": sum(cell["legal_balls"] >= MIN_CELL_BALLS for cell in cells),
        },
        "error": None,
        "provenance": provenance,
        "coverage": coverage,
    }


def heatmap_chart(dataset: Dataset) -> tuple[dict | None, dict]:
    """Turn a saved aggregate into a heatmap; blank cells retain their denominators."""
    data = dataset.data
    cricket_format = data["format"]
    if cricket_format not in PHASES:
        raise ValueError("Invalid batter-bowler dataset.")
    phases = [phase[0] for phase in PHASES[cricket_format]]
    bowlers, cells = data["bowlers"], data["cells"]
    identity = data["identity"]
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("name"), str)
        or not isinstance(bowlers, list)
        or not isinstance(cells, list)
        or data.get("phases") != phases
        or len(bowlers) > MAX_BOWLERS
    ):
        raise ValueError("Invalid batter-bowler dataset.")
    labels = {}
    for bowler in bowlers:
        if (
            not isinstance(bowler, dict)
            or not isinstance(bowler.get("bowler_id"), str)
            or not isinstance(bowler.get("label"), str)
            or type(bowler.get("legal_balls")) is not int
            or bowler["legal_balls"] < 0
            or bowler["bowler_id"] in labels
        ):
            raise ValueError("Invalid batter-bowler dataset.")
        labels[bowler["bowler_id"]] = bowler["label"]
    point_map = {}
    for cell in cells:
        if (
            not isinstance(cell, dict)
            or cell.get("bowler_id") not in labels
            or cell.get("phase") not in phases
            or type(cell.get("runs")) is not int
            or type(cell.get("legal_balls")) is not int
            or min(cell["runs"], cell["legal_balls"]) < 0
        ):
            raise ValueError("Invalid batter-bowler dataset.")
        key = (cell["bowler_id"], cell["phase"])
        if key in point_map:
            raise ValueError("Invalid batter-bowler dataset.")
        point_map[key] = cell
    if len(point_map) != len(bowlers) * len(phases):
        raise ValueError("Invalid batter-bowler dataset.")

    points = []
    plotted_cells = 0
    for bowler in bowlers:
        for phase in phases:
            cell = point_map[(bowler["bowler_id"], phase)]
            balls = cell["legal_balls"]
            eligible = balls >= MIN_CELL_BALLS
            plotted_cells += eligible
            points.append(
                {
                    "x": bowler["label"],
                    "row": phase,
                    "y": round(100 * cell["runs"] / balls, 2) if eligible else None,
                    "sample_size": balls,
                    "legal_balls": balls,
                    "runs": cell["runs"],
                    "bowler_id": bowler["bowler_id"],
                    **(
                        {"note": "Fewer than six legal balls; rate not shown."}
                        if not eligible
                        else {}
                    ),
                }
            )
    plotted = {
        "bowlers": len(bowlers),
        "data_points": plotted_cells,
        "blank_cells": len(points) - plotted_cells,
        "minimum_legal_balls_per_cell": MIN_CELL_BALLS,
    }
    if not plotted_cells:
        return None, plotted
    return {
        "metric": "runs_per_100_legal_balls",
        "group_by": "bowler_phase",
        "chart_type": "heatmap",
        "title": f"{identity['name']}: batting rate by bowler and phase ({cricket_format.upper()})",
        "x_label": "Bowler",
        "y_label": "Match phase",
        "value_label": "Batter runs per 100 legal balls",
        "x_categories": [bowler["label"] for bowler in bowlers],
        "row_categories": phases,
        "series": [{"name": cricket_format.upper(), "points": points}],
    }, plotted
