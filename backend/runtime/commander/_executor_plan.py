"""Tick planning, economy sync, wait-refresh checks, and ready-action selection helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import time
from typing import cast

import requests

from backend.core.coercion import coerce_int
from backend.core.config import StrategyConfig
from backend.core.model import (
    GameState as ModelGameState,
)
from backend.core.model import (
    PlannedAction,
    Resources,
    is_asteroid_planet,
    normalize_planet_type,
)
from backend.core.model import (
    game_state_from_dict as model_game_state_from_dict,
)
from backend.planner.heuristics import (
    _count_planet_building_queues,
    merge_manual_queue_actions,
    plan_actions,
)
from backend.runtime.commander._types import (
    PARALLEL_BUILD_KINDS,
    CommanderApiLike,
    CommanderConfigLike,
    GameStateCmdLike,
    PlannedActionCmdLike,
)
from backend.runtime.commander.errors import (
    compute_rate_limit_backoff,
    is_captcha_error,
    safe_cmd_int,
    safe_cmd_opt_int,
    safe_cmd_str,
    safe_finite_float,
)
from backend.runtime.commander.payloads import (
    hold_busy_fleet_slots,
    planned_action_from_dict,
)
from backend.runtime.commander_client import GameApiError

ActionDedupKey = tuple[str, int, int | None, int | None, str]
_PRIO_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def decode_matching_state(raw_state: Mapping[str, object], reference: object) -> GameStateCmdLike:
    """Decode ``raw_state`` into ``core.model.GameState``."""
    _ = reference
    return model_game_state_from_dict(raw_state)


def _to_legacy_state(state: GameStateCmdLike) -> ModelGameState:
    return cast(ModelGameState, state)


def to_canonical_action(act: PlannedActionCmdLike) -> PlannedAction:
    """Convert any ``PlannedActionCmdLike`` into a canonical ``core.model.PlannedAction``."""
    if isinstance(act, PlannedAction):
        return act
    return PlannedAction(
        priority=act.priority,
        kind=act.kind,
        planet_id=act.planet_id,
        planet_coords=act.planet_coords,
        target_name=act.target_name,
        reason=act.reason,
        target_id=act.target_id,
        target_level=act.target_level,
        cost=Resources(
            pig_iron=act.cost.pig_iron,
            metal=act.cost.metal,
            kryptonite=act.cost.kryptonite,
            spice=act.cost.spice,
        ),
        missing=Resources(
            pig_iron=act.missing.pig_iron,
            metal=act.missing.metal,
            kryptonite=act.missing.kryptonite,
            spice=act.missing.spice,
        ),
        executable_now=act.executable_now,
        wait_seconds=act.wait_seconds,
        manual_queue_id=act.manual_queue_id,
    )


def action_dedup_key(act: PlannedActionCmdLike) -> ActionDedupKey:
    """Return stable deduplication identity for a planned action within one tick."""
    return (
        act.kind,
        safe_cmd_int(act.planet_id, 0),
        safe_cmd_opt_int(act.target_id),
        safe_cmd_opt_int(act.target_level),
        safe_cmd_str(act.target_name),
    )


def _ai_tick_rank(act: PlannedActionCmdLike, built_pids: set[int]) -> tuple[int, int, int, int]:
    pr = _PRIO_RANK.get(str(act.priority or "").upper(), 9)
    if pr == 0:
        return (0, 0 if act.kind == "ALERT_HOSTILE" else 1, 0, 0)
    is_manual = 0 if act.manual_queue_id is not None else 1
    if pr == 1:
        if act.kind == "THROTTLE_BUILDING":
            rsn_l = safe_cmd_str(act.reason).lower()
            if "negative energy" in rsn_l or "metal storage full" in rsn_l or "metal overflow cleared" in rsn_l:
                return (1, 0, 0, 0)
            return (1, is_manual, 4, 0)
        if act.kind in PARALLEL_BUILD_KINDS:
            pid_v = safe_cmd_int(act.planet_id, 0)
            return (1, is_manual, 1, 1 if pid_v in built_pids else 0)
        if act.kind == "RESEARCH":
            return (1, is_manual, 1, 1)
        if act.kind == "TRANSPORT":
            return (1, is_manual, 2, 0)
        if act.kind == "SPY":
            return (1, is_manual, 3, 0)
        return (1, is_manual, 5, 0)
    return (pr, is_manual, 0, 0)


def pick_next_ready_action(
    cur_actions: Sequence[PlannedAction],
    attempted: set[ActionDedupKey],
    built_pids: set[int],
    *,
    is_ai_planner: bool,
    only_parallel_builds: bool = False,
) -> PlannedAction | None:
    """Select the next executable, unattempted action for the current tick."""
    candidates = [a for a in cur_actions if a.executable_now and action_dedup_key(a) not in attempted]
    if only_parallel_builds:
        candidates = [
            a
            for a in candidates
            if a.kind in PARALLEL_BUILD_KINDS
            and safe_cmd_int(a.planet_id, 0) > 0
            and safe_cmd_int(a.planet_id, 0) not in built_pids
        ]
    if not candidates:
        return None
    if not is_ai_planner or only_parallel_builds:
        return candidates[0]
    return min(candidates, key=lambda act: _ai_tick_rank(act, built_pids))


def sync_active_planet_economy(
    api: CommanderApiLike,
    state: GameStateCmdLike,
    *,
    live: bool,
    include_buildings: bool,
    cfg: CommanderConfigLike,
    economy_sync_timestamps: Mapping[int, float] | None,
    now_fn: Callable[[], float] = time.time,
) -> GameStateCmdLike:
    """Refresh Blast Furnace economy rate via ``sync_economy`` when stale or missing."""
    active = state.active_planet()
    has_hostile_alert = any(bool(fa.hostile) for fa in state.fleet_alerts)
    if (
        not live
        or has_hostile_alert
        or economy_sync_timestamps is None
        or active is None
        or normalize_planet_type(active.planet_type) in ("asteroid", "raid")
        or active.building_level(2) < 1
    ):
        return state
    has_r2 = isinstance(active.economy_rates, Mapping) and "resource2" in active.economy_rates
    raw_ts: object = economy_sync_timestamps.get(active.planet_id, 0.0)
    last_econ_ts = safe_finite_float(raw_ts) or 0.0
    econ_refresh_ttl = max(3600.0, float(safe_cmd_int(cfg.action_batch_interval_seconds, 120)) * 10.0)
    if has_r2 and last_econ_ts > 0.0 and not (include_buildings and (now_fn() - last_econ_ts) >= econ_refresh_ttl):
        return state
    try:
        sync_res = api.send_command({"command": "sync_economy", "planet_id": active.planet_id})
        if isinstance(sync_res, Mapping):
            raw_st = sync_res.get("state")
            if isinstance(raw_st, Mapping):
                return decode_matching_state(raw_st, state)
    except (requests.RequestException, GameApiError) as sync_exc:
        if compute_rate_limit_backoff(safe_cmd_str(sync_exc)) is not None:
            raise
    return state


def _count_normal_planets(state: GameStateCmdLike) -> int:
    return sum(1 for p in state.planets if normalize_planet_type(p.planet_type) == "normal")


def compute_tick_actions(
    api: CommanderApiLike,
    cur_st: GameStateCmdLike,
    mq_items: Sequence[Mapping[str, object]] | None,
    *,
    cfg: StrategyConfig,
    is_ai_planner: bool,
    max_planets_gate: int,
    active_fsc: Mapping[int, float],
    scanned_systems: Mapping[str, float] | None,
    throttle_cooldowns: Mapping[int, float] | None,
    transmitter_status: Mapping[str, object] | None,
) -> list[PlannedAction]:
    """Compute tick actions via remote AI plan or local heuristic strategy + manual queue."""
    if is_ai_planner and _count_normal_planets(cur_st) >= max_planets_gate:
        try:
            plan_resp = api.send_command({"command": "plan", "refresh": False})
            raw_actions = plan_resp.get("actions") if isinstance(plan_resp, Mapping) else None
            if (
                isinstance(plan_resp, Mapping)
                and plan_resp.get("effective_planner_mode") == "ai_planner"
                and isinstance(raw_actions, list)
            ):
                parsed_ai = [act for item in raw_actions if (act := planned_action_from_dict(item)) is not None]
                if parsed_ai:
                    return hold_busy_fleet_slots(parsed_ai, active_fsc, re_sort_priority=False)
        except (requests.RequestException, GameApiError) as plan_exc:
            exc_txt = safe_cmd_str(plan_exc)
            if compute_rate_limit_backoff(exc_txt) is not None or is_captcha_error(exc_txt):
                raise

    st_for_plan = _to_legacy_state(cur_st)
    raw_base = plan_actions(
        st_for_plan,
        config=cfg,
        scanned_systems=dict(scanned_systems) if scanned_systems is not None else None,
        throttle_cooldowns=dict(throttle_cooldowns) if throttle_cooldowns is not None else None,
        transmitter_status=dict(transmitter_status) if transmitter_status is not None else None,
    )
    base = hold_busy_fleet_slots([to_canonical_action(a) for a in raw_base], active_fsc, re_sort_priority=True)
    if mq_items:
        merged_raw, _ = merge_manual_queue_actions(st_for_plan, raw_base, list(mq_items), config=cfg)
        return hold_busy_fleet_slots([to_canonical_action(a) for a in merged_raw], active_fsc, re_sort_priority=False)
    return base


def should_refresh_buildings_for_wait(
    state: GameStateCmdLike,
    actions: Sequence[PlannedAction],
) -> bool:
    """Return True when resources now cover a non-queued wait item on the active planet."""
    active_p = state.active_planet()
    if active_p is None:
        return False
    st_legacy = _to_legacy_state(state)
    b_queued = _count_planet_building_queues(st_legacy, st_legacy.active_planet(), is_active_planet=True)
    is_astro = is_asteroid_planet(active_p.planet_type)
    has_cmd = coerce_int((state.specs or {}).get("construction_commander", 0), 0) > 0
    queue_cap = 1 if is_astro else (6 if has_cmd else 5)
    if b_queued >= queue_cap:
        return False
    for act in actions:
        if act.kind not in ("WAIT_RESOURCES", "BUILD") or act.target_id is None or act.cost.is_zero():
            continue
        reason_l = (act.reason or "").lower()
        if any(
            marker in reason_l
            for marker in ("build queue full", "waiting/queued", "not currently visible/unlocked", "future/queued")
        ):
            continue
        if act.planet_id != active_p.planet_id:
            continue
        res = active_p.resources
        if not (
            res.pig_iron >= act.cost.pig_iron
            and res.metal >= act.cost.metal
            and res.kryptonite >= act.cost.kryptonite
            and res.spice >= act.cost.spice
        ):
            continue
        offer = next((o for o in active_p.building_offers if o.building_id == act.target_id), None)
        if offer is not None and offer.can_build:
            continue
        return True
    return False
