"""Read saved cricket charts without depending on a browser charting library."""

import math
from html import unescape


def _title(value: object, fallback: str) -> str:
    if isinstance(value, dict):
        value = value.get("text")
    return unescape(value) if isinstance(value, str) else fallback


def _point_value(value: object, *, ordinate: bool = False):
    if ordinate and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("Unsupported saved chart value.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Unsupported saved chart value.")
    if ordinate and isinstance(value, str):
        raise ValueError("Unsupported saved chart value.")
    return value


def normalize_chart_spec(spec: dict) -> dict:
    """Return v2 chart data; translate only literal values from legacy Plotly traces.

    The adapter never executes a figure, evaluates expressions, or recalculates a metric.
    Unknown trace kinds are rejected so a historic record cannot inject chart options.
    """
    if not isinstance(spec, dict):
        raise TypeError("Invalid saved chart.")
    if spec.get("schema_version") == 2:
        chart = spec.get("chart")
        if not isinstance(chart, dict) or not isinstance(chart.get("series"), list):
            raise ValueError("Invalid saved chart.")
        return {
            "schema_version": 2,
            "chart": chart,
            "coverage": spec.get("coverage", {}),
            "provenance": spec.get("provenance", []),
        }
    figure = spec.get("figure")
    if not isinstance(figure, dict) or not isinstance(figure.get("data"), list):
        raise TypeError("Invalid saved chart.")
    layout = figure.get("layout") or {}
    if not isinstance(layout, dict):
        raise TypeError("Invalid saved chart.")
    series = []
    chart_type = spec.get("chart_type")
    for trace in figure["data"]:
        if not isinstance(trace, dict) or trace.get("type") not in {"bar", "scatter"}:
            raise ValueError("Unsupported saved chart trace.")
        xs, ys = trace.get("x"), trace.get("y")
        if not isinstance(xs, list) or not isinstance(ys, list) or len(xs) != len(ys):
            raise ValueError("Invalid saved chart trace.")
        trace_type = (
            "bar"
            if trace["type"] == "bar"
            else "line"
            if "lines" in str(trace.get("mode", ""))
            else "scatter"
        )
        if chart_type is None:
            chart_type = trace_type
        elif chart_type != trace_type:
            raise ValueError("Mixed saved chart traces are unsupported.")
        custom = trace.get("customdata")
        if custom is not None and (not isinstance(custom, list) or len(custom) != len(xs)):
            raise ValueError("Invalid saved chart trace.")
        points = []
        for index, (x, y) in enumerate(zip(xs, ys, strict=True)):
            point = {"x": _point_value(x), "y": _point_value(y, ordinate=True)}
            if custom is not None and isinstance(custom[index], list) and len(custom[index]) == 2:
                match_id, innings_number = custom[index]
                if isinstance(match_id, str) and type(innings_number) is int:
                    point.update({"match_id": match_id, "innings_number": innings_number})
            points.append(point)
        series.append({"name": _title(trace.get("name"), "Series"), "points": points})
    xaxis, yaxis = layout.get("xaxis") or {}, layout.get("yaxis") or {}
    if not isinstance(xaxis, dict) or not isinstance(yaxis, dict):
        raise TypeError("Invalid saved chart axes.")
    return {
        "schema_version": 2,
        "chart": {
            "metric": spec.get("metric", "unknown"),
            "group_by": spec.get("group_by", "unknown"),
            "chart_type": chart_type or "bar",
            "title": _title(layout.get("title"), "Cricket chart"),
            "x_label": _title(xaxis.get("title"), ""),
            "y_label": _title(yaxis.get("title"), ""),
            "series": series,
        },
        "coverage": spec.get("coverage", {}),
        "provenance": spec.get("provenance", []),
    }
