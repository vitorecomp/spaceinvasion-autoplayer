"""Post-execution side-effects: economy, transmitter, throttle, fleet slots, raids, and DB logs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
import time

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, PlanetState, PlannedAction
from spaceinvasion.planner.heuristics import AI_INFRA_CARGO_TAG
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._exec_preflight import compute_fleet_slot_busy_cooldown_s
from spaceinvasion.runtime.bridge._helpers import bounded_api_str, safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._helpers_queue import extract_coords_from_text
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.executor import ActionExecutor, ExecutionResult

_GAL_SYS_RE = re.compile(r"(\d{1,2}):(\d{1,3})", re.ASCII)
_COORDS_3_RE = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)
_COORDS_2_OR_3_RE = re.compile(r"(\d{1,2}:\d{1,3}(?::\d{1,2})?)", re.ASCII)
_FLEET_KINDS: frozenset[str] = frozenset(
    {"ATTACK", "SPY", "TRANSPORT", "STATION", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE"}
)


@dataclass
class ExecutionHooks:
    """Explicit post-construction callbacks injected into `ActionExecutionService`."""

    on_plan_with_context: Callable[[GameState], list[PlannedAction]] | None = None
    on_serialize_action: Callable[[PlannedAction, GameState | None], dict[str, object]] | None = None
    on_note_transmitter_activation: Callable[..., dict[str, object]] | None = None
    on_record_transport_dispatch: Callable[..., None] | None = None
    on_record_infra_transport_spend: Callable[[PlannedAction, int], None] | None = None
    on_record_spare_ship_spend: Callable[[PlannedAction, int], None] | None = None
    on_record_observed_fleet_slots: Callable[[PlanetState | None, int | None], None] | None = None
    on_apply_fleet_slot_cooldowns: Callable[..., list[PlannedAction]] | None = None
    on_record_manual_queue_completion: Callable[..., None] | None = None
    on_publish_snapshot: Callable[[], None] | None = None
    on_attach_raid_stats: Callable[[GameState], GameState] | None = None


def apply_live_economy_and_galaxy_effects(
    ctx: BridgeContext,
    intel_sync: IntelSyncService,
    executor: ActionExecutor,
    state: GameState,
    res: ExecutionResult,
) -> None:
    """Persist observed economy rates and galaxy slots/scans after a live execution."""
    for pid, rates in (executor.last_economy_rates or {}).items():
        if rates:
            ctx.economy_rates[int(pid)] = dict(rates)
            ctx.economy_sync_timestamps[int(pid)] = time.time()
            if ctx.db is not None:
                coords = next((p.coords for p in state.planets if p.planet_id == int(pid)), "")
                ctx.db.save_economy_rates(int(pid), coords, dict(rates))
    gal_slots = list(executor.last_galaxy_slots or ())
    if gal_slots:
        intel_sync.persist_galaxy_slots(gal_slots)
        executor.last_galaxy_slots = []
    if res.executed and res.action.kind == "SCAN_GALAXY":
        msg_txt = f"{bounded_api_str(res.message, default='')} {bounded_api_str(res.action.target_name, default='')}"
        m_sys = _GAL_SYS_RE.search(msg_txt)
        if m_sys:
            intel_sync.persist_galaxy_scan(int(m_sys.group(1)), int(m_sys.group(2)), gal_slots)


def apply_transmitter_and_throttle_effects(
    ctx: BridgeContext,
    lifecycle: BridgeLifecycleService,
    hooks: ExecutionHooks,
    executor: ActionExecutor,
    state: GameState,
    res: ExecutionResult,
) -> None:
    """Record transmitter activations and blast-furnace throttle/overflow pause timestamps."""
    if res.action.kind == "ACTIVATE_TRANSMITTER" and hooks.on_note_transmitter_activation is not None:
        tm_info = executor.last_transmitter_info
        tm_map: Mapping[str, object] | None = tm_info if isinstance(tm_info, Mapping) else None
        free_rem_val = (
            safe_api_int(tm_map.get("free_spins"), 0)
            if (tm_map is not None and tm_map.get("free_spins") is not None)
            else None
        )
        rew_val = str(tm_map.get("latest_reward") or "") if tm_map is not None else ""
        db_checked = bool(tm_map.get("daily_bonus_checked", True)) if tm_map is not None else True
        db_collected = bool(tm_map.get("daily_bonus_collected", False)) if tm_map is not None else False
        if res.executed:
            hooks.on_note_transmitter_activation(
                activated=True,
                free_spins_remaining=free_rem_val,
                reward=rew_val,
                daily_bonus_checked=db_checked,
                daily_bonus_collected=db_collected,
                daily_bonus_available=False,
            )
        elif tm_map is not None and safe_api_int(tm_map.get("free_spins"), 1) <= 0 and bool(res.validated):
            hooks.on_note_transmitter_activation(
                activated=False,
                free_spins_remaining=0,
                daily_bonus_checked=db_checked,
                daily_bonus_collected=db_collected,
                daily_bonus_available=False,
            )

    if res.executed and res.action.kind == "THROTTLE_BUILDING":
        pid_i = game_data.cap_json_int(safe_api_int(res.action.planet_id, 0))
        now_wall = time.time()
        ctx.throttle_timestamps[pid_i] = now_wall
        if pid_i > 0 and safe_api_int(res.action.target_id, 0) == 2:
            pct_set = safe_api_int(res.action.target_level, 0)
            rsn_th = bounded_api_str(res.action.reason, default="").lower()
            p_th = next((p for p in (state.planets or ()) if p.planet_id == pid_i), None)
            live_overflow = False
            if p_th is not None:
                sto_m = safe_api_int(p_th.storage.metal, 0)
                res_m = safe_api_int(p_th.resources.metal, 0)
                sto_p = safe_api_int(p_th.storage.pig_iron, 0)
                res_p = safe_api_int(p_th.resources.pig_iron, 0)
                live_overflow = sto_m > 0 and res_m >= sto_m and (sto_p <= 0 or res_p < sto_p)
            if pct_set == 0 and ("metal storage full" in rsn_th or live_overflow):
                ctx.metal_overflow_paused_planets[pid_i] = now_wall
                lifecycle.persist_runtime_state()
            elif ctx.metal_overflow_paused_planets.pop(pid_i, None) is not None:
                lifecycle.persist_runtime_state()


def _record_observed_fleet_slots_fallback(
    ctx: BridgeContext, planet: PlanetState | None, available: int | None
) -> None:
    if planet is None or available is None or isinstance(available, bool):
        return
    avail_i = safe_api_int(available, -1)
    if avail_i < 0:
        return
    pid_i = int(planet.planet_id)
    b10_lvl = max(0, planet.building_level(10))
    changed = False
    if pid_i > 0 and ctx.observed_fleet_slots_by_pid.get(pid_i) != (b10_lvl, avail_i):
        ctx.observed_fleet_slots_by_pid[pid_i] = (b10_lvl, avail_i)
        changed = True
    if b10_lvl >= 1 and avail_i in (b10_lvl, b10_lvl + 1):
        new_off = avail_i - b10_lvl
        if ctx.observed_fleet_slot_base_offset != new_off:
            ctx.observed_fleet_slot_base_offset = new_off
            changed = True
    if changed:
        ctx.invalidate_dashboard_cache()


def apply_fleet_and_target_block_effects(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    hooks: ExecutionHooks,
    state: GameState,
    res: ExecutionResult,
) -> None:
    """Update observed fleet slots, fleet-slot cooldowns, server protection blocks, and debris clears."""
    if res.action.kind not in _FLEET_KINDS:
        return
    pid_f = game_data.cap_json_int(safe_api_int(res.action.planet_id, 0))
    origin_p = next((p for p in (state.planets or ()) if p.planet_id == pid_f), None)
    if res.observed_fleet_slots_available is not None:
        if hooks.on_record_observed_fleet_slots is not None:
            hooks.on_record_observed_fleet_slots(origin_p, res.observed_fleet_slots_available)
        else:
            _record_observed_fleet_slots_fallback(ctx, origin_p, res.observed_fleet_slots_available)
    if pid_f > 0:
        if res.executed:
            ctx.fleet_slot_cooldown_until.pop(pid_f, None)
        else:
            msg_l = bounded_api_str(res.message, default="").lower()
            if any(marker in msg_l for marker in BridgeConstants.FLEET_SLOT_BUSY_MARKERS):
                cd_dur_s = compute_fleet_slot_busy_cooldown_s(ctx, state, origin_p)
                ctx.fleet_slot_cooldown_until[pid_f] = time.monotonic() + cd_dur_s

    tname_f = game_data.safe_key(res.action.target_name) if res.action.target_name is not None else ""
    m_fcoords = _COORDS_3_RE.search(tname_f)
    f_coords = normalize_coords(m_fcoords.group(1)) if m_fcoords else ""
    if f_coords and res.target_cooldown_seconds is not None:
        cd_s = safe_api_float(res.target_cooldown_seconds, 0.0)
        if cd_s > 0.0:
            now_wall = time.time()
            until_ts = round(now_wall + cd_s, 3)
            b_reason = bounded_api_str(
                res.target_block_reason or res.message,
                default="Mission blocked by server protection",
            )
            ctx.target_blocks[f_coords] = (until_ts, b_reason)
            if ctx.db is not None:
                ctx.db.record_target_block(
                    f_coords,
                    blocked_until_ts=until_ts,
                    reason=b_reason,
                    is_bash=bool(res.target_is_bash_block),
                    ts=now_wall,
                )
                state_cache.refresh_raid_targets_locked(limit=100, state_override=state)
            else:
                ctx.raid_targets = state_cache.enrich_raid_targets(ctx.raid_targets)

    if f_coords and res.clear_target_debris:
        ctx.galaxy_debris_amounts.pop(f_coords, None)
        if ctx.db is not None:
            ctx.db.set_target_debris(f_coords, False)
            state_cache.refresh_raid_targets_locked(limit=100, state_override=state)
        else:
            updated_rt: list[dict[str, object]] = []
            for r_item in ctx.raid_targets:
                c_item = dict(r_item)
                if normalize_coords(str(c_item.get("coords") or c_item.get("target_coords") or "")) == f_coords:
                    c_item["has_debris"] = False
                updated_rt.append(c_item)
            ctx.raid_targets = tuple(updated_rt)


def apply_raid_transport_ship_colony_effects(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    intel_sync: IntelSyncService,
    lifecycle: BridgeLifecycleService,
    hooks: ExecutionHooks,
    state: GameState,
    res: ExecutionResult,
    cmd: str,
) -> None:
    """Record raids, transport dispatches, AI ship budget spends, and colony outcomes."""
    if res.executed and res.action.kind == "ATTACK" and ctx.db is not None:
        tname_s = game_data.safe_key(res.action.target_name) if res.action.target_name is not None else ""
        reason_s = game_data.safe_key(res.action.reason) if res.action.reason is not None else ""
        m_coords = _COORDS_3_RE.search(tname_s)
        t_coords = normalize_coords(m_coords.group(1) if m_coords else tname_s)
        reason_l = f"{tname_s} {reason_s}".lower()
        target_row: Mapping[str, object] | None = next(
            (
                r
                for r in (ctx.raid_targets or ())
                if normalize_coords(str(r.get("coords") or r.get("target_coords") or "")) == t_coords
            ),
            None,
        )
        if target_row is None:
            target_row = ctx.db.get_spy_target_by_coords(t_coords)
        if target_row is None:
            target_row = next(
                (
                    r
                    for r in ctx.db.get_spy_targets(limit=200)
                    if normalize_coords(str(r.get("coords") or r.get("target_coords") or "")) == t_coords
                ),
                None,
            )
        row_exempt = bool(
            target_row is not None
            and (
                target_row.get("is_spacia")
                or target_row.get("is_inactive")
                or game_data.safe_key(target_row.get("status_color") or "").lower() == "yellow"
            )
        )
        is_exempt = row_exempt or ("spacia" in reason_l or "inactive" in reason_l or "yellow" in reason_l)
        ctx.db.record_raid(t_coords, is_exempt=is_exempt, outcome=res.message)
        state_cache.refresh_raid_targets_locked(limit=100, state_override=state)

    if res.executed and res.action.kind == "TRANSPORT" and hooks.on_record_transport_dispatch is not None:
        orig_tr = normalize_coords(str(res.action.planet_coords or ""))
        if not orig_tr:
            orig_p = next((p for p in state.planets if p.planet_id == res.action.planet_id), None)
            orig_tr = normalize_coords(orig_p.coords or "") if orig_p is not None else ""
        dest_tr = extract_coords_from_text(res.action.target_name or "") or extract_coords_from_text(res.message or "")
        if orig_tr and dest_tr and res.action.cost and not res.action.cost.is_zero():
            hooks.on_record_transport_dispatch(
                origin_coords=orig_tr,
                dest_coords=dest_tr,
                cargo=res.action.cost,
                ships=bounded_api_str(res.message, default="")[:140],
                source="dispatch",
            )

    if res.executed and res.action.kind == "BUILD_SHIP":
        ship_sid = game_data.safe_optional_int(res.action.target_id, None)
        ship_qty = max(1, safe_api_int(res.action.target_level, 1))
        adaptive_exec = bool(ctx.strategy_config.ai_adaptive_execution)
        if AI_INFRA_CARGO_TAG in str(res.action.reason or "") or (adaptive_exec and ship_sid in (1, 2)):
            if hooks.on_record_infra_transport_spend is not None:
                hooks.on_record_infra_transport_spend(res.action, ship_qty)
            lifecycle.persist_runtime_state()
        elif adaptive_exec and ship_sid != 9:
            if hooks.on_record_spare_ship_spend is not None:
                hooks.on_record_spare_ship_spend(res.action, ship_qty)
            lifecycle.persist_runtime_state()

    if res.action.kind in ("COLONIZE", "ABANDON_COLONY"):
        intel_sync.record_colony_dispatch_outcome_locked(state, res, cmd)


def record_action_and_manual_queue(
    ctx: BridgeContext,
    lifecycle: BridgeLifecycleService,
    hooks: ExecutionHooks,
    cmd: str,
    res: ExecutionResult,
    *,
    dry_run: bool,
    manual_queue_id: int | None,
    source: str,
) -> None:
    """Append action telemetry to DB, complete matching manual queue entries, and raise alerts."""
    if ctx.db is None:
        return
    ctx.db.record_action(
        source=source,
        command=cmd,
        action=res.action,
        executed=res.executed,
        dry_run=res.dry_run,
        message=res.message,
    )
    if res.executed and not dry_run:
        if manual_queue_id is not None and hooks.on_record_manual_queue_completion is not None:
            hooks.on_record_manual_queue_completion(manual_queue_id, res.message)
        act_tname = game_data.safe_key(res.action.target_name) if res.action.target_name is not None else ""
        m_tc = _COORDS_2_OR_3_RE.search(act_tname)
        exec_lvl = game_data.safe_optional_int(res.action.target_level, None)
        ctx.db.complete_matching_manual_command(
            kind=res.action.kind,
            planet_id=game_data.safe_int(res.action.planet_id, 0),
            target_id=game_data.safe_optional_int(res.action.target_id, None),
            target_coords=m_tc.group(1) if m_tc else act_tname,
            executed_level=exec_lvl if res.action.kind in ("BUILD", "DEMOLISH_BUILDING", "RESEARCH") else None,
            executed_qty=exec_lvl if res.action.kind == "BUILD_SHIP" else None,
            message=res.message,
        )
        ctx.dash_ag_cache_json = None
    if not dry_run:
        lifecycle.record_action_validation_notification_locked(cmd, res)
        lifecycle.persist_runtime_state()
