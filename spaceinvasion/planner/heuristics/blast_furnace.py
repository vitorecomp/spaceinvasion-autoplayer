"""Blast furnace (`b2`) heuristic and AI-Planner throttling policies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import time

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState, PlanetState
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _resolve_storage_warn_ratio,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._furnace_ai import (
    _build_empire_pool_context,
    _plan_single_colony_ai_throttle,
)
from spaceinvasion.planner.heuristics._furnace_ai_adaptive import (
    _adaptive_colony_smelt_decision,
    _build_adaptive_empire_context,
    _fallback_queue_remaining_seconds,
)
from spaceinvasion.planner.heuristics._furnace_heuristic import (
    PreThrottlePlanFn,
    _build_furnace_snapshot,
    _build_post_throttle_state,
    _extract_furnace_demand_signals,
    _plan_active_furnace_pause,
    _run_pre_throttle_plan,
)
from spaceinvasion.planner.heuristics._furnace_resume import (
    _compute_p1_resume_pct,
    _plan_non_p1_furnace_resume,
)
from spaceinvasion.planner.heuristics._types import (
    _METAL_CONSUMER_KINDS,
    AI_THROTTLE_COOLDOWN_SECONDS,
    BLAST_FURNACE,
    EMPIRE_SMELT_HOLD_RATIO,
    EMPIRE_SMELT_MIN_SPARE_PIG,
    FUSION_PLANT,
    METAL_OVERFLOW_RESUME_PCT,
    SOLAR_CELLS_ID,
    THERMOSOLAR_PLANT,
    PlannedAction,
)

METAL_OVERFLOW_PAUSE_PCT: int = 100

__all__ = [
    "AI_THROTTLE_COOLDOWN_SECONDS",
    "EMPIRE_SMELT_HOLD_RATIO",
    "EMPIRE_SMELT_MIN_SPARE_PIG",
    "METAL_OVERFLOW_PAUSE_PCT",
    "METAL_OVERFLOW_RESUME_PCT",
    "_adaptive_colony_smelt_decision",
    "_fallback_queue_remaining_seconds",
    "_plan_blast_furnace_throttle",
    "p1_metal_shortfall",
    "plan_ai_blast_furnace_throttles",
    "plan_p1_metal_resume_throttle",
]


def p1_metal_shortfall(
    planet: PlanetState,
    actions: Sequence[PlannedAction],
) -> tuple[int, PlannedAction | None]:
    """Largest Metal shortfall among `P0`/`P1` resource-consuming actions on `planet`."""
    sto_met = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)
    best_miss = 0
    best_act: PlannedAction | None = None
    for act in actions:
        if act.planet_id != planet.planet_id or act.priority not in ("P0", "P1"):
            continue
        if act.kind not in _METAL_CONSUMER_KINDS:
            continue
        miss_m = _safe_strategy_int(getattr(act.missing, "metal", 0), 0)
        if miss_m <= 0:
            continue
        rsn_low = _safe_strategy_str(act.reason).lower()
        if (
            "[unmet prereqs:" in rsn_low
            or "(requires research lab" in rsn_low
            or "(requires weapons factory" in rsn_low
        ):
            continue
        if sto_met > 0 and _safe_strategy_int(getattr(act.cost, "metal", 0), 0) > sto_met:
            continue
        if miss_m > best_miss:
            best_miss = miss_m
            best_act = act
    return best_miss, best_act


def plan_p1_metal_resume_throttle(
    state: GameState,
    planet: PlanetState,
    p1_metal_need: int,
    p1_metal_act: PlannedAction,
) -> PlannedAction | None:
    """Resume a paused (`0%`) Blast furnace at the highest energy-safe (and Pig-iron-sustainable) % for a P0/P1 item."""
    if _norm_planet_type(planet) in ("asteroid", "raid") or p1_metal_need <= 0:
        return None
    b2_lvl = planet.building_level(BLAST_FURNACE)
    if b2_lvl <= 0:
        return None
    rates = getattr(planet, "economy_rates", None) or {}
    cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
    if cur_b2_pct > 0:
        return None
    cur_b1_pct = max(0, min(100, _safe_strategy_int(rates.get("resource1", 100), 100)))
    res_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    res_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    sto_pig = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    sto_met = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)
    if (sto_met > 0 and res_met * 100 >= sto_met * 98) and (sto_pig <= 0 or res_pig * 100 < sto_pig * 99):
        return None

    e_prod = _safe_strategy_int(getattr(planet.energy, "produced", 0), 0)
    e_used = _safe_strategy_int(getattr(planet.energy, "used", 0), 0)
    e_net = _safe_strategy_int(getattr(planet.energy, "net", 0), 0)
    is_power_p1 = p1_metal_act.kind in ("BUILD", "WAIT_RESOURCES", "BUILD_SHIP") and p1_metal_act.target_id in (
        FUSION_PLANT,
        THERMOSOLAR_PLANT,
        SOLAR_CELLS_ID,
    )
    if e_net < 0 and not is_power_p1:
        return None

    target_pct, pct_notes = _compute_p1_resume_pct(
        state,
        planet,
        b2_lvl,
        cur_b1_pct,
        p1_metal_need,
        e_prod=e_prod,
        e_used=e_used,
        e_net=e_net,
        res_pig=res_pig,
    )
    note_s = f" ({', '.join(pct_notes)})" if pct_notes else ""
    return PlannedAction(
        priority="P1",
        kind="THROTTLE_BUILDING",
        planet_id=planet.planet_id,
        planet_coords=_safe_strategy_str(planet.coords),
        target_id=BLAST_FURNACE,
        target_name=f"Blast furnace {target_pct}%",
        target_level=target_pct,
        reason=(
            f"P1 {p1_metal_act.target_name} needs {p1_metal_need:,} more Metal: "
            f"resume Blast furnace to {target_pct}%{note_s}"
        ),
        executable_now=True,
    )


def _plan_blast_furnace_throttle(
    state: GameState,
    planet: PlanetState,
    existing_plan: list[PlannedAction],
    cfg: StrategyConfig,
    *,
    last_throttle_ts: float | None = None,
    now_ts: float | None = None,
    storage_warn_ratio: float | None = None,
    _check_bounce: bool = True,
    pre_throttle_plan_fn: PreThrottlePlanFn | None = None,
) -> PlannedAction | None:
    """Pause Blast furnace (`0%`) to stop 2:1 Pig iron drain when Iron-starved, or resume (`100%`)."""
    if _norm_planet_type(planet) in ("asteroid", "raid"):
        return None
    b2_lvl = planet.building_level(BLAST_FURNACE)
    if b2_lvl <= 0:
        return None

    warn_ratio = _resolve_storage_warn_ratio(cfg, storage_warn_ratio)
    snap = _build_furnace_snapshot(state, planet, b2_lvl, warn_ratio=warn_ratio)
    ref_now = _safe_strategy_float(now_ts, time.time()) if now_ts is not None else time.time()
    last_t_val = _safe_strategy_float(last_throttle_ts, -1.0) if last_throttle_ts is not None else None
    if last_t_val is not None and last_t_val >= 0.0 and (ref_now - last_t_val) < 300.0:
        if not ((snap.e_net < 0 or snap.metal_full_with_pig_space) and snap.cur_b2_pct > 0):
            return None

    sig = _extract_furnace_demand_signals(planet, existing_plan, snap)
    p1_metal_need, p1_metal_act = p1_metal_shortfall(planet, sig.p_acts)
    candidate: PlannedAction | None = None
    p1_resume_override = False

    if snap.cur_b2_pct > 0:
        candidate = _plan_active_furnace_pause(
            planet,
            snap,
            p1_metal_need=p1_metal_need,
            top_needs_pig_only=sig.top_needs_pig_only,
            top_pig_storage_capped=sig.top_pig_storage_capped,
            metal_starved_with_positive_pig=sig.metal_starved_with_positive_pig,
            any_needs_metal=sig.any_needs_metal,
        )
    elif not snap.metal_full_with_pig_space:
        if p1_metal_need > 0 and p1_metal_act is not None:
            candidate = plan_p1_metal_resume_throttle(state, planet, p1_metal_need, p1_metal_act)
            p1_resume_override = candidate is not None
        else:
            candidate = _plan_non_p1_furnace_resume(
                state,
                planet,
                cfg,
                snap,
                sig.p_acts,
                top_needs_pig_only=sig.top_needs_pig_only,
                top_needs_metal=sig.top_needs_metal,
                any_needs_metal=sig.any_needs_metal,
                metal_starved_with_positive_pig=sig.metal_starved_with_positive_pig,
            )

    if candidate is not None and _check_bounce and not p1_resume_override:
        post_state, post_planet = _build_post_throttle_state(state, planet, snap, candidate)
        post_plan = _run_pre_throttle_plan(
            post_state, warn_ratio=warn_ratio, cfg=cfg, pre_throttle_plan_fn=pre_throttle_plan_fn
        )
        if post_plan is not None:
            bounce = _plan_blast_furnace_throttle(
                post_state,
                post_planet,
                post_plan,
                cfg,
                last_throttle_ts=None,
                now_ts=now_ts,
                storage_warn_ratio=warn_ratio,
                _check_bounce=False,
                pre_throttle_plan_fn=pre_throttle_plan_fn,
            )
            if bounce is not None:
                return None

    return candidate


def plan_ai_blast_furnace_throttles(
    state: GameState,
    ai_actions: Sequence[PlannedAction],
    fleet_actions: Sequence[PlannedAction],
    cfg: StrategyConfig,
    *,
    throttle_cooldowns: Mapping[int, float] | None = None,
    now_ts: float | None = None,
    storage_warn_ratio: float | None = None,
    metal_overflow_paused: Iterable[int] | None = None,
    cleared_overflow_pids: set[int] | None = None,
    inbound_cargo_by_planet: Mapping[int, object] | None = None,
    in_progress_rem_by_planet: Mapping[int, float] | None = None,
    in_progress_research_rem_seconds: float | None = None,
    enable_donor_smelting: bool = False,
) -> list[PlannedAction]:
    """Strict binary (`100%` / `0%`) Blast furnace (`b2`) control for active `ai_planner` mode."""
    _ = (fleet_actions, storage_warn_ratio)
    adaptive = bool(getattr(cfg, "ai_adaptive_execution", False))
    if not cfg.auto_throttle_blast_furnace or cfg.merchant_mode:
        return []
    active = state.active_planet()
    if active is None:
        return []

    normal_planets = [p for p in (state.planets or ()) if _norm_planet_type(p) == "normal"]
    target_planets = (
        normal_planets if cfg.allow_multi_planet else ([active] if _norm_planet_type(active) == "normal" else [])
    )
    if not target_planets:
        return []

    ref_now = _safe_strategy_float(now_ts, time.time()) if now_ts is not None else time.time()
    cooldown_map = throttle_cooldowns or {}
    overflow_paused_set = {
        pid_v for raw_pid in (metal_overflow_paused or ()) if (pid_v := _safe_strategy_int(raw_pid, 0)) > 0
    }
    if cleared_overflow_pids is not None:
        cleared_overflow_pids.update(overflow_paused_set - {p.planet_id for p in target_planets})

    speedup = _safe_strategy_float(getattr(state, "speedup_factor", 1.0), 1.0)
    if speedup <= 0:
        speedup = 1.0

    ai_steps = [a for a in ai_actions if a.kind in ("BUILD", "WAIT_RESOURCES", "RESEARCH")]
    ready_ai = [a for a in ai_steps if a.executable_now]
    waiting_ai = [a for a in ai_steps if not a.executable_now and not a.missing.is_zero()]
    reserved_step = waiting_ai[0] if waiting_ai else None

    use_empire_pool = bool(cfg.allow_transport and cfg.allow_multi_planet) and not adaptive
    empire_ctx = (
        _build_empire_pool_context(state, normal_planets, ready_ai, reserved_step)
        if use_empire_pool
        else _build_empire_pool_context(state, (), (), None)
    )
    adaptive_ctx = (
        _build_adaptive_empire_context(
            state,
            normal_planets,
            waiting_ai,
            ready_ai,
            ref_now,
            inbound_cargo_by_planet=inbound_cargo_by_planet,
            in_progress_rem_by_planet=in_progress_rem_by_planet,
            in_progress_research_rem_seconds=in_progress_research_rem_seconds,
            enable_donor_smelting=enable_donor_smelting,
        )
        if adaptive
        else None
    )

    out: list[PlannedAction] = []
    for planet in target_planets:
        act = _plan_single_colony_ai_throttle(
            planet,
            speedup=speedup,
            ref_now=ref_now,
            cooldown_map=cooldown_map,
            overflow_paused_set=overflow_paused_set,
            cleared_overflow_pids=cleared_overflow_pids,
            adaptive=adaptive,
            use_empire_pool=use_empire_pool,
            empire_ctx=empire_ctx,
            adaptive_ctx=adaptive_ctx,
            waiting_ai=waiting_ai,
            ready_ai=ready_ai,
        )
        if act is not None:
            out.append(act)
    return out
