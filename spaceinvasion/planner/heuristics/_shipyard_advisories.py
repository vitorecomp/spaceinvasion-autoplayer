"""Mining tech ROI and goal-ship unlock-chain advisories for non-raid planets."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import BuildingOffer, GameState, PlanetState, ResearchOffer
from spaceinvasion.planner.heuristics._actions import _make_build_action, _make_research_action
from spaceinvasion.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _planet_has_free_building_fields,
    _resolve_shipyard_targets,
    _resources_from_dict,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._econ_helpers import _empire_raw_spice_eq_per_hour, _resolve_prereq_chain
from spaceinvasion.planner.heuristics._queues import _eff_level
from spaceinvasion.planner.heuristics._types import WEAPONS_FACTORY, PlannedAction

_FLEETER_GOAL_SHIPS: tuple[str, ...] = (
    "small-transport",
    "spy-probe",
    "recycler",
    "colony-ship",
    "phoenix",
    "transmitter",
    "battleship",
    "stealth-bomber",
)
_DEFAULT_GOAL_SHIPS: tuple[str, ...] = ("small-transport", "colony-ship")


def _plan_mining_tech_advisory(
    *,
    state: GameState,
    planet: PlanetState,
    eff_buildings: Mapping[str, int],
    research_offers: Mapping[str, ResearchOffer],
    research_queue_busy: bool,
    config: StrategyConfig,
    advisories: list[PlannedAction],
) -> None:
    mining_lvl = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    r1_info = catalog.RESEARCH_BY_ID.get("r1")
    if r1_info and not catalog.missing_prerequisites(
        "research", "r1", buildings=eff_buildings, researches=state.researches
    ):
        empire_raw = _empire_raw_spice_eq_per_hour(state)
        delta_r1 = 0.02 * empire_raw
        levels_raw = r1_info.get("levels")
        lvl_row = levels_raw.get(mining_lvl + 1) if isinstance(levels_raw, Mapping) else None
        r1_cost_d = lvl_row.get("cost", {}) if isinstance(lvl_row, Mapping) else {}
        r1_cost_sp = catalog.spice_equivalent(r1_cost_d)
        if delta_r1 > 0 and r1_cost_sp > 0:
            pb = r1_cost_sp / delta_r1
            reason = (
                f"+2% Pig/Kryp/Spice across {len(state.planets)} planet(s): "
                f"+{delta_r1:.1f} Spice-eq/h ({pb:.1f}h payback)"
            )
        else:
            reason = "+2% Pig iron, Kryptonite & Spice output across all planets"
        advisories.append(
            _make_research_action(
                "P3",
                planet,
                state,
                "r1",
                reason,
                research_offers,
                research_queue_busy,
                allow_live=config.allow_research,
            )
        )
    elif max(0, _safe_strategy_int((state.researches or {}).get("r2", 0), 0)) < 4 and 2 not in {
        a.target_id for a in advisories if a.kind == "RESEARCH"
    }:
        r2_cur = max(0, _safe_strategy_int((state.researches or {}).get("r2", 0), 0))
        advisories.append(
            _make_research_action(
                "P3",
                planet,
                state,
                "r2",
                f"Global +1% energy & prerequisite L4 for Mining tech ({catalog.fmt_compact_int(r2_cur)}/4)",
                research_offers,
                research_queue_busy,
                allow_live=config.allow_research,
            )
        )


def _plan_goal_ship_advisories(
    *,
    state: GameState,
    planet: PlanetState,
    eff_buildings: Mapping[str, int],
    offers: Mapping[int, BuildingOffer],
    research_offers: Mapping[str, ResearchOffer],
    queue_full: bool,
    research_queue_busy: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
    base_ready: bool,
    wf: int,
    need_local_wf: bool,
    build_unlocks: list[PlannedAction],
    advisories: list[PlannedAction],
) -> None:
    goal_ships = _FLEETER_GOAL_SHIPS if config.playstyle == "fleeter" else _DEFAULT_GOAL_SHIPS
    planned_ship_names = {a.target_name for a in advisories if a.kind in ("BUILD_SHIP", "SHIP")}
    norm_shipyard_targets = _resolve_shipyard_targets(config.shipyard_targets)
    if wf >= 1 and config.allow_shipyard:
        for _, raw_t_cnt in norm_shipyard_targets.items():
            if raw_t_cnt > 0:
                planned_ship_names.add("Small transport")
                break
    for ship_slug in goal_ships:
        ship_info = catalog.SHIPS.get(ship_slug)
        if not ship_info or str(ship_info["name"]) in planned_ship_names:
            continue
        steps = _resolve_prereq_chain("ships", ship_slug, str(ship_info["name"]), eff_buildings, state.researches)
        if not steps:
            ship_cost = _resources_from_dict(ship_info.get("base_cost", {}))
            advisories.append(
                PlannedAction(
                    priority="P3",
                    kind="SHIP",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_strategy_str(planet.coords),
                    target_name=str(ship_info["name"]),
                    cost=ship_cost,
                    missing=planet.resources.missing(ship_cost),
                    reason=f"{ship_info['name']} unlocked (trade/expedition/colonization ready)",
                    executable_now=False,
                )
            )
            continue
        first_res = next((s for s in steps if s["category"] == "research"), None)
        if first_res:
            res_info = catalog.resolve_unit("research", first_res["slug"])
            rkey = str((res_info or {}).get("id") or "")
            if rkey and int(rkey[1:]) not in {a.target_id for a in advisories if a.kind == "RESEARCH"}:
                cur_s = catalog.fmt_compact_int(_safe_strategy_int(first_res.get("current", 0), 0))
                req_s = catalog.fmt_compact_int(_safe_strategy_int(first_res.get("required", 0), 0))
                advisories.append(
                    _make_research_action(
                        "P3",
                        planet,
                        state,
                        rkey,
                        f"Tech step ({cur_s}/{req_s}) toward {first_res['goal']}",
                        research_offers,
                        research_queue_busy,
                        allow_live=config.allow_research,
                    )
                )
            continue
        first_bld = next((s for s in steps if s["category"] == "buildings"), None)
        if first_bld and base_ready and _planet_has_free_building_fields(planet):
            bld_info = catalog.resolve_unit("buildings", first_bld["slug"])
            req_bid = int(bld_info["id"]) if (bld_info and bld_info.get("id") is not None) else 0
            if (
                req_bid > 0
                and (req_bid != WEAPONS_FACTORY or need_local_wf)
                and _is_building_allowed_on_planet(planet, req_bid, offers)
                and req_bid not in {a.target_id for a in build_unlocks}
            ):
                cur_s = catalog.fmt_compact_int(_safe_strategy_int(first_bld.get("current", 0), 0))
                req_s = catalog.fmt_compact_int(_safe_strategy_int(first_bld.get("required", 0), 0))
                act = _make_build_action(
                    "P3",
                    planet,
                    req_bid,
                    str(first_bld["name"]),
                    f"Building step ({cur_s}/{req_s}) toward {first_bld['goal']}",
                    offers,
                    effective_level=_eff_level(planet, eff_buildings, req_bid),
                    researches=state.researches,
                    allow_remote_colony_build=allow_remote_build,
                )
                advisories.append(_apply_queue_cap_if_full(act, queue_full))
