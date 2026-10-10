"""Top-level P0-P3 heuristic plan assembly (`plan_actions` & `plan_fleet_and_attack_actions`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState
from spaceinvasion.planner.eta import attach_action_etas as _attach_etas_impl
from spaceinvasion.planner.heuristics._actions import _plan_transmitter_action
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
from spaceinvasion.planner.heuristics._assembly_rank import (
    _PRIORITY_RANK,
    dedupe_actions,
    fleet_subrank,
    plan_band_subrank,
    plan_tutorial_or_economy,
)
from spaceinvasion.planner.heuristics._cargo_helpers import (
    _adaptive_execution_enabled,
    _in_flight_ship_counts,
    compute_infra_transport_targets,
)
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _offer_map,
    _research_offer_map,
    _resolve_storage_warn_ratio,
    _ship_offer_map,
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
from spaceinvasion.planner.heuristics.blast_furnace import _plan_blast_furnace_throttle
from spaceinvasion.planner.heuristics.fleet_missions import _plan_combat_and_raid_actions
from spaceinvasion.planner.heuristics.shipyard_budgets import (
    _plan_fleet_research_actions,
    _plan_planet_shipyard_targets,
    filter_shipyard_actions_by_spare_budget,
)
from spaceinvasion.planner.heuristics.tutorial import _plan_tutorial_fleet_milestones


def attach_action_etas(state: GameState, actions: Sequence[PlannedAction]) -> list[PlannedAction]:
    """Attach computed ETA fields to heuristic `PlannedAction` entries."""
    out: list[PlannedAction] = _attach_etas_impl(state, list(actions))
    return out


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


def plan_fleet_and_attack_actions(
    state: GameState,
    *,
    config: StrategyConfig | None = None,
    scanned_systems: Mapping[str, float] | None = None,
    now_ts: float | None = None,
    raid_stats: Mapping[str, object] | None = None,
    transmitter_status: Mapping[str, object] | None = None,
    existing_plan: Sequence[PlannedAction] = (),
    priority_resource_need: Mapping[int, object] | None = None,
    spare_budget: Mapping[str, object] | None = None,
    transport_demand: Mapping[str, object] | None = None,
) -> list[PlannedAction]:
    """Generate the tactical Fleet / Attack Planner action layer (Tab 4 & hybrid AI-Planner layer)."""
    cfg = config or StrategyConfig()
    state_for_plan = replace(state, raid_stats=dict(raid_stats)) if isinstance(raid_stats, Mapping) else state
    active = state_for_plan.active_planet()
    if active is None:
        return []

    actions: list[PlannedAction] = []
    append_hostile_escape_actions(state_for_plan, active, cfg, actions)
    combined_context = [*existing_plan, *actions]
    actions.extend(
        _plan_combat_and_raid_actions(
            state_for_plan,
            active,
            cfg,
            scanned_systems=scanned_systems,
            now_ts=now_ts,
            existing_plan=combined_context,
            priority_resource_need=priority_resource_need,
        )
    )
    actions.extend(_plan_fleet_research_actions(state_for_plan, cfg, existing_plan=combined_context))

    planets_for_ships = list(state_for_plan.planets) if cfg.allow_multi_planet else [active]
    planets_for_ships.sort(key=lambda p: 0 if p.planet_id == active.planet_id else 1)
    flights_now: Mapping[str, object] | None = None
    infra_targets: Mapping[int, Mapping[str, int]] | None = None
    if _adaptive_execution_enabled(cfg):
        flights_now = _in_flight_ship_counts(state_for_plan)
        infra_targets = compute_infra_transport_targets(
            state_for_plan, cfg, transport_demand, in_flight_counts=flights_now
        )
    for planet in planets_for_ships:
        actions.extend(
            _plan_planet_shipyard_targets(
                state_for_plan,
                planet,
                _ship_offer_map(planet),
                cfg,
                transport_demand=transport_demand if _adaptive_execution_enabled(cfg) else None,
                in_flight_counts=flights_now,
                infra_transport_targets=infra_targets,
            )
        )

    tm_act = _plan_transmitter_action(state_for_plan, cfg, transmitter_status=transmitter_status)
    if tm_act is not None:
        actions.append(tm_act)
    if spare_budget is not None:
        actions = filter_shipyard_actions_by_spare_budget(state_for_plan, actions, spare_budget)

    actions.sort(
        key=lambda a: (
            _PRIORITY_RANK.get(a.priority, 9),
            -1 if a.kind == "ALERT_HOSTILE" else (0 if a.executable_now else 1),
            fleet_subrank(a),
        )
    )
    return attach_action_etas(state, dedupe_actions(actions))


def plan_actions(
    state: GameState,
    *,
    storage_warn_ratio: float | None = None,
    config: StrategyConfig | None = None,
    scanned_systems: Mapping[str, float] | None = None,
    throttle_cooldowns: Mapping[int, float] | None = None,
    now_ts: float | None = None,
    raid_stats: Mapping[str, object] | None = None,
    transmitter_status: Mapping[str, object] | None = None,
    priority_resource_need: Mapping[int, object] | None = None,
) -> list[PlannedAction]:
    """Generate an ETA-annotated action plan via the configured planner (`priority` or `ai_planner`)."""
    cfg = config or StrategyConfig()
    warn_ratio = _resolve_storage_warn_ratio(cfg, storage_warn_ratio)
    state_for_plan = replace(state, raid_stats=dict(raid_stats)) if isinstance(raid_stats, Mapping) else state
    tm_act = _plan_transmitter_action(state_for_plan, cfg, transmitter_status=transmitter_status)

    plan, planets_to_check = _build_pre_throttle_plan(
        state_for_plan,
        warn_ratio=warn_ratio,
        cfg=cfg,
        scanned_systems=scanned_systems,
        now_ts=now_ts,
        include_combat=True,
        priority_resource_need=priority_resource_need,
    )
    if tm_act is not None:
        plan.append(tm_act)
    if not planets_to_check:
        return attach_action_etas(state, plan)

    if cfg.auto_throttle_blast_furnace and not cfg.merchant_mode:
        cooldown_map = throttle_cooldowns or {}
        for planet in planets_to_check:
            throttle_act = _plan_blast_furnace_throttle(
                state,
                planet,
                plan,
                cfg,
                last_throttle_ts=cooldown_map.get(planet.planet_id),
                now_ts=now_ts,
                storage_warn_ratio=warn_ratio,
                pre_throttle_plan_fn=_build_pre_throttle_plan,
            )
            if throttle_act is not None:
                plan.append(throttle_act)

    plan.sort(
        key=lambda a: (
            _PRIORITY_RANK.get(a.priority, 9),
            0 if a.executable_now else 1,
            plan_band_subrank(a),
        )
    )
    return attach_action_etas(state, dedupe_actions(plan))
