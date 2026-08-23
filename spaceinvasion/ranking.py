"""Ranking evolution tracker, points estimator, Attack Zone classifier, and growth forecasts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields as dataclass_fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Optional

from spaceinvasion import game_data
from spaceinvasion.fleet import (
    DEFENSE_SLUG_BY_KEY,
    SHIP_SLUG_BY_KEY,
    SpyReport,
    plan_escape_flight,
    simulate_raid_outcome,
)
from spaceinvasion.state import GameState, PlanetState, Resources, normalize_planet_type

NEWBIE_PROTECTION_POINTS = 10_000
POINTS_PER_RESOURCE_DIVISOR = 1_000.0


@dataclass(frozen=True)
class AttackZoneBand:
    """SpaceInvasion Classic Attack Zone point band (`internalUniverse`)."""

    zone: int
    min_pts: int
    max_pts: Optional[int]  # None means unbounded upper tier (AZ 20)

    @property
    def label(self) -> str:
        return f"AZ {self.zone}"

    @property
    def range_text(self) -> str:
        if self.max_pts is None:
            return f"{self.min_pts:,}+"
        return f"{self.min_pts:,} – {self.max_pts:,}"


ATTACK_ZONES: tuple[AttackZoneBand, ...] = (
    AttackZoneBand(1, 0, 1_000),
    AttackZoneBand(2, 1_000, 5_000),
    AttackZoneBand(3, 5_000, 25_000),
    AttackZoneBand(4, 25_000, 50_000),
    AttackZoneBand(5, 50_000, 100_000),
    AttackZoneBand(6, 100_000, 250_000),
    AttackZoneBand(7, 250_000, 500_000),
    AttackZoneBand(8, 500_000, 1_000_000),
    AttackZoneBand(9, 1_000_000, 2_500_000),
    AttackZoneBand(10, 2_500_000, 5_000_000),
    AttackZoneBand(11, 5_000_000, 10_000_000),
    AttackZoneBand(12, 10_000_000, 20_000_000),
    AttackZoneBand(13, 20_000_000, 35_000_000),
    AttackZoneBand(14, 35_000_000, 50_000_000),
    AttackZoneBand(15, 50_000_000, 75_000_000),
    AttackZoneBand(16, 75_000_000, 100_000_000),
    AttackZoneBand(17, 100_000_000, 250_000_000),
    AttackZoneBand(18, 250_000_000, 1_000_000_000),
    AttackZoneBand(19, 1_000_000_000, 5_000_000_000),
    AttackZoneBand(20, 5_000_000_000, None),
)


def attack_zone_for_points(points: float) -> dict[str, Any]:
    """Return current and next Attack Zone metadata for `points`."""
    pts = max(0.0, _safe_finite_float(points, 0.0))
    chosen = ATTACK_ZONES[-1]
    next_band: Optional[AttackZoneBand] = None
    for idx, band in enumerate(ATTACK_ZONES):
        if band.max_pts is None or pts < band.max_pts:
            chosen = band
            next_band = ATTACK_ZONES[idx + 1] if idx + 1 < len(ATTACK_ZONES) else None
            break

    return {
        "zone": chosen.zone,
        "label": chosen.label,
        "range_text": chosen.range_text,
        "min_pts": chosen.min_pts,
        "max_pts": chosen.max_pts,
        "next_zone": next_band.label if next_band else None,
        "next_zone_pts": next_band.min_pts if next_band else None,
        "pts_to_next_zone": max(0.0, (next_band.min_pts - pts)) if next_band else 0.0,
    }


def _sum_cost_resources(cost: dict[str, Any], totals: dict[str, int]) -> None:
    for res_key in ("pig_iron", "metal", "kryptonite", "spice"):
        totals[res_key] += _safe_nonneg_int(cost.get(res_key, 0), 0)


def estimate_account_points(state: GameState) -> dict[str, Any]:
    """Estimate empire points (1 pt / 1,000 resources) and Spice value across buildings, research, and fleet."""
    bld_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    flt_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    res_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}

    for planet in state.planets:
        b_map = planet.buildings if isinstance(planet.buildings, Mapping) else {}
        for bkey, lvl in b_map.items():
            bkey_s = _safe_eta_str(bkey).strip()
            if bkey_s.startswith("b"):
                bkey_s = bkey_s[1:]
            if not (bkey_s.isascii() and bkey_s.isdigit() and len(bkey_s) <= 6):
                continue
            bid = int(bkey_s)
            lvl_i = min(1000, _safe_nonneg_int(lvl, 0))
            for l in range(1, lvl_i + 1):
                _sum_cost_resources(game_data.building_cost(bid, l), bld_res)

        s_map = planet.ships if isinstance(planet.ships, Mapping) else {}
        for skey, qty in s_map.items():
            count = _safe_nonneg_int(qty, 0)
            if count <= 0:
                continue
            slug = SHIP_SLUG_BY_KEY.get(skey)
            unit = game_data.SHIPS.get(slug or "") if slug else None
            base_cost = (unit or {}).get("base_cost") or {}
            for rk in flt_res:
                flt_res[rk] += _safe_nonneg_int(base_cost.get(rk, 0), 0) * count

        d_map = planet.defense if isinstance(planet.defense, Mapping) else {}
        for dkey, qty in d_map.items():
            count = _safe_nonneg_int(qty, 0)
            if count <= 0:
                continue
            slug = DEFENSE_SLUG_BY_KEY.get(dkey)
            unit = game_data.DEFENSES.get(slug or "") if slug else None
            base_cost = (unit or {}).get("base_cost") or {}
            for rk in flt_res:
                flt_res[rk] += _safe_nonneg_int(base_cost.get(rk, 0), 0) * count

    r_map = state.researches if isinstance(state.researches, Mapping) else {}
    for rkey, lvl in r_map.items():
        info = game_data.resolve_unit("research", rkey)
        levels = (info or {}).get("levels") or {}
        lvl_i = min(1000, _safe_nonneg_int(lvl, 0))
        for l in range(1, lvl_i + 1):
            row = levels.get(l) or {}
            _sum_cost_resources(row.get("cost") or {}, res_res)

    bld_pts = _cap_eta_float(_safe_big_mul_div(sum(bld_res.values()), 1.0, POINTS_PER_RESOURCE_DIVISOR))
    res_pts = _cap_eta_float(_safe_big_mul_div(sum(res_res.values()), 1.0, POINTS_PER_RESOURCE_DIVISOR))
    flt_pts = _cap_eta_float(_safe_big_mul_div(sum(flt_res.values()), 1.0, POINTS_PER_RESOURCE_DIVISOR))
    total_pts = _cap_eta_float(bld_pts + res_pts + flt_pts)

    combined = {k: bld_res[k] + res_res[k] + flt_res[k] for k in bld_res}
    for planet in state.planets:
        combined["pig_iron"] += _safe_nonneg_int(planet.resources.pig_iron, 0)
        combined["metal"] += _safe_nonneg_int(planet.resources.metal, 0)
        combined["kryptonite"] += _safe_nonneg_int(planet.resources.kryptonite, 0)
        combined["spice"] += _safe_nonneg_int(planet.resources.spice, 0)

    return {
        "estimated_points": round(total_pts, 1),
        "buildings_points": round(bld_pts, 1),
        "research_points": round(res_pts, 1),
        "fleet_defense_points": round(flt_pts, 1),
        "spice_value": round(_cap_eta_float(game_data.spice_equivalent(combined)), 1),
    }


_MAX_ETA_FLOAT: float = 1e200
_MAX_ETA_INT: int = 10**5000
_MAX_JSON_SAFE_INT: int = game_data.MAX_JSON_SAFE_INT


def _cap_json_int(value: Any) -> int:
    return game_data.cap_json_int(value, limit=_MAX_JSON_SAFE_INT)


def _cap_eta_float(
    value: float,
    *,
    default: float = 0.0,
    allow_negative: bool = False,
) -> float:
    if not math.isfinite(value):
        if math.isnan(value):
            return default
        if value > 0.0:
            return _MAX_ETA_FLOAT
        return -_MAX_ETA_FLOAT if allow_negative else 0.0
    if value > _MAX_ETA_FLOAT:
        return _MAX_ETA_FLOAT
    if value < 0.0:
        return max(-_MAX_ETA_FLOAT, value) if allow_negative else 0.0
    return value


def _safe_eta_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return game_data.fmt_compact_int(raw) if abs(raw).bit_length() >= 12_000 else str(raw)
    return game_data.safe_key(raw)


def _safe_finite_float(raw: Any, default: float = 0.0) -> float:
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isascii():
            return default
        raw = s
    try:
        val = float(raw)
    except Exception:
        return default
    return val if math.isfinite(val) else default


def _safe_optional_finite_float(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isascii():
            return None
        raw = s
    try:
        val = float(raw)
    except Exception:
        return None
    return val if math.isfinite(val) else None


def _safe_optional_finite_int(raw: Any) -> Optional[int]:
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isascii():
            return None
        try:
            return int(s)
        except Exception:
            raw = s
    val = _safe_optional_finite_float(raw)
    if val is None:
        return None
    try:
        return int(val)
    except Exception:
        return None


def _safe_speedup_factor(raw_speedup: Any) -> float:
    if raw_speedup is None or isinstance(raw_speedup, (bool, Mapping, list, tuple, set, frozenset)):
        return 1.0
    if isinstance(raw_speedup, (bytes, bytearray)):
        raw_speedup = bytes(raw_speedup).decode("utf-8", errors="replace")
    if isinstance(raw_speedup, str):
        s = raw_speedup.strip()
        if not s or not s.isascii():
            return 1.0
        raw_speedup = s
    try:
        speedup = float(raw_speedup)
    except Exception:
        return 1.0
    if not math.isfinite(speedup) or speedup <= 0.0:
        return 1.0
    return speedup


def _safe_big_float(raw: Any, cap: float = _MAX_ETA_FLOAT) -> float:
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
    f = _safe_finite_float(raw, 0.0)
    if f <= 0.0:
        return 0.0
    return min(cap, f)


def _safe_nonneg_int(raw: Any, default: int = 0) -> int:
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float):
        if not math.isfinite(raw):
            return default
        return max(0, int(raw))
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isascii():
            return default
        try:
            return max(0, int(s))
        except Exception:
            pass
        try:
            val = float(s)
        except Exception:
            return default
        if not math.isfinite(val):
            return default
        try:
            return max(0, int(val))
        except Exception:
            return default
    return default


def _safe_big_mul_div(qty: int | float, scale: float, divisor: float) -> float:
    """Compute `(qty * scale) / divisor` safely for arbitrarily large ints without `OverflowError`."""
    if not math.isfinite(divisor) or divisor <= 0.0 or not math.isfinite(scale) or scale <= 0.0:
        return 0.0
    if isinstance(qty, int):
        if qty <= 0:
            return 0.0
        if qty.bit_length() <= 900:
            val = (float(qty) * scale) / divisor
            return min(_MAX_ETA_FLOAT, val) if math.isfinite(val) else _MAX_ETA_FLOAT
        min_log2 = (qty.bit_length() - 1) + (math.log2(scale) - math.log2(divisor))
        if min_log2 > 666.0:
            return _MAX_ETA_FLOAT
        try:
            d_val = (Decimal(qty) * Decimal(str(scale))) / Decimal(str(divisor))
        except (InvalidOperation, ValueError, ArithmeticError):
            return _MAX_ETA_FLOAT
        if not d_val.is_finite() or d_val > Decimal("1e200"):
            return _MAX_ETA_FLOAT
        return max(0.0, float(d_val))
    qty_f = float(qty)
    if not math.isfinite(qty_f):
        return _MAX_ETA_FLOAT if qty_f > 0.0 else 0.0
    if qty_f <= 0.0:
        return 0.0
    val = (qty_f * scale) / divisor
    return min(_MAX_ETA_FLOAT, val) if math.isfinite(val) else _MAX_ETA_FLOAT


def estimate_planet_hourly_economy(state: GameState, planet: PlanetState) -> dict[str, Any]:
    """Compute hourly mine output, Blast furnace Pig iron burn, and net rate for one planet."""
    researches_raw = getattr(state, "researches", None)
    raw_r1 = researches_raw.get("r1", 0) if isinstance(researches_raw, Mapping) else 0
    mining_tech = min(100_000, _safe_nonneg_int(raw_r1, 0))
    speedup = _safe_speedup_factor(getattr(state, "speedup_factor", None))

    prod_ratio = 1.0
    e_prod = _safe_nonneg_int(getattr(planet.energy, "produced", 0), 0)
    e_used = _safe_nonneg_int(getattr(planet.energy, "used", 0), 0)
    if e_used > 0 and e_prod < e_used:
        prod_ratio = game_data.safe_big_ratio(e_prod, e_used, cap=1.0)

    rates_raw = getattr(planet, "economy_rates", None)
    if not isinstance(rates_raw, Mapping):
        rates_raw = {}

    def _rate_pct(key: str) -> int:
        val = rates_raw.get(key, 100)
        if val is None or isinstance(val, (bool, Mapping, list, tuple, set, frozenset)):
            return 100
        if isinstance(val, int):
            return max(0, min(100, val))
        if isinstance(val, float):
            if not math.isfinite(val):
                return 100
            return max(0, min(100, int(val)))
        if isinstance(val, (bytes, bytearray)):
            val = bytes(val).decode("utf-8", errors="replace")
        if isinstance(val, str):
            s = val.strip()
            if not s or not s.isascii():
                return 100
            val = s
        try:
            parsed = float(val)
        except Exception:
            return 100
        if not math.isfinite(parsed):
            return 100
        return max(0, min(100, int(parsed)))

    pct1 = _rate_pct("resource1")
    pct2 = _rate_pct("resource2")
    pct3 = _rate_pct("resource3")
    pct4 = _rate_pct("resource4")

    factor = speedup * prod_ratio
    b1 = planet.building_level(1)
    b2 = planet.building_level(2)
    b3 = planet.building_level(3)
    b4 = planet.building_level(4)

    raw_p_pig = (
        game_data.mine_production_per_hour(
            1, b1, temperature=planet.temperature, mining_tech_level=mining_tech
        )
        * factor
        * (pct1 / 100.0)
    )
    raw_p_met = game_data.mine_production_per_hour(2, b2) * factor * (pct2 / 100.0)
    raw_used_pig = game_data.blast_furnace_pig_iron_used(b2) * factor * (pct2 / 100.0)
    raw_p_kryp = (
        game_data.mine_production_per_hour(
            3, b3, temperature=planet.temperature, mining_tech_level=mining_tech
        )
        * factor
        * (pct3 / 100.0)
    )
    raw_p_spice = (
        game_data.mine_production_per_hour(
            4, b4, temperature=planet.temperature, mining_tech_level=mining_tech
        )
        * factor
        * (pct4 / 100.0)
    )
    # Subtract before clamping so (inf - inf -> nan -> 0.0) preserves test_ranking.py:698-705
    # and (inf - finite -> +inf -> +_MAX_ETA_FLOAT) preserves positive sign at 1e306.
    raw_net_pig = raw_p_pig - raw_used_pig
    p_pig = _cap_eta_float(raw_p_pig)
    used_pig = _cap_eta_float(raw_used_pig)
    net_pig = _cap_eta_float(raw_net_pig, allow_negative=True)
    p_met = _cap_eta_float(raw_p_met)
    p_kryp = _cap_eta_float(raw_p_kryp)
    p_spice = _cap_eta_float(raw_p_spice)

    return {
        "planet_id": _cap_json_int(_safe_eta_int(getattr(planet, "planet_id", 0), 0)),
        "planet_coords": _safe_eta_str(planet.coords),
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
    speedup = _safe_speedup_factor(getattr(state, "speedup_factor", None))

    net_pig = 0.0
    net_met = 0.0
    net_kryp = 0.0
    net_spice = 0.0

    for planet in state.planets:
        p_econ = estimate_planet_hourly_economy(state, planet)
        net_pig += float(p_econ["pig_iron_per_hr"])
        net_met += float(p_econ["metal_per_hr"])
        net_kryp += float(p_econ["kryptonite_per_hr"])
        net_spice += float(p_econ["spice_per_hr"])

    net_pig = _cap_eta_float(net_pig, allow_negative=True)
    net_met = _cap_eta_float(net_met)
    net_kryp = _cap_eta_float(net_kryp)
    net_spice = _cap_eta_float(net_spice)
    net_total_res = _cap_eta_float(
        max(0.0, net_pig) + max(0.0, net_met) + max(0.0, net_kryp) + max(0.0, net_spice)
    )
    pts_per_hr = _cap_eta_float(net_total_res / POINTS_PER_RESOURCE_DIVISOR)
    spice_hr = _cap_eta_float(
        game_data.spice_equivalent(
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


def _simulate_compound_points(start_pts: float, steady_rate_hr: float, total_hours: int) -> float:
    """Estimate cumulative score with continuous mine reinvestment attenuated by `1.51x` upgrade scaling."""
    pts = _cap_eta_float(max(0.0, start_pts))
    rate = _cap_eta_float(max(0.0, steady_rate_hr))
    for _ in range(max(0, total_hours)):
        pts += rate
        if not math.isfinite(pts) or pts >= _MAX_ETA_FLOAT:
            return _MAX_ETA_FLOAT
        if rate > 0:
            # Diminishing marginal ROI as account scale expands across build tiers
            reinvest_boost = min(0.045, 30.0 / max(180.0, pts))
            rate = _cap_eta_float(rate * (1.0 + reinvest_boost))
    return _cap_eta_float(pts)


def _format_eta(hours: Optional[float], *, zero_label: str = "Reached") -> str:
    if hours is None or not math.isfinite(hours):
        return "—"
    if hours <= 0:
        return zero_label
    if hours < 1.0:
        return f"{max(1, round(hours * 60))}m"
    if hours < 48.0:
        return f"{hours:.1f}h"
    return f"{hours / 24.0:.1f}d"


def _solve_compound_eta_hours(
    start_pts: float, steady_rate_hr: float, target_pts: float, max_hours: int = 24 * 30
) -> Optional[float]:
    if start_pts >= target_pts:
        return 0.0
    if not math.isfinite(steady_rate_hr) or steady_rate_hr <= 0:
        return None
    pts = _cap_eta_float(max(0.0, start_pts))
    rate = _cap_eta_float(steady_rate_hr)
    for h in range(1, max_hours + 1):
        pts += rate
        if not math.isfinite(pts) or pts >= target_pts:
            return float(h)
        reinvest_boost = min(0.045, 30.0 / max(180.0, pts))
        rate = _cap_eta_float(rate * (1.0 + reinvest_boost))
    return None


def load_rank_history(history_path: Optional[Path]) -> list[dict[str, Any]]:
    """Load persisted rank evolution samples safely."""
    if history_path is None or not history_path.exists():
        return []
    try:
        raw = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [
        game_data.sanitize_json_tree(item, max_int=_MAX_JSON_SAFE_INT, nonfinite_float=None)
        for item in raw
        if isinstance(item, Mapping)
    ]


def record_rank_snapshot(
    history_path: Optional[Path],
    state: GameState,
    *,
    max_entries: int = 240,
    min_interval_seconds: float = 300.0,
) -> list[dict[str, Any]]:
    """Append a deduplicated rank/points observation to `history_path` and return all samples."""
    samples = load_rank_history(history_path)
    est = estimate_account_points(state)
    econ = estimate_hourly_economy(state)
    now_ts = _safe_finite_float(state.timestamp, 0.0)
    if now_ts <= 0:
        now_ts = time.time()

    srv_pts = _safe_nonneg_int(state.account_points, 0) if state.account_points is not None else None
    if srv_pts is not None and srv_pts > _MAX_JSON_SAFE_INT:
        srv_pts = _MAX_JSON_SAFE_INT
    srv_rank = _safe_optional_finite_int(state.account_rank)
    if srv_rank is not None:
        srv_rank = max(0, min(_MAX_JSON_SAFE_INT, srv_rank))
    entry = {
        "ts": round(now_ts, 3),
        "server_points": srv_pts,
        "server_rank": srv_rank,
        "estimated_points": est["estimated_points"],
        "pts_per_hr": econ["pts_per_hr_steady"],
        "spice_value": est["spice_value"],
        "planets": len(state.planets),
    }

    should_append = False
    if not samples:
        should_append = True
    else:
        last = samples[-1]
        last_ts = _safe_optional_finite_float(last.get("ts"))
        last_est = _safe_optional_finite_float(last.get("estimated_points"))
        if last_ts is None or last_est is None:
            should_append = True
        else:
            elapsed = abs(entry["ts"] - last_ts)
            changed_server = (
                (entry["server_points"] is not None and entry["server_points"] != last.get("server_points"))
                or (entry["server_rank"] is not None and entry["server_rank"] != last.get("server_rank"))
            )
            changed_est = int(round(entry["estimated_points"])) != int(round(last_est))
            if changed_server or changed_est or elapsed >= min_interval_seconds:
                should_append = True

    if should_append:
        samples.append(entry)
        if len(samples) > max_entries:
            samples = samples[-max_entries:]
        if history_path is not None:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            safe_samples = game_data.sanitize_json_tree(
                samples, max_int=_MAX_JSON_SAFE_INT, nonfinite_float=None
            )
            history_path.write_text(
                json.dumps(safe_samples, indent=2, allow_nan=False), encoding="utf-8"
            )

    return samples


def build_ranking_evolution(
    state: GameState,
    history: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build current rank metrics, Attack Zone progress, forecasts, and observed trend."""
    est = estimate_account_points(state)
    econ = estimate_hourly_economy(state)
    est_pts = _safe_big_float(est["estimated_points"])
    server_pts = (
        min(_MAX_JSON_SAFE_INT, _safe_nonneg_int(state.account_points, 0))
        if state.account_points is not None
        else None
    )
    server_rank = _safe_optional_finite_int(state.account_rank)
    if server_rank is not None:
        server_rank = max(0, min(_MAX_JSON_SAFE_INT, server_rank))
    baseline_pts = _cap_eta_float(max(_safe_big_float(server_pts or 0), est_pts))

    az = attack_zone_for_points(baseline_pts)
    rate_hr = _cap_eta_float(_safe_finite_float(econ["pts_per_hr_steady"], 0.0))
    newbie_rem = _cap_eta_float(max(0.0, NEWBIE_PROTECTION_POINTS - baseline_pts))

    horizon_defs = (
        ("1h", "+1 Hour", 1),
        ("6h", "+6 Hours", 6),
        ("24h", "+24 Hours", 24),
        ("72h", "+3 Days", 72),
        ("168h", "+7 Days", 168),
    )
    horizons: list[dict[str, Any]] = []
    for key, label, hours in horizon_defs:
        steady_total = round(_cap_eta_float(baseline_pts + rate_hr * hours), 1)
        compound_total = round(_cap_eta_float(_simulate_compound_points(baseline_pts, rate_hr, hours)), 1)
        horizons.append(
            {
                "key": key,
                "label": label,
                "hours": hours,
                "steady_points": steady_total,
                "steady_delta": round(_cap_eta_float(steady_total - baseline_pts, allow_negative=True), 1),
                "steady_az": attack_zone_for_points(steady_total)["label"],
                "compound_points": compound_total,
                "compound_delta": round(_cap_eta_float(compound_total - baseline_pts, allow_negative=True), 1),
                "compound_az": attack_zone_for_points(compound_total)["label"],
            }
        )

    milestone_targets = [
        ("AZ 2 Entry", 1_000),
        ("AZ 3 Entry", 5_000),
        ("Newbie Shield End", NEWBIE_PROTECTION_POINTS),
        ("AZ 4 Entry", 25_000),
        ("AZ 5 Entry", 50_000),
        ("AZ 6 Entry", 100_000),
        ("AZ 7 Entry", 250_000),
        ("AZ 8 Entry", 500_000),
        ("AZ 9 Entry", 1_000_000),
    ]
    upcoming = [(name, target) for name, target in milestone_targets if target > baseline_pts]
    if not upcoming:
        nxt_zone_pts = az.get("next_zone_pts")
        if nxt_zone_pts:
            upcoming = [(f"{az['next_zone']} Entry", int(nxt_zone_pts))]

    milestones: list[dict[str, Any]] = []
    for name, target in upcoming[:4]:
        missing_pts = round(_cap_eta_float(max(0.0, target - baseline_pts)), 1)
        steady_h = _cap_eta_float(missing_pts / rate_hr) if rate_hr > 0 else None
        comp_h = _solve_compound_eta_hours(baseline_pts, rate_hr, float(target))
        milestones.append(
            {
                "label": name,
                "target_points": target,
                "missing_points": missing_pts,
                "steady_eta": _format_eta(steady_h),
                "compound_eta": _format_eta(comp_h),
            }
        )

    samples = [
        game_data.sanitize_json_tree(s, max_int=_MAX_JSON_SAFE_INT, nonfinite_float=None)
        for s in (history or [])
        if isinstance(s, Mapping)
    ]
    recent_samples = samples[-20:]
    observed_delta_pts: Optional[float] = None
    observed_delta_rank: Optional[int] = None
    observed_rate_hr: Optional[float] = None
    elapsed_hours = 0.0
    if len(samples) >= 2:
        first, last = samples[0], samples[-1]
        dt_sec = _cap_eta_float(
            _safe_finite_float(last.get("ts", 0.0), 0.0)
            - _safe_finite_float(first.get("ts", 0.0), 0.0),
            allow_negative=True,
        )
        elapsed_hours = _cap_eta_float(max(0.0, dt_sec / 3600.0))
        p0 = _safe_optional_finite_float(first.get("server_points"))
        p1 = _safe_optional_finite_float(last.get("server_points"))
        if p0 is not None and p1 is not None and p1 != p0:
            raw_delta = p1 - p0
        else:
            raw_delta = _safe_finite_float(last.get("estimated_points", 0.0), 0.0) - _safe_finite_float(
                first.get("estimated_points", 0.0), 0.0
            )
        observed_delta_pts = round(_cap_eta_float(raw_delta, allow_negative=True), 1)
        if elapsed_hours >= 0.05 and observed_delta_pts is not None and math.isfinite(observed_delta_pts):
            rate_val = observed_delta_pts / elapsed_hours
            observed_rate_hr = round(_cap_eta_float(rate_val, allow_negative=True), 2) if math.isfinite(rate_val) else None
        r0 = _safe_optional_finite_int(first.get("server_rank"))
        r1 = _safe_optional_finite_int(last.get("server_rank"))
        if r0 is not None and r1 is not None:
            # Negative means rank number dropped (e.g. #420 -> #393 = climbed +27 spots)
            observed_delta_rank = max(-_MAX_JSON_SAFE_INT, min(_MAX_JSON_SAFE_INT, r0 - r1))

    return {
        "current": {
            "server_points": server_pts,
            "server_rank": server_rank,
            "baseline_points": round(baseline_pts, 1),
            **est,
            "attack_zone": az,
            "newbie_protected": baseline_pts < NEWBIE_PROTECTION_POINTS,
            "newbie_remaining_pts": round(newbie_rem, 1),
            "newbie_eta_steady": _format_eta(_cap_eta_float(newbie_rem / rate_hr) if rate_hr > 0 else None),
            "newbie_eta_compound": _format_eta(
                _solve_compound_eta_hours(baseline_pts, rate_hr, float(NEWBIE_PROTECTION_POINTS))
            ),
        },
        "rates": econ,
        "horizons": horizons,
        "milestones": milestones,
        "observed_trend": {
            "sample_count": len(samples),
            "elapsed_hours": round(_cap_eta_float(elapsed_hours), 2),
            "delta_points": observed_delta_pts,
            "rank_climbed": observed_delta_rank,
            "observed_pts_per_hr": observed_rate_hr,
            "samples": recent_samples,
        },
    }


def _res_dict(res: Resources) -> dict[str, int]:
    return {
        "pig_iron": _cap_json_int(max(0, _safe_eta_int(getattr(res, "pig_iron", 0), 0))),
        "metal": _cap_json_int(max(0, _safe_eta_int(getattr(res, "metal", 0), 0))),
        "kryptonite": _cap_json_int(max(0, _safe_eta_int(getattr(res, "kryptonite", 0), 0))),
        "spice": _cap_json_int(max(0, _safe_eta_int(getattr(res, "spice", 0), 0))),
    }


def _building_category(building_id: int) -> str:
    if building_id in (1, 2, 3, 4):
        return "mines"
    if building_id in (5, 6):
        return "energy"
    if building_id in (11, 12, 13, 14):
        return "storage"
    if building_id in (7, 8, 9, 10, 17):
        return "infra"
    return "special"


def _format_duration(seconds: Optional[float]) -> str:
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


def _compute_resource_eta(
    missing: dict[str, int],
    rates_hr: dict[str, float],
) -> tuple[Optional[float], Optional[str]]:
    """Return `(wait_hours, bottleneck_resource)` to accumulate `missing` resources."""
    need_keys = [
        rk
        for rk in ("pig_iron", "metal", "kryptonite", "spice")
        if _safe_eta_int(missing.get(rk, 0), 0, pos_inf_value=_MAX_ETA_INT) > 0
    ]
    if not need_keys:
        return 0.0, None

    max_hours = 0.0
    bottleneck: Optional[str] = None
    for rk in need_keys:
        raw_rate = rates_hr.get(rk, 0.0)
        try:
            rate = float(0.0 if raw_rate is None else raw_rate)
        except Exception:
            return None, rk
        if not math.isfinite(rate) or rate <= 0.0:
            return None, rk
        need_val = _safe_eta_int(missing.get(rk, 0), 0, pos_inf_value=_MAX_ETA_INT)
        hrs = _safe_big_mul_div(need_val, 1.0, rate)
        if bottleneck is None or hrs > max_hours:
            max_hours = hrs
            bottleneck = rk
    return max_hours, bottleneck


def _research_level_stats(info: dict[str, Any], level: int) -> dict[str, Any]:
    levels = info.get("levels") or {}
    row = levels.get(level)
    if row:
        return dict(row)
    if levels:
        max_lvl = max(int(k) for k in levels.keys())
        base_row = levels.get(max_lvl) or {}
        exp = min(max(0, level - max_lvl), 600)
        scale = 2.0 ** exp
        base_cost = base_row.get("cost") or {}
        return {
            "cost": {
                rk: game_data.safe_scale_round_int(base_cost.get(rk, 0), scale)
                for rk in ("pig_iron", "metal", "kryptonite", "spice")
            },
            "base_time_s": min(_MAX_ETA_FLOAT, float(base_row.get("base_time_s", 0.0) or 0.0) * scale),
        }
    return {"cost": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}, "base_time_s": 0.0}


def build_buildings_eta(
    state: GameState,
    planet: Optional[PlanetState] = None,
    *,
    horizon_count: int = 4,
) -> dict[str, Any]:
    """Compute next-level costs, missing resources, build times, and reach ETAs for `b1`–`b21` and `r1`–`r15`."""
    target = planet or state.active_planet()
    if target is None:
        return {
            "planet": None,
            "planet_economy": {},
            "buildings": [],
            "researches": [],
        }

    safe_horizon = min(100, max(0, _safe_nonneg_int(horizon_count, 4)))
    p_econ = estimate_planet_hourly_economy(state, target)
    rates_hr = {
        "pig_iron": float(p_econ["pig_iron_per_hr"]),
        "metal": float(p_econ["metal_per_hr"]),
        "kryptonite": float(p_econ["kryptonite_per_hr"]),
        "spice": float(p_econ["spice_per_hr"]),
    }
    res_have = _res_dict(target.resources)
    speedup = float(p_econ["speedup_factor"])
    dev_lvl = max(0, target.building_level(7))
    lab_lvl = max(0, target.building_level(8))
    micro_lvl = max(0, target.building_level(17))
    b_divisor = max(1.0, float(min(dev_lvl, 1_000_000) + 1) * float(2 ** min(micro_lvl, 60)) * speedup)
    r_divisor = max(1.0, float(min(lab_lvl, 1_000_000) + 1) * speedup)

    is_active = target.planet_id == state.active_planet_id
    queued_bids: dict[int, int] = {}
    if is_active:
        for q in state.active_queues:
            if getattr(q, "category", None) == "building" and getattr(q, "building_id", None) is not None:
                q_bid = game_data.safe_optional_int(q.building_id, None)
                if q_bid is not None:
                    queued_bids[q_bid] = queued_bids.get(q_bid, 0) + 1

    b_offers: dict[int, Any] = {}
    for o in target.building_offers or ():
        o_bid = game_data.safe_optional_int(getattr(o, "building_id", None), None)
        if o_bid is not None:
            b_offers[o_bid] = o
    r_offers = {
        rk: o
        for o in (state.research_offers or ())
        if (rk := game_data.safe_key(getattr(o, "research_key", "") or ""))
    }

    buildings_rows: list[dict[str, Any]] = []
    for bid in sorted(game_data.BUILDING_BY_ID.keys()):
        info = game_data.BUILDING_BY_ID[bid]
        cur_lvl = target.building_level(bid)
        nxt_lvl = cur_lvl + 1
        offer = b_offers.get(bid)
        stats_next = game_data.building_level_stats(bid, nxt_lvl)
        raw_cost = (
            _res_dict(offer.cost)
            if (
                offer is not None
                and not offer.cost.is_zero()
                and _safe_eta_int(getattr(offer, "target_level", None), -1) == nxt_lvl
            )
            else (stats_next.get("cost") or game_data.building_cost(bid, nxt_lvl))
        )
        cost = {
            rk: _safe_nonneg_int(raw_cost.get(rk, 0), 0)
            for rk in ("pig_iron", "metal", "kryptonite", "spice")
        }
        missing = {rk: max(0, cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cost}
        missing_total = sum(missing.values())
        can_afford = missing_total == 0
        wait_h, bottleneck = _compute_resource_eta(missing, rates_hr)
        if wait_h is not None:
            wait_h = _cap_eta_float(wait_h)
        base_time_s = _safe_finite_float(stats_next.get("base_time_s", 0.0), 0.0)
        build_s = _cap_eta_float(base_time_s / b_divisor)
        reach_h = _cap_eta_float(wait_h + build_s / 3600.0) if wait_h is not None else None
        unmet = game_data.missing_prerequisites(
            "buildings",
            bid,
            buildings=target.buildings,
            researches=state.researches,
        )

        # Cumulative L+1..L+horizon_count forecast assuming sequential investment
        horizons: list[dict[str, Any]] = []
        cum_cost = {rk: 0 for rk in ("pig_iron", "metal", "kryptonite", "spice")}
        cum_build_s = 0.0
        sim_dev = dev_lvl
        sim_micro = micro_lvl
        for step_idx in range(safe_horizon):
            step_lvl = nxt_lvl + step_idx
            step_stats = game_data.building_level_stats(bid, step_lvl)
            step_raw_cost = (
                cost
                if step_idx == 0
                else (step_stats.get("cost") or game_data.building_cost(bid, step_lvl))
            )
            for rk in cum_cost:
                cum_cost[rk] += _safe_nonneg_int(step_raw_cost.get(rk, 0), 0)
            cum_missing = {rk: max(0, cum_cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cum_cost}
            step_wait_h, step_bn = _compute_resource_eta(cum_missing, rates_hr)
            if step_wait_h is not None:
                step_wait_h = _cap_eta_float(step_wait_h)
            step_div = max(
                1.0,
                float(min(max(0, sim_dev), 1_000_000) + 1) * float(2 ** min(max(0, sim_micro), 60)) * speedup,
            )
            step_base_s = _safe_finite_float(step_stats.get("base_time_s", 0.0), 0.0)
            step_b_s = _cap_eta_float(step_base_s / step_div)
            cum_build_s = _cap_eta_float(cum_build_s + step_b_s)
            if bid == 7:
                sim_dev = step_lvl
            elif bid == 17:
                sim_micro = step_lvl
            step_reach_h = _cap_eta_float(step_wait_h + cum_build_s / 3600.0) if step_wait_h is not None else None
            horizons.append(
                {
                    "level": _cap_json_int(step_lvl),
                    "cost": {rk: _cap_json_int(_safe_nonneg_int(step_raw_cost.get(rk, 0), 0)) for rk in cum_cost},
                    "cum_cost": {rk: _cap_json_int(v) for rk, v in cum_cost.items()},
                    "build_time_text": _format_duration(step_b_s),
                    "wait_eta": _format_eta(step_wait_h),
                    "reach_eta": _format_eta(step_reach_h),
                    "bottleneck": step_bn,
                }
            )

        rate_key = f"resource{bid}" if bid in (1, 2, 3, 4) else None
        rate_pct = p_econ["rates_pct"].get(rate_key, 100) if rate_key else None

        buildings_rows.append(
            {
                "building_id": bid,
                "key": f"b{bid}",
                "slug": str(info["slug"]),
                "name": _safe_eta_str(target.building_names.get(f"b{bid}")) or str(info["name"]),
                "category": _building_category(bid),
                "asteroid_ok": bool(info.get("asteroid_ok", False)),
                "current_level": _cap_json_int(cur_lvl),
                "next_level": _cap_json_int(nxt_lvl),
                "queued_count": queued_bids.get(bid, 0),
                "energy_delta": _cap_json_int(game_data.building_energy_delta(bid, nxt_lvl)),
                "cost": {rk: _cap_json_int(v) for rk, v in cost.items()},
                "missing": {rk: _cap_json_int(v) for rk, v in missing.items()},
                "missing_total": _cap_json_int(missing_total),
                "can_afford": can_afford,
                "can_build_live": bool(offer.can_build and can_afford) if offer is not None else False,
                "offer_visible": offer is not None,
                "unmet_prerequisites": [str(u) for u in unmet],
                "wait_hours": round(wait_h, 3) if wait_h is not None else None,
                "wait_eta": _format_eta(wait_h),
                "build_time_seconds": round(build_s, 1),
                "build_time_text": _format_duration(build_s),
                "reach_hours": round(reach_h, 3) if reach_h is not None else None,
                "reach_eta": _format_eta(reach_h),
                "bottleneck": bottleneck,
                "economy_rate_key": rate_key,
                "economy_rate_pct": rate_pct,
                "horizons": horizons,
            }
        )

    research_rows: list[dict[str, Any]] = []
    r_keys = sorted(
        game_data.RESEARCH_BY_ID.keys(),
        key=lambda rk: int(rk[1:]) if (rk[1:].isascii() and rk[1:].isdigit()) else 999,
    )
    for rkey in r_keys:
        info = game_data.RESEARCH_BY_ID[rkey]
        rid_num = int(rkey[1:]) if (rkey[1:].isascii() and rkey[1:].isdigit()) else None
        cur_lvl = _safe_nonneg_int((state.researches or {}).get(rkey, 0), 0)
        nxt_lvl = cur_lvl + 1
        offer = r_offers.get(rkey)
        stats_next = _research_level_stats(info, nxt_lvl)
        raw_cost = (
            _res_dict(offer.cost)
            if (
                offer is not None
                and not offer.cost.is_zero()
                and _safe_eta_int(getattr(offer, "target_level", None), -1) == nxt_lvl
            )
            else (stats_next.get("cost") or {})
        )
        cost = {
            rk: _safe_nonneg_int(raw_cost.get(rk, 0), 0)
            for rk in ("pig_iron", "metal", "kryptonite", "spice")
        }
        missing = {rk: max(0, cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cost}
        missing_total = sum(missing.values())
        can_afford = missing_total == 0
        wait_h, bottleneck = _compute_resource_eta(missing, rates_hr)
        if wait_h is not None:
            wait_h = _cap_eta_float(wait_h)
        res_base_s = _safe_finite_float(stats_next.get("base_time_s", 0.0), 0.0)
        res_s = _cap_eta_float(res_base_s / r_divisor)
        reach_h = _cap_eta_float(wait_h + res_s / 3600.0) if wait_h is not None else None
        unmet = game_data.missing_prerequisites(
            "research",
            rkey,
            buildings=target.buildings,
            researches=state.researches,
        )

        r_horizons: list[dict[str, Any]] = []
        cum_cost = {rk: 0 for rk in ("pig_iron", "metal", "kryptonite", "spice")}
        cum_res_s = 0.0
        for step_idx in range(min(3, safe_horizon)):
            step_lvl = nxt_lvl + step_idx
            step_stats = _research_level_stats(info, step_lvl)
            step_raw = cost if step_idx == 0 else (step_stats.get("cost") or {})
            for rk in cum_cost:
                cum_cost[rk] += _safe_nonneg_int(step_raw.get(rk, 0), 0)
            cum_missing = {rk: max(0, cum_cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cum_cost}
            step_wait_h, step_bn = _compute_resource_eta(cum_missing, rates_hr)
            if step_wait_h is not None:
                step_wait_h = _cap_eta_float(step_wait_h)
            step_base_s = _safe_finite_float(step_stats.get("base_time_s", 0.0), 0.0)
            step_s = _cap_eta_float(step_base_s / r_divisor)
            cum_res_s = _cap_eta_float(cum_res_s + step_s)
            step_reach_h = _cap_eta_float(step_wait_h + cum_res_s / 3600.0) if step_wait_h is not None else None
            r_horizons.append(
                {
                    "level": _cap_json_int(step_lvl),
                    "cost": {rk: _cap_json_int(_safe_nonneg_int(step_raw.get(rk, 0), 0)) for rk in cum_cost},
                    "build_time_text": _format_duration(step_s),
                    "wait_eta": _format_eta(step_wait_h),
                    "reach_eta": _format_eta(step_reach_h),
                    "bottleneck": step_bn,
                }
            )

        research_rows.append(
            {
                "research_id": rid_num,
                "key": rkey,
                "slug": str(info["slug"]),
                "name": _safe_eta_str(state.research_names.get(rkey)) or str(info["name"]),
                "current_level": _cap_json_int(cur_lvl),
                "next_level": _cap_json_int(nxt_lvl),
                "cost": {rk: _cap_json_int(v) for rk, v in cost.items()},
                "missing": {rk: _cap_json_int(v) for rk, v in missing.items()},
                "missing_total": _cap_json_int(missing_total),
                "can_afford": can_afford,
                "can_research_live": bool(offer.can_research and can_afford) if offer is not None else False,
                "active": bool(offer.active) if offer is not None else False,
                "offer_visible": offer is not None,
                "unmet_prerequisites": [str(u) for u in unmet],
                "wait_hours": round(wait_h, 3) if wait_h is not None else None,
                "wait_eta": _format_eta(wait_h),
                "build_time_seconds": round(res_s, 1),
                "build_time_text": _format_duration(res_s),
                "reach_hours": round(reach_h, 3) if reach_h is not None else None,
                "reach_eta": _format_eta(reach_h),
                "bottleneck": bottleneck,
                "horizons": r_horizons,
            }
        )

    return {
        "planet": {
            "planet_id": _cap_json_int(_safe_eta_int(target.planet_id, 0)),
            "name": _safe_eta_str(target.name),
            "coords": _safe_eta_str(target.coords),
            "planet_type": normalize_planet_type(target),
            "temperature": _cap_json_int(_safe_eta_int(target.temperature, 0)),
            "fields_used": _cap_json_int(_safe_nonneg_int(target.fields_used, 0)),
            "fields_max": _cap_json_int(_safe_nonneg_int(target.fields_max, 0)),
            "resources": res_have,
            "dev_center_level": _cap_json_int(dev_lvl),
            "research_lab_level": _cap_json_int(lab_lvl),
            "microsystem_level": _cap_json_int(micro_lvl),
            "building_time_divisor": round(_cap_eta_float(b_divisor), 2),
            "research_time_divisor": round(_cap_eta_float(r_divisor), 2),
        },
        "planet_economy": p_econ,
        "buildings": buildings_rows,
        "researches": research_rows,
    }


def build_combat_overview(
    state: GameState,
    planet: Optional[PlanetState] = None,
    *,
    safety_margin: float = 1.4,
) -> dict[str, Any]:
    """Build combat readiness, AZ bounds, stationed ships/defense, and Escape flight data."""
    target = planet or state.active_planet()
    est = estimate_account_points(state)
    srv_pts = _safe_nonneg_int(state.account_points, 0) if state.account_points is not None else 0
    baseline_pts = max(_safe_big_float(srv_pts), _safe_big_float(est["estimated_points"]))
    az = attack_zone_for_points(baseline_pts)

    r_map = state.researches if isinstance(state.researches, Mapping) else {}
    techs = {
        "weapons": _cap_json_int(_safe_nonneg_int(r_map.get("r8", 0), 0)),
        "shield": _cap_json_int(_safe_nonneg_int(r_map.get("r9", 0), 0)),
        "defense": _cap_json_int(_safe_nonneg_int(r_map.get("r10", 0), 0)),
        "spy": _cap_json_int(_safe_nonneg_int(r_map.get("r7", 0), 0)),
        "targeting": _cap_json_int(_safe_nonneg_int(r_map.get("r11", 0), 0)),
        "particle": _cap_json_int(_safe_nonneg_int(r_map.get("r12", 0), 0)),
        "rocket": _cap_json_int(_safe_nonneg_int(r_map.get("r4", 0), 0)),
        "diffusion": _cap_json_int(_safe_nonneg_int(r_map.get("r5", 0), 0)),
        "warp": _cap_json_int(_safe_nonneg_int(r_map.get("r6", 0), 0)),
    }

    atk_mult = 1.0 + 0.10 * min(100_000, techs["weapons"])
    shd_mult = 1.0 + 0.10 * min(100_000, techs["shield"])
    def_mult = 1.0 + 0.10 * min(100_000, techs["defense"])

    ship_catalog: list[dict[str, Any]] = []
    defense_catalog: list[dict[str, Any]] = []
    total_fleet_power = 0.0
    total_defense_power = 0.0
    total_cargo = 0

    for skey in sorted(
        SHIP_SLUG_BY_KEY.keys(),
        key=lambda k: int(k[1:]) if (k[1:].isascii() and k[1:].isdigit()) else 99,
    ):
        slug = SHIP_SLUG_BY_KEY[skey]
        unit = game_data.SHIPS.get(slug) or {}
        stats = unit.get("stats") or {}
        owned = _safe_nonneg_int((target.ships or {}).get(skey, 0), 0) if target else 0
        owned_f = _safe_big_float(owned, cap=1e100)
        atk = float(stats.get("attack", 0) or 0) * atk_mult
        shd = float(stats.get("shield", 0) or 0) * shd_mult
        hul = float(stats.get("hull", 0) or 0) * def_mult
        power = (atk + shd + hul * 0.25) * owned_f
        cargo = int(stats.get("cargo", 0) or 0)
        total_fleet_power += power
        total_cargo += cargo * owned
        ship_catalog.append(
            {
                "key": skey,
                "unit_id": int(skey[1:]) if (skey[1:].isascii() and skey[1:].isdigit()) else 0,
                "slug": slug,
                "name": str(unit.get("name", slug)),
                "owned": _cap_json_int(owned),
                "attack": int(stats.get("attack", 0) or 0),
                "shield": int(stats.get("shield", 0) or 0),
                "hull": int(stats.get("hull", 0) or 0),
                "cargo": cargo,
                "speed": int(stats.get("speed", 0) or 0),
                "base_cost": unit.get("base_cost") or {},
            }
        )

    for dkey in sorted(
        DEFENSE_SLUG_BY_KEY.keys(),
        key=lambda k: int(k[1:]) if (k[1:].isascii() and k[1:].isdigit()) else 99,
    ):
        slug = DEFENSE_SLUG_BY_KEY[dkey]
        unit = game_data.DEFENSES.get(slug) or {}
        stats = unit.get("stats") or {}
        owned = _safe_nonneg_int((target.defense or {}).get(dkey, 0), 0) if target else 0
        owned_f = _safe_big_float(owned, cap=1e100)
        atk = float(stats.get("attack", 0) or 0) * atk_mult
        shd = float(stats.get("shield", 0) or 0) * shd_mult
        hul = float(stats.get("hull", 0) or 0) * def_mult
        total_defense_power += (atk + shd + hul * 0.25) * owned_f
        defense_catalog.append(
            {
                "key": dkey,
                "unit_id": int(dkey[1:]) if (dkey[1:].isascii() and dkey[1:].isdigit()) else 0,
                "slug": slug,
                "name": str(unit.get("name", slug)),
                "owned": _cap_json_int(owned),
                "attack": int(stats.get("attack", 0) or 0),
                "shield": int(stats.get("shield", 0) or 0),
                "hull": int(stats.get("hull", 0) or 0),
                "base_cost": unit.get("base_cost") or {},
            }
        )

    ship_offers: list[dict[str, Any]] = []
    if target is not None:
        for o in target.ship_offers or ():
            ship_offers.append(
                {
                    "unit_type": _safe_eta_str(o.unit_type),
                    "unit_id": _cap_json_int(_safe_nonneg_int(o.unit_id, 0)),
                    "name": _safe_eta_str(o.name),
                    "owned": _cap_json_int(_safe_nonneg_int(o.owned, 0)),
                    "cost": _res_dict(o.cost),
                    "can_build": o.can_build,
                    "status_text": _safe_eta_str(o.status_text),
                }
            )

    escape_plan_dict: Optional[dict[str, Any]] = None
    if target is not None:
        other_coords = next(
            (p.coords for p in state.planets if p.planet_id != target.planet_id),
            target.coords,
        )
        esc = plan_escape_flight(target, target_coords=other_coords)
        esc_cargo = _res_dict(esc.cargo)
        escape_plan_dict = {
            "feasible": esc.feasible,
            "target_coords": _safe_eta_str(esc.target_coords),
            "ships": {
                game_data.safe_key(k): _cap_json_int(_safe_nonneg_int(v, 0))
                for k, v in (esc.ships or {}).items()
            },
            "cargo": esc_cargo,
            "total_cargo": _cap_json_int(sum(esc_cargo.values())),
            "total_capacity": _cap_json_int(_safe_nonneg_int(esc.total_capacity, 0)),
            "fuel_reserved_spice": _cap_json_int(_safe_nonneg_int(esc.fuel_reserved_spice, 0)),
            "duration_hint": _safe_eta_str(esc.duration_hint),
            "spice_saved": esc_cargo.get("spice", 0),
            "reason": _safe_eta_str(esc.reason),
        }

    raw_margin = _safe_finite_float(safety_margin, 1.4)
    eff_margin = raw_margin if raw_margin >= 0.0 else 1.4
    return {
        "planet_id": _cap_json_int(_safe_eta_int(target.planet_id, 0)) if target else None,
        "planet_coords": _safe_eta_str(target.coords) if target else None,
        "weapons_factory_level": _cap_json_int(target.building_level(9)) if target else 0,
        "fleet_base_level": _cap_json_int(target.building_level(10)) if target else 0,
        "galaxy_scanner_level": _cap_json_int(target.building_level(19)) if target else 0,
        "combat_techs": techs,
        "attack_zone": az,
        "newbie_protected": baseline_pts < NEWBIE_PROTECTION_POINTS,
        "safety_margin": eff_margin,
        "bash_rule": "Max 3 attacks / 24h on active planets (exempt: yellow inactives & Spacia)",
        "az_rule": "Eligible targets within AZ ±2 (Top 50 bypass AZ outside Newbie Shield)",
        "combat_model": "1:1 zero-rapidfire combat; attacker retreats unless ratio >= safety margin",
        "stationed_fleet_power": round(total_fleet_power, 1),
        "stationed_defense_power": round(total_defense_power, 1),
        "total_cargo_capacity": _cap_json_int(total_cargo),
        "ships": ship_catalog,
        "defenses": defense_catalog,
        "ship_offers": ship_offers,
        "escape_plan": escape_plan_dict,
    }


def simulate_combat_matchup(
    state: GameState,
    *,
    attacker_ships: dict[str, int],
    defender_ships: Optional[dict[str, int]] = None,
    defender_defense: Optional[dict[str, int]] = None,
    defender_researches: Optional[dict[str, int]] = None,
    target_resources: Optional[dict[str, int]] = None,
    target_coords: str = "01:001:01",
    safety_margin: float = 1.4,
) -> dict[str, Any]:
    """Run 1:1 zero-rapidfire combat simulator with attacker's current techs and safety threshold."""
    atk_map = attacker_ships if isinstance(attacker_ships, Mapping) else {}
    def_s_map = defender_ships if isinstance(defender_ships, Mapping) else {}
    def_d_map = defender_defense if isinstance(defender_defense, Mapping) else {}
    def_r_map = defender_researches if isinstance(defender_researches, Mapping) else {}
    clean_atk = {
        game_data.safe_key(k): cnt for k, v in atk_map.items() if (cnt := _safe_nonneg_int(v, 0)) > 0
    }
    clean_def_ships = {
        game_data.safe_key(k): cnt for k, v in def_s_map.items() if (cnt := _safe_nonneg_int(v, 0)) > 0
    }
    clean_def_defs = {
        game_data.safe_key(k): cnt for k, v in def_d_map.items() if (cnt := _safe_nonneg_int(v, 0)) > 0
    }
    clean_def_researches = {
        game_data.safe_key(k): cnt for k, v in def_r_map.items() if (cnt := _safe_nonneg_int(v, 0)) > 0
    }
    res_map = target_resources if isinstance(target_resources, Mapping) else {}
    report = SpyReport(
        target_coords=target_coords,
        owner="Simulated Target",
        resources=Resources(
            pig_iron=_safe_nonneg_int(res_map.get("pig_iron", 0), 0),
            metal=_safe_nonneg_int(res_map.get("metal", 0), 0),
            kryptonite=_safe_nonneg_int(res_map.get("kryptonite", 0), 0),
            spice=_safe_nonneg_int(res_map.get("spice", 0), 0),
        ),
        fleet=clean_def_ships,
        defense=clean_def_defs,
    )
    raw_margin = _safe_finite_float(safety_margin, 1.4)
    eff_margin = max(1.0, raw_margin if raw_margin > 0.0 else 1.4)
    est = simulate_raid_outcome(
        clean_atk,
        report,
        attacker_researches=state.researches,
        defender_researches=clean_def_researches,
        safety_margin=eff_margin,
    )
    plunder_map = _res_dict(est.expected_loot)
    return {
        "safe_to_attack": est.safe_to_attack,
        "win_ratio": est.power_ratio,
        "safety_margin": eff_margin,
        "attacker_power": est.attacker_power,
        "defender_power": est.defender_power,
        "defender_researches": {k: _cap_json_int(v) for k, v in clean_def_researches.items()},
        "stealth_first_strike_power": est.stealth_first_strike_power,
        "cargo_capacity": _cap_json_int(_safe_nonneg_int(est.cargo_capacity, 0)),
        "loot_spice_equivalent": est.loot_spice_equivalent,
        "expected_plunder": plunder_map,
        "total_plunder": _cap_json_int(sum(plunder_map.values())),
        "notes": est.reason,
    }


_ETA_RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
_INSTANT_ACTION_KINDS: frozenset[str] = frozenset(
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


def _action_get(action: Any, key: str, default: Any = None) -> Any:
    if isinstance(action, Mapping):
        return action.get(key, default)
    return getattr(action, key, default)


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


_SCI_NUM_RE = re.compile(r"^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))[eE]([+-]?\d+)$", re.ASCII)
_PLAIN_NUM_RE = re.compile(r"^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))$", re.ASCII)


def _safe_eta_int(raw: Any, default: int = 0, *, pos_inf_value: Optional[int] = None) -> int:
    if isinstance(raw, os.PathLike):
        try:
            raw = os.fspath(raw)
        except Exception:
            return default
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if math.isfinite(raw):
            return int(raw)
        if pos_inf_value is not None and raw > 0.0:
            return pos_inf_value
        return default
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
            if nz_int:
                adjusted = len(nz_int) - 1 + exp
            else:
                lz = len(frac_digits) - len(frac_digits.lstrip("0"))
                adjusted = -(lz + 1) + exp
            if adjusted > 5000:
                return -(10**5000) if sign == "-" else (10**5000)
            if adjusted < 0:
                return 0
        else:
            m_pl = _PLAIN_NUM_RE.match(s)
            if m_pl:
                sign, int_part, _, _ = m_pl.groups()
                nz_int = (int_part or "").lstrip("0")
                if len(nz_int) - 1 > 5000:
                    return -(10**5000) if sign == "-" else (10**5000)
        d = Decimal(s)
    except Exception:
        return default
    return _decimal_to_eta_int(d, default)


def _extract_resource_map(raw: Any) -> Optional[dict[str, int]]:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return {
            rk: max(0, _safe_eta_int(raw.get(rk, 0), 0, pos_inf_value=_MAX_ETA_INT))
            for rk in _ETA_RESOURCE_KEYS
        }
    if any(hasattr(raw, rk) for rk in _ETA_RESOURCE_KEYS):
        return {
            rk: max(0, _safe_eta_int(getattr(raw, rk, 0), 0, pos_inf_value=_MAX_ETA_INT))
            for rk in _ETA_RESOURCE_KEYS
        }
    return None


def _resolve_planet_for_eta(state: GameState, action: Any) -> Optional[PlanetState]:
    planets = tuple(getattr(state, "planets", ()) or ())
    if not planets:
        return None
    raw_pid = _action_get(action, "planet_id", None)
    if isinstance(raw_pid, os.PathLike):
        try:
            raw_pid = os.fspath(raw_pid)
        except Exception:
            return None
    if isinstance(raw_pid, (bytes, bytearray)):
        raw_pid = bytes(raw_pid).decode("utf-8", errors="replace")
    if isinstance(raw_pid, str):
        raw_pid = raw_pid.strip()
    raw_coords = _safe_eta_str(_action_get(action, "planet_coords", "")).strip()

    if raw_pid is not None and raw_pid != "":
        if isinstance(raw_pid, (bool, Mapping, list, tuple, set, frozenset)):
            return None
        if isinstance(raw_pid, float):
            if not math.isfinite(raw_pid) or not raw_pid.is_integer():
                return None
            try:
                pid = int(raw_pid)
            except Exception:
                return None
        elif isinstance(raw_pid, Decimal):
            if not _decimal_is_integer(raw_pid):
                return None
            max_pid_bits = max(
                (abs(_safe_eta_int(p.planet_id, 0)).bit_length() for p in planets),
                default=0,
            )
            if raw_pid != 0 and raw_pid.adjusted() > max(20, max_pid_bits):
                return None
            try:
                pid = int(raw_pid)
            except Exception:
                return None
        elif isinstance(raw_pid, int):
            pid = raw_pid
        elif isinstance(raw_pid, str):
            s = raw_pid.strip()
            if not s or not s.isascii():
                return None
            try:
                pid = int(s)
            except Exception:
                return None
        else:
            try:
                pid = int(raw_pid)
            except Exception:
                return None
        for planet in planets:
            p_pid = _safe_optional_finite_int(planet.planet_id)
            if p_pid is not None and p_pid == pid:
                return planet
        return None

    if raw_coords:
        for planet in planets:
            if _safe_eta_str(planet.coords).strip() == raw_coords:
                return planet
        return None

    return state.active_planet()


def _resolve_building_id(target_id: Any, target_name: Any) -> Optional[int]:
    if target_id is not None and target_id != "":
        if isinstance(target_id, bool):
            return None
        if isinstance(target_id, int):
            bid = target_id
        else:
            s = _safe_eta_str(target_id).strip()
            try:
                if s.lower().startswith("b") and len(s) <= 12 and s[1:].isascii() and s[1:].isdigit():
                    bid = int(s[1:])
                elif len(s) <= 12 and s.isascii() and s.isdigit():
                    bid = int(s)
                else:
                    return None
            except Exception:
                return None
        return bid if bid in game_data.BUILDING_BY_ID else None

    if target_name:
        tname = _safe_eta_str(target_name)
        if tname:
            info = game_data.resolve_unit("buildings", tname)
            if info and info.get("id") is not None:
                bid = int(info["id"])
                return bid if bid in game_data.BUILDING_BY_ID else None
    return None


def _resolve_research_key(target_id: Any, target_name: Any) -> Optional[str]:
    if target_id is not None and target_id != "":
        if isinstance(target_id, bool):
            return None
        if isinstance(target_id, int):
            rkey = f"r{target_id}" if 0 <= target_id <= 10_000 else ""
        else:
            s = _safe_eta_str(target_id).strip()
            try:
                if s.lower().startswith("r") and len(s) <= 12 and s[1:].isascii() and s[1:].isdigit():
                    rkey = f"r{int(s[1:])}"
                elif len(s) <= 12 and s.isascii() and s.isdigit():
                    rkey = f"r{int(s)}"
                else:
                    return None
            except Exception:
                return None
        return rkey if rkey in game_data.RESEARCH_BY_ID else None

    if target_name:
        tname = _safe_eta_str(target_name)
        if tname:
            info = game_data.resolve_unit("research", tname)
            if info and info.get("id") is not None:
                rkey = str(info["id"])
                return rkey if rkey in game_data.RESEARCH_BY_ID else None
    return None


def _resolve_unit_slug(
    category: str,
    prefix: str,
    slug_by_key: Mapping[str, str],
    catalog: Mapping[str, dict[str, Any]],
    target_id: Any,
    target_name: Any,
) -> Optional[str]:
    if target_id is not None and target_id != "":
        if isinstance(target_id, bool):
            return None
        if isinstance(target_id, int):
            return slug_by_key.get(f"{prefix}{target_id}") if 0 <= target_id <= 10_000 else None
        s = _safe_eta_str(target_id).strip()
        try:
            if s.lower().startswith(prefix) and len(s) <= 12 and s[1:].isascii() and s[1:].isdigit():
                return slug_by_key.get(f"{prefix}{int(s[1:])}")
            if len(s) <= 12 and s.isascii() and s.isdigit():
                return slug_by_key.get(f"{prefix}{int(s)}")
        except Exception:
            return None
        if s in catalog:
            return s
        return None

    if target_name:
        tname = _safe_eta_str(target_name)
        if tname:
            info = game_data.resolve_unit(category, tname)
            if info and info.get("slug") in catalog:
                return str(info["slug"])
    return None


def _round_seconds(seconds: float) -> float:
    if seconds <= 0.0 or not math.isfinite(seconds):
        return 0.0
    if seconds >= 0.01:
        return round(seconds, 2)
    return max(0.0001, round(seconds, 4))


def compute_action_eta(state: GameState, action: Any) -> dict[str, Any]:
    """Compute resource wait ETA, action duration, and total completion ETA for one planned action."""
    kind = _safe_eta_str(_action_get(action, "kind", "")).strip().upper()
    if kind in _INSTANT_ACTION_KINDS:
        return {
            "wait_hours": 0.0,
            "wait_seconds": 0.0,
            "wait_eta": "Ready",
            "duration_seconds": 0.0,
            "duration_text": "Instant",
            "total_hours": 0.0,
            "total_seconds": 0.0,
            "total_eta": "Ready",
            "eta_bottleneck": None,
        }

    planet = _resolve_planet_for_eta(state, action)
    if planet is not None:
        p_econ = estimate_planet_hourly_economy(state, planet)
        rates_hr = {
            "pig_iron": float(p_econ["pig_iron_per_hr"]),
            "metal": float(p_econ["metal_per_hr"]),
            "kryptonite": float(p_econ["kryptonite_per_hr"]),
            "spice": float(p_econ["spice_per_hr"]),
        }
        speedup = float(p_econ["speedup_factor"])
        dev_lvl = max(0, planet.building_level(7))
        lab_lvl = max(0, planet.building_level(8))
        wf_lvl = max(0, planet.building_level(9))
        micro_lvl = max(0, planet.building_level(17))
    else:
        rates_hr = {rk: 0.0 for rk in _ETA_RESOURCE_KEYS}
        speedup = _safe_speedup_factor(getattr(state, "speedup_factor", None))
        dev_lvl = 0
        lab_lvl = 0
        wf_lvl = 0
        micro_lvl = 0

    missing_map = _extract_resource_map(_action_get(action, "missing", None))
    if missing_map is None:
        cost_map = _extract_resource_map(_action_get(action, "cost", None))
        if cost_map is not None:
            if planet is not None:
                have = _res_dict(planet.resources)
                missing_map = {
                    rk: max(0, cost_map[rk] - int(have.get(rk, 0) or 0))
                    for rk in _ETA_RESOURCE_KEYS
                }
            else:
                missing_map = dict(cost_map)
        else:
            missing_map = {rk: 0 for rk in _ETA_RESOURCE_KEYS}

    wait_h_raw, bottleneck = _compute_resource_eta(missing_map, rates_hr)

    dur_s_raw = 0.0
    raw_tid = _action_get(action, "target_id", None)
    raw_tname = _action_get(action, "target_name", None)
    raw_tlvl = _action_get(action, "target_level", None)
    micro_mult = float(2 ** min(micro_lvl, 60))

    if kind in ("BUILD", "WAIT_RESOURCES"):
        bid = _resolve_building_id(raw_tid, raw_tname)
        tgt_lvl = max(0, _safe_eta_int(raw_tlvl, 0))
        if bid is not None and tgt_lvl > 0:
            stats = game_data.building_level_stats(bid, tgt_lvl)
            base_s = _safe_finite_float(stats.get("base_time_s", 0.0), 0.0)
            b_divisor = max(1.0, float(min(dev_lvl, 1_000_000) + 1) * micro_mult * speedup)
            dur_s_raw = _cap_eta_float(base_s / b_divisor)
    elif kind == "DEMOLISH_BUILDING":
        bid = _resolve_building_id(raw_tid, raw_tname)
        step_lvl = max(1, _safe_eta_int(raw_tlvl, 0) + 1)
        if bid is not None and step_lvl > 0:
            stats = game_data.building_level_stats(bid, step_lvl)
            base_s = _safe_finite_float(stats.get("base_time_s", 0.0), 0.0)
            b_divisor = max(1.0, float(min(dev_lvl, 1_000_000) + 1) * micro_mult * speedup)
            dur_s_raw = _cap_eta_float(base_s / b_divisor)
    elif kind == "RESEARCH":
        rkey = _resolve_research_key(raw_tid, raw_tname)
        tgt_lvl = max(0, _safe_eta_int(raw_tlvl, 0))
        if rkey is not None and tgt_lvl > 0:
            info = game_data.RESEARCH_BY_ID[rkey]
            stats = _research_level_stats(info, tgt_lvl)
            base_s = _safe_finite_float(stats.get("base_time_s", 0.0), 0.0)
            # Research intentionally ignores Microsystem accelerator (b17)
            r_divisor = max(1.0, float(min(lab_lvl, 1_000_000) + 1) * speedup)
            dur_s_raw = _cap_eta_float(base_s / r_divisor)
    elif kind == "BUILD_SHIP":
        slug = _resolve_unit_slug(
            "ships", "s", SHIP_SLUG_BY_KEY, game_data.SHIPS, raw_tid, raw_tname
        )
        has_tlvl = raw_tlvl is not None and (not isinstance(raw_tlvl, str) or raw_tlvl.strip() != "")
        raw_qty = raw_tlvl if has_tlvl else _action_get(action, "quantity", None)
        has_qty = raw_qty is not None and (not isinstance(raw_qty, str) or raw_qty.strip() != "")
        qty = 1 if not has_qty else max(0, _safe_eta_int(raw_qty, 0))
        if slug is not None and qty > 0:
            base_s = _safe_finite_float((game_data.SHIPS.get(slug) or {}).get("base_build_time_s", 0.0), 0.0)
            wf_divisor = max(1.0, float(min(max(1, wf_lvl), 1_000_000)) * micro_mult * speedup)
            dur_s_raw = _cap_eta_float(_safe_big_mul_div(qty, base_s, wf_divisor))
    elif kind in ("BUILD_DEFENSE", "DEFENSE"):
        slug = _resolve_unit_slug(
            "defense", "d", DEFENSE_SLUG_BY_KEY, game_data.DEFENSES, raw_tid, raw_tname
        )
        has_tlvl = raw_tlvl is not None and (not isinstance(raw_tlvl, str) or raw_tlvl.strip() != "")
        raw_qty = raw_tlvl if has_tlvl else _action_get(action, "quantity", None)
        has_qty = raw_qty is not None and (not isinstance(raw_qty, str) or raw_qty.strip() != "")
        qty = 1 if not has_qty else max(0, _safe_eta_int(raw_qty, 0))
        if slug is not None and qty > 0:
            base_s = _safe_finite_float((game_data.DEFENSES.get(slug) or {}).get("base_build_time_s", 0.0), 0.0)
            wf_divisor = max(1.0, float(min(max(1, wf_lvl), 1_000_000)) * micro_mult * speedup)
            dur_s_raw = _cap_eta_float(_safe_big_mul_div(qty, base_s, wf_divisor))

    if not math.isfinite(dur_s_raw) or dur_s_raw <= 0.0:
        dur_s_raw = 0.0
    else:
        dur_s_raw = _cap_eta_float(dur_s_raw)

    duration_seconds = _round_seconds(dur_s_raw)
    if dur_s_raw <= 0.0:
        duration_text = "Instant"
    elif dur_s_raw <= 0.5:
        duration_text = "1s"
    else:
        duration_text = _format_duration(dur_s_raw)

    if wait_h_raw is None or not math.isfinite(wait_h_raw):
        return {
            "wait_hours": None,
            "wait_seconds": None,
            "wait_eta": "—",
            "duration_seconds": duration_seconds,
            "duration_text": duration_text,
            "total_hours": None,
            "total_seconds": None,
            "total_eta": "—",
            "eta_bottleneck": bottleneck,
        }

    wait_h = _cap_eta_float(float(wait_h_raw))
    wait_s = _cap_eta_float(wait_h * 3600.0)
    total_s = _cap_eta_float(wait_s + dur_s_raw)
    total_h = _cap_eta_float(total_s / 3600.0)
    return {
        "wait_hours": 0.0 if wait_h == 0.0 else round(wait_h, 6),
        "wait_seconds": _round_seconds(wait_s),
        "wait_eta": _format_eta(wait_h, zero_label="Ready"),
        "duration_seconds": duration_seconds,
        "duration_text": duration_text,
        "total_hours": 0.0 if total_h == 0.0 else round(total_h, 6),
        "total_seconds": _round_seconds(total_s),
        "total_eta": _format_eta(total_h, zero_label="Ready"),
        "eta_bottleneck": bottleneck,
    }


def attach_action_etas(state: GameState, actions: Sequence[Any]) -> list[Any]:
    """Attach computed ETA fields onto each `PlannedAction` or action `dict`, returning a new list."""
    out: list[Any] = []
    for act in actions:
        eta = compute_action_eta(state, act)
        if isinstance(act, Mapping):
            out.append({**act, **eta})
        elif is_dataclass(act) and not isinstance(act, type):
            field_names = {f.name for f in dataclass_fields(act)}
            updates = {k: v for k, v in eta.items() if k in field_names}
            out.append(replace(act, **updates))
        else:
            for k, v in eta.items():
                setattr(act, k, v)
            out.append(act)
    return out
