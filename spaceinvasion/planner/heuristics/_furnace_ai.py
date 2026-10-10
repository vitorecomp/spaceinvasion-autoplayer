"""AI-Planner binary (`100%` / `0%`) Blast furnace pool, overflow, and per-colony dispatch helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math

from spaceinvasion.core import catalog
from spaceinvasion.core.state import GameState, PlanetState
from spaceinvasion.planner.heuristics._common import (
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._furnace_ai_adaptive import (
    _adaptive_colony_smelt_decision,
    _AdaptiveEmpireContext,
    _sum_action_cost,
)
from spaceinvasion.planner.heuristics._types import (
    AI_THROTTLE_COOLDOWN_SECONDS,
    BLAST_FURNACE,
    METAL_OVERFLOW_RESUME_PCT,
    PlannedAction,
)


@dataclass(frozen=True)
class _EmpirePoolContext:
    """Pooled empire smelting intent for non-adaptive multi-planet transport mode."""

    reserved_step: PlannedAction | None = None
    reserved_metal_gap: int = 0
    reserved_pig_gap: int = 0
    empire_wants_smelt: bool = False


def _build_empire_pool_context(
    state: GameState,
    normal_planets: Sequence[PlanetState],
    ready_ai: Sequence[PlannedAction],
    reserved_step: PlannedAction | None,
) -> _EmpirePoolContext:
    empire_metal_stock = sum(max(0, _safe_strategy_int(getattr(p.resources, "metal", 0), 0)) for p in normal_planets)
    empire_pig_stock = sum(max(0, _safe_strategy_int(getattr(p.resources, "pig_iron", 0), 0)) for p in normal_planets)
    for fq in getattr(state, "active_queues", None) or ():
        if _safe_strategy_str(getattr(fq, "category", "")).lower() != "fleet":
            continue
        cargo = getattr(fq, "cargo", None)
        if cargo is None:
            continue
        fq_txt = (
            f"{_safe_strategy_str(getattr(fq, 'name', ''))} {_safe_strategy_str(getattr(fq, 'detail', ''))}"
        ).lower()
        if "hostile" in fq_txt or "enemy" in fq_txt:
            continue
        if any(tok in fq_txt for tok in ("transport", "station", "return")):
            empire_metal_stock += max(0, _safe_strategy_int(getattr(cargo, "metal", 0), 0))
            empire_pig_stock += max(0, _safe_strategy_int(getattr(cargo, "pig_iron", 0), 0))

    avail_empire_metal = max(0, empire_metal_stock - _sum_action_cost(ready_ai, "metal"))
    avail_empire_pig = max(0, empire_pig_stock - _sum_action_cost(ready_ai, "pig_iron"))
    if reserved_step is None:
        return _EmpirePoolContext()
    reserved_metal_gap = max(
        0, max(0, _safe_strategy_int(getattr(reserved_step.cost, "metal", 0), 0)) - avail_empire_metal
    )
    reserved_pig_gap = max(
        0, max(0, _safe_strategy_int(getattr(reserved_step.cost, "pig_iron", 0), 0)) - avail_empire_pig
    )
    return _EmpirePoolContext(
        reserved_step=reserved_step,
        reserved_metal_gap=reserved_metal_gap,
        reserved_pig_gap=reserved_pig_gap,
        empire_wants_smelt=reserved_metal_gap > 0,
    )


def _resolve_colony_smelt_intent(
    planet: PlanetState,
    *,
    adaptive: bool,
    use_empire_pool: bool,
    empire_ctx: _EmpirePoolContext,
    adaptive_ctx: _AdaptiveEmpireContext | None,
    waiting_ai: Sequence[PlannedAction],
    ready_ai: Sequence[PlannedAction],
    res_met: int,
    res_pig: int,
    e_prod: int,
    e_used: int,
    b2_lvl: int,
    cur_b2_pct: int,
    metal_is_full: bool,
    bf_pig_used_full: float,
) -> tuple[bool, PlannedAction | None, int, int, str | None, str]:
    if adaptive and adaptive_ctx is not None:
        extra_energy = int(math.ceil(catalog.mine_energy_needed(BLAST_FURNACE, b2_lvl) * (100 - cur_b2_pct) / 100.0))
        empire_energy_ok = (e_prod <= 0 and e_used <= 0) or (e_prod - e_used) >= extra_energy
        want_smelt, active_step, metal_gap, pig_gap, on_reason = _adaptive_colony_smelt_decision(
            planet,
            waiting_ai=waiting_ai,
            ready_ai=ready_ai,
            inbound=adaptive_ctx.inbound_map.get(planet.planet_id),
            rem_build_sec=_safe_strategy_float(adaptive_ctx.rem_q_map.get(planet.planet_id, 0.0) or 0.0, 0.0),
            rem_research_sec=adaptive_ctx.rem_research_sec,
            local_b2_metal_per_hour=bf_pig_used_full / 2.0,
            cur_b2_pct=cur_b2_pct,
            metal_is_full=metal_is_full,
            donor_need=adaptive_ctx.donor_need,
            donor_gap=adaptive_ctx.donor_gap,
            donor_hold=adaptive_ctx.donor_hold,
            empire_pig_tight=adaptive_ctx.empire_pig_tight,
            empire_pig_short=adaptive_ctx.empire_pig_short,
            empire_energy_ok=empire_energy_ok,
        )
        return want_smelt, active_step, metal_gap, pig_gap, on_reason, ""

    if use_empire_pool:
        return (
            empire_ctx.empire_wants_smelt,
            empire_ctx.reserved_step,
            empire_ctx.reserved_metal_gap,
            empire_ctx.reserved_pig_gap,
            None,
            "empire ",
        )

    local_waiting_ai = [a for a in waiting_ai if a.planet_id == planet.planet_id]
    local_ready_ai = [a for a in ready_ai if a.planet_id == planet.planet_id]
    active_step = local_waiting_ai[0] if local_waiting_ai else None
    if active_step is None:
        return False, None, 0, 0, None, ""
    avail_local_metal = max(0, res_met - _sum_action_cost(local_ready_ai, "metal"))
    avail_local_pig = max(0, res_pig - _sum_action_cost(local_ready_ai, "pig_iron"))
    metal_gap = max(0, max(0, _safe_strategy_int(getattr(active_step.cost, "metal", 0), 0)) - avail_local_metal)
    pig_gap = max(0, max(0, _safe_strategy_int(getattr(active_step.cost, "pig_iron", 0), 0)) - avail_local_pig)
    return metal_gap > 0, active_step, metal_gap, pig_gap, None, ""


def _resolve_overflow_throttle(
    planet: PlanetState,
    *,
    cur_b2_pct: int,
    was_overflow_paused: bool,
    metal_full_with_pig_space: bool,
    metal_has_resume_space: bool,
    metal_cap_pct: float,
    bf_pig_used_full: float,
    want_smelt: bool,
    step_lbl: str,
    metal_gap: int,
    scope_lbl: str,
    cleared_overflow_pids: set[int] | None,
) -> tuple[bool, PlannedAction | None]:
    """Handle Metal >= 100% overflow pause & `< 95%` resume hysteresis; returns `(handled, action)`."""
    coords = _safe_strategy_str(planet.coords)
    if metal_full_with_pig_space:
        if cur_b2_pct > 0:
            return True, PlannedAction(
                priority="P1",
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=coords,
                target_id=BLAST_FURNACE,
                target_name="Blast furnace 0%",
                target_level=0,
                reason=(
                    f"Metal storage full ({metal_cap_pct * 100:.0f}%) while Pig iron has free capacity — "
                    f"pause Blast furnace ({cur_b2_pct}% -> 0%) until Metal drops below "
                    f"{METAL_OVERFLOW_RESUME_PCT}% (saves +{bf_pig_used_full:.0f} Pig iron/h)"
                ),
                executable_now=True,
            )
        return True, None

    if not was_overflow_paused:
        return False, None
    if cur_b2_pct > 0:
        if cleared_overflow_pids is not None:
            cleared_overflow_pids.add(planet.planet_id)
        return False, None
    if not metal_has_resume_space:
        return True, None
    if want_smelt:
        return True, PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=planet.planet_id,
            planet_coords=coords,
            target_id=BLAST_FURNACE,
            target_name="Blast furnace 100%",
            target_level=100,
            reason=(
                f"Metal overflow cleared ({metal_cap_pct * 100:.0f}% < {METAL_OVERFLOW_RESUME_PCT}%) — "
                f"auto-resume Blast furnace (0% -> 100%) for AI-Planner "
                f"({step_lbl} needs {metal_gap:,} more {scope_lbl}Metal)"
            ),
            executable_now=True,
        )
    if cleared_overflow_pids is not None:
        cleared_overflow_pids.add(planet.planet_id)
    return True, None


def _emit_binary_ai_throttle(
    planet: PlanetState,
    *,
    cur_b2_pct: int,
    metal_is_full: bool,
    bf_pig_used_full: float,
    want_smelt: bool,
    active_step: PlannedAction | None,
    step_lbl: str,
    metal_gap: int,
    pig_gap: int,
    on_reason: str | None,
    scope_lbl: str,
) -> PlannedAction | None:
    """Emit strict binary (`100%` / `0%`) AI-Planner Blast furnace throttle when state differs."""
    coords = _safe_strategy_str(planet.coords)
    if want_smelt:
        if metal_is_full or cur_b2_pct >= 100:
            return None
        verb = "enable" if cur_b2_pct == 0 else "set"
        reason = (
            f"{on_reason} — {verb} Blast furnace ({cur_b2_pct}% -> 100%)"
            if on_reason
            else (
                f"AI-Planner: {step_lbl} needs {metal_gap:,} more {scope_lbl}Metal — "
                f"{verb} Blast furnace ({cur_b2_pct}% -> 100%)"
            )
        )
        return PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=planet.planet_id,
            planet_coords=coords,
            target_id=BLAST_FURNACE,
            target_name="Blast furnace 100%",
            target_level=100,
            reason=reason,
            executable_now=True,
        )

    if cur_b2_pct == 0:
        return None
    if active_step is not None:
        if pig_gap > 0:
            prio = "P1"
            reason = (
                f"AI-Planner: {active_step.target_name} already has enough {scope_lbl}Metal and "
                f"needs {pig_gap:,} more Pig iron — disable Blast furnace "
                f"({cur_b2_pct}% -> 0%, saves +{bf_pig_used_full:.0f} Pig iron/h)"
            )
        else:
            prio = "P2"
            reason = (
                f"AI-Planner: {active_step.target_name} already has enough {scope_lbl}Metal — "
                f"disable Blast furnace ({cur_b2_pct}% -> 0%, saves +{bf_pig_used_full:.0f} Pig iron/h)"
            )
    else:
        prio = "P2"
        reason = (
            f"AI-Planner: no waiting step requires Metal (idle) — disable Blast furnace "
            f"({cur_b2_pct}% -> 0%, saves +{bf_pig_used_full:.0f} Pig iron/h)"
        )
    return PlannedAction(
        priority=prio,
        kind="THROTTLE_BUILDING",
        planet_id=planet.planet_id,
        planet_coords=coords,
        target_id=BLAST_FURNACE,
        target_name="Blast furnace 0%",
        target_level=0,
        reason=reason,
        executable_now=True,
    )


def _plan_single_colony_ai_throttle(
    planet: PlanetState,
    *,
    speedup: float,
    ref_now: float,
    cooldown_map: Mapping[int, float],
    overflow_paused_set: set[int],
    cleared_overflow_pids: set[int] | None,
    adaptive: bool,
    use_empire_pool: bool,
    empire_ctx: _EmpirePoolContext,
    adaptive_ctx: _AdaptiveEmpireContext | None,
    waiting_ai: Sequence[PlannedAction],
    ready_ai: Sequence[PlannedAction],
) -> PlannedAction | None:
    b2_lvl = planet.building_level(BLAST_FURNACE)
    was_overflow_paused = planet.planet_id in overflow_paused_set
    if b2_lvl <= 0:
        if was_overflow_paused and cleared_overflow_pids is not None:
            cleared_overflow_pids.add(planet.planet_id)
        return None

    rates = getattr(planet, "economy_rates", None) or {}
    cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
    e_prod = _safe_strategy_int(getattr(planet.energy, "produced", 0), 0)
    e_used = _safe_strategy_int(getattr(planet.energy, "used", 0), 0)
    res_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    res_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    sto_pig = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    sto_met = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)

    metal_is_full = sto_met > 0 and res_met >= sto_met
    metal_full_with_pig_space = metal_is_full and (sto_pig <= 0 or res_pig < sto_pig)
    metal_has_resume_space = sto_met <= 0 or res_met * 100 < sto_met * METAL_OVERFLOW_RESUME_PCT
    metal_cap_pct = catalog.safe_big_ratio(res_met, sto_met, cap=1e200) if sto_met > 0 else 0.0
    prod_ratio = catalog.safe_big_ratio(e_prod, e_used, cap=1.0) if (e_used > 0 and e_prod < e_used) else 1.0
    bf_pig_used_full = catalog.blast_furnace_pig_iron_used(b2_lvl) * speedup * prod_ratio

    want_smelt, active_step, metal_gap, pig_gap, on_reason, scope_lbl = _resolve_colony_smelt_intent(
        planet,
        adaptive=adaptive,
        use_empire_pool=use_empire_pool,
        empire_ctx=empire_ctx,
        adaptive_ctx=adaptive_ctx,
        waiting_ai=waiting_ai,
        ready_ai=ready_ai,
        res_met=res_met,
        res_pig=res_pig,
        e_prod=e_prod,
        e_used=e_used,
        b2_lvl=b2_lvl,
        cur_b2_pct=cur_b2_pct,
        metal_is_full=metal_is_full,
        bf_pig_used_full=bf_pig_used_full,
    )
    step_lbl = active_step.target_name if active_step is not None else "next step"

    handled, overflow_act = _resolve_overflow_throttle(
        planet,
        cur_b2_pct=cur_b2_pct,
        was_overflow_paused=was_overflow_paused,
        metal_full_with_pig_space=metal_full_with_pig_space,
        metal_has_resume_space=metal_has_resume_space,
        metal_cap_pct=metal_cap_pct,
        bf_pig_used_full=bf_pig_used_full,
        want_smelt=want_smelt,
        step_lbl=step_lbl,
        metal_gap=metal_gap,
        scope_lbl=scope_lbl,
        cleared_overflow_pids=cleared_overflow_pids,
    )
    if handled:
        return overflow_act

    raw_last = cooldown_map.get(planet.planet_id)
    last_t_val = _safe_strategy_float(raw_last, -1.0) if raw_last is not None else None
    if last_t_val is not None and last_t_val >= 0.0 and (ref_now - last_t_val) < AI_THROTTLE_COOLDOWN_SECONDS:
        return None

    return _emit_binary_ai_throttle(
        planet,
        cur_b2_pct=cur_b2_pct,
        metal_is_full=metal_is_full,
        bf_pig_used_full=bf_pig_used_full,
        want_smelt=want_smelt,
        active_step=active_step,
        step_lbl=step_lbl,
        metal_gap=metal_gap,
        pig_gap=pig_gap,
        on_reason=on_reason,
        scope_lbl=scope_lbl,
    )
