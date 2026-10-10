"""Paused Blast furnace (`0%`) resume lookahead and P1 sustainable-rate helpers."""

from __future__ import annotations

from collections.abc import Sequence
import math

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState
from spaceinvasion.planner.heuristics._actions import _pick_best_power_building
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _offer_map,
    _planet_has_free_building_fields,
    _resources_from_dict,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
    _ship_offer_map,
)
from spaceinvasion.planner.heuristics._furnace_heuristic import _FurnaceSnapshot
from spaceinvasion.planner.heuristics._queues import (
    _count_planet_building_queues,
    _eff_level,
    _effective_building_levels_and_energy,
    _energy_starvation_delta,
    _max_build_slots,
)
from spaceinvasion.planner.heuristics._types import (
    BLAST_FURNACE,
    FUSION_PLANT,
    PIG_IRON_MINE,
    SOLAR_CELLS_ID,
    THERMOSOLAR_PLANT,
    PlannedAction,
)


def _compute_p1_resume_pct(
    state: GameState,
    planet: PlanetState,
    b2_lvl: int,
    cur_b1_pct: int,
    p1_metal_need: int,
    *,
    e_prod: int,
    e_used: int,
    e_net: int,
    res_pig: int,
) -> tuple[int, list[str]]:
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

    target_pct = 100
    pct_notes: list[str] = []
    if b2_full_energy > 0 and e_net < b2_full_energy:
        energy_pct = (max(0, e_net) * 100 // b2_full_energy) // 10 * 10
        target_pct = max(10, min(100, int(energy_pct)))
        pct_notes.append(f"energy-limited, net +{max(0, e_net)}")
    pig_for_metal = 2 * p1_metal_need
    if (
        net_pig_at_100 < 0
        and res_pig < pig_for_metal + pause_pig_floor
        and math.isfinite(bf_pig_used_full)
        and bf_pig_used_full > 0
    ):
        sust_pct = 10
        for cand_pct in (100, 90, 80, 70, 60, 50, 40, 30, 20, 10):
            if bf_pig_used_full * (cand_pct / 100.0) <= p_pig_gross * 0.85:
                sust_pct = cand_pct
                break
        if sust_pct < target_pct:
            target_pct = sust_pct
            pct_notes.append("Pig-iron-sustainable")
    return target_pct, pct_notes


def _would_resume_trigger_p1_power(
    planet: PlanetState,
    p_acts: Sequence[PlannedAction],
    eff_buildings: dict[str, int],
    projected_net_if_resumed: int,
) -> tuple[bool, bool]:
    has_p1_power = any(
        a.priority == "P1"
        and a.kind in ("BUILD", "WAIT_RESOURCES", "BUILD_SHIP")
        and a.target_id in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID)
        for a in p_acts
    )
    if has_p1_power:
        return True, True
    for act in p_acts:
        tid = catalog.safe_optional_int(act.target_id, None)
        if tid is None or tid in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID):
            continue
        if act.kind not in ("BUILD", "WAIT_RESOURCES"):
            continue
        nxt_l = _safe_strategy_int(act.target_level, 0) or (_eff_level(planet, eff_buildings, tid) + 1)
        if _energy_starvation_delta(tid, nxt_l, projected_net_if_resumed) < 0:
            return False, True
    return False, False


def _resume_blocked_by_power_need(
    state: GameState,
    planet: PlanetState,
    cfg: StrategyConfig,
    eff_buildings: dict[str, int],
    *,
    is_active: bool,
    has_p1_power: bool,
    metal_starved_with_positive_pig: bool,
) -> bool:
    queue_full = _count_planet_building_queues(state, planet, is_active_planet=is_active) >= _max_build_slots(
        planet, state
    )
    allow_remote = cfg.allow_multi_planet and not is_active
    forbid_solar_bf = bool(getattr(cfg, "forbid_planetary_solar", False))
    solar_offer = _ship_offer_map(planet).get(SOLAR_CELLS_ID)
    f_rem_bf = _safe_strategy_int(planet.fields_remaining, 0)
    hot_tight_bf = (
        not forbid_solar_bf
        and _norm_planet_type(planet) == "normal"
        and _safe_strategy_int(planet.fields_max, 0) > 0
        and 0 < f_rem_bf <= _safe_strategy_int(cfg.min_free_fields_warning, 20)
        and _safe_strategy_int(planet.temperature, 0) >= _safe_strategy_int(cfg.hot_planet_solar_temp_threshold, 40)
    )
    use_solar_cells = (
        (
            _norm_planet_type(planet) == "asteroid"
            or (not forbid_solar_bf and (not _planet_has_free_building_fields(planet) or hot_tight_bf))
        )
        and solar_offer is not None
        and solar_offer.can_build
        and cfg.allow_shipyard
    )
    if use_solar_cells and solar_offer is not None:
        miss_pw = planet.resources.missing(solar_offer.cost)
        can_exec_power = planet.resources.can_afford(solar_offer.cost)
    else:
        power_bid, _, _ = _pick_best_power_building(
            planet,
            eff_buildings,
            state.researches,
            forbid_planetary_solar=forbid_solar_bf,
        )
        power_lvl = _eff_level(planet, eff_buildings, power_bid) + 1
        power_cost = _resources_from_dict(catalog.building_cost(power_bid, power_lvl))
        power_offer = _offer_map(planet).get(power_bid)
        if power_offer is not None:
            miss_pw = planet.resources.missing(power_offer.cost)
            can_exec_power = (
                (not queue_full) and power_offer.can_build and planet.resources.can_afford(power_offer.cost)
            )
        else:
            miss_pw = planet.resources.missing(power_cost)
            prereq_ok = not catalog.missing_prerequisites(
                "buildings",
                power_bid,
                buildings=planet.buildings,
                researches=state.researches,
            )
            can_exec_power = (
                (not queue_full)
                and allow_remote
                and (not power_cost.is_zero())
                and planet.resources.can_afford(power_cost)
                and prereq_ok
            )
    return not metal_starved_with_positive_pig and (
        has_p1_power
        or miss_pw.pig_iron > 0
        or (not miss_pw.is_zero() and miss_pw.metal == 0)
        or (not can_exec_power and miss_pw.metal == 0)
    )


def _plan_non_p1_furnace_resume(
    state: GameState,
    planet: PlanetState,
    cfg: StrategyConfig,
    snap: _FurnaceSnapshot,
    p_acts: Sequence[PlannedAction],
    *,
    top_needs_pig_only: bool,
    top_needs_metal: bool,
    any_needs_metal: bool,
    metal_starved_with_positive_pig: bool,
) -> PlannedAction | None:
    """Apply energy-coupling lookahead and symmetric deadband before resuming a paused (`0%`) furnace."""
    if snap.metal_cap_pct >= snap.warn_ratio * 0.85:
        return None
    if any(
        a.priority == "P1" and a.missing.pig_iron > 0 and not (snap.sto_pig > 0 and a.cost.pig_iron > snap.sto_pig)
        for a in p_acts
    ):
        return None

    resume_energy_cost = int(round(snap.b2_full_energy * max(0, 100 - snap.cur_b2_pct) / 100.0))
    if snap.e_net - resume_energy_cost < 0:
        return None

    active = state.active_planet()
    is_active = active is not None and planet.planet_id == active.planet_id
    eff_buildings, projected_net = _effective_building_levels_and_energy(
        state,
        planet,
        is_active_planet=is_active,
    )
    projected_net_if_resumed = projected_net - resume_energy_cost
    has_p1_power, would_trigger_p1_power = _would_resume_trigger_p1_power(
        planet, p_acts, eff_buildings, projected_net_if_resumed
    )
    if would_trigger_p1_power and _resume_blocked_by_power_need(
        state,
        planet,
        cfg,
        eff_buildings,
        is_active=is_active,
        has_p1_power=has_p1_power,
        metal_starved_with_positive_pig=metal_starved_with_positive_pig,
    ):
        return None

    coords = _safe_strategy_str(planet.coords)
    if (
        top_needs_metal
        or metal_starved_with_positive_pig
        or (not top_needs_pig_only and any_needs_metal and snap.net_pig_at_100 >= 0)
    ):
        resume_pig_min_i = (
            max(2000, math.ceil(snap.bf_pig_used_full * 0.6)) if math.isfinite(snap.bf_pig_used_full) else 2000
        )
        if snap.net_pig_at_100 >= 0 or snap.res_pig >= resume_pig_min_i:
            return PlannedAction(
                priority="P1",
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=coords,
                target_id=BLAST_FURNACE,
                target_name="Blast furnace 100%",
                target_level=100,
                reason="Metal needed for next priority build/research: resume Blast furnace to 100%",
                executable_now=True,
            )

    min_pig_for_bf = max(5000, math.ceil(snap.bf_pig_used_full * 1.5)) if math.isfinite(snap.bf_pig_used_full) else 5000
    if (
        not top_needs_pig_only
        and snap.net_pig_at_100 >= 0
        and snap.res_pig >= min_pig_for_bf
        and snap.res_pig >= (2 * snap.res_met)
    ):
        return PlannedAction(
            priority="P2",
            kind="THROTTLE_BUILDING",
            planet_id=planet.planet_id,
            planet_coords=coords,
            target_id=BLAST_FURNACE,
            target_name="Blast furnace 100%",
            target_level=100,
            reason="Pig iron reserves healthy: resume Blast furnace smelting to 100%",
            executable_now=True,
        )
    return None
