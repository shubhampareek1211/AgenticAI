"""Build renderer-independent charts from conversation-owned cricket datasets."""

import math
import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from cricket.models import ChartSpec, Dataset
from cricket.results import error_result

ANNUAL_METRICS = {
    "runs": ("Runs by year", "Batter runs"),
    "batting_average": ("Batting average by year", "Runs per dismissal"),
    "mean_runs_per_innings": ("Mean runs per innings by year", "Runs per innings"),
    "runs_per_100_legal_balls": ("Runs per 100 legal balls by year", "Runs per 100 legal balls"),
    "boundaries": ("Boundaries by year", "Fours and sixes"),
}
PLAYER_VIEWS = {
    (metric, "year", chart_type) for metric in ANNUAL_METRICS for chart_type in ("bar", "line")
} | {
    ("batting_average", "rolling_innings", "line"),
    ("runs_per_100_legal_balls", "rolling_innings", "line"),
    ("dismissals", "kind", "bar"),
    ("dismissals", "kind", "donut"),
    ("runs", "innings_date", "line"),
    ("runs", "innings_date", "scatter"),
    ("runs", "balls_faced", "scatter"),
}
WICKET_VIEWS = {
    ("runs_per_100_balls", "wicket_phase", "bar"),
    ("boundary_ball_percentage", "wicket_phase", "bar"),
}
MATCH_VIEWS = {
    ("runs_per_over", "over", "bar"),
    ("cumulative_runs", "over", "line"),
    ("run_components", "over", "stacked_area"),
    ("partnership_runs", "stand", "stacked_bar"),
}
BATTER_BOWLER_VIEWS = {("runs_per_100_legal_balls", "bowler_phase", "heatmap")}
SQUAD_VIEWS = {("batting_average", "strike_rate", "bubble")}


def chart_error(code: str, message: str, dataset: Dataset | None = None) -> dict:
    """Keep available source and coverage details on a domain failure."""
    result = error_result(code, message)
    if dataset is not None:
        result["provenance"] = dataset.provenance.get("sources", [])
        result["coverage"] = dataset.coverage
    return result


def complete_innings(dataset: Dataset) -> tuple[list[dict], int]:
    """Validate saved innings and exclude incomplete observations before plotting."""
    raw = dataset.data.get("innings")
    if not isinstance(raw, list):
        raise TypeError("Invalid player dataset.")
    included, excluded = [], 0
    for row in raw:
        if not isinstance(row, dict) or not isinstance(row.get("data_complete"), bool):
            raise TypeError("Invalid player dataset.")
        if not row["data_complete"]:
            excluded += 1
            continue
        try:
            when = date.fromisoformat(row["date"])
            runs, balls = row["runs"], row["legal_balls"]
            fours, sixes, dismissed = row["fours"], row["sixes"], row["dismissed"]
            if (
                when.isoformat() != row["date"]
                or row["format"] not in {"odi", "t20i"}
                or not isinstance(row["match_id"], str)
                or not isinstance(row["innings_number"], int)
                or isinstance(row["innings_number"], bool)
                or not all(
                    type(value) is int and value >= 0 for value in (runs, balls, fours, sixes)
                )
                or not isinstance(dismissed, bool)
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("Invalid player dataset.") from None
        included.append(row)
    included.sort(key=lambda row: (row["date"], row["match_id"], row["innings_number"]))
    return included, excluded


def annual_point(metric: str, year: str, rows: list[dict]) -> dict:
    """Carry sample size, denominators, and boundary breakdown with each value."""
    runs = sum(row["runs"] for row in rows)
    dismissals = sum(row["dismissed"] for row in rows)
    balls = sum(row["legal_balls"] for row in rows)
    fours = sum(row["fours"] for row in rows)
    sixes = sum(row["sixes"] for row in rows)
    values = {
        "runs": runs,
        "batting_average": runs / dismissals if dismissals else None,
        "mean_runs_per_innings": runs / len(rows),
        "runs_per_100_legal_balls": 100 * runs / balls if balls else None,
        "boundaries": fours + sixes,
    }
    point = {"x": year, "y": values[metric], "sample_size": len(rows)}
    if metric == "batting_average":
        point.update({"runs": runs, "dismissals": dismissals})
    elif metric == "mean_runs_per_innings":
        point["runs"] = runs
    elif metric == "runs_per_100_legal_balls":
        point.update({"runs": runs, "legal_balls": balls})
    elif metric == "boundaries":
        point.update({"fours": fours, "sixes": sixes})
    if point["y"] is None:
        point["note"] = (
            "No dismissals in complete batting innings."
            if metric == "batting_average"
            else "No legal balls faced in complete batting innings."
        )
    return point


def player_chart(dataset: Dataset, metric: str, group_by: str, chart_type: str):
    """Derive every point from complete saved innings, grouped by format."""
    rows, excluded = complete_innings(dataset)
    if not rows:
        return None, {"batting_innings": 0, "excluded_incomplete_batting_innings": excluded}
    identity = dataset.data.get("identity", {})
    name = str(identity.get("espn_display_name") or identity.get("name") or dataset.player_id)
    by_format = defaultdict(list)
    for row in rows:
        by_format[row["format"]].append(row)
    series = []
    for cricket_format in ("odi", "t20i"):
        group = by_format.get(cricket_format, [])
        if not group:
            continue
        if group_by == "year":
            by_year = defaultdict(list)
            for row in group:
                by_year[row["date"][:4]].append(row)
            points = [annual_point(metric, year, by_year[year]) for year in sorted(by_year)]
        else:
            points = [
                {
                    "x": row["legal_balls"] if group_by == "balls_faced" else row["date"],
                    "y": row["runs"],
                    "sample_size": 1,
                    "match_id": row["match_id"],
                    "innings_number": row["innings_number"],
                }
                for row in group
            ]
        series.append({"name": cricket_format.upper(), "points": points})
    if group_by == "year":
        title, y_label = ANNUAL_METRICS[metric]
        x_label = "Year"
    elif group_by == "innings_date":
        title, x_label, y_label = "Innings scores over time", "Match date", "Batter runs"
    else:
        title, x_label, y_label = "Runs versus balls faced", "Legal balls faced", "Batter runs"
    chart = {
        "metric": metric,
        "group_by": group_by,
        "chart_type": chart_type,
        "title": f"{name}: {title}",
        "x_label": x_label,
        "y_label": y_label,
        "series": series,
    }
    plotted = {
        "matches": len({row["match_id"] for row in rows}),
        "batting_innings": len(rows),
        "data_points": sum(len(item["points"]) for item in series),
        "excluded_incomplete_batting_innings": excluded,
        "date_start": rows[0]["date"],
        "date_end": rows[-1]["date"],
        "formats": sorted(by_format),
    }
    return chart, plotted


def rolling_chart(dataset: Dataset, metric: str, window_size: int):
    """Compute full moving windows and a format-specific imported-match baseline."""
    rows, excluded = complete_innings(dataset)
    by_format = defaultdict(list)
    for row in rows:
        by_format[row["format"]].append(row)
    series = []
    for cricket_format in ("odi", "t20i"):
        group = by_format.get(cricket_format, [])
        if len(group) < window_size:
            continue
        total_runs = sum(row["runs"] for row in group)
        total_dismissals = sum(row["dismissed"] for row in group)
        total_balls = sum(row["legal_balls"] for row in group)
        baseline_denominator = total_dismissals if metric == "batting_average" else total_balls
        baseline = (
            total_runs / baseline_denominator * (100 if metric == "runs_per_100_legal_balls" else 1)
            if baseline_denominator
            else None
        )
        points, baseline_points = [], []
        for end in range(window_size, len(group) + 1):
            window = group[end - window_size : end]
            runs = sum(row["runs"] for row in window)
            denominator = sum(
                row["dismissed"] if metric == "batting_average" else row["legal_balls"]
                for row in window
            )
            value = (
                runs / denominator * (100 if metric == "runs_per_100_legal_balls" else 1)
                if denominator
                else None
            )
            point = {
                "x": end,
                "y": value,
                "sample_size": window_size,
                "date": group[end - 1]["date"],
                "match_id": group[end - 1]["match_id"],
                "innings_number": group[end - 1]["innings_number"],
                "runs": runs,
                "dismissals" if metric == "batting_average" else "legal_balls": denominator,
            }
            if value is None:
                point["note"] = (
                    "No dismissals in this complete-innings window."
                    if metric == "batting_average"
                    else "No legal balls faced in this complete-innings window."
                )
            points.append(point)
            baseline_point = {"x": end, "y": baseline, "sample_size": len(group)}
            if baseline is None:
                baseline_point["note"] = (
                    "No dismissals in complete imported batting innings."
                    if metric == "batting_average"
                    else "No legal balls faced in complete imported batting innings."
                )
            baseline_points.append(baseline_point)
        series.extend(
            [
                {"name": cricket_format.upper(), "points": points},
                {"name": f"{cricket_format.upper()} baseline", "points": baseline_points},
            ]
        )
    plotted = {
        "matches": len({row["match_id"] for row in rows}),
        "batting_innings": len(rows),
        "excluded_incomplete_batting_innings": excluded,
        "window_size": window_size,
        "data_points": sum(
            len(item["points"]) for item in series if "baseline" not in item["name"]
        ),
        "formats": sorted(by_format),
    }
    if not series:
        return None, plotted
    title, y_label = (
        ("Rolling batting average", "Runs per dismissal")
        if metric == "batting_average"
        else ("Rolling runs per 100 legal balls", "Runs per 100 legal balls")
    )
    chart = {
        "metric": metric,
        "group_by": "rolling_innings",
        "chart_type": "line",
        "title": f"{title} ({window_size} innings)",
        "x_label": "Complete batting innings in format",
        "y_label": y_label,
        "window_size": window_size,
        "series": series,
    }
    return chart, plotted


def dismissal_chart(dataset: Dataset):
    """Count saved dismissal events by kind for each format."""
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
            by_format[row["format"]][event["kind"]] += 1
    plotted = {
        "batting_innings": len(rows),
        "dismissals": len(seen_ids),
        "excluded_incomplete_batting_innings": excluded,
        "legacy_missing_dismissal_kinds": legacy_missing,
        "formats": sorted({row["format"] for row in rows}),
        "method": "Counts scoreboard dismissals; retired hurt and retired not out are excluded.",
    }
    if legacy_missing or not seen_ids:
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
        "chart_type": "bar",
        "title": "Dismissals by kind",
        "x_label": "Dismissal kind",
        "y_label": "Dismissals in complete innings",
        "series": series,
    }, plotted


def match_chart(dataset: Dataset, metric: str):
    """Chart only saved complete regular innings; over numbers become one-based labels."""
    match = dataset.data.get("match")
    innings = dataset.data.get("innings")
    if not isinstance(match, dict) or not isinstance(innings, list):
        raise TypeError("Invalid match dataset.")
    match_id = match.get("id")
    if not isinstance(match_id, str) or not match_id:
        raise ValueError("Invalid match identity.")
    series = []
    excluded = 0
    for entry in innings:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("data_complete"), bool)
            or not isinstance(entry.get("super_over"), bool)
        ):
            raise TypeError("Invalid match innings.")
        if not entry["data_complete"] or entry["super_over"]:
            excluded += 1
            continue
        if (
            type(entry.get("number")) is not int
            or entry["number"] <= 0
            or not isinstance(entry.get("team"), str)
            or not entry["team"]
        ):
            raise ValueError("Invalid match innings.")
        overs = entry.get("overs")
        if not isinstance(overs, list):
            raise TypeError("Invalid match overs.")
        seen_overs = set()
        points = []
        cumulative = 0
        if any(
            not isinstance(over, dict)
            or any(
                type(over.get(key)) is not int or over[key] < 0
                for key in ("over_number", "runs", "wickets", "legal_balls")
            )
            for over in overs
        ):
            raise TypeError("Invalid match over.")
        for over in sorted(overs, key=lambda item: item.get("over_number", -1)):
            number, runs, wickets, legal_balls = (
                over.get("over_number"),
                over.get("runs"),
                over.get("wickets"),
                over.get("legal_balls"),
            )
            if number in seen_overs:
                raise ValueError("Invalid match over.")
            seen_overs.add(number)
            cumulative += runs
            points.append(
                {
                    "x": number + 1,
                    "y": runs if metric == "runs_per_over" else cumulative,
                    "wickets": wickets,
                    "legal_balls": legal_balls,
                    "sample_size": legal_balls,
                }
            )
        if points:
            series.append(
                {"name": f"{entry['team']} (innings {entry['number']})", "points": points}
            )
    plotted = {
        "innings": len(series),
        "excluded_incomplete_or_super_over_innings": excluded,
        "data_points": sum(len(item["points"]) for item in series),
        "wicket_method": "Scoreboard dismissals in each over; retired hurt and retired not out are excluded.",
    }
    if not series:
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
    return {
        "metric": metric,
        "group_by": "over",
        "chart_type": "bar" if metric == "runs_per_over" else "line",
        "title": ("Runs per over" if metric == "runs_per_over" else "Cumulative runs by over")
        + f" — {context}",
        "x_label": "Over",
        "y_label": "Runs",
        "series": series,
    }, plotted


def wicket_chart(dataset: Dataset, metric: str):
    """Expose saved before/after rates without recomputing event metrics."""
    eligible = dataset.coverage.get("eligible_events")
    if isinstance(eligible, bool) or not isinstance(eligible, int) or eligible < 0:
        raise ValueError("Invalid wicket-response dataset.")
    if eligible == 0:
        return None, {"eligible_events": 0, "data_points": 0, "insufficient_sample": True}
    rates = dataset.data.get("rates")
    if not isinstance(rates, dict):
        raise TypeError("Invalid wicket-response dataset.")
    values = []
    for phase in ("before", "after"):
        item = rates.get(phase)
        value = item.get(metric) if isinstance(item, dict) else None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("Invalid wicket-response dataset.")
        if value < 0 or (metric == "boundary_ball_percentage" and value > 100):
            raise ValueError("Invalid wicket-response dataset.")
        values.append(value)
    insufficient = eligible < 10
    label = (
        "Runs per 100 legal balls" if metric == "runs_per_100_balls" else "Boundary-ball percentage"
    )
    chart = {
        "metric": metric,
        "group_by": "wicket_phase",
        "chart_type": "bar",
        "title": f"Wicket response: {label}" + (" (insufficient sample)" if insufficient else ""),
        "x_label": "Metric",
        "y_label": label,
        "series": [
            {"name": phase.title(), "points": [{"x": label, "y": value, "sample_size": eligible}]}
            for phase, value in zip(("before", "after"), values, strict=True)
        ],
    }
    return chart, {
        "eligible_events": eligible,
        "data_points": 2,
        "insufficient_sample": insufficient,
    }


def create_cricket_chart(
    session: Session,
    conversation_id: uuid.UUID,
    *,
    dataset_id: str,
    chart_type: str,
    metric: str,
    group_by: str,
    window_size: int | None = None,
) -> dict:
    """Persist a versioned chart, returning a compact reference for the chat loop."""
    try:
        dataset_uuid = uuid.UUID(dataset_id)
    except (TypeError, ValueError, AttributeError):
        return chart_error("invalid_arguments", "dataset_id must be a UUID.")
    view = (metric, group_by, chart_type)
    if view not in PLAYER_VIEWS | WICKET_VIEWS | MATCH_VIEWS | BATTER_BOWLER_VIEWS | SQUAD_VIEWS:
        return chart_error("invalid_chart", "Choose a supported metric, grouping, and chart type.")
    if group_by == "rolling_innings":
        if window_size is None:
            window_size = 5
        if type(window_size) is not int or not 3 <= window_size <= 20:
            return chart_error("invalid_arguments", "window_size must be an integer from 3 to 20.")
    elif window_size is not None:
        return chart_error("invalid_arguments", "window_size applies only to rolling charts.")
    dataset = session.get(Dataset, dataset_uuid)
    if dataset is None or dataset.conversation_id != conversation_id:
        return chart_error("dataset_not_found", "Dataset not found in this conversation.")
    expected_views = {
        "player_data": PLAYER_VIEWS,
        "wicket_response": WICKET_VIEWS,
        "match_data": MATCH_VIEWS,
        "batter_bowler_data": BATTER_BOWLER_VIEWS,
        "squad_comparison": SQUAD_VIEWS,
    }
    if dataset.kind not in expected_views:
        return chart_error("invalid_chart", "This dataset does not support charts.", dataset)
    if view not in expected_views[dataset.kind]:
        return chart_error("invalid_chart", "This chart is incompatible with the dataset.", dataset)
    try:
        if dataset.kind == "player_data":
            if group_by == "rolling_innings":
                chart, plotted = rolling_chart(dataset, metric, window_size)
            elif group_by == "kind":
                if chart_type == "donut":
                    from cricket.variety_donut import donut_chart

                    chart, plotted = donut_chart(dataset)
                else:
                    chart, plotted = dismissal_chart(dataset)
            else:
                chart, plotted = player_chart(dataset, metric, group_by, chart_type)
        elif dataset.kind == "wicket_response":
            chart, plotted = wicket_chart(dataset, metric)
        elif dataset.kind == "batter_bowler_data":
            from cricket.variety_heatmap import heatmap_chart

            chart, plotted = heatmap_chart(dataset)
        elif dataset.kind == "squad_comparison":
            from cricket.variety_bubble import bubble_chart

            chart, plotted = bubble_chart(dataset)
        else:
            if chart_type == "stacked_area":
                from cricket.variety_area import area_chart

                chart, plotted = area_chart(dataset)
            elif chart_type == "stacked_bar":
                from cricket.variety_partnership import partnership_chart

                chart, plotted = partnership_chart(dataset)
            else:
                chart, plotted = match_chart(dataset, metric)
    except (KeyError, TypeError, ValueError):
        return chart_error("invalid_dataset", "Saved chart data is invalid.", dataset)
    if chart is None:
        if (
            dataset.kind == "player_data"
            and group_by == "kind"
            and plotted.get("legacy_missing_dismissal_kinds")
        ):
            message = "Refresh player data to chart dismissal kinds."
        elif dataset.kind == "player_data" and group_by == "kind":
            message = "No scoreboard dismissals occur in complete batting innings."
        elif dataset.kind == "player_data" and group_by == "rolling_innings":
            message = f"At least {window_size} complete batting innings in one format are required."
        elif dataset.kind == "batter_bowler_data":
            message = "No batter-bowler phase cell has at least six legal balls."
        elif dataset.kind == "squad_comparison":
            message = (
                "No batters meet the innings and legal-ball minimums for a squad bubble chart."
            )
        elif chart_type == "stacked_area" and plotted.get("legacy_missing_components"):
            message = "Reload the match to chart over run components."
        elif chart_type == "stacked_bar" and plotted.get("insufficient_data_reason"):
            message = plotted["insufficient_data_reason"]
        elif dataset.kind == "match_data":
            message = "No complete regular innings with over data match this dataset."
        elif dataset.kind == "wicket_response":
            message = "No eligible wicket-response events match this dataset."
        else:
            message = "No complete batting innings match this dataset."
        return chart_error(
            "insufficient_data",
            message,
            dataset,
        )
    coverage = {**dataset.coverage, "plotted": plotted}
    provenance = dataset.provenance.get("sources", [])
    chart = ChartSpec(
        conversation_id=conversation_id,
        dataset_id=dataset_uuid,
        spec={
            "schema_version": 2,
            "chart": chart,
            "coverage": coverage,
            "provenance": provenance,
        },
    )
    session.add(chart)
    session.flush()
    return {
        "ok": True,
        "data": {
            "chart_id": str(chart.id),
            "dataset_id": str(dataset_uuid),
            "spec": {
                "schema_version": 2,
                "metric": metric,
                "group_by": group_by,
                "chart_type": chart_type,
                "data_ref": {"chart_id": str(chart.id)},
                **({"window_size": window_size} if group_by == "rolling_innings" else {}),
            },
        },
        "error": None,
        "provenance": provenance,
        "coverage": coverage,
    }
