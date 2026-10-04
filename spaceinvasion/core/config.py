"""Non-secret strategy profile loader (`config.json` / database) for SpaceInvasion."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Optional
from urllib.parse import quote

DEFAULT_CONFIG_DB_PATH: Path = Path("state") / "spaceinvasion.db"

VALID_PLANNER_MODES: tuple[str, ...] = ("priority", "ai_planner")

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
    """Validate and normalize `planner_mode` (`'priority'` default or `'ai_planner'`; legacy `'ga_nn'` maps to `'priority'`)."""
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
    if not mode or mode == "ga_nn":
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

FLEET_RESEARCH_KEYS: tuple[str, ...] = (
    "r3",
    "r4",
    "r5",
    "r6",
    "r7",
    "r8",
    "r9",
    "r10",
    "r11",
    "r12",
    "r13",
    "r14",
    "r15",
)

DEFAULT_FLEET_RESEARCH_TARGETS: dict[str, int] = {}


@dataclass(frozen=True)
class StrategyConfig:
    """Empire strategy toggles and safety thresholds loaded from database or JSON profile."""

    playstyle: str = "fleeter"  # "fleeter" | "hybrid" | "miner"
    merchant_mode: bool = False
    auto_throttle_blast_furnace: bool = True
    allow_research: bool = True
    allow_fleet_research: bool = True
    allow_shipyard: bool = True
    allow_multi_planet: bool = True
    allow_fleetsave: bool = False
    allow_galaxy_scan: bool = False
    allow_raids: bool = False
    allow_pvp_in_az: bool = False
    allow_colonize: bool = False
    allow_transport: bool = False
    allow_transmitter: bool = False
    forbid_planetary_solar: bool = False
    storage_warn_ratio: float = 0.85
    blast_pig_iron_max_share: float = 0.70
    combat_safety_margin: float = 1.4
    raid_min_loot_spice_eq: float = 1500.0
    attack_min_res_per_hour: float = 0.0
    recycle_min_res_per_hour: float = 0.0
    fleet_save_fuel_reserve_spice: int = 500
    action_batch_interval_seconds: int = 120
    max_actions_per_hour: int = 60
    page_view_limit_per_minute: int = 60
    hot_planet_solar_temp_threshold: int = 40
    min_free_fields_warning: int = 20
    min_colony_fields: int = 300
    max_planets: int = 11
    galaxy_sweep_interval_seconds: int = 270
    galaxy_sweep_pages: int = 100
    galaxy_scan_click_delay_seconds: float = 1.5
    galaxy_scan_click_jitter_seconds: float = 0.6
    galaxy_scan_explore_first: bool = True
    galaxy_scan_refresh_pct: int = 0
    galaxy_scan_all_galaxies: bool = False
    galaxy_scan_block_other_actions: bool = True
    galaxy_scan_near_weight: float = 2.0
    galaxy_scan_attack_weight: float = 3.0
    reserved_spy_slots: int = 1
    spy_reserved_slots_only: bool = False
    spy_dispatch_planet_id: int = 0
    planner_mode: str = "priority"  # "priority" | "ai_planner"
    ai_resource_spare_pct: float = 10.0
    ai_adaptive_execution: bool = False
    ga_population_size: int = 24
    ga_generations: int = 12
    ga_mutation_rate: float = 0.25
    shipyard_targets: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SHIPYARD_TARGETS)
    )
    fleet_research_targets: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_FLEET_RESEARCH_TARGETS)
    )


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


_FLEET_RESEARCH_KEY_ALIASES: dict[str, str] = {
    "r1": "r1",
    "r2": "r2",
    "r3": "r3",
    "r4": "r4",
    "r5": "r5",
    "r6": "r6",
    "r7": "r7",
    "r8": "r8",
    "r9": "r9",
    "r10": "r10",
    "r11": "r11",
    "r12": "r12",
    "r13": "r13",
    "r14": "r14",
    "r15": "r15",
    "research[1]": "r1",
    "research[2]": "r2",
    "research[3]": "r3",
    "research[4]": "r4",
    "research[5]": "r5",
    "research[6]": "r6",
    "research[7]": "r7",
    "research[8]": "r8",
    "research[9]": "r9",
    "research[10]": "r10",
    "research[11]": "r11",
    "research[12]": "r12",
    "research[13]": "r13",
    "research[14]": "r14",
    "research[15]": "r15",
    "mining technology": "r1",
    "energy technology": "r2",
    "engine research": "r3",
    "rocket engine": "r4",
    "diffusion engine": "r5",
    "warp engine": "r6",
    "spy technology": "r7",
    "weapons technology": "r8",
    "shield technology": "r9",
    "defense systems": "r10",
    "targeting systems": "r11",
    "particle analyzer": "r12",
    "graviton technology": "r13",
    "teleportation": "r14",
    "black hole research": "r15",
}


def _canonicalize_fleet_research_key(raw_key: str) -> str:
    stripped = raw_key.strip()
    if not stripped:
        return raw_key
    if stripped.isascii() and stripped.isdigit():
        return raw_key
    norm = " ".join(stripped.lower().replace("_", " ").replace("-", " ").split())
    return _FLEET_RESEARCH_KEY_ALIASES.get(norm, raw_key)


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

    fleet_research_targets: dict[str, int] = dict(DEFAULT_FLEET_RESEARCH_TARGETS)
    raw_fr_targets = data.get("fleet_research_targets")
    if isinstance(raw_fr_targets, Mapping):
        for k, v in raw_fr_targets.items():
            key_str = _canonicalize_fleet_research_key(_safe_cfg_key(k))
            if not key_str:
                continue
            lvl = max(0, min(100, _safe_cfg_int(v, 0)))
            if lvl > 0:
                fleet_research_targets[key_str] = lvl
            else:
                fleet_research_targets.pop(key_str, None)

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
        allow_fleet_research=_safe_cfg_bool(data.get("allow_fleet_research"), True),
        allow_shipyard=_safe_cfg_bool(data.get("allow_shipyard"), True),
        allow_multi_planet=_safe_cfg_bool(data.get("allow_multi_planet"), True),
        allow_fleetsave=_safe_cfg_bool(data.get("allow_fleetsave"), False),
        allow_galaxy_scan=_safe_cfg_bool(data.get("allow_galaxy_scan"), False),
        allow_raids=_safe_cfg_bool(data.get("allow_raids"), False),
        allow_pvp_in_az=_safe_cfg_bool(data.get("allow_pvp_in_az"), False),
        allow_colonize=_safe_cfg_bool(data.get("allow_colonize"), False),
        allow_transport=_safe_cfg_bool(data.get("allow_transport"), False),
        allow_transmitter=_safe_cfg_bool(data.get("allow_transmitter"), False),
        forbid_planetary_solar=_safe_cfg_bool(data.get("forbid_planetary_solar"), False),
        storage_warn_ratio=max(0.1, min(0.99, _safe_cfg_float(data.get("storage_warn_ratio"), 0.85))),
        blast_pig_iron_max_share=max(0.1, min(1.0, _safe_cfg_float(data.get("blast_pig_iron_max_share"), 0.70))),
        combat_safety_margin=max(1.0, _safe_cfg_float(data.get("combat_safety_margin"), 1.4)),
        raid_min_loot_spice_eq=max(0.0, _safe_cfg_float(data.get("raid_min_loot_spice_eq"), 1500.0)),
        attack_min_res_per_hour=max(0.0, _safe_cfg_float(data.get("attack_min_res_per_hour"), 0.0)),
        recycle_min_res_per_hour=max(0.0, _safe_cfg_float(data.get("recycle_min_res_per_hour"), 0.0)),
        fleet_save_fuel_reserve_spice=max(0, _safe_cfg_int(data.get("fleet_save_fuel_reserve_spice"), 500)),
        action_batch_interval_seconds=max(5, min(86400, _safe_cfg_int(data.get("action_batch_interval_seconds"), 120))),
        max_actions_per_hour=max(1, _safe_cfg_int(data.get("max_actions_per_hour"), 60)),
        page_view_limit_per_minute=max(
            6, min(600, _safe_cfg_int(data.get("page_view_limit_per_minute"), 60))
        ),
        hot_planet_solar_temp_threshold=_safe_cfg_int(data.get("hot_planet_solar_temp_threshold"), 40),
        min_free_fields_warning=max(1, _safe_cfg_int(data.get("min_free_fields_warning"), 20)),
        min_colony_fields=max(50, min(600, _safe_cfg_int(data.get("min_colony_fields"), 300))),
        max_planets=max(1, min(50, _safe_cfg_int(data.get("max_planets"), 11))),
        galaxy_sweep_interval_seconds=max(
            30, min(86400, _safe_cfg_int(data.get("galaxy_sweep_interval_seconds"), 270))
        ),
        galaxy_sweep_pages=max(1, min(400, _safe_cfg_int(data.get("galaxy_sweep_pages"), 100))),
        galaxy_scan_click_delay_seconds=max(
            0.2, min(60.0, _safe_cfg_float(data.get("galaxy_scan_click_delay_seconds"), 1.5))
        ),
        galaxy_scan_click_jitter_seconds=max(
            0.0, min(30.0, _safe_cfg_float(data.get("galaxy_scan_click_jitter_seconds"), 0.6))
        ),
        galaxy_scan_explore_first=_safe_cfg_bool(data.get("galaxy_scan_explore_first"), True),
        galaxy_scan_refresh_pct=max(0, min(100, _safe_cfg_int(data.get("galaxy_scan_refresh_pct"), 0))),
        galaxy_scan_all_galaxies=_safe_cfg_bool(data.get("galaxy_scan_all_galaxies"), False),
        galaxy_scan_block_other_actions=_safe_cfg_bool(
            data.get("galaxy_scan_block_other_actions"), True
        ),
        galaxy_scan_near_weight=max(
            0.0, min(20.0, _safe_cfg_float(data.get("galaxy_scan_near_weight"), 2.0))
        ),
        galaxy_scan_attack_weight=max(
            0.0, min(20.0, _safe_cfg_float(data.get("galaxy_scan_attack_weight"), 3.0))
        ),
        reserved_spy_slots=max(0, min(10, _safe_cfg_int(data.get("reserved_spy_slots"), 1))),
        spy_reserved_slots_only=_safe_cfg_bool(data.get("spy_reserved_slots_only"), False),
        spy_dispatch_planet_id=max(0, _safe_cfg_int(data.get("spy_dispatch_planet_id"), 0)),
        planner_mode=normalize_planner_mode(raw_mode),
        ai_resource_spare_pct=max(
            0.0, min(100.0, _safe_cfg_float(data.get("ai_resource_spare_pct"), 10.0))
        ),
        ai_adaptive_execution=_safe_cfg_bool(data.get("ai_adaptive_execution"), False),
        ga_population_size=max(4, min(200, _safe_cfg_int(data.get("ga_population_size"), 24))),
        ga_generations=max(1, min(200, _safe_cfg_int(data.get("ga_generations"), 12))),
        ga_mutation_rate=max(0.0, min(1.0, _safe_cfg_float(data.get("ga_mutation_rate"), 0.25))),
        shipyard_targets=targets,
        fleet_research_targets=fleet_research_targets,
    )


def _is_sqlite_config_path(path: Path) -> bool:
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return True
    if not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def load_strategy_config_from_sqlite(db_path: Path = DEFAULT_CONFIG_DB_PATH) -> Optional[StrategyConfig]:
    """Load persisted `StrategyConfig` from SQLite (`strategy_config_store` or `meta['strategy_config']`)."""
    if not db_path.is_file():
        return None
    raw_json: Optional[str] = None
    try:
        with tempfile.TemporaryDirectory(prefix="si_cfg_ro_") as tmp_dir:
            tmp_db = Path(tmp_dir) / db_path.name
            shutil.copy2(db_path, tmp_db)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(f"{db_path}{suffix}")
                if sidecar.is_file():
                    with contextlib.suppress(OSError):
                        shutil.copy2(sidecar, Path(f"{tmp_db}{suffix}"))
            with contextlib.closing(sqlite3.connect(str(tmp_db), timeout=5.0)) as conn:
                has_store = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_config_store' LIMIT 1"
                ).fetchone()
                if has_store is not None:
                    row = conn.execute(
                        "SELECT config_json FROM strategy_config_store WHERE id = 1"
                    ).fetchone()
                    if row is not None and isinstance(row[0], str) and row[0].strip():
                        raw_json = row[0]
                if raw_json is None:
                    has_meta = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta' LIMIT 1"
                    ).fetchone()
                    if has_meta is not None:
                        mrow = conn.execute(
                            "SELECT value FROM meta WHERE key = 'strategy_config'"
                        ).fetchone()
                        if mrow is not None and isinstance(mrow[0], str) and mrow[0].strip():
                            raw_json = mrow[0]
    except (OSError, sqlite3.Error):
        return None
    if not raw_json:
        return None
    try:
        parsed = _sanitize_cfg_json_tree(json.loads(raw_json))
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    return strategy_config_from_dict(parsed)


def _serialize_strategy_config_json(cfg: StrategyConfig, *, indent: Optional[int] = None) -> str:
    clean_cfg = strategy_config_from_dict(asdict(cfg))
    cfg_dict = _sanitize_cfg_json_tree(asdict(clean_cfg))
    return json.dumps(cfg_dict, indent=indent, sort_keys=True, allow_nan=False)


def save_strategy_config_to_sqlite(
    cfg: StrategyConfig,
    db_path: Path = DEFAULT_CONFIG_DB_PATH,
    *,
    source: str = "sqlite",
) -> Path:
    """Persist `StrategyConfig` into SQLite (`strategy_config_store`, `meta`, and `config_history`)."""
    cfg_json = _serialize_strategy_config_json(cfg)
    now_ts = round(time.time(), 3)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=10.0)) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_config_store (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                config_json TEXT NOT NULL,
                updated_ts REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'sqlite'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_config_store(id, config_json, updated_ts, source)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                config_json = excluded.config_json,
                updated_ts = excluded.updated_ts,
                source = excluded.source
            """,
            (cfg_json, now_ts, str(source or "sqlite")[:32]),
        )
        has_meta = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta' LIMIT 1"
        ).fetchone()
        if has_meta is not None:
            conn.execute(
                """
                INSERT INTO meta(key, value, updated_ts) VALUES ('strategy_config', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_ts = excluded.updated_ts
                """,
                (cfg_json, now_ts),
            )
        has_hist = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_history' LIMIT 1"
        ).fetchone()
        if has_hist is not None:
            last_row = conn.execute(
                "SELECT config_json FROM config_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last_row is None or last_row[0] != cfg_json:
                conn.execute(
                    "INSERT INTO config_history(ts, config_json) VALUES (?, ?)",
                    (now_ts, cfg_json),
                )
        conn.commit()
    return db_path


def load_strategy_config_from_postgres(dsn: str) -> Optional[StrategyConfig]:
    """Load persisted `StrategyConfig` from PostgreSQL (`strategy_config_store` or `meta['strategy_config']`)."""
    raw_json: Optional[str] = None
    try:
        import psycopg

        with psycopg.connect(str(dsn).strip(), connect_timeout=3) as conn:
            has_store = conn.execute(
                "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'strategy_config_store' LIMIT 1"
            ).fetchone()
            if has_store is not None:
                row = conn.execute("SELECT config_json FROM strategy_config_store WHERE id = 1").fetchone()
                if row is not None and isinstance(row[0], str) and row[0].strip():
                    raw_json = row[0]
            if raw_json is None:
                has_meta = conn.execute(
                    "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'meta' LIMIT 1"
                ).fetchone()
                if has_meta is not None:
                    mrow = conn.execute("SELECT value FROM meta WHERE key = 'strategy_config'").fetchone()
                    if mrow is not None and isinstance(mrow[0], str) and mrow[0].strip():
                        raw_json = mrow[0]
    except Exception:
        return None
    if not raw_json:
        return None
    try:
        parsed = _sanitize_cfg_json_tree(json.loads(raw_json))
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    return strategy_config_from_dict(parsed)


def save_strategy_config_to_postgres(
    cfg: StrategyConfig,
    dsn: str,
    *,
    source: str = "postgres",
) -> str:
    """Persist `StrategyConfig` into PostgreSQL (`strategy_config_store`, `meta`, and `config_history`)."""
    import psycopg

    cfg_json = _serialize_strategy_config_json(cfg)
    now_ts = round(time.time(), 3)
    target_dsn = str(dsn).strip()
    with psycopg.connect(target_dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_config_store (
                id BIGINT PRIMARY KEY CHECK (id = 1),
                config_json TEXT NOT NULL,
                updated_ts DOUBLE PRECISION NOT NULL,
                source TEXT NOT NULL DEFAULT 'postgres'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_config_store(id, config_json, updated_ts, source)
            VALUES (1, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                config_json = EXCLUDED.config_json,
                updated_ts = EXCLUDED.updated_ts,
                source = EXCLUDED.source
            """,
            (cfg_json, now_ts, str(source or "postgres")[:32]),
        )
        has_meta = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'meta' LIMIT 1"
        ).fetchone()
        if has_meta is not None:
            conn.execute(
                """
                INSERT INTO meta(key, value, updated_ts) VALUES ('strategy_config', %s, %s)
                ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value, updated_ts = EXCLUDED.updated_ts
                """,
                (cfg_json, now_ts),
            )
        has_hist = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'config_history' LIMIT 1"
        ).fetchone()
        if has_hist is not None:
            last_row = conn.execute("SELECT config_json FROM config_history ORDER BY id DESC LIMIT 1").fetchone()
            if last_row is None or last_row[0] != cfg_json:
                conn.execute("INSERT INTO config_history(ts, config_json) VALUES (%s, %s)", (now_ts, cfg_json))
        conn.commit()
    return target_dsn


def _resolve_default_config_target() -> tuple[str, str | Path]:
    """Pure-stdlib resolver for default `StrategyConfig` storage (`postgres` in Docker, `sqlite` on host)."""
    env = os.environ
    in_container = str(env.get("SPACEINVASION_CONTAINER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    env_url = str(env.get("SPACEINVASION_DB_URL") or "").strip()
    env_backend = str(env.get("SPACEINVASION_DB_BACKEND") or "").strip().lower()

    def _pg_dsn() -> str:
        if env_url.lower().startswith(("postgresql://", "postgres://")):
            return env_url
        host = str(env.get("SPACEINVASION_PG_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        try:
            port = max(1, min(65535, int(str(env.get("SPACEINVASION_PG_PORT") or "5433").strip())))
        except ValueError:
            port = 5433
        dbn = str(env.get("SPACEINVASION_PG_DB") or "spaceinvasion").strip() or "spaceinvasion"
        usr = str(env.get("SPACEINVASION_PG_USER") or "spaceinvasion").strip() or "spaceinvasion"
        pw = str(env.get("SPACEINVASION_PG_PASSWORD") or "spaceinvasion")
        return f"postgresql://{quote(usr, safe='')}:{quote(pw, safe='')}@{host}:{port}/{quote(dbn, safe='')}"

    if (
        in_container
        or env_backend in ("pg", "postgres", "postgresql")
        or env_url.lower().startswith(("postgresql://", "postgres://"))
    ):
        return ("postgres", _pg_dsn())
    return ("sqlite", DEFAULT_CONFIG_DB_PATH)


def load_strategy_config(path: Optional[Path | str] = None) -> StrategyConfig:
    """Load `StrategyConfig` from active database (`postgres` in Docker, `sqlite` on host), or explicit test path."""
    if path is None:
        backend, target = _resolve_default_config_target()
        if backend == "postgres":
            from_pg = load_strategy_config_from_postgres(str(target))
            return from_pg if from_pg is not None else StrategyConfig()
        from_db = load_strategy_config_from_sqlite(Path(target))
        return from_db if from_db is not None else StrategyConfig()
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        from_pg = load_strategy_config_from_postgres(path)
        return from_pg if from_pg is not None else StrategyConfig()
    if isinstance(path, str):
        path = Path(path)
    if _is_sqlite_config_path(path):
        from_db = load_strategy_config_from_sqlite(path)
        return from_db if from_db is not None else StrategyConfig()
    if not path.exists():
        return StrategyConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return StrategyConfig()
    if not text.strip():
        return StrategyConfig()
    try:
        parsed = _sanitize_cfg_json_tree(json.loads(text))
    except (ValueError, TypeError):
        return StrategyConfig()
    if not isinstance(parsed, Mapping):
        return StrategyConfig()
    return strategy_config_from_dict(parsed)


def save_strategy_config(cfg: StrategyConfig, path: Optional[Path | str] = None) -> Path | str:
    """Persist `StrategyConfig` to active database (`postgres` in Docker, `sqlite` on host), or explicit JSON test path."""
    if path is None:
        backend, target = _resolve_default_config_target()
        if backend == "postgres":
            return save_strategy_config_to_postgres(cfg, str(target))
        return save_strategy_config_to_sqlite(cfg, Path(target))
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        return save_strategy_config_to_postgres(cfg, path)
    if not isinstance(path, Path):
        ok_fsp, fsp_val = _safe_fspath(path)
        if not ok_fsp:
            raise TypeError("Expected Path, str, or os.PathLike for config path.")
        path = Path(os.fsdecode(fsp_val))
    if _is_sqlite_config_path(path):
        return save_strategy_config_to_sqlite(cfg, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_strategy_config_json(cfg, indent=2) + "\n", encoding="utf-8")
    return path
