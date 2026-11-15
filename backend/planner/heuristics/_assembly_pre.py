"""Thermosolar teardown, storage overflow, and warehouse-cap expansion guards for plan assembly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from backend.core import catalog
from backend.core.config import StrategyConfig
from backend.core.model import BuildingOffer, GameState, PlanetState
from backend.planner.heuristics._actions import _make_build_action
from backend.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _norm_planet_type,
    _offer_map,
    _planet_has_free_building_fields,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from backend.planner.heuristics._queues import (
    _building_job_matches_planet,
    _eff_level,
    _effective_building_levels_and_energy,
)
from backend.planner.heuristics._types import (
    _WAREHOUSE_BY_RESOURCE,
    BLAST_FURNACE,
    FUSION_PLANT,
    KRYPTONITE_MINE,
    PIG_IRON_MINE,
    SPICE_MINE,
    THERMOSOLAR_PLANT,
    PlannedAction,
)


def _throttled_energy_reserve(planet: PlanetState) -> int:
    reserve = 0
    e_rates = planet.economy_rates if isinstance(planet.economy_rates, Mapping) else {}
    for bid_m, rkey_m in (
        (PIG_IRON_MINE, "resource1"),
        (BLAST_FURNACE, "resource2"),
        (KRYPTONITE_MINE, "resource3"),
        (SPICE_MINE, "resource4"),
    ):
        pct_m = max(0, min(100, _safe_strategy_int(e_rates.get(rkey_m, 100), 100)))
        lvl_m = int(planet.building_level(bid_m))
        if pct_m < 100 and lvl_m > 0:
            full_m = abs(sum(catalog.building_energy_delta(bid_m, lvl) for lvl in range(1, min(lvl_m, 101) + 1)))
            reserve += int(math.ceil(full_m * (100 - pct_m) / 100.0))
    return reserve


def append_thermosolar_teardown_guard(
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    eff_buildings: Mapping[str, int],
    *,
    projected_net: int,
    ptype_now: str,
    is_active: bool,
    queue_full: bool,
    allow_remote: bool,
    cfg: StrategyConfig,
    plan: list[PlannedAction],
) -> None:
    """Demolish Thermosolar plant (`b6`) on normal planets to reclaim building fields when energy buffer allows."""
    cold_spice_keep_b6 = (
        not bool(getattr(cfg, "forbid_planetary_solar", False))
        and _safe_strategy_int(planet.temperature, 0) <= -100
        and _eff_level(planet, eff_buildings, SPICE_MINE) >= 12
        and (_safe_strategy_int(planet.fields_max, 0) <= 0 or _safe_strategy_int(planet.fields_remaining, 0) > 40)
        and planet.building_level(THERMOSOLAR_PLANT) <= 6
    )
    if ptype_now in ("asteroid", "raid") or (state.tutorial and state.tutorial.can_complete) or cold_spice_keep_b6:
        return
    cur_b6 = int(planet.building_level(THERMOSOLAR_PLANT))
    tut_needs_b6 = bool(
        state.tutorial
        and not state.tutorial.can_complete
        and any(
            not t.completed
            and t.category == "building"
            and catalog.safe_optional_int(t.building_id, None) == THERMOSOLAR_PLANT
            for t in state.tutorial.tasks
        )
    )
    b6_in_queue = any(
        _building_job_matches_planet(q, state, planet, is_active_planet=is_active)
        and (
            catalog.safe_optional_int(q.building_id, None) == THERMOSOLAR_PLANT
            or "thermosolar" in _safe_strategy_str(q.name).lower()
            or "thermal" in _safe_strategy_str(q.name).lower()
        )
        for q in state.active_queues
    )
    if cur_b6 <= 0 or tut_needs_b6 or b6_in_queue:
        return

    r2_lvl = max(0, _safe_strategy_int((state.researches or {}).get("r2", 0), 0))
    safe_b6_loss = int(
        math.ceil(max(0, catalog.building_energy_delta(THERMOSOLAR_PLANT, cur_b6)) * (1.0 + 0.01 * min(r2_lvl, 200)))
    )
    avail_e_buffer = min(_safe_strategy_int(planet.energy.net, 0), projected_net) - _throttled_energy_reserve(planet)
    if avail_e_buffer - safe_b6_loss >= 0:
        allow_bld_cfg = bool(getattr(cfg, "allow_build", True))
        can_dem_now = bool(allow_bld_cfg and not queue_full and (is_active or allow_remote))
        dem_reason = f"Tear down Thermosolar plant (L{cur_b6} -> L{cur_b6 - 1}) to reclaim planetary building fields"
        if queue_full:
            dem_reason += " (build queue full)"
        elif not allow_bld_cfg:
            dem_reason += " (allow_build=false in Config)"
        elif not (is_active or allow_remote):
            dem_reason += " (remote colony build disabled)"
        plan.append(
            PlannedAction(
                priority="P1",
                kind="DEMOLISH_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=_safe_strategy_str(planet.coords),
                target_id=THERMOSOLAR_PLANT,
                target_name="Thermosolar plant",
                target_level=cur_b6 - 1,
                reason=dem_reason,
                executable_now=can_dem_now,
            )
        )
    elif _is_building_allowed_on_planet(planet, FUSION_PLANT, offers) and not any(
        a.planet_id == planet.planet_id and a.target_id == FUSION_PLANT and a.kind in ("BUILD", "WAIT_RESOURCES")
        for a in plan
    ):
        plan.append(
            _apply_queue_cap_if_full(
                _make_build_action(
                    "P1",
                    planet,
                    FUSION_PLANT,
                    "Fusion plant",
                    f"Upgrade Fusion plant before tearing down Thermosolar plant L{cur_b6} (needs +{safe_b6_loss} energy buffer)",
                    offers,
                    effective_level=_eff_level(planet, eff_buildings, FUSION_PLANT),
                    researches=state.researches,
                    allow_remote_colony_build=allow_remote,
                ),
                queue_full,
            )
        )


def append_storage_overflow_guard(
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    eff_buildings: Mapping[str, int],
    *,
    ptype_now: str,
    warn_ratio: float,
    queue_full: bool,
    allow_remote: bool,
    plan: list[PlannedAction],
) -> None:
    if ptype_now in ("asteroid", "raid"):
        return
    for res_key, raw_ratio in planet.storage_fill_ratios().items():
        ratio = _safe_strategy_float(raw_ratio, 0.0)
        if ratio < warn_ratio:
            continue
        wh_id, wh_name = _WAREHOUSE_BY_RESOURCE[res_key]
        if not _is_building_allowed_on_planet(planet, wh_id, offers):
            continue
        plan.append(
            _apply_queue_cap_if_full(
                _make_build_action(
                    "P1",
                    planet,
                    wh_id,
                    wh_name,
                    f"{res_key.replace('_', ' ').title()} storage at {ratio * 100:.0f}% (5% cap risk)",
                    offers,
                    effective_level=_eff_level(planet, eff_buildings, wh_id),
                    researches=state.researches,
                    allow_remote_colony_build=allow_remote,
                ),
                queue_full,
            )
        )


def append_warehouse_guards_and_cap_expansions(
    state: GameState,
    active: PlanetState,
    planets_to_check: Sequence[PlanetState],
    queued_counts: Mapping[int, int],
    *,
    active_queue_full: bool,
    cfg: StrategyConfig,
    plan: list[PlannedAction],
) -> None:
    """Expand warehouses when a P1/P2 `WAIT_RESOURCES` upgrade exceeds current storage capacity."""
    for planet in planets_to_check:
        if _norm_planet_type(planet) in ("asteroid", "raid") or not _planet_has_free_building_fields(planet):
            continue
        is_p_active = planet.planet_id == active.planet_id
        p_offers = _offer_map(planet)
        if not p_offers:
            continue
        eff_b_wh, _ = _effective_building_levels_and_energy(state, planet, is_active_planet=is_p_active)
        p_queue_full = active_queue_full if is_p_active else False
        p_allow_remote = cfg.allow_multi_planet and not is_p_active
        p_wait_acts = [
            a
            for a in plan
            if a.planet_id == planet.planet_id and a.priority in ("P1", "P2") and a.kind == "WAIT_RESOURCES"
        ]
        for w_act in p_wait_acts:
            for rk in ("pig_iron", "metal", "kryptonite", "spice"):
                cost_v = _safe_strategy_int(getattr(w_act.cost, rk, 0), 0)
                sto_v = _safe_strategy_int(getattr(planet.storage, rk, 0), 0)
                if sto_v <= 0 or cost_v <= sto_v:
                    continue
                wh_id, wh_name = _WAREHOUSE_BY_RESOURCE[rk]
                if wh_id not in p_offers or not _is_building_allowed_on_planet(planet, wh_id, p_offers):
                    continue
                if is_p_active and queued_counts.get(wh_id, 0) > 0:
                    continue
                if any(
                    a.planet_id == planet.planet_id and a.target_id == wh_id and a.kind in ("BUILD", "WAIT_RESOURCES")
                    for a in plan
                ):
                    continue
                plan.append(
                    _apply_queue_cap_if_full(
                        _make_build_action(
                            w_act.priority,
                            planet,
                            wh_id,
                            wh_name,
                            (
                                f"Expand {rk.replace('_', ' ')} cap ({catalog.fmt_compact_int(sto_v)}) "
                                f"for {w_act.target_name} ({catalog.fmt_compact_int(cost_v)} needed)"
                            ),
                            p_offers,
                            effective_level=_eff_level(planet, eff_b_wh, wh_id),
                            researches=state.researches,
                            allow_remote_colony_build=p_allow_remote,
                        ),
                        p_queue_full,
                    )
                )
