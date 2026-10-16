"""Pure low-level resolvers, duration formatters, and numeric guards for `spaceinvasion.planner.eta`."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import math
import os
import re

from spaceinvasion.combat._points_helpers import (
    MAX_ETA_FLOAT,
    MAX_ETA_INT,
    safe_big_mul_div,
    safe_eta_str,
    safe_optional_finite_int,
)
from spaceinvasion.core.catalog import (
    BUILDING_BY_ID,
    RESEARCH_BY_ID,
    resolve_unit,
    safe_finite_float,
    safe_scale_round_int,
)
from spaceinvasion.core.model import GameState, PlanetState

ETA_RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")

INSTANT_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "ALERT_HOSTILE",
        "ESCAPE_FLIGHT",
        "CLAIM_TUTORIAL",
        "THROTTLE_BUILDING",
        "SCAN_GALAXY",
        "ATTACK",
        "SPY",
        "TUTORIAL_TASK",
        "NONE",
        "COLONIZE",
        "RECYCLE",
        "TRANSPORT",
        "STATION",
    }
)

_SCI_NUM_RE = re.compile(r"^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))[eE]([+-]?\d+)$", re.ASCII)
_PLAIN_NUM_RE = re.compile(r"^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))$", re.ASCII)


def building_category(building_id: int) -> str:
    """Classify a SpaceInvasion building ID (`1..21`) into its UI category bucket."""
    if building_id in (1, 2, 3, 4):
        return "mines"
    if building_id in (5, 6):
        return "energy"
    if building_id in (11, 12, 13, 14):
        return "storage"
    if building_id in (7, 8, 9, 10, 17):
        return "infra"
    return "special"


def format_duration(seconds: float | None) -> str:
    """Format a build/research duration in seconds as `1s`, `45s`, `12m 05s`, `3h 15m`, or `2d 4h`."""
    if seconds is None or not math.isfinite(seconds):
        return "—"
    if 0.0 < seconds <= 0.5:
        return "1s"
    sec = max(0, int(round(seconds)))
    if sec < 60:
        return f"{sec}s"
    mins, s = divmod(sec, 60)
    if mins < 60:
        return f"{mins}m {s:02d}s"
    hrs, m = divmod(mins, 60)
    if hrs < 24:
        return f"{hrs}h {m:02d}m"
    days, h = divmod(hrs, 24)
    return f"{days}d {h}h"


def round_seconds(seconds: float) -> float:
    """Round non-negative duration seconds while preserving sub-centisecond visibility."""
    if seconds <= 0.0 or not math.isfinite(seconds):
        return 0.0
    if seconds >= 0.01:
        return round(seconds, 2)
    return max(0.0001, round(seconds, 4))


def _decimal_to_eta_int(value: Decimal, default: int = 0) -> int:
    if not value.is_finite():
        return default
    if value == 0 or value.adjusted() < 0:
        return 0
    if value.adjusted() > 5000:
        return -(10**5000) if value < 0 else (10**5000)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError, InvalidOperation, ArithmeticError):
        return default


def _decimal_is_integer(value: Decimal) -> bool:
    if not value.is_finite():
        return False
    tup = value.as_tuple()
    if not isinstance(tup.exponent, int):
        return False
    if tup.exponent >= 0:
        return True
    frac_len = -tup.exponent
    digits = tup.digits
    if frac_len >= len(digits):
        return all(d == 0 for d in digits)
    return all(d == 0 for d in digits[-frac_len:])


def safe_eta_int(raw: object, default: int = 0, *, pos_inf_value: int | None = None) -> int:
    """Parse an arbitrary numeric/Decimal/scientific-notation input safely into a bounded int."""
    if isinstance(raw, os.PathLike):
        try:
            raw = os.fspath(raw)
        except (TypeError, ValueError, OSError, RuntimeError, AttributeError, LookupError, OverflowError, RecursionError):
            return default
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if math.isfinite(raw):
            return int(raw)
        return pos_inf_value if (pos_inf_value is not None and raw > 0.0) else default
    if isinstance(raw, Decimal):
        if pos_inf_value is not None and raw.is_infinite() and raw > 0:
            return pos_inf_value
        return _decimal_to_eta_int(raw, default)
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    try:
        s = raw.strip() if isinstance(raw, str) else str(raw).strip()
        if not s or not s.isascii():
            return default
        if pos_inf_value is not None and s.lower() in ("inf", "+inf", "infinity", "+infinity"):
            return pos_inf_value
        m_sci = _SCI_NUM_RE.match(s)
        if m_sci:
            sign, int_part, frac1, frac2, exp_str = m_sci.groups()
            int_digits = int_part or ""
            frac_digits = frac1 or frac2 or ""
            if not any(ch != "0" for ch in int_digits) and not any(ch != "0" for ch in frac_digits):
                return 0
            exp = int(exp_str)
            nz_int = int_digits.lstrip("0")
            adjusted = (
                (len(nz_int) - 1 + exp) if nz_int else (-(len(frac_digits) - len(frac_digits.lstrip("0")) + 1) + exp)
            )
            if adjusted > 5000:
                return -(10**5000) if sign == "-" else (10**5000)
            if adjusted < 0:
                return 0
        else:
            m_pl = _PLAIN_NUM_RE.match(s)
            if m_pl:
                sign, int_part, _, _ = m_pl.groups()
                if len((int_part or "").lstrip("0")) - 1 > 5000:
                    return -(10**5000) if sign == "-" else (10**5000)
        d = Decimal(s)
    except (ValueError, TypeError, InvalidOperation, OverflowError, ArithmeticError):
        return default
    return _decimal_to_eta_int(d, default)


def compute_resource_eta(
    missing: Mapping[str, int],
    rates_hr: Mapping[str, float],
) -> tuple[float | None, str | None]:
    """Return `(wait_hours, bottleneck_resource)` to accumulate `missing` resources."""
    need_keys = [rk for rk in ETA_RESOURCE_KEYS if safe_eta_int(missing.get(rk, 0), 0, pos_inf_value=MAX_ETA_INT) > 0]
    if not need_keys:
        return 0.0, None
    max_hours = 0.0
    bottleneck: str | None = None
    for rk in need_keys:
        rate = safe_finite_float(rates_hr.get(rk, 0.0), 0.0)
        if not math.isfinite(rate) or rate <= 0.0:
            return None, rk
        need_val = safe_eta_int(missing.get(rk, 0), 0, pos_inf_value=MAX_ETA_INT)
        hrs = safe_big_mul_div(need_val, 1.0, rate)
        if bottleneck is None or hrs > max_hours:
            max_hours = hrs
            bottleneck = rk
    return max_hours, bottleneck


def research_level_stats(info: Mapping[str, object], level: int) -> dict[str, object]:
    """Look up or extrapolate (`2^delta`) cost & base build time for a research level."""
    levels = info.get("levels")
    if isinstance(levels, Mapping):
        row = levels.get(level)
        if isinstance(row, Mapping):
            return dict(row)
        if levels:
            max_lvl = max(int(k) for k in levels.keys())
            base_row = levels.get(max_lvl)
            if isinstance(base_row, Mapping):
                exp = min(max(0, level - max_lvl), 600)
                scale = 2.0**exp
                base_cost = base_row.get("cost")
                cost_map = base_cost if isinstance(base_cost, Mapping) else {}
                base_time = safe_finite_float(base_row.get("base_time_s", 0.0), 0.0)
                return {
                    "cost": {rk: safe_scale_round_int(cost_map.get(rk, 0), scale) for rk in ETA_RESOURCE_KEYS},
                    "base_time_s": min(MAX_ETA_FLOAT, base_time * scale),
                }
    return {"cost": {rk: 0 for rk in ETA_RESOURCE_KEYS}, "base_time_s": 0.0}


def action_field(action: object, key: str, default: object = None) -> object:
    """Read `key` from a mapping or dataclass/object action."""
    if isinstance(action, Mapping):
        return action.get(key, default)
    return getattr(action, key, default)


def extract_resource_map(raw: object) -> dict[str, int] | None:
    """Extract a 4-resource integer map from a mapping or `Resources`-like object."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return {rk: max(0, safe_eta_int(raw.get(rk, 0), 0, pos_inf_value=MAX_ETA_INT)) for rk in ETA_RESOURCE_KEYS}
    if any(hasattr(raw, rk) for rk in ETA_RESOURCE_KEYS):
        return {rk: max(0, safe_eta_int(getattr(raw, rk, 0), 0, pos_inf_value=MAX_ETA_INT)) for rk in ETA_RESOURCE_KEYS}
    return None


def resolve_planet_for_eta(state: GameState, action: object) -> PlanetState | None:
    """Resolve the target `PlanetState` for an action by `planet_id`, `planet_coords`, or active planet."""
    planets = tuple(state.planets or ())
    if not planets:
        return None
    raw_pid = action_field(action, "planet_id", None)
    if isinstance(raw_pid, os.PathLike):
        try:
            raw_pid = os.fspath(raw_pid)
        except (TypeError, ValueError, OSError, RuntimeError, AttributeError, LookupError, OverflowError, RecursionError):
            return None
    if isinstance(raw_pid, (bytes, bytearray)):
        raw_pid = bytes(raw_pid).decode("utf-8", errors="replace")
    if isinstance(raw_pid, str):
        raw_pid = raw_pid.strip()
    raw_coords = safe_eta_str(action_field(action, "planet_coords", "")).strip()

    if raw_pid is not None and raw_pid != "":
        pid = _parse_exact_planet_id(raw_pid, planets)
        if pid is None:
            return None
        for planet in planets:
            p_pid = safe_optional_finite_int(planet.planet_id)
            if p_pid is not None and p_pid == pid:
                return planet
        return None

    if raw_coords:
        for planet in planets:
            if safe_eta_str(planet.coords).strip() == raw_coords:
                return planet
        return None

    return state.active_planet()


def _parse_exact_planet_id(raw_pid: object, planets: tuple[PlanetState, ...]) -> int | None:
    if isinstance(raw_pid, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(raw_pid, int):
        return raw_pid
    if isinstance(raw_pid, float):
        return int(raw_pid) if (math.isfinite(raw_pid) and raw_pid.is_integer()) else None
    if isinstance(raw_pid, Decimal):
        if not _decimal_is_integer(raw_pid):
            return None
        max_pid_bits = max((abs(safe_eta_int(p.planet_id, 0)).bit_length() for p in planets), default=0)
        if raw_pid != 0 and raw_pid.adjusted() > max(20, max_pid_bits):
            return None
        try:
            return int(raw_pid)
        except (ValueError, OverflowError, InvalidOperation):
            return None
    if isinstance(raw_pid, str):
        s = raw_pid.strip()
        if not s or not s.isascii():
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def resolve_building_id(target_id: object, target_name: object) -> int | None:
    """Resolve a building target identifier (`1`, `'b1'`, or name) to its numeric ID."""
    if target_id is not None and target_id != "":
        if isinstance(target_id, bool):
            return None
        if isinstance(target_id, int):
            bid = target_id
        else:
            s = safe_eta_str(target_id).strip()
            if s.lower().startswith("b") and len(s) <= 12 and s[1:].isascii() and s[1:].isdigit():
                bid = int(s[1:])
            elif len(s) <= 12 and s.isascii() and s.isdigit():
                bid = int(s)
            else:
                return None
        return bid if bid in BUILDING_BY_ID else None

    if target_name:
        info = resolve_unit("buildings", safe_eta_str(target_name))
        if info and isinstance(info.get("id"), int):
            bid = int(info["id"])
            return bid if bid in BUILDING_BY_ID else None
    return None


def resolve_research_key(target_id: object, target_name: object) -> str | None:
    """Resolve a research target identifier (`1`, `'r1'`, or name) to `'rN'`."""
    if target_id is not None and target_id != "":
        if isinstance(target_id, bool):
            return None
        if isinstance(target_id, int):
            rkey = f"r{target_id}" if 0 <= target_id <= 10_000 else ""
        else:
            s = safe_eta_str(target_id).strip()
            if s.lower().startswith("r") and len(s) <= 12 and s[1:].isascii() and s[1:].isdigit():
                rkey = f"r{int(s[1:])}"
            elif len(s) <= 12 and s.isascii() and s.isdigit():
                rkey = f"r{int(s)}"
            else:
                return None
        return rkey if rkey in RESEARCH_BY_ID else None

    if target_name:
        info = resolve_unit("research", safe_eta_str(target_name))
        if info and info.get("id") is not None:
            rkey = str(info["id"])
            return rkey if rkey in RESEARCH_BY_ID else None
    return None


def resolve_unit_slug(
    category: str,
    prefix: str,
    slug_by_key: Mapping[str, str],
    catalog: Mapping[str, Mapping[str, object]],
    target_id: object,
    target_name: object,
) -> str | None:
    """Resolve a ship or defense unit identifier to its canonical hyphenated slug."""
    if target_id is not None and target_id != "":
        if isinstance(target_id, bool):
            return None
        if isinstance(target_id, int):
            return slug_by_key.get(f"{prefix}{target_id}") if 0 <= target_id <= 10_000 else None
        s = safe_eta_str(target_id).strip()
        if s.lower().startswith(prefix) and len(s) <= 12 and s[1:].isascii() and s[1:].isdigit():
            return slug_by_key.get(f"{prefix}{int(s[1:])}")
        if len(s) <= 12 and s.isascii() and s.isdigit():
            return slug_by_key.get(f"{prefix}{int(s)}")
        return s if s in catalog else None

    if target_name:
        info = resolve_unit(category, safe_eta_str(target_name))
        if info and isinstance(info.get("slug"), str) and str(info["slug"]) in catalog:
            return str(info["slug"])
    return None
