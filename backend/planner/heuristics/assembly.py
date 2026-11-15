"""Top-level P0-P3 heuristic plan assembly (`plan_actions` & `plan_fleet_and_attack_actions`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from backend.core.config import StrategyConfig
from backend.core.model import GameState
from backend.planner.eta import attach_action_etas as _attach_etas_impl
from backend.planner.heuristics._actions import _plan_transmitter_action
from backend.planner.heuristics._assembly_guards import append_hostile_escape_actions
from backend.planner.heuristics._assembly_plan import _build_pre_throttle_plan
from backend.planner.heuristics._assembly_rank import (
    _PRIORITY_RANK,
    dedupe_actions,
    fleet_subrank,
    plan_band_subrank,
)
from backend.planner.heuristics._cargo_helpers import (
    _adaptive_execution_enabled,
    _in_flight_ship_counts,
    compute_infra_transport_targets,
)
from backend.planner.heuristics._common import _resolve_storage_warn_ratio, _ship_offer_map
from backend.planner.heuristics._types import PlannedAction
from backend.planner.heuristics.blast_furnace import _plan_blast_furnace_throttle
from backend.planner.heuristics.fleet_missions import _plan_combat_and_raid_actions
from backend.planner.heuristics.shipyard_budgets import (
    _plan_fleet_research_actions,
    _plan_planet_shipyard_targets,
    filter_shipyard_actions_by_spare_budget,
)


def attach_action_etas(state: GameState, actions: Sequence[PlannedAction]) -> list[PlannedAction]:
    """Attach computed ETA fields to heuristic `PlannedAction` entries."""
    return [a for a in _attach_etas_impl(state, list(actions)) if isinstance(a, PlannedAction)]


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
