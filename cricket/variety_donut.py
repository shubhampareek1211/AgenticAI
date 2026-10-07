"""Dismissal donut from the saved, complete player innings dataset."""

from collections import defaultdict

from cricket.queries import NOT_DISMISSALS


def donut_chart(dataset):
    """Return one dismissal-kind ring per format using the v2 chart contract."""
    # Import here because chart_tool dispatches to this module.
    from cricket.chart_tool import complete_innings

    rows, excluded = complete_innings(dataset)
    by_format = defaultdict(lambda: defaultdict(int))
    seen_ids = set()
    legacy_missing = False
    for row in rows:
        events = row.get("dismissal_kinds")
        if not isinstance(events, list):
            legacy_missing = True
            continue
        for event in events:
            if (
                not isinstance(event, dict)
                or not isinstance(event.get("dismissal_id"), str)
                or not event["dismissal_id"]
                or event["dismissal_id"] in seen_ids
                or not isinstance(event.get("kind"), str)
                or not event["kind"]
            ):
                raise ValueError("Invalid saved dismissal data.")
            seen_ids.add(event["dismissal_id"])
            kind = event["kind"].strip()
            if not kind:
                raise ValueError("Invalid saved dismissal data.")
            if kind.casefold() in NOT_DISMISSALS | {"not out"}:
                continue
            by_format[row["format"]][kind] += 1

    counted = sum(sum(counts.values()) for counts in by_format.values())
    plotted = {
        "batting_innings": len(rows),
        "dismissals": counted,
        "excluded_incomplete_batting_innings": excluded,
        "legacy_missing_dismissal_kinds": legacy_missing,
        "formats": sorted({row["format"] for row in rows}),
        "method": "Counts scoreboard dismissals; retired hurt and retired not out are excluded.",
    }
    if legacy_missing or not counted:
        return None, plotted

    series = [
        {
            "name": cricket_format.upper(),
            "points": [
                {
                    "x": kind,
                    "y": count,
                    "sample_size": sum(row["format"] == cricket_format for row in rows),
                }
                for kind, count in sorted(by_format[cricket_format].items())
            ],
        }
        for cricket_format in ("odi", "t20i")
        if by_format.get(cricket_format)
    ]
    plotted["data_points"] = sum(len(item["points"]) for item in series)
    return {
        "metric": "dismissals",
        "group_by": "kind",
        "chart_type": "donut",
        "title": "Dismissal share by kind",
        "x_label": "Dismissal kind",
        "y_label": "Dismissals in complete innings",
        "series": series,
    }, plotted
