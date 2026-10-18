"""Structured tick summary formatting and post-tick logging for ``runtime.commander``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
from pathlib import Path
import random

from spaceinvasion.core.coercion import fmt_compact_int
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.core.redaction import redact_sensitive
from spaceinvasion.runtime.commander._types import (
    GameStateCmdLike,
    PlanetCmdLike,
    PlannedActionCmdLike,
)
from spaceinvasion.runtime.commander.errors import safe_cmd_int, safe_cmd_str
from spaceinvasion.runtime.commander.wakeup import compute_next_wakeup_seconds
from spaceinvasion.runtime.logs import append_tick_logs, format_action_summary

LOGGER = logging.getLogger("spaceinvasion.runtime.commander")


def should_refresh_buildings_after_tick(
    need_buildings: bool,
    effective_live: bool,
    outcomes: Sequence[Mapping[str, object]],
) -> bool:
    """Return True when building state was refreshed during this tick."""
    if need_buildings:
        return True
    if not effective_live:
        return False
    for item in outcomes:
        if not isinstance(item, Mapping) or not bool(item.get("executed")):
            continue
        act = item.get("action")
        if isinstance(act, Mapping) and safe_cmd_str(act.get("kind")).upper() in ("BUILD", "CLAIM_TUTORIAL"):
            return True
        if safe_cmd_str(item.get("command")).lower() in ("build", "claim_tutorial"):
            return True
    return False


def format_metal_production_tag(planet: PlanetCmdLike | None) -> str:
    """Format the ``| MetalProd=ON/OFF(X%)`` tag for normal planets with a Blast Furnace."""
    if (
        planet is None
        or normalize_planet_type(planet.planet_type) in ("asteroid", "raid")
        or planet.building_level(2) < 1
    ):
        return ""
    raw_r2: object = (planet.economy_rates or {}).get("resource2", 100)
    if isinstance(raw_r2, str) and raw_r2.strip().endswith("%"):
        raw_r2 = raw_r2.strip()[:-1].strip()
    b2_pct = max(0, min(100, safe_cmd_int(raw_r2, 100)))
    return f" | MetalProd={'ON' if b2_pct > 0 else 'OFF'}({b2_pct}%)"


def format_tick_summary_lines(
    *,
    tick: int,
    mode_label: str,
    state: GameStateCmdLike,
    actual_action: PlannedActionCmdLike | None,
    actual_result: Mapping[str, object] | None,
    next_action: PlannedActionCmdLike | None,
    outcomes: Sequence[Mapping[str, object]],
) -> tuple[str, str, str]:
    """Build the three redacted human-readable summary lines for a completed tick."""
    planet = state.active_planet()
    res_str = planet.resources.format_short() if planet is not None else "?"
    raw_msg = actual_result.get("message", "no action") if isinstance(actual_result, Mapping) else "no action"
    msg = str(redact_sensitive(safe_cmd_str(raw_msg, "no action")))
    coords_txt = str(redact_sensitive(safe_cmd_str(planet.coords, "?"))) if planet is not None else "?"
    metal_tag = format_metal_production_tag(planet)
    executed_count = sum(1 for o in outcomes if isinstance(o, Mapping) and bool(o.get("executed")))
    batch_tag = f" | BatchExec={executed_count}" if executed_count > 1 else ""
    headline = f"[CMD tick {fmt_compact_int(tick)} | {mode_label}] {coords_txt} | {res_str}{metal_tag}{batch_tag}"
    actual_line = f"  Actual: {format_action_summary(actual_action)} => {msg}"
    next_line = f"  Next:   {format_action_summary(next_action)}"
    return headline, actual_line, next_line


def summarize_and_log_tick(
    *,
    tick: int,
    mode_label: str,
    state: GameStateCmdLike,
    actual_action: PlannedActionCmdLike | None,
    actual_result: Mapping[str, object] | None,
    next_action: PlannedActionCmdLike | None,
    outcomes: Sequence[Mapping[str, object]],
    run_loop: bool,
    eff_min_interval_s: float,
    eff_max_idle_s: float,
    fleet_slot_cooldowns: Mapping[int, float] | None,
    log_dir: Path | None,
    logger: logging.Logger = LOGGER,
    jitter_fn: Callable[[float, float], float] = random.uniform,
) -> float | None:
    """Emit structured summary log lines, compute next wakeup sleep, and append tick logs."""
    headline, actual_line, next_line = format_tick_summary_lines(
        tick=tick,
        mode_label=mode_label,
        state=state,
        actual_action=actual_action,
        actual_result=actual_result,
        next_action=next_action,
        outcomes=outcomes,
    )
    logger.info("%s", headline)
    logger.info("%s", actual_line)
    logger.info("%s", next_line)

    loop_sleep: float | None = None
    if run_loop:
        wake_s, wake_reason = compute_next_wakeup_seconds(
            state,
            next_action,
            outcomes,
            min_interval_s=eff_min_interval_s,
            max_idle_s=eff_max_idle_s,
            fleet_slot_cooldowns=fleet_slot_cooldowns,
        )
        jitter_s = jitter_fn(0.5, 2.5) if wake_s >= 5.0 else 0.0
        loop_sleep = wake_s + jitter_s
        if loop_sleep >= 5.0:
            logger.info("  Wake:   in %.0fs (%s)", loop_sleep, redact_sensitive(wake_reason))

    if log_dir is not None:
        append_tick_logs(
            log_dir,
            source="commander",
            tick=tick,
            mode=mode_label,
            state=state,
            actual_action=actual_action,
            actual_result=actual_result,
            next_action=next_action,
            outcomes=outcomes,
        )
    return loop_sleep
