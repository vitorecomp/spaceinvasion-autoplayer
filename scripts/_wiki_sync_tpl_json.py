"""Generated JSON key/tree sanitization template for sync_wiki_data."""

from __future__ import annotations

_HELPER_CODE_JSON: str = '''

_MAX_KEY_NODES = 256
_MAX_KEY_CHARS = 4096
_MAX_TREE_NODES = 32_768
_MAX_TREE_DEPTH = 64


def _clip_str(text: str, limit: int = _MAX_KEY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _safe_json_key(
    k: Any,
    max_int: int,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
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
                fsp = os.fspath(k)
            except Exception:
                return f"<{type(k).__name__}>"
            return _safe_json_key(fsp, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(k, BaseException):
        str_fn = getattr(type(k), "__str__", BaseException.__str__)
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                return _clip_str(str(k), 16_384)
            except Exception:
                pass
        args = getattr(k, "args", ())
        if not isinstance(args, (tuple, list)) or not args:
            return "" if str_fn is BaseException.__str__ else type(k).__name__
        vid = id(k)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(k).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            if len(args) == 1:
                a0 = args[0]
                if isinstance(a0, str):
                    res = repr(a0) if isinstance(k, KeyError) else a0
                    return _clip_str(res, 16_384)
                return _clip_str(
                    _safe_json_key(a0, max_int, _depth=_depth + 1, _seen=seen, _budget=budget),
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
                    parts.append(
                        _safe_json_key(
                            item,
                            max_int,
                            _depth=_depth + 1,
                            _seen=seen,
                            _budget=budget,
                        )
                    )
            if len(args) > 8:
                parts.append(f"...+{len(args) - 8}")
            return _clip_str("(" + ", ".join(parts) + ")", _MAX_KEY_CHARS)
        finally:
            seen.discard(vid)
    if isinstance(k, (tuple, list, set, frozenset, Mapping)):
        vid = id(k)
        if vid in seen:
            if isinstance(k, tuple):
                return "(...)"
            if isinstance(k, list):
                return "[...]"
            if isinstance(k, frozenset):
                return "frozenset({...})"
            return "{...}"
        if _depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(k, tuple):
                parts = []
                for idx, item in enumerate(k):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(
                        _safe_json_key(item, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                    )
                inner = ", ".join(parts)
                return _clip_str(f"({inner},)" if len(k) == 1 else f"({inner})", _MAX_KEY_CHARS)
            if isinstance(k, list):
                parts = []
                for idx, item in enumerate(k):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(
                        _safe_json_key(item, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                    )
                return _clip_str("[" + ", ".join(parts) + "]", _MAX_KEY_CHARS)
            if isinstance(k, (set, frozenset)):
                parts = []
                for idx, item in enumerate(k):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(
                        _safe_json_key(item, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                    )
                inner = ", ".join(sorted(parts))
                if isinstance(k, frozenset):
                    return _clip_str(f"frozenset({{{inner}}})", _MAX_KEY_CHARS)
                return _clip_str(f"{{{inner}}}" if parts else "set()", _MAX_KEY_CHARS)
            # Mapping
            parts = []
            for idx, (mk, mv) in enumerate(k.items()):
                if idx >= 32 or budget[0] <= 0:
                    parts.append("...")
                    break
                ks = _safe_json_key(mk, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                vs = _safe_json_key(mv, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                parts.append(f"{ks!r}: {vs!r}")
            return _clip_str("{" + ", ".join(parts) + "}", _MAX_KEY_CHARS)
        finally:
            seen.discard(vid)
    try:
        return str(k)
    except Exception:
        return type(k).__name__


def safe_key(key: Any, *, max_int: int = MAX_JSON_SAFE_INT) -> str:
    """Convert arbitrary dict keys or identifiers to string safely (resisting >4300-digit ints/tuples)."""
    return _safe_json_key(key, max_int)


def sanitize_json_tree(
    obj: Any,
    *,
    max_int: int = MAX_JSON_SAFE_INT,
    nonfinite_float: Any = 0.0,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool or tv is str:
            return val
        if tv is int:
            return val if -max_int <= val <= max_int else cap_json_int(val, limit=max_int)
        if tv is float:
            return val if math.isfinite(val) else nonfinite_float
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset, os.PathLike, bytes, bytearray, int, float)):
            if not (is_dict or is_seq) and isinstance(val, (bool, str)):
                return str(val)
            if not (is_dict or is_seq) and isinstance(val, int):
                return cap_json_int(int(val), limit=max_int)
            if not (is_dict or is_seq) and isinstance(val, float):
                fval = float(val)
                return fval if math.isfinite(fval) else nonfinite_float
            if not (is_dict or is_seq) and isinstance(val, (bytes, bytearray)):
                return bytes(val).decode("utf-8", errors="replace")
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_dict or isinstance(val, Mapping):
                    out: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        sk = k if type(k) is str else _safe_json_key(k, max_int)
                        out[sk] = _walk(v, d + 1)
                    return out
                if is_seq or isinstance(val, (list, tuple, set, frozenset)):
                    out_list: list[Any] = []
                    for v in val:
                        if budget[0] <= 0:
                            out_list.append("[Truncated]")
                            break
                        out_list.append(_walk(v, d + 1))
                    return out_list
                try:
                    fsp = os.fspath(val)
                except Exception:
                    return safe_key(val, max_int=max_int)
                if isinstance(fsp, (bytes, bytearray)):
                    return bytes(fsp).decode("utf-8", errors="replace")
                if isinstance(fsp, str):
                    return fsp
                if isinstance(fsp, os.PathLike):
                    return safe_key(fsp, max_int=max_int)
                return _walk(fsp, d + 1)
            finally:
                seen.discard(vid)
        return safe_key(val, max_int=max_int)

    return _walk(obj, _depth)
'''
