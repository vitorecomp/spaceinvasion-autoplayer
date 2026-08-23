"""Structured JSONL and human-readable logging helpers for `logs/`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Optional, Sequence

from spaceinvasion import game_data
from spaceinvasion.state import GameState
from spaceinvasion.strategy import PlannedAction

_SECRET_DICT_KEYS: frozenset[str] = frozenset(
    {"sid", "password", "passwd", "cookie", "api_key", "secret", "authorization"}
)
_SECRET_DICT_STEMS: tuple[str, ...] = (
    "password",
    "passwd",
    "cookie",
    "api_key",
    "api-key",
    "secret",
    "authorization",
    "sid",
)
_BENIGN_KEY_PREFIX_TOKENS: frozenset[str] = frozenset(
    {
        "has",
        "no",
        "non",
        "without",
        "is",
        "allow",
        "require",
        "enable",
        "disable",
        "need",
        "check",
        "use",
        "missing",
        "empty",
    }
)
_SECRET_FAST_MARKERS: tuple[str, ...] = (
    "sid",
    "pass",
    "cookie",
    "api_key",
    "api-key",
    "secret",
    "auth",
    "bearer",
)


def is_secret_dict_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    low = key.strip().lower()
    if not low:
        return False
    if not low.isascii():
        low = low.replace("i\u0307", "i").replace("\u0131", "i").replace("\u017f", "s")
    if low == "api-key" or low in _SECRET_DICT_KEYS:
        return True
    for stem in _SECRET_DICT_STEMS:
        if low.endswith(stem):
            prefix = low[: len(low) - len(stem)]
            if not prefix:
                return True
            if prefix[-1] not in ("_", "-"):
                return False
            stripped = prefix.strip("_-")
            if not stripped:
                return True
            parts = [p for p in stripped.replace("-", "_").split("_") if p]
            if not parts:
                return True
            if not all(p.isascii() and p.isalnum() for p in parts):
                return False
            if parts[0] in _BENIGN_KEY_PREFIX_TOKENS:
                return False
            return True
    return False


_is_secret_dict_key = is_secret_dict_key

_COMP_PREFIX_RE = r"[_-]*(?:[a-z0-9]+[_-]+)*"
_COMP_KEY_RE = rf"(?P<key>{_COMP_PREFIX_RE}(?:sid|password|passwd|cookie|api_key|api-key|secret|authorization))"
_COMP_KV_KEY_RE = rf"(?P<key>{_COMP_PREFIX_RE}(?:password|passwd|cookie|api_key|api-key|secret))"
_COMP_AUTH_KEY_RE = rf"(?P<key>{_COMP_PREFIX_RE}authorization)"
_AUTH_SCHEME_RE = r"(?:bearer|basic|token|digest)"
_KV_SEP_RE = r"(?:[ \t]*[:=][ \t]*|%(?:25){0,4}3[ad])"
_SCHEME_SEP_RE = r"(?:[ \t]+|%(?:25){0,4}20|\+)"
_TOK_SAFE_CHAR = r"[^\s&,;\"'`<>#)}\]\[]"
_UNQUOTED_TOK_RE = rf"(?:(?!%(?:25){{0,4}}(?:26|23|20|09|0[ad]|22|27|3[bce]|5[bd])){_TOK_SAFE_CHAR})+"


def _quoted_val_pattern(p: str) -> str:
    return (
        rf"(?P<{p}_vq>[\"'])(?:"
        rf"(?P<{p}_qval>(?:(?!(?P={p}_vq))(?:\\[^\r\n]|[^\\\r\n]))+)(?P<{p}_cq>(?P={p}_vq))|"
        rf"(?!(?P={p}_vq)|\\?[\"'])(?P<{p}_uqval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_evq2>\\\\(?P<{p}_ec2>[\"']))(?:"
        rf"(?P<{p}_eqval2>(?:[^\\\r\n]|\\\\(?!(?P={p}_ec2))|\\(?!(?P={p}_ec2)|\\)|\\\\\\(?P={p}_ec2))+)(?P<{p}_ecq2>(?P={p}_evq2))|"
        rf"(?!(?P={p}_evq2)|\\?[\"'])(?P<{p}_euqval2>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_evq>\\(?P<{p}_ec>[\"']))(?:"
        rf"(?P<{p}_eqval>(?:[^\\\r\n]|\\\\\\\\|\\\\\\(?P={p}_ec)|\\\\(?!(?P={p}_ec))[^\\\r\n]|\\(?!(?P={p}_ec))[^\\\r\n])+)(?P<{p}_ecq>(?P={p}_evq))|"
        rf"(?!(?P={p}_evq)|\\?[\"'])(?P<{p}_euqval>{_UNQUOTED_TOK_RE})"
        rf")"
    )


def _delim_val_pattern(p: str) -> str:
    return (
        f"{_quoted_val_pattern(p)}|"
        rf"(?P<{p}_bk><)(?:"
        rf"(?P<{p}_bkval>[ \t]*[^\s<>\r\n][^<>\r\n]*)(?P<{p}_bkc>>)|"
        rf"(?P<{p}_ubkval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_sb>\[)(?:"
        rf"(?P<{p}_sbval>[ \t]*[^\s\[\]\r\n][^\[\]\r\n]*)(?P<{p}_sbc>\])|"
        rf"(?P<{p}_usbval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_pq>%(?:25){{0,4}}(?:22|27))(?:"
        rf"(?P<{p}_pqval>(?:(?!(?P={p}_pq)|%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)(?P<{p}_pqc>(?P={p}_pq))|"
        rf"(?P<{p}_upqval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_pb>%(?:25){{0,4}}3c)(?:"
        rf"(?P<{p}_pbval>(?:(?!%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)(?P<{p}_pbc>%(?:25){{0,4}}3e)|"
        rf"(?P<{p}_upbval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_psb>%(?:25){{0,4}}5b)(?:"
        rf"(?P<{p}_psbval>(?:(?!%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)(?P<{p}_psbc>%(?:25){{0,4}}5d)|"
        rf"(?P<{p}_upsbval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?!(?:\\?[\"']|<|\[|%(?:25){{0,4}}(?:22|27|3c|5b)))(?P<{p}_uval>{_UNQUOTED_TOK_RE})"
    )


def _format_quoted_val(match: re.Match[str], p: str) -> str:
    vq = match.group(f"{p}_vq")
    if vq is not None:
        return f"{vq}REDACTED{vq}" if match.group(f"{p}_cq") is not None else f"{vq}REDACTED"
    evq2 = match.group(f"{p}_evq2")
    if evq2 is not None:
        return f"{evq2}REDACTED{evq2}" if match.group(f"{p}_ecq2") is not None else f"{evq2}REDACTED"
    evq = match.group(f"{p}_evq")
    if evq is not None:
        return f"{evq}REDACTED{evq}" if match.group(f"{p}_ecq") is not None else f"{evq}REDACTED"
    return ""


def _format_delim_val(match: re.Match[str], p: str) -> str:
    q = _format_quoted_val(match, p)
    if q:
        return q
    if match.group(f"{p}_bk") is not None:
        return "<REDACTED>" if match.group(f"{p}_bkc") is not None else "<REDACTED"
    if match.group(f"{p}_sb") is not None:
        return "[REDACTED]" if match.group(f"{p}_sbc") is not None else "[REDACTED"
    pq = match.group(f"{p}_pq")
    if pq is not None:
        return f"{pq}REDACTED{pq}" if match.group(f"{p}_pqc") is not None else f"{pq}REDACTED"
    pb = match.group(f"{p}_pb")
    if pb is not None:
        pbc = match.group(f"{p}_pbc")
        return f"{pb}REDACTED{pbc}" if pbc is not None else f"{pb}REDACTED"
    psb = match.group(f"{p}_psb")
    if psb is not None:
        psbc = match.group(f"{p}_psbc")
        return f"{psb}REDACTED{psbc}" if psbc is not None else f"{psb}REDACTED"
    return "REDACTED"


_SECRET_JSON_KV_RE = re.compile(
    rf"(?P<prefix>(?P<kq>\\{{0,2}}[\"']){_COMP_KEY_RE}(?P=kq)[ \t]*:[ \t]*)(?:{_quoted_val_pattern('j')})",
    re.IGNORECASE,
)
_SECRET_URLENC_JSON_KV_RE = re.compile(
    rf"(?P<prefix>(?P<kq>%(?:25){{0,4}}(?:22|27)){_COMP_KEY_RE}(?P=kq)(?:[ \t]|%(?:25){{0,4}}20|\+)*(?::|%(?:25){{0,4}}3a)(?:[ \t]|%(?:25){{0,4}}20|\+)*)(?:"
    rf"{_quoted_val_pattern('uj')}|"
    rf"(?P<uj_pq>%(?:25){{0,4}}(?:22|27))(?:"
    rf"(?P<uj_pqval>(?:(?!(?P=uj_pq)|%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)(?P<uj_pqc>(?P=uj_pq))|"
    rf"(?P<uj_upqval>{_UNQUOTED_TOK_RE})"
    rf")"
    rf")",
    re.IGNORECASE,
)
_SECRET_QUOTED_KV_RE = re.compile(
    rf"(?P<prefix>(?<![\w-]){_COMP_KEY_RE}{_KV_SEP_RE}(?:{_AUTH_SCHEME_RE}{_SCHEME_SEP_RE})?)(?:{_quoted_val_pattern('qk')})",
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(
    rf"(?<![\w-])(?P<hdr>{_COMP_AUTH_KEY_RE}{_KV_SEP_RE})"
    rf"(?:"
    rf"(?P<scheme>{_AUTH_SCHEME_RE}{_SCHEME_SEP_RE})(?:{_delim_val_pattern('s')})"
    rf"|"
    rf"(?!{_AUTH_SCHEME_RE}(?:{_SCHEME_SEP_RE}|$|(?=[&,;\"'`<>#)}}\[%]|\])))(?:{_delim_val_pattern('r')})"
    rf")",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(
    rf"(?P<prefix>(?:(?<![A-Za-z0-9_])|%(?:25){{0,4}}(?:20|22|27|3[acd]|2c|5b|7b))bearer(?:[ \t]+|%(?:25){{0,4}}20))(?:{_delim_val_pattern('b')})",
    re.IGNORECASE,
)
_SID_RE = re.compile(
    rf"(?P<bprefix>(?<![\w-])|%(?:25){{0,4}}(?:3[fb]|26|3b|20|09|22|27))(?P<key>{_COMP_PREFIX_RE}sid)(?P<sep>=|%(?:25){{0,4}}3d)(?:{_delim_val_pattern('sid')})",
    re.IGNORECASE,
)
_SECRET_KV_RE = re.compile(
    rf"(?<![\w-]){_COMP_KV_KEY_RE}(?P<sep>{_KV_SEP_RE})(?:{_delim_val_pattern('kv')})",
    re.IGNORECASE,
)
_HTML_SECRET_INPUT_RE = re.compile(
    rf"(?P<p1><input\b[^>]*?\bname=(?P<nq1>[\"']){_COMP_KEY_RE}(?P=nq1)[^>]*?\bvalue=(?P<vq1>[\"']))(?P<val1>[^\"']*)(?P<s1>(?P=vq1))"
    rf"|"
    rf"(?P<p2><input\b[^>]*?\bvalue=(?P<vq2>[\"']))(?P<val2>[^\"']*)(?P<m2>(?P=vq2)[^>]*?\bname=(?P<nq2>[\"'])(?P<key2>{_COMP_PREFIX_RE}(?:sid|password|passwd|cookie|api_key|api-key|secret|authorization))(?P=nq2))",
    re.IGNORECASE,
)


def _sub_html_secret_input(match: re.Match[str]) -> str:
    if match.group("p1") is not None:
        if not is_secret_dict_key(match.group("key")):
            return match.group(0)
        return f"{match.group('p1')}REDACTED{match.group('s1')}"
    if not is_secret_dict_key(match.group("key2")):
        return match.group(0)
    return f"{match.group('p2')}REDACTED{match.group('m2')}"


def _sub_secret_json_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{_format_quoted_val(match, 'j')}"


def _sub_secret_urlenc_json_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    q = _format_quoted_val(match, "uj")
    if q:
        return f"{match.group('prefix')}{q}"
    pq = match.group("uj_pq")
    if match.group("uj_pqc") is not None:
        return f"{match.group('prefix')}{pq}REDACTED{pq}"
    return f"{match.group('prefix')}{pq}REDACTED"


def _sub_secret_quoted(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{_format_quoted_val(match, 'qk')}"


def _sub_auth_header(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    hdr = match.group("hdr")
    scheme = match.group("scheme")
    if scheme is None:
        return f"{hdr}{_format_delim_val(match, 'r')}"
    return f"{hdr}{scheme}{_format_delim_val(match, 's')}"


def _sub_bearer(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{_format_delim_val(match, 'b')}"


def _sub_sid_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('bprefix')}{match.group('key')}{match.group('sep')}{_format_delim_val(match, 'sid')}"


def _sub_secret_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('key')}{match.group('sep')}{_format_delim_val(match, 'kv')}"


def _decode_bytes(raw: bytes | bytearray) -> str:
    return bytes(raw).decode("utf-8", errors="replace")


def _redact_text(text: str) -> str:
    if not text or len(text) < 5:
        return text
    low = text.lower()
    if not any(marker in low for marker in _SECRET_FAST_MARKERS) and (
        text.isascii()
        or (
            "\u0130" not in text
            and "\u0131" not in text
            and "\u017f" not in text
            and "\u212a" not in text
        )
    ):
        return text
    masked = _HTML_SECRET_INPUT_RE.sub(_sub_html_secret_input, text)
    masked = _SECRET_JSON_KV_RE.sub(_sub_secret_json_kv, masked)
    masked = _SECRET_URLENC_JSON_KV_RE.sub(_sub_secret_urlenc_json_kv, masked)
    masked = _SECRET_QUOTED_KV_RE.sub(_sub_secret_quoted, masked)
    masked = _AUTH_HEADER_RE.sub(_sub_auth_header, masked)
    masked = _BEARER_RE.sub(_sub_bearer, masked)
    masked = _SID_RE.sub(_sub_sid_kv, masked)
    return _SECRET_KV_RE.sub(_sub_secret_kv, masked)


_MAX_LOG_KEY_NODES = 256
_MAX_LOG_KEY_CHARS = 4096
_MAX_LOG_TREE_NODES = 32_768
_MAX_LOG_TREE_DEPTH = 64


def _clip_log_str(text: str, limit: int = _MAX_LOG_KEY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _safe_fspath_text(obj: os.PathLike[Any]) -> str:
    try:
        raw = os.fspath(obj)
    except Exception:
        return f"<{type(obj).__name__}>"
    if isinstance(raw, (bytes, bytearray)):
        return _decode_bytes(raw)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, os.PathLike):
        return f"<{type(raw).__name__}>"
    return _safe_summary_text(raw, f"<{type(raw).__name__}>")


def _safe_exc_text(
    exc: BaseException,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_LOG_KEY_NODES]
    str_fn = getattr(type(exc), "__str__", BaseException.__str__)
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            return _clip_log_str(str(exc), 16_384)
        except Exception:
            pass
    args = getattr(exc, "args", ())
    if not isinstance(args, (tuple, list)) or not args:
        return "" if str_fn is BaseException.__str__ else type(exc).__name__
    vid = id(exc)
    if vid in seen or _depth >= 16 or budget[0] <= 0:
        return f"<{type(exc).__name__}>"
    budget[0] -= 1
    seen.add(vid)
    try:
        if len(args) == 1:
            a0 = args[0]
            if isinstance(a0, str):
                res = repr(a0) if isinstance(exc, KeyError) else a0
                return _clip_log_str(res, 16_384)
            if isinstance(a0, BaseException):
                return _safe_exc_text(a0, _depth=_depth + 1, _seen=seen, _budget=budget)
            return _clip_log_str(_safe_summary_text(a0, f"<{type(a0).__name__}>"), _MAX_LOG_KEY_CHARS)
        parts: list[str] = []
        for a in args[:8]:
            if budget[0] <= 0:
                parts.append("...")
                break
            if isinstance(a, str):
                parts.append(repr(a))
            elif isinstance(a, BaseException):
                parts.append(_safe_exc_text(a, _depth=_depth + 1, _seen=seen, _budget=budget))
            else:
                parts.append(_safe_summary_text(a, f"<{type(a).__name__}>"))
        if len(args) > 8:
            parts.append(f"...+{len(args) - 8}")
        return _clip_log_str("(" + ", ".join(parts) + ")", _MAX_LOG_KEY_CHARS)
    finally:
        seen.discard(vid)


def _redact_mapping_key(
    key: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    if isinstance(key, str):
        return _redact_text(key)
    if key is None or isinstance(key, (bool, int, float)):
        return key
    if isinstance(key, (bytes, bytearray)):
        return _redact_text(_decode_bytes(key))
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_LOG_KEY_NODES]
    if isinstance(key, os.PathLike):
        vid = id(key)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(key).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            return _redact_text(_safe_fspath_text(key))
        finally:
            seen.discard(vid)
    if isinstance(key, BaseException):
        return _redact_text(_safe_exc_text(key, _depth=_depth, _seen=seen, _budget=budget))
    if isinstance(key, (tuple, frozenset)):
        vid = id(key)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return "(...)" if isinstance(key, tuple) else "frozenset({...})"
        budget[0] -= 1
        seen.add(vid)
        try:
            items: list[Any] = []
            for idx, el in enumerate(key):
                if idx >= 64 or budget[0] <= 0:
                    items.append("...")
                    break
                items.append(_redact_mapping_key(el, _depth=_depth + 1, _seen=seen, _budget=budget))
            return tuple(items) if isinstance(key, tuple) else frozenset(items)
        except TypeError:
            pass
        finally:
            seen.discard(vid)
    try:
        safe_k = game_data.safe_key(key)
    except Exception:
        safe_k = f"<{type(key).__name__}>"
    return _redact_text(safe_k)


def redact_sensitive(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    """Recursively mask transient session tokens and secret key-value fields before writing logs."""
    if isinstance(obj, str):
        return _redact_text(obj)
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return _redact_text(_decode_bytes(obj))
    if isinstance(obj, BaseException):
        return _redact_text(_safe_exc_text(obj))
    if isinstance(obj, os.PathLike):
        return _redact_text(_safe_fspath_text(obj))
    if isinstance(obj, (Mapping, list, tuple, set, frozenset)):
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [_MAX_LOG_TREE_NODES]
        vid = id(obj)
        if vid in seen:
            return "[Cycle]"
        if _depth >= _MAX_LOG_TREE_DEPTH or budget[0] <= 0:
            return "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(obj, Mapping):
                out: dict[Any, Any] = {}
                for k, v in obj.items():
                    if budget[0] <= 0:
                        out["[Truncated]"] = "[Truncated]"
                        break
                    clean_k = _redact_text(k) if isinstance(k, str) else _redact_mapping_key(k)
                    if isinstance(clean_k, str) and is_secret_dict_key(clean_k):
                        if v is None or v == "":
                            out[clean_k] = v
                        elif isinstance(v, (bytes, bytearray)) and len(v) == 0:
                            out[clean_k] = ""
                        else:
                            out[clean_k] = "REDACTED"
                    else:
                        out[clean_k] = redact_sensitive(
                            v, _depth=_depth + 1, _seen=seen, _budget=budget
                        )
                return out
            out_list: list[Any] = []
            for v in obj:
                if budget[0] <= 0:
                    out_list.append("[Truncated]")
                    break
                out_list.append(
                    redact_sensitive(v, _depth=_depth + 1, _seen=seen, _budget=budget)
                )
            return out_list
        finally:
            seen.discard(vid)
    return obj


def _safe_summary_text(val: Any, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        return _safe_fspath_text(val)
    if isinstance(val, (bytes, bytearray)):
        return _decode_bytes(val)
    if isinstance(val, str):
        return val
    if isinstance(val, BaseException):
        return _safe_exc_text(val)
    if isinstance(val, int) and not isinstance(val, bool):
        return game_data.fmt_compact_int(val)
    if isinstance(val, (tuple, list, set, frozenset, Mapping)):
        return game_data.safe_key(val)
    try:
        return str(val)
    except Exception:
        return f"<{type(val).__name__}>"


def _coerce_json_scalars(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_LOG_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool or tv is int or tv is float or tv is str:
            return val
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if not (is_dict or is_seq) and isinstance(val, (bool, int, float, str)):
            return val
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset)):
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_LOG_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_dict or isinstance(val, Mapping):
                    out: dict[Any, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        out[k] = _walk(v, d + 1)
                    return out
                out_list: list[Any] = []
                for v in val:
                    if budget[0] <= 0:
                        out_list.append("[Truncated]")
                        break
                    out_list.append(_walk(v, d + 1))
                return out_list
            finally:
                seen.discard(vid)
        return _safe_summary_text(val)

    return _walk(obj, _depth)


def _to_dict(item: Any) -> Optional[dict[str, Any]]:
    if item is None:
        return None
    if isinstance(item, Mapping):
        return redact_sensitive(dict(item))
    if is_dataclass(item) and not isinstance(item, type):
        return redact_sensitive(asdict(item))
    if isinstance(item, os.PathLike):
        return {"value": redact_sensitive(item)}
    if isinstance(item, (bytes, bytearray)):
        return {"value": redact_sensitive(_decode_bytes(item))}
    if isinstance(item, BaseException):
        return {"value": redact_sensitive(_safe_exc_text(item))}
    if isinstance(item, int) and not isinstance(item, bool):
        return {"value": game_data.fmt_compact_int(item)}
    if isinstance(item, (tuple, list, set, frozenset)):
        return {"value": redact_sensitive(game_data.safe_key(item))}
    try:
        raw = str(item)
    except Exception:
        raw = f"<{type(item).__name__}>"
    return {"value": redact_sensitive(raw)}


def format_action_summary(action: PlannedAction | dict[str, Any] | None) -> str:
    """Format an action concisely for console and `.log` lines."""
    data = _to_dict(action)
    if not data:
        return "none"
    prio = redact_sensitive(_safe_summary_text(data.get("priority"), "?"))
    ready = "READY" if data.get("executable_now") else "WAIT"
    kind = redact_sensitive(_safe_summary_text(data.get("kind"), "ACTION"))
    name = redact_sensitive(_safe_summary_text(data.get("target_name"), "?"))
    lvl = data.get("target_level")
    coords = redact_sensitive(_safe_summary_text(data.get("planet_coords"), ""))
    if isinstance(lvl, int) and not isinstance(lvl, bool) and lvl:
        lvl_part = f" -> L{game_data.fmt_compact_int(lvl)}"
    else:
        lvl_part = f" -> L{redact_sensitive(_safe_summary_text(lvl, ''))}" if lvl else ""
    coord_part = f" @ [{coords}]" if coords else ""
    missing = data.get("missing")
    miss_parts = (
        [
            f"{redact_sensitive(_safe_summary_text(k, '?'))}={game_data.fmt_compact_int(v, grouped=True)}"
            for k, v in missing.items()
            if isinstance(v, int) and not isinstance(v, bool) and v > 0
        ]
        if isinstance(missing, Mapping)
        else []
    )
    miss_part = f" (missing: {', '.join(miss_parts)})" if miss_parts else ""
    return f"[{prio}][{ready}] {kind} {name}{lvl_part}{coord_part}{miss_part}"


def pick_next_action(
    actions: Sequence[PlannedAction],
    actual_action: Optional[PlannedAction],
    *,
    after_live_execution: bool = False,
) -> Optional[PlannedAction]:
    """Select the logical next action after `actual_action`.

    When `actions` comes from a post-execution refresh (`after_live_execution=True`),
    the top planned action is already the next step. Otherwise, return the second
    distinct planned action (or the top action if waiting).
    """
    if not actions:
        return None
    if after_live_execution or actual_action is None or not actual_action.executable_now:
        return actions[0]
    for candidate in actions:
        if (
            candidate.kind != actual_action.kind
            or candidate.planet_id != actual_action.planet_id
            or candidate.target_id != actual_action.target_id
            or candidate.target_name != actual_action.target_name
            or candidate.target_level != actual_action.target_level
        ):
            return candidate
    return actions[1] if len(actions) > 1 else None


def append_tick_logs(
    log_dir: Path,
    *,
    source: str,
    tick: int,
    mode: str,
    state: GameState,
    actual_action: PlannedAction | dict[str, Any] | None,
    actual_result: Optional[dict[str, Any]],
    next_action: PlannedAction | dict[str, Any] | None,
    outcomes: Sequence[dict[str, Any]] = (),
) -> tuple[Path, Path]:
    """Write structured JSONL and readable `.log` entries under `log_dir`."""
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / f"{source}.jsonl"
    text_path = log_dir / f"{source}.log"

    ts = datetime.now(timezone.utc).isoformat()
    planet = state.active_planet()
    actual_dict = _to_dict(actual_action)
    next_dict = _to_dict(next_action)

    record = redact_sensitive(
        _coerce_json_scalars(
            game_data.sanitize_json_tree(
                redact_sensitive(
                    {
                        "ts": ts,
                        "tick": tick,
                        "mode": mode,
                        "universe_id": state.universe_id,
                        "planet": planet.coords if planet else None,
                        "resources": asdict(planet.resources) if planet else None,
                        "energy": (
                            {"produced": planet.energy.produced, "used": planet.energy.used}
                            if planet
                            else None
                        ),
                        "actual_action": actual_dict,
                        "actual_result": actual_result,
                        "next_action": next_dict,
                        "outcomes": list(outcomes),
                    }
                ),
                max_int=game_data.MAX_JSON_SAFE_INT,
                nonfinite_float=None,
            )
        )
    )
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")

    sanitized_result = record.get("actual_result")
    raw_msg = (
        sanitized_result.get("message", "no action")
        if isinstance(sanitized_result, Mapping)
        else "no action"
    )
    result_msg = _safe_summary_text(raw_msg, "no action") if raw_msg is not None else "no action"
    planet_label = _safe_summary_text(record.get("planet"), "?") or "?"
    mode_label = _safe_summary_text(record.get("mode"), "") or _safe_summary_text(mode, "")
    if isinstance(tick, int) and not isinstance(tick, bool):
        tick_label = game_data.fmt_compact_int(tick)
    else:
        safe_tick = record.get("tick")
        if isinstance(safe_tick, int) and not isinstance(safe_tick, bool):
            tick_label = game_data.fmt_compact_int(safe_tick)
        else:
            tick_label = _safe_summary_text(safe_tick if safe_tick is not None else tick, "")
    res_str = planet.resources.format_short() if planet else "?"
    line = redact_sensitive(
        f"[{ts}] [tick {tick_label} | {mode_label}] {planet_label} | {res_str}\n"
        f"  ACTUAL: {format_action_summary(actual_dict)} => {result_msg}\n"
        f"  NEXT:   {format_action_summary(next_dict)}\n"
    )
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)

    return jsonl_path, text_path


def append_api_log(
    log_dir: Path,
    *,
    method: str,
    path: str,
    status: int,
    payload: Optional[dict[str, Any]] = None,
    response: Optional[dict[str, Any]] = None,
) -> tuple[Path, Path]:
    """Append an HTTP API request/command audit event into `logs/game_api.*`."""
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "game_api.jsonl"
    text_path = log_dir / "game_api.log"

    ts = datetime.now(timezone.utc).isoformat()
    payload_map = payload if isinstance(payload, Mapping) else {}
    resp_map = response if isinstance(response, Mapping) else {}
    actual_action = resp_map.get("action")
    next_action = resp_map.get("next_action")
    cmd = payload_map.get("command") or resp_map.get("command")

    record = redact_sensitive(
        _coerce_json_scalars(
            game_data.sanitize_json_tree(
                redact_sensitive(
                    {
                        "ts": ts,
                        "method": method,
                        "path": path,
                        "status": status,
                        "command": cmd,
                        "payload": payload,
                        "actual_action": actual_action,
                        "next_action": next_action,
                        "executed": resp_map.get("executed"),
                        "dry_run": resp_map.get("dry_run"),
                        "message": resp_map.get("message") or resp_map.get("error"),
                    }
                ),
                max_int=game_data.MAX_JSON_SAFE_INT,
                nonfinite_float=None,
            )
        )
    )
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")

    method_label = _safe_summary_text(record.get("method"), "")
    redacted_path = _safe_summary_text(record.get("path"), "")
    msg = _safe_summary_text(record.get("message"), "")
    cmd_label = _safe_summary_text(record.get("command"), "")
    actual_str = (
        format_action_summary(record["actual_action"])
        if record.get("actual_action")
        else (cmd_label or redacted_path)
    )
    next_str = format_action_summary(record["next_action"]) if record["next_action"] else "n/a"
    status_label = (
        game_data.fmt_compact_int(status)
        if isinstance(status, int) and not isinstance(status, bool)
        else _safe_summary_text(record.get("status"), "")
    )
    line = redact_sensitive(
        f"[{ts}] {method_label} {redacted_path} -> {status_label} | actual={actual_str}"
        f" | next={next_str}" + (f" | {msg}" if msg else "") + "\n"
    )
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)

    return jsonl_path, text_path


DEFAULT_CRASH_LOG_DIR: Path = Path(__file__).resolve().parent.parent / "state"


def append_crash_debug_log(
    *,
    component: str,
    event: str,
    error: Optional[BaseException] = None,
    details: Optional[Mapping[str, Any]] = None,
    crash_dir: Optional[Path] = None,
) -> Optional[tuple[Path, Path]]:
    """Append a gitignored, `sid=`-redacted diagnostic/crash record to `state/crash_debug.*`.

    Never raises so instrumentation cannot mask or alter runtime behavior.
    """
    import traceback as _tb

    try:
        target_dir = crash_dir if crash_dir is not None else DEFAULT_CRASH_LOG_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = target_dir / "crash_debug.jsonl"
        text_path = target_dir / "crash_debug.log"

        ts = datetime.now(timezone.utc).isoformat()
        exc_type = type(error).__name__ if error is not None else None
        exc_msg = _safe_exc_text(error) if error is not None else None
        tb_str = (
            "".join(_tb.format_exception(type(error), error, error.__traceback__))
            if error is not None and getattr(error, "__traceback__", None) is not None
            else None
        )
        raw_rec = {
            "ts": ts,
            "component": component,
            "event": event,
            "exc_type": exc_type,
            "exc_message": exc_msg,
            "traceback": _clip_log_str(tb_str, 16_384) if tb_str else None,
            "details": dict(details) if isinstance(details, Mapping) else {},
        }
        record = redact_sensitive(
            _coerce_json_scalars(
                game_data.sanitize_json_tree(
                    redact_sensitive(raw_rec),
                    max_int=game_data.MAX_JSON_SAFE_INT,
                    nonfinite_float=None,
                )
            )
        )
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, allow_nan=False) + "\n")

        header = redact_sensitive(
            f"=== [{ts}] {component} :: {event}"
            + (f" ({exc_type}: {exc_msg})" if exc_type else "")
            + " ===\n"
        )
        det_json = redact_sensitive(
            json.dumps(record.get("details") or {}, indent=2, allow_nan=False)
        )
        tb_redacted = redact_sensitive(str(record.get("traceback") or ""))
        with text_path.open("a", encoding="utf-8") as handle:
            handle.write(header)
            if det_json and det_json != "{}":
                handle.write(f"details: {det_json}\n")
            if tb_redacted:
                handle.write(f"traceback:\n{tb_redacted}\n")
        return jsonl_path, text_path
    except Exception:
        return None
