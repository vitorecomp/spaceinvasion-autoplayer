"""Internal read-only protocols, coercion, and sanitization helpers for ``runtime.logs``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import os
from typing import Protocol, runtime_checkable

from spaceinvasion.core.coercion import fmt_compact_int, safe_key
from spaceinvasion.core.redaction import redact_sensitive

MAX_LOG_KEY_NODES = 256
MAX_LOG_KEY_CHARS = 4096
MAX_LOG_TREE_NODES = 32_768
MAX_LOG_TREE_DEPTH = 64

SAFE_LOG_ERRORS = (
    OSError,
    TypeError,
    ValueError,
    RuntimeError,
    AttributeError,
    LookupError,
    OverflowError,
    RecursionError,
    UnicodeError,
)


@runtime_checkable
class ResourcesLogLike(Protocol):
    """Minimal resource contract for tick logging."""

    @property
    def pig_iron(self) -> int: ...

    @property
    def metal(self) -> int: ...

    @property
    def kryptonite(self) -> int: ...

    @property
    def spice(self) -> int: ...

    def format_short(self) -> str: ...


@runtime_checkable
class EnergyLogLike(Protocol):
    """Minimal energy contract for tick logging."""

    @property
    def produced(self) -> int: ...

    @property
    def used(self) -> int: ...


@runtime_checkable
class PlanetLogLike(Protocol):
    """Minimal active-planet contract for tick logging."""

    @property
    def coords(self) -> str: ...

    @property
    def resources(self) -> ResourcesLogLike: ...

    @property
    def energy(self) -> EnergyLogLike: ...


@runtime_checkable
class GameStateLogLike(Protocol):
    """Minimal game-state contract satisfied by ``core.model.GameState`` and ``core.state.GameState``."""

    @property
    def universe_id(self) -> int: ...

    def active_planet(self) -> PlanetLogLike | None: ...


@runtime_checkable
class PlannedActionLike(Protocol):
    """Minimal action contract satisfied by ``core.model.PlannedAction`` and ``planner.strategy.PlannedAction``."""

    @property
    def priority(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def planet_id(self) -> int: ...

    @property
    def planet_coords(self) -> str: ...

    @property
    def target_name(self) -> str: ...

    @property
    def target_id(self) -> int | None: ...

    @property
    def target_level(self) -> int | None: ...

    @property
    def executable_now(self) -> bool: ...


def decode_bytes(raw: bytes | bytearray) -> str:
    """Decode arbitrary byte payloads into UTF-8 with replacement."""
    return bytes(raw).decode("utf-8", errors="replace")


def clip_log_str(text: str, limit: int = MAX_LOG_KEY_CHARS) -> str:
    """Truncate ``text`` cleanly with a ``...[truncated]`` suffix if over ``limit``."""
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def safe_fspath_text(obj: os.PathLike[str] | os.PathLike[bytes]) -> str:
    """Convert an ``os.PathLike`` to text safely without raising."""
    try:
        raw: object = os.fspath(obj)
    except SAFE_LOG_ERRORS:
        return f"<{type(obj).__name__}>"
    if isinstance(raw, (bytes, bytearray)):
        return decode_bytes(raw)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, os.PathLike):
        return f"<{type(raw).__name__}>"
    return safe_summary_text(raw, f"<{type(raw).__name__}>")


def safe_exc_text(
    exc: BaseException,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
    budget: list[int] | None = None,
) -> str:
    """Render an exception into bounded text resilient against broken ``__str__`` or cycles."""
    active_seen = seen if seen is not None else set()
    active_budget = budget if budget is not None else [MAX_LOG_KEY_NODES]
    str_fn = type(exc).__str__
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            return clip_log_str(str(exc), 16_384)
        except SAFE_LOG_ERRORS:
            pass
    args = exc.args
    if not isinstance(args, (tuple, list)) or not args:
        return "" if str_fn is BaseException.__str__ else type(exc).__name__
    vid = id(exc)
    if vid in active_seen or depth >= 16 or active_budget[0] <= 0:
        return f"<{type(exc).__name__}>"
    active_budget[0] -= 1
    active_seen.add(vid)
    try:
        if len(args) == 1:
            a0: object = args[0]
            if isinstance(a0, str):
                res = repr(a0) if isinstance(exc, KeyError) else a0
                return clip_log_str(res, 16_384)
            if isinstance(a0, BaseException):
                return safe_exc_text(a0, depth=depth + 1, seen=active_seen, budget=active_budget)
            return clip_log_str(safe_summary_text(a0, f"<{type(a0).__name__}>"), MAX_LOG_KEY_CHARS)
        parts: list[str] = []
        for item in args[:8]:
            if active_budget[0] <= 0:
                parts.append("...")
                break
            if isinstance(item, str):
                parts.append(repr(item))
            elif isinstance(item, BaseException):
                parts.append(safe_exc_text(item, depth=depth + 1, seen=active_seen, budget=active_budget))
            else:
                parts.append(safe_summary_text(item, f"<{type(item).__name__}>"))
        if len(args) > 8:
            parts.append(f"...+{len(args) - 8}")
        return clip_log_str("(" + ", ".join(parts) + ")", MAX_LOG_KEY_CHARS)
    finally:
        active_seen.discard(vid)


def safe_summary_text(val: object, default: str = "") -> str:
    """Safely render arbitrary scalar/container values into compact single-line text."""
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        return safe_fspath_text(val)
    if isinstance(val, (bytes, bytearray)):
        return decode_bytes(val)
    if isinstance(val, str):
        return val
    if isinstance(val, BaseException):
        return safe_exc_text(val)
    if isinstance(val, int) and not isinstance(val, bool):
        return fmt_compact_int(val)
    if isinstance(val, (tuple, list, set, frozenset, Mapping)):
        return safe_key(val)
    try:
        return str(val)
    except SAFE_LOG_ERRORS:
        return f"<{type(val).__name__}>"


def coerce_json_scalars(
    obj: object,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
    budget: list[int] | None = None,
) -> object:
    """Convert non-JSON leaf objects into bounded strings while detecting cycles and deep trees."""
    active_seen = seen if seen is not None else set()
    active_budget = budget if budget is not None else [MAX_LOG_TREE_NODES]

    def _walk(val: object, cur_depth: int) -> object:
        tv = type(val)
        if val is None or tv is bool or tv is int or tv is float or tv is str:
            return val
        if isinstance(val, (Mapping, list, tuple, set, frozenset)):
            vid = id(val)
            if vid in active_seen:
                return "[Cycle]"
            if cur_depth >= MAX_LOG_TREE_DEPTH or active_budget[0] <= 0:
                return "[Truncated]"
            active_budget[0] -= 1
            active_seen.add(vid)
            try:
                if isinstance(val, Mapping):
                    out: dict[object, object] = {}
                    for k, v in val.items():
                        if active_budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        out[k] = _walk(v, cur_depth + 1)
                    return out
                out_list: list[object] = []
                for item in val:
                    if active_budget[0] <= 0:
                        out_list.append("[Truncated]")
                        break
                    out_list.append(_walk(item, cur_depth + 1))
                return out_list
            finally:
                active_seen.discard(vid)
        return safe_summary_text(val)

    return _walk(obj, depth)


def to_redacted_dict(item: object) -> dict[str, object] | None:
    """Convert an action, mapping, dataclass, or scalar into a redacted plain dictionary."""
    if item is None:
        return None
    if isinstance(item, Mapping):
        red = redact_sensitive(dict(item))
        return {str(k): v for k, v in red.items()} if isinstance(red, Mapping) else {"value": red}
    if is_dataclass(item) and not isinstance(item, type):
        red = redact_sensitive(asdict(item))
        return {str(k): v for k, v in red.items()} if isinstance(red, Mapping) else {"value": red}
    if isinstance(item, os.PathLike):
        return {"value": redact_sensitive(item)}
    if isinstance(item, (bytes, bytearray)):
        return {"value": redact_sensitive(decode_bytes(item))}
    if isinstance(item, BaseException):
        return {"value": redact_sensitive(safe_exc_text(item))}
    if isinstance(item, int) and not isinstance(item, bool):
        return {"value": fmt_compact_int(item)}
    if isinstance(item, (tuple, list, set, frozenset)):
        return {"value": redact_sensitive(safe_key(item))}
    try:
        raw = str(item)
    except SAFE_LOG_ERRORS:
        raw = f"<{type(item).__name__}>"
    return {"value": redact_sensitive(raw)}


def resources_to_dict(res: ResourcesLogLike) -> dict[str, int]:
    """Serialize a ``ResourcesLogLike`` object into a plain integer mapping."""
    return {
        "pig_iron": int(res.pig_iron),
        "metal": int(res.metal),
        "kryptonite": int(res.kryptonite),
        "spice": int(res.spice),
    }


def first_non_empty_action_seq(actions: Sequence[PlannedActionLike]) -> bool:
    """Return True when ``actions`` has at least one entry."""
    return len(actions) > 0
