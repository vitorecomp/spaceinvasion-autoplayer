"""Cycle-safe, depth-bounded JSON-tree and key sanitization helpers.

Pure stdlib helpers extracted from `spaceinvasion.core.game_data` with strict
type annotations and bounded resource/recursion guards.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import os
from typing import Any

from spaceinvasion.core.catalog.coercion import MAX_JSON_SAFE_INT, cap_json_int

_MAX_KEY_NODES = 256
_MAX_KEY_CHARS = 4096
_MAX_TREE_NODES = 32_768
_MAX_TREE_DEPTH = 64


def _clip_str(text: str, limit: int = _MAX_KEY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _format_exception_key(
    exc: BaseException,
    max_int: int,
    depth: int,
    seen: set[int],
    budget: list[int],
) -> str:
    str_fn = type(exc).__str__
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            return _clip_str(str(exc), 16_384)
        except (TypeError, ValueError, RuntimeError, RecursionError, LookupError, AttributeError):
            pass
    args = exc.args
    if not isinstance(args, (tuple, list)) or not args:
        return "" if str_fn is BaseException.__str__ else type(exc).__name__
    vid = id(exc)
    if vid in seen or depth >= 16 or budget[0] <= 0:
        return f"<{type(exc).__name__}>"
    budget[0] -= 1
    seen.add(vid)
    try:
        if len(args) == 1:
            a0 = args[0]
            if isinstance(a0, str):
                res = repr(a0) if isinstance(exc, KeyError) else a0
                return _clip_str(res, 16_384)
            return _clip_str(
                _safe_json_key(a0, max_int, _depth=depth + 1, _seen=seen, _budget=budget),
                _MAX_KEY_CHARS,
            )
        parts: list[str] = []
        for item in args[:8]:
            if budget[0] <= 0:
                parts.append("...")
                break
            if isinstance(item, str):
                parts.append(repr(item))
            else:
                parts.append(_safe_json_key(item, max_int, _depth=depth + 1, _seen=seen, _budget=budget))
        if len(args) > 8:
            parts.append(f"...+{len(args) - 8}")
        return _clip_str("(" + ", ".join(parts) + ")", _MAX_KEY_CHARS)
    finally:
        seen.discard(vid)


def _format_collection_key(
    col: tuple[object, ...] | list[object] | set[object] | frozenset[object] | Mapping[object, object],
    max_int: int,
    depth: int,
    seen: set[int],
    budget: list[int],
) -> str:
    vid = id(col)
    if vid in seen:
        if isinstance(col, tuple):
            return "(...)"
        if isinstance(col, list):
            return "[...]"
        if isinstance(col, frozenset):
            return "frozenset({...})"
        return "{...}"
    if depth >= 16 or budget[0] <= 0:
        return "..."
    budget[0] -= 1
    seen.add(vid)
    try:
        if isinstance(col, Mapping):
            parts: list[str] = []
            for idx, (mk, mv) in enumerate(col.items()):
                if idx >= 32 or budget[0] <= 0:
                    parts.append("...")
                    break
                ks = _safe_json_key(mk, max_int, _depth=depth + 1, _seen=seen, _budget=budget)
                vs = _safe_json_key(mv, max_int, _depth=depth + 1, _seen=seen, _budget=budget)
                parts.append(f"{ks!r}: {vs!r}")
            return _clip_str("{" + ", ".join(parts) + "}", _MAX_KEY_CHARS)
        items_out: list[str] = []
        for idx, item in enumerate(col):
            if idx >= 64 or budget[0] <= 0:
                items_out.append("...")
                break
            items_out.append(_safe_json_key(item, max_int, _depth=depth + 1, _seen=seen, _budget=budget))
        if isinstance(col, tuple):
            inner = ", ".join(items_out)
            return _clip_str(f"({inner},)" if len(col) == 1 else f"({inner})", _MAX_KEY_CHARS)
        if isinstance(col, list):
            return _clip_str("[" + ", ".join(items_out) + "]", _MAX_KEY_CHARS)
        inner = ", ".join(sorted(items_out))
        if isinstance(col, frozenset):
            return _clip_str(f"frozenset({{{inner}}})", _MAX_KEY_CHARS)
        return _clip_str(f"{{{inner}}}" if items_out else "set()", _MAX_KEY_CHARS)
    finally:
        seen.discard(vid)


def _safe_json_key(
    k: object,
    max_int: int,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    if isinstance(k, str):
        return k
    if isinstance(k, int) and not isinstance(k, bool):
        return str(cap_json_int(k, limit=max_int))
    if isinstance(k, (bytes, bytearray)):
        return bytes(k).decode("utf-8", errors="replace")
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_KEY_NODES]
    if isinstance(k, os.PathLike):
        vid = id(k)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(k).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            try:
                fsp: object = os.fspath(k)
            except (TypeError, ValueError, RuntimeError, OSError):
                return f"<{type(k).__name__}>"
            return _safe_json_key(fsp, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(k, BaseException):
        return _format_exception_key(k, max_int, _depth, seen, budget)
    if isinstance(k, (tuple, list, set, frozenset, Mapping)):
        return _format_collection_key(k, max_int, _depth, seen, budget)
    try:
        return str(k)
    except (TypeError, ValueError, RuntimeError, RecursionError, LookupError, AttributeError):
        return type(k).__name__


def safe_key(key: object, *, max_int: int = MAX_JSON_SAFE_INT) -> str:
    """Convert arbitrary dict keys or identifiers to string safely (resisting >4300-digit ints/tuples)."""
    return _safe_json_key(key, max_int)


def _walk_slow(
    val: object,
    d: int,
    *,
    max_int: int,
    nonfinite_float: object,
    seen: set[int],
    budget: list[int],
    walk_fn: Any,
) -> Any:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return str(val)
    if isinstance(val, int):
        return cap_json_int(int(val), limit=max_int)
    if isinstance(val, float):
        fval = float(val)
        return fval if math.isfinite(fval) else nonfinite_float
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, (Mapping, list, tuple, set, frozenset, os.PathLike)):
        vid = id(val)
        if vid in seen:
            return "[Cycle]"
        if d >= _MAX_TREE_DEPTH or budget[0] <= 0:
            return "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(val, Mapping):
                out_m: dict[str, Any] = {}
                for k, v in val.items():
                    if budget[0] <= 0:
                        out_m["[Truncated]"] = "[Truncated]"
                        break
                    sk = k if type(k) is str else _safe_json_key(k, max_int)
                    out_m[sk] = walk_fn(v, d + 1)
                return out_m
            if isinstance(val, (list, tuple, set, frozenset)):
                out_l: list[Any] = []
                for v in val:
                    if budget[0] <= 0:
                        out_l.append("[Truncated]")
                        break
                    out_l.append(walk_fn(v, d + 1))
                return out_l
            try:
                fsp: object = os.fspath(val)
            except (TypeError, ValueError, RuntimeError, OSError):
                return safe_key(val, max_int=max_int)
            if isinstance(fsp, (bytes, bytearray)):
                return bytes(fsp).decode("utf-8", errors="replace")
            if isinstance(fsp, str):
                return fsp
            if isinstance(fsp, os.PathLike):
                return safe_key(fsp, max_int=max_int)
            return walk_fn(fsp, d + 1)
        finally:
            seen.discard(vid)
    return safe_key(val, max_int=max_int)


def sanitize_json_tree(
    obj: object,
    *,
    max_int: int = MAX_JSON_SAFE_INT,
    nonfinite_float: object = 0.0,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> Any:
    """Walk an arbitrary object tree into cycle-safe, finite, JSON-serializable primitives."""
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_TREE_NODES]

    def _walk(val: object, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool or tv is str:
            return val
        if tv is int and isinstance(val, int):
            return val if -max_int <= val <= max_int else cap_json_int(val, limit=max_int)
        if tv is float and isinstance(val, float):
            return val if math.isfinite(val) else nonfinite_float
        if tv is list or tv is tuple or tv is dict or tv is set or tv is frozenset:
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if tv is dict and isinstance(val, dict):
                    out: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        sk = k if type(k) is str else _safe_json_key(k, max_int)
                        out[sk] = _walk(v, d + 1)
                    return out
                if isinstance(val, (list, tuple, set, frozenset)):
                    out_list: list[Any] = []
                    for v in val:
                        if budget[0] <= 0:
                            out_list.append("[Truncated]")
                            break
                        out_list.append(_walk(v, d + 1))
                    return out_list
            finally:
                seen.discard(vid)
        return _walk_slow(
            val,
            d,
            max_int=max_int,
            nonfinite_float=nonfinite_float,
            seen=seen,
            budget=budget,
            walk_fn=_walk,
        )

    return _walk(obj, _depth)
