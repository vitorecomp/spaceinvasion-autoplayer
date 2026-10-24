"""Runtime state, strategy config, custom planet names, sweep, transmitter, and notifications mixin."""

from __future__ import annotations

from collections.abc import Mapping
import re
import time

from spaceinvasion.core.catalog import cap_json_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.db.dto import NotificationRecord
from spaceinvasion.runtime.bridge._db_gateway_common import (
    _clean_str,
    _GatewayCore,
    _loads_dict,
    _safe_float,
    _safe_int,
    server_day_key,
)

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _notification_to_dict(rec: NotificationRecord) -> dict[str, object]:
    return {
        "id": cap_json_int(rec.id),
        "notification_code": rec.notification_code,
        "created_ts": rec.created_ts,
        "updated_ts": rec.updated_ts,
        "severity": rec.severity,
        "category": rec.category,
        "title": rec.title,
        "message": rec.message,
        "help_prompt": rec.help_prompt,
        "action_kind": rec.action_kind,
        "planet_coords": rec.planet_coords,
        "target_name": rec.target_name,
        "dedupe_key": rec.dedupe_key,
        "occurrence_count": max(1, cap_json_int(rec.occurrence_count)),
        "status": rec.status,
        "acknowledged_ts": rec.acknowledged_ts,
        "details": _loads_dict(rec.details_json),
    }


def _sanitize_custom_planet_name(raw: object, *, max_len: int = 48) -> str:
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return ""
    text = " ".join(_CTRL_RE.sub(" ", _clean_str(raw)).split())
    return text[: max(1, min(128, _safe_int(max_len, 48)))].strip()


def _normalize_custom_planet_names(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for raw_pid, raw_name in raw.items():
        pid = _safe_int(raw_pid, 0)
        if pid > 0 and (clean := _sanitize_custom_planet_name(raw_name)):
            out[str(pid)] = clean
    return out


class _MetaGatewayMixin(_GatewayCore):
    """Runtime state, strategy config, custom names, galaxy sweep, transmitter, and notifications."""

    def save_runtime_state(self, runtime_state: Mapping[str, object], *, ts: float | None = None) -> None:
        """Persist bridge runtime status snapshot into `meta['bridge_runtime_state']`."""
        if isinstance(runtime_state, Mapping):
            self.save_meta_json("bridge_runtime_state", dict(runtime_state), ts=ts)

    def load_runtime_state(self) -> dict[str, object]:
        """Load bridge runtime status snapshot from `meta['bridge_runtime_state']`."""
        raw = self.load_meta_json("bridge_runtime_state")
        return {str(k): v for k, v in raw.items()} if isinstance(raw, Mapping) else {}

    def save_strategy_config_snapshot(
        self,
        config_payload: Mapping[str, object],
        *,
        ts: float | None = None,
        source: str = "sqlite",
    ) -> None:
        """Upsert active strategy config in `strategy_config_store`, `meta`, and `config_history`."""
        if not isinstance(config_payload, Mapping):
            return
        with self._store.repositories() as repos:
            repos.settings.save_strategy_config(config_payload, ts=ts, source=source)
        self._bump_change()

    def load_strategy_config_snapshot(self) -> dict[str, object] | None:
        """Load persisted strategy config from `strategy_config_store` with `meta` fallback."""
        with self._store.repositories() as repos:
            rec = repos.settings.load_strategy_config()
        if rec is None or not rec.config_json.strip():
            return None
        parsed = _loads_dict(rec.config_json)
        return parsed if parsed or rec.config_json.strip() == "{}" else None

    def get_custom_planet_names(self) -> dict[str, str]:
        """Return persisted custom planet display names (`{str(planet_id): name}`)."""
        return _normalize_custom_planet_names(self.load_meta_json("custom_planet_names"))

    def set_custom_planet_name(self, planet_id: int, name: object, *, ts: float | None = None) -> dict[str, str]:
        """Persist or clear (`name=''`) a custom display name for `planet_id`."""
        pid = _safe_int(planet_id, 0)
        names = self.get_custom_planet_names()
        if pid <= 0:
            return dict(names)
        clean = _sanitize_custom_planet_name(name)
        if clean:
            names[str(pid)] = clean
        else:
            names.pop(str(pid), None)
        self.save_meta_json("custom_planet_names", names, ts=ts)
        return dict(names)

    def set_custom_planet_names(
        self,
        updates: Mapping[object, object],
        *,
        replace: bool = False,
        ts: float | None = None,
    ) -> dict[str, str]:
        """Batch-update custom display names in `meta['custom_planet_names']`."""
        names = {} if replace else self.get_custom_planet_names()
        if isinstance(updates, Mapping):
            for raw_pid, raw_name in updates.items():
                pid = _safe_int(raw_pid, 0)
                if pid <= 0:
                    continue
                clean = _sanitize_custom_planet_name(raw_name)
                if clean:
                    names[str(pid)] = clean
                else:
                    names.pop(str(pid), None)
        self.save_meta_json("custom_planet_names", names, ts=ts)
        return dict(names)

    def get_galaxy_sweep_state(self, *, now_ts: float | None = None) -> dict[str, object]:
        """Return persisted periodic galaxy sweep cursor and telemetry."""
        eff_now = _safe_float(now_ts, time.time()) if now_ts is not None else time.time()
        raw = self.load_meta_json("galaxy_sweep_state")
        data = raw if isinstance(raw, Mapping) else {}
        next_due = _safe_float(data.get("next_sweep_due_ts"), 0.0)
        return {
            "cursor_galaxy": cap_json_int(max(1, min(14, _safe_int(data.get("cursor_galaxy"), 1)))),
            "cursor_system": cap_json_int(max(1, min(400, _safe_int(data.get("cursor_system"), 1)))),
            "last_sweep_started_ts": _safe_float(data.get("last_sweep_started_ts"), 0.0),
            "last_sweep_completed_ts": _safe_float(data.get("last_sweep_completed_ts"), 0.0),
            "next_sweep_due_ts": next_due,
            "due": eff_now >= next_due,
            "last_pages_scanned": cap_json_int(max(0, _safe_int(data.get("last_pages_scanned"), 0))),
            "last_arrow_steps": cap_json_int(max(0, _safe_int(data.get("last_arrow_steps"), 0))),
            "last_players_found": cap_json_int(max(0, _safe_int(data.get("last_players_found"), 0))),
            "last_colonies_found": cap_json_int(max(0, _safe_int(data.get("last_colonies_found"), 0))),
            "last_debris_found": cap_json_int(max(0, _safe_int(data.get("last_debris_found"), 0))),
            "total_sweeps": cap_json_int(max(0, _safe_int(data.get("total_sweeps"), 0))),
            "total_pages_scanned": cap_json_int(max(0, _safe_int(data.get("total_pages_scanned"), 0))),
            "click_delay_seconds": round(max(0.2, min(60.0, _safe_float(data.get("click_delay_seconds"), 1.5))), 3),
            "click_jitter_seconds": round(max(0.0, min(30.0, _safe_float(data.get("click_jitter_seconds"), 0.6))), 3),
        }

    def save_galaxy_sweep_state(self, updates: Mapping[str, object], *, ts: float | None = None) -> dict[str, object]:
        """Update and persist galaxy sweep cursor/telemetry in `meta`."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        merged = dict(self.get_galaxy_sweep_state(now_ts=eff_now))
        merged.pop("due", None)
        for k, v in updates.items():
            if k != "due":
                merged[str(k)] = v
        self.save_meta_json("galaxy_sweep_state", merged, ts=eff_now)
        return self.get_galaxy_sweep_state(now_ts=eff_now)

    def get_transmitter_status(self, *, now_ts: float | None = None) -> dict[str, object]:
        """Return persisted Interdimensional Transmitter daily free-spin status."""
        eff_now = _safe_float(now_ts, time.time()) if now_ts is not None else time.time()
        today = server_day_key(eff_now)
        raw = self.load_meta_json("transmitter_state")
        data = raw if isinstance(raw, Mapping) else {}
        last_act_day = _clean_str(data.get("last_activated_day")).strip()
        last_chk_day = _clean_str(data.get("last_checked_day")).strip()
        bonus_chk_day = _clean_str(data.get("daily_bonus_checked_day")).strip()
        bonus_col_day = _clean_str(data.get("daily_bonus_collected_day")).strip()
        bonus_avail_day = _clean_str(data.get("daily_bonus_available_day")).strip()
        bonus_avail = bool(data.get("daily_bonus_available", False)) and (bonus_avail_day in ("", today))
        free_rem = data.get("free_spins_remaining")
        free_opt = cap_json_int(max(0, _safe_int(free_rem, 0))) if free_rem is not None else None
        done_today = (last_act_day == today) or (
            last_chk_day == today and free_opt == 0 and bonus_chk_day == today and not bonus_avail
        )
        return {
            "today_day_key": today,
            "due": not done_today,
            "last_activated_day": last_act_day,
            "last_checked_day": last_chk_day,
            "daily_bonus_checked_day": bonus_chk_day,
            "daily_bonus_collected_day": bonus_col_day,
            "daily_bonus_available": bonus_avail,
            "last_activated_ts": _safe_float(data.get("last_activated_ts"), 0.0),
            "last_checked_ts": _safe_float(data.get("last_checked_ts"), 0.0),
            "free_spins_remaining": free_opt,
            "last_reward": _clean_str(data.get("last_reward"), 160).strip(),
            "total_activations": cap_json_int(max(0, _safe_int(data.get("total_activations"), 0))),
        }

    def record_transmitter_state(
        self,
        *,
        activated: bool,
        free_spins_remaining: int | None = None,
        reward: str = "",
        daily_bonus_checked: bool | None = None,
        daily_bonus_collected: bool | None = None,
        daily_bonus_available: bool | None = None,
        ts: float | None = None,
    ) -> dict[str, object]:
        """Persist Interdimensional Transmitter daily check or activation outcome into `meta`."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        today = server_day_key(eff_now)
        cur = self.get_transmitter_status(now_ts=eff_now)
        checked_day = (
            today if (daily_bonus_checked is True or daily_bonus_collected is True) else cur["daily_bonus_checked_day"]
        )
        collected_day = today if daily_bonus_collected is True else cur["daily_bonus_collected_day"]
        if daily_bonus_available is not None:
            avail_flag = bool(daily_bonus_available)
        elif activated or daily_bonus_collected is True or daily_bonus_checked is True:
            avail_flag = False
        else:
            avail_flag = bool(cur["daily_bonus_available"])
        tot_act = _safe_int(cur["total_activations"], 0)
        payload: dict[str, object] = {
            "last_activated_day": today if activated else cur["last_activated_day"],
            "last_checked_day": today,
            "daily_bonus_checked_day": checked_day,
            "daily_bonus_collected_day": collected_day,
            "daily_bonus_available": avail_flag,
            "daily_bonus_available_day": today if avail_flag else "",
            "last_activated_ts": eff_now if activated else cur["last_activated_ts"],
            "last_checked_ts": eff_now,
            "free_spins_remaining": (
                cap_json_int(max(0, _safe_int(free_spins_remaining, 0)))
                if free_spins_remaining is not None
                else cur["free_spins_remaining"]
            ),
            "last_reward": _clean_str(reward, 160).strip() if reward else cur["last_reward"],
            "total_activations": cap_json_int(tot_act + 1) if activated else tot_act,
        }
        self.save_meta_json("transmitter_state", payload, ts=eff_now)
        return self.get_transmitter_status(now_ts=eff_now)

    def note_daily_login_bonus_seen(self, *, present: bool, ts: float | None = None) -> dict[str, object]:
        """Record passive observation of the Daily Login Bonus popup without overwriting checks."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        today = server_day_key(eff_now)
        cur = self.get_transmitter_status(now_ts=eff_now)
        if present:
            if (
                cur["last_activated_day"] == today
                or cur["daily_bonus_collected_day"] == today
                or cur["daily_bonus_available"]
            ):
                return cur
            new_avail = True
        else:
            if not cur["daily_bonus_available"]:
                return cur
            new_avail = False
        payload = {k: v for k, v in cur.items() if k not in ("today_day_key", "due")}
        payload["daily_bonus_available"] = new_avail
        payload["daily_bonus_available_day"] = today if new_avail else ""
        self.save_meta_json("transmitter_state", payload, ts=eff_now)
        return self.get_transmitter_status(now_ts=eff_now)

    def create_or_bump_notification(
        self,
        *,
        title: str,
        message: str,
        help_prompt: str = "",
        severity: str = "warning",
        category: str = "action_validation",
        action_kind: str = "",
        planet_coords: str = "",
        target_name: str = "",
        dedupe_key: str = "",
        details: Mapping[str, object] | None = None,
        ts: float | None = None,
    ) -> dict[str, object]:
        """Create an unread notification or increment `occurrence_count` on an identical unread key."""
        with self._store.repositories() as repos:
            rec = repos.notifications.create_or_bump(
                title=title,
                message=message,
                help_prompt=help_prompt,
                severity=severity,
                category=category,
                action_kind=action_kind,
                planet_coords=normalize_coords(planet_coords) if planet_coords.strip() else "",
                target_name=target_name,
                dedupe_key=dedupe_key,
                details=details,
                ts=ts,
            )
        self._bump_change()
        return _notification_to_dict(rec)

    def get_notification(self, notification_id: int | str) -> dict[str, object] | None:
        """Fetch one notification by integer ID or `NTF-XXXX` code."""
        with self._store.repositories() as repos:
            rec = repos.notifications.get_by_id(notification_id)
        return _notification_to_dict(rec) if rec is not None else None

    def get_notifications(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        """List persistent notifications ordered newest first."""
        with self._store.repositories() as repos:
            rows = repos.notifications.list_notifications(status=status, limit=limit)
        return [_notification_to_dict(r) for r in rows]

    def unread_notification_count(self) -> int:
        """Return count of unread UI notifications."""
        with self._store.repositories() as repos:
            return repos.notifications.unread_count()

    def acknowledge_notification(
        self, notification_id: int | str, *, status: str = "read", ts: float | None = None
    ) -> dict[str, object] | None:
        """Mark one notification `read`, `dismissed`, or `unread`."""
        with self._store.repositories() as repos:
            rec = repos.notifications.acknowledge(notification_id, status=status, ts=ts)
        if rec is not None:
            self._bump_change()
            return _notification_to_dict(rec)
        return None

    def acknowledge_all_notifications(self, *, status: str = "read", ts: float | None = None) -> int:
        """Acknowledge all unread notifications and return the updated count."""
        with self._store.repositories() as repos:
            cnt = repos.notifications.acknowledge_all(status=status, ts=ts)
        if cnt > 0:
            self._bump_change()
        return cnt


__all__ = ["_MetaGatewayMixin"]
