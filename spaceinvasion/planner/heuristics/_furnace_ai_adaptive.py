"""Plan v2/v4 adaptive per-colony and empire smelting decision helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from spaceinvasion.combat.flight import normalize_coords
from spaceinvasion.core.model import GameState, PlanetState
from spaceinvasion.planner.heuristics._cargo_helpers import (
    _fallback_inbound_cargo_by_planet,
    _friendly_cargo_flights,
    _inbound_resource_value,
)
from spaceinvasion.planner.heuristics._common import (
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._types import (
    EMPIRE_SMELT_HOLD_RATIO,
    EMPIRE_SMELT_MIN_SPARE_PIG,
    PlannedAction,
)


@dataclass(frozen=True)
class _AdaptiveEmpireContext:
    """Per-colony adaptive queues, inbound cargo, and Plan v4 empire smelting flags."""

    inbound_map: Mapping[int, object]
    rem_q_map: Mapping[int, float]
    rem_research_sec: float
    donor_need: bool = False
    donor_hold: bool = False
    donor_gap: int = 0
    empire_pig_tight: bool = False
    empire_pig_short: bool = False


def _sum_action_cost(acts: Sequence[PlannedAction], key: str) -> int:
    return sum(max(0, _safe_strategy_int(getattr(a.cost, key, 0), 0)) for a in acts)


def _fallback_queue_remaining_seconds(
    state: GameState,
    now_ts: float,
) -> tuple[dict[int, float], float]:
    """Derive `(build_rem_by_planet_id, research_rem_seconds)` from `state.active_queues`."""
    build_rem: dict[int, float] = {}
    res_rem = 0.0
    by_coords: dict[str, int] = {}
    for p in getattr(state, "planets", None) or ():
        nc = normalize_coords(getattr(p, "coords", "") or "")
        if nc:
            by_coords[nc] = p.planet_id
    active_p = state.active_planet()
    active_pid = active_p.planet_id if active_p is not None else None
    ref_ts = max(0.0, _safe_strategy_float(now_ts, 0.0))
    if ref_ts <= 0.0:
        ref_ts = max(0.0, _safe_strategy_float(getattr(state, "timestamp", 0.0), 0.0))

    for q in getattr(state, "active_queues", None) or ():
        cat = _safe_strategy_str(getattr(q, "category", "")).strip().lower()
        if cat not in ("building", "research"):
            continue
        fin_ts = _safe_strategy_float(getattr(q, "finish_ts", None), 0.0)
        if fin_ts > 0.0 and ref_ts > 0.0:
            rem_s = max(0.0, fin_ts - ref_ts)
        else:
            rem_s = max(0.0, _safe_strategy_float(getattr(q, "remaining_seconds", None), 0.0))
        if rem_s <= 0.0:
            continue
        if cat == "research":
            if rem_s > res_rem:
                res_rem = rem_s
        else:
            orig_c = normalize_coords(getattr(q, "origin_coords", "") or "")
            pid = by_coords.get(orig_c) if orig_c else active_pid
            if pid is not None and rem_s > build_rem.get(pid, 0.0):
                build_rem[pid] = rem_s
    return build_rem, res_rem


def _compute_local_adaptive_trigger(
    local_waiting: Sequence[PlannedAction],
    *,
    have_metal: int,
    local_metal_gap: int,
    rate_m_hr: float,
    rem_build_sec: float,
    rem_research_sec: float,
    step_lbl: str,
) -> tuple[float | None, float | None, str]:
    build_waiting = [a for a in local_waiting if a.kind != "RESEARCH"]
    res_waiting = [a for a in local_waiting if a.kind == "RESEARCH"]
    eff_rem_build = max(0.0, float(rem_build_sec))
    eff_rem_res = max(0.0, float(rem_research_sec))

    if build_waiting:
        b_gap = max(0, _sum_action_cost(build_waiting, "metal") - have_metal)
        if b_gap > 0:
            b_smelt = (b_gap / rate_m_hr) * 3600.0
            if eff_rem_build <= b_smelt:
                return eff_rem_build, b_smelt, build_waiting[0].target_name
    if res_waiting:
        r_gap = max(0, _sum_action_cost(res_waiting, "metal") - have_metal)
        if r_gap > 0:
            r_smelt = (r_gap / rate_m_hr) * 3600.0
            if eff_rem_res <= r_smelt:
                return eff_rem_res, r_smelt, res_waiting[0].target_name
    if build_waiting and res_waiting:
        comb_rem = max(eff_rem_build, eff_rem_res)
        comb_smelt = (local_metal_gap / rate_m_hr) * 3600.0
        if comb_rem <= comb_smelt:
            return comb_rem, comb_smelt, step_lbl
    return None, None, step_lbl


def _adaptive_empire_arm_reason(
    *,
    cur_b2_pct: int,
    metal_is_full: bool,
    donor_need: bool,
    donor_hold: bool,
    donor_gap: int,
    empire_pig_tight: bool,
    empire_pig_short: bool,
    empire_energy_ok: bool,
    res_pig: int,
    sto_pig: int,
    local_ready: Sequence[PlannedAction],
    need_pig: int,
    local_b2_metal_per_hour: float,
) -> str | None:
    """Plan v4 empire smelting arm: return reason string when colony should smelt spare Pig iron."""
    furnace_on = cur_b2_pct > 0
    empire_short = donor_need or (furnace_on and donor_hold)
    pig_blocked = empire_pig_short or (empire_pig_tight and not furnace_on)
    if not (empire_short and empire_energy_ok and not metal_is_full and not pig_blocked):
        return None
    pig_per_hour = 2.0 * max(0.0, float(local_b2_metal_per_hour))
    pig_spare = max(0, res_pig - _sum_action_cost(local_ready, "pig_iron")) - need_pig
    if furnace_on:
        pig_fine = pig_spare > 0
    else:
        pig_fine = pig_spare >= max(EMPIRE_SMELT_MIN_SPARE_PIG, pig_per_hour) or (
            sto_pig > 0 and pig_spare * 2 >= sto_pig
        )
    if not pig_fine:
        return None
    why = (
        f"empire waiting steps need {donor_gap:,} more Metal than pooled + in-flight stock"
        if donor_gap > 0
        else "pooled Metal is still within 10% of what the empire's waiting steps need"
    )
    return f"AI-Planner empire: {why}; smelt this colony's spare Pig iron ({pig_spare:,} above its own steps)"


def _adaptive_colony_smelt_decision(
    planet: PlanetState,
    *,
    waiting_ai: Sequence[PlannedAction],
    ready_ai: Sequence[PlannedAction],
    inbound: object,
    rem_build_sec: float,
    rem_research_sec: float,
    local_b2_metal_per_hour: float,
    cur_b2_pct: int,
    metal_is_full: bool,
    donor_need: bool,
    donor_gap: int,
    donor_hold: bool = False,
    empire_pig_tight: bool = False,
    empire_pig_short: bool = False,
    empire_energy_ok: bool = True,
) -> tuple[bool, PlannedAction | None, int, int, str | None]:
    """Per-colony furnace decision for adaptive mode: `(want_smelt, step, metal_gap, pig_gap, on_reason)`."""
    pid = planet.planet_id
    local_waiting = [a for a in waiting_ai if a.planet_id == pid]
    local_ready = [a for a in ready_ai if a.planet_id == pid]
    active_step = local_waiting[0] if local_waiting else None

    res_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    res_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    sto_pig = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    need_metal = _sum_action_cost(local_waiting, "metal")
    need_pig = _sum_action_cost(local_waiting, "pig_iron")
    have_metal = max(0, res_met - _sum_action_cost(local_ready, "metal")) + _inbound_resource_value(inbound, "metal")
    have_pig = max(0, res_pig - _sum_action_cost(local_ready, "pig_iron")) + _inbound_resource_value(
        inbound, "pig_iron"
    )
    local_metal_gap = max(0, need_metal - have_metal) if local_waiting else 0
    pig_gap = max(0, need_pig - have_pig) if local_waiting else 0
    step_lbl = active_step.target_name if active_step is not None else "next step"

    if local_metal_gap > 0:
        if cur_b2_pct > 0:
            return (
                True,
                active_step,
                local_metal_gap,
                pig_gap,
                f"AI-Planner: {step_lbl} needs {local_metal_gap:,} more Metal on this colony (stay smelting)",
            )
        trigger_rem, trigger_smelt, trigger_lbl = _compute_local_adaptive_trigger(
            local_waiting,
            have_metal=have_metal,
            local_metal_gap=local_metal_gap,
            rate_m_hr=max(1e-6, float(local_b2_metal_per_hour)),
            rem_build_sec=rem_build_sec,
            rem_research_sec=rem_research_sec,
            step_lbl=step_lbl,
        )
        if trigger_rem is not None and trigger_smelt is not None:
            return (
                True,
                active_step,
                local_metal_gap,
                pig_gap,
                f"AI-Planner: {trigger_lbl} needs {local_metal_gap:,} more Metal on this colony "
                f"(queue free in {trigger_rem / 60.0:.0f} min, smelting closes gap in {trigger_smelt / 60.0:.0f} min)",
            )

    empire_reason = _adaptive_empire_arm_reason(
        cur_b2_pct=cur_b2_pct,
        metal_is_full=metal_is_full,
        donor_need=donor_need,
        donor_hold=donor_hold,
        donor_gap=donor_gap,
        empire_pig_tight=empire_pig_tight,
        empire_pig_short=empire_pig_short,
        empire_energy_ok=empire_energy_ok,
        res_pig=res_pig,
        sto_pig=sto_pig,
        local_ready=local_ready,
        need_pig=need_pig,
        local_b2_metal_per_hour=local_b2_metal_per_hour,
    )
    if empire_reason is not None:
        return True, active_step, donor_gap, pig_gap, empire_reason

    return False, active_step, local_metal_gap, pig_gap, None


def _build_adaptive_empire_context(
    state: GameState,
    normal_planets: Sequence[PlanetState],
    waiting_ai: Sequence[PlannedAction],
    ready_ai: Sequence[PlannedAction],
    ref_now: float,
    *,
    inbound_cargo_by_planet: Mapping[int, object] | None,
    in_progress_rem_by_planet: Mapping[int, float] | None,
    in_progress_research_rem_seconds: float | None,
    enable_donor_smelting: bool,
) -> _AdaptiveEmpireContext:
    fb_build_rem, fb_res_rem = _fallback_queue_remaining_seconds(state, ref_now)
    rem_q_map = in_progress_rem_by_planet if in_progress_rem_by_planet is not None else fb_build_rem
    rem_research_sec = (
        max(0.0, _safe_strategy_float(in_progress_research_rem_seconds, 0.0))
        if in_progress_research_rem_seconds is not None
        else fb_res_rem
    )
    inbound_map: Mapping[int, object] = (
        inbound_cargo_by_planet if inbound_cargo_by_planet is not None else _fallback_inbound_cargo_by_planet(state)
    )
    if not enable_donor_smelting:
        return _AdaptiveEmpireContext(
            inbound_map=inbound_map,
            rem_q_map=rem_q_map,
            rem_research_sec=rem_research_sec,
        )

    flying_cargo = [cargo for cargo, _d, _o, _r in _friendly_cargo_flights(state)]

    def _empire_need_and_pool(res_key: str) -> tuple[int, int]:
        need = _sum_action_cost(waiting_ai, res_key)
        ready = _sum_action_cost(ready_ai, res_key)
        stock = sum(max(0, _safe_strategy_int(getattr(p.resources, res_key, 0), 0)) for p in normal_planets)
        flying = sum(_inbound_resource_value(cargo, res_key) for cargo in flying_cargo)
        return need, max(0, stock - ready) + flying

    emp_need_metal, pooled_metal = _empire_need_and_pool("metal")
    donor_gap = max(0, emp_need_metal - pooled_metal)
    emp_need_pig, pooled_pig = _empire_need_and_pool("pig_iron")
    return _AdaptiveEmpireContext(
        inbound_map=inbound_map,
        rem_q_map=rem_q_map,
        rem_research_sec=rem_research_sec,
        donor_need=donor_gap > 0,
        donor_hold=emp_need_metal > 0 and pooled_metal < emp_need_metal * EMPIRE_SMELT_HOLD_RATIO,
        donor_gap=donor_gap,
        empire_pig_short=pooled_pig < emp_need_pig,
        empire_pig_tight=pooled_pig < emp_need_pig * EMPIRE_SMELT_HOLD_RATIO,
    )
