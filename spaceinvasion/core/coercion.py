"""Canonical numeric coercion, bounded integer parsing, and safe JSON tree helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import itertools
import math
import os
import re

MAX_JSON_SAFE_INT: int = 10**200
INT64_MIN: int = -(2**63)
INT64_MAX: int = (2**63) - 1
MAX_SAFE_JS_INT: int = (2**53) - 1
MAX_ASCII_INT_DIGITS: int = 4000

_NON_INT_LITERALS: frozenset[str] = frozenset("null none ~ nan inf +inf -inf infinity +infinity -infinity".split())
_NULL_LITERALS: frozenset[str] = frozenset({"", "null", "none", "~"})
_TRUE_LITERALS: frozenset[str] = frozenset("true 1 yes on".split())
_FALSE_LITERALS: frozenset[str] = frozenset("false 0 no off".split())
_NON_ASCII_DIGIT_RE: re.Pattern[str] = re.compile(r"[^\d]", re.ASCII)
_SAFE_ERRS = (TypeError, ValueError, OverflowError, OSError, RuntimeError, AttributeError, LookupError, RecursionError)


def coerce_optional_int(value: object, default: int | None = None) -> int | None:
    """Coerce ``value`` to ``int | None``, returning ``default`` for invalid/non-ASCII/non-finite inputs."""
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii() or txt.lower() in _NON_INT_LITERALS:
            return default
        value = txt
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except _SAFE_ERRS:
        try:
            parsed_f = float(value)  # type: ignore[arg-type]
        except _SAFE_ERRS:
            return default
        return int(parsed_f) if math.isfinite(parsed_f) else default


def coerce_int(value: object, default: int = 0) -> int:
    """Coerce ``value`` to ``int``, returning ``default`` for invalid/non-ASCII/non-finite inputs."""
    parsed = coerce_optional_int(value, None)
    return default if parsed is None else parsed


def cap_json_int(value: object, *, limit: int = MAX_JSON_SAFE_INT, default: int = 0) -> int:
    """Coerce ``value`` to ``int`` and clamp into ``[-limit, limit]``."""
    v = value if (isinstance(value, int) and not isinstance(value, bool)) else coerce_int(value, default)
    return limit if v > limit else (-limit if v < -limit else v)


def coerce_bounded_int(value: object, default: int = 0, *, cap: int = MAX_JSON_SAFE_INT) -> int:
    """Parse ASCII integer strings or numeric values without calling ``int()`` on >4000-digit strings."""
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, int):
        return cap_json_int(value, limit=cap, default=default)
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        sign = -1 if txt.startswith("-") else 1
        digits = txt[1:] if txt[:1] in ("+", "-") else txt
        if not digits or not (digits.isascii() and digits.isdigit()):
            return default
        trimmed = digits.lstrip("0") or "0"
        return (
            (-cap if sign < 0 else cap)
            if len(trimmed) > MAX_ASCII_INT_DIGITS
            else cap_json_int(sign * int(trimmed), limit=cap, default=default)
        )
    return cap_json_int(coerce_int(value, default), limit=cap, default=default)


def coerce_sql_int(value: object, default: int = 0) -> int:
    """Coerce ``value`` to ``int`` clamped to signed 64-bit SQL integer bounds."""
    return max(INT64_MIN, min(INT64_MAX, coerce_int(value, default)))


def coerce_sql_optional_int(value: object) -> int | None:
    """Coerce ``value`` to ``int | None`` clamped to signed 64-bit SQL integer bounds."""
    val = coerce_optional_int(value, None)
    return None if val is None else max(INT64_MIN, min(INT64_MAX, val))


def parse_strict_ascii_int(
    value: object,
    *,
    name: str = "value",
    allow_none: bool = False,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int | None:
    """Strictly parse an API/config integer; rejects booleans, Unicode digits, and unsafe floats."""
    if allow_none and value is None:
        return None
    if isinstance(value, bool) or value is None or isinstance(value, (Mapping, list, tuple, set, frozenset)):
        raise ValueError(f"{name!r} must be an integer.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or abs(value) > MAX_SAFE_JS_INT:
            raise ValueError(f"{name!r} must be a safe finite integer.")
        parsed = int(value)
    elif isinstance(value, str):
        txt = value.strip()
        digits = txt[1:] if txt[:1] in ("+", "-") else txt
        if not txt or not txt.isascii() or not digits or not digits.isdigit():
            raise ValueError(f"{name!r} must be an integer.")
        trimmed = digits.lstrip("0") or "0"
        if len(trimmed) > MAX_ASCII_INT_DIGITS:
            raise ValueError(f"{name!r} integer is out of range.")
        parsed = -int(trimmed) if txt[0] == "-" else int(trimmed)
    else:
        raise ValueError(f"{name!r} must be an integer.")
    if min_val is not None and parsed < min_val:
        raise ValueError(f"{name!r} must be >= {min_val}.")
    if max_val is not None and parsed > max_val:
        raise ValueError(f"{name!r} must be <= {max_val}.")
    return parsed


def parse_strict_finite_float(
    value: object, *, name: str = "value", min_val: float | None = None, max_val: float | None = None
) -> float:
    """Strictly parse an API/config finite float; rejects booleans, underscores, non-ASCII, and NaN/Inf."""
    if isinstance(value, bool) or value is None or isinstance(value, (Mapping, list, tuple, set, frozenset)):
        raise ValueError(f"{name!r} must be a finite number.")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii() or "_" in txt:
            raise ValueError(f"{name!r} must be a finite number.")
        value = txt
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except _SAFE_ERRS as exc:
        raise ValueError(f"{name!r} must be a finite number.") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name!r} must be a finite number.")
    if min_val is not None and parsed < min_val:
        raise ValueError(f"{name!r} must be >= {min_val}.")
    if max_val is not None and parsed > max_val:
        raise ValueError(f"{name!r} must be <= {max_val}.")
    return parsed


def parse_game_int(raw: object) -> int:
    """Parse SpaceInvasion dot-grouped integer strings like ``'1.234.567'`` -> ``1234567``."""
    if isinstance(raw, os.PathLike):
        try:
            raw = os.fspath(raw)
        except _SAFE_ERRS:
            return 0
    if raw is None or isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return MAX_JSON_SAFE_INT if abs(raw).bit_length() > 13_288 else abs(raw)
    try:
        raw_s = bytes(raw).decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except ValueError:
        return MAX_JSON_SAFE_INT
    except _SAFE_ERRS:
        return 0
    digits = _NON_ASCII_DIGIT_RE.sub("", raw_s)
    return 0 if not digits else (MAX_JSON_SAFE_INT if len(digits) > MAX_ASCII_INT_DIGITS else int(digits))


def coerce_optional_finite_float(
    value: object, default: float | None = None, *, overflow_cap: float | None = None, normalize_neg_zero: bool = False
) -> float | None:
    """Coerce ``value`` to a finite ``float | None``, returning ``default`` for invalid/NaN/Inf inputs."""
    if isinstance(value, os.PathLike):
        try:
            value = os.fspath(value)
        except _SAFE_ERRS:
            return default
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii() or txt.lower() in _NULL_LITERALS:
            return default
        value = txt
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except OverflowError:
        return (
            (overflow_cap if value > 0 else -overflow_cap)
            if (overflow_cap is not None and isinstance(value, int))
            else default
        )
    except _SAFE_ERRS:
        return default
    return default if not math.isfinite(parsed) else (0.0 if (normalize_neg_zero and parsed == 0.0) else parsed)


def coerce_finite_float(
    value: object, default: float = 0.0, *, overflow_cap: float | None = 1e300, normalize_neg_zero: bool = False
) -> float:
    """Coerce ``value`` to a finite ``float``, returning ``default`` for invalid/NaN/Inf inputs."""
    parsed = coerce_optional_finite_float(value, None, overflow_cap=overflow_cap, normalize_neg_zero=normalize_neg_zero)
    return default if parsed is None else parsed


def coerce_bool(value: object, default: bool = False) -> bool:
    """Coerce strings/numbers/booleans into ``bool``, guarding containers and non-finite floats."""
    if isinstance(value, os.PathLike):
        try:
            value = os.fspath(value)
        except _SAFE_ERRS:
            return default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, float):
        return default if not math.isfinite(value) else bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        return True if low in _TRUE_LITERALS else (False if low in _FALSE_LITERALS else default)
    try:
        return bool(value)
    except _SAFE_ERRS:
        return default


def fmt_compact_int(value: object, *, signed: bool = False, grouped: bool = False, default: int = 0) -> str:
    """Format an integer safely without triggering Python's 4300-digit string limit."""
    v = coerce_int(value, default)
    if abs(v).bit_length() >= 12_000:
        return (
            f"{'-' if v < 0 else ('+' if signed and v > 0 else '')}~1e{int(abs(v).bit_length() * 0.3010299956639812)}"
        )
    return f"{v:+,}" if (signed and grouped) else (f"{v:+d}" if signed else (f"{v:,}" if grouped else str(v)))


def _clip(text: str, limit: int = 4096) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _safe_json_key(key: object, max_int: int, depth: int, seen: set[int], budget: list[int]) -> str:
    if isinstance(key, str):
        return key
    if isinstance(key, int) and not isinstance(key, bool):
        return str(cap_json_int(key, limit=max_int))
    if isinstance(key, (bytes, bytearray)):
        return bytes(key).decode("utf-8", errors="replace")
    if isinstance(key, (os.PathLike, BaseException, tuple, list, set, frozenset, Mapping)):
        vid, d = id(key), depth + 1
        if vid in seen:
            if isinstance(key, tuple):
                return "(...)"
            if isinstance(key, list):
                return "[...]"
            if isinstance(key, frozenset):
                return "frozenset({...})"
            return "{...}" if isinstance(key, (set, Mapping)) else f"<{type(key).__name__}>"
        if depth >= 16 or budget[0] <= 0:
            return f"<{type(key).__name__}>" if isinstance(key, (os.PathLike, BaseException)) else "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(key, os.PathLike):
                try:
                    return _safe_json_key(os.fspath(key), max_int, d, seen, budget)
                except _SAFE_ERRS:
                    return f"<{type(key).__name__}>"
            if isinstance(key, BaseException):
                str_fn = getattr(type(key), "__str__", BaseException.__str__)
                if str_fn not in (BaseException.__str__, KeyError.__str__):
                    try:
                        return _clip(str(key), 16_384)
                    except _SAFE_ERRS:
                        pass
                args = getattr(key, "args", ())
                if not isinstance(args, (tuple, list)) or not args:
                    return "" if str_fn is BaseException.__str__ else type(key).__name__
                if len(args) == 1:
                    a0 = args[0]
                    if isinstance(a0, str):
                        return _clip(repr(a0) if isinstance(key, KeyError) else a0, 16_384)
                    return _clip(_safe_json_key(a0, max_int, d, seen, budget))
                parts = [
                    repr(a) if isinstance(a, str) else _safe_json_key(a, max_int, d, seen, budget)
                    for a in args[:8]
                    if budget[0] > 0
                ]
                if len(args) > 8:
                    parts.append(f"...+{len(args) - 8}")
                return _clip(f"({', '.join(parts)})")
            if isinstance(key, Mapping):
                pairs = [
                    f"{_safe_json_key(mk, max_int, d, seen, budget)!r}: {_safe_json_key(mv, max_int, d, seen, budget)!r}"
                    for mk, mv in itertools.islice(key.items(), 32)
                    if budget[0] > 0
                ]
                return _clip("{" + ", ".join(pairs) + "}")
            items = [_safe_json_key(el, max_int, d, seen, budget) for el in itertools.islice(key, 64) if budget[0] > 0]
            if isinstance(key, tuple):
                return _clip(f"({', '.join(items)},)" if len(key) == 1 else f"({', '.join(items)})")
            if isinstance(key, list):
                return _clip(f"[{', '.join(items)}]")
            inner_s = ", ".join(sorted(items))
            if isinstance(key, frozenset):
                return _clip(f"frozenset({{{inner_s}}})")
            return _clip(f"{{{inner_s}}}" if items else "set()")
        finally:
            seen.discard(vid)
    try:
        return str(key)
    except _SAFE_ERRS:
        return type(key).__name__


def safe_key(key: object, *, max_int: int = MAX_JSON_SAFE_INT) -> str:
    """Convert arbitrary dict keys or identifiers to string safely (resisting >4300-digit ints/cycles)."""
    return _safe_json_key(key, max_int, 0, set(), [256])


def sanitize_json_tree(obj: object, *, max_int: int = MAX_JSON_SAFE_INT, nonfinite_float: object = 0.0) -> object:
    """Recursively sanitize an object tree for strict RFC-8259 JSON serialization."""
    seen: set[int] = set()
    budget = [32_768]

    def _walk(val: object, depth: int) -> object:
        if val is None or isinstance(val, bool):
            return bool(val) if val is not None else None
        if isinstance(val, str):
            return str(val)
        if isinstance(val, int):
            iv = int(val)
            return iv if -max_int <= iv <= max_int else cap_json_int(iv, limit=max_int)
        if isinstance(val, float):
            fv = float(val)
            return fv if math.isfinite(fv) else nonfinite_float
        if isinstance(val, (bytes, bytearray)):
            return bytes(val).decode("utf-8", errors="replace")
        if not isinstance(val, (Mapping, list, tuple, set, frozenset, os.PathLike)):
            return safe_key(val, max_int=max_int)
        vid = id(val)
        if vid in seen:
            return "[Cycle]"
        if depth >= 64 or budget[0] <= 0:
            return "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(val, Mapping):
                out_d: dict[str, object] = {}
                for k, v in val.items():
                    if budget[0] <= 0:
                        out_d["[Truncated]"] = "[Truncated]"
                        break
                    out_d[k if type(k) is str else safe_key(k, max_int=max_int)] = _walk(v, depth + 1)
                return out_d
            if isinstance(val, (list, tuple, set, frozenset)):
                out_l: list[object] = []
                for v in val:
                    if budget[0] <= 0:
                        out_l.append("[Truncated]")
                        break
                    out_l.append(_walk(v, depth + 1))
                return out_l
            try:
                fsp: object = os.fspath(val)
            except _SAFE_ERRS:
                return safe_key(val, max_int=max_int)
            if isinstance(fsp, os.PathLike):
                return safe_key(val, max_int=max_int)
            if isinstance(fsp, (bytes, bytearray)):
                return bytes(fsp).decode("utf-8", errors="replace")
            return fsp if isinstance(fsp, str) else _walk(fsp, depth + 1)
        finally:
            seen.discard(vid)

    return _walk(obj, 0)


def safe_sequence(value: object) -> tuple[object, ...]:
    """Return ``tuple(value)`` for non-string sequences, otherwise ``()``."""
    return tuple(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


parse_si_int = parse_game_int
