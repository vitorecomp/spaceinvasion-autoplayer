"""``full_planner_path.txt`` exporter (14-line header + causal numbered action lines)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime

from backend.core.coercion import coerce_finite_float, coerce_int
from backend.planner.ai._types import PATH_HOURS_PER_WEEK
from backend.planner.ai.telemetry import fmt_path_time
from backend.planner.config_optimizer import RESOURCE_KEYS


def _format_factor_levels(entries: Mapping[str, object] | None) -> str:
    if not isinstance(entries, Mapping) or not entries:
        return "none"
    return ", ".join(f"{name} {value}" for name, value in entries.items())


def _format_step_lines(steps: Sequence[object]) -> list[str]:
    width = max(1, len(str(len(steps))))
    lines: list[str] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping):
            continue
        seq = coerce_int(step.get("seq"), idx)
        kind = str(step.get("kind") or "").strip().upper()
        entity_name = str(step.get("entity_name") or step.get("entity_key") or "Unknown")
        target_level = coerce_int(step.get("target_level"), 0)
        start_sec = coerce_finite_float(step.get("start_sec"), 0.0)
        dur_sec = coerce_finite_float(step.get("duration_sec"), 0.0)
        start_txt = str(step.get("start_text") or fmt_path_time(start_sec / 3600.0))
        dur_txt = str(step.get("duration_text") or fmt_path_time(dur_sec / 3600.0))
        coords = str(step.get("planet_coords") or "account")

        if kind == "BUILD":
            action = f"build {entity_name} level {target_level}"
        elif kind in ("DEMOLISH", "DEMOLISH_BUILDING"):
            action = f"tear down {entity_name} to level {target_level}"
        elif kind == "RESEARCH":
            action = f"research {entity_name} level {target_level}"
        elif kind in ("COLONIZE_PREVIEW", "COLONIZE"):
            action = "found the colony"
        else:
            action = f"{kind.lower()} {entity_name} level {target_level}"
        where = "" if (kind == "RESEARCH" or coords == "account") else f" on {coords}"
        lines.append(f"{seq:>{width}}. at {start_txt}{where}: {action} (takes {dur_txt})")
    return lines


def _format_result_summary(summary: Mapping[str, object], weeks: int, line_count: int) -> str:
    status = str(summary.get("status") or "horizon")
    if status == "reached":
        return (
            f"configuration reached after {summary.get('time', '0s')} "
            f"within the {weeks}-week horizon ({line_count} actions)"
        )
    if status == "horizon":
        return (
            f"{weeks}-week horizon ended with {coerce_int(summary.get('steps_started'), line_count)} "
            f"of {coerce_int(summary.get('steps'), line_count)} actions started "
            f"({coerce_int(summary.get('steps_completed'), 0)} finished, "
            f"{coerce_int(summary.get('steps_running'), 0)} running)"
        )
    return (
        f"NOT reached. The path stops after {coerce_int(summary.get('steps_started'), line_count)} "
        f"of {coerce_int(summary.get('steps'), line_count)} actions: "
        f"the empire can never pay for {summary.get('waiting_for')}"
    )


def export_ai_planner_txt(plan_result: Mapping[str, object]) -> str:
    """Export ``run_ai_planner`` output in the exact 14-line header + causal line format."""
    raw_summary = plan_result.get("summary")
    summary: Mapping[str, object] = raw_summary if isinstance(raw_summary, Mapping) else {}
    raw_steps = plan_result.get("steps")
    steps: Sequence[object] = (
        raw_steps if isinstance(raw_steps, Sequence) and not isinstance(raw_steps, (str, bytes)) else ()
    )
    lines = _format_step_lines(steps)

    weeks = coerce_int(summary.get("reward_weeks"), 26)
    discount = coerce_finite_float(summary.get("weekly_discount"), 0.90)
    result_txt = _format_result_summary(summary, weeks, len(lines))

    written = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    config_saved = str(plan_result.get("config_generated_at") or "")
    raw_b_reached = summary.get("max_building_levels_reached")
    raw_r_reached = summary.get("research_levels_reached")
    buildings_line = _format_factor_levels(raw_b_reached if isinstance(raw_b_reached, Mapping) else None)
    research_line = _format_factor_levels(raw_r_reached if isinstance(raw_r_reached, Mapping) else None)
    s0 = coerce_int(summary.get("start_fleet_slots"), 0)
    s1 = coerce_int(summary.get("final_fleet_slots"), 0)
    per_slot = coerce_finite_float(summary.get("attacks_per_slot_per_hour"), 0.0)
    raw_spare = summary.get("spare_percent")
    spare_map: Mapping[str, object] = raw_spare if isinstance(raw_spare, Mapping) else {}
    spare_txt = ", ".join(f"{res} {coerce_finite_float(spare_map.get(res), 0.0):.1f}%" for res in RESOURCE_KEYS)
    disc_reward = coerce_finite_float(summary.get("discounted_reward"), 0.0)
    total_sp = coerce_finite_float(summary.get("total_spice_eq_produced"), 0.0)
    w1_sp = coerce_finite_float(summary.get("week_1_spice_eq"), 0.0)
    wl_sp = coerce_finite_float(summary.get("last_week_spice_eq"), 0.0)
    horizon_txt = str(summary.get("horizon") or fmt_path_time(PATH_HOURS_PER_WEEK * weeks))

    header = [
        f"Best {weeks}-week path toward the saved configuration (notebooks/full_planner.ipynb, Section 11)",
        f"Written: {written}",
        f"Configuration: config_optimizer_result.json, saved {config_saved}",
        f"Strategy: {summary.get('strategy', 'discounted payback')}",
        f"Horizon: {weeks} weeks ({horizon_txt}), weekly discount {discount:.2f}",
        f"Attacks & spare: {s0} -> {s1} fleet slots ({s0 * per_slot:.2f} -> {s1 * per_slot:.2f} attacks/h); "
        f"spare {spare_txt}",
        f"Reward: {disc_reward:,.1f} SpiceEq net discounted "
        f"({total_sp:,.1f} net SpiceEq produced in {weeks} weeks; "
        f"week 1: {w1_sp:,.1f}, week {weeks}: {wl_sp:,.1f})",
        f"Result: {result_txt}",
        f"Colonies reached: {coerce_int(summary.get('colonies_founded'), 0)}/"
        f"{coerce_int(summary.get('colonies_target'), 0)} founded",
        f"Max building levels reached: {buildings_line}",
        f"Research levels reached: {research_line}",
        f"Actions: {len(lines)} within {weeks} weeks, in the order in which they start. "
        "Times count from the moment this file was written.",
        "Planets are named by their coordinates. "
        'EMPTY:nn is an empty colony slot: "found the colony" comes first there.',
        "",
    ]
    return "\n".join([*header, *lines]) + "\n"


__all__ = ["_format_factor_levels", "export_ai_planner_txt"]
