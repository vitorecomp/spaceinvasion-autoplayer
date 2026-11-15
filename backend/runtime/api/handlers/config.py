"""Non-browser strategy configuration and custom planet rename `@command` handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from backend.core.config import StrategyConfig, save_strategy_config, strategy_config_from_dict
from backend.core.model import GameState
from backend.planner.graph.heuristics import build_action_graph
from backend.runtime.api.handlers._bridge_proto import BridgeApiFacade
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError, FieldBounds
from backend.runtime.bridge._helpers import (
    parse_command_int,
    safe_hourly_budget,
    sanitize_json_dict,
)
from backend.runtime.bridge._helpers_sig import redact_action_graph_strings
from backend.runtime.bridge._helpers_validation import validate_config_patch


@dataclass(frozen=True)
class ConfigParams:
    """Parameters for reading the active `StrategyConfig`."""


@dataclass(frozen=True)
class UpdateConfigParams:
    """Parameters for patching and persisting `StrategyConfig`."""

    config: object = None
    include_action_graph: object = None
    planet_id: int | None = field(
        default=None,
        metadata={"bounds": FieldBounds(min_val=1, max_val=2**63 - 1)},
    )


@dataclass(frozen=True)
class RenamePlanetParams:
    """Parameters for renaming a single planet or batch-updating custom planet names."""

    planet_id: object = None
    name: object = ""
    planet_names: object = None


def _galaxy_scan_config_key(cfg: StrategyConfig) -> tuple[object, ...]:
    return (
        cfg.allow_galaxy_scan,
        cfg.galaxy_scan_block_other_actions,
        cfg.galaxy_sweep_pages,
        cfg.galaxy_scan_explore_first,
        cfg.galaxy_scan_refresh_pct,
        cfg.galaxy_scan_all_galaxies,
        cfg.galaxy_scan_near_weight,
        cfg.galaxy_scan_attack_weight,
    )


def _merge_config_patch(prev_cfg: StrategyConfig, raw_cfg: Mapping[str, object]) -> StrategyConfig:
    merged: dict[str, object] = {**asdict(prev_cfg), **dict(raw_cfg)}
    raw_ship_targets = raw_cfg.get("shipyard_targets")
    if isinstance(raw_ship_targets, Mapping):
        merged["shipyard_targets"] = {**dict(prev_cfg.shipyard_targets), **dict(raw_ship_targets)}
    raw_fleet_res = raw_cfg.get("fleet_research_targets")
    if isinstance(raw_fleet_res, Mapping):
        merged["fleet_research_targets"] = {**dict(prev_cfg.fleet_research_targets), **dict(raw_fleet_res)}
    return strategy_config_from_dict(merged)


def _build_optional_action_graph(
    bridge: BridgeApiFacade,
    new_cfg: StrategyConfig,
    planet_id: int | None,
) -> dict[str, object]:
    ag_state = bridge.attach_raid_stats(bridge.ctx.last_state) if bridge.ctx.last_state is not None else None
    if ag_state is not None and planet_id is not None:
        bridge.ctx.resolve_planet(ag_state, planet_id)
    if ag_state is not None:
        ag_actions, ag_opt = bridge.plan_with_summary(ag_state, config=new_cfg)
    else:
        ag_actions, ag_opt = [], None
    graph = build_action_graph(
        ag_state,
        ag_actions,
        effective_config=new_cfg,
        active_config=new_cfg,
        simulated=False,
        optimizer_summary=ag_opt,
        planet_filter=planet_id,
        sanitize=False,
    )
    return redact_action_graph_strings(graph)


def _handle_config(bridge: BridgeApiFacade, _params: ConfigParams) -> dict[str, object]:
    """Return the active `StrategyConfig` snapshot."""
    with bridge.ctx.state_lock:
        cfg_dict = asdict(bridge.ctx.strategy_config)
    return sanitize_json_dict({"ok": True, "command": "config", "config": cfg_dict})


def _handle_update_config(bridge: BridgeApiFacade, params: UpdateConfigParams) -> dict[str, object]:
    """Validate, merge, persist, and propagate a `StrategyConfig` patch."""
    raw_cfg = params.config
    if not isinstance(raw_cfg, Mapping):
        raise ApiRequestError("'update_config' requires a 'config' dictionary.", field="config")
    validate_config_patch(raw_cfg, strict_bools=False)

    inc_ag = False
    if params.include_action_graph is not None:
        if not isinstance(params.include_action_graph, bool):
            raise ApiRequestError(
                "'update_config' field 'include_action_graph' must be a boolean.",
                field="include_action_graph",
            )
        inc_ag = params.include_action_graph

    with bridge.ctx.state_lock:
        prev_cfg = bridge.ctx.strategy_config
        new_cfg = _merge_config_patch(prev_cfg, raw_cfg)
        if bridge.ctx.config_path is not None:
            save_strategy_config(new_cfg, bridge.ctx.config_path)
        bridge.ctx.strategy_config = new_cfg
        if not new_cfg.allow_galaxy_scan:
            bridge.ctx.sweep_wake_event.set()
        if bridge.ctx.db is not None:
            bridge.ctx.db.save_strategy_config_snapshot(asdict(new_cfg))
        bridge.ctx.invalidate_dashboard_cache()
        if _galaxy_scan_config_key(new_cfg) != _galaxy_scan_config_key(prev_cfg):
            bridge.ctx.smart_sweep_summary_ts = 0.0
        bridge.lifecycle.persist_runtime_state()
        bridge.sync_executors_from_config()
        if "max_actions_per_hour" in raw_cfg:
            budget = safe_hourly_budget(new_cfg.max_actions_per_hour, 60)
            for executor in (bridge.ctx.live_executor, bridge.ctx.dry_executor):
                executor.max_actions_per_hour = budget
                executor.enforce_hourly_cap = True
        bridge.ctx.sync_page_limiter_from_config(new_cfg)
        upd_resp: dict[str, object] = {
            "ok": True,
            "command": "update_config",
            "config": asdict(new_cfg),
        }
        if inc_ag:
            upd_resp["action_graph"] = _build_optional_action_graph(bridge, new_cfg, params.planet_id)

    if not new_cfg.allow_galaxy_scan:
        bridge.abort_auto_galaxy_sweep_if_disabled()
    bridge.publish_snapshot()
    return sanitize_json_dict(upd_resp)


def _resolve_state_locked(bridge: BridgeApiFacade) -> GameState | None:
    if bridge.ctx.last_state is not None:
        return bridge.ctx.last_state
    if bridge.ctx.db is not None:
        loaded = bridge.ctx.db.load_latest_game_state()
        return loaded if isinstance(loaded, GameState) else None
    return None


def _rename_planets_batch(bridge: BridgeApiFacade, raw_batch: object) -> dict[str, object]:
    if not isinstance(raw_batch, Mapping) or not raw_batch:
        raise ApiRequestError("'rename_planet' field 'planet_names' must be a non-empty dictionary.")
    with bridge.ctx.state_lock:
        snap_state = _resolve_state_locked(bridge)
        if snap_state is None or not snap_state.planets:
            raise ApiRequestError("No game state snapshot available yet; cannot validate planet_id.")
        planets_by_id = {int(p.planet_id): p for p in snap_state.planets}
        validated_updates: dict[int, object] = {}
        for raw_pid, raw_nm in raw_batch.items():
            pid_i = parse_command_int("planet_id", raw_pid, min_val=1, max_val=2**63 - 1)
            if pid_i is None or pid_i not in planets_by_id:
                raise ApiRequestError(f"Planet {raw_pid} does not belong to this empire.")
            if raw_nm is not None and not isinstance(raw_nm, str):
                raise ApiRequestError("'rename_planet' planet names must be strings.")
            validated_updates[int(pid_i)] = "" if raw_nm is None else raw_nm
        updated_names = bridge.names.set_custom_planet_names_batch_locked(validated_updates)
        updated_planets: list[dict[str, object]] = []
        for pid_i in validated_updates:
            p_obj = planets_by_id[pid_i]
            g_name = str(p_obj.name or "")
            c_name = updated_names.get(str(pid_i)) or None
            updated_planets.append(
                {
                    "planet_id": pid_i,
                    "coords": str(p_obj.coords or ""),
                    "name": c_name or g_name,
                    "game_name": g_name,
                    "custom_name": c_name,
                }
            )

    bridge.publish_snapshot()
    first_p = updated_planets[0] if updated_planets else None
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "rename_planet",
            "planet_id": first_p["planet_id"] if first_p else None,
            "name": first_p["name"] if first_p else "",
            "custom_name": first_p["custom_name"] if first_p else None,
            "game_name": first_p["game_name"] if first_p else "",
            "custom_planet_names": updated_names,
            "planet": first_p,
            "updated": updated_planets,
        }
    )


def _handle_rename_planet(bridge: BridgeApiFacade, params: RenamePlanetParams) -> dict[str, object]:
    """Set or clear custom display names for one or more owned planets."""
    if params.planet_names is not None:
        return _rename_planets_batch(bridge, params.planet_names)

    pid_val = parse_command_int("planet_id", params.planet_id, min_val=1, max_val=2**63 - 1)
    if pid_val is None:
        raise ApiRequestError("'rename_planet' requires integer 'planet_id' or dict 'planet_names'.")
    raw_name = params.name
    if raw_name is not None and not isinstance(raw_name, str):
        raise ApiRequestError("'rename_planet' field 'name' must be a string.", field="name")

    with bridge.ctx.state_lock:
        snap_state = _resolve_state_locked(bridge)
        if snap_state is None or not snap_state.planets:
            raise ApiRequestError("No game state snapshot available yet; cannot validate planet_id.")
        target_p = next((p for p in snap_state.planets if int(p.planet_id) == int(pid_val)), None)
        if target_p is None:
            raise ApiRequestError(f"Planet {pid_val} does not belong to this empire.")
        updated_names = bridge.names.set_custom_planet_name_locked(
            int(pid_val),
            "" if raw_name is None else raw_name,
        )
        g_name = str(target_p.name or "")
        c_name = updated_names.get(str(pid_val)) or None
        eff_name = c_name or g_name
        planet_info: dict[str, object] = {
            "planet_id": int(pid_val),
            "coords": str(target_p.coords or ""),
            "name": eff_name,
            "game_name": g_name,
            "custom_name": c_name,
        }

    bridge.publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "rename_planet",
            "planet_id": int(pid_val),
            "name": eff_name,
            "custom_name": c_name,
            "game_name": g_name,
            "custom_planet_names": updated_names,
            "planet": planet_info,
        }
    )


def register_config_handlers(registry: CommandRegistry[BridgeApiFacade]) -> None:
    """Register all 3 non-browser config and planet-rename commands."""
    registry.command(
        "config",
        needs_browser=False,
        mutates=False,
        summary="Return the active StrategyConfig snapshot.",
        params=ConfigParams,
    )(_handle_config)
    registry.command(
        "update_config",
        needs_browser=False,
        mutates=True,
        summary="Validate, persist, and propagate a StrategyConfig patch.",
        params=UpdateConfigParams,
    )(_handle_update_config)
    registry.command(
        "rename_planet",
        needs_browser=False,
        mutates=True,
        summary="Set or clear custom display names for one or more owned planets.",
        params=RenamePlanetParams,
    )(_handle_rename_planet)
