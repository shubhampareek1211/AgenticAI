"""Per-over run components for complete match innings and stacked area charts."""

from collections import defaultdict
from collections.abc import Iterable

from cricket.models import Dataset, Delivery

COMPONENTS = ("running", "boundary", "other_batter", "extras")
COMPONENT_LABELS = {
    "running": "Running (1–3)",
    "boundary": "Boundaries",
    "other_batter": "Other batter runs",
    "extras": "Extras",
}


def over_components(deliveries: Iterable[Delivery]) -> dict[int, dict[str, int]]:
    """Allocate every recorded delivery run exactly once, excluding innings penalties."""
    overs: dict[int, dict[str, int]] = defaultdict(lambda: dict.fromkeys(COMPONENTS, 0))
    for delivery in deliveries:
        batter = delivery.batter_runs
        extras = delivery.extras_runs
        total = delivery.total_runs
        if (
            type(delivery.over_number) is not int
            or delivery.over_number < 0
            or any(type(value) is not int or value < 0 for value in (batter, extras, total))
            or total != batter + extras
            or type(delivery.is_boundary) is not bool
            or delivery.data_complete is not True
            or (delivery.is_boundary and batter not in (4, 6))
        ):
            raise ValueError("Invalid delivery for run-component aggregation.")
        parts = overs[delivery.over_number]
        if delivery.is_boundary:
            parts["boundary"] += batter
        elif batter in (1, 2, 3):
            parts["running"] += batter
        else:
            # A non-boundary four and overthrows belong here, not in boundaries.
            parts["other_batter"] += batter
        parts["extras"] += extras
    return {number: overs[number] for number in sorted(overs)}


def area_chart(dataset: Dataset) -> tuple[dict | None, dict]:
    """Build separate team stacks from saved complete regular innings only."""
    match = dataset.data.get("match")
    innings = dataset.data.get("innings")
    if not isinstance(match, dict) or not isinstance(innings, list):
        raise TypeError("Invalid match dataset.")
    match_id = match.get("id")
    if not isinstance(match_id, str) or not match_id:
        raise ValueError("Invalid match identity.")

    series = []
    included = 0
    excluded = 0
    legacy_missing = False
    for entry in innings:
        if (
            not isinstance(entry, dict)
            or type(entry.get("data_complete")) is not bool
            or type(entry.get("super_over")) is not bool
        ):
            raise TypeError("Invalid match innings.")
        if not entry["data_complete"] or entry["super_over"]:
            excluded += 1
            continue
        number, team, overs = entry.get("number"), entry.get("team"), entry.get("overs")
        if (
            type(number) is not int
            or number <= 0
            or not isinstance(team, str)
            or not team
            or not isinstance(overs, list)
        ):
            raise ValueError("Invalid match innings.")
        if any(not isinstance(over, dict) for over in overs):
            raise TypeError("Invalid match over.")
        seen = set()
        points = {part: [] for part in COMPONENTS}
        for over in sorted(overs, key=lambda value: value.get("over_number", -1)):
            over_number, runs, legal_balls = (
                over.get("over_number"),
                over.get("runs"),
                over.get("legal_balls"),
            )
            if (
                any(
                    type(value) is not int or value < 0
                    for value in (over_number, runs, legal_balls)
                )
                or over_number in seen
            ):
                raise ValueError("Invalid match over.")
            seen.add(over_number)
            components = over.get("components")
            if components is None:
                legacy_missing = True
                continue
            if (
                not isinstance(components, dict)
                or set(components) != set(COMPONENTS)
                or any(
                    type(components[part]) is not int or components[part] < 0 for part in COMPONENTS
                )
                or sum(components.values()) != runs
            ):
                raise ValueError("Invalid saved over components.")
            for part in COMPONENTS:
                points[part].append(
                    {
                        "x": over_number + 1,
                        "y": components[part],
                        "sample_size": legal_balls,
                        "over_runs": runs,
                        "innings_number": number,
                        "team": team,
                        "component": part,
                    }
                )
        if points["running"]:
            included += 1
            for part in COMPONENTS:
                series.append(
                    {
                        "name": f"{team} (innings {number}) — {COMPONENT_LABELS[part]}",
                        "innings_number": number,
                        "component": part,
                        "points": points[part],
                    }
                )

    plotted = {
        "innings": included,
        "excluded_incomplete_or_super_over_innings": excluded,
        "data_points": sum(len(item["points"]) for item in series),
        "legacy_missing_components": legacy_missing,
        "method": "Each delivery total is allocated to batter running, actual boundaries, other batter runs, or extras. Pre/post innings penalty runs are excluded. Each innings has its own stack.",
    }
    if legacy_missing or not series:
        return None, plotted
    teams = match.get("teams")
    label = (
        " vs ".join(teams)
        if isinstance(teams, list) and all(isinstance(team, str) and team for team in teams)
        else match_id
    )
    when = match.get("date_start")
    context = (
        f"{label}, {when} (match {match_id})"
        if isinstance(when, str) and when
        else f"match {match_id}"
    )
    chart = {
        "metric": "run_components",
        "group_by": "over",
        "chart_type": "stacked_area",
        "title": f"Run components by over — {context}",
        "x_label": "Over",
        "y_label": "Runs in over",
        "series": series,
    }
    return chart, plotted
