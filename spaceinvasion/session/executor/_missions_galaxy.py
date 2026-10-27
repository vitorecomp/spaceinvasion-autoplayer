"""Galaxy scan and 2D Galaxy quick-spy helpers for `MissionRunner`."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
import re
import types
from typing import Protocol, runtime_checkable

from spaceinvasion.core import catalog
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.parsing.galaxy import parse_galaxy_system_html
from spaceinvasion.parsing.models import GalaxySlot, GameState
from spaceinvasion.session.executor._fleet_errors import is_unattackable_target_html
from spaceinvasion.session.executor._fleet_step1 import (
    BuildUpdatedStateFn,
    NavigateAndSelectFn,
    confirmed_mismatch_pid,
    mismatch_abort_result,
)
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import ExecutionResult, call_with_interactive_priority, safe_str

_GAL_SYS_RE = re.compile(r"(\d{1,2}):(\d{1,3})", re.ASCII)
_TARGET_COORDS_RE = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)
_ASTEROID_MARKER_RE = re.compile(r"\[\s*a\s*\]|\basteroid\b", re.IGNORECASE)


@runtime_checkable
class _GalaxyStepClient(Protocol):
    def step_galaxy_system(
        self,
        *,
        galaxy: int,
        system: int,
        pace: bool = False,
        priority: str = "normal",
    ) -> tuple[str, int, int]: ...


@runtime_checkable
class _LegacyOpenGalaxyClient(Protocol):
    def open_galaxy(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> str: ...


@runtime_checkable
class _QuickSpyDispatchClient(Protocol):
    def dispatch_galaxy_spy(
        self,
        *,
        galaxy: int,
        system: int,
        position: int,
        quantity: int = 1,
        is_asteroid: bool = False,
        priority: str = "interactive",
    ) -> Mapping[str, object]: ...


def _is_spaceinvasion_callable(fn: object) -> bool:
    if isinstance(fn, types.MethodType):
        return fn.__func__.__module__.startswith("spaceinvasion")
    if isinstance(fn, types.FunctionType):
        return fn.__module__.startswith("spaceinvasion")
    return False


def _supports_step_galaxy(client: BrowserClientLike) -> bool:
    if not isinstance(client, _GalaxyStepClient):
        return False
    mod = type(client).__module__
    if "unittest.mock" in mod:
        return "step_galaxy_system" in vars(client)
    return mod.startswith("spaceinvasion")


def scan_galaxy_system(
    client: BrowserClientLike,
    action: PlannedAction,
) -> tuple[ExecutionResult, list[GalaxySlot]]:
    """Scan a Galaxy `[GG:SSS]` system and return `(ExecutionResult, parsed_slots)`."""
    tname_s = safe_str(action.target_name)
    coords_s = safe_str(action.planet_coords)
    m = _GAL_SYS_RE.search(tname_s) or _GAL_SYS_RE.search(coords_s)
    gal, sys_num = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
    call_with_interactive_priority(client.ensure_in_game)
    if _supports_step_galaxy(client) and isinstance(client, _GalaxyStepClient):
        html, gal, sys_num = client.step_galaxy_system(
            galaxy=gal,
            system=sys_num,
            pace=True,
            priority="interactive",
        )
    else:
        open_client: _LegacyOpenGalaxyClient = client
        html = open_client.open_galaxy(galaxy=gal, system=sys_num)
        actual_coords = client.last_galaxy_coords
        if isinstance(actual_coords, tuple) and len(actual_coords) == 2:
            c0, c1 = actual_coords
            if isinstance(c0, int) and isinstance(c1, int):
                gal, sys_num = c0, c1
    slots = list(parse_galaxy_system_html(html, galaxy=gal, system=sys_num))
    occupied = [s for s in slots if s.status_color != "free"]
    yellows = [s for s in slots if s.status_color == "yellow"]
    spacias = [s for s in slots if s.is_spacia]
    result = ExecutionResult(
        executed=True,
        dry_run=False,
        action=action,
        message=(
            f"Scanned Galaxy [{gal:02d}:{sys_num:03d}]: "
            f"{len(occupied)} occupied ({len(yellows)} inactive yellow, {len(spacias)} Spacia)."
        ),
    )
    return result, slots


def _evaluate_free_slot_ecology(
    ctx: ExecutorContext,
    action: PlannedAction,
    parsed_q_slots: list[GalaxySlot],
    *,
    t_gal: int,
    t_sys: int,
    t_pos: int,
) -> ExecutionResult | None:
    matched_free_slot = next(
        (
            sl
            for sl in parsed_q_slots
            if sl.position == t_pos
            and sl.status_color == "free"
            and (sl.uninhabited_fields is not None or sl.colonize_max_fields is not None)
        ),
        None,
    )
    if matched_free_slot is None:
        return None
    ctx.executed_timestamps.append(ctx.clock())
    if matched_free_slot.uninhabited_fields is not None:
        eco_desc = f"{matched_free_slot.uninhabited_fields} fields"
    else:
        c_min_f = matched_free_slot.colonize_min_fields or matched_free_slot.colonize_max_fields
        eco_desc = f"{c_min_f}–{matched_free_slot.colonize_max_fields} fields"
    if matched_free_slot.temperature is not None:
        eco_desc += f" ({matched_free_slot.temperature}°C)"
    free_cnt = sum(
        1
        for sl in parsed_q_slots
        if sl.status_color == "free" and (sl.uninhabited_fields is not None or sl.colonize_max_fields is not None)
    )
    return ExecutionResult(
        executed=True,
        dry_run=False,
        action=action,
        message=(
            f"Probed free slot [{t_gal:02d}:{t_sys:03d}:{t_pos:02d}] ecology via 2D Galaxy: "
            f"{eco_desc} ({free_cnt} free slot(s) graded in [{t_gal:02d}:{t_sys:03d}])."
        ),
        validated=True,
    )


def _handle_quick_spy_response(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    state: GameState,
    action: PlannedAction,
    qspy: Mapping[str, object],
    *,
    t_gal: int,
    t_sys: int,
    t_pos: int,
    probe_count: int,
    build_updated_state_fn: BuildUpdatedStateFn,
) -> tuple[ExecutionResult | None, list[GalaxySlot] | None]:
    parsed_q_slots: list[GalaxySlot] = []
    q_html = str(qspy.get("html") or "")
    if q_html:
        with contextlib.suppress(ValueError, TypeError, KeyError, AttributeError):
            parsed_q_slots = list(
                parse_galaxy_system_html(
                    q_html,
                    galaxy=catalog.safe_int(qspy.get("galaxy"), t_gal) or t_gal,
                    system=catalog.safe_int(qspy.get("system"), t_sys) or t_sys,
                )
            )
    slots_update: list[GalaxySlot] | None = parsed_q_slots if parsed_q_slots else None

    if qspy.get("started"):
        ctx.executed_timestamps.append(ctx.clock())
        updated = call_with_interactive_priority(client.read_planets_overview, refresh=True)
        updated_state = build_updated_state_fn(client, state, action, updated, is_fleet=True)
        return (
            ExecutionResult(
                executed=True,
                dry_run=False,
                action=action,
                message=(
                    f"Dispatched spy probe ({probe_count}x s10) via 2D Galaxy to "
                    f"[{t_gal:02d}:{t_sys:03d}:{t_pos:02d}] from [{safe_str(action.planet_coords)}]."
                ),
                updated_state=updated_state,
            ),
            slots_update,
        )

    eco_res = _evaluate_free_slot_ecology(ctx, action, parsed_q_slots, t_gal=t_gal, t_sys=t_sys, t_pos=t_pos)
    if eco_res is not None:
        return eco_res, slots_update

    q_err = str(qspy.get("error") or "").strip()
    if q_err and is_unattackable_target_html(q_err):
        return (
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {q_err}.",
                target_cooldown_seconds=21600.0,
                target_block_reason=q_err,
                validated=False,
                validation_error=q_err,
            ),
            slots_update,
        )
    return None, slots_update


def try_quick_galaxy_free_slot_spy(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    state: GameState,
    action: PlannedAction,
    *,
    probe_count: int,
    active_planet_confirmed: bool,
    navigate_and_select_fn: NavigateAndSelectFn,
    build_updated_state_fn: BuildUpdatedStateFn,
) -> tuple[ExecutionResult | None, list[GalaxySlot] | None]:
    """Attempt 2D Galaxy quick-spy for free-slot ecology probes; return `(result, parsed_slots)` or `(None, ...)`."""
    tname_s = safe_str(action.target_name)
    reason_s = safe_str(action.reason)
    is_free_slot_probe = "(free slot)" in tname_s.lower() or "probe free slot" in reason_s.lower()
    m_coords = _TARGET_COORDS_RE.search(tname_s)
    if not (is_free_slot_probe and m_coords is not None and isinstance(client, _QuickSpyDispatchClient)):
        return None, None
    if not _is_spaceinvasion_callable(client.dispatch_galaxy_spy):
        return None, None

    t_gal, t_sys, t_pos = int(m_coords.group(1)), int(m_coords.group(2)), int(m_coords.group(3))
    if len(state.planets) > 1 or not active_planet_confirmed or state.active_planet_id != action.planet_id:
        navigate_and_select_fn(
            client, state, "internalHome", action.planet_id, active_planet_confirmed=active_planet_confirmed
        )
        mismatch_pid = confirmed_mismatch_pid(ctx, action.planet_id)
        if mismatch_pid is not None:
            return mismatch_abort_result(action, mismatch_pid), None

    is_ast = bool(_ASTEROID_MARKER_RE.search(f"{tname_s} {reason_s}"))
    qspy = client.dispatch_galaxy_spy(
        galaxy=t_gal,
        system=t_sys,
        position=t_pos,
        quantity=probe_count,
        is_asteroid=is_ast,
        priority="interactive",
    )
    if not isinstance(qspy, Mapping):
        return None, None
    return _handle_quick_spy_response(
        ctx,
        client,
        state,
        action,
        qspy,
        t_gal=t_gal,
        t_sys=t_sys,
        t_pos=t_pos,
        probe_count=probe_count,
        build_updated_state_fn=build_updated_state_fn,
    )
