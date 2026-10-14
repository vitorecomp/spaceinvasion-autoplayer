"""Heuristic Blast furnace snapshot, pause decision, and post-throttle bounce helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
import math

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import EnergyBalance, GameState, PlanetState
from spaceinvasion.planner.heuristics._common import (
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._types import (
    _METAL_CONSUMER_KINDS,
    BLAST_FURNACE,
    PIG_IRON_MINE,
    PlannedAction,
)

PreThrottlePlanFn = Callable[..., tuple[list[PlannedAction], list[PlanetState]]]


@dataclass(frozen=True)
class _FurnaceDemandSignals:
    """Priority-sorted colony actions and Pig-iron/Metal demand flags for throttling."""

    p_acts: list[PlannedAction]
    top_needs_pig_only: bool
    top_needs_metal: bool
    any_needs_metal: bool
    top_pig_storage_capped: bool
    metal_starved_with_positive_pig: bool


def _extract_furnace_demand_signals(
    planet: PlanetState,
    existing_plan: Sequence[PlannedAction],
    snap: _FurnaceSnapshot,
) -> _FurnaceDemandSignals:
    prio_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    p_acts = [a for a in existing_plan if a.planet_id == planet.planet_id and a.kind in _METAL_CONSUMER_KINDS]
    p_acts.sort(key=lambda a: prio_order.get(a.priority, 9))
    top_blocker = next(
        (
            a
            for a in p_acts
            if a.kind == "WAIT_RESOURCES"
            or (a.kind == "BUILD_SHIP" and not a.executable_now and not a.missing.is_zero())
        ),
        None,
    )
    top_needs_pig_only = top_blocker is not None and top_blocker.missing.pig_iron > 0 and top_blocker.missing.metal == 0
    top_needs_metal = (
        top_blocker is not None
        and top_blocker.missing.metal > 0
        and (top_blocker.missing.pig_iron == 0 or snap.res_pig >= 2000)
    )
    metal_scope = p_acts if snap.cur_b2_pct > 0 else p_acts[:3]
    any_needs_metal = top_needs_metal or any(a.missing.metal > 0 for a in metal_scope)
    top_pig_storage_capped = bool(
        top_blocker is not None and snap.sto_pig > 0 and top_blocker.cost.pig_iron > snap.sto_pig
    )
    metal_starved_with_positive_pig = bool(
        any_needs_metal and snap.net_pig_at_100 >= 0 and snap.res_pig >= snap.pause_pig_floor
    )
    return _FurnaceDemandSignals(
        p_acts=p_acts,
        top_needs_pig_only=top_needs_pig_only,
        top_needs_metal=top_needs_metal,
        any_needs_metal=any_needs_metal,
        top_pig_storage_capped=top_pig_storage_capped,
        metal_starved_with_positive_pig=metal_starved_with_positive_pig,
    )


@dataclass(frozen=True)
class _FurnaceSnapshot:
    """Computed Blast furnace energy, stock, rate, and threshold snapshot for a colony."""

    cur_b2_pct: int
    cur_b1_pct: int
    e_prod: int
    e_used: int
    e_net: int
    res_pig: int
    res_met: int
    sto_pig: int
    sto_met: int
    metal_full_with_pig_space: bool
    bf_pig_used_full: float
    net_pig_at_100: float
    b2_full_energy: int
    pause_pig_floor: int
    pause_pig_full: int
    metal_cap_pct: float
    warn_ratio: float


def _build_furnace_snapshot(
    state: GameState,
    planet: PlanetState,
    b2_lvl: int,
    *,
    warn_ratio: float,
) -> _FurnaceSnapshot:
    rates = getattr(planet, "economy_rates", None) or {}
    cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
    cur_b1_pct = max(0, min(100, _safe_strategy_int(rates.get("resource1", 100), 100)))

    e_prod = _safe_strategy_int(getattr(planet.energy, "produced", 0), 0)
    e_used = _safe_strategy_int(getattr(planet.energy, "used", 0), 0)
    e_net = _safe_strategy_int(getattr(planet.energy, "net", 0), 0)
    res_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    res_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    sto_pig = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    sto_met = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)

    pig_has_space = sto_pig <= 0 or res_pig * 100 < sto_pig * 99
    metal_is_full = sto_met > 0 and res_met * 100 >= sto_met * 98
    metal_full_with_pig_space = metal_is_full and pig_has_space

    mining_tech = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    speedup = _safe_strategy_float(getattr(state, "speedup_factor", 1.0), 1.0)
    if speedup <= 0:
        speedup = 1.0
    prod_ratio = catalog.safe_big_ratio(e_prod, e_used, cap=1.0) if (e_used > 0 and e_prod < e_used) else 1.0
    factor = speedup * prod_ratio

    p_pig_gross = (
        catalog.mine_production_per_hour(
            PIG_IRON_MINE,
            planet.building_level(PIG_IRON_MINE),
            temperature=_safe_strategy_int(planet.temperature, 0),
            mining_tech_level=mining_tech,
        )
        * factor
        * (max(0, cur_b1_pct) / 100.0)
    )
    bf_pig_used_full = catalog.blast_furnace_pig_iron_used(b2_lvl) * factor
    net_pig_at_100 = p_pig_gross - bf_pig_used_full
    b2_full_energy = abs(
        _safe_strategy_int(catalog.building_level_stats(BLAST_FURNACE, b2_lvl).get("energy", 0) or 0, 0)
    )
    pause_pig_floor = math.ceil(max(500.0, bf_pig_used_full * 0.5)) if math.isfinite(bf_pig_used_full) else 500
    pause_pig_full = math.ceil(bf_pig_used_full) if math.isfinite(bf_pig_used_full) else 0
    metal_cap_pct = catalog.safe_big_ratio(res_met, sto_met, cap=1e200) if sto_met > 0 else 0.0

    return _FurnaceSnapshot(
        cur_b2_pct=cur_b2_pct,
        cur_b1_pct=cur_b1_pct,
        e_prod=e_prod,
        e_used=e_used,
        e_net=e_net,
        res_pig=res_pig,
        res_met=res_met,
        sto_pig=sto_pig,
        sto_met=sto_met,
        metal_full_with_pig_space=metal_full_with_pig_space,
        bf_pig_used_full=bf_pig_used_full,
        net_pig_at_100=net_pig_at_100,
        b2_full_energy=b2_full_energy,
        pause_pig_floor=pause_pig_floor,
        pause_pig_full=pause_pig_full,
        metal_cap_pct=metal_cap_pct,
        warn_ratio=warn_ratio,
    )


def _plan_active_furnace_pause(
    planet: PlanetState,
    snap: _FurnaceSnapshot,
    *,
    p1_metal_need: int,
    top_needs_pig_only: bool,
    top_pig_storage_capped: bool,
    metal_starved_with_positive_pig: bool,
    any_needs_metal: bool,
) -> PlannedAction | None:
    """Decide whether an active (`> 0%`) Blast furnace should pause to `0%`."""
    coords = _safe_strategy_str(planet.coords)
    if snap.metal_full_with_pig_space:
        return PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=planet.planet_id,
            planet_coords=coords,
            target_id=BLAST_FURNACE,
            target_name="Blast furnace 0%",
            target_level=0,
            reason=(
                f"Metal storage full ({snap.metal_cap_pct * 100:.0f}%) while Pig iron has free capacity — "
                f"disable Blast furnace (0%) to avoid throwing Metal away and save "
                f"+{snap.bf_pig_used_full:.0f} Pig iron/h"
            ),
            executable_now=True,
        )
    if p1_metal_need > 0:
        return None
    if (top_needs_pig_only and not top_pig_storage_capped and not metal_starved_with_positive_pig) or (
        not any_needs_metal and snap.net_pig_at_100 < 0 and snap.res_pig < snap.pause_pig_floor
    ):
        return PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=planet.planet_id,
            planet_coords=coords,
            target_id=BLAST_FURNACE,
            target_name="Blast furnace 0%",
            target_level=0,
            reason=(
                f"Pause Blast furnace (currently {snap.cur_b2_pct}%) to save "
                f"+{snap.bf_pig_used_full:.0f} Pig iron/h while Pig iron is the bottleneck"
            ),
            executable_now=True,
        )
    if not any_needs_metal and (
        snap.metal_cap_pct >= snap.warn_ratio or (snap.net_pig_at_100 < 0 and snap.res_pig < snap.pause_pig_full)
    ):
        return PlannedAction(
            priority="P2",
            kind="THROTTLE_BUILDING",
            planet_id=planet.planet_id,
            planet_coords=coords,
            target_id=BLAST_FURNACE,
            target_name="Blast furnace 0%",
            target_level=0,
            reason=(
                f"Blast furnace drains {snap.bf_pig_used_full:.0f} Pig iron/h; "
                "pause to 0% to prioritize Pig iron stockpiling"
            ),
            executable_now=True,
        )
    return None


def _run_pre_throttle_plan(
    post_state: GameState,
    *,
    warn_ratio: float,
    cfg: StrategyConfig,
    pre_throttle_plan_fn: PreThrottlePlanFn | None,
) -> list[PlannedAction] | None:
    """Invoke explicit or static `_build_pre_throttle_plan` without cyclic imports."""
    plan_fn = pre_throttle_plan_fn
    if plan_fn is None:
        from spaceinvasion.planner.heuristics._assembly_plan import _build_pre_throttle_plan

        plan_fn = _build_pre_throttle_plan
    post_plan, _ = plan_fn(
        post_state,
        warn_ratio=warn_ratio,
        cfg=cfg,
        include_combat=False,
    )
    return post_plan


def _build_post_throttle_state(
    state: GameState,
    planet: PlanetState,
    snap: _FurnaceSnapshot,
    candidate: PlannedAction,
) -> tuple[GameState, PlanetState]:
    new_pct = _safe_strategy_int(candidate.target_level, 0)
    delta_e = int(round(snap.b2_full_energy * (new_pct - snap.cur_b2_pct) / 100.0))
    new_rates = dict(getattr(planet, "economy_rates", None) or {})
    new_rates["resource2"] = new_pct
    post_planet = replace(
        planet,
        economy_rates=new_rates,
        energy=EnergyBalance(snap.e_prod, max(0, snap.e_used + delta_e)),
    )
    post_state = replace(
        state,
        planets=tuple(post_planet if p.planet_id == planet.planet_id else p for p in state.planets),
    )
    return post_state, post_planet
