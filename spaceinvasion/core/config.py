"""Non-secret strategy profile loader (`config.yaml` / `config.json`) for SpaceInvasion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Optional

VALID_PLANNER_MODES: tuple[str, ...] = ("priority", "ga_nn")

_CFG_SECRET_DICT_KEYS: frozenset[str] = frozenset(
    {"sid", "password", "passwd", "cookie", "api_key", "secret", "authorization"}
)
_CFG_SECRET_DICT_STEMS: tuple[str, ...] = (
    "password",
    "passwd",
    "cookie",
    "api_key",
    "api-key",
    "secret",
    "authorization",
    "sid",
)
_CFG_BENIGN_KEY_PREFIX_TOKENS: frozenset[str] = frozenset(
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
_CFG_SECRET_FAST_MARKERS: tuple[str, ...] = (
    "sid",
    "pass",
    "cookie",
    "api_key",
    "api-key",
    "secret",
    "auth",
    "bearer",
)


def _is_cfg_secret_dict_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    low = key.strip().lower()
    if not low:
        return False
    if not low.isascii():
        low = low.replace("i\u0307", "i").replace("\u0131", "i").replace("\u017f", "s")
    if low == "api-key" or low in _CFG_SECRET_DICT_KEYS:
        return True
    for stem in _CFG_SECRET_DICT_STEMS:
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
            if parts[0] in _CFG_BENIGN_KEY_PREFIX_TOKENS:
                return False
            return True
    return False


_CFG_COMP_PREFIX_RE = r"[_-]*(?:[a-z0-9]+[_-]+)*"
_CFG_COMP_KEY_RE = rf"(?P<key>{_CFG_COMP_PREFIX_RE}(?:sid|password|passwd|cookie|api_key|api-key|secret|authorization))"
_CFG_COMP_KV_KEY_RE = rf"(?P<key>{_CFG_COMP_PREFIX_RE}(?:password|passwd|cookie|api_key|api-key|secret))"
_CFG_COMP_AUTH_KEY_RE = rf"(?P<key>{_CFG_COMP_PREFIX_RE}authorization)"
_CFG_AUTH_SCHEME_RE = r"(?:bearer|basic|token|digest)"
_CFG_KV_SEP_RE = r"(?:[ \t]*[:=][ \t]*|%(?:25){0,4}3[ad])"
_CFG_SCHEME_SEP_RE = r"(?:[ \t]+|%(?:25){0,4}20|\+)"
_CFG_TOK_SAFE_CHAR = r"[^\s&,;\"'`<>#)}\]\[]"
_CFG_UNQUOTED_TOK_RE = rf"(?:(?!%(?:25){{0,4}}(?:26|23|20|09|0[ad]|22|27|3[bce]|5[bd])){_CFG_TOK_SAFE_CHAR})+"


def _cfg_quoted_val_pattern(p: str) -> str:
    return (
        rf"(?P<{p}_vq>[\"'])(?:"
        rf"(?P<{p}_qval>(?:(?!(?P={p}_vq))(?:\\[^\r\n]|[^\\\r\n]))+)(?P<{p}_cq>(?P={p}_vq))|"
        rf"(?!(?P={p}_vq)|\\?[\"'])(?P<{p}_uqval>{_CFG_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_evq2>\\\\(?P<{p}_ec2>[\"']))(?:"
        rf"(?P<{p}_eqval2>(?:[^\\\r\n]|\\\\(?!(?P={p}_ec2))|\\(?!(?P={p}_ec2)|\\)|\\\\\\(?P={p}_ec2))+)(?P<{p}_ecq2>(?P={p}_evq2))|"
        rf"(?!(?P={p}_evq2)|\\?[\"'])(?P<{p}_euqval2>{_CFG_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_evq>\\(?P<{p}_ec>[\"']))(?:"
        rf"(?P<{p}_eqval>(?:[^\\\r\n]|\\\\\\\\|\\\\\\(?P={p}_ec)|\\\\(?!(?P={p}_ec))[^\\\r\n]|\\(?!(?P={p}_ec))[^\\\r\n])+)(?P<{p}_ecq>(?P={p}_evq))|"
        rf"(?!(?P={p}_evq)|\\?[\"'])(?P<{p}_euqval>{_CFG_UNQUOTED_TOK_RE})"
        rf")"
    )


def _cfg_delim_val_pattern(p: str) -> str:
    return (
        f"{_cfg_quoted_val_pattern(p)}|"
        rf"(?P<{p}_bk><)(?:"
        rf"(?P<{p}_bkval>[ \t]*[^\s<>\r\n][^<>\r\n]*)(?P<{p}_bkc>>)|"
        rf"(?P<{p}_ubkval>{_CFG_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_sb>\[)(?:"
        rf"(?P<{p}_sbval>[ \t]*[^\s\[\]\r\n][^\[\]\r\n]*)(?P<{p}_sbc>\])|"
        rf"(?P<{p}_usbval>{_CFG_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_pq>%(?:25){{0,4}}(?:22|27))(?:"
        rf"(?P<{p}_pqval>(?:(?!(?P={p}_pq)|%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_CFG_TOK_SAFE_CHAR})+)(?P<{p}_pqc>(?P={p}_pq))|"
        rf"(?P<{p}_upqval>{_CFG_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_pb>%(?:25){{0,4}}3c)(?:"
        rf"(?P<{p}_pbval>(?:(?!%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_CFG_TOK_SAFE_CHAR})+)(?P<{p}_pbc>%(?:25){{0,4}}3e)|"
        rf"(?P<{p}_upbval>{_CFG_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_psb>%(?:25){{0,4}}5b)(?:"
        rf"(?P<{p}_psbval>(?:(?!%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_CFG_TOK_SAFE_CHAR})+)(?P<{p}_psbc>%(?:25){{0,4}}5d)|"
        rf"(?P<{p}_upsbval>{_CFG_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?!(?:\\?[\"']|<|\[|%(?:25){{0,4}}(?:22|27|3c|5b)))(?P<{p}_uval>{_CFG_UNQUOTED_TOK_RE})"
    )


def _format_cfg_quoted_val(match: re.Match[str], p: str) -> str:
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


def _format_cfg_delim_val(match: re.Match[str], p: str) -> str:
    q = _format_cfg_quoted_val(match, p)
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


_CFG_SECRET_JSON_KV_RE = re.compile(
    rf"(?P<prefix>(?P<kq>\\{{0,2}}[\"']){_CFG_COMP_KEY_RE}(?P=kq)[ \t]*:[ \t]*)(?:{_cfg_quoted_val_pattern('j')})",
    re.IGNORECASE,
)
_CFG_SECRET_URLENC_JSON_KV_RE = re.compile(
    rf"(?P<prefix>(?P<kq>%(?:25){{0,4}}(?:22|27)){_CFG_COMP_KEY_RE}(?P=kq)(?:[ \t]|%(?:25){{0,4}}20|\+)*(?::|%(?:25){{0,4}}3a)(?:[ \t]|%(?:25){{0,4}}20|\+)*)(?:"
    rf"{_cfg_quoted_val_pattern('uj')}|"
    rf"(?P<uj_pq>%(?:25){{0,4}}(?:22|27))(?:"
    rf"(?P<uj_pqval>(?:(?!(?P=uj_pq)|%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_CFG_TOK_SAFE_CHAR})+)(?P<uj_pqc>(?P=uj_pq))|"
    rf"(?P<uj_upqval>{_CFG_UNQUOTED_TOK_RE})"
    rf")"
    rf")",
    re.IGNORECASE,
)
_CFG_SECRET_QUOTED_KV_RE = re.compile(
    rf"(?P<prefix>(?<![\w-]){_CFG_COMP_KEY_RE}{_CFG_KV_SEP_RE}(?:{_CFG_AUTH_SCHEME_RE}{_CFG_SCHEME_SEP_RE})?)(?:{_cfg_quoted_val_pattern('qk')})",
    re.IGNORECASE,
)
_CFG_AUTH_HEADER_RE = re.compile(
    rf"(?<![\w-])(?P<hdr>{_CFG_COMP_AUTH_KEY_RE}{_CFG_KV_SEP_RE})"
    rf"(?:"
    rf"(?P<scheme>{_CFG_AUTH_SCHEME_RE}{_CFG_SCHEME_SEP_RE})(?:{_cfg_delim_val_pattern('s')})"
    rf"|"
    rf"(?!{_CFG_AUTH_SCHEME_RE}(?:{_CFG_SCHEME_SEP_RE}|$|(?=[&,;\"'`<>#)}}\[%]|\])))(?:{_cfg_delim_val_pattern('r')})"
    rf")",
    re.IGNORECASE,
)
_CFG_BEARER_RE = re.compile(
    rf"(?P<prefix>(?:(?<![A-Za-z0-9_])|%(?:25){{0,4}}(?:20|22|27|3[acd]|2c|5b|7b))bearer(?:[ \t]+|%(?:25){{0,4}}20))(?:{_cfg_delim_val_pattern('b')})",
    re.IGNORECASE,
)
_CFG_SID_RE = re.compile(
    rf"(?P<bprefix>(?<![\w-])|%(?:25){{0,4}}(?:3[fb]|26|3b|20|09|22|27))(?P<key>{_CFG_COMP_PREFIX_RE}sid)(?P<sep>=|%(?:25){{0,4}}3d)(?:{_cfg_delim_val_pattern('sid')})",
    re.IGNORECASE,
)
_CFG_SECRET_KV_RE = re.compile(
    rf"(?<![\w-]){_CFG_COMP_KV_KEY_RE}(?P<sep>{_CFG_KV_SEP_RE})(?:{_cfg_delim_val_pattern('kv')})",
    re.IGNORECASE,
)


def _sub_cfg_secret_json_kv(match: re.Match[str]) -> str:
    if not _is_cfg_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{_format_cfg_quoted_val(match, 'j')}"


def _sub_cfg_secret_urlenc_json_kv(match: re.Match[str]) -> str:
    if not _is_cfg_secret_dict_key(match.group("key")):
        return match.group(0)
    q = _format_cfg_quoted_val(match, "uj")
    if q:
        return f"{match.group('prefix')}{q}"
    pq = match.group("uj_pq")
    if match.group("uj_pqc") is not None:
        return f"{match.group('prefix')}{pq}REDACTED{pq}"
    return f"{match.group('prefix')}{pq}REDACTED"


def _sub_cfg_secret_quoted(match: re.Match[str]) -> str:
    if not _is_cfg_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{_format_cfg_quoted_val(match, 'qk')}"


def _sub_cfg_auth_header(match: re.Match[str]) -> str:
    if not _is_cfg_secret_dict_key(match.group("key")):
        return match.group(0)
    hdr = match.group("hdr")
    scheme = match.group("scheme")
    if scheme is None:
        return f"{hdr}{_format_cfg_delim_val(match, 'r')}"
    return f"{hdr}{scheme}{_format_cfg_delim_val(match, 's')}"


def _sub_cfg_bearer(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{_format_cfg_delim_val(match, 'b')}"


def _sub_cfg_sid_kv(match: re.Match[str]) -> str:
    if not _is_cfg_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('bprefix')}{match.group('key')}{match.group('sep')}{_format_cfg_delim_val(match, 'sid')}"


def _sub_cfg_secret_kv(match: re.Match[str]) -> str:
    if not _is_cfg_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('key')}{match.group('sep')}{_format_cfg_delim_val(match, 'kv')}"


def _decode_cfg_bytes(raw: bytes | bytearray) -> str:
    return bytes(raw).decode("utf-8", errors="replace")


def _redact_cfg_text(text: str) -> str:
    if not text or len(text) < 5:
        return text
    low = text.lower()
    if not any(marker in low for marker in _CFG_SECRET_FAST_MARKERS) and (
        text.isascii()
        or (
            "\u0130" not in text
            and "\u0131" not in text
            and "\u017f" not in text
            and "\u212a" not in text
        )
    ):
        return text
    masked = _CFG_SECRET_JSON_KV_RE.sub(_sub_cfg_secret_json_kv, text)
    masked = _CFG_SECRET_URLENC_JSON_KV_RE.sub(_sub_cfg_secret_urlenc_json_kv, masked)
    masked = _CFG_SECRET_QUOTED_KV_RE.sub(_sub_cfg_secret_quoted, masked)
    masked = _CFG_AUTH_HEADER_RE.sub(_sub_cfg_auth_header, masked)
    masked = _CFG_BEARER_RE.sub(_sub_cfg_bearer, masked)
    masked = _CFG_SID_RE.sub(_sub_cfg_sid_kv, masked)
    return _CFG_SECRET_KV_RE.sub(_sub_cfg_secret_kv, masked)


def _safe_fspath(value: Any) -> tuple[bool, Any]:
    try:
        return True, os.fspath(value)
    except Exception:
        return False, None


_MAX_CFG_KEY_NODES = 256
_MAX_CFG_KEY_CHARS = 4096
_MAX_CFG_TREE_NODES = 32_768
_MAX_CFG_TREE_DEPTH = 64


def _clip_cfg_str(text: str, limit: int = _MAX_CFG_KEY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _safe_cfg_exc_text(
    exc: BaseException,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_KEY_NODES]
    str_fn = getattr(type(exc), "__str__", BaseException.__str__)
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            return _clip_cfg_str(str(exc), 16_384)
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
                return _clip_cfg_str(res, 16_384)
            return _clip_cfg_str(
                _safe_cfg_key(a0, _depth=_depth + 1, _seen=seen, _budget=budget),
                _MAX_CFG_KEY_CHARS,
            )
        parts: list[str] = []
        for a in args[:8]:
            if budget[0] <= 0:
                parts.append("...")
                break
            if isinstance(a, str):
                parts.append(repr(a))
            else:
                parts.append(_safe_cfg_key(a, _depth=_depth + 1, _seen=seen, _budget=budget))
        if len(args) > 8:
            parts.append(f"...+{len(args) - 8}")
        return _clip_cfg_str("(" + ", ".join(parts) + ")", _MAX_CFG_KEY_CHARS)
    finally:
        seen.discard(vid)


def normalize_planner_mode(value: Any, *, strict: bool = False) -> str:
    """Validate and normalize `planner_mode` (`'priority'` default or `'ga_nn'`)."""
    if value is None:
        return "priority"
    if isinstance(value, os.PathLike):
        ok, fsp = _safe_fspath(value)
        if not ok:
            if strict:
                raise ValueError(
                    f"Unsupported planner_mode; expected one of {VALID_PLANNER_MODES}."
                )
            return "priority"
        value = fsp
        if value is None:
            return "priority"
    if isinstance(value, (bool, int, float, Mapping, list, tuple, set, frozenset)):
        if strict:
            raise ValueError(
                f"Unsupported planner_mode {type(value).__name__}; expected one of {VALID_PLANNER_MODES}."
            )
        return "priority"
    if isinstance(value, (bytes, bytearray)):
        value = _decode_cfg_bytes(value)
    try:
        mode = str(value).strip().lower()
    except Exception:
        if strict:
            raise ValueError(
                f"Unsupported planner_mode; expected one of {VALID_PLANNER_MODES}."
            )
        return "priority"
    if not mode:
        return "priority"
    if mode in VALID_PLANNER_MODES:
        return mode
    if strict:
        raise ValueError(
            f"Unsupported planner_mode {_redact_cfg_text(mode)!r}; expected one of {VALID_PLANNER_MODES}."
        )
    return "priority"

DEFAULT_SHIPYARD_TARGETS: dict[str, int] = {
    "small-fighter": 12,
    "small-transport": 6,
    "spy-probe": 10,
    "colony-ship": 1,
    "recycler": 4,
    "starcruiser": 6,
    "phoenix": 10,
    "transmitter": 6,
    "battleship": 8,
    "stealth-bomber": 4,
    "destroyer": 4,
}


@dataclass(frozen=True)
class StrategyConfig:
    """Empire strategy toggles and safety thresholds loaded from `config.yaml`."""

    playstyle: str = "fleeter"  # "fleeter" | "hybrid" | "miner"
    merchant_mode: bool = False
    auto_throttle_blast_furnace: bool = True
    allow_research: bool = True
    allow_shipyard: bool = True
    allow_multi_planet: bool = True
    allow_fleetsave: bool = False
    allow_galaxy_scan: bool = False
    allow_raids: bool = False
    allow_pvp_in_az: bool = False
    allow_colonize: bool = False
    forbid_planetary_solar: bool = False
    storage_warn_ratio: float = 0.85
    blast_pig_iron_max_share: float = 0.70
    combat_safety_margin: float = 1.4
    raid_min_loot_spice_eq: float = 1500.0
    fleet_save_fuel_reserve_spice: int = 500
    action_batch_interval_seconds: int = 120
    max_actions_per_hour: int = 60
    hot_planet_solar_temp_threshold: int = 40
    min_free_fields_warning: int = 20
    min_colony_fields: int = 300
    planner_mode: str = "priority"  # "priority" | "ga_nn"
    ga_population_size: int = 24
    ga_generations: int = 12
    ga_horizon_steps: int = 6
    ga_mutation_rate: float = 0.25
    ga_nn_weight: float = 0.35
    ga_seed: Optional[int] = None
    shipyard_targets: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SHIPYARD_TARGETS)
    )


def _strip_yaml_comment(line: str) -> str:
    in_quote: str | None = None
    idx = 0
    n = len(line)
    while idx < n:
        ch = line[idx]
        if in_quote == '"':
            if ch == "\\":
                idx += 2
                continue
            if ch == '"':
                in_quote = None
        elif in_quote == "'":
            if ch == "'":
                if idx + 1 < n and line[idx + 1] == "'":
                    idx += 2
                    continue
                in_quote = None
        else:
            if ch in ('"', "'") and (idx == 0 or line[idx - 1].isspace() or line[idx - 1] == ":"):
                in_quote = ch
            elif ch == "#":
                return line[:idx]
        idx += 1
    return line


_MAX_CFG_INT: int = 10**200


def _clamp_cfg_int(value: int) -> int:
    if value > _MAX_CFG_INT:
        return _MAX_CFG_INT
    if value < -_MAX_CFG_INT:
        return -_MAX_CFG_INT
    return value


def _safe_cfg_key_repr(
    val: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    if isinstance(val, str):
        return repr(val)
    if isinstance(val, int) and not isinstance(val, bool):
        return str(_clamp_cfg_int(val))
    if isinstance(val, (bytes, bytearray)):
        return repr(_decode_cfg_bytes(val))
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_KEY_NODES]
    if isinstance(val, os.PathLike):
        vid = id(val)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(val).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            ok, fsp = _safe_fspath(val)
            if not ok:
                return f"<{type(val).__name__}>"
            return _safe_cfg_key_repr(fsp, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(val, BaseException):
        return repr(_safe_cfg_exc_text(val, _depth=_depth, _seen=seen, _budget=budget))
    if isinstance(val, (tuple, frozenset, list, set, Mapping)):
        vid = id(val)
        if vid in seen:
            if isinstance(val, tuple):
                return "(...)"
            if isinstance(val, list):
                return "[...]"
            if isinstance(val, frozenset):
                return "frozenset({...})"
            return "{...}"
        if _depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(val, tuple):
                parts: list[str] = []
                for idx, el in enumerate(val):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(_safe_cfg_key_repr(el, _depth=_depth + 1, _seen=seen, _budget=budget))
                inner = ", ".join(parts)
                return _clip_cfg_str(f"({inner},)" if len(val) == 1 else f"({inner})")
            if isinstance(val, list):
                parts = []
                for idx, el in enumerate(val):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(_safe_cfg_key_repr(el, _depth=_depth + 1, _seen=seen, _budget=budget))
                return _clip_cfg_str("[" + ", ".join(parts) + "]")
            if isinstance(val, (set, frozenset)):
                parts = []
                for idx, el in enumerate(val):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(_safe_cfg_key_repr(el, _depth=_depth + 1, _seen=seen, _budget=budget))
                inner = ", ".join(sorted(parts))
                if isinstance(val, frozenset):
                    return _clip_cfg_str(f"frozenset({{{inner}}})")
                return _clip_cfg_str(f"{{{inner}}}" if parts else "set()")
            parts = []
            for idx, (mk, mv) in enumerate(val.items()):
                if idx >= 32 or budget[0] <= 0:
                    parts.append("...")
                    break
                ks = _safe_cfg_key_repr(mk, _depth=_depth + 1, _seen=seen, _budget=budget)
                vs = _safe_cfg_key_repr(mv, _depth=_depth + 1, _seen=seen, _budget=budget)
                parts.append(f"{ks}: {vs}")
            return _clip_cfg_str("{" + ", ".join(parts) + "}")
        finally:
            seen.discard(vid)
    try:
        return repr(val)
    except Exception:
        return f"<{type(val).__name__}>"


def _safe_cfg_key(
    key: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    if isinstance(key, str):
        return key
    if isinstance(key, (bytes, bytearray)):
        return _decode_cfg_bytes(key)
    if isinstance(key, int) and not isinstance(key, bool):
        return str(_clamp_cfg_int(key))
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_KEY_NODES]
    if isinstance(key, os.PathLike):
        vid = id(key)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(key).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            ok, fsp = _safe_fspath(key)
            if not ok:
                return f"<{type(key).__name__}>"
            return _safe_cfg_key(fsp, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(key, BaseException):
        return _safe_cfg_exc_text(key, _depth=_depth, _seen=seen, _budget=budget)
    if isinstance(key, (tuple, frozenset, list, set, Mapping)):
        vid = id(key)
        if vid in seen:
            if isinstance(key, tuple):
                return "(...)"
            if isinstance(key, list):
                return "[...]"
            if isinstance(key, frozenset):
                return "frozenset({...})"
            return "{...}"
        if _depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(key, tuple):
                parts: list[str] = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(_safe_cfg_key_repr(el, _depth=_depth + 1, _seen=seen, _budget=budget))
                inner = ", ".join(parts)
                return _clip_cfg_str(f"({inner},)" if len(key) == 1 else f"({inner})")
            if isinstance(key, list):
                parts = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(_safe_cfg_key_repr(el, _depth=_depth + 1, _seen=seen, _budget=budget))
                return _clip_cfg_str("[" + ", ".join(parts) + "]")
            if isinstance(key, (set, frozenset)):
                parts = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(_safe_cfg_key_repr(el, _depth=_depth + 1, _seen=seen, _budget=budget))
                inner = ", ".join(sorted(parts))
                if isinstance(key, frozenset):
                    return _clip_cfg_str(f"frozenset({{{inner}}})")
                return _clip_cfg_str(f"{{{inner}}}" if parts else "set()")
            parts = []
            for idx, (mk, mv) in enumerate(key.items()):
                if idx >= 32 or budget[0] <= 0:
                    parts.append("...")
                    break
                ks = _safe_cfg_key(mk, _depth=_depth + 1, _seen=seen, _budget=budget)
                vs = _safe_cfg_key(mv, _depth=_depth + 1, _seen=seen, _budget=budget)
                parts.append(f"{ks!r}: {vs!r}")
            return _clip_cfg_str("{" + ", ".join(parts) + "}")
        finally:
            seen.discard(vid)
    try:
        return str(key)
    except Exception:
        return f"<{type(key).__name__}>"


def _sanitize_cfg_json_tree(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool:
            return val
        if tv is str:
            return _redact_cfg_text(val)
        if tv is int:
            return _clamp_cfg_int(val)
        if tv is float:
            return val if math.isfinite(val) else None
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if not (is_dict or is_seq):
            if isinstance(val, bool):
                return bool(val)
            if isinstance(val, (bytes, bytearray)):
                return _redact_cfg_text(_decode_cfg_bytes(val))
            if isinstance(val, str):
                return _redact_cfg_text(str(val))
            if isinstance(val, int):
                return _clamp_cfg_int(int(val))
            if isinstance(val, float):
                fv = float(val)
                return fv if math.isfinite(fv) else None
            if isinstance(val, BaseException):
                return _redact_cfg_text(_safe_cfg_exc_text(val))
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset, os.PathLike)):
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_CFG_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if not (is_dict or is_seq) and isinstance(val, os.PathLike):
                    ok, fsp = _safe_fspath(val)
                    if not ok:
                        return f"<{type(val).__name__}>"
                    if isinstance(fsp, (bytes, bytearray)):
                        return _redact_cfg_text(_decode_cfg_bytes(fsp))
                    if isinstance(fsp, str):
                        return _redact_cfg_text(fsp)
                    if isinstance(fsp, int) and not isinstance(fsp, bool):
                        return _clamp_cfg_int(fsp)
                    if isinstance(fsp, os.PathLike):
                        return f"<{type(fsp).__name__}>"
                    return _walk(fsp, d + 1)
                if is_dict or isinstance(val, Mapping):
                    out: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        clean_k = (
                            _redact_cfg_text(k)
                            if type(k) is str
                            else _redact_cfg_text(_safe_cfg_key(k))
                        )
                        if _is_cfg_secret_dict_key(clean_k):
                            if v is None or v == "":
                                out[clean_k] = v
                            elif isinstance(v, (bytes, bytearray)) and len(v) == 0:
                                out[clean_k] = ""
                            else:
                                out[clean_k] = "REDACTED"
                        else:
                            out[clean_k] = _walk(v, d + 1)
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
        try:
            raw = str(val)
        except Exception:
            raw = f"<{type(val).__name__}>"
        return _redact_cfg_text(raw)

    return _walk(obj, _depth)


def _parse_scalar(raw: str) -> Any:
    val = raw.strip()
    if not val:
        return ""
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        try:
            decoded = json.loads(val)
            if isinstance(decoded, str):
                return decoded
        except (ValueError, TypeError):
            pass
        return val[1:-1]
    if val.startswith("'") and val.endswith("'") and len(val) >= 2:
        return val[1:-1].replace("''", "'")
    low = val.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none", "~"):
        return None
    if val.startswith("{") or val.startswith("["):
        try:
            return _sanitize_cfg_json_tree(json.loads(val))
        except (ValueError, TypeError):
            return val
    if not val.isascii():
        return val
    try:
        return int(val)
    except ValueError:
        pass
    s_digits = val[1:] if val[0] in ("+", "-") else val
    if s_digits.isascii() and s_digits.isdigit():
        return -_MAX_CFG_INT if val[0] == "-" else _MAX_CFG_INT
    try:
        parsed_f = float(val)
        return parsed_f if math.isfinite(parsed_f) else val
    except (ValueError, OverflowError):
        return val


def _split_yaml_key_value(line: str) -> Optional[tuple[str, str]]:
    in_quote: str | None = None
    idx = 0
    n = len(line)
    while idx < n:
        ch = line[idx]
        if in_quote == '"':
            if ch == "\\":
                idx += 2
                continue
            if ch == '"':
                in_quote = None
        elif in_quote == "'":
            if ch == "'":
                if idx + 1 < n and line[idx + 1] == "'":
                    idx += 2
                    continue
                in_quote = None
        else:
            if ch in ('"', "'") and (idx == 0 or line[idx - 1].isspace() or line[idx - 1] == ":"):
                in_quote = ch
            elif ch == ":":
                return line[:idx], line[idx + 1 :]
        idx += 1
    return None


def _parse_yaml_key(raw_key: str) -> str:
    key = raw_key.strip()
    if len(key) >= 2 and key.startswith('"') and key.endswith('"'):
        try:
            decoded = json.loads(key)
            if isinstance(decoded, str):
                return decoded
        except (ValueError, TypeError):
            pass
        return key[1:-1]
    if len(key) >= 2 and key.startswith("'") and key.endswith("'"):
        return key[1:-1].replace("''", "'")
    return key


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse flat and one-level nested `key: value` YAML without external dependencies."""
    root: dict[str, Any] = {}
    current_section: str | None = None
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = _strip_yaml_comment(raw_line).rstrip()
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        stripped = cleaned.strip()
        kv = _split_yaml_key_value(stripped)
        if kv is None:
            continue
        raw_key, raw_val = kv
        key = _parse_yaml_key(raw_key)
        val_part = raw_val.strip()
        if indent == 0:
            if not val_part:
                root[key] = {}
                current_section = key
            else:
                root[key] = _parse_scalar(val_part)
                current_section = None
        elif current_section is not None and isinstance(root.get(current_section), dict):
            root[current_section][key] = _parse_scalar(val_part)
    return root


def _parse_cfg_digit_string(raw: str) -> Optional[int]:
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
    return _clamp_cfg_int(-parsed if neg else parsed)


def _safe_cfg_int(value: Any, default: int) -> int:
    safe_default = _clamp_cfg_int(default)
    if isinstance(value, os.PathLike):
        ok, value = _safe_fspath(value)
        if not ok:
            return safe_default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset, bool)):
        return safe_default
    if isinstance(value, (bytes, bytearray)):
        value = _decode_cfg_bytes(value)
    if isinstance(value, str):
        if value.strip().lower() in ("", "null", "none", "~"):
            return safe_default
        parsed_s = _parse_cfg_digit_string(value)
        return parsed_s if parsed_s is not None else safe_default
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return safe_default
        return _clamp_cfg_int(int(value))
    except Exception:
        return safe_default


def _safe_cfg_optional_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    safe_default = (
        _clamp_cfg_int(default)
        if isinstance(default, int) and not isinstance(default, bool)
        else default
    )
    if isinstance(value, os.PathLike):
        ok, value = _safe_fspath(value)
        if not ok:
            return safe_default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset, bool)):
        return safe_default
    if isinstance(value, (bytes, bytearray)):
        value = _decode_cfg_bytes(value)
    if isinstance(value, str):
        if value.strip().lower() in ("", "null", "none", "~"):
            return safe_default
        parsed_s = _parse_cfg_digit_string(value)
        return parsed_s if parsed_s is not None else safe_default
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return safe_default
        return _clamp_cfg_int(int(value))
    except Exception:
        return safe_default


def _safe_cfg_float(value: Any, default: float) -> float:
    if isinstance(value, os.PathLike):
        ok, value = _safe_fspath(value)
        if not ok:
            return default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset, bool)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = _decode_cfg_bytes(value)
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isascii() or s.lower() in ("null", "none", "~"):
            return default
        value = s
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_cfg_bool(value: Any, default: bool) -> bool:
    if isinstance(value, os.PathLike):
        ok, value = _safe_fspath(value)
        if not ok:
            return default
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = _decode_cfg_bytes(value)
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
    except Exception:
        return default


_SHIPYARD_SLUG_ALIASES: dict[str, str] = {
    "s1": "small-transport",
    "s2": "large-transport",
    "s3": "transmitter",
    "s4": "small-fighter",
    "s5": "warship",
    "s6": "starcruiser",
    "s7": "phoenix",
    "s8": "colony-ship",
    "s9": "solar-cells",
    "s10": "spy-probe",
    "s11": "recycler",
    "s13": "battleship",
    "s14": "stealth-bomber",
    "s15": "destroyer",
    "s16": "imperial-starbase",
    "small transport": "small-transport",
    "large transport": "large-transport",
    "transmitter": "transmitter",
    "small fighter": "small-fighter",
    "warship": "warship",
    "starcruiser": "starcruiser",
    "phoenix": "phoenix",
    "colony ship": "colony-ship",
    "solar cells": "solar-cells",
    "solar cell": "solar-cells",
    "spy probe": "spy-probe",
    "recycler": "recycler",
    "battleship": "battleship",
    "stealth bomber": "stealth-bomber",
    "destroyer": "destroyer",
    "imperial starbase": "imperial-starbase",
    "ship[1]": "small-transport",
    "ship[2]": "large-transport",
    "ship[3]": "transmitter",
    "ship[4]": "small-fighter",
    "ship[5]": "warship",
    "ship[6]": "starcruiser",
    "ship[7]": "phoenix",
    "ship[8]": "colony-ship",
    "ship[9]": "solar-cells",
    "ship[10]": "spy-probe",
    "ship[11]": "recycler",
    "ship[13]": "battleship",
    "ship[14]": "stealth-bomber",
    "ship[15]": "destroyer",
    "ship[16]": "imperial-starbase",
}


def _canonicalize_shipyard_target_key(raw_key: str) -> str:
    stripped = raw_key.strip()
    if not stripped:
        return raw_key
    if stripped.isascii() and stripped.isdigit():
        return raw_key
    norm = " ".join(stripped.lower().replace("_", " ").replace("-", " ").split())
    return _SHIPYARD_SLUG_ALIASES.get(norm, raw_key)


def strategy_config_from_dict(data: Any) -> StrategyConfig:
    """Build a validated `StrategyConfig` from parsed mapping data."""
    if not isinstance(data, Mapping):
        return StrategyConfig()

    targets = dict(DEFAULT_SHIPYARD_TARGETS)
    raw_targets = data.get("shipyard_targets")
    if isinstance(raw_targets, Mapping):
        for k, v in raw_targets.items():
            key_str = _canonicalize_shipyard_target_key(_safe_cfg_key(k))
            targets[key_str] = max(0, _safe_cfg_int(v, targets.get(key_str, 0)))

    raw_playstyle = data.get("playstyle", "fleeter")
    if isinstance(raw_playstyle, os.PathLike):
        ok, fsp = _safe_fspath(raw_playstyle)
        raw_playstyle = fsp if ok else "fleeter"
        if isinstance(raw_playstyle, (bytes, bytearray)):
            raw_playstyle = _decode_cfg_bytes(raw_playstyle)
    if raw_playstyle is None or isinstance(
        raw_playstyle, (Mapping, list, tuple, set, frozenset, bytes, bytearray, int, float, bool)
    ):
        playstyle = "fleeter"
    else:
        try:
            playstyle = str(raw_playstyle).strip().lower() or "fleeter"
        except Exception:
            playstyle = "fleeter"

    raw_mode = data.get("planner_mode", "priority")
    if isinstance(raw_mode, os.PathLike):
        ok, fsp = _safe_fspath(raw_mode)
        raw_mode = fsp if ok else "priority"
    if isinstance(raw_mode, (Mapping, list, tuple, set, frozenset, int, float, bool)):
        raw_mode = "priority"
    elif isinstance(raw_mode, (bytes, bytearray)):
        raw_mode = _decode_cfg_bytes(raw_mode)

    return StrategyConfig(
        playstyle=playstyle,
        merchant_mode=_safe_cfg_bool(data.get("merchant_mode"), False),
        auto_throttle_blast_furnace=_safe_cfg_bool(data.get("auto_throttle_blast_furnace"), True),
        allow_research=_safe_cfg_bool(data.get("allow_research"), True),
        allow_shipyard=_safe_cfg_bool(data.get("allow_shipyard"), True),
        allow_multi_planet=_safe_cfg_bool(data.get("allow_multi_planet"), True),
        allow_fleetsave=_safe_cfg_bool(data.get("allow_fleetsave"), False),
        allow_galaxy_scan=_safe_cfg_bool(data.get("allow_galaxy_scan"), False),
        allow_raids=_safe_cfg_bool(data.get("allow_raids"), False),
        allow_pvp_in_az=_safe_cfg_bool(data.get("allow_pvp_in_az"), False),
        allow_colonize=_safe_cfg_bool(data.get("allow_colonize"), False),
        forbid_planetary_solar=_safe_cfg_bool(data.get("forbid_planetary_solar"), False),
        storage_warn_ratio=max(0.1, min(0.99, _safe_cfg_float(data.get("storage_warn_ratio"), 0.85))),
        blast_pig_iron_max_share=max(0.1, min(1.0, _safe_cfg_float(data.get("blast_pig_iron_max_share"), 0.70))),
        combat_safety_margin=max(1.0, _safe_cfg_float(data.get("combat_safety_margin"), 1.4)),
        raid_min_loot_spice_eq=max(0.0, _safe_cfg_float(data.get("raid_min_loot_spice_eq"), 1500.0)),
        fleet_save_fuel_reserve_spice=max(0, _safe_cfg_int(data.get("fleet_save_fuel_reserve_spice"), 500)),
        action_batch_interval_seconds=max(5, min(86400, _safe_cfg_int(data.get("action_batch_interval_seconds"), 120))),
        max_actions_per_hour=max(1, _safe_cfg_int(data.get("max_actions_per_hour"), 60)),
        hot_planet_solar_temp_threshold=_safe_cfg_int(data.get("hot_planet_solar_temp_threshold"), 40),
        min_free_fields_warning=max(1, _safe_cfg_int(data.get("min_free_fields_warning"), 20)),
        min_colony_fields=max(50, min(600, _safe_cfg_int(data.get("min_colony_fields"), 300))),
        planner_mode=normalize_planner_mode(raw_mode),
        ga_population_size=max(4, min(200, _safe_cfg_int(data.get("ga_population_size"), 24))),
        ga_generations=max(1, min(200, _safe_cfg_int(data.get("ga_generations"), 12))),
        ga_horizon_steps=max(1, min(24, _safe_cfg_int(data.get("ga_horizon_steps"), 6))),
        ga_mutation_rate=max(0.0, min(1.0, _safe_cfg_float(data.get("ga_mutation_rate"), 0.25))),
        ga_nn_weight=max(0.0, min(1.0, _safe_cfg_float(data.get("ga_nn_weight"), 0.35))),
        ga_seed=_safe_cfg_optional_int(data.get("ga_seed"), None),
        shipyard_targets=targets,
    )


def load_strategy_config(path: Path = Path("config.yaml")) -> StrategyConfig:
    """Load `StrategyConfig` from `path` (YAML or JSON), falling back to safe defaults."""
    if not path.exists():
        return StrategyConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return StrategyConfig()
    if not text.strip():
        return StrategyConfig()
    if path.suffix.lower() == ".json":
        try:
            parsed = _sanitize_cfg_json_tree(json.loads(text))
        except (ValueError, TypeError):
            return StrategyConfig()
    else:
        parsed = _parse_simple_yaml(text)
    if not isinstance(parsed, Mapping):
        return StrategyConfig()
    return strategy_config_from_dict(parsed)


_YAML_QUOTE_CHARS: frozenset[str] = frozenset("#:\"'\\{}[]&*!|>%@`,")
_YAML_LINE_BREAK_CHARS: frozenset[str] = frozenset("\x85\u2028\u2029")


def _needs_yaml_quoting(value: str) -> bool:
    if not value or value[0].isspace() or value[-1].isspace():
        return True
    if value.lower() in ("true", "false", "null", "none", "~"):
        return True
    if any(
        ord(ch) < 0x20
        or ord(ch) == 0x7F
        or ch in _YAML_QUOTE_CHARS
        or ch in _YAML_LINE_BREAK_CHARS
        for ch in value
    ):
        return True
    if _parse_cfg_digit_string(value) is not None:
        return True
    try:
        float(value)
        return True
    except (ValueError, OverflowError):
        return False


def _format_yaml_string(value: str) -> str:
    if _needs_yaml_quoting(value):
        return json.dumps(value, allow_nan=False)
    return value


def save_strategy_config(cfg: StrategyConfig, path: Path = Path("config.yaml")) -> Path:
    """Persist `StrategyConfig` cleanly in simple YAML format matching `load_strategy_config`."""
    storage_warn_ratio = max(0.1, min(0.99, _safe_cfg_float(cfg.storage_warn_ratio, 0.85)))
    blast_share = max(0.1, min(1.0, _safe_cfg_float(cfg.blast_pig_iron_max_share, 0.70)))
    combat_margin = max(1.0, _safe_cfg_float(cfg.combat_safety_margin, 1.4))
    raid_min_loot = max(0.0, _safe_cfg_float(cfg.raid_min_loot_spice_eq, 1500.0))
    fleet_reserve = max(0, _safe_cfg_int(cfg.fleet_save_fuel_reserve_spice, 500))
    batch_interval = max(5, min(86400, _safe_cfg_int(cfg.action_batch_interval_seconds, 120)))
    max_actions = max(1, _safe_cfg_int(cfg.max_actions_per_hour, 60))
    hot_temp = _safe_cfg_int(cfg.hot_planet_solar_temp_threshold, 40)
    min_fields = max(1, _safe_cfg_int(cfg.min_free_fields_warning, 20))
    min_colony = max(50, min(600, _safe_cfg_int(cfg.min_colony_fields, 300)))
    planner_mode = normalize_planner_mode(cfg.planner_mode)
    ga_pop = max(4, min(200, _safe_cfg_int(cfg.ga_population_size, 24)))
    ga_gen = max(1, min(200, _safe_cfg_int(cfg.ga_generations, 12)))
    ga_hor = max(1, min(24, _safe_cfg_int(cfg.ga_horizon_steps, 6)))
    ga_mut = max(0.0, min(1.0, _safe_cfg_float(cfg.ga_mutation_rate, 0.25)))
    ga_nn_w = max(0.0, min(1.0, _safe_cfg_float(cfg.ga_nn_weight, 0.35)))
    ga_seed = _safe_cfg_optional_int(cfg.ga_seed, None)
    raw_playstyle = cfg.playstyle
    if isinstance(raw_playstyle, os.PathLike):
        ok, fsp = _safe_fspath(raw_playstyle)
        raw_playstyle = fsp if ok else "fleeter"
    if raw_playstyle is None or isinstance(
        raw_playstyle, (Mapping, list, tuple, set, frozenset, int, float, bool)
    ):
        playstyle = "fleeter"
    else:
        if isinstance(raw_playstyle, (bytes, bytearray)):
            raw_playstyle = _decode_cfg_bytes(raw_playstyle)
        try:
            playstyle = str(raw_playstyle).strip().lower() or "fleeter"
        except Exception:
            playstyle = "fleeter"

    lines = [
        "# SpaceInvasion Autoplayer runtime strategy configuration",
        f"playstyle: {_format_yaml_string(playstyle)}",
        f"merchant_mode: {'true' if _safe_cfg_bool(cfg.merchant_mode, False) else 'false'}",
        f"auto_throttle_blast_furnace: {'true' if _safe_cfg_bool(cfg.auto_throttle_blast_furnace, True) else 'false'}",
        f"allow_research: {'true' if _safe_cfg_bool(cfg.allow_research, True) else 'false'}",
        f"allow_shipyard: {'true' if _safe_cfg_bool(cfg.allow_shipyard, True) else 'false'}",
        f"allow_multi_planet: {'true' if _safe_cfg_bool(cfg.allow_multi_planet, True) else 'false'}",
        f"allow_fleetsave: {'true' if _safe_cfg_bool(cfg.allow_fleetsave, False) else 'false'}",
        f"allow_galaxy_scan: {'true' if _safe_cfg_bool(cfg.allow_galaxy_scan, False) else 'false'}",
        f"allow_raids: {'true' if _safe_cfg_bool(cfg.allow_raids, False) else 'false'}",
        f"allow_pvp_in_az: {'true' if _safe_cfg_bool(cfg.allow_pvp_in_az, False) else 'false'}",
        f"allow_colonize: {'true' if _safe_cfg_bool(cfg.allow_colonize, False) else 'false'}",
        f"forbid_planetary_solar: {'true' if _safe_cfg_bool(cfg.forbid_planetary_solar, False) else 'false'}",
        f"storage_warn_ratio: {storage_warn_ratio}",
        f"blast_pig_iron_max_share: {blast_share}",
        f"combat_safety_margin: {combat_margin}",
        f"raid_min_loot_spice_eq: {raid_min_loot}",
        f"fleet_save_fuel_reserve_spice: {fleet_reserve}",
        f"action_batch_interval_seconds: {batch_interval}",
        f"max_actions_per_hour: {max_actions}",
        f"hot_planet_solar_temp_threshold: {hot_temp}",
        f"min_free_fields_warning: {min_fields}",
        f"min_colony_fields: {min_colony}",
        f"planner_mode: {planner_mode}",
        f"ga_population_size: {ga_pop}",
        f"ga_generations: {ga_gen}",
        f"ga_horizon_steps: {ga_hor}",
        f"ga_mutation_rate: {ga_mut}",
        f"ga_nn_weight: {ga_nn_w}",
        f"ga_seed: {'null' if ga_seed is None else ga_seed}",
        "shipyard_targets:",
    ]
    raw_targets = cfg.shipyard_targets if isinstance(cfg.shipyard_targets, Mapping) else DEFAULT_SHIPYARD_TARGETS
    canonical_targets: dict[str, int] = {}
    for slug, cnt in raw_targets.items():
        safe_slug = _safe_cfg_key(slug)
        if not safe_slug:
            continue
        canon_slug = _canonicalize_shipyard_target_key(safe_slug)
        canonical_targets[canon_slug] = max(0, _safe_cfg_int(cnt, 0))
    for canon_slug, val in canonical_targets.items():
        lines.append(f"  {_format_yaml_string(canon_slug)}: {val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
