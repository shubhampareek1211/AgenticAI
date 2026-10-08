"""Reconstruct ordered batting stands and plot their run contributions."""

from collections.abc import Iterable, Mapping

from cricket.models import Dataset, Delivery, Wicket


def innings_partnerships(
    deliveries: Iterable[Delivery],
    wickets_by_delivery: Mapping[int, Iterable[Wicket]],
    player_names: Mapping[str, str],
) -> tuple[list[dict], str | None]:
    """Return complete stands, or a reason that boundaries cannot be trusted.

    Delivery order and batter IDs, rather than inferred strike rotation, define a
    stand. A wicket or retirement ends the current stand after that delivery.
    """
    balls = list(deliveries)
    if not balls:
        return [], "No recorded deliveries in the complete innings."
    if any(
        type(ball.sequence) is not int or type(ball.id) is not int or ball.id <= 0 for ball in balls
    ):
        return [], "Delivery identity or sequence is unresolved."
    balls.sort(key=lambda ball: ball.sequence)
    if any(ball.sequence != index for index, ball in enumerate(balls, 1)):
        return [], "Delivery sequence is missing or duplicated."
    ball_ids = {ball.id for ball in balls}
    if len(ball_ids) != len(balls) or any(
        ball_id not in ball_ids for ball_id in wickets_by_delivery
    ):
        return [], "Wicket delivery cannot be matched to a unique recorded ball."

    stands: list[dict] = []
    current: dict | None = None
    for ball in balls:
        batter, non_striker = ball.batter_id, ball.non_striker_id
        if (
            not isinstance(batter, str)
            or not batter
            or not isinstance(non_striker, str)
            or not non_striker
            or batter == non_striker
            or any(
                not isinstance(player_names.get(identity), str)
                or not player_names[identity].strip()
                for identity in (batter, non_striker)
            )
        ):
            return [], "Both batters need distinct, resolved player IDs and names."
        runs = (ball.batter_runs, ball.extras_runs, ball.total_runs)
        if (
            any(type(value) is not int or value < 0 for value in runs)
            or runs[0] + runs[1] != runs[2]
            or ball.data_complete is not True
        ):
            return [], "Delivery runs or completeness cannot be verified."
        pair = {batter, non_striker}
        if current is None:
            current = {
                "number": len(stands) + 1,
                "batter_a_id": batter,
                "batter_a_name": player_names[batter],
                "batter_a_runs": 0,
                "batter_b_id": non_striker,
                "batter_b_name": player_names[non_striker],
                "batter_b_runs": 0,
                "extras": 0,
                "runs": 0,
                "deliveries": 0,
                "ended_by": [],
            }
        elif pair != {current["batter_a_id"], current["batter_b_id"]}:
            return [], "Batter pair changed without a recorded wicket or retirement."

        component = "batter_a_runs" if batter == current["batter_a_id"] else "batter_b_runs"
        current[component] += ball.batter_runs
        current["extras"] += ball.extras_runs
        current["runs"] += ball.total_runs
        current["deliveries"] += 1

        wicket_rows = list(wickets_by_delivery.get(ball.id, ()))
        if wicket_rows:
            if any(
                not isinstance(wicket.player_out_id, str)
                or wicket.player_out_id not in pair
                or not isinstance(wicket.kind, str)
                or not wicket.kind.strip()
                or type(wicket.ordinal) is not int
                or wicket.ordinal <= 0
                for wicket in wicket_rows
            ):
                return [], "A wicket cannot be assigned to the current batter pair."
            current["ended_by"] = [
                {"player_id": wicket.player_out_id, "kind": wicket.kind}
                for wicket in sorted(wicket_rows, key=lambda wicket: wicket.ordinal)
            ]
            stands.append(current)
            current = None
    if current is not None:
        stands.append(current)
    return stands, None


def partnership_chart(dataset: Dataset) -> tuple[dict | None, dict]:
    """Build per-innings stacked bars from saved, validated stands."""
    match = dataset.data.get("match")
    innings = dataset.data.get("innings")
    if not isinstance(match, dict) or not isinstance(innings, list):
        raise TypeError("Invalid match dataset.")
    match_id = match.get("id")
    if not isinstance(match_id, str) or not match_id:
        raise ValueError("Invalid match identity.")

    series = []
    included = excluded = 0
    reason = None
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
            or any(
                not isinstance(over, dict) or type(over.get("runs")) is not int or over["runs"] < 0
                for over in overs
            )
        ):
            raise ValueError("Invalid match innings.")
        stands = entry.get("partnerships")
        if entry.get("partnerships_error"):
            reason = str(entry["partnerships_error"])
            break
        if not isinstance(stands, list):
            reason = "This match snapshot predates partnership records. Reload the match."
            break
        if not stands:
            reason = "No reliable partnerships were recorded for a complete innings."
            break

        points_by_component = {component: [] for component in ("batter_a", "batter_b", "extras")}
        for index, stand in enumerate(stands, 1):
            if (
                not isinstance(stand, dict)
                or type(stand.get("number")) is not int
                or stand["number"] != index
            ):
                raise ValueError("Invalid saved partnership order.")
            a_id, b_id = stand.get("batter_a_id"), stand.get("batter_b_id")
            a_name, b_name = stand.get("batter_a_name"), stand.get("batter_b_name")
            if (
                not all(isinstance(value, str) and value for value in (a_id, b_id, a_name, b_name))
                or a_id == b_id
                or any(
                    type(stand.get(key)) is not int or stand[key] < 0
                    for key in ("batter_a_runs", "batter_b_runs", "extras", "runs", "deliveries")
                )
                or stand["deliveries"] == 0
                or stand["runs"]
                != stand["batter_a_runs"] + stand["batter_b_runs"] + stand["extras"]
            ):
                raise ValueError("Invalid saved partnership contributions.")
            x = f"I{number} · Stand {index}"
            common = {
                "x": x,
                "innings_number": number,
                "team": team,
                "stand_number": index,
                "stand_runs": stand["runs"],
                "batter_a_name": a_name,
                "batter_b_name": b_name,
                "sample_size": stand["deliveries"],
            }
            for component, key, name, identity in (
                ("batter_a", "batter_a_runs", a_name, a_id),
                ("batter_b", "batter_b_runs", b_name, b_id),
                ("extras", "extras", "Extras", None),
            ):
                points_by_component[component].append(
                    {
                        **common,
                        "y": stand[key],
                        "component": component,
                        "batter_name": name,
                        "batter_id": identity,
                    }
                )
        if sum(stand["runs"] for stand in stands) != sum(over["runs"] for over in overs):
            raise ValueError("Saved partnership runs do not match recorded delivery overs.")
        included += 1
        for component, label in (
            ("batter_a", "Batter A"),
            ("batter_b", "Batter B"),
            ("extras", "Extras"),
        ):
            series.append(
                {
                    "name": f"{team} (innings {number}) — {label}",
                    "innings_number": number,
                    "component": component,
                    "points": points_by_component[component],
                }
            )

    plotted = {
        "innings": included,
        "excluded_incomplete_or_super_over_innings": excluded,
        "stands": sum(len(item["points"]) for item in series if item["component"] == "batter_a"),
        "data_points": sum(len(item["points"]) for item in series),
        "method": "Each delivery is counted once: striker's batter runs plus delivery extras. Wickets and retirements end stands. Pre/post innings penalties are excluded.",
    }
    if reason:
        plotted["insufficient_data_reason"] = reason
        return None, plotted
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
        "metric": "partnership_runs",
        "group_by": "stand",
        "chart_type": "stacked_bar",
        "title": f"Partnership runs — {context}",
        "x_label": "Partnership",
        "y_label": "Stand runs",
        "series": series,
    }, plotted
