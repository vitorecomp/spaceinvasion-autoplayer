"""Helper ship builds (s10/s8) and uninhabited free-slot colony grading for fleet planning."""

from __future__ import annotations

from collections.abc import Mapping

from backend.combat.colonies import grade_colony_candidate
from backend.core import catalog
from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState
from backend.planner.heuristics._common import (
    _norm_planet_type,
    _parse_gal_sys_pos,
    _resources_from_dict,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
    _ship_offer_map,
)
from backend.planner.heuristics._fleet_context import _FleetContext
from backend.planner.heuristics._types import (
    FLEET_BASE,
    WEAPONS_FACTORY,
    PlannedAction,
)

_ColonySpySortKey = tuple[int, int, int, int]
_ColonizeSortKey = tuple[int, int, int, int, int]


def _build_designated_probe_action(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
) -> PlannedAction | None:
    """Build a Spy probe (s10) on the designated spy colony if it currently has none stationed."""
    designated = ctx.pools.designated_spy_planet
    if not (ctx.pools.designated_missing_probes and designated is not None and cfg.allow_shipyard):
        return None
    if designated.building_level(WEAPONS_FACTORY) < 1:
        return None
    unmet_s10 = catalog.missing_prerequisites(
        "ships",
        "spy-probe",
        buildings=designated.buildings,
        researches=state.researches or {},
    )
    if unmet_s10:
        return None
    s10_off = _ship_offer_map(designated).get(10)
    s10_cost = (
        s10_off.cost
        if (s10_off is not None and not s10_off.cost.is_zero())
        else _resources_from_dict((catalog.SHIPS.get("spy-probe") or {}).get("base_cost", {}))
    )
    s10_can = bool(designated.resources.can_afford(s10_cost) and (s10_off is None or s10_off.can_build))
    return PlannedAction(
        priority="P1" if ctx.want_colony_probes else "P2",
        kind="BUILD_SHIP",
        planet_id=designated.planet_id,
        planet_coords=_safe_strategy_str(designated.coords),
        target_id=10,
        target_name="Spy probe",
        target_level=1,
        cost=s10_cost,
        missing=designated.resources.missing(s10_cost),
        reason=f"Build Spy probe (s10) on designated spy colony [{_safe_strategy_str(designated.coords)}]",
        executable_now=s10_can,
    )


def _build_colony_ship_action(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
) -> PlannedAction | None:
    """Build Colony ships (s8) up to parallel colonization slot capacity when open slots remain."""
    has_hostile_alert = any(bool(getattr(fa, "is_hostile", False)) for fa in (state.fleet_alerts or ()))
    if not (ctx.want_colonize and cfg.allow_colonize and not ctx.tutorial_colonize_pending and not has_hostile_alert):
        return None
    wf_col_candidates = [
        p
        for p in state.planets
        if p.planet_id not in ctx.undersized_pids
        and _norm_planet_type(p) != "raid"
        and p.building_level(WEAPONS_FACTORY) >= 1
    ]
    if not wf_col_candidates:
        return None

    ref_s8_p = ctx.pools.colony_planet or wf_col_candidates[0]
    max_slots_s8 = 1 + max(0, ref_s8_p.building_level(FLEET_BASE))
    spy_res_s8 = (
        min(ctx.res_spy_cfg_pre, max(0, max_slots_s8 - 1))
        if _safe_strategy_int(ref_s8_p.ships.get("s10", 0), 0) > 0
        else 0
    )
    s8_target = min(ctx.needed_colonies, max(1, max_slots_s8 - spy_res_s8))
    total_s8_all = ctx.pools.total_stationed_s8 + ctx.pools.queued_s8 + len(ctx.in_flight_col_coords)
    if total_s8_all >= s8_target:
        return None

    wf_col_candidates.sort(
        key=lambda p: (
            0
            if not catalog.missing_prerequisites(
                "ships", "colony-ship", buildings=p.buildings, researches=state.researches or {}
            )
            else 1,
            0 if p.planet_id == active.planet_id else 1,
            -p.building_level(WEAPONS_FACTORY),
        )
    )
    wf_col_p = wf_col_candidates[0]
    unmet_s8_p = catalog.missing_prerequisites(
        "ships", "colony-ship", buildings=wf_col_p.buildings, researches=state.researches or {}
    )
    s8_off = _ship_offer_map(wf_col_p).get(8)
    s8_cost = (
        s8_off.cost
        if (s8_off is not None and not s8_off.cost.is_zero())
        else _resources_from_dict((catalog.SHIPS.get("colony-ship") or {}).get("base_cost", {}))
    )
    s8_can = bool(
        cfg.allow_shipyard
        and not unmet_s8_p
        and wf_col_p.resources.can_afford(s8_cost)
        and (s8_off is None or s8_off.can_build)
    )
    s8_reason = f"Build Colony ship (s8) to colonize a {ctx.min_colony_fields}+ field planet"
    if total_s8_all > 0:
        s8_reason += f" ({total_s8_all}/{s8_target} s8 for parallel colonization)"
    if unmet_s8_p:
        s8_reason += f" [Hold: Prerequisites unmet ({unmet_s8_p[0]['name']} L{unmet_s8_p[0]['required']})]"
    elif not cfg.allow_shipyard:
        s8_reason += " [Hold: allow_shipyard=false in Config]"
    return PlannedAction(
        priority="P1",
        kind="BUILD_SHIP",
        planet_id=wf_col_p.planet_id,
        planet_coords=_safe_strategy_str(wf_col_p.coords),
        target_id=8,
        target_name=(_safe_strategy_str(s8_off.name) if (s8_off is not None and s8_off.name) else "") or "Colony ship",
        target_level=1,
        cost=s8_cost,
        missing=wf_col_p.resources.missing(s8_cost),
        reason=s8_reason,
        executable_now=s8_can,
    )


def _enqueue_colonize_candidate(
    norm_coords: str,
    uf: int | None,
    cmax_col: int | None,
    grade_info: Mapping[str, object],
    cfg: StrategyConfig,
    ctx: _FleetContext,
    colonize_actions: list[tuple[_ColonizeSortKey, PlannedAction]],
) -> None:
    if norm_coords in ctx.in_flight_col_coords:
        return
    col_exec_planet = ctx.pools.colony_planet if ctx.pools.colony_planet is not None else ctx.pools.origin
    hold_col_inflight = bool(ctx.tutorial_colonize_pending and ctx.in_flight_col_coords)
    can_exec_col = bool(cfg.allow_colonize and ctx.pools.has_colony_ship and not hold_col_inflight)
    col_prefix = "Tutorial colonize: " if ctx.tutorial_colonize_pending else ""
    f_disp = str(grade_info.get("fields_display") or (f"{uf} fields" if uf is not None else f"{cmax_col} fields"))
    eff_f_sort = int(uf if uf is not None else (cmax_col or 0))
    score_i = _safe_strategy_int(grade_info.get("score"), 0)
    dist_i = int(_safe_strategy_float(grade_info.get("distance"), 0.0))
    col_reason = (
        f"{col_prefix}Colonize {norm_coords} "
        f"({f_disp} >= {ctx.min_colony_fields} target, Grade {grade_info['grade']} {score_i}/100)"
    )
    if not ctx.pools.has_colony_ship:
        col_reason += " [Hold: Build Colony ship (s8) first]"
    elif hold_col_inflight:
        col_reason += " [Hold: Colony ship already in transit]"
    elif not cfg.allow_colonize:
        col_reason += " [Hold: allow_colonize=false in Config]"
    colonize_actions.append(
        (
            (0 if can_exec_col else 1, 0, -score_i, -eff_f_sort, dist_i),
            PlannedAction(
                priority="P1",
                kind="COLONIZE",
                planet_id=col_exec_planet.planet_id,
                planet_coords=_safe_strategy_str(col_exec_planet.coords),
                target_id=8,
                target_level=1,
                target_name=f"Colonize -> {norm_coords} ({f_disp})",
                reason=col_reason,
                executable_now=can_exec_col,
            ),
        )
    )


def _enqueue_colony_probe_candidate(
    raw: Mapping[str, object],
    norm_coords: str,
    t_gal: int,
    t_sys: int,
    pos: int,
    spied_ts_col: float,
    grade_info: Mapping[str, object],
    cfg: StrategyConfig,
    ctx: _FleetContext,
    colony_spy_actions: list[tuple[_ColonySpySortKey, PlannedAction]],
) -> None:
    block_low = _safe_strategy_str(raw.get("block_reason")).lower()
    attack_exempt_banner = "can't be attacked" in block_low or "cannot be attacked" in block_low
    if not (
        not bool(grade_info["probed"])
        and spied_ts_col <= 0.0
        and ctx.want_colony_probes
        and ctx.pools.can_plan_spy
        and (_safe_strategy_float(raw.get("blocked_until_ts"), 0.0) <= ctx.ref_now or attack_exempt_banner)
        and (ctx.bash_tracker.can_attack(norm_coords, is_inactive=True, timestamp=ctx.ref_now) or attack_exempt_banner)
    ):
        return
    probe_planet = ctx.pools.probe_planet
    if probe_planet is None:
        return
    max_spy_slots_col = ctx.max_slots_for(probe_planet)
    used_spy_col = ctx.used_slots_for(probe_planet)
    p_gal_c, p_sys_c, p_pos_c = _parse_gal_sys_pos(probe_planet.coords)
    phys_dist_col = abs(p_gal_c - t_gal) * 20000 + abs(p_sys_c - t_sys) * 95 + abs(p_pos_c - pos) * 5
    zone_prio = 0 if 7 <= pos <= 12 else (1 if 4 <= pos <= 6 else (2 if 13 <= pos <= 16 else 3))
    can_exec_pcol = bool(
        (cfg.allow_galaxy_scan or cfg.allow_colonize) and ctx.pools.has_probes and used_spy_col < max_spy_slots_col
    )
    pcol_reason = (
        f"Probe free slot {norm_coords} (pos {pos}, {grade_info['orbital_zone']}) "
        f"for {ctx.min_colony_fields}+ fields ({ctx.qualified_candidates_count}/{ctx.needed_colonies} qualified)"
    )
    if ctx.pools.designated_missing_probes:
        pcol_reason += " [Hold: No Spy probes (s10) stationed on designated spy colony]"
    elif used_spy_col >= max_spy_slots_col:
        pcol_reason += f" [Hold: Fleet slots full ({used_spy_col}/{max_spy_slots_col} in flight)]"
    elif not (cfg.allow_galaxy_scan or cfg.allow_colonize):
        pcol_reason += " [Hold: allow_galaxy_scan/allow_colonize disabled in Config]"
    colony_spy_actions.append(
        (
            (0 if can_exec_pcol else 1, zone_prio, phys_dist_col, pos),
            PlannedAction(
                priority="P1",
                kind="SPY",
                planet_id=probe_planet.planet_id,
                planet_coords=_safe_strategy_str(probe_planet.coords),
                target_id=10,
                target_level=1,
                target_name=f"{norm_coords} (Free slot)",
                reason=pcol_reason,
                executable_now=can_exec_pcol,
            ),
        )
    )


def _classify_free_colony_slot(
    raw: Mapping[str, object],
    norm_coords: str,
    t_gal: int,
    t_sys: int,
    pos: int,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    colonize_actions: list[tuple[_ColonizeSortKey, PlannedAction]],
    colony_spy_actions: list[tuple[_ColonySpySortKey, PlannedAction]],
) -> None:
    """Grade an uninhabited slot and enqueue either a `COLONIZE` candidate or a free-slot `SPY` probe."""
    raw_uf = raw.get("uninhabited_fields")
    uf = catalog.safe_optional_int(raw_uf, None) if raw_uf is not None else None
    raw_cmin = raw.get("colonize_min_fields")
    cmin_col = catalog.safe_optional_int(raw_cmin, None) if raw_cmin is not None else None
    raw_cmax = raw.get("colonize_max_fields")
    cmax_col = catalog.safe_optional_int(raw_cmax, None) if raw_cmax is not None else None
    raw_temp_c = raw.get("temperature")
    temp_col = catalog.safe_optional_int(raw_temp_c, None) if raw_temp_c is not None else None
    spied_ts_col = _safe_strategy_float(raw.get("last_spied_ts"), 0.0)
    col_ref_planet = ctx.pools.colony_planet or ctx.pools.probe_planet or ctx.pools.origin
    grade_info = grade_colony_candidate(
        coords=norm_coords,
        position=pos,
        uninhabited_fields=uf,
        colonize_min_fields=cmin_col,
        colonize_max_fields=cmax_col,
        temperature=temp_col,
        origin_coords=col_ref_planet.coords,
        min_fields_target=ctx.min_colony_fields,
    )
    if bool(grade_info["meets_target"]) and (cfg.allow_colonize or ctx.tutorial_colonize_pending):
        _enqueue_colonize_candidate(norm_coords, uf, cmax_col, grade_info, cfg, ctx, colonize_actions)
        return
    _enqueue_colony_probe_candidate(
        raw, norm_coords, t_gal, t_sys, pos, spied_ts_col, grade_info, cfg, ctx, colony_spy_actions
    )
