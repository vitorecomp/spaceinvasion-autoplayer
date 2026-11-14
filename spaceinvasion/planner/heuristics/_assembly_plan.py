"""Pre-throttle heuristic plan builder shared by plan assembly and Blast furnace checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState, PlanetState
from spaceinvasion.planner.heuristics._assembly_guards import (
    append_hostile_escape_actions,
    append_negative_energy_guard,
    select_planets_and_abandon_actions,
)
from spaceinvasion.planner.heuristics._assembly_pre import (
    append_storage_overflow_guard,
    append_thermosolar_teardown_guard,
    append_warehouse_guards_and_cap_expansions,
)
from spaceinvasion.planner.heuristics._assembly_rank import plan_tutorial_or_economy
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _offer_map,
    _research_offer_map,
)
from spaceinvasion.planner.heuristics._queues import (
    _count_planet_building_queues,
    _effective_building_levels_and_energy,
    _max_build_slots,
    _queued_building_counts,
)
from spaceinvasion.planner.heuristics._tutorial_tasks import (
    _tutorial_colonize_task,
    _tutorial_fight_task,
    _tutorial_recycle_task,
    _tutorial_spy_task,
)
from spaceinvasion.planner.heuristics._types import PlannedAction
from spaceinvasion.planner.heuristics.fleet_missions import _plan_combat_and_raid_actions
from spaceinvasion.planner.heuristics.tutorial import _plan_tutorial_fleet_milestones


def _append_post_economy_phases(
    state: GameState,
    active: PlanetState,
    planets_to_check: Sequence[PlanetState],
    tutorial_actions: Sequence[PlannedAction],
    queued_counts: Mapping[int, int],
    *,
    active_queue_full: bool,
    cfg: StrategyConfig,
    scanned_systems: Mapping[str, float] | None,
    now_ts: float | None,
    include_combat: bool,
    priority_resource_need: Mapping[int, object] | None,
    plan: list[PlannedAction],
) -> None:
    if state.tutorial and state.tutorial.can_complete:
        return
    if state.tutorial is not None:
        tut_emitted_b = {
            (a.planet_id, int(a.target_id))
            for a in tutorial_actions
            if a.kind in ("BUILD", "WAIT_RESOURCES") and a.target_id is not None
        }
        tut_emitted_r = {
            f"r{int(a.target_id)}" for a in tutorial_actions if a.kind == "RESEARCH" and a.target_id is not None
        }
        plan.extend(
            _plan_tutorial_fleet_milestones(
                state,
                active,
                cfg,
                planets_to_check=planets_to_check,
                emitted_build_ids=tut_emitted_b,
                emitted_res_keys=tut_emitted_r,
            )
        )
    append_warehouse_guards_and_cap_expansions(
        state, active, planets_to_check, queued_counts, active_queue_full=active_queue_full, cfg=cfg, plan=plan
    )
    if include_combat and (
        not tutorial_actions
        or _tutorial_fight_task(state) is not None
        or _tutorial_spy_task(state) is not None
        or _tutorial_recycle_task(state) is not None
        or _tutorial_colonize_task(state) is not None
        or cfg.allow_colonize
        or cfg.allow_galaxy_scan
        or getattr(cfg, "allow_transport", False)
    ):
        plan.extend(
            _plan_combat_and_raid_actions(
                state,
                active,
                cfg,
                scanned_systems=scanned_systems,
                now_ts=now_ts,
                existing_plan=plan,
                priority_resource_need=priority_resource_need,
            )
        )


def _build_pre_throttle_plan(
    state: GameState,
    *,
    warn_ratio: float,
    cfg: StrategyConfig,
    scanned_systems: Mapping[str, float] | None = None,
    now_ts: float | None = None,
    include_combat: bool = True,
    priority_resource_need: Mapping[int, object] | None = None,
) -> tuple[list[PlannedAction], list[PlanetState]]:
    plan: list[PlannedAction] = []
    active = state.active_planet()
    if active is None:
        return plan, []

    append_hostile_escape_actions(state, active, cfg, plan)
    queued_counts = _queued_building_counts(state, active, is_active_planet=True)
    active_queue_full = _count_planet_building_queues(state, active, is_active_planet=True) >= _max_build_slots(
        active, state
    )
    research_offers = _research_offer_map(state)
    research_queue_busy = any(q.category == "research" for q in state.active_queues) or any(
        o.active for o in state.research_offers
    )
    planets_to_check = select_planets_and_abandon_actions(state, active, cfg, plan)

    for planet in planets_to_check:
        is_active = planet.planet_id == active.planet_id
        offers = _offer_map(planet)
        eff_buildings, projected_net = _effective_building_levels_and_energy(state, planet, is_active_planet=is_active)
        queue_full = _count_planet_building_queues(state, planet, is_active_planet=is_active) >= _max_build_slots(
            planet, state
        )
        allow_remote = cfg.allow_multi_planet and not is_active
        ptype_now = _norm_planet_type(planet)
        append_negative_energy_guard(
            state,
            planet,
            offers,
            eff_buildings,
            ptype_now=ptype_now,
            queue_full=queue_full,
            allow_remote=allow_remote,
            cfg=cfg,
            plan=plan,
        )
        append_thermosolar_teardown_guard(
            state,
            planet,
            offers,
            eff_buildings,
            projected_net=projected_net,
            ptype_now=ptype_now,
            is_active=is_active,
            queue_full=queue_full,
            allow_remote=allow_remote,
            cfg=cfg,
            plan=plan,
        )
        append_storage_overflow_guard(
            state,
            planet,
            offers,
            eff_buildings,
            ptype_now=ptype_now,
            warn_ratio=warn_ratio,
            queue_full=queue_full,
            allow_remote=allow_remote,
            plan=plan,
        )

    tutorial_actions = plan_tutorial_or_economy(
        state,
        active,
        planets_to_check,
        research_offers=research_offers,
        queued_counts=queued_counts,
        active_queue_full=active_queue_full,
        research_queue_busy=research_queue_busy,
        cfg=cfg,
        plan=plan,
    )
    _append_post_economy_phases(
        state,
        active,
        planets_to_check,
        tutorial_actions,
        queued_counts,
        active_queue_full=active_queue_full,
        cfg=cfg,
        scanned_systems=scanned_systems,
        now_ts=now_ts,
        include_combat=include_combat,
        priority_resource_need=priority_resource_need,
        plan=plan,
    )
    return plan, planets_to_check
