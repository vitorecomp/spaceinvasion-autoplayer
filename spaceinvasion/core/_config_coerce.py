"""Bounded, cycle-safe JSON/scalar coercion and alias helpers for `StrategyConfig`."""

from __future__ import annotations

from collections.abc import Mapping
import math
import os

from spaceinvasion.core._config_aliases import (
    canonicalize_fleet_research_key,
    canonicalize_shipyard_target_key,
)
from spaceinvasion.core.redaction import (
    _clip as _clip_cfg_str,
)
from spaceinvasion.core.redaction import (
    _safe_text as _redaction_safe_text,
)
from spaceinvasion.core.redaction import (
    is_secret_dict_key as _is_cfg_secret_dict_key,
)
from spaceinvasion.core.redaction import (
    redact_text as _redact_cfg_text,
)

_MAX_CFG_KEY_NODES = 256
_MAX_CFG_TREE_NODES = 32_768
_MAX_CFG_TREE_DEPTH = 64
_MAX_CFG_INT: int = 10**200
_SAFE_COERCE_ERRORS = (
    TypeError,
    ValueError,
    OverflowError,
    OSError,
    RuntimeError,
    AttributeError,
    LookupError,
    RecursionError,
)


def decode_cfg_bytes(raw: bytes | bytearray) -> str:
    return bytes(raw).decode("utf-8", errors="replace")


def safe_fspath(value: object) -> tuple[bool, object]:
    try:
        return True, os.fspath(value)  # type: ignore[call-overload]
    except _SAFE_COERCE_ERRORS:
        return False, None


def safe_cfg_exc_text(
    exc: BaseException,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_KEY_NODES]
    return _clip_cfg_str(_redaction_safe_text(exc, f"<{type(exc).__name__}>", _depth, seen, budget), 16_384)


def clamp_cfg_int(value: int) -> int:
    if value > _MAX_CFG_INT:
        return _MAX_CFG_INT
    if value < -_MAX_CFG_INT:
        return -_MAX_CFG_INT
    return value


def _format_seq_repr(val: object, *, depth: int, seen: set[int], budget: list[int]) -> str:
    if isinstance(val, tuple):
        parts = _collect_repr_items(val, depth=depth, seen=seen, budget=budget)
        inner = ", ".join(parts)
        return _clip_cfg_str(f"({inner},)" if len(val) == 1 else f"({inner})")
    if isinstance(val, list):
        parts = _collect_repr_items(val, depth=depth, seen=seen, budget=budget)
        return _clip_cfg_str("[" + ", ".join(parts) + "]")
    if isinstance(val, (set, frozenset)):
        parts = _collect_repr_items(val, depth=depth, seen=seen, budget=budget)
        inner = ", ".join(sorted(parts))
        return _clip_cfg_str(
            f"frozenset({{{inner}}})" if isinstance(val, frozenset) else (f"{{{inner}}}" if parts else "set()")
        )
    return "{}"


def _collect_repr_items(items: object, *, depth: int, seen: set[int], budget: list[int]) -> list[str]:
    parts: list[str] = []
    if not isinstance(items, (tuple, list, set, frozenset)):
        return parts
    for idx, el in enumerate(items):
        if idx >= 64 or budget[0] <= 0:
            parts.append("...")
            break
        parts.append(safe_cfg_key_repr(el, _depth=depth + 1, _seen=seen, _budget=budget))
    return parts


def _cycle_placeholder(val: object) -> str:
    if isinstance(val, tuple):
        return "(...)"
    if isinstance(val, list):
        return "[...]"
    if isinstance(val, frozenset):
        return "frozenset({...})"
    return "{...}"


def _format_cfg_key(
    val: object,
    *,
    as_repr: bool,
    depth: int,
    seen: set[int],
    budget: list[int],
) -> str:
    if isinstance(val, str):
        return repr(val) if as_repr else val
    if isinstance(val, (bytes, bytearray)):
        text = decode_cfg_bytes(val)
        return repr(text) if as_repr else text
    if isinstance(val, int) and not isinstance(val, bool):
        return str(clamp_cfg_int(val))
    if isinstance(val, os.PathLike):
        vid = id(val)
        if vid in seen or depth >= 16 or budget[0] <= 0:
            return f"<{type(val).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            ok, fsp = safe_fspath(val)
            return (
                _format_cfg_key(fsp, as_repr=as_repr, depth=depth + 1, seen=seen, budget=budget)
                if ok
                else f"<{type(val).__name__}>"
            )
        finally:
            seen.discard(vid)
    if isinstance(val, BaseException):
        exc_txt = safe_cfg_exc_text(val, _depth=depth, _seen=seen, _budget=budget)
        return repr(exc_txt) if as_repr else exc_txt
    if isinstance(val, (tuple, frozenset, list, set, Mapping)):
        vid = id(val)
        if vid in seen:
            return _cycle_placeholder(val)
        if depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(val, Mapping):
                parts: list[str] = []
                for idx, (mk, mv) in enumerate(val.items()):
                    if idx >= 32 or budget[0] <= 0:
                        parts.append("...")
                        break
                    ks = _format_cfg_key(mk, as_repr=as_repr, depth=depth + 1, seen=seen, budget=budget)
                    vs = _format_cfg_key(mv, as_repr=as_repr, depth=depth + 1, seen=seen, budget=budget)
                    parts.append(f"{ks}: {vs}" if as_repr else f"{ks!r}: {vs!r}")
                return _clip_cfg_str("{" + ", ".join(parts) + "}")
            return _format_seq_repr(val, depth=depth, seen=seen, budget=budget)
        finally:
            seen.discard(vid)
    try:
        return repr(val) if as_repr else str(val)
    except _SAFE_COERCE_ERRORS:
        return f"<{type(val).__name__}>"


def safe_cfg_key_repr(
    val: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_KEY_NODES]
    return _format_cfg_key(val, as_repr=True, depth=_depth, seen=seen, budget=budget)


def safe_cfg_key(
    key: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_KEY_NODES]
    return _format_cfg_key(key, as_repr=False, depth=_depth, seen=seen, budget=budget)


def _sanitize_scalar(val: object) -> tuple[bool, object]:
    if val is None or isinstance(val, bool):
        return True, (val if type(val) is bool or val is None else bool(val))
    if isinstance(val, (bytes, bytearray)):
        return True, _redact_cfg_text(decode_cfg_bytes(val))
    if isinstance(val, str):
        return True, _redact_cfg_text(val if type(val) is str else str(val))
    if isinstance(val, int):
        return True, clamp_cfg_int(val if type(val) is int else int(val))
    if isinstance(val, float):
        fv = val if type(val) is float else float(val)
        return True, (fv if math.isfinite(fv) else None)
    if isinstance(val, BaseException):
        return True, _redact_cfg_text(safe_cfg_exc_text(val))
    return False, None


def sanitize_cfg_json_tree(
    obj: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> object:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_TREE_NODES]
    handled, scalar = _sanitize_scalar(obj)
    if handled:
        return scalar
    if isinstance(obj, (Mapping, list, tuple, set, frozenset, os.PathLike)):
        vid = id(obj)
        if vid in seen:
            return "[Cycle]"
        if _depth >= _MAX_CFG_TREE_DEPTH or budget[0] <= 0:
            return "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(obj, os.PathLike):
                ok, fsp = safe_fspath(obj)
                if not ok or isinstance(fsp, os.PathLike):
                    return f"<{type(obj).__name__}>"
                return sanitize_cfg_json_tree(fsp, _depth=_depth + 1, _seen=seen, _budget=budget)
            if isinstance(obj, Mapping):
                out: dict[str, object] = {}
                for k, v in obj.items():
                    if budget[0] <= 0:
                        out["[Truncated]"] = "[Truncated]"
                        break
                    clean_k = _redact_cfg_text(k) if type(k) is str else _redact_cfg_text(safe_cfg_key(k))
                    if _is_cfg_secret_dict_key(clean_k):
                        if v is None or v == "":
                            out[clean_k] = v
                        elif isinstance(v, (bytes, bytearray)) and len(v) == 0:
                            out[clean_k] = ""
                        else:
                            out[clean_k] = "REDACTED"
                    else:
                        out[clean_k] = sanitize_cfg_json_tree(v, _depth=_depth + 1, _seen=seen, _budget=budget)
                return out
            out_list: list[object] = []
            for v in obj:
                if budget[0] <= 0:
                    out_list.append("[Truncated]")
                    break
                out_list.append(sanitize_cfg_json_tree(v, _depth=_depth + 1, _seen=seen, _budget=budget))
            return out_list
        finally:
            seen.discard(vid)
    try:
        raw = str(obj)
    except _SAFE_COERCE_ERRORS:
        raw = f"<{type(obj).__name__}>"
    return _redact_cfg_text(raw)


def parse_cfg_digit_string(raw: str) -> int | None:
    s = raw.strip()
    if not s:
        return None
    neg = False
    if s[0] in ("+", "-"):
        neg = s[0] == "-"
        s = s[1:]
    if not (s.isascii() and s.isdigit()):
        return None
    trimmed = s.lstrip("0") or "0"
    if len(trimmed) > 4000:
        return -_MAX_CFG_INT if neg else _MAX_CFG_INT
    parsed = int(trimmed)
    return clamp_cfg_int(-parsed if neg else parsed)


def safe_cfg_optional_int(value: object, default: int | None = None) -> int | None:
    safe_default = clamp_cfg_int(default) if isinstance(default, int) and not isinstance(default, bool) else default
    if isinstance(value, os.PathLike):
        ok, value = safe_fspath(value)
        if not ok:
            return safe_default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset, bool)):
        return safe_default
    if isinstance(value, (bytes, bytearray)):
        value = decode_cfg_bytes(value)
    if isinstance(value, str):
        if value.strip().lower() in ("", "null", "none", "~"):
            return safe_default
        parsed_s = parse_cfg_digit_string(value)
        return parsed_s if parsed_s is not None else safe_default
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return safe_default
        return clamp_cfg_int(int(value))  # type: ignore[call-overload]
    except _SAFE_COERCE_ERRORS:
        return safe_default


def safe_cfg_int(value: object, default: int) -> int:
    parsed = safe_cfg_optional_int(value, default)
    return parsed if parsed is not None else clamp_cfg_int(default)


def safe_cfg_float(value: object, default: float) -> float:
    if isinstance(value, os.PathLike):
        ok, value = safe_fspath(value)
        if not ok:
            return default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset, bool)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = decode_cfg_bytes(value)
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isascii() or s.lower() in ("null", "none", "~"):
            return default
        value = s
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except _SAFE_COERCE_ERRORS:
        return default
    return parsed if math.isfinite(parsed) else default


def safe_cfg_bool(value: object, default: bool) -> bool:
    if isinstance(value, os.PathLike):
        ok, value = safe_fspath(value)
        if not ok:
            return default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = decode_cfg_bytes(value)
    if isinstance(value, float):
        return default if not math.isfinite(value) else bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("", "null", "none", "~"):
            return default
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        return default
    try:
        return bool(value)
    except _SAFE_COERCE_ERRORS:
        return default


__all__ = [
    "_SAFE_COERCE_ERRORS",
    "canonicalize_fleet_research_key",
    "canonicalize_shipyard_target_key",
    "clamp_cfg_int",
    "decode_cfg_bytes",
    "parse_cfg_digit_string",
    "safe_cfg_bool",
    "safe_cfg_exc_text",
    "safe_cfg_float",
    "safe_cfg_int",
    "safe_cfg_key",
    "safe_cfg_key_repr",
    "safe_cfg_optional_int",
    "safe_fspath",
    "sanitize_cfg_json_tree",
]
