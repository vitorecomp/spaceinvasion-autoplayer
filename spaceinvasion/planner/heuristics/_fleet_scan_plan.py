"""Smart galaxy scan selection and tutorial recycle fast-path for fleet mission planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _parse_gal_sys_pos,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._fleet_context import _FleetContext
from spaceinvasion.planner.heuristics._fleet_harvest import _HarvestSortKey
from spaceinvasion.planner.heuristics._scan import select_planner_scan_system
from spaceinvasion.planner.heuristics._types import PlannedAction


def _plan_tutorial_recycle_result(
    cfg: StrategyConfig,
    ctx: _FleetContext,
    recycle_actions: list[tuple[_HarvestSortKey, PlannedAction]],
) -> list[PlannedAction] | None:
    """Handle tutorial space-rubble recycling priority: harvest known rubble or scan recent battle system."""
    if not ctx.tutorial_recycle_pending:
        return None
    if recycle_actions:
        recycle_actions.sort(key=lambda item: item[0])
        return [act for _, act in recycle_actions[:3]]
    recycler_planet = ctx.pools.recycler_planet
    if not (ctx.pools.has_recyclers and not ctx.own_fleet_in_flight and recycler_planet is not None):
        return None
    r_gal, r_sys, _ = _parse_gal_sys_pos(recycler_planet.coords)
    unscanned_battles: list[tuple[int, float, int, int]] = []
    for skey_b, b_ts in ctx.latest_battle_by_sys.items():
        if b_ts > ctx.scanned_map.get(skey_b, 0.0):
            parts_b = skey_b.split(":")
            if len(parts_b) == 2 and parts_b[0].isdigit() and parts_b[1].isdigit():
                bg, bs = int(parts_b[0]), int(parts_b[1])
                if 1 <= bs <= 400:
                    dist_b = abs(r_gal - bg) * 400 + abs(r_sys - bs)
                    unscanned_battles.append((dist_b, -b_ts, bg, bs))
    if not unscanned_battles:
        return None
    unscanned_battles.sort()
    _, _, gal_b, sys_b = unscanned_battles[0]
    scan_note_r = "" if cfg.allow_galaxy_scan else " (enable allow_galaxy_scan in Config)"
    tut_desc = _safe_strategy_str(ctx.tut_recycle.description) if ctx.tut_recycle is not None else ""
    return [
        PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=recycler_planet.planet_id,
            planet_coords=_safe_strategy_str(recycler_planet.coords),
            target_name=f"Galaxy [{gal_b:02d}:{sys_b:03d}]",
            reason=f"Scan battle system {gal_b:02d}:{sys_b:03d} for space rubble ({tut_desc}){scan_note_r}",
            executable_now=bool(cfg.allow_galaxy_scan),
        )
    ]


def _pick_legacy_spiral_system(
    colonies: Sequence[tuple[PlanetState, int, int]],
    scanned_map: Mapping[str, float],
    ref_now: float,
    max_radius: int,
    scan_cooldown_s: float,
) -> tuple[PlanetState, int, int] | None:
    offsets = [0] + [off for d in range(1, max_radius + 1) for off in (d, -d)]
    for off in offsets:
        for col_p, col_gal, col_sys in colonies:
            cand_sys = col_sys + off
            if not (1 <= cand_sys <= 400):
                continue
            skey = f"{col_gal:02d}:{cand_sys:03d}"
            prev_ts = scanned_map.get(skey)
            if prev_ts is not None and (ref_now - prev_ts) < scan_cooldown_s:
                continue
            return col_p, col_gal, cand_sys
    return None


def _next_spiral_scan_action(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
) -> PlannedAction | None:
    """Select the next `SCAN_GALAXY` target using 2-phase smart selection with legacy spiral fallback."""
    scan_cooldown_s = 1800.0
    primary_scan_p = (
        ctx.pools.recycler_planet
        if (ctx.tutorial_recycle_pending and ctx.pools.recycler_planet is not None)
        else (
            ctx.pools.colony_planet if (ctx.want_colonize and ctx.pools.colony_planet is not None) else ctx.pools.origin
        )
    )
    base_planets = [primary_scan_p]
    if cfg.allow_multi_planet:
        for p in state.planets:
            if p.planet_id != primary_scan_p.planet_id and _norm_planet_type(p) != "raid":
                base_planets.append(p)
    colonies: list[tuple[PlanetState, int, int]] = []
    for p in base_planets:
        g_p, s_p, _ = _parse_gal_sys_pos(p.coords)
        colonies.append((p, g_p, s_p))
    max_radius = 25 if (ctx.tutorial_recycle_pending or ctx.want_colonize) else 15

    hist_stats = next(
        (
            rs
            for rs in ctx.stats_sources
            if isinstance(rs.get("history"), Sequence) and not isinstance(rs.get("history"), (str, bytes))
        ),
        ctx.stats_sources[0] if ctx.stats_sources else None,
    )
    scanned_arg: dict[object, object] = {k: v for k, v in ctx.scanned_map.items()}
    pick = select_planner_scan_system(
        [_safe_strategy_str(p.coords) for p in base_planets],
        scanned_systems=scanned_arg,
        raid_targets=[r for r in ctx.raid_targets if isinstance(r, Mapping)],
        raid_stats=hist_stats,
        cfg=cfg,
        radius=max_radius,
        now_ts=ctx.ref_now if ctx.ref_now > 0.0 else None,
        cooldown_s=scan_cooldown_s,
    )
    smart_note = ""
    chosen: tuple[PlanetState, int, int] | None
    if pick is not None:
        p_gal = _safe_strategy_int(pick.get("galaxy"), 0)
        p_sys = _safe_strategy_int(pick.get("system"), 0)
        best_col = min(
            colonies,
            key=lambda c: (0 if c[1] == p_gal else 1, min(abs(c[2] - p_sys), 400 - abs(c[2] - p_sys))),
        )
        chosen = (best_col[0], p_gal, p_sys)
        phase_lbl = "explore" if pick.get("phase") == "exploratory" else "refresh"
        smart_note = f" [{phase_lbl}: {_safe_strategy_str(pick.get('reason'))}]"
    else:
        chosen = _pick_legacy_spiral_system(colonies, ctx.scanned_map, ctx.ref_now, max_radius, scan_cooldown_s)

    if chosen is None:
        return None
    scan_planet, gal_num, sys_num = chosen
    if ctx.want_colonize:
        tut_hint = f" and {ctx.min_colony_fields}+ field colony slots"
    elif ctx.tut_recycle is not None:
        tut_hint = f" and space rubble for {_safe_strategy_str(ctx.tut_recycle.description)}"
    elif ctx.tut_fight is not None:
        tut_hint = f" for {_safe_strategy_str(ctx.tut_fight.description)}"
    else:
        tut_hint = ""
    scan_note = "" if cfg.allow_galaxy_scan else " (click Scan Galaxy in Combat or enable allow_galaxy_scan in Config)"
    scan_prio = "P1" if (ctx.want_colonize or ctx.want_colony_probes) else "P2"
    return PlannedAction(
        priority=scan_prio,
        kind="SCAN_GALAXY",
        planet_id=scan_planet.planet_id,
        planet_coords=_safe_strategy_str(scan_planet.coords),
        target_name=f"Galaxy [{gal_num:02d}:{sys_num:03d}]",
        reason=(
            f"Scan system {gal_num:02d}:{sys_num:03d} for yellow inactive / Spacia raid targets"
            f"{tut_hint}{smart_note}{scan_note}"
        ),
        executable_now=bool(cfg.allow_galaxy_scan),
    )
