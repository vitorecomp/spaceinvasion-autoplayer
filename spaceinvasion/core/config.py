"""Non-secret strategy profile loader (`config.json` / pure validation) for SpaceInvasion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
from typing import Any, Optional

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

VALID_PLANNER_MODES: tuple[str, ...] = ("priority", "ai_planner")


def _decode_cfg_bytes(raw: bytes | bytearray) -> str:
    return bytes(raw).decode("utf-8", errors="replace")


def _safe_fspath(value: Any) -> tuple[bool, Any]:
    try:
        return True, os.fspath(value)
    except Exception:
        return False, None


_MAX_CFG_KEY_NODES = 256
_MAX_CFG_KEY_CHARS = 4096
_MAX_CFG_TREE_NODES = 32_768
_MAX_CFG_TREE_DEPTH = 64


def _safe_cfg_exc_text(
    exc: BaseException,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_CFG_KEY_NODES]
    return _clip_cfg_str(
        _redaction_safe_text(exc, f"<{type(exc).__name__}>", _depth, seen, budget),
        16_384,
    )


def normalize_planner_mode(value: Any, *, strict: bool = False) -> str:
    """Validate and normalize `planner_mode` (`'priority'` default or `'ai_planner'`; legacy `'ga_nn'` maps to `'priority'`)."""
    if value is None:
        return "priority"
    if isinstance(value, os.PathLike):
        ok, fsp = _safe_fspath(value)
        if not ok:
            if strict:
                raise ValueError(f"Unsupported planner_mode; expected one of {VALID_PLANNER_MODES}.")
            return "priority"
        value = fsp
        if value is None:
            return "priority"
    if isinstance(value, (bool, int, float, Mapping, list, tuple, set, frozenset)):
        if strict:
            raise ValueError(f"Unsupported planner_mode {type(value).__name__}; expected one of {VALID_PLANNER_MODES}.")
        return "priority"
    if isinstance(value, (bytes, bytearray)):
        value = _decode_cfg_bytes(value)
    try:
        mode = str(value).strip().lower()
    except Exception:
        if strict:
            raise ValueError(f"Unsupported planner_mode; expected one of {VALID_PLANNER_MODES}.")
        return "priority"
    if not mode or mode == "ga_nn":
        return "priority"
    if mode in VALID_PLANNER_MODES:
        return mode
    if strict:
        raise ValueError(f"Unsupported planner_mode {_redact_cfg_text(mode)!r}; expected one of {VALID_PLANNER_MODES}.")
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
    shipyard_targets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SHIPYARD_TARGETS))
    fleet_research_targets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_FLEET_RESEARCH_TARGETS))


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
                        clean_k = _redact_cfg_text(k) if type(k) is str else _redact_cfg_text(_safe_cfg_key(k))
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
    safe_default = _clamp_cfg_int(default) if isinstance(default, int) and not isinstance(default, bool) else default
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
        page_view_limit_per_minute=max(6, min(600, _safe_cfg_int(data.get("page_view_limit_per_minute"), 60))),
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
        galaxy_scan_block_other_actions=_safe_cfg_bool(data.get("galaxy_scan_block_other_actions"), True),
        galaxy_scan_near_weight=max(0.0, min(20.0, _safe_cfg_float(data.get("galaxy_scan_near_weight"), 2.0))),
        galaxy_scan_attack_weight=max(0.0, min(20.0, _safe_cfg_float(data.get("galaxy_scan_attack_weight"), 3.0))),
        reserved_spy_slots=max(0, min(10, _safe_cfg_int(data.get("reserved_spy_slots"), 1))),
        spy_reserved_slots_only=_safe_cfg_bool(data.get("spy_reserved_slots_only"), False),
        spy_dispatch_planet_id=max(0, _safe_cfg_int(data.get("spy_dispatch_planet_id"), 0)),
        planner_mode=normalize_planner_mode(raw_mode),
        ai_resource_spare_pct=max(0.0, min(100.0, _safe_cfg_float(data.get("ai_resource_spare_pct"), 10.0))),
        ai_adaptive_execution=_safe_cfg_bool(data.get("ai_adaptive_execution"), False),
        ga_population_size=max(4, min(200, _safe_cfg_int(data.get("ga_population_size"), 24))),
        ga_generations=max(1, min(200, _safe_cfg_int(data.get("ga_generations"), 12))),
        ga_mutation_rate=max(0.0, min(1.0, _safe_cfg_float(data.get("ga_mutation_rate"), 0.25))),
        shipyard_targets=targets,
        fleet_research_targets=fleet_research_targets,
    )


def _serialize_strategy_config_json(cfg: StrategyConfig, *, indent: Optional[int] = None) -> str:
    clean_cfg = strategy_config_from_dict(asdict(cfg))
    cfg_dict = _sanitize_cfg_json_tree(asdict(clean_cfg))
    return json.dumps(cfg_dict, indent=indent, sort_keys=True, allow_nan=False)


def load_strategy_config(path: Path | str) -> StrategyConfig:
    """Load ``StrategyConfig`` from an explicit JSON file path."""
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        raise ValueError("Use spaceinvasion.db.config_store.load_strategy_config for PostgreSQL DSNs.")
    if isinstance(path, str):
        path = Path(path)
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


def save_strategy_config(cfg: StrategyConfig, path: Path | str) -> Path:
    """Persist ``StrategyConfig`` to an explicit JSON file path."""
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        raise ValueError("Use spaceinvasion.db.config_store.save_strategy_config for PostgreSQL DSNs.")
    if not isinstance(path, Path):
        ok_fsp, fsp_val = _safe_fspath(path)
        if not ok_fsp:
            raise TypeError("Expected Path, str, or os.PathLike for config path.")
        path = Path(os.fsdecode(fsp_val))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_strategy_config_json(cfg, indent=2) + "\n", encoding="utf-8")
    return path
