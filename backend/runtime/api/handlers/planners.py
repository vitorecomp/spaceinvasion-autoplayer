"""Non-browser Config Optimizer, AI-Planner, Fleet Planner, and Combat Simulator `@command` handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from backend.combat.simulator import simulate_combat_matchup
from backend.core.model import GameState
from backend.runtime.api.handlers._bridge_proto import BridgeApiFacade
from backend.runtime.api.handlers._planners_helpers import (
    parse_horizon_years,
    parse_sim_target_coords,
    validate_ai_get_payload,
    validate_ai_run_payload,
    validate_co_run_payload,
    validate_co_update_planet_payload,
    validate_field_conflict_payload,
    validate_fleet_planner_payload,
)
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError, FieldBounds
from backend.runtime.bridge._helpers import GalaxyScanInProgressError, sanitize_json_dict
from backend.runtime.bridge._helpers_validation import parse_sim_count_map, validate_strict_float


class _UnsetType:
    """Deterministic sentinel distinguishing omitted JSON keys from explicit `null`."""

    def __repr__(self) -> str:
        return "UNSET"


_UNSET: Final[_UnsetType] = _UnsetType()


@dataclass(frozen=True)
class PlannerJobsStatusParams:
    """Parameters for inspecting background Config Optimizer and AI-Planner jobs."""


@dataclass(frozen=True)
class ConfigOptimizerGetParams:
    """Parameters for reading active Config Optimizer state and recent run history."""

    limit: int = field(default=20, metadata={"bounds": FieldBounds(min_val=1, max_val=200)})


@dataclass(frozen=True)
class ConfigOptimizerSetSpecializationsParams:
    """Parameters for locking or clearing per-planet Config Optimizer specializations."""

    planet_specializations: object = None
    replace: object = False


@dataclass(frozen=True)
class ConfigOptimizerUpdatePlanetParams:
    """Parameters for manually overriding target buildings on a single Config Optimizer planet."""

    target_buildings: object = None
    slot: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=100)})
    planet_id: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=-1000, max_val=2**63 - 1)})
    coords: object = None
    fields_max_override: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=2000)})
    locked_specialization: object = None
    planet_name: object = None
    lock_for_ga: object = True
    auto_warehouses: object = False
    auto_replan_ai: object = True


@dataclass(frozen=True)
class ConfigOptimizerYearlyProjectionParams:
    """Parameters for computing multi-year ships/week ramp projection from active Config Optimizer."""

    horizon_years: object = 5


@dataclass(frozen=True)
class ConfigOptimizerRunParams:
    """Parameters for starting or synchronously running a Config Optimizer job."""

    params: object = None
    auto_replan_ai: object = True
    sync: object = False
    trigger_source: object = "manual"


@dataclass(frozen=True)
class AiPlannerGetParams:
    """Parameters for querying active AI-Planner timeline, graph, and adaptive execution status."""

    window_size: int = field(default=40, metadata={"bounds": FieldBounds(min_val=5, max_val=200)})
    week: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=520)})
    planet_id: object = None
    page: int = field(default=1, metadata={"bounds": FieldBounds(min_val=1, max_val=100_000)})
    page_size: int = field(default=50, metadata={"bounds": FieldBounds(min_val=5, max_val=500)})
    search: object = ""
    limit: int = field(default=20, metadata={"bounds": FieldBounds(min_val=1, max_val=200)})


@dataclass(frozen=True)
class AiPlannerRunParams:
    """Parameters for starting or synchronously running an AI-Planner job."""

    params: object = None
    sync: object = False
    refresh: object = False
    auto_run_config_if_missing: object = True
    trigger_source: object = "manual"


@dataclass(frozen=True)
class ResolveFieldConflictParams:
    """Parameters for resolving a pending field-capacity conflict (`demolish` or `keep_reserve`)."""

    conflict_key: object = None
    decision: object = None
    action: object = None
    sync: object = False
    candidate_building_key: object = None
    building_key: object = None


@dataclass(frozen=True)
class FleetPlannerGetParams:
    """Parameters for evaluating the Fleet & Attack Planner graph and spare ship budget."""

    config: object = None


@dataclass(frozen=True)
class SimulateCombatParams:
    """Parameters for running the 1:1 zero-rapidfire SpaceInvasion combat simulator."""

    attacker_ships: object = None
    defender_ships: object = None
    defender_defense: object = None
    defender_researches: object = None
    target_resources: object = None
    target_coords: object = None
    safety_margin: object = _UNSET


def _handle_planner_jobs_status(bridge: BridgeApiFacade, _params: PlannerJobsStatusParams) -> dict[str, object]:
    """Return status snapshots for Config Optimizer, AI-Planner jobs, and pending field conflicts."""
    snap = bridge.get_planner_jobs_status()
    return sanitize_json_dict({"ok": True, "command": "planner_jobs_status", **snap})


def _handle_config_optimizer_get(bridge: BridgeApiFacade, params: ConfigOptimizerGetParams) -> dict[str, object]:
    """Return active Config Optimizer plan, run history, specializations, and defaults."""
    res = bridge.config_optimizer_get(limit=params.limit)
    return sanitize_json_dict({"ok": True, "command": "config_optimizer_get", **res})


def _handle_config_optimizer_set_specializations(
    bridge: BridgeApiFacade,
    params: ConfigOptimizerSetSpecializationsParams,
) -> dict[str, object]:
    """Persist per-planet specialization locks for Config Optimizer runs."""
    raw_specs = params.planet_specializations
    if not isinstance(raw_specs, Mapping):
        raise ApiRequestError(
            "'config_optimizer_set_specializations' field 'planet_specializations' must be a dictionary."
        )
    if not isinstance(params.replace, bool):
        raise ApiRequestError("'config_optimizer_set_specializations' field 'replace' must be a boolean.")

    if params.replace:
        with bridge.ctx.state_lock:
            bridge.ctx.planet_specializations_mem.clear()
            if bridge.ctx.db is not None:
                bridge.ctx.db.update_config_optimizer_specializations({}, replace=True)

    res = bridge.config_optimizer_set_specializations(dict(raw_specs))
    bridge.publish_snapshot()
    return sanitize_json_dict({"ok": True, "command": "config_optimizer_set_specializations", **res})


def _handle_config_optimizer_update_planet(
    bridge: BridgeApiFacade,
    params: ConfigOptimizerUpdatePlanetParams,
) -> dict[str, object]:
    """Apply manual target building overrides to a single planet in the Config Optimizer plan."""
    normalized = validate_co_update_planet_payload(
        target_buildings=params.target_buildings,
        slot=params.slot,
        planet_id=params.planet_id,
        coords=params.coords,
        fields_max_override=params.fields_max_override,
        locked_specialization=params.locked_specialization,
        planet_name=params.planet_name,
        lock_for_ga=params.lock_for_ga,
        auto_warehouses=params.auto_warehouses,
        auto_replan_ai=params.auto_replan_ai,
    )
    res = bridge.config_optimizer_update_planet(normalized)
    bridge.publish_snapshot()
    return sanitize_json_dict({"ok": True, "command": "config_optimizer_update_planet", **res})


def _handle_config_optimizer_yearly_projection(
    bridge: BridgeApiFacade,
    params: ConfigOptimizerYearlyProjectionParams,
) -> dict[str, object]:
    """Compute multi-year ships/week ramp projection from the active Config Optimizer plan."""
    horizon = parse_horizon_years(params.horizon_years)
    res = bridge.config_optimizer_yearly_projection({"horizon_years": horizon})
    return sanitize_json_dict({"ok": True, "command": "config_optimizer_yearly_projection", **res})


def _handle_config_optimizer_run(bridge: BridgeApiFacade, params: ConfigOptimizerRunParams) -> dict[str, object]:
    """Start or synchronously execute a Config Optimizer run."""
    normalized = validate_co_run_payload(
        raw_params=params.params,
        auto_replan_ai=params.auto_replan_ai,
        sync=params.sync,
        trigger_source=params.trigger_source,
    )
    res = bridge.config_optimizer_run(normalized)
    bridge.publish_snapshot()
    return sanitize_json_dict({"ok": True, "command": "config_optimizer_run", **res})


def _handle_ai_planner_get(bridge: BridgeApiFacade, params: AiPlannerGetParams) -> dict[str, object]:
    """Return active AI-Planner graph, paginated steps, telemetry, and adaptive execution status."""
    normalized = validate_ai_get_payload(
        window_size=params.window_size,
        week=params.week,
        planet_id=params.planet_id,
        page=params.page,
        page_size=params.page_size,
        search=params.search,
        limit=params.limit,
    )
    res = bridge.ai_planner_get(normalized)
    return sanitize_json_dict({"ok": True, "command": "ai_planner_get", **res})


def _handle_ai_planner_run(bridge: BridgeApiFacade, params: AiPlannerRunParams) -> dict[str, object]:
    """Start or synchronously execute an AI-Planner trajectory optimization run."""
    normalized = validate_ai_run_payload(
        raw_params=params.params,
        sync=params.sync,
        refresh=params.refresh,
        auto_run_config_if_missing=params.auto_run_config_if_missing,
        trigger_source=params.trigger_source,
    )
    if normalized["refresh"] and bridge.is_galaxy_scan_blocking_locked():
        raise GalaxyScanInProgressError(bridge.galaxy_scan_busy_error_message())
    res = bridge.ai_planner_run(normalized)
    bridge.publish_snapshot()
    return sanitize_json_dict({"ok": True, "command": "ai_planner_run", **res})


def _handle_resolve_field_conflict(bridge: BridgeApiFacade, params: ResolveFieldConflictParams) -> dict[str, object]:
    """Resolve a pending colony field-capacity conflict via demolition or reserved floor."""
    normalized = validate_field_conflict_payload(
        conflict_key=params.conflict_key,
        decision=params.decision,
        action=params.action,
        sync=params.sync,
        candidate_building_key=params.candidate_building_key,
        building_key=params.building_key,
    )
    res = bridge.resolve_field_conflict(normalized)
    bridge.publish_snapshot()
    return sanitize_json_dict({"ok": True, "command": "resolve_field_conflict", **res})


def _handle_fleet_planner_get(bridge: BridgeApiFacade, params: FleetPlannerGetParams) -> dict[str, object]:
    """Return Fleet & Attack Planner DAG, slot budgets, attack telemetry, and spare ship budget."""
    normalized = validate_fleet_planner_payload(params.config)
    res = bridge.fleet_planner_get(normalized)
    return sanitize_json_dict({"ok": True, "command": "fleet_planner_get", **res})


def _handle_simulate_combat(bridge: BridgeApiFacade, params: SimulateCombatParams) -> dict[str, object]:
    """Run the 1:1 zero-rapidfire SpaceInvasion Classic combat matchup simulator."""
    atk_ships = parse_sim_count_map("attacker_ships", params.attacker_ships)
    def_ships = parse_sim_count_map("defender_ships", params.defender_ships)
    def_defense = parse_sim_count_map("defender_defense", params.defender_defense)
    def_researches = parse_sim_count_map("defender_researches", params.defender_researches)
    tgt_resources = parse_sim_count_map("target_resources", params.target_resources, max_val=1_000_000_000_000)
    tgt_coords = parse_sim_target_coords(params.target_coords)
    with bridge.ctx.state_lock:
        state_for_sim = bridge.ctx.last_state or GameState(
            universe_id=0, user_id=0, nickname="simulator", timestamp=0.0, planets=()
        )
        default_margin = float(bridge.ctx.strategy_config.combat_safety_margin)
    margin = (
        validate_strict_float("safety_margin", params.safety_margin, min_val=1.0, max_val=1000.0)
        if params.safety_margin is not _UNSET
        else default_margin
    )
    sim = simulate_combat_matchup(
        state_for_sim,
        attacker_ships=atk_ships,
        defender_ships=def_ships,
        defender_defense=def_defense,
        defender_researches=def_researches,
        target_resources=tgt_resources,
        target_coords=tgt_coords,
        safety_margin=margin,
    )
    return sanitize_json_dict({"ok": True, "command": "simulate_combat", "simulation": sim})


def register_planner_handlers(registry: CommandRegistry[BridgeApiFacade]) -> None:
    """Register all 11 non-browser planner and combat simulator commands."""
    registry.command(
        "planner_jobs_status",
        needs_browser=False,
        mutates=False,
        summary="Inspect planner background jobs and field conflicts.",
        params=PlannerJobsStatusParams,
    )(_handle_planner_jobs_status)
    registry.command(
        "config_optimizer_get",
        needs_browser=False,
        mutates=False,
        summary="Read active Config Optimizer result and run history.",
        params=ConfigOptimizerGetParams,
    )(_handle_config_optimizer_get)
    registry.command(
        "config_optimizer_set_specializations",
        needs_browser=False,
        mutates=True,
        summary="Update or replace per-planet Config Optimizer specializations.",
        params=ConfigOptimizerSetSpecializationsParams,
    )(_handle_config_optimizer_set_specializations)
    registry.command(
        "config_optimizer_update_planet",
        needs_browser=False,
        mutates=True,
        summary="Apply manual target building overrides on a planet.",
        params=ConfigOptimizerUpdatePlanetParams,
    )(_handle_config_optimizer_update_planet)
    registry.command(
        "config_optimizer_yearly_projection",
        needs_browser=False,
        mutates=False,
        summary="Compute multi-year ships/week ramp projection.",
        params=ConfigOptimizerYearlyProjectionParams,
    )(_handle_config_optimizer_yearly_projection)
    registry.command(
        "config_optimizer_run",
        needs_browser=False,
        mutates=True,
        summary="Start or synchronously execute a Config Optimizer run.",
        params=ConfigOptimizerRunParams,
    )(_handle_config_optimizer_run)
    registry.command(
        "ai_planner_get",
        needs_browser=False,
        mutates=False,
        summary="Read active AI-Planner graph, steps, and adaptive status.",
        params=AiPlannerGetParams,
    )(_handle_ai_planner_get)
    registry.command(
        "ai_planner_run",
        needs_browser=False,
        mutates=True,
        summary="Start or synchronously execute an AI-Planner run.",
        params=AiPlannerRunParams,
    )(_handle_ai_planner_run)
    registry.command(
        "resolve_field_conflict",
        needs_browser=False,
        mutates=True,
        summary="Resolve a pending colony field-capacity conflict.",
        params=ResolveFieldConflictParams,
    )(_handle_resolve_field_conflict)
    registry.command(
        "fleet_planner_get",
        needs_browser=False,
        mutates=False,
        summary="Evaluate Fleet & Attack Planner graph and spare ship budget.",
        params=FleetPlannerGetParams,
    )(_handle_fleet_planner_get)
    registry.command(
        "simulate_combat",
        needs_browser=False,
        mutates=False,
        summary="Run the 1:1 zero-rapidfire combat simulator.",
        params=SimulateCombatParams,
    )(_handle_simulate_combat)
