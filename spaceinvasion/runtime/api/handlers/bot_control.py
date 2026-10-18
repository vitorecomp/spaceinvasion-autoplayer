"""Non-browser bot control and operator notification `@command` handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.runtime.api.handlers._bridge_proto import BridgeApiFacade
from spaceinvasion.runtime.api.registry import CommandRegistry
from spaceinvasion.runtime.api.validation import ApiRequestError, FieldBounds
from spaceinvasion.runtime.bridge._helpers import (
    bounded_api_str,
    parse_command_int,
    sanitize_json_dict,
)


class _UnsetType:
    """Deterministic sentinel distinguishing omitted JSON keys from explicit `null`."""

    def __repr__(self) -> str:
        return "UNSET"


_UNSET: Final[_UnsetType] = _UnsetType()


@dataclass(frozen=True)
class BotControlParams:
    """Parameters for the `bot_control` pause/resume and mode-override command."""

    paused: bool | _UnsetType = _UNSET
    action: str | None = field(
        default=None,
        metadata={"bounds": FieldBounds(strip=True, description="Optional 'pause' or 'resume' action")},
    )
    mode_override: str | None | _UnsetType = _UNSET


@dataclass(frozen=True)
class NotificationsListParams:
    """Parameters for listing persisted operator notifications."""

    limit: int = field(default=50, metadata={"bounds": FieldBounds(min_val=1, max_val=200)})
    status: str | None = field(default="all", metadata={"bounds": FieldBounds(strip=True)})


@dataclass(frozen=True)
class NotificationGetParams:
    """Parameters for fetching a single notification by numeric ID or `NTF-XXXX` code."""

    notification_id: int | str | None = None
    notification_code: str | None = None
    id: int | str | None = None


@dataclass(frozen=True)
class NotificationAckParams:
    """Parameters for acknowledging or updating a single notification's status."""

    notification_id: int | str | None = None
    notification_code: str | None = None
    status: str | None = field(default="read", metadata={"bounds": FieldBounds(strip=True)})


@dataclass(frozen=True)
class NotificationsAckAllParams:
    """Parameters for acknowledging all active notifications."""

    status: str | None = field(default="read", metadata={"bounds": FieldBounds(strip=True)})


def _resolve_paused_override(params: BotControlParams) -> bool | None:
    paused_in: bool | None = None
    if params.action is not None:
        act = params.action.strip().lower()
        if act == "pause":
            paused_in = True
        elif act == "resume":
            paused_in = False
        elif act:
            raise ApiRequestError("action must be 'pause' or 'resume'.", field="action")
    if params.paused is not _UNSET:
        paused_in = bool(params.paused)
    return paused_in


def _handle_bot_control(bridge: BridgeApiFacade, params: BotControlParams) -> dict[str, object]:
    """Pause/resume automation, clear CAPTCHA gates on resume, or toggle mode override."""
    paused_in = _resolve_paused_override(params)
    mode_ov = params.mode_override
    if mode_ov is not _UNSET and mode_ov not in (None, "live", "dry-run"):
        raise ApiRequestError("mode_override must be null, 'live', or 'dry-run'.", field="mode_override")

    with bridge.ctx.state_lock:
        if paused_in is not None:
            bridge.ctx.bot_control["paused"] = paused_in
            if paused_in:
                bridge.ctx.sweep_wake_event.set()
            else:
                bridge.ctx.bot_control["captcha_blocked"] = False
                bridge.ctx.bot_control["captcha_message"] = None
                bridge.lifecycle.ack_captcha_notifications_locked()
                bridge.ctx.invalidate_dashboard_cache()
        if mode_ov is not _UNSET:
            bridge.ctx.bot_control["mode_override"] = mode_ov
        bridge.lifecycle.persist_runtime_state()
        ctrl_copy = dict(bridge.ctx.bot_control)

    bridge.publish_snapshot()
    return sanitize_json_dict({"ok": True, "command": "bot_control", "bot_control": ctrl_copy})


def _handle_notifications_list(bridge: BridgeApiFacade, params: NotificationsListParams) -> dict[str, object]:
    """List persisted operator notifications and current unread count."""
    n_status = bounded_api_str(params.status or "all", default="all").strip().lower()
    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        items: list[dict[str, object]] = (
            [dict(row) for row in db.get_notifications(status=n_status, limit=params.limit)] if db is not None else []
        )
        unread_cnt = int(db.unread_notification_count()) if db is not None else 0
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "notifications_list",
            "unread_count": unread_cnt,
            "items": items,
        }
    )


def _handle_notification_get(bridge: BridgeApiFacade, params: NotificationGetParams) -> dict[str, object]:
    """Fetch a single notification by numeric ID or `NTF-XXXX` code."""
    raw_nid: object = (
        params.notification_id
        if params.notification_id is not None
        else (params.notification_code if params.notification_code is not None else params.id)
    )
    parsed_nid = GameDatabase.parse_notification_id(raw_nid)
    if parsed_nid is None:
        raise ApiRequestError("'notification_get' requires a valid 'notification_id' (e.g. 42 or 'NTF-0042').")

    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database notifications are not configured.")
        found_item = db.get_notification(parsed_nid)
        if found_item is None:
            raise ApiRequestError(f"Notification {GameDatabase.format_notification_code(parsed_nid)} not found.")
    return sanitize_json_dict({"ok": True, "command": "notification_get", "item": found_item})


def _handle_notification_ack(bridge: BridgeApiFacade, params: NotificationAckParams) -> dict[str, object]:
    """Acknowledge, dismiss, or re-mark a single notification as unread."""
    raw_ack_nid: object = params.notification_id if params.notification_id is not None else params.notification_code
    nid = (
        GameDatabase.parse_notification_id(raw_ack_nid)
        if isinstance(raw_ack_nid, str)
        else parse_command_int("notification_id", raw_ack_nid, min_val=1, max_val=2**62)
    )
    if nid is None:
        raise ApiRequestError("'notification_id' must be a positive integer or code like 'NTF-0042'.")

    ack_st = bounded_api_str(params.status or "read", default="read").strip().lower()
    if ack_st not in ("read", "dismissed", "unread"):
        raise ApiRequestError("status must be 'read', 'dismissed', or 'unread'.")

    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database notifications are not configured.")
        updated_item = db.acknowledge_notification(int(nid), status=ack_st)
        if updated_item is None:
            raise ApiRequestError(f"Notification #{nid} not found.")
        summary = bridge.lifecycle.get_notifications_summary_locked()

    bridge.publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "notification_ack",
            "item": updated_item,
            "notifications": summary,
        }
    )


def _handle_notifications_ack_all(bridge: BridgeApiFacade, params: NotificationsAckAllParams) -> dict[str, object]:
    """Mark all active notifications as `read` or `dismissed`."""
    ack_st = bounded_api_str(params.status or "read", default="read").strip().lower()
    if ack_st not in ("read", "dismissed"):
        raise ApiRequestError("status must be 'read' or 'dismissed'.")

    with bridge.ctx.state_lock:
        db = bridge.ctx.db
        changed = int(db.acknowledge_all_notifications(status=ack_st)) if db is not None else 0
        summary = bridge.lifecycle.get_notifications_summary_locked()

    bridge.publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "notifications_ack_all",
            "updated_count": changed,
            "notifications": summary,
        }
    )


def register_bot_control_handlers(registry: CommandRegistry[BridgeApiFacade]) -> None:
    """Register all 5 non-browser bot-control and notification commands."""
    registry.command(
        "bot_control",
        needs_browser=False,
        mutates=True,
        summary="Pause/resume automation, clear CAPTCHA gates on resume, or update mode_override.",
        params=BotControlParams,
    )(_handle_bot_control)
    registry.command(
        "notifications_list",
        needs_browser=False,
        mutates=False,
        summary="List persisted operator notifications and current unread count.",
        params=NotificationsListParams,
    )(_handle_notifications_list)
    registry.command(
        "notification_get",
        needs_browser=False,
        mutates=False,
        summary="Fetch a single operator notification by numeric ID or NTF-XXXX code.",
        params=NotificationGetParams,
    )(_handle_notification_get)
    registry.command(
        "notification_ack",
        needs_browser=False,
        mutates=True,
        summary="Acknowledge, dismiss, or re-mark a single operator notification.",
        params=NotificationAckParams,
    )(_handle_notification_ack)
    registry.command(
        "notifications_ack_all",
        needs_browser=False,
        mutates=True,
        summary="Mark all active operator notifications as read or dismissed.",
        params=NotificationsAckAllParams,
    )(_handle_notifications_ack_all)
