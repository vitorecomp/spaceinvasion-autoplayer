"""Shared base class, type coercions, and Berlin server-day helper for `RuntimeDatabaseGateway`."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone, tzinfo
from decimal import Decimal
import json
import math
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from spaceinvasion.core.catalog.json_sanitize import safe_key
from spaceinvasion.core.redaction import redact_sensitive, redact_text
from spaceinvasion.db.store import DatabaseStore


def _safe_float(raw: object, default: float = 0.0) -> float:
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        try:
            val = float(raw)
        except OverflowError:
            return default
        return val if math.isfinite(val) else default
    if isinstance(raw, str):
        try:
            val = float(raw.strip())
            return val if math.isfinite(val) else default
        except (ValueError, OverflowError):
            return default
    return default


def _safe_int(raw: object, default: int = 0) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, (float, Decimal)):
        try:
            fval = float(raw)
            return int(fval) if math.isfinite(fval) else default
        except OverflowError:
            return default
    if isinstance(raw, str):
        try:
            fval = float(raw.strip())
            return int(fval) if math.isfinite(fval) else default
        except (ValueError, OverflowError):
            return default
    return default


def _clean_str(raw: object, max_len: int | None = None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        out = redact_text(raw)
    elif isinstance(raw, (bytes, bytearray)):
        out = redact_text(bytes(raw).decode("utf-8", errors="replace"))
    else:
        red = redact_sensitive(raw)
        out = red if isinstance(red, str) else redact_text(safe_key(red))
    return out[:max_len] if (max_len is not None and len(out) > max_len) else out


def _loads_dict(raw: str) -> dict[str, object]:
    if not raw or not raw.strip():
        return {}
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): v for k, v in parsed.items()}


def _loads_list(raw: str) -> list[object]:
    if not raw or not raw.strip():
        return []
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def server_day_key(ts: float | None = None) -> str:
    """Return the SpaceInvasion server calendar date (`YYYY-MM-DD`, Europe/Berlin with UTC fallback)."""
    eff_ts = _safe_float(ts, time.time()) if ts is not None else time.time()
    if eff_ts <= 0.0:
        eff_ts = time.time()
    tz: tzinfo
    try:
        tz = ZoneInfo("Europe/Berlin")
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    try:
        return datetime.fromtimestamp(eff_ts, tz=tz).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


class _GatewayCore:
    """Shared gateway store handle, monotonic change counter, and meta JSON helpers."""

    _store: DatabaseStore
    _postgres_url: str
    _change_lock: threading.Lock
    _change_counter: int
    _last_slot_history_sig: tuple[float, tuple[str, ...]] | None

    def _bump_change(self) -> None:
        with self._change_lock:
            self._change_counter += 1

    def load_meta_json(self, key: str) -> object:
        """Load and JSON-decode any `meta` entry (dict, list, scalar), or `None`."""
        with self._store.repositories() as repos:
            rec = repos.settings.get_meta(key)
        if rec is None or not rec.value:
            return None
        try:
            return json.loads(rec.value)
        except ValueError:
            return None

    def save_meta_json(self, key: str, payload: object, *, ts: float | None = None) -> None:
        """Persist redacted JSON into `meta` (syncing discarded colony flags when applicable)."""
        eff_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        with self._store.repositories() as repos:
            repos.settings.upsert_meta_json(key, payload, ts=eff_ts)
            if str(key).strip() == "discarded_colony_coords":
                coords: list[str] = []
                if isinstance(payload, Mapping):
                    coords = [str(k) for k in payload]
                elif isinstance(payload, (list, tuple, set, frozenset)):
                    coords = [str(k) for k in payload]
                repos.colonies.sync_discarded_coords(coords, ts=eff_ts)
        self._bump_change()


__all__ = [
    "_GatewayCore",
    "_clean_str",
    "_loads_dict",
    "_loads_list",
    "_safe_float",
    "_safe_int",
    "server_day_key",
]
