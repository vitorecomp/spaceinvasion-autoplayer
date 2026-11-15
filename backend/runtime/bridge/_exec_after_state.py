"""Post-execution state refresh, optional return-home sync, and response summary assembly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
import time

from backend.core.model import GameState, PlannedAction
from backend.parsing import parse_home_metadata, parse_tutorial_from_home
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._exec_postprocess import ExecutionHooks
from backend.runtime.bridge._helpers import redact_api_payload, sanitize_finite_json, sanitize_json_dict
from backend.runtime.bridge._helpers_sig import call_with_optional_priority
from backend.runtime.bridge.lifecycle import BridgeLifecycleService
from backend.runtime.bridge.state_cache import StateCacheService
from backend.session.client import GameBrowserClient, is_server_rate_limit_html
from backend.session.executor import ExecutionResult


def _plan_after(hooks: ExecutionHooks, state: GameState) -> list[PlannedAction]:
    if hooks.on_plan_with_context is None:
        return []
    actions = hooks.on_plan_with_context(state)
    if hooks.on_apply_fleet_slot_cooldowns is not None:
        return hooks.on_apply_fleet_slot_cooldowns(actions)
    return actions


def _extract_valid_observed_planet(res: ExecutionResult) -> tuple[int | None, bool | None]:
    raw_pid = res.observed_active_planet_id
    valid_pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
    raw_conf = res.observed_active_planet_confirmed
    conf_bool = raw_conf if isinstance(raw_conf, bool) else None
    return valid_pid, conf_bool


def _record_with_updated_state(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    state: GameState,
    res: ExecutionResult,
    res_updated: GameState,
    *,
    valid_obs_pid: int | None,
    obs_conf_bool: bool | None,
) -> GameState:
    if res.action.kind == "BUILD" and res.action.target_id == 9:
        ctx.last_shipyard_sync_at = 0.0
        ctx.last_shipyard_sync_sig = None
    elif res.action.kind == "BUILD" and res.action.target_id == 8:
        ctx.last_research_sync_at = 0.0
        ctx.last_research_sync_sig = None
        res_updated = replace(res_updated, research_offers=())
    state_to_record = res_updated
    if (
        obs_conf_bool is True
        and valid_obs_pid is not None
        and any(p.planet_id == valid_obs_pid for p in res_updated.planets)
        and res_updated.active_planet_id != valid_obs_pid
    ):
        state_to_record = replace(res_updated, active_planet_id=valid_obs_pid)
    if res.action.kind != "RESEARCH" and (
        state_to_record.active_planet_id != state.active_planet_id
        or {p.planet_id: p.building_level(8) for p in state_to_record.planets}
        != {p.planet_id: p.building_level(8) for p in state.planets}
    ):
        state_to_record = replace(state_to_record, research_offers=())
    after_state = state_cache.record_state(
        state_to_record,
        fetched_buildings=(res.action.kind in ("BUILD", "DEMOLISH_BUILDING")),
        fetched_research=(res.action.kind == "RESEARCH"),
        fetched_shipyard=(res.action.kind == "BUILD_SHIP"),
        fetched_home=(res.action.kind == "CLAIM_TUTORIAL"),
        observed_active_planet=(obs_conf_bool is True),
    )
    if obs_conf_bool is False and len(after_state.planets) > 1:
        ctx.active_planet_confirmed = False
    return after_state


def _record_after_spy(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    state: GameState,
    *,
    valid_obs_pid: int | None,
    obs_conf_bool: bool | None,
) -> GameState:
    ctx.pending_spy_sync = True
    ctx.last_spy_dispatched_at = time.monotonic()
    state_to_record = state
    obs_flag = False
    if obs_conf_bool is True:
        obs_flag = True
        if (
            valid_obs_pid is not None
            and any(p.planet_id == valid_obs_pid for p in state.planets)
            and state.active_planet_id != valid_obs_pid
        ):
            state_to_record = replace(state, active_planet_id=valid_obs_pid, research_offers=())
            ctx.last_research_sync_sig = None
    after_state = state_cache.record_state(
        state_to_record,
        fetched_buildings=(ctx.last_buildings_fetched and state_to_record.active_planet_id == state.active_planet_id),
        fetched_home=False,
        observed_active_planet=obs_flag,
        update_snapshot_ts=False,
    )
    if obs_conf_bool is False and len(after_state.planets) > 1:
        ctx.active_planet_confirmed = False
    return after_state


def _reread_snapshot_after_exec(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient,
    state: GameState,
    res: ExecutionResult,
    *,
    valid_obs_pid: int | None,
    obs_conf_bool: bool | None,
) -> GameState:
    ctx.last_snapshot_at = 0.0
    if res.action.kind == "BUILD" and res.action.target_id == 9:
        ctx.last_shipyard_sync_at = 0.0
        ctx.last_shipyard_sync_sig = None
    elif res.action.kind == "BUILD" and res.action.target_id == 8:
        ctx.last_research_sync_at = 0.0
        ctx.last_research_sync_sig = None
    inc_b = res.action.kind in ("BUILD", "DEMOLISH_BUILDING", "CLAIM_TUTORIAL")
    inc_r = res.action.kind == "RESEARCH"
    inc_h = res.action.kind == "CLAIM_TUTORIAL"
    snap = state_cache.read_snapshot(
        client,
        include_buildings=inc_b,
        include_research=inc_r,
        include_shipyard=False,
        include_home=inc_h,
        return_home=False,
        priority="interactive",
    )
    try:
        obs_active = bool(client.last_snapshot_observed_active_planet)
    except AttributeError:
        obs_active = False
    if obs_conf_bool is True:
        obs_active = True
        if (
            valid_obs_pid is not None
            and any(p.planet_id == valid_obs_pid for p in snap.planets)
            and snap.active_planet_id != valid_obs_pid
        ):
            snap = replace(snap, active_planet_id=valid_obs_pid)
    if (res.action.kind == "BUILD" and res.action.target_id == 8) or (
        not inc_r
        and (
            snap.active_planet_id != state.active_planet_id
            or {p.planet_id: p.building_level(8) for p in snap.planets}
            != {p.planet_id: p.building_level(8) for p in state.planets}
        )
    ):
        snap = replace(snap, research_offers=())
        ctx.last_research_sync_sig = None
    after_state = state_cache.record_state(
        snap,
        fetched_buildings=inc_b,
        fetched_research=inc_r,
        fetched_home=inc_h,
        observed_active_planet=obs_active,
    )
    if obs_conf_bool is False and len(after_state.planets) > 1:
        ctx.active_planet_confirmed = False
    return after_state


def _handle_return_home(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient,
    after_state: GameState,
) -> GameState:
    ret_html = call_with_optional_priority(client.navigate_menu, "internalHome", priority="interactive")
    if (
        isinstance(ret_html, str)
        and ret_html
        and not is_server_rate_limit_html(ret_html)
        and "bitmeup-login" not in ret_html.lower()
        and "sip-tab-universe" not in ret_html.lower()
    ):
        home_pid, h_spd, h_newbie, h_q, h_al = parse_home_metadata(ret_html)
        tut_home = parse_tutorial_from_home(ret_html)
        p_ids = {p.planet_id for p in after_state.planets}
        updated = replace(
            after_state,
            tutorial=tut_home if tut_home is not None else after_state.tutorial,
            active_queues=h_q,
            fleet_alerts=h_al,
            newbie_protection=bool(h_newbie),
            speedup_factor=h_spd if h_spd is not None else after_state.speedup_factor,
            active_planet_id=(
                home_pid if (home_pid is not None and home_pid in p_ids) else after_state.active_planet_id
            ),
        )
        return state_cache.record_state(
            updated,
            fetched_buildings=ctx.last_buildings_fetched,
            fetched_home=True,
            observed_active_planet=(home_pid is not None and home_pid in p_ids),
        )
    return after_state


def refresh_after_state_and_plan(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    hooks: ExecutionHooks,
    client: GameBrowserClient | None,
    state: GameState,
    res: ExecutionResult,
    *,
    dry_run: bool,
    return_home: bool,
) -> tuple[GameState | None, list[PlannedAction]]:
    """Record post-execution state snapshot (handling all action branches) and compute next plan."""
    after_state: GameState | None = None
    res_updated = res.updated_state
    valid_obs_pid, obs_conf_bool = _extract_valid_observed_planet(res)

    if res.executed and res.action.kind == "SCAN_GALAXY":
        after_state = state_cache.record_state(
            state,
            fetched_buildings=ctx.last_buildings_fetched,
            fetched_home=False,
            observed_active_planet=False,
            update_snapshot_ts=False,
        )
        after_plan = _plan_after(hooks, after_state)
    elif res.executed and isinstance(res_updated, GameState):
        after_state = _record_with_updated_state(
            ctx, state_cache, state, res, res_updated, valid_obs_pid=valid_obs_pid, obs_conf_bool=obs_conf_bool
        )
        after_plan = _plan_after(hooks, after_state)
    elif res.executed and res.action.kind == "SPY":
        after_state = _record_after_spy(
            ctx, state_cache, state, valid_obs_pid=valid_obs_pid, obs_conf_bool=obs_conf_bool
        )
        after_plan = _plan_after(hooks, after_state)
    elif res.executed and client is not None:
        after_state = _reread_snapshot_after_exec(
            ctx, state_cache, client, state, res, valid_obs_pid=valid_obs_pid, obs_conf_bool=obs_conf_bool
        )
        after_plan = _plan_after(hooks, after_state)
    else:
        state_to_record = res_updated if (not dry_run and isinstance(res_updated, GameState)) else state
        obs_flag = False
        if not dry_run and obs_conf_bool is True:
            obs_flag = True
            if (
                valid_obs_pid is not None
                and any(p.planet_id == valid_obs_pid for p in state_to_record.planets)
                and state_to_record.active_planet_id != valid_obs_pid
            ):
                state_to_record = replace(state_to_record, active_planet_id=valid_obs_pid, research_offers=())
                ctx.last_research_sync_sig = None
        merged_state = state_cache.record_state(
            state_to_record,
            fetched_buildings=(
                ctx.last_buildings_fetched and state_to_record.active_planet_id == state.active_planet_id
            ),
            fetched_home=False,
            observed_active_planet=obs_flag,
            update_snapshot_ts=isinstance(res_updated, GameState) and not dry_run,
        )
        if not dry_run and obs_conf_bool is False and len(merged_state.planets) > 1:
            ctx.active_planet_confirmed = False
        after_plan = _plan_after(hooks, merged_state)

    if (
        return_home
        and res.executed
        and client is not None
        and after_state is not None
        and not (res.action.kind == "CLAIM_TUTORIAL" and isinstance(res_updated, GameState))
    ):
        after_state = _handle_return_home(ctx, state_cache, client, after_state)
        after_plan = _plan_after(hooks, after_state)

    return after_state, after_plan


def build_execution_summary(
    ctx: BridgeContext,
    lifecycle: BridgeLifecycleService,
    hooks: ExecutionHooks,
    cmd: str,
    res: ExecutionResult,
    after_state: GameState | None,
    after_plan: list[PlannedAction],
    *,
    serialize_action_fn: Callable[[PlannedAction, GameState | None], dict[str, object]],
) -> dict[str, object]:
    """Assemble the exact legacy action-execution API response dictionary."""
    next_action = serialize_action_fn(after_plan[0], after_state) if after_plan else None
    summary: dict[str, object] = {
        "ok": True,
        "command": cmd,
        "executed": res.executed,
        "validated": bool(res.validated),
        "validation_error": redact_api_payload(res.validation_error),
        "dry_run": res.dry_run,
        "message": redact_api_payload(res.message),
        "action": serialize_action_fn(res.action, None),
        "next_action": next_action,
    }
    if after_state is not None:
        enriched = hooks.on_attach_raid_stats(after_state) if hooks.on_attach_raid_stats is not None else after_state
        summary["state"] = sanitize_finite_json(asdict(enriched))
    if ctx.db is not None:
        summary["manual_queue"] = sanitize_finite_json(ctx.db.get_manual_queue(status="pending", limit=200))
        summary["notifications"] = sanitize_finite_json(lifecycle.get_notifications_summary_locked())
    return sanitize_json_dict(summary)
