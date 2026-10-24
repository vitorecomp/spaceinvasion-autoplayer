"""Shared result dataclass and safe coercion helpers for `exec_ops`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import functools
import math
import os

from spaceinvasion.core import catalog
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.browser_client._protocols import call_with_optional_priority

call_with_interactive_priority: Callable[..., object] = functools.partial(
    call_with_optional_priority, priority="interactive"
)


def safe_optional_wait_limit(raw: object, default: float = 60.0) -> float | None:
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw.bit_length() > 128:
        return 3600.0 if raw > 0 else 0.0
    val = float(raw)
    if not math.isfinite(val):
        return default
    return max(0.0, min(3600.0, val))


def safe_str(
    val: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    if val is None:
        return ""
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except (TypeError, ValueError, OSError):
            return f"<{type(val).__name__}>"
        if val is None:
            return ""
        if isinstance(val, os.PathLike):
            return f"<{type(val).__name__}>"
    if isinstance(val, str):
        return val
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, int) and not isinstance(val, bool):
        return catalog.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    if isinstance(val, BaseException):
        return _safe_exception_str(val, depth=_depth, seen=_seen, budget=_budget)
    if isinstance(val, (tuple, list, set, frozenset, Mapping)):
        try:
            return catalog.safe_key(val)
        except (TypeError, ValueError, RecursionError):
            return f"<{type(val).__name__}>"
    try:
        return str(val)
    except (TypeError, ValueError, RecursionError):
        return f"<{type(val).__name__}>"


def _safe_exception_str(
    val: BaseException,
    *,
    depth: int,
    seen: set[int] | None,
    budget: list[int] | None,
) -> str:
    try:
        str_fn: object = type(val).__str__
    except (AttributeError, TypeError):
        str_fn = BaseException.__str__
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            return str(val)[:4096]
        except (TypeError, ValueError, RecursionError):
            pass
    try:
        args: object = val.args
    except (AttributeError, TypeError):
        args = ()
    if not isinstance(args, (tuple, list)) or not args:
        return "" if str_fn is BaseException.__str__ else type(val).__name__
    eff_seen = seen if seen is not None else set()
    eff_budget = budget if budget is not None else [256]
    vid = id(val)
    if vid in eff_seen or depth >= 16 or eff_budget[0] <= 0:
        return f"<{type(val).__name__}>"
    eff_budget[0] -= 1
    eff_seen.add(vid)
    try:
        if len(args) == 1:
            a0 = args[0]
            if isinstance(a0, str):
                return (repr(a0) if isinstance(val, KeyError) else a0)[:4096]
            return safe_str(a0, _depth=depth + 1, _seen=eff_seen, _budget=eff_budget)[:4096]
        parts: list[str] = []
        for a in args[:8]:
            if eff_budget[0] <= 0:
                parts.append("...")
                break
            parts.append(
                repr(a) if isinstance(a, str) else safe_str(a, _depth=depth + 1, _seen=eff_seen, _budget=eff_budget)
            )
        if len(args) > 8:
            parts.append(f"...+{len(args) - 8}")
        return ("(" + ", ".join(parts) + ")")[:4096]
    finally:
        eff_seen.discard(vid)


def safe_hourly_cap(raw: object, default: int = 30) -> int:
    cap = catalog.safe_int(raw, default)
    return max(0, min(1_000_000, cap))


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of attempting one planned action."""

    executed: bool
    dry_run: bool
    action: PlannedAction
    message: str
    updated_state: GameState | None = None
    observed_active_planet_id: int | None = None
    observed_active_planet_confirmed: bool | None = None
    target_cooldown_seconds: float | None = None
    target_block_reason: str | None = None
    target_is_bash_block: bool = False
    clear_target_debris: bool = False
    validated: bool = True
    validation_error: str | None = None
    observed_fleet_slots_available: int | None = None
    observed_fleet_slots_filled: int | None = None
