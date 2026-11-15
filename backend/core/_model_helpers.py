"""Low-level pure helpers and domain constants for `backend.core.model`."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import math
import os
from typing import Protocol, runtime_checkable

from backend.core.coercion import (
    coerce_int,
    fmt_compact_int,
    safe_key,
)

BUILDING_ID_BY_NAME: dict[str, int] = {
    "pig iron mine": 1,
    "blast furnace": 2,
    "kryptonite mine": 3,
    "spice mine": 4,
    "fusion plant": 5,
    "thermosolar plant": 6,
    "development center": 7,
    "research lab": 8,
    "weapons factory": 9,
    "fleet base": 10,
    "pig iron warehouse": 11,
    "metal warehouse": 12,
    "kryptonite warehouse": 13,
    "spice warehouse": 14,
    "particle cannon": 15,
    "particle shield": 16,
    "microsystem accelerator": 17,
    "teleporter": 18,
    "galaxy scanner": 19,
    "tunnel system": 20,
    "quantum array": 21,
}

VALID_BUILDING_IDS: frozenset[int] = frozenset(range(1, 22))
ASTEROID_ALLOWED_BUILDINGS: frozenset[int] = frozenset({7, 9, 10, 17, 19})
RAID_COLONY_FORBIDDEN_BUILDINGS: frozenset[int] = frozenset({1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14, 19, 20, 21})

_FSPATH_ERRS = (
    TypeError,
    ValueError,
    OSError,
    RuntimeError,
    AttributeError,
    LookupError,
    OverflowError,
    RecursionError,
)


@runtime_checkable
class _HasPlanetType(Protocol):
    planet_type: object


def safe_ratio(num: object, den: object, *, cap: float = 1e200) -> float:
    """Compute ``num / den`` safely even for huge integers exceeding IEEE-754 float range."""
    if isinstance(num, bool) or isinstance(den, bool):
        return 0.0
    nv = num if isinstance(num, (int, float)) else coerce_int(num, 0)
    dv = den if isinstance(den, (int, float)) else coerce_int(den, 0)
    if (isinstance(dv, float) and (not math.isfinite(dv) or dv <= 0.0)) or (isinstance(dv, int) and dv <= 0):
        return 0.0
    if (isinstance(nv, float) and (not math.isfinite(nv) or nv <= 0.0)) or (isinstance(nv, int) and nv <= 0):
        return 0.0
    if (isinstance(nv, int) and isinstance(dv, int) and nv.bit_length() <= 1020 and dv.bit_length() <= 1020) or (
        isinstance(nv, float) and isinstance(dv, float)
    ):
        try:
            val = nv / dv
            if math.isfinite(val):
                return max(0.0, min(cap, val))
        except OverflowError:
            pass
    if isinstance(nv, int):
        sn = max(0, nv.bit_length() - 53)
        m_n, e_n = float(nv >> sn), sn
    else:
        m_n, e_n = math.frexp(float(nv))
    if isinstance(dv, int):
        sd = max(0, dv.bit_length() - 53)
        m_d, e_d = float(dv >> sd), sd
    else:
        m_d, e_d = math.frexp(float(dv))
    if m_d == 0.0 or m_n == 0.0:
        return 0.0
    m_q, r_e = math.frexp(m_n / m_d)
    total_e = r_e + e_n - e_d
    if total_e > 900:
        return cap
    if total_e < -1074:
        return 0.0
    res = math.ldexp(m_q, total_e)
    return max(0.0, min(cap, res)) if math.isfinite(res) else 0.0


def safe_state_str(value: object, default: str = "") -> str:
    """Coerce an arbitrary value to ``str`` without raising on huge ints or broken PathLike objects."""
    if value is None:
        return default
    if isinstance(value, os.PathLike):
        try:
            unwrapped: object = os.fspath(value)
        except _FSPATH_ERRS:
            return safe_key(value)
        if unwrapped is None:
            return default
        value = unwrapped
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return fmt_compact_int(value) if abs(value).bit_length() >= 12_000 else str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return safe_key(value)


def safe_optional_state_str(value: object) -> str | None:
    """Coerce an arbitrary scalar value to ``str | None``, rejecting containers and booleans."""
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(value, os.PathLike):
        try:
            unwrapped: object = os.fspath(value)
        except _FSPATH_ERRS:
            return safe_key(value)
        if unwrapped is None or isinstance(unwrapped, (bool, Mapping, list, tuple, set, frozenset)):
            return None
        value = unwrapped
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, int) and abs(value).bit_length() >= 12_000:
        return fmt_compact_int(value)
    return safe_key(value)


def contains_non_dict_mapping(obj: object) -> bool:
    """Return True if ``obj`` contains any non-``dict`` ``Mapping`` (depth/node-bounded)."""
    seen: set[int] = set()
    budget = [32_768]

    def _walk(val: object, depth: int) -> bool:
        if val is None or isinstance(val, (bool, int, float, str, bytes)):
            return False
        if isinstance(val, Mapping):
            if not isinstance(val, dict):
                return True
            vid = id(val)
            if vid in seen or depth >= 64 or budget[0] <= 0:
                return False
            budget[0] -= 1
            seen.add(vid)
            try:
                for item in val.values():
                    if budget[0] <= 0:
                        break
                    if _walk(item, depth + 1):
                        return True
                return False
            finally:
                seen.discard(vid)
        if isinstance(val, (list, tuple)):
            vid = id(val)
            if vid in seen or depth >= 64 or budget[0] <= 0:
                return False
            budget[0] -= 1
            seen.add(vid)
            try:
                for item in val:
                    if budget[0] <= 0:
                        break
                    if _walk(item, depth + 1):
                        return True
                return False
            finally:
                seen.discard(vid)
        return False

    return _walk(obj, 0)


def deep_plain_value(value: object) -> object:
    """Recursively convert arbitrary nested mappings/sequences into plain ``dict``/``list``/``tuple``."""
    seen: set[int] = set()
    budget = [32_768]

    def _walk(val: object, depth: int) -> object:
        if val is None or isinstance(val, (bool, int, float, str, bytes)):
            return val
        if isinstance(val, (Mapping, list, tuple)):
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
                        out_d[k if type(k) is str else safe_key(k)] = _walk(v, depth + 1)
                    return out_d
                out_seq: list[object] = []
                for v in val:
                    if budget[0] <= 0:
                        out_seq.append("[Truncated]")
                        break
                    out_seq.append(_walk(v, depth + 1))
                return out_seq if isinstance(val, list) else tuple(out_seq)
            finally:
                seen.discard(vid)
        return copy.deepcopy(val)

    return _walk(value, 0)


def normalize_int_mapping(raw: object) -> dict[str, int]:
    """Normalize a ``Mapping`` into a plain ``dict[str, int]`` with non-negative int values."""
    if not isinstance(raw, Mapping):
        return {}
    if isinstance(raw, dict) and not any(
        type(k) is not str or isinstance(v, bool) or (isinstance(v, int) and v < 0) for k, v in raw.items()
    ):
        return raw
    out: dict[str, int] = {}
    for k, v in raw.items():
        sk = k if type(k) is str else safe_key(k)
        if isinstance(v, bool):
            out[sk] = 0
        elif isinstance(v, int):
            out[sk] = max(0, v)
        else:
            out[sk] = max(0, coerce_int(v, 0))
    return out


def normalize_str_mapping(raw: object) -> dict[str, str]:
    """Normalize a ``Mapping`` into a plain ``dict[str, str]``."""
    if not isinstance(raw, Mapping):
        return {}
    if isinstance(raw, dict) and all(type(k) is str and type(v) is str for k, v in raw.items()):
        return raw
    return {safe_key(k): safe_state_str(v) for k, v in raw.items()}


def extract_planet_type_token(raw: object) -> str:
    """Extract a normalized lowercase planet-type token from an arbitrary value."""
    if isinstance(raw, os.PathLike):
        try:
            raw = os.fspath(raw)
        except _FSPATH_ERRS:
            return ""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return ""
    return safe_key(raw).strip().lower()


def normalize_planet_type(planet_or_type: object) -> str:
    """Return normalized lowercase planet_type (``'normal'``, ``'asteroid'``, ``'raid'``, ``'moon'``, ...)."""
    if isinstance(planet_or_type, Mapping):
        txt = extract_planet_type_token(planet_or_type.get("planet_type"))
        if not txt:
            txt = extract_planet_type_token(planet_or_type.get("type", "normal"))
        return txt or "normal"
    if isinstance(planet_or_type, (str, bytes, bytearray, os.PathLike)):
        return extract_planet_type_token(planet_or_type) or "normal"
    if isinstance(planet_or_type, _HasPlanetType):
        return extract_planet_type_token(planet_or_type.planet_type) or "normal"
    return extract_planet_type_token(planet_or_type) or "normal"


def is_asteroid_planet(planet_or_type: object) -> bool:
    """Return True iff ``planet_or_type`` represents an asteroid."""
    return normalize_planet_type(planet_or_type) == "asteroid"


def is_raid_planet(planet_or_type: object) -> bool:
    """Return True iff ``planet_or_type`` represents a raid colony."""
    return normalize_planet_type(planet_or_type) == "raid"


def is_building_allowed_on_planet_type(
    planet_or_type: object,
    building_id: object,
    *,
    has_b8_offer: bool = False,
    allow_asteroid_research_lab: bool = False,
) -> bool:
    """Enforce REQ-BLD-003 building eligibility for asteroids, raid colonies, and normal planets."""
    if isinstance(building_id, bool):
        return False
    if isinstance(building_id, int):
        bid = building_id
    elif isinstance(building_id, float):
        if not math.isfinite(building_id) or not building_id.is_integer():
            return False
        bid = int(building_id)
    elif isinstance(building_id, (str, bytes, bytearray)):
        raw_s = (
            bytes(building_id).decode("utf-8", errors="replace")
            if isinstance(building_id, (bytes, bytearray))
            else building_id
        )
        s = raw_s.strip()
        if not s or not s.isascii() or len(s) > 100:
            return False
        digits = s[1:] if s[:1] in "+-" else s
        if not digits or not digits.isdigit():
            return False
        bid = int(s)
    else:
        return False
    if bid not in VALID_BUILDING_IDS:
        return False
    ptype = normalize_planet_type(planet_or_type)
    if ptype == "asteroid":
        if bid in ASTEROID_ALLOWED_BUILDINGS:
            return True
        return bid == 8 and (bool(allow_asteroid_research_lab) or bool(has_b8_offer))
    if bid == 19:
        return False
    return not (ptype == "raid" and bid in RAID_COLONY_FORBIDDEN_BUILDINGS)
