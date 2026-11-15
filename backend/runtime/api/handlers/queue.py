"""Non-browser manual command queue `@command` handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from backend.core import catalog as game_data
from backend.core.catalog import SHIP_SLUG_BY_KEY
from backend.planner.heuristics.manual_queue import merge_manual_queue_actions
from backend.runtime.api.handlers._bridge_proto import BridgeApiFacade
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError, FieldBounds
from backend.runtime.bridge._helpers import (
    bounded_api_str,
    parse_command_int,
    safe_api_int,
    sanitize_json_dict,
)


class _UnsetType:
    """Deterministic sentinel distinguishing omitted JSON keys from explicit `null`."""

    def __repr__(self) -> str:
        return "UNSET"


_UNSET: Final[_UnsetType] = _UnsetType()

_ALLOWED_QUEUE_KINDS: frozenset[str] = frozenset(
    (
        "BUILD DEMOLISH_BUILDING RESEARCH BUILD_SHIP THROTTLE_BUILDING SPY TRANSPORT "
        "STATION ATTACK RECYCLE COLONIZE ABANDON_COLONY SCAN_GALAXY CLAIM_TUTORIAL"
    ).split()
)
_TARGET_ID_REQUIRED_KINDS: frozenset[str] = frozenset(
    {"BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING"}
)
_TARGET_COORDS_REQUIRED_KINDS: frozenset[str] = frozenset(
    {"SPY", "TRANSPORT", "STATION", "ATTACK", "RECYCLE", "COLONIZE"}
)


@dataclass(frozen=True)
class QueueAddParams:
    """Parameters for enqueueing a manual command into the persistent priority queue."""

    kind: object = None
    priority: object = "P1"
    position: object = "top"
    planet_id: int | None = field(default=0, metadata={"bounds": FieldBounds(min_val=0, max_val=2**63 - 1)})
    target_id: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=1, max_val=2**31 - 1)})
    target_level: object = None
    quantity: object = None
    percentage: object = None
    target_name: object = ""
    target_coords: object = ""
    note: object = ""
    deduplicate_pending: bool = False


@dataclass(frozen=True)
class QueueUpdateParams:
    """Parameters for reprioritizing, reordering, or editing a pending queue item."""

    queue_id: object = None
    priority: object = None
    move: object = None
    sort_rank: int | None = field(default=None, metadata={"bounds": FieldBounds(min_val=-1_000_000, max_val=1_000_000)})
    target_level: object = _UNSET
    note: object = _UNSET


@dataclass(frozen=True)
class QueueCancelParams:
    """Parameters for canceling a single pending manual queue item."""

    queue_id: object = None


@dataclass(frozen=True)
class QueueClearParams:
    """Parameters for clearing all pending manual queue items."""


def _resolve_default_target_name(norm_kind: str, tid: int | None, tcoords: str) -> str:
    if norm_kind in ("BUILD", "DEMOLISH_BUILDING", "THROTTLE_BUILDING") and tid is not None:
        info = game_data.resolve_unit("buildings", tid)
        return str((info or {}).get("name") or f"Building b{tid}")
    if norm_kind == "RESEARCH" and tid is not None:
        info = game_data.resolve_unit("research", f"r{tid}")
        return str((info or {}).get("name") or f"r{tid}")
    if norm_kind == "BUILD_SHIP" and tid is not None:
        s_slug = SHIP_SLUG_BY_KEY.get(f"s{tid}", f"s{tid}")
        s_inf = (
            game_data.resolve_unit("ships", s_slug)
            or game_data.resolve_unit("ships", f"s{tid}")
            or game_data.resolve_unit("ships", tid)
            or {}
        )
        return str(s_inf.get("name") or f"Ship s{tid}")
    return tcoords


def _review_queue_metal_status_locked(
    bridge: BridgeApiFacade,
    planet_id: int,
    pending_mq: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Inspect Blast furnace Metal production vs pinned queue position on `planet_id`."""
    if bridge.ctx.last_state is None or not pending_mq:
        return {"checked": False, "summary": ""}
    enriched = bridge.attach_raid_stats(bridge.ctx.last_state)
    planet = (
        next((p for p in enriched.planets if p.planet_id == planet_id), None)
        if planet_id > 0
        else enriched.active_planet()
    )
    if planet is None:
        return {"checked": False, "summary": ""}
    b2_lvl = planet.building_level(2)
    cur_b2_pct = max(0, min(100, safe_api_int((planet.economy_rates or {}).get("resource2", 100), 100)))
    jobs_snap = bridge.get_planner_jobs_status()
    in_ai_planner = jobs_snap.get("effective_planner_mode") == "ai_planner"
    merged, _ = merge_manual_queue_actions(
        enriched,
        [],
        pending_mq,
        config=bridge.ctx.strategy_config,
        reconcile_metal_production=not in_ai_planner,
    )
    p_acts = [a for a in merged if a.planet_id == planet.planet_id]
    bf_throttle = next(
        (a for a in p_acts if a.manual_queue_id is None and a.kind == "THROTTLE_BUILDING" and a.target_id == 2),
        None,
    )
    deferred_metal = next(
        (
            a
            for a in p_acts
            if a.manual_queue_id is not None and "[Metal production queued after" in str(a.reason or "")
        ),
        None,
    )
    pinned_needs_metal = any(a.manual_queue_id is not None and safe_api_int(a.missing.metal, 0) > 0 for a in p_acts)
    summary_txt = ""
    scheduled_pct: int | None = None
    if in_ai_planner and pinned_needs_metal:
        summary_txt = "Blast furnace controlled exclusively by AI-Planner (+ 100% Metal overflow guard)"
    elif bf_throttle is not None:
        scheduled_pct = safe_api_int(bf_throttle.target_level, cur_b2_pct)
        summary_txt = (
            f"Metal production scheduled: Blast furnace {cur_b2_pct}% -> {scheduled_pct}%"
            if scheduled_pct > 0
            else "Blast furnace held at 0% until earlier Pig iron item completes"
        )
    elif deferred_metal is not None:
        summary_txt = "Metal production queued after earlier Pig iron item completes"
    elif pinned_needs_metal:
        if b2_lvl <= 0:
            summary_txt = "Warning: pinned item needs Metal, and Colony has no Blast furnace (b2=0)"
        elif cur_b2_pct > 0:
            summary_txt = f"Metal production active (Blast furnace {cur_b2_pct}%)"
    return {
        "checked": True,
        "planet_id": planet.planet_id,
        "planet_coords": planet.coords,
        "blast_furnace_level": b2_lvl,
        "current_blast_furnace_pct": cur_b2_pct,
        "scheduled_blast_furnace_pct": scheduled_pct,
        "pinned_needs_metal": pinned_needs_metal,
        "summary": summary_txt,
    }


def _parse_queue_add_fields(
    params: QueueAddParams,
) -> tuple[str, str, str, int, int | None, int | None, str, str, str]:
    raw_kind = params.kind
    if not isinstance(raw_kind, str) or not raw_kind.strip():
        raise ApiRequestError("'queue_add' requires a non-empty string 'kind'.")
    norm_kind = raw_kind.strip().upper()
    if norm_kind not in _ALLOWED_QUEUE_KINDS:
        raise ApiRequestError(f"Unsupported queue command kind '{norm_kind}'.")
    raw_prio = str(params.priority or "P1").strip().upper()
    if raw_prio not in ("P0", "P1", "P2", "P3"):
        raise ApiRequestError("'priority' must be one of P0, P1, P2, P3.")
    raw_pos = str(params.position or "top").strip().lower()
    if raw_pos not in ("top", "bottom"):
        raw_pos = "top"
    pid = int(params.planet_id or 0)
    tid = params.target_id
    tlvl_raw = (
        params.target_level
        if params.target_level is not None
        else (params.quantity if params.quantity is not None else params.percentage)
    )
    tlvl = parse_command_int("target_level", tlvl_raw, allow_none=True, min_val=0, max_val=100_000)
    tname = bounded_api_str(params.target_name or "", default="")[:160]
    tcoords = bounded_api_str(params.target_coords or "", default="")[:64]
    note = bounded_api_str(params.note or "", default="")[:280]
    if norm_kind in _TARGET_ID_REQUIRED_KINDS and tid is None:
        raise ApiRequestError(f"'{norm_kind}' requires 'target_id'.")
    if norm_kind in _TARGET_COORDS_REQUIRED_KINDS and not (tcoords.strip() or tname.strip()):
        raise ApiRequestError(f"'{norm_kind}' requires 'target_coords'.")
    return norm_kind, raw_prio, raw_pos, pid, tid, tlvl, tname, tcoords, note


def _handle_queue_add(bridge: BridgeApiFacade, params: QueueAddParams) -> dict[str, object]:
    """Enqueue a manual command into the persistent priority queue and review Metal production."""
    norm_kind, raw_prio, raw_pos, pid, tid, tlvl, tname, tcoords, note = _parse_queue_add_fields(params)
    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database is not configured on this API bridge.")
        pcoords = ""
        if bridge.ctx.last_state is not None:
            p_match = (
                next((p for p in bridge.ctx.last_state.planets if p.planet_id == pid), None)
                if pid > 0
                else bridge.ctx.last_state.active_planet()
            )
            if p_match is not None:
                if pid <= 0:
                    pid = p_match.planet_id
                pcoords = p_match.coords
        if not tname:
            tname = _resolve_default_target_name(norm_kind, tid, tcoords)
        item = db.enqueue_manual_command(
            kind=norm_kind,
            priority=raw_prio,
            position=raw_pos,
            planet_id=int(pid),
            planet_coords=pcoords,
            target_id=tid,
            target_name=tname,
            target_coords=tcoords or tname,
            target_level=tlvl,
            note=note,
            deduplicate_pending=bool(params.deduplicate_pending),
        )
        bridge.ctx.invalidate_dashboard_cache()
        pending: list[dict[str, object]] = [dict(r) for r in db.get_manual_queue(status="pending", limit=200)]
        metal_review = _review_queue_metal_status_locked(bridge, safe_api_int(item.get("planet_id"), 0), pending)

    bridge.publish_snapshot()
    summary_str = str(metal_review.get("summary") or "")
    msg_suffix = f" ({summary_str})" if summary_str else ""
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "queue_add",
            "message": f"Queued {norm_kind} ({tname}) at {item['priority']}{msg_suffix}.",
            "item": item,
            "manual_queue": pending,
            "metal_production_review": metal_review,
        }
    )


def _handle_queue_update(bridge: BridgeApiFacade, params: QueueUpdateParams) -> dict[str, object]:
    """Update priority, sort order, target level, or note on a pending manual queue item."""
    qid = parse_command_int("queue_id", params.queue_id, min_val=1, max_val=2**63 - 1)
    if qid is None:
        raise ApiRequestError("'queue_update' requires 'queue_id'.")
    prio_arg: str | None = None
    if params.priority is not None:
        prio_arg = str(params.priority).strip().upper()
        if prio_arg not in ("P0", "P1", "P2", "P3"):
            raise ApiRequestError("'priority' must be one of P0, P1, P2, P3.")
    move_arg: str | None = None
    if params.move is not None:
        move_arg = str(params.move).strip().lower()
        if move_arg not in ("top", "bottom", "up", "down"):
            raise ApiRequestError("'move' must be one of top, bottom, up, down.")
    tlvl_arg: object = ...
    if params.target_level is not _UNSET:
        tlvl_arg = parse_command_int("target_level", params.target_level, allow_none=True, min_val=0, max_val=100_000)
    note_arg: str | None = (
        bounded_api_str(params.note or "", default="")[:280]
        if params.note is not _UNSET and params.note is not None
        else None
    )

    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database is not configured on this API bridge.")
        updated = db.update_manual_queue_item(
            int(qid),
            priority=prio_arg,
            move=move_arg,
            sort_rank=params.sort_rank,
            target_level=tlvl_arg,
            note=note_arg,
        )
        if updated is None:
            raise ApiRequestError(f"Queue item #{qid} not found.")
        bridge.ctx.invalidate_dashboard_cache()
        pending: list[dict[str, object]] = [dict(r) for r in db.get_manual_queue(status="pending", limit=200)]
        metal_review = _review_queue_metal_status_locked(bridge, safe_api_int(updated.get("planet_id"), 0), pending)

    bridge.publish_snapshot()
    summary_str = str(metal_review.get("summary") or "")
    msg_suffix = f" ({summary_str})" if summary_str else ""
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "queue_update",
            "message": f"Updated queue item #{qid} ({updated['priority']}){msg_suffix}.",
            "item": updated,
            "manual_queue": pending,
            "metal_production_review": metal_review,
        }
    )


def _handle_queue_cancel(bridge: BridgeApiFacade, params: QueueCancelParams) -> dict[str, object]:
    """Cancel a single pending manual queue item."""
    qid = parse_command_int("queue_id", params.queue_id, min_val=1, max_val=2**63 - 1)
    if qid is None:
        raise ApiRequestError("'queue_cancel' requires 'queue_id'.")
    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database is not configured on this API bridge.")
        ok_c = bool(db.cancel_manual_queue_item(int(qid)))
        bridge.ctx.invalidate_dashboard_cache()
        pending: list[dict[str, object]] = [dict(r) for r in db.get_manual_queue(status="pending", limit=200)]

    bridge.publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "queue_cancel",
            "canceled": ok_c,
            "message": f"Canceled queue item #{qid}." if ok_c else f"Queue item #{qid} already inactive.",
            "manual_queue": pending,
        }
    )


def _handle_queue_clear(bridge: BridgeApiFacade, _params: QueueClearParams) -> dict[str, object]:
    """Clear all pending manual queue commands."""
    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database is not configured on this API bridge.")
        cnt = int(db.clear_manual_queue())
        bridge.ctx.invalidate_dashboard_cache()

    bridge.publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "queue_clear",
            "cleared_count": cnt,
            "message": f"Cleared {cnt} pending command(s) from queue.",
            "manual_queue": [],
        }
    )


def register_queue_handlers(registry: CommandRegistry[BridgeApiFacade]) -> None:
    """Register all 4 non-browser manual queue commands."""
    registry.command(
        "queue_add",
        needs_browser=False,
        mutates=True,
        summary="Enqueue a manual command into the persistent priority queue.",
        params=QueueAddParams,
    )(_handle_queue_add)
    registry.command(
        "queue_update",
        needs_browser=False,
        mutates=True,
        summary="Update priority, ordering, target level, or note on a pending queue item.",
        params=QueueUpdateParams,
    )(_handle_queue_update)
    registry.command(
        "queue_cancel",
        needs_browser=False,
        mutates=True,
        summary="Cancel a pending manual queue item.",
        params=QueueCancelParams,
    )(_handle_queue_cancel)
    registry.command(
        "queue_clear",
        needs_browser=False,
        mutates=True,
        summary="Clear all pending commands from the manual queue.",
        params=QueueClearParams,
    )(_handle_queue_clear)
