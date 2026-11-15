"""SpaceInvasion Classic economy, level-curve, temperature, and energy formulas."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any, Protocol, runtime_checkable

from backend.core.catalog.coercion import (
    cap_json_int,
    safe_finite_float,
    safe_int,
    safe_optional_int,
)
from backend.core.catalog.data_buildings import (
    MERCHANT_RATES_IN_SPICE,
    MINING_TECH_BONUS_PER_LEVEL,
)
from backend.core.catalog.lookup import resolve_unit

_RESOURCE_KEYS: tuple[str, str, str, str] = ("pig_iron", "metal", "kryptonite", "spice")


@runtime_checkable
class _ResourceAttributes(Protocol):
    pig_iron: object
    metal: object
    kryptonite: object
    spice: object


def _cost_dict(cost: object) -> dict[str, int]:
    if cost is None:
        return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    if isinstance(cost, Mapping):
        return {k: safe_int(cost.get(k, 0), 0) for k in _RESOURCE_KEYS}
    if isinstance(cost, _ResourceAttributes):
        return {
            "pig_iron": safe_int(cost.pig_iron, 0),
            "metal": safe_int(cost.metal, 0),
            "kryptonite": safe_int(cost.kryptonite, 0),
            "spice": safe_int(cost.spice, 0),
        }
    return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}


def _cost_float_dict(cost: object) -> dict[str, float]:
    if cost is None:
        return {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0}
    if isinstance(cost, Mapping):
        return {k: safe_finite_float(cost.get(k, 0.0), 0.0) for k in _RESOURCE_KEYS}
    if isinstance(cost, _ResourceAttributes):
        return {
            "pig_iron": safe_finite_float(cost.pig_iron, 0.0),
            "metal": safe_finite_float(cost.metal, 0.0),
            "kryptonite": safe_finite_float(cost.kryptonite, 0.0),
            "spice": safe_finite_float(cost.spice, 0.0),
        }
    return {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0}


def spice_equivalent(cost_or_res: object) -> float:
    """Convert any resource bundle to Spice equivalents at fixed Merchant parity."""
    vals = _cost_float_dict(cost_or_res)
    total = (
        vals["pig_iron"] / MERCHANT_RATES_IN_SPICE["pig_iron"]
        + vals["metal"] / MERCHANT_RATES_IN_SPICE["metal"]
        + vals["kryptonite"] / MERCHANT_RATES_IN_SPICE["kryptonite"]
        + vals["spice"] / MERCHANT_RATES_IN_SPICE["spice"]
    )
    return total if math.isfinite(total) else 0.0


def building_level_stats(building_id_or_slug: int | str, level: int) -> dict[str, Any]:
    """Return wiki table row for `(building, level)`, or `{}` if `level <= 0` or out of range."""
    lvl = safe_int(level, 0)
    if lvl <= 0 or lvl > 10_000:
        return {}
    info = resolve_unit("buildings", building_id_or_slug)
    if not info:
        return {}
    levels = info.get("levels")
    if isinstance(levels, dict):
        row = levels.get(lvl)
        if isinstance(row, dict):
            return row
    return {}


def building_cost(building_id_or_slug: int | str, target_level: int) -> dict[str, int]:
    """Return wiki resource cost dict for upgrading `building` to `target_level`."""
    row = building_level_stats(building_id_or_slug, target_level)
    cost = row.get("cost")
    if isinstance(cost, dict):
        return dict(cost)
    return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}


def interpolate_temp_value(temp_map: Mapping[int, int], temperature: int | float) -> float:
    """Linearly interpolate a wiki temperature table for an arbitrary planet temperature."""
    if not temp_map or not isinstance(temp_map, Mapping):
        return 0.0
    temp = safe_finite_float(temperature, 0.0)
    pts_raw: list[tuple[int, float]] = []
    for k, v in temp_map.items():
        ki = safe_optional_int(k, None)
        if ki is None:
            continue
        pts_raw.append((cap_json_int(ki, limit=10**12), safe_finite_float(v, 0.0)))
    if not pts_raw:
        return 0.0
    pts = sorted(pts_raw)
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:], strict=False):
        if t0 <= temp <= t1:
            if t1 == t0:
                return v0
            ratio = (temp - t0) / (t1 - t0)
            return v0 + ratio * (v1 - v0)
    return pts[-1][1]


def mine_production_per_hour(
    building_id: int,
    level: int,
    *,
    temperature: int = 0,
    mining_tech_level: int = 0,
) -> float:
    """Return hourly base output for mines (`b1..b4`), including temp curve & Mining tech (+2%/lvl).

    Blast furnace (`b2`) converts Pig iron to Metal at 2:1 and is NOT boosted by Mining technology.
    """
    lvl = safe_int(level, 0)
    bid = safe_int(building_id, 0)
    if lvl <= 0:
        return 0.0
    row = building_level_stats(bid, lvl)
    if not row:
        return 0.0
    if "prod_by_temp" in row and row["prod_by_temp"]:
        base = interpolate_temp_value(row["prod_by_temp"], temperature)
    else:
        base = float(row.get("prod", 0))
    safe_tech = max(0, min(100_000, safe_int(mining_tech_level, 0)))
    if bid in (1, 3, 4) and safe_tech > 0:
        base *= 1.0 + MINING_TECH_BONUS_PER_LEVEL * safe_tech
    return base


def blast_furnace_pig_iron_used(level: int) -> float:
    """Return hourly Pig iron consumed by Blast furnace (`b2`) at `level`."""
    lvl = safe_int(level, 0)
    if lvl <= 0:
        return 0.0
    return float(building_level_stats(2, lvl).get("pig_iron_used", 0))


def mine_energy_needed(building_id_or_slug: int | str, level: int) -> int:
    """Return positive energy consumed by `building` at `level` (0 for power plants)."""
    lvl = safe_int(level, 0)
    if lvl <= 0:
        return 0
    val = int(building_level_stats(building_id_or_slug, lvl).get("energy", 0))
    return -val if val < 0 else 0


def building_energy_delta(building_id_or_slug: int | str, target_level: int) -> int:
    """Signed net energy change when upgrading `building` from `target_level - 1` to `target_level`."""
    tgt = safe_int(target_level, 0)
    if tgt <= 0:
        return 0
    cur_energy = int(building_level_stats(building_id_or_slug, tgt - 1).get("energy", 0))
    nxt_energy = int(building_level_stats(building_id_or_slug, tgt).get("energy", 0))
    return nxt_energy - cur_energy
