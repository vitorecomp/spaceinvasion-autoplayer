"""Fleet Planner P2 research target and ship-prerequisite unlock planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState, ResearchOffer
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _research_offer_map,
    _resolve_fleet_research_targets,
    _resolve_shipyard_targets,
    _resources_from_dict,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._queues import _active_research_increments
from spaceinvasion.planner.heuristics._types import RESEARCH_LAB, WEAPONS_FACTORY, PlannedAction


def _eff_res_level(state: GameState, active_res_inc: Mapping[str, int], rk: str) -> int:
    live = max(0, _safe_strategy_int((state.researches or {}).get(rk, 0), 0))
    return live + active_res_inc.get(rk, 0)


def _analyze_existing_research_plan(existing_plan: Sequence[PlannedAction]) -> tuple[set[int], bool]:
    existing_research_ids: set[int] = set()
    ai_p1_research_ready = False
    first_waiting_ai: PlannedAction | None = None
    for act in existing_plan or ():
        if act.kind == "RESEARCH":
            tid = catalog.safe_optional_int(act.target_id, None)
            if tid is not None:
                existing_research_ids.add(tid)
            if act.priority in ("P0", "P1") and act.executable_now:
                ai_p1_research_ready = True
        if (
            first_waiting_ai is None
            and act.priority == "P1"
            and not act.executable_now
            and act.kind in ("BUILD", "RESEARCH", "WAIT_RESOURCES")
        ):
            first_waiting_ai = act
    ai_p1_research_blocking = ai_p1_research_ready or bool(
        first_waiting_ai is not None and first_waiting_ai.kind == "RESEARCH"
    )
    return existing_research_ids, ai_p1_research_blocking


def _collect_shipyard_research_desires(
    state: GameState,
    config: StrategyConfig,
    non_raid_planets: Sequence[PlanetState],
    active_res_inc: Mapping[str, int],
) -> tuple[dict[str, int], dict[str, list[str]], dict[str, bool]]:
    desired: dict[str, int] = {}
    unlock_ships: dict[str, list[str]] = {}
    unlock_b9_ready: dict[str, bool] = {}

    for rk, lvl in _resolve_fleet_research_targets(getattr(config, "fleet_research_targets", None)).items():
        if lvl > desired.get(rk, 0):
            desired[rk] = lvl

    if not config.allow_shipyard:
        return desired, unlock_ships, unlock_b9_ready

    has_asteroid = any(_norm_planet_type(p) == "asteroid" for p in non_raid_planets)
    max_b9 = max((p.building_level(WEAPONS_FACTORY) for p in non_raid_planets), default=0)
    for ship_slug, target_cnt in _resolve_shipyard_targets(config.shipyard_targets).items():
        if target_cnt <= 0:
            continue
        if ship_slug == "solar-cells" and bool(getattr(config, "forbid_planetary_solar", False)) and not has_asteroid:
            continue
        ship_info = catalog.SHIPS.get(ship_slug) or catalog.resolve_unit("ships", ship_slug)
        if not isinstance(ship_info, Mapping):
            continue
        ship_name = str(ship_info.get("name") or ship_slug)
        req_b9 = 0
        ship_res_reqs: list[tuple[str, int]] = []
        for req in ship_info.get("requirements") or ():
            if not isinstance(req, Mapping):
                continue
            rcat = str(req.get("category") or "").strip().lower()
            rlvl = max(0, _safe_strategy_int(req.get("level"), 0))
            rslug = str(req.get("slug") or req.get("name") or "").strip()
            if rcat == "buildings" and rslug in ("weapons-factory", "b9", "Weapons factory"):
                req_b9 = max(req_b9, rlvl)
            elif rcat == "research" and rlvl > 0:
                r_info = catalog.resolve_unit("research", rslug)
                r_id = str(r_info.get("id") or "") if isinstance(r_info, Mapping) else ""
                if r_id in catalog.RESEARCH_BY_ID:
                    ship_res_reqs.append((r_id, rlvl))
        if not ship_res_reqs:
            continue
        ship_b9_ok = max_b9 >= req_b9
        for req_rk, req_lvl in ship_res_reqs:
            if req_lvl > desired.get(req_rk, 0):
                desired[req_rk] = req_lvl
            if _eff_res_level(state, active_res_inc, req_rk) < req_lvl:
                ships_list = unlock_ships.setdefault(req_rk, [])
                if ship_name not in ships_list:
                    ships_list.append(ship_name)
                if ship_b9_ok:
                    unlock_b9_ready[req_rk] = True
    return desired, unlock_ships, unlock_b9_ready


def _expand_transitive_research_prereqs(
    state: GameState,
    active_res_inc: Mapping[str, int],
    desired: dict[str, int],
    unlock_ships: dict[str, list[str]],
    unlock_b9_ready: dict[str, bool],
) -> None:
    for _ in range(16):
        changed = False
        for rk, goal_lvl in list(desired.items()):
            if _eff_res_level(state, active_res_inc, rk) >= goal_lvl:
                continue
            r_info = catalog.RESEARCH_BY_ID.get(rk)
            if not isinstance(r_info, Mapping):
                continue
            parent_ships = unlock_ships.get(rk, ())
            parent_b9_ready = bool(unlock_b9_ready.get(rk, False))
            for req in r_info.get("requirements") or ():
                if not isinstance(req, Mapping):
                    continue
                if str(req.get("category") or "").strip().lower() != "research":
                    continue
                dep_lvl = max(0, _safe_strategy_int(req.get("level"), 0))
                if dep_lvl <= 0:
                    continue
                dep_slug = str(req.get("slug") or req.get("name") or "").strip()
                dep_info = catalog.resolve_unit("research", dep_slug)
                dep_rk = str(dep_info.get("id") or "") if isinstance(dep_info, Mapping) else ""
                if dep_rk not in catalog.RESEARCH_BY_ID:
                    continue
                if dep_lvl > desired.get(dep_rk, 0):
                    desired[dep_rk] = dep_lvl
                    changed = True
                if parent_ships and _eff_res_level(state, active_res_inc, dep_rk) < dep_lvl:
                    dep_ships = unlock_ships.setdefault(dep_rk, [])
                    for sname in parent_ships:
                        if sname not in dep_ships:
                            dep_ships.append(sname)
                            changed = True
                if (
                    parent_b9_ready
                    and _eff_res_level(state, active_res_inc, dep_rk) < dep_lvl
                    and not unlock_b9_ready.get(dep_rk, False)
                ):
                    unlock_b9_ready[dep_rk] = True
                    changed = True
        if not changed:
            break


def _build_fleet_research_candidate(
    *,
    state: GameState,
    active: PlanetState,
    lab_planet: PlanetState,
    lab_coords_s: str,
    config: StrategyConfig,
    rk: str,
    goal_lvl: int,
    active_res_inc: Mapping[str, int],
    existing_research_ids: set[int],
    research_offers: Mapping[str, ResearchOffer],
    research_queue_busy: bool,
    ai_p1_research_blocking: bool,
    unlock_ships: Mapping[str, list[str]],
    unlock_b9_ready: Mapping[str, bool],
) -> tuple[bool, int, int, float, int, PlannedAction] | None:
    if rk not in catalog.RESEARCH_BY_ID:
        return None
    live_lvl = max(0, _safe_strategy_int((state.researches or {}).get(rk, 0), 0))
    if live_lvl + active_res_inc.get(rk, 0) >= goal_lvl:
        return None
    rid_num = int(rk[1:])
    if rid_num in existing_research_ids:
        return None

    unmet = catalog.missing_prerequisites("research", rk, buildings=lab_planet.buildings, researches=state.researches)
    if any(m.get("category") == "research" for m in unmet):
        return None

    next_lvl = live_lvl + 1
    r_info = catalog.RESEARCH_BY_ID[rk]
    r_name = str(r_info.get("name") or rk)
    offer = research_offers.get(rk)

    if offer is not None and catalog.safe_optional_int(offer.target_level, None) == next_lvl:
        cost = offer.cost
        can_offer = bool(offer.can_research)
        raw_href = _safe_strategy_str(offer.research_href) or None
        tid = catalog.safe_optional_int(offer.research_id, rid_num)
    else:
        levels_raw = r_info.get("levels")
        lvl_row = levels_raw.get(next_lvl) if isinstance(levels_raw, Mapping) else None
        cost = _resources_from_dict(lvl_row.get("cost") if isinstance(lvl_row, Mapping) else {})
        can_offer = bool(config.allow_multi_planet and lab_planet.planet_id != active.planet_id and not cost.is_zero())
        raw_href = None
        tid = rid_num

    missing = lab_planet.resources.missing(cost)
    can_exec = (
        (not unmet)
        and (not research_queue_busy)
        and (not ai_p1_research_blocking)
        and can_offer
        and lab_planet.resources.can_afford(cost)
    )
    ships_unlocked = unlock_ships.get(rk, [])
    if ships_unlocked:
        ship_preview = ", ".join(ships_unlocked[:3])
        if len(ships_unlocked) > 3:
            ship_preview += f" (+{len(ships_unlocked) - 3})"
        base_reason = f"Fleet research (spare budget): {r_name} L{next_lvl}/{goal_lvl} — unlocks {ship_preview}"
    else:
        base_reason = f"Fleet research target (spare budget): {r_name} L{next_lvl}/{goal_lvl}"

    if unmet:
        first_req = unmet[0]
        cur_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("current", 0), 0))
        req_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("required", 0), 0))
        note = f" (requires {first_req['name']} {cur_s}/{req_s})"
    elif research_queue_busy:
        note = " (research slot busy)"
    elif ai_p1_research_blocking:
        note = " (holding Research Lab for AI-Planner research)"
    else:
        note = ""

    act = PlannedAction(
        priority="P2",
        kind="RESEARCH",
        planet_id=lab_planet.planet_id,
        planet_coords=lab_coords_s,
        target_id=tid,
        target_name=r_name,
        target_level=next_lvl,
        cost=cost,
        missing=missing,
        reason=f"{base_reason}{note}",
        executable_now=can_exec,
        action_href=raw_href if can_exec else None,
    )
    tier = 0 if unlock_b9_ready.get(rk, False) else (1 if ships_unlocked else 2)
    return can_exec, (1 if unmet else 0), tier, catalog.spice_equivalent(cost), rid_num, act


def _plan_fleet_research_actions(
    state: GameState,
    config: StrategyConfig,
    *,
    existing_plan: Sequence[PlannedAction] = (),
) -> list[PlannedAction]:
    """Plan P2 fleet/ship prerequisite and target `RESEARCH` actions funded by the Fleet Planner spare budget."""
    if not config.allow_research or not bool(getattr(config, "allow_fleet_research", True)):
        return []
    if state.tutorial and not state.tutorial.can_complete:
        return []
    active = state.active_planet()
    if active is None:
        return []

    candidate_planets = list(state.planets) if config.allow_multi_planet else [active]
    non_raid_planets = [p for p in candidate_planets if _norm_planet_type(p) != "raid"]
    if not non_raid_planets:
        return []

    lab_planet = max(
        non_raid_planets,
        key=lambda p: (p.building_level(RESEARCH_LAB), 1 if p.planet_id == active.planet_id else 0),
    )
    if lab_planet.building_level(RESEARCH_LAB) < 1:
        return []

    research_offers = _research_offer_map(state) if lab_planet.planet_id == active.planet_id else {}
    research_queue_busy = any(getattr(q, "category", "") == "research" for q in (state.active_queues or ())) or any(
        getattr(o, "active", False) for o in (state.research_offers or ())
    )
    active_res_inc = _active_research_increments(state)
    existing_research_ids, ai_p1_research_blocking = _analyze_existing_research_plan(existing_plan)

    desired, unlock_ships, unlock_b9_ready = _collect_shipyard_research_desires(
        state, config, non_raid_planets, active_res_inc
    )
    if not desired:
        return []
    _expand_transitive_research_prereqs(state, active_res_inc, desired, unlock_ships, unlock_b9_ready)

    ready_candidates: list[tuple[tuple[int, float, int], PlannedAction]] = []
    waiting_candidates: list[tuple[tuple[int, int, float, int], PlannedAction]] = []
    lab_coords_s = _safe_strategy_str(lab_planet.coords)
    for rk, goal_lvl in desired.items():
        built = _build_fleet_research_candidate(
            state=state,
            active=active,
            lab_planet=lab_planet,
            lab_coords_s=lab_coords_s,
            config=config,
            rk=rk,
            goal_lvl=goal_lvl,
            active_res_inc=active_res_inc,
            existing_research_ids=existing_research_ids,
            research_offers=research_offers,
            research_queue_busy=research_queue_busy,
            ai_p1_research_blocking=ai_p1_research_blocking,
            unlock_ships=unlock_ships,
            unlock_b9_ready=unlock_b9_ready,
        )
        if built is None:
            continue
        can_exec, blocked_rank, tier, cost_sp, rid_num, act = built
        if can_exec:
            ready_candidates.append(((tier, cost_sp, rid_num), act))
        else:
            waiting_candidates.append(((blocked_rank, tier, cost_sp, rid_num), act))

    ready_candidates.sort(key=lambda pair: pair[0])
    waiting_candidates.sort(key=lambda pair: pair[0])
    out_research: list[PlannedAction] = [act for _, act in ready_candidates]
    for _, wait_act in waiting_candidates[:3]:
        out_research.append(wait_act)
    return out_research
