"""Pre-dispatch safety checks for SPY, Spacia, and RECYCLE live actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
import logging
import math
import re
import time

from spaceinvasion.combat.fleet import fleet_cargo_capacity, normalize_coords, parse_galaxy_system_html
from spaceinvasion.core import game_data
from spaceinvasion.core.redaction import redact_text
from spaceinvasion.core.state import (
    GameState,
    PlanetState,
    count_in_flight_fleets_for_planet,
    count_in_flight_spy_fleets_for_planet,
    extract_in_flight_recycle_coords,
    is_in_flight_fleet_job,
)
from spaceinvasion.planner.strategy import PlannedAction
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._intel_messages import is_real_client_method
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.auth import AntiBotChallengeError
from spaceinvasion.session.client import GameBrowserClient, RateLimitError

_LOGGER = logging.getLogger(__name__)
_COORDS_3_RE = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)

SerializeActionFn = Callable[[PlannedAction, GameState | None], dict[str, object]]


def max_fleet_slots_for_planet(ctx: BridgeContext, planet: PlanetState | None) -> int:
    """Return max concurrent fleet slots for `planet` using observed offsets when available."""
    if planet is None:
        return 1
    b10_lvl = max(0, planet.building_level(10))
    obs_pair = ctx.observed_fleet_slots_by_pid.get(int(planet.planet_id))
    if obs_pair is not None and obs_pair[0] == b10_lvl:
        return max(0, int(obs_pair[1]))
    if ctx.observed_fleet_slot_base_offset is not None:
        return max(0, b10_lvl + int(ctx.observed_fleet_slot_base_offset))
    return 1 + b10_lvl


def compute_fleet_slot_busy_cooldown_s(ctx: BridgeContext, state: GameState, planet: PlanetState | None) -> float:
    """Compute cooldown seconds until the earliest in-flight fleet from `planet` frees a slot."""
    if planet is None:
        return BridgeConstants.FLEET_SLOT_COOLDOWN_S
    norm_p = normalize_coords(planet.coords or "")
    earliest_rem: float | None = None
    for job in state.active_queues or ():
        if not is_in_flight_fleet_job(job):
            continue
        q_orig = normalize_coords(job.origin_coords or "")
        if q_orig and norm_p and q_orig != norm_p:
            continue
        rem_s = job.remaining_seconds
        if isinstance(rem_s, (int, float)) and not isinstance(rem_s, bool) and math.isfinite(rem_s) and rem_s > 0.0:
            if earliest_rem is None or float(rem_s) < earliest_rem:
                earliest_rem = float(rem_s)
    if earliest_rem is not None:
        return max(30.0, min(earliest_rem + 2.0, 1800.0))
    return BridgeConstants.FLEET_SLOT_COOLDOWN_S


def build_abort_response(
    ctx: BridgeContext,
    cmd: str,
    planned: PlannedAction,
    state: GameState,
    abort_msg: str,
    *,
    serialize_action_fn: SerializeActionFn,
    source: str = "api",
) -> dict[str, object]:
    """Record a pre-dispatch abort and return the canonical API response dict."""
    aborted_act = replace(planned, executable_now=False)
    if ctx.db is not None:
        ctx.db.record_action(
            source=source,
            command=cmd,
            action=aborted_act,
            executed=False,
            dry_run=False,
            message=abort_msg,
        )
    return sanitize_json_dict(
        {
            "ok": True,
            "command": cmd,
            "executed": False,
            "validated": True,
            "dry_run": False,
            "message": abort_msg,
            "action": serialize_action_fn(aborted_act, state),
            "next_action": None,
        }
    )


def check_spy_reserved_slots_preflight(
    ctx: BridgeContext,
    cmd: str,
    state: GameState,
    planned: PlannedAction,
    *,
    serialize_action_fn: SerializeActionFn,
) -> dict[str, object] | None:
    """Enforce `spy_reserved_slots_only` cap before dispatching any live SPY probe."""
    if planned.kind != "SPY" or not bool(ctx.strategy_config.spy_reserved_slots_only):
        return None
    spy_planet = (
        next((p for p in (state.planets or ()) if p.planet_id == planned.planet_id), None) or state.active_planet()
    )
    if spy_planet is None:
        return None
    max_spy_sl = max_fleet_slots_for_planet(ctx, spy_planet)
    spy_cap = min(max_spy_sl, max(0, safe_api_int(ctx.strategy_config.reserved_spy_slots, 1)))
    spy_used = count_in_flight_spy_fleets_for_planet(state, spy_planet)
    if spy_used < spy_cap:
        return None
    abort_msg = (
        f"Aborted: Spy restricted to reserved slot(s) ({spy_used}/{spy_cap} spy slots in use on [{spy_planet.coords}])."
    )
    return build_abort_response(ctx, cmd, planned, state, abort_msg, serialize_action_fn=serialize_action_fn)


def _is_expected_spacia_target(
    ctx: BridgeContext,
    planned: PlannedAction,
    sp_gal: int,
    sp_sys: int,
    norm_sp_target: str,
) -> bool:
    sp_tname = game_data.safe_key(planned.target_name) if planned.target_name is not None else ""
    sp_reason = game_data.safe_key(planned.reason) if planned.reason is not None else ""
    if "spacia" in sp_tname.lower() or "spacia" in sp_reason.lower():
        return True
    for rt_row in ctx.raid_targets or ():
        if normalize_coords(str(rt_row.get("coords") or rt_row.get("target_coords") or "")) == norm_sp_target:
            return bool(rt_row.get("is_spacia")) or str(rt_row.get("owner") or "").strip().lower() == "spacia"
    if ctx.db is not None:
        st_row = ctx.db.get_spy_target_by_coords(norm_sp_target)
        if isinstance(st_row, Mapping) and (
            bool(st_row.get("is_spacia")) or str(st_row.get("owner") or "").strip().lower() == "spacia"
        ):
            return True
        for gs_row in ctx.db.get_galaxy_slots(galaxy=sp_gal, system=sp_sys, limit=20):
            if normalize_coords(str(gs_row.get("coords") or "")) == norm_sp_target:
                return bool(gs_row.get("is_spacia")) or str(gs_row.get("owner") or "").strip().lower() == "spacia"
    return False


def _open_galaxy_live_safe(client: GameBrowserClient, galaxy: int, system: int) -> str:
    try:
        return StateCacheService.open_galaxy(client, galaxy=galaxy, system=system, priority="interactive")
    except (RateLimitError, AntiBotChallengeError):
        raise
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Live galaxy preflight read failed for %02d:%03d", galaxy, system, exc_info=True)
        return ""


def check_spacia_live_galaxy_preflight(
    ctx: BridgeContext,
    intel_sync: IntelSyncService,
    client: GameBrowserClient | None,
    cmd: str,
    state: GameState,
    planned: PlannedAction,
    *,
    serialize_action_fn: SerializeActionFn,
) -> dict[str, object] | None:
    """Confirm Spacia target still exists in live `internalGalaxy` before SPY/ATTACK dispatch."""
    if planned.kind not in ("SPY", "ATTACK") or client is None:
        return None
    sp_tname = game_data.safe_key(planned.target_name) if planned.target_name is not None else ""
    sp_reason = game_data.safe_key(planned.reason) if planned.reason is not None else ""
    if "free slot" in sp_tname.lower() or "free slot" in sp_reason.lower():
        return None
    match = _COORDS_3_RE.search(sp_tname)
    if not match:
        return None
    sp_gal, sp_sys, sp_pos = int(match.group(1)), int(match.group(2)), int(match.group(3))
    norm_target = f"{sp_gal:02d}:{sp_sys:03d}:{sp_pos:02d}"
    if not _is_expected_spacia_target(ctx, planned, sp_gal, sp_sys, norm_target):
        return None
    if not is_real_client_method(client, "open_galaxy"):
        return None
    origin = next((p for p in (state.planets or ()) if p.planet_id == planned.planet_id), None) or state.active_planet()
    if origin is not None:
        if planned.kind == "SPY" and safe_api_int(origin.ships.get("s10", 0), 0) <= 0:
            return build_abort_response(
                ctx,
                cmd,
                planned,
                state,
                f"Aborted: no spy probes (s10) stationed on [{origin.coords}].",
                serialize_action_fn=serialize_action_fn,
            )
        max_sl = max_fleet_slots_for_planet(ctx, origin)
        used_sl = count_in_flight_fleets_for_planet(state, origin)
        cd_rem = float(ctx.fleet_slot_cooldown_until.get(int(origin.planet_id), 0.0)) - time.monotonic()
        if used_sl >= max_sl or cd_rem > 0.0:
            return build_abort_response(
                ctx,
                cmd,
                planned,
                state,
                f"Aborted: no free fleet slot on [{origin.coords}] ({used_sl}/{max_sl} in flight).",
                serialize_action_fn=serialize_action_fn,
            )

    gal_html = _open_galaxy_live_safe(client, sp_gal, sp_sys)
    live_slots = parse_galaxy_system_html(gal_html, galaxy=sp_gal, system=sp_sys) if gal_html.strip() else ()
    live_slot = None
    if live_slots:
        intel_sync.persist_galaxy_scan(sp_gal, sp_sys, live_slots)
        intel_sync.persist_galaxy_slots(live_slots)
        live_slot = next((s for s in live_slots if normalize_coords(s.coords) == norm_target), None)
    if live_slot is None:
        abort_msg = f"Aborted: live galaxy view could not confirm Spacia at [{norm_target}] before {planned.kind.lower()} dispatch."
        _LOGGER.info("%s", redact_text(f"[API] {abort_msg}"))
        return build_abort_response(ctx, cmd, planned, state, abort_msg, serialize_action_fn=serialize_action_fn)
    if not live_slot.is_spacia:
        ctx.dash_ag_cache_json = None
        live_col = str(live_slot.status_color or "").strip().lower()
        live_own = str(live_slot.owner or "").strip()
        if live_col == "free" and not live_own:
            abort_msg = (
                f"Aborted: live galaxy check confirmed [{norm_target}] is now uninhabited "
                "(Spacia despawned); cleared stale Spacia target."
            )
        else:
            abort_msg = (
                f"Aborted: live galaxy check confirmed [{norm_target}] is no longer Spacia "
                f"(now {live_own or 'occupied'} [{live_col or 'unknown'}]); updated galaxy intel."
            )
        _LOGGER.info("%s", redact_text(f"[API] {abort_msg}"))
        return build_abort_response(ctx, cmd, planned, state, abort_msg, serialize_action_fn=serialize_action_fn)
    return None


def check_recycle_live_galaxy_preflight(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    intel_sync: IntelSyncService,
    client: GameBrowserClient | None,
    cmd: str,
    state: GameState,
    planned: PlannedAction,
    *,
    serialize_action_fn: SerializeActionFn,
) -> tuple[PlannedAction, dict[str, object] | None]:
    """Verify recycler availability, no duplicate wave, live debris presence, and resize recycler count."""
    if planned.kind != "RECYCLE" or client is None:
        return planned, None
    origin = next((p for p in (state.planets or ()) if p.planet_id == planned.planet_id), None) or state.active_planet()
    if origin is not None:
        if safe_api_int(origin.ships.get("s11", 0), 0) <= 0:
            return planned, build_abort_response(
                ctx,
                cmd,
                planned,
                state,
                f"Aborted: no recyclers (s11) stationed on [{origin.coords}].",
                serialize_action_fn=serialize_action_fn,
            )
        max_sl = max_fleet_slots_for_planet(ctx, origin)
        used_sl = count_in_flight_fleets_for_planet(state, origin)
        cd_rem = float(ctx.fleet_slot_cooldown_until.get(int(origin.planet_id), 0.0)) - time.monotonic()
        if used_sl >= max_sl or cd_rem > 0.0:
            return planned, build_abort_response(
                ctx,
                cmd,
                planned,
                state,
                f"Aborted: no free fleet slot on [{origin.coords}] ({used_sl}/{max_sl} in flight).",
                serialize_action_fn=serialize_action_fn,
            )
    tname_pre = game_data.safe_key(planned.target_name) if planned.target_name is not None else ""
    match = _COORDS_3_RE.search(tname_pre)
    if not match:
        return planned, None
    t_gal, t_sys, t_pos = int(match.group(1)), int(match.group(2)), int(match.group(3))
    norm_target = f"{t_gal:02d}:{t_sys:03d}:{t_pos:02d}"
    if cmd != "recycle" and norm_target in extract_in_flight_recycle_coords(state):
        return planned, build_abort_response(
            ctx,
            cmd,
            planned,
            state,
            f"Aborted: Recyclers are already in transit to [{norm_target}].",
            serialize_action_fn=serialize_action_fn,
        )
    if not is_real_client_method(client, "open_galaxy"):
        return planned, None
    gal_html = _open_galaxy_live_safe(client, t_gal, t_sys)
    live_slots = parse_galaxy_system_html(gal_html, galaxy=t_gal, system=t_sys) if gal_html.strip() else ()
    live_slot = None
    if live_slots:
        intel_sync.persist_galaxy_scan(t_gal, t_sys, live_slots)
        intel_sync.persist_galaxy_slots(live_slots)
        live_slot = next((s for s in live_slots if normalize_coords(s.coords) == norm_target), None)
    if live_slot is None:
        return planned, build_abort_response(
            ctx,
            cmd,
            planned,
            state,
            f"Aborted: live galaxy view could not confirm debris at [{norm_target}] before dispatch.",
            serialize_action_fn=serialize_action_fn,
        )
    if not live_slot.has_debris:
        if ctx.db is not None:
            ctx.db.set_target_debris(norm_target, False)
        ctx.galaxy_debris_amounts.pop(norm_target, None)
        state_cache.refresh_raid_targets_locked(limit=100, state_override=state)
        ctx.dash_ag_cache_json = None
        return planned, build_abort_response(
            ctx,
            cmd,
            planned,
            state,
            f"Aborted: live galaxy check confirmed [{norm_target}] is clean (no debris field); cleared stale debris flag.",
            serialize_action_fn=serialize_action_fn,
        )
    if cmd != "recycle":
        live_tot = (
            max(0, safe_api_int(live_slot.debris_pig_iron, 0))
            + max(0, safe_api_int(live_slot.debris_metal, 0))
            + max(0, safe_api_int(live_slot.debris_kryptonite, 0))
            + max(0, safe_api_int(live_slot.debris_spice, 0))
        )
        if live_tot > 0:
            miner_lvl = safe_api_int((state.specs or {}).get("miner", 0), 0)
            per_rec_cap = max(1, fleet_cargo_capacity({"s11": 1}, miner_spec_level=miner_lvl))
            needed_rec = max(1, math.ceil(live_tot / per_rec_cap))
            if safe_api_int(planned.target_level, 0) > needed_rec:
                planned = replace(planned, target_level=needed_rec)
    return planned, None
