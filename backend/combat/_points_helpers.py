"""Shared pure numeric guards and SpaceInvasion Classic hourly economy math (zero DB imports)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import math

from backend.core.catalog import (
    MAX_JSON_SAFE_INT,
    blast_furnace_pig_iron_used,
    cap_json_int,
    fmt_compact_int,
    mine_production_per_hour,
    safe_big_ratio,
    safe_int,
    safe_key,
    safe_optional_int,
    spice_equivalent,
)
from backend.core.catalog import (
    safe_finite_float as safe_finite_float,
)
from backend.core.model import GameState, PlanetState, Resources

POINTS_PER_RESOURCE_DIVISOR = 1_000.0
MAX_ETA_FLOAT: float = 1e200
MAX_ETA_INT: int = 10**5000
MAX_JSON_SAFE: int = MAX_JSON_SAFE_INT


def cap_json_int_eta(value: object) -> int:
    """Clamp integer-coercible value to `[-MAX_JSON_SAFE, MAX_JSON_SAFE]`."""
    return cap_json_int(value, limit=MAX_JSON_SAFE)


def cap_eta_float(value: float, *, default: float = 0.0, allow_negative: bool = False) -> float:
    """Clamp float to `[-MAX_ETA_FLOAT, MAX_ETA_FLOAT]` (or `[0, MAX_ETA_FLOAT]`), handling NaN/Inf."""
    if not math.isfinite(value):
        if math.isnan(value):
            return default
        if value > 0.0:
            return MAX_ETA_FLOAT
        return -MAX_ETA_FLOAT if allow_negative else 0.0
    if value > MAX_ETA_FLOAT:
        return MAX_ETA_FLOAT
    if value < 0.0:
        return max(-MAX_ETA_FLOAT, value) if allow_negative else 0.0
    return value


def safe_eta_str(raw: object) -> str:
    """Safely stringify an identifier or compact very large ints."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return fmt_compact_int(raw) if abs(raw).bit_length() >= 12_000 else str(raw)
    return safe_key(raw)


def safe_optional_finite_float(raw: object) -> float | None:
    """Parse an optional finite float, returning `None` on invalid/non-finite input."""
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isascii():
            return None
        try:
            val = float(s)
        except ValueError:
            return None
        return val if math.isfinite(val) else None
    return None


def safe_optional_finite_int(raw: object) -> int | None:
    """Parse an optional finite int, returning `None` on invalid/non-finite input."""
    return safe_optional_int(raw, None)


def safe_speedup_factor(raw_speedup: object) -> float:
    """Return a strictly positive finite universe speedup factor (default `1.0`)."""
    val = safe_optional_finite_float(raw_speedup)
    if val is None or val <= 0.0:
        return 1.0
    return val


def safe_big_float(raw: object, cap: float = MAX_ETA_FLOAT) -> float:
    """Convert arbitrarily large non-negative integers or floats to capped float without overflow."""
    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw <= 0:
            return 0.0
        if raw.bit_length() > 666:
            return cap
        try:
            f = float(raw)
            return min(cap, f) if math.isfinite(f) else cap
        except OverflowError:
            return cap
    f = safe_finite_float(raw, 0.0)
    return min(cap, f) if f > 0.0 else 0.0


def safe_nonneg_int(raw: object, default: int = 0) -> int:
    """Parse a non-negative integer, returning `default` on non-finite or invalid input."""
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float):
        return max(0, int(raw)) if math.isfinite(raw) else default
    parsed = safe_optional_int(raw, None)
    return default if parsed is None else max(0, parsed)


def safe_big_mul_div(qty: int | float, scale: float, divisor: float) -> float:
    """Compute `(qty * scale) / divisor` safely for arbitrarily large ints without `OverflowError`."""
    if not math.isfinite(divisor) or divisor <= 0.0 or not math.isfinite(scale) or scale <= 0.0:
        return 0.0
    if isinstance(qty, int):
        if qty <= 0:
            return 0.0
        if qty.bit_length() <= 900:
            val = (float(qty) * scale) / divisor
            return min(MAX_ETA_FLOAT, val) if math.isfinite(val) else MAX_ETA_FLOAT
        min_log2 = (qty.bit_length() - 1) + (math.log2(scale) - math.log2(divisor))
        if min_log2 > 666.0:
            return MAX_ETA_FLOAT
        try:
            d_val = (Decimal(qty) * Decimal(str(scale))) / Decimal(str(divisor))
        except (InvalidOperation, ValueError, ArithmeticError):
            return MAX_ETA_FLOAT
        if not d_val.is_finite() or d_val > Decimal("1e200"):
            return MAX_ETA_FLOAT
        return max(0.0, float(d_val))
    qty_f = float(qty)
    if not math.isfinite(qty_f):
        return MAX_ETA_FLOAT if qty_f > 0.0 else 0.0
    if qty_f <= 0.0:
        return 0.0
    val = (qty_f * scale) / divisor
    return min(MAX_ETA_FLOAT, val) if math.isfinite(val) else MAX_ETA_FLOAT


def format_eta(hours: float | None, *, zero_label: str = "Reached") -> str:
    """Format an ETA in hours as `Reached`, `12m`, `4.2h`, or `3.5d`."""
    if hours is None or not math.isfinite(hours):
        return "—"
    if hours <= 0:
        return zero_label
    if hours < 1.0:
        return f"{max(1, round(hours * 60))}m"
    if hours < 48.0:
        return f"{hours:.1f}h"
    return f"{hours / 24.0:.1f}d"


def res_dict(res: Resources) -> dict[str, int]:
    """Serialize a `Resources` bundle into a non-negative JSON-safe int dict."""
    return {
        "pig_iron": cap_json_int_eta(max(0, safe_int(res.pig_iron, 0))),
        "metal": cap_json_int_eta(max(0, safe_int(res.metal, 0))),
        "kryptonite": cap_json_int_eta(max(0, safe_int(res.kryptonite, 0))),
        "spice": cap_json_int_eta(max(0, safe_int(res.spice, 0))),
    }


def _rate_pct(rates_raw: Mapping[str, object], key: str) -> int:
    val = rates_raw.get(key, 100)
    parsed = safe_optional_finite_float(val)
    if parsed is None:
        return 100
    return max(0, min(100, int(parsed)))


def estimate_planet_hourly_economy(state: GameState, planet: PlanetState) -> dict[str, object]:
    """Compute hourly mine output, Blast furnace 2:1 Pig iron burn, and net rate for one planet.

    Preserves SpaceInvasion Classic invariant: Blast furnace (b2) uses 2:1 Pig iron and is NOT
    boosted by Mining technology (r1).
    """
    researches_raw = state.researches if isinstance(state.researches, Mapping) else {}
    mining_tech = min(100_000, safe_nonneg_int(researches_raw.get("r1", 0), 0))
    speedup = safe_speedup_factor(state.speedup_factor)

    prod_ratio = 1.0
    e_prod = safe_nonneg_int(planet.energy.produced, 0)
    e_used = safe_nonneg_int(planet.energy.used, 0)
    if e_used > 0 and e_prod < e_used:
        prod_ratio = safe_big_ratio(e_prod, e_used, cap=1.0)

    rates_raw: Mapping[str, object] = planet.economy_rates if isinstance(planet.economy_rates, Mapping) else {}
    pct1 = _rate_pct(rates_raw, "resource1")
    pct2 = _rate_pct(rates_raw, "resource2")
    pct3 = _rate_pct(rates_raw, "resource3")
    pct4 = _rate_pct(rates_raw, "resource4")

    factor = speedup * prod_ratio
    b1 = planet.building_level(1)
    b2 = planet.building_level(2)
    b3 = planet.building_level(3)
    b4 = planet.building_level(4)

    raw_p_pig = (
        mine_production_per_hour(1, b1, temperature=planet.temperature, mining_tech_level=mining_tech)
        * factor
        * (pct1 / 100.0)
    )
    raw_p_met = mine_production_per_hour(2, b2) * factor * (pct2 / 100.0)
    raw_used_pig = blast_furnace_pig_iron_used(b2) * factor * (pct2 / 100.0)
    raw_p_kryp = (
        mine_production_per_hour(3, b3, temperature=planet.temperature, mining_tech_level=mining_tech)
        * factor
        * (pct3 / 100.0)
    )
    raw_p_spice = (
        mine_production_per_hour(4, b4, temperature=planet.temperature, mining_tech_level=mining_tech)
        * factor
        * (pct4 / 100.0)
    )

    raw_net_pig = raw_p_pig - raw_used_pig
    p_pig = cap_eta_float(raw_p_pig)
    used_pig = cap_eta_float(raw_used_pig)
    net_pig = cap_eta_float(raw_net_pig, allow_negative=True)
    p_met = cap_eta_float(raw_p_met)
    p_kryp = cap_eta_float(raw_p_kryp)
    p_spice = cap_eta_float(raw_p_spice)

    return {
        "planet_id": cap_json_int_eta(safe_int(planet.planet_id, 0)),
        "planet_coords": safe_eta_str(planet.coords),
        "speedup_factor": speedup,
        "prod_ratio": round(prod_ratio, 4),
        "rates_pct": {
            "resource1": pct1,
            "resource2": pct2,
            "resource3": pct3,
            "resource4": pct4,
        },
        "blast_furnace_pct": pct2,
        "pig_iron_gross_per_hr": round(p_pig, 1),
        "blast_furnace_pig_used_per_hr": round(used_pig, 1),
        "pig_iron_per_hr": round(net_pig, 1),
        "metal_per_hr": round(p_met, 1),
        "kryptonite_per_hr": round(p_kryp, 1),
        "spice_per_hr": round(p_spice, 1),
    }


def estimate_hourly_economy(state: GameState) -> dict[str, float]:
    """Compute empire-wide hourly net production and potential point gain per hour."""
    speedup = safe_speedup_factor(state.speedup_factor)
    net_pig = 0.0
    net_met = 0.0
    net_kryp = 0.0
    net_spice = 0.0

    for planet in state.planets:
        p_econ = estimate_planet_hourly_economy(state, planet)
        net_pig += safe_finite_float(p_econ["pig_iron_per_hr"], 0.0)
        net_met += safe_finite_float(p_econ["metal_per_hr"], 0.0)
        net_kryp += safe_finite_float(p_econ["kryptonite_per_hr"], 0.0)
        net_spice += safe_finite_float(p_econ["spice_per_hr"], 0.0)

    net_pig = cap_eta_float(net_pig, allow_negative=True)
    net_met = cap_eta_float(net_met)
    net_kryp = cap_eta_float(net_kryp)
    net_spice = cap_eta_float(net_spice)
    net_total_res = cap_eta_float(max(0.0, net_pig) + max(0.0, net_met) + max(0.0, net_kryp) + max(0.0, net_spice))
    pts_per_hr = cap_eta_float(net_total_res / POINTS_PER_RESOURCE_DIVISOR)
    spice_hr = cap_eta_float(
        spice_equivalent(
            {
                "pig_iron": max(0.0, net_pig),
                "metal": max(0.0, net_met),
                "kryptonite": max(0.0, net_kryp),
                "spice": max(0.0, net_spice),
            }
        )
    )

    return {
        "speedup_factor": speedup,
        "pig_iron_per_hr": round(net_pig, 1),
        "metal_per_hr": round(net_met, 1),
        "kryptonite_per_hr": round(net_kryp, 1),
        "spice_per_hr": round(net_spice, 1),
        "net_resources_per_hr": round(net_total_res, 1),
        "pts_per_hr_steady": round(pts_per_hr, 2),
        "spice_eq_per_hr": round(spice_hr, 1),
    }
