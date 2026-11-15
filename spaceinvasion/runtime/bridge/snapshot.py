"""Phase 4.2 & 4.3 lock-free bridge snapshot publication and versioned dashboard deltas."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
import time
from types import MappingProxyType
from typing import Any

from spaceinvasion.core.catalog.coercion import safe_int
from spaceinvasion.core.catalog.json_sanitize import sanitize_json_tree
from spaceinvasion.runtime.bridge._helpers import SERVER_BOOT_ID
from spaceinvasion.runtime.bridge._helpers_sig import compute_ui_assets_state


def _freeze_mapping(raw: Mapping[str, object]) -> Mapping[str, object]:
    """Return a shallow immutable view over a sanitized dict copy."""
    sanitized: dict[str, object] = sanitize_json_tree(dict(raw))
    return MappingProxyType(sanitized)


def _freeze_series(items: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    """Return an immutable tuple of sanitized read-only row mappings."""
    return tuple(_freeze_mapping(row) for row in items)


def compute_section_version(value: object) -> str:
    """Compute a deterministic 16-hex-char content token for a dashboard section payload."""
    sanitized = sanitize_json_tree(value)
    encoded = json.dumps(
        sanitized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _extract_cursor(row: Mapping[str, object], key: str) -> int:
    return safe_int(row.get(key, 0), 0)


@dataclass(frozen=True)
class PublishedBridgeSnapshot:
    """Immutable snapshot published by the bridge after each state change.

    Readers consume this object directly with zero lock acquisition.
    """

    generation: int
    published_at: float
    health: Mapping[str, object]
    bot_control: Mapping[str, object]
    state: Mapping[str, object]
    sections: Mapping[str, object]
    section_versions: Mapping[str, str]
    hourly_production_history: tuple[Mapping[str, object], ...] = ()
    loot_recycle_hourly: tuple[Mapping[str, object], ...] = ()

    @property
    def latest_minute_bucket(self) -> int | None:
        """Return the highest `minute_bucket` cursor in `hourly_production_history`, or None."""
        if not self.hourly_production_history:
            return None
        return max(_extract_cursor(row, "minute_bucket") for row in self.hourly_production_history)

    @property
    def latest_loot_hour(self) -> int | None:
        """Return the highest `hour_bucket` cursor in `loot_recycle_hourly`, or None."""
        if not self.loot_recycle_hourly:
            return None
        return max(_extract_cursor(row, "hour_bucket") for row in self.loot_recycle_hourly)


class SnapshotPublisher:
    """Thread-safe lock-free publisher for `PublishedBridgeSnapshot`.

    Writes are serialized via an internal writer lock so generation counters stay
    monotonic; reads perform a single atomic attribute fetch and NEVER block on
    writers or on `browser_lock`.
    """

    def __init__(self, initial: PublishedBridgeSnapshot | None = None) -> None:
        self._write_lock = threading.Lock()
        self._current: PublishedBridgeSnapshot | None = initial

    def current(self) -> PublishedBridgeSnapshot | None:
        """Return the latest published snapshot without acquiring any lock."""
        return self._current

    def health_payload(self) -> dict[str, object]:
        """Return a lock-free `/health` response payload from the latest snapshot."""
        snap = self._current
        if snap is None:
            return {"ok": False, "ready": False, "snapshot_generation": 0}
        out: dict[str, object] = dict(snap.health)
        out.setdefault("ok", True)
        out["snapshot_generation"] = snap.generation
        out["published_at"] = snap.published_at
        return out

    def bot_status_payload(self) -> dict[str, object]:
        """Return a lock-free `/bot_status` response payload from the latest snapshot."""
        snap = self._current
        if snap is None:
            return {"ok": True, "paused": False, "captcha_blocked": False, "snapshot_generation": 0}
        out: dict[str, object] = dict(snap.bot_control)
        out.setdefault("ok", True)
        out["snapshot_generation"] = snap.generation
        out["published_at"] = snap.published_at
        return out

    def publish(
        self,
        *,
        health: Mapping[str, object],
        bot_control: Mapping[str, object],
        state: Mapping[str, object],
        sections: Mapping[str, object] | None = None,
        section_versions: Mapping[str, str] | None = None,
        hourly_production_history: Sequence[Mapping[str, object]] = (),
        loot_recycle_hourly: Sequence[Mapping[str, object]] = (),
        published_at: float | None = None,
    ) -> PublishedBridgeSnapshot:
        """Build a new immutable snapshot and atomically swap it into `_current`."""
        ts = time.time() if published_at is None else float(published_at)
        merged_sections: dict[str, object] = dict(sections or {})
        merged_sections.setdefault("state", dict(state))
        merged_sections.setdefault("bot_control", dict(bot_control))
        frozen_sections = _freeze_mapping(merged_sections)

        computed_versions: dict[str, str] = {}
        provided_versions = section_versions or {}
        for sec_name, sec_val in frozen_sections.items():
            explicit = provided_versions.get(sec_name)
            computed_versions[sec_name] = str(explicit) if explicit is not None else compute_section_version(sec_val)

        with self._write_lock:
            next_gen = (self._current.generation + 1) if self._current is not None else 1
            snap = PublishedBridgeSnapshot(
                generation=next_gen,
                published_at=ts,
                health=_freeze_mapping(health),
                bot_control=_freeze_mapping(bot_control),
                state=_freeze_mapping(state),
                sections=frozen_sections,
                section_versions=MappingProxyType(computed_versions),
                hourly_production_history=_freeze_series(hourly_production_history),
                loot_recycle_hourly=_freeze_series(loot_recycle_hourly),
            )
            self._current = snap
            return snap

    def publish_section(
        self,
        section_name: str,
        value: object,
        *,
        version: str | None = None,
        published_at: float | None = None,
    ) -> PublishedBridgeSnapshot:
        """Atomically update a single named section while preserving all other section tokens."""
        ts = time.time() if published_at is None else float(published_at)
        sanitized_val: Any = sanitize_json_tree(value)
        new_token = str(version) if version is not None else compute_section_version(sanitized_val)

        with self._write_lock:
            prev = self._current
            if prev is None:
                raise RuntimeError("Cannot update a single section before publishing an initial snapshot.")
            next_sections = dict(prev.sections)
            next_sections[section_name] = sanitized_val
            next_versions = dict(prev.section_versions)
            next_versions[section_name] = new_token
            next_state = (
                _freeze_mapping(sanitized_val)
                if section_name == "state" and isinstance(sanitized_val, Mapping)
                else prev.state
            )
            next_bot_control = (
                _freeze_mapping(sanitized_val)
                if section_name == "bot_control" and isinstance(sanitized_val, Mapping)
                else prev.bot_control
            )
            snap = PublishedBridgeSnapshot(
                generation=prev.generation + 1,
                published_at=ts,
                health=prev.health,
                bot_control=next_bot_control,
                state=next_state,
                sections=MappingProxyType(next_sections),
                section_versions=MappingProxyType(next_versions),
                hourly_production_history=prev.hourly_production_history,
                loot_recycle_hourly=prev.loot_recycle_hourly,
            )
            self._current = snap
            return snap


def build_dashboard_delta_response(
    snapshot: PublishedBridgeSnapshot,
    *,
    client_section_versions: Mapping[str, str] | None = None,
    since_minute_bucket: int | None = None,
    since_loot_hour: int | None = None,
    ui_dir: Path | None = None,
) -> dict[str, object]:
    """Assemble a Phase 4.3 versioned dashboard response omitting unchanged sections.

    When `client_section_versions` matches `snapshot.section_versions` and the
    time-series cursors are current, the response contains only compact headers,
    `section_versions`, `unchanged_sections`, and empty delta series (< 5 KB).
    """
    client_versions = client_section_versions or {}
    unchanged: list[str] = []
    section_versions = dict(snapshot.section_versions)
    ui_assets = compute_ui_assets_state(ui_dir)
    payload: dict[str, object] = {
        "ok": True,
        "delta": bool(client_versions) or since_minute_bucket is not None or since_loot_hour is not None,
        "snapshot_generation": snapshot.generation,
        "published_at": snapshot.published_at,
        "server_boot_id": SERVER_BOOT_ID,
        **ui_assets,
        "health": dict(snapshot.health),
        "section_versions": section_versions,
        "unchanged_sections": unchanged,
    }

    for sec_name in sorted(snapshot.sections):
        sec_ver = section_versions.get(sec_name, "")
        if client_versions.get(sec_name) == sec_ver:
            unchanged.append(sec_name)
        else:
            payload[sec_name] = snapshot.sections[sec_name]

    if "bot_control" not in snapshot.sections:
        bot_ver = section_versions.get("bot_control") or compute_section_version(snapshot.bot_control)
        section_versions.setdefault("bot_control", bot_ver)
        if client_versions.get("bot_control") == bot_ver:
            unchanged.append("bot_control")
        else:
            payload["bot_control"] = dict(snapshot.bot_control)

    if since_minute_bucket is None:
        prod_rows = [dict(row) for row in snapshot.hourly_production_history]
        payload["hourly_production_mode"] = "full"
    else:
        prod_rows = [
            dict(row)
            for row in snapshot.hourly_production_history
            if _extract_cursor(row, "minute_bucket") > since_minute_bucket
        ]
        payload["hourly_production_mode"] = "delta"
    payload["hourly_production_history"] = prod_rows
    payload["latest_minute_bucket"] = snapshot.latest_minute_bucket

    if since_loot_hour is None:
        loot_rows = [dict(row) for row in snapshot.loot_recycle_hourly]
        payload["loot_recycle_mode"] = "full"
    else:
        loot_rows = [
            dict(row) for row in snapshot.loot_recycle_hourly if _extract_cursor(row, "hour_bucket") > since_loot_hour
        ]
        payload["loot_recycle_mode"] = "delta"
    payload["loot_recycle_hourly"] = loot_rows
    payload["latest_loot_hour"] = snapshot.latest_loot_hour

    return payload
