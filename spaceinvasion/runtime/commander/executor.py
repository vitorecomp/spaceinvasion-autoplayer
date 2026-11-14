"""Single-tick commander orchestration and HTTP command batch execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
import logging

import requests

from spaceinvasion.core.config import StrategyConfig, normalize_planner_mode
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.core.redaction import format_clean_error, redact_sensitive
from spaceinvasion.runtime.commander._executor_plan import (
    ActionDedupKey,
    action_dedup_key,
    compute_tick_actions,
    decode_matching_state,
    pick_next_ready_action,
    should_refresh_buildings_for_wait,
    sync_active_planet_economy,
)
from spaceinvasion.runtime.commander._types import (
    FLEET_ACTION_KINDS,
    PARALLEL_BUILD_KINDS,
    CommanderApiLike,
    GameStateCmdLike,
)
from spaceinvasion.runtime.commander.errors import (
    is_galaxy_scan_busy_error,
    safe_cmd_int,
    safe_cmd_str,
)
from spaceinvasion.runtime.commander.payloads import action_to_command_payload
from spaceinvasion.runtime.commander_client import GameApiError
from spaceinvasion.runtime.logs import pick_next_action

LOGGER = logging.getLogger("spaceinvasion.runtime.commander")

_RecomputeFn = Callable[[GameStateCmdLike, Sequence[Mapping[str, object]] | None], list[PlannedAction]]


def _send_action_command(
    api: CommanderApiLike,
    ready: PlannedAction,
    *,
    live: bool,
    acted: int,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    payload = action_to_command_payload(ready, dry_run=not live)
    try:
        res = api.send_command(payload)
    except (requests.RequestException, GameApiError) as cmd_exc:
        if is_galaxy_scan_busy_error(safe_cmd_str(cmd_exc)) and acted > 0:
            blocked: dict[str, object] = {
                "ok": True,
                "executed": False,
                "dry_run": not live,
                "sweep_blocked": True,
                "message": format_clean_error(safe_cmd_str(cmd_exc)),
            }
            return None, blocked
        raise
    if not isinstance(res, Mapping):
        raise GameApiError(f"Expected JSON object from send_command, got {type(res).__name__}")
    return {str(k): v for k, v in res.items()}, None


def _handle_failed_execution(
    ready: PlannedAction,
    res_dict: Mapping[str, object],
    state: GameStateCmdLike,
    actions: list[PlannedAction],
    cur_mq: Sequence[Mapping[str, object]] | None,
    active_fsc: dict[int, float],
    recompute: _RecomputeFn,
) -> tuple[GameStateCmdLike, list[PlannedAction]]:
    fail_msg_l = safe_cmd_str(res_dict.get("message", "")).lower()
    raw_st = res_dict.get("state")
    if ready.kind in FLEET_ACTION_KINDS and (
        "no ship input fields found on internalfleet" in fail_msg_l or "nofleet" in fail_msg_l
    ):
        pid_busy = safe_cmd_int(ready.planet_id, 0)
        if pid_busy > 0:
            active_fsc[pid_busy] = max(active_fsc.get(pid_busy, 0.0), 90.0)
        if isinstance(raw_st, Mapping):
            state = decode_matching_state(raw_st, state)
        return state, recompute(state, cur_mq)
    if isinstance(raw_st, Mapping):
        state = decode_matching_state(raw_st, state)
        return state, recompute(state, cur_mq)
    return state, actions


def _execute_tick_batch(
    api: CommanderApiLike,
    state: GameStateCmdLike,
    actions: list[PlannedAction],
    cur_mq: Sequence[Mapping[str, object]] | None,
    *,
    live: bool,
    max_actions: int,
    cfg: StrategyConfig,
    is_ai_planner: bool,
    active_fsc: dict[int, float],
    recompute: _RecomputeFn,
) -> tuple[
    GameStateCmdLike,
    list[PlannedAction],
    PlannedAction | None,
    dict[str, object] | None,
    list[dict[str, object]],
    bool,
]:
    outcomes: list[dict[str, object]] = []
    actual_action: PlannedAction | None = None
    actual_result: dict[str, object] | None = None
    after_live = False
    acted = 0
    failed_attempts = 0
    max_failures = max(6, max_actions * 2)
    attempted_keys: set[ActionDedupKey] = set()
    built_planets: set[int] = set()
    allow_parallel = bool(live and max_actions > 1 and cfg.allow_multi_planet)
    parallel_cap = max_actions + max(0, len(state.planets))

    while True:
        only_parallel = acted >= max_actions
        if (only_parallel and (not allow_parallel or acted >= parallel_cap)) or failed_attempts >= max_failures:
            break
        ready = pick_next_ready_action(
            actions,
            attempted_keys,
            built_planets,
            is_ai_planner=is_ai_planner,
            only_parallel_builds=only_parallel,
        )
        if ready is None:
            break
        attempted_keys.add(action_dedup_key(ready))
        actual_action = ready
        res_dict, sweep_blocked = _send_action_command(api, ready, live=live, acted=acted)
        if sweep_blocked is not None:
            outcomes.append(sweep_blocked)
            break
        if res_dict is None:
            break
        raw_mq = res_dict.get("manual_queue")
        if isinstance(raw_mq, list):
            cur_mq = [x for x in raw_mq if isinstance(x, Mapping)]
        actual_result = res_dict
        outcomes.append(res_dict)
        acted += 1
        if not live or res_dict.get("captcha_gate") or res_dict.get("paused") or res_dict.get("rate_limited"):
            break
        if not res_dict.get("executed"):
            failed_attempts += 1
            state, actions = _handle_failed_execution(ready, res_dict, state, actions, cur_mq, active_fsc, recompute)
            continue
        after_live = True
        if ready.kind in PARALLEL_BUILD_KINDS and (pid_built := safe_cmd_int(ready.planet_id, 0)) > 0:
            built_planets.add(pid_built)
        need_refresh = acted < max_actions or (
            allow_parallel
            and acted < parallel_cap
            and any(
                a.executable_now
                and a.kind in PARALLEL_BUILD_KINDS
                and safe_cmd_int(a.planet_id, 0) > 0
                and safe_cmd_int(a.planet_id, 0) not in built_planets
                and action_dedup_key(a) not in attempted_keys
                for a in actions
            )
        )
        raw_st = res_dict.get("state")
        if isinstance(raw_st, Mapping):
            state = decode_matching_state(raw_st, state)
        elif need_refresh:
            state = api.get_state(include_buildings=False)
        actions = recompute(state, cur_mq)

    return state, actions, actual_action, actual_result, outcomes, after_live


def run_commander_tick(
    api: CommanderApiLike,
    *,
    live: bool,
    max_actions: int,
    config: StrategyConfig | None = None,
    include_buildings: bool = True,
    scanned_systems: Mapping[str, float] | None = None,
    throttle_cooldowns: Mapping[int, float] | None = None,
    economy_sync_timestamps: Mapping[int, float] | None = None,
    fleet_slot_cooldowns: Mapping[int, float] | None = None,
    manual_queue: Sequence[Mapping[str, object]] | None = None,
    transmitter_status: Mapping[str, object] | None = None,
    logger: logging.Logger = LOGGER,
) -> tuple[
    GameStateCmdLike,
    PlannedAction | None,
    dict[str, object] | None,
    PlannedAction | None,
    list[dict[str, object]],
]:
    """Fetch state via HTTP, compute planned actions, and dispatch ready commands."""
    cfg = config or StrategyConfig()
    fetched = api.get_state(include_buildings=include_buildings)
    state: GameStateCmdLike = sync_active_planet_economy(
        api,
        fetched,
        live=live,
        include_buildings=include_buildings,
        cfg=cfg,
        economy_sync_timestamps=economy_sync_timestamps,
    )
    norm_mode = normalize_planner_mode(cfg.planner_mode)
    is_ai_planner = norm_mode == "ai_planner"
    max_planets_gate = max(1, safe_cmd_int(cfg.max_planets, 11))
    active_fsc: dict[int, float] = dict(fleet_slot_cooldowns) if fleet_slot_cooldowns else {}

    def _recompute(st: GameStateCmdLike, mq: Sequence[Mapping[str, object]] | None) -> list[PlannedAction]:
        return compute_tick_actions(
            api,
            st,
            mq,
            cfg=cfg,
            is_ai_planner=is_ai_planner,
            max_planets_gate=max_planets_gate,
            active_fsc=active_fsc,
            scanned_systems=scanned_systems,
            throttle_cooldowns=throttle_cooldowns,
            transmitter_status=transmitter_status,
        )

    actions = _recompute(state, manual_queue)
    if (
        not include_buildings
        and not any(a.executable_now for a in actions)
        and should_refresh_buildings_for_wait(state, actions)
    ):
        refetched = api.get_state(include_buildings=True)
        if isinstance(refetched, GameStateCmdLike):
            state = refetched
            actions = _recompute(state, manual_queue)

    for alert in actions:
        if alert.priority == "P0":
            logger.warning("[CMD][P0 ALERT] %s", redact_sensitive(safe_cmd_str(alert.reason)))

    state, actions, actual_action, actual_result, outcomes, after_live = _execute_tick_batch(
        api,
        state,
        actions,
        manual_queue,
        live=live,
        max_actions=max_actions,
        cfg=cfg,
        is_ai_planner=is_ai_planner,
        active_fsc=active_fsc,
        recompute=_recompute,
    )

    if actual_action is None and actions:
        actual_action = actions[0]
        actual_result = {
            "ok": True,
            "executed": False,
            "dry_run": not live,
            "message": redact_sensitive(
                f"Waiting: {safe_cmd_str(actual_action.target_name)} ({safe_cmd_str(actual_action.reason)})"
            ),
            "action": redact_sensitive(asdict(actual_action)),
        }
        outcomes.append(actual_result)

    next_action = pick_next_action(actions, actual_action, after_live_execution=after_live)
    if next_action is not None and not next_action.executable_now and any(a.executable_now for a in actions):
        next_ready = next((a for a in actions if a.executable_now), None)
        if next_ready is not None:
            next_action = next_ready
    return state, actual_action, actual_result, next_action, outcomes


__all__ = [
    "ActionDedupKey",
    "action_dedup_key",
    "pick_next_ready_action",
    "run_commander_tick",
]
