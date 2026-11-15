"""Galaxy scan, Spy scouting, Colonize, Transport/Station tiering, Recycle, and Attack mission planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState
from backend.planner.heuristics._common import _safe_strategy_int
from backend.planner.heuristics._fleet_context import _build_fleet_context, _FleetContext
from backend.planner.heuristics._fleet_harvest import _HarvestSortKey, _plan_unified_harvest
from backend.planner.heuristics._fleet_helper_builds import (
    _build_colony_ship_action,
    _build_designated_probe_action,
    _ColonizeSortKey,
    _ColonySpySortKey,
)
from backend.planner.heuristics._fleet_scan_plan import (
    _next_spiral_scan_action,
    _plan_tutorial_recycle_result,
)
from backend.planner.heuristics._fleet_slots import (
    _apply_strict_spy_slot_gating,
    _gate_colonize_tier,
    _gate_station_tier,
    _gate_transport_tier,
    _SlotReservations,
)
from backend.planner.heuristics._fleet_targets import (
    _collect_target_candidates,
    _SpySortKey,
)
from backend.planner.heuristics._types import PlannedAction

__all__ = ["_plan_combat_and_raid_actions"]


def _assemble_fleet_output(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    *,
    colony_spy_actions: list[tuple[_ColonySpySortKey, PlannedAction]],
    spy_actions: list[tuple[_SpySortKey, PlannedAction]],
    transport_actions: list[PlannedAction],
    colonize_actions: list[tuple[_ColonizeSortKey, PlannedAction]],
    station_actions: list[PlannedAction],
    colony_ship_build_act: PlannedAction | None,
    designated_probe_build_act: PlannedAction | None,
    harvest_actions: list[tuple[_HarvestSortKey, PlannedAction]],
) -> list[PlannedAction]:
    """Assemble final ordered fleet mission actions according to tutorial, tier, and scan fallbacks."""
    if ctx.tutorial_spy_pending and spy_actions:
        spy_actions.sort(key=lambda item: item[0])
        out_tut_spy = [act for _, act in spy_actions[:3]]
        if designated_probe_build_act is not None:
            out_tut_spy.insert(0, designated_probe_build_act)
        if colony_ship_build_act is not None:
            out_tut_spy.insert(0, colony_ship_build_act)
        return out_tut_spy

    if (
        transport_actions
        or station_actions
        or colonize_actions
        or colony_spy_actions
        or colony_ship_build_act is not None
        or harvest_actions
    ):
        out_actions: list[PlannedAction] = []
        if colony_spy_actions:
            colony_spy_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in colony_spy_actions[:2])
        if spy_actions:
            spy_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in spy_actions[:2])
        if transport_actions:
            out_actions.extend(transport_actions)
        if colonize_actions:
            colonize_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in colonize_actions)
        if station_actions:
            out_actions.extend(station_actions)
        if colony_ship_build_act is not None:
            out_actions.append(colony_ship_build_act)
        if designated_probe_build_act is not None:
            out_actions.append(designated_probe_build_act)
        if harvest_actions:
            harvest_actions.sort(key=lambda item: item[0])
            harvest_cap = min(12, max(5, 3 * max(len(ctx.pools.attack_planets), len(ctx.pools.recycler_planets), 1)))
            out_actions.extend(act for _, act in harvest_actions[:harvest_cap])
        if (ctx.want_colonize or ctx.want_colony_probes) and not colonize_actions and not colony_spy_actions:
            nxt_scan = _next_spiral_scan_action(state, cfg, ctx)
            if nxt_scan is not None:
                out_actions.append(nxt_scan)
        return out_actions

    if spy_actions or colony_spy_actions or designated_probe_build_act is not None:
        out_spy_only: list[PlannedAction] = []
        if colony_ship_build_act is not None:
            out_spy_only.append(colony_ship_build_act)
        if designated_probe_build_act is not None:
            out_spy_only.append(designated_probe_build_act)
        if colony_spy_actions:
            colony_spy_actions.sort(key=lambda item: item[0])
            out_spy_only.extend(act for _, act in colony_spy_actions[:3])
        if spy_actions:
            spy_actions.sort(key=lambda item: item[0])
            out_spy_only.extend(act for _, act in spy_actions[:3])
        return out_spy_only

    nxt_scan = _next_spiral_scan_action(state, cfg, ctx)
    out_tail: list[PlannedAction] = []
    if colony_ship_build_act is not None:
        out_tail.append(colony_ship_build_act)
    if nxt_scan is not None:
        out_tail.append(nxt_scan)
    return out_tail


def _plan_combat_and_raid_actions(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    *,
    scanned_systems: Mapping[str, float] | None = None,
    now_ts: float | None = None,
    raid_stats: Mapping[str, object] | None = None,
    existing_plan: Sequence[PlannedAction] = (),
    priority_resource_need: Mapping[int, object] | None = None,
) -> list[PlannedAction]:
    """Plan Galaxy exploration sweep, Spy probe scouting, Space Rubble recycling, and scored Attack targets."""
    if state.tutorial is not None and state.tutorial.can_complete:
        return []

    ctx = _build_fleet_context(
        state,
        active,
        cfg,
        scanned_systems=scanned_systems,
        now_ts=now_ts,
        raid_stats=raid_stats,
    )
    if ctx is None:
        return []

    designated_probe_build_act = _build_designated_probe_action(state, cfg, ctx)
    colony_ship_build_act = _build_colony_ship_action(state, active, cfg, ctx)
    scanned = _collect_target_candidates(cfg, ctx)

    res_spy_cfg = max(0, _safe_strategy_int(getattr(cfg, "reserved_spy_slots", 1), 1))
    spy_only_res = bool(getattr(cfg, "spy_reserved_slots_only", False))
    colony_spy_actions, spy_actions = _apply_strict_spy_slot_gating(
        ctx, res_spy_cfg, spy_only_res, scanned.colony_spy_actions, scanned.spy_actions
    )
    all_spy_planet_ids = {
        sa.planet_id
        for _, sa in [*spy_actions, *colony_spy_actions]
        if sa.executable_now or (cfg.allow_galaxy_scan or cfg.allow_colonize)
    }
    base_slots = _SlotReservations(res_spy_cfg, spy_only_res, all_spy_planet_ids, {}, {}, {})

    transport_actions, tr_res_by_planet = _gate_transport_tier(
        state, cfg, ctx, base_slots, existing_plan, priority_resource_need
    )
    colonize_actions, col_res_by_planet = _gate_colonize_tier(
        cfg, ctx, base_slots, tr_res_by_planet, scanned.colonize_actions
    )
    station_actions, st_res_by_planet = _gate_station_tier(
        state, cfg, ctx, base_slots, tr_res_by_planet, col_res_by_planet, existing_plan
    )
    slots = _SlotReservations(
        res_spy_cfg, spy_only_res, all_spy_planet_ids, tr_res_by_planet, col_res_by_planet, st_res_by_planet
    )

    recycle_actions, scored_attacks = _plan_unified_harvest(
        state, cfg, ctx, slots, scanned.debris_entries, scanned.attack_entries
    )
    tut_rec_result = _plan_tutorial_recycle_result(cfg, ctx, recycle_actions)
    if tut_rec_result is not None:
        return tut_rec_result

    harvest_actions: list[tuple[_HarvestSortKey, PlannedAction]] = []
    for sk, act in scored_attacks:
        g = slots.gate_non_spy_slot(ctx, act)
        harvest_actions.append(((0 if g.executable_now else 1, sk[1], sk[2], sk[3]), g))
    for sk, act in recycle_actions:
        g = slots.gate_non_spy_slot(ctx, act)
        harvest_actions.append(((0 if g.executable_now else 1, sk[1], sk[2], sk[3]), g))

    return _assemble_fleet_output(
        state,
        cfg,
        ctx,
        colony_spy_actions=colony_spy_actions,
        spy_actions=spy_actions,
        transport_actions=transport_actions,
        colonize_actions=colonize_actions,
        station_actions=station_actions,
        colony_ship_build_act=colony_ship_build_act,
        designated_probe_build_act=designated_probe_build_act,
        harvest_actions=harvest_actions,
    )
