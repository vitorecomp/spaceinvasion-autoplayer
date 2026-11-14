"""Action-graph, buildings-ETA, and combat-overview view handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from spaceinvasion.combat.points import build_combat_overview
from spaceinvasion.core.config import normalize_planner_mode
from spaceinvasion.planner.eta import build_buildings_eta
from spaceinvasion.planner.graph import build_action_graph, merge_strategy_config_overrides
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import parse_command_int, redact_api_payload, sanitize_json_dict
from spaceinvasion.runtime.bridge._helpers_sig import redact_action_graph_strings
from spaceinvasion.runtime.bridge._helpers_validation import validate_config_patch


@dataclass(frozen=True)
class ActionGraphParams:
    """Parameters for `action_graph`."""

    config: object = None
    planner_mode: object = None
    simulated: object = None
    planet_id: object = None
    refresh: object = False


@dataclass(frozen=True)
class PlanetViewParams:
    """Parameters for `buildings_eta` and `combat_overview`."""

    planet_id: object = None
    refresh: bool = False


def parse_planner_mode_override(raw_mode: object) -> str | None:
    """Strictly validate an optional `planner_mode` override string."""
    if raw_mode is None:
        return None
    if not isinstance(raw_mode, str) or not raw_mode.strip():
        raise ApiRequestError("'planner_mode' override must be a non-empty string ('priority' or 'ai_planner').")
    try:
        return str(normalize_planner_mode(raw_mode, strict=True))
    except ValueError as exc:
        raise ApiRequestError(str(redact_api_payload(exc))) from exc


def handle_action_graph(bridge: BrowserBridgeFacade, params: ActionGraphParams) -> dict[str, object]:
    """Handle `action_graph` using cached state when `refresh=False` and Chrome only when `refresh=True`."""
    raw_ag_cfg = params.config
    if raw_ag_cfg is not None:
        if not isinstance(raw_ag_cfg, Mapping):
            raise ApiRequestError("'action_graph' field 'config' must be a dictionary.")
        validate_config_patch(raw_ag_cfg)
    ag_req_mode = parse_planner_mode_override(params.planner_mode)
    has_overrides = bool(raw_ag_cfg) or (ag_req_mode is not None)
    if params.simulated is not None:
        if not isinstance(params.simulated, bool):
            raise ApiRequestError("'action_graph' field 'simulated' must be a boolean.")
        ag_simulated = params.simulated
    else:
        ag_simulated = has_overrides
    ag_pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
    if params.refresh is not None and not isinstance(params.refresh, bool):
        raise ApiRequestError("'action_graph' field 'refresh' must be a boolean.")
    if not bool(params.refresh):
        return _cached_action_graph(
            bridge,
            raw_ag_cfg=raw_ag_cfg if isinstance(raw_ag_cfg, Mapping) else None,
            ag_req_mode=ag_req_mode,
            ag_simulated=ag_simulated,
            has_overrides=has_overrides,
            ag_pid=ag_pid,
        )

    def _run_refresh(session: BrowserCommandSession) -> dict[str, object]:
        effective_cfg, _ = merge_strategy_config_overrides(
            bridge.ctx.strategy_config,
            raw_ag_cfg if isinstance(raw_ag_cfg, Mapping) else None,
            planner_mode_override=ag_req_mode,
        )
        state = session.cached_or_fetch(
            include_buildings=True, include_research=False, include_shipyard=False, priority="normal"
        )
        custom_names = bridge.names.get_custom_planet_names_locked()
        disp_state = bridge.names.apply_custom_planet_names_to_state(state, custom_names) or state
        if ag_pid is not None:
            bridge.ctx.resolve_planet(disp_state, ag_pid)
        actions, ag_opt_summary = bridge.plan_with_summary_ex(
            disp_state, config=effective_cfg, record_summary=(not ag_simulated and not has_overrides)
        )
        graph_obj = build_action_graph(
            disp_state,
            actions,
            effective_config=effective_cfg,
            active_config=bridge.ctx.strategy_config,
            simulated=ag_simulated,
            optimizer_summary=ag_opt_summary,
            planet_filter=ag_pid,
        )
        return sanitize_json_dict(
            {"ok": True, "command": "action_graph", "action_graph": graph_obj, "custom_planet_names": custom_names}
        )

    with bridge.ctx.lock:
        return run_browser_command(bridge, "action_graph", {"refresh": True}, _run_refresh)


def _cached_action_graph(
    bridge: BrowserBridgeFacade,
    *,
    raw_ag_cfg: Mapping[str, object] | None,
    ag_req_mode: str | None,
    ag_simulated: bool,
    has_overrides: bool,
    ag_pid: int | None,
) -> dict[str, object]:
    with bridge.ctx.state_lock:
        effective_cfg, _ = merge_strategy_config_overrides(
            bridge.ctx.strategy_config, raw_ag_cfg, planner_mode_override=ag_req_mode
        )
        custom_names = bridge.names.get_custom_planet_names_locked()
        raw_state = bridge.attach_raid_stats(bridge.ctx.last_state) if bridge.ctx.last_state is not None else None
        ag_state = bridge.names.apply_custom_planet_names_to_state(raw_state, custom_names) if raw_state else None
        if ag_state is not None and ag_pid is not None:
            bridge.ctx.resolve_planet(ag_state, ag_pid)
        if ag_state is not None:
            ag_actions, ag_opt_summary = bridge.plan_with_summary_ex(
                ag_state,
                config=effective_cfg,
                record_summary=(not ag_simulated and not has_overrides),
                already_enriched=True,
            )
        else:
            ag_actions, ag_opt_summary = [], None
        graph_obj = redact_action_graph_strings(
            build_action_graph(
                ag_state,
                ag_actions,
                effective_config=effective_cfg,
                active_config=bridge.ctx.strategy_config,
                simulated=ag_simulated,
                optimizer_summary=ag_opt_summary,
                planet_filter=ag_pid,
                sanitize=False,
            )
        )
        return sanitize_json_dict(
            {"ok": True, "command": "action_graph", "action_graph": graph_obj, "custom_planet_names": custom_names}
        )


def handle_buildings_eta(bridge: BrowserBridgeFacade, params: PlanetViewParams) -> dict[str, object]:
    """Handle `buildings_eta` from cache when warm or live browser when `refresh=True`."""
    pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
    if not params.refresh and bridge.ctx.last_state is not None:
        with bridge.ctx.state_lock:
            disp_st = bridge.names.apply_custom_planet_names_to_state(bridge.ctx.last_state) or bridge.ctx.last_state
            planet = bridge.ctx.resolve_planet(disp_st, pid) if pid is not None else disp_st.active_planet()
            eta_payload = build_buildings_eta(disp_st, planet)
            co_goals = bridge.get_config_optimizer_building_goals_locked()
            if isinstance(eta_payload, dict):
                eta_payload["config_optimizer_goals"] = co_goals
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "buildings_eta",
                    "buildings_eta": eta_payload,
                    "config_optimizer_goals": co_goals,
                }
            )

    def _run_live(session: BrowserCommandSession) -> dict[str, object]:
        state = session.cached_or_fetch(
            include_buildings=True, include_research=False, include_shipyard=False, priority="normal"
        )
        disp_st = bridge.names.apply_custom_planet_names_to_state(state) or state
        planet = bridge.ctx.resolve_planet(disp_st, pid) if pid is not None else disp_st.active_planet()
        eta_payload = build_buildings_eta(disp_st, planet)
        co_goals = bridge.get_config_optimizer_building_goals_locked()
        if isinstance(eta_payload, dict):
            eta_payload["config_optimizer_goals"] = co_goals
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "buildings_eta",
                "buildings_eta": eta_payload,
                "config_optimizer_goals": co_goals,
            }
        )

    with bridge.ctx.lock:
        return run_browser_command(bridge, "buildings_eta", {"refresh": params.refresh}, _run_live)


def handle_combat_overview(bridge: BrowserBridgeFacade, params: PlanetViewParams) -> dict[str, object]:
    """Handle `combat_overview` from cache when warm or live browser when `refresh=True`."""
    pid = parse_command_int("planet_id", params.planet_id, allow_none=True, min_val=1, max_val=2**63 - 1)
    if not params.refresh and bridge.ctx.last_state is not None:
        with bridge.ctx.state_lock:
            disp_st = bridge.names.apply_custom_planet_names_to_state(bridge.ctx.last_state) or bridge.ctx.last_state
            planet = bridge.ctx.resolve_planet(disp_st, pid) if pid is not None else disp_st.active_planet()
            combat = build_combat_overview(
                disp_st, planet, safety_margin=bridge.ctx.strategy_config.combat_safety_margin
            )
            return sanitize_json_dict({"ok": True, "command": "combat_overview", "combat": combat})

    def _run_live(session: BrowserCommandSession) -> dict[str, object]:
        state = session.cached_or_fetch(
            include_buildings=False, include_research=False, include_shipyard=True, priority="normal"
        )
        disp_st = bridge.names.apply_custom_planet_names_to_state(state) or state
        planet = bridge.ctx.resolve_planet(disp_st, pid) if pid is not None else disp_st.active_planet()
        combat = build_combat_overview(disp_st, planet, safety_margin=bridge.ctx.strategy_config.combat_safety_margin)
        return sanitize_json_dict({"ok": True, "command": "combat_overview", "combat": combat})

    with bridge.ctx.lock:
        return run_browser_command(bridge, "combat_overview", {"refresh": params.refresh}, _run_live)
