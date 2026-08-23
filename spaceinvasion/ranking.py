"""Ranking evolution tracker, points estimator, Attack Zone classifier, and growth forecasts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields as dataclass_fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
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
from spaceinvasion.state import GameState, PlanetState, Resources

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
    pts = max(0.0, float(points))
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
        totals[res_key] += int(cost.get(res_key, 0) or 0)


def estimate_account_points(state: GameState) -> dict[str, Any]:
    """Estimate empire points (1 pt / 1,000 resources) and Spice value across buildings, research, and fleet."""
    bld_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    flt_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    res_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}

    for planet in state.planets:
        for bkey, lvl in (planet.buildings or {}).items():
            try:
                bid = int(str(bkey).lstrip("b"))
            except ValueError:
                continue
            for l in range(1, max(0, int(lvl)) + 1):
                _sum_cost_resources(game_data.building_cost(bid, l), bld_res)

        for skey, qty in (planet.ships or {}).items():
            count = max(0, int(qty or 0))
            if count <= 0:
                continue
            slug = SHIP_SLUG_BY_KEY.get(skey)
            unit = game_data.SHIPS.get(slug or "") if slug else None
            base_cost = (unit or {}).get("base_cost") or {}
            for rk in flt_res:
                flt_res[rk] += int(base_cost.get(rk, 0) or 0) * count

        for dkey, qty in (planet.defense or {}).items():
            count = max(0, int(qty or 0))
            if count <= 0:
                continue
            slug = DEFENSE_SLUG_BY_KEY.get(dkey)
            unit = game_data.DEFENSES.get(slug or "") if slug else None
            base_cost = (unit or {}).get("base_cost") or {}
            for rk in flt_res:
                flt_res[rk] += int(base_cost.get(rk, 0) or 0) * count

    for rkey, lvl in (state.researches or {}).items():
        info = game_data.resolve_unit("research", rkey)
        levels = (info or {}).get("levels") or {}
        for l in range(1, max(0, int(lvl)) + 1):
            row = levels.get(l) or {}
            _sum_cost_resources(row.get("cost") or {}, res_res)

    bld_pts = sum(bld_res.values()) / POINTS_PER_RESOURCE_DIVISOR
    res_pts = sum(res_res.values()) / POINTS_PER_RESOURCE_DIVISOR
    flt_pts = sum(flt_res.values()) / POINTS_PER_RESOURCE_DIVISOR
    total_pts = bld_pts + res_pts + flt_pts

    combined = {k: bld_res[k] + res_res[k] + flt_res[k] for k in bld_res}
    for planet in state.planets:
        combined["pig_iron"] += int(planet.resources.pig_iron)
        combined["metal"] += int(planet.resources.metal)
        combined["kryptonite"] += int(planet.resources.kryptonite)
        combined["spice"] += int(planet.resources.spice)

    return {
        "estimated_points": round(total_pts, 1),
        "buildings_points": round(bld_pts, 1),
        "research_points": round(res_pts, 1),
        "fleet_defense_points": round(flt_pts, 1),
        "spice_value": round(game_data.spice_equivalent(combined), 1),
    }


_MAX_ETA_FLOAT: float = 1e200


def _safe_speedup_factor(raw_speedup: Any) -> float:
    if raw_speedup is None or isinstance(raw_speedup, bool):
        return 1.0
    try:
        speedup = float(raw_speedup)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if not math.isfinite(speedup) or speedup <= 0.0:
        return 1.0
    return speedup


def _safe_nonneg_int(raw: Any, default: int = 0) -> int:
    if raw is None or isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float):
        if not math.isfinite(raw):
            return default
        return max(0, int(raw))
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(val):
        return default
    try:
        return max(0, int(val))
    except (OverflowError, ValueError):
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
    if planet.energy.used > 0 and planet.energy.produced < planet.energy.used:
        prod_ratio = max(0.0, planet.energy.produced / planet.energy.used)

    rates_raw = getattr(planet, "economy_rates", None)
    if not isinstance(rates_raw, Mapping):
        rates_raw = {}

    def _rate_pct(key: str) -> int:
        val = rates_raw.get(key, 100)
        if val is None or isinstance(val, bool):
            return 100
        if isinstance(val, int):
            return max(0, min(100, val))
        if isinstance(val, float):
            if not math.isfinite(val):
                return 100
            return max(0, min(100, int(val)))
        try:
            parsed = float(str(val).strip())
        except (TypeError, ValueError, OverflowError):
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

    p_pig = (
        game_data.mine_production_per_hour(
            1, b1, temperature=planet.temperature, mining_tech_level=mining_tech
        )
        * factor
        * (pct1 / 100.0)
    )
    p_met = game_data.mine_production_per_hour(2, b2) * factor * (pct2 / 100.0)
    used_pig = game_data.blast_furnace_pig_iron_used(b2) * factor * (pct2 / 100.0)
    p_kryp = (
        game_data.mine_production_per_hour(
            3, b3, temperature=planet.temperature, mining_tech_level=mining_tech
        )
        * factor
        * (pct3 / 100.0)
    )
    p_spice = (
        game_data.mine_production_per_hour(
            4, b4, temperature=planet.temperature, mining_tech_level=mining_tech
        )
        * factor
        * (pct4 / 100.0)
    )

    return {
        "planet_id": planet.planet_id,
        "planet_coords": planet.coords,
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
        "pig_iron_per_hr": round(p_pig - used_pig, 1),
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

    net_total_res = max(0.0, net_pig) + max(0.0, net_met) + max(0.0, net_kryp) + max(0.0, net_spice)
    pts_per_hr = net_total_res / POINTS_PER_RESOURCE_DIVISOR
    spice_hr = game_data.spice_equivalent(
        {
            "pig_iron": max(0.0, net_pig),
            "metal": max(0.0, net_met),
            "kryptonite": max(0.0, net_kryp),
            "spice": max(0.0, net_spice),
        }
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
    pts = max(0.0, start_pts)
    rate = max(0.0, steady_rate_hr)
    for _ in range(max(0, total_hours)):
        pts += rate
        if rate > 0:
            # Diminishing marginal ROI as account scale expands across build tiers
            reinvest_boost = min(0.045, 30.0 / max(180.0, pts))
            rate *= 1.0 + reinvest_boost
    return pts


def _format_eta(hours: Optional[float], *, zero_label: str = "Reached") -> str:
    if hours is None:
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
    if steady_rate_hr <= 0:
        return None
    pts = start_pts
    rate = steady_rate_hr
    for h in range(1, max_hours + 1):
        pts += rate
        if pts >= target_pts:
            return float(h)
        reinvest_boost = min(0.045, 30.0 / max(180.0, pts))
        rate *= 1.0 + reinvest_boost
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
    return [item for item in raw if isinstance(item, dict)]


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
    now_ts = float(state.timestamp or time.time())
    if now_ts <= 0:
        now_ts = time.time()

    entry = {
        "ts": round(now_ts, 3),
        "server_points": state.account_points,
        "server_rank": state.account_rank,
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
        elapsed = abs(entry["ts"] - float(last.get("ts", 0.0)))
        changed_server = (
            (entry["server_points"] is not None and entry["server_points"] != last.get("server_points"))
            or (entry["server_rank"] is not None and entry["server_rank"] != last.get("server_rank"))
        )
        changed_est = int(round(entry["estimated_points"])) != int(
            round(float(last.get("estimated_points", 0.0)))
        )
        if changed_server or changed_est or elapsed >= min_interval_seconds:
            should_append = True

    if should_append:
        samples.append(entry)
        if len(samples) > max_entries:
            samples = samples[-max_entries:]
        if history_path is not None:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(json.dumps(samples, indent=2), encoding="utf-8")

    return samples


def build_ranking_evolution(
    state: GameState,
    history: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build current rank metrics, Attack Zone progress, forecasts, and observed trend."""
    est = estimate_account_points(state)
    econ = estimate_hourly_economy(state)
    est_pts = float(est["estimated_points"])
    server_pts = state.account_points
    baseline_pts = max(float(server_pts or 0), est_pts)

    az = attack_zone_for_points(baseline_pts)
    rate_hr = float(econ["pts_per_hr_steady"])
    newbie_rem = max(0.0, NEWBIE_PROTECTION_POINTS - baseline_pts)

    horizon_defs = (
        ("1h", "+1 Hour", 1),
        ("6h", "+6 Hours", 6),
        ("24h", "+24 Hours", 24),
        ("72h", "+3 Days", 72),
        ("168h", "+7 Days", 168),
    )
    horizons: list[dict[str, Any]] = []
    for key, label, hours in horizon_defs:
        steady_total = round(baseline_pts + rate_hr * hours, 1)
        compound_total = round(_simulate_compound_points(baseline_pts, rate_hr, hours), 1)
        horizons.append(
            {
                "key": key,
                "label": label,
                "hours": hours,
                "steady_points": steady_total,
                "steady_delta": round(steady_total - baseline_pts, 1),
                "steady_az": attack_zone_for_points(steady_total)["label"],
                "compound_points": compound_total,
                "compound_delta": round(compound_total - baseline_pts, 1),
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
        missing_pts = round(max(0.0, target - baseline_pts), 1)
        steady_h = (missing_pts / rate_hr) if rate_hr > 0 else None
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

    samples = list(history or [])
    recent_samples = samples[-20:]
    observed_delta_pts: Optional[float] = None
    observed_delta_rank: Optional[int] = None
    observed_rate_hr: Optional[float] = None
    elapsed_hours = 0.0
    if len(samples) >= 2:
        first, last = samples[0], samples[-1]
        dt_sec = float(last.get("ts", 0.0)) - float(first.get("ts", 0.0))
        elapsed_hours = max(0.0, dt_sec / 3600.0)
        p0 = first.get("server_points")
        p1 = last.get("server_points")
        if p0 is not None and p1 is not None and p1 != p0:
            observed_delta_pts = round(float(p1) - float(p0), 1)
        else:
            observed_delta_pts = round(
                float(last.get("estimated_points", 0.0)) - float(first.get("estimated_points", 0.0)),
                1,
            )
        if elapsed_hours >= 0.05 and observed_delta_pts is not None:
            observed_rate_hr = round(observed_delta_pts / elapsed_hours, 2)
        r0 = first.get("server_rank")
        r1 = last.get("server_rank")
        if r0 is not None and r1 is not None:
            # Negative means rank number dropped (e.g. #420 -> #393 = climbed +27 spots)
            observed_delta_rank = int(r0) - int(r1)

    return {
        "current": {
            "server_points": server_pts,
            "server_rank": state.account_rank,
            "baseline_points": round(baseline_pts, 1),
            **est,
            "attack_zone": az,
            "newbie_protected": baseline_pts < NEWBIE_PROTECTION_POINTS,
            "newbie_remaining_pts": round(newbie_rem, 1),
            "newbie_eta_steady": _format_eta((newbie_rem / rate_hr) if rate_hr > 0 else None),
            "newbie_eta_compound": _format_eta(
                _solve_compound_eta_hours(baseline_pts, rate_hr, float(NEWBIE_PROTECTION_POINTS))
            ),
        },
        "rates": econ,
        "horizons": horizons,
        "milestones": milestones,
        "observed_trend": {
            "sample_count": len(samples),
            "elapsed_hours": round(elapsed_hours, 2),
            "delta_points": observed_delta_pts,
            "rank_climbed": observed_delta_rank,
            "observed_pts_per_hr": observed_rate_hr,
            "samples": recent_samples,
        },
    }


def _res_dict(res: Resources) -> dict[str, int]:
    return {
        "pig_iron": int(res.pig_iron),
        "metal": int(res.metal),
        "kryptonite": int(res.kryptonite),
        "spice": int(res.spice),
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
    if seconds is None:
        return "—"
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
    need_keys = [rk for rk in ("pig_iron", "metal", "kryptonite", "spice") if int(missing.get(rk, 0) or 0) > 0]
    if not need_keys:
        return 0.0, None

    max_hours = 0.0
    bottleneck: Optional[str] = None
    for rk in need_keys:
        raw_rate = rates_hr.get(rk, 0.0)
        try:
            rate = float(0.0 if raw_rate is None else raw_rate)
        except (TypeError, ValueError, OverflowError):
            return None, rk
        if not math.isfinite(rate) or rate <= 0.0:
            return None, rk
        hrs = _safe_big_mul_div(int(missing[rk]), 1.0, rate)
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
                rk: int(round(float(base_cost.get(rk, 0) or 0) * scale))
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

    p_econ = estimate_planet_hourly_economy(state, target)
    rates_hr = {
        "pig_iron": float(p_econ["pig_iron_per_hr"]),
        "metal": float(p_econ["metal_per_hr"]),
        "kryptonite": float(p_econ["kryptonite_per_hr"]),
        "spice": float(p_econ["spice_per_hr"]),
    }
    res_have = _res_dict(target.resources)
    speedup = float(p_econ["speedup_factor"])
    dev_lvl = target.building_level(7)
    lab_lvl = target.building_level(8)
    micro_lvl = target.building_level(17)
    b_divisor = max(1.0, float(dev_lvl + 1) * (2**micro_lvl) * speedup)
    r_divisor = max(1.0, float(lab_lvl + 1) * speedup)

    is_active = target.planet_id == state.active_planet_id
    queued_bids: dict[int, int] = {}
    if is_active:
        for q in state.active_queues:
            if q.category == "building" and q.building_id is not None:
                queued_bids[int(q.building_id)] = queued_bids.get(int(q.building_id), 0) + 1

    b_offers = {int(o.building_id): o for o in (target.building_offers or ())}
    r_offers = {str(o.research_key): o for o in (state.research_offers or ())}

    buildings_rows: list[dict[str, Any]] = []
    for bid in sorted(game_data.BUILDING_BY_ID.keys()):
        info = game_data.BUILDING_BY_ID[bid]
        cur_lvl = target.building_level(bid)
        nxt_lvl = cur_lvl + 1
        offer = b_offers.get(bid)
        stats_next = game_data.building_level_stats(bid, nxt_lvl)
        raw_cost = (
            _res_dict(offer.cost)
            if (offer is not None and not offer.cost.is_zero() and offer.target_level == nxt_lvl)
            else (stats_next.get("cost") or game_data.building_cost(bid, nxt_lvl))
        )
        cost = {rk: int(raw_cost.get(rk, 0) or 0) for rk in ("pig_iron", "metal", "kryptonite", "spice")}
        missing = {rk: max(0, cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cost}
        missing_total = sum(missing.values())
        can_afford = missing_total == 0
        wait_h, bottleneck = _compute_resource_eta(missing, rates_hr)
        base_time_s = float(stats_next.get("base_time_s", 0.0) or 0.0)
        build_s = base_time_s / b_divisor
        reach_h = (wait_h + build_s / 3600.0) if wait_h is not None else None
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
        for step_idx in range(horizon_count):
            step_lvl = nxt_lvl + step_idx
            step_stats = game_data.building_level_stats(bid, step_lvl)
            step_raw_cost = (
                cost
                if step_idx == 0
                else (step_stats.get("cost") or game_data.building_cost(bid, step_lvl))
            )
            for rk in cum_cost:
                cum_cost[rk] += int(step_raw_cost.get(rk, 0) or 0)
            cum_missing = {rk: max(0, cum_cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cum_cost}
            step_wait_h, step_bn = _compute_resource_eta(cum_missing, rates_hr)
            step_div = max(1.0, float(sim_dev + 1) * (2**sim_micro) * speedup)
            step_b_s = float(step_stats.get("base_time_s", 0.0) or 0.0) / step_div
            cum_build_s += step_b_s
            if bid == 7:
                sim_dev = step_lvl
            elif bid == 17:
                sim_micro = step_lvl
            step_reach_h = (step_wait_h + cum_build_s / 3600.0) if step_wait_h is not None else None
            horizons.append(
                {
                    "level": step_lvl,
                    "cost": dict(step_raw_cost),
                    "cum_cost": dict(cum_cost),
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
                "name": target.building_names.get(f"b{bid}") or str(info["name"]),
                "category": _building_category(bid),
                "asteroid_ok": bool(info.get("asteroid_ok", False)),
                "current_level": cur_lvl,
                "next_level": nxt_lvl,
                "queued_count": queued_bids.get(bid, 0),
                "energy_delta": int(game_data.building_energy_delta(bid, nxt_lvl)),
                "cost": cost,
                "missing": missing,
                "missing_total": missing_total,
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
        key=lambda rk: int(rk[1:]) if rk[1:].isdigit() else 999,
    )
    for rkey in r_keys:
        info = game_data.RESEARCH_BY_ID[rkey]
        rid_num = int(rkey[1:]) if rkey[1:].isdigit() else None
        cur_lvl = int((state.researches or {}).get(rkey, 0) or 0)
        nxt_lvl = cur_lvl + 1
        offer = r_offers.get(rkey)
        stats_next = _research_level_stats(info, nxt_lvl)
        raw_cost = (
            _res_dict(offer.cost)
            if (offer is not None and not offer.cost.is_zero() and offer.target_level == nxt_lvl)
            else (stats_next.get("cost") or {})
        )
        cost = {rk: int(raw_cost.get(rk, 0) or 0) for rk in ("pig_iron", "metal", "kryptonite", "spice")}
        missing = {rk: max(0, cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cost}
        missing_total = sum(missing.values())
        can_afford = missing_total == 0
        wait_h, bottleneck = _compute_resource_eta(missing, rates_hr)
        res_s = float(stats_next.get("base_time_s", 0.0) or 0.0) / r_divisor
        reach_h = (wait_h + res_s / 3600.0) if wait_h is not None else None
        unmet = game_data.missing_prerequisites(
            "research",
            rkey,
            buildings=target.buildings,
            researches=state.researches,
        )

        r_horizons: list[dict[str, Any]] = []
        cum_cost = {rk: 0 for rk in ("pig_iron", "metal", "kryptonite", "spice")}
        cum_res_s = 0.0
        for step_idx in range(min(3, horizon_count)):
            step_lvl = nxt_lvl + step_idx
            step_stats = _research_level_stats(info, step_lvl)
            step_raw = cost if step_idx == 0 else (step_stats.get("cost") or {})
            for rk in cum_cost:
                cum_cost[rk] += int(step_raw.get(rk, 0) or 0)
            cum_missing = {rk: max(0, cum_cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in cum_cost}
            step_wait_h, step_bn = _compute_resource_eta(cum_missing, rates_hr)
            step_s = float(step_stats.get("base_time_s", 0.0) or 0.0) / r_divisor
            cum_res_s += step_s
            step_reach_h = (step_wait_h + cum_res_s / 3600.0) if step_wait_h is not None else None
            r_horizons.append(
                {
                    "level": step_lvl,
                    "cost": dict(step_raw),
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
                "name": state.research_names.get(rkey) or str(info["name"]),
                "current_level": cur_lvl,
                "next_level": nxt_lvl,
                "cost": cost,
                "missing": missing,
                "missing_total": missing_total,
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
            "planet_id": target.planet_id,
            "name": target.name,
            "coords": target.coords,
            "planet_type": target.planet_type,
            "temperature": target.temperature,
            "fields_used": target.fields_used,
            "fields_max": target.fields_max,
            "resources": res_have,
            "dev_center_level": dev_lvl,
            "research_lab_level": lab_lvl,
            "microsystem_level": micro_lvl,
            "building_time_divisor": round(b_divisor, 2),
            "research_time_divisor": round(r_divisor, 2),
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
    baseline_pts = max(float(state.account_points or 0), float(est["estimated_points"]))
    az = attack_zone_for_points(baseline_pts)

    techs = {
        "weapons": int((state.researches or {}).get("r8", 0) or 0),
        "shield": int((state.researches or {}).get("r9", 0) or 0),
        "defense": int((state.researches or {}).get("r10", 0) or 0),
        "spy": int((state.researches or {}).get("r7", 0) or 0),
        "targeting": int((state.researches or {}).get("r11", 0) or 0),
        "particle": int((state.researches or {}).get("r12", 0) or 0),
        "rocket": int((state.researches or {}).get("r4", 0) or 0),
        "diffusion": int((state.researches or {}).get("r5", 0) or 0),
        "warp": int((state.researches or {}).get("r6", 0) or 0),
    }

    atk_mult = 1.0 + 0.10 * techs["weapons"]
    shd_mult = 1.0 + 0.10 * techs["shield"]
    def_mult = 1.0 + 0.10 * techs["defense"]

    ship_catalog: list[dict[str, Any]] = []
    defense_catalog: list[dict[str, Any]] = []
    total_fleet_power = 0.0
    total_defense_power = 0.0
    total_cargo = 0

    for skey in sorted(SHIP_SLUG_BY_KEY.keys(), key=lambda k: int(k[1:]) if k[1:].isdigit() else 99):
        slug = SHIP_SLUG_BY_KEY[skey]
        unit = game_data.SHIPS.get(slug) or {}
        stats = unit.get("stats") or {}
        owned = int((target.ships or {}).get(skey, 0) or 0) if target else 0
        atk = float(stats.get("attack", 0) or 0) * atk_mult
        shd = float(stats.get("shield", 0) or 0) * shd_mult
        hul = float(stats.get("hull", 0) or 0) * def_mult
        power = (atk + shd + hul * 0.25) * owned
        cargo = int(stats.get("cargo", 0) or 0)
        total_fleet_power += power
        total_cargo += cargo * owned
        ship_catalog.append(
            {
                "key": skey,
                "unit_id": int(skey[1:]) if skey[1:].isdigit() else 0,
                "slug": slug,
                "name": str(unit.get("name", slug)),
                "owned": owned,
                "attack": int(stats.get("attack", 0) or 0),
                "shield": int(stats.get("shield", 0) or 0),
                "hull": int(stats.get("hull", 0) or 0),
                "cargo": cargo,
                "speed": int(stats.get("speed", 0) or 0),
                "base_cost": unit.get("base_cost") or {},
            }
        )

    for dkey in sorted(DEFENSE_SLUG_BY_KEY.keys(), key=lambda k: int(k[1:]) if k[1:].isdigit() else 99):
        slug = DEFENSE_SLUG_BY_KEY[dkey]
        unit = game_data.DEFENSES.get(slug) or {}
        stats = unit.get("stats") or {}
        owned = int((target.defense or {}).get(dkey, 0) or 0) if target else 0
        atk = float(stats.get("attack", 0) or 0) * atk_mult
        shd = float(stats.get("shield", 0) or 0) * shd_mult
        hul = float(stats.get("hull", 0) or 0) * def_mult
        total_defense_power += (atk + shd + hul * 0.25) * owned
        defense_catalog.append(
            {
                "key": dkey,
                "unit_id": int(dkey[1:]) if dkey[1:].isdigit() else 0,
                "slug": slug,
                "name": str(unit.get("name", slug)),
                "owned": owned,
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
                    "unit_type": o.unit_type,
                    "unit_id": o.unit_id,
                    "name": o.name,
                    "owned": o.owned,
                    "cost": _res_dict(o.cost),
                    "can_build": o.can_build,
                    "status_text": o.status_text,
                }
            )

    escape_plan_dict: Optional[dict[str, Any]] = None
    if target is not None:
        other_coords = next(
            (p.coords for p in state.planets if p.planet_id != target.planet_id),
            target.coords,
        )
        esc = plan_escape_flight(target, target_coords=other_coords)
        escape_plan_dict = {
            "feasible": esc.feasible,
            "target_coords": esc.target_coords,
            "ships": dict(esc.ships),
            "cargo": _res_dict(esc.cargo),
            "total_cargo": sum(_res_dict(esc.cargo).values()),
            "total_capacity": esc.total_capacity,
            "fuel_reserved_spice": esc.fuel_reserved_spice,
            "duration_hint": esc.duration_hint,
            "spice_saved": esc.cargo.spice,
            "reason": esc.reason,
        }

    return {
        "planet_id": target.planet_id if target else None,
        "planet_coords": target.coords if target else None,
        "weapons_factory_level": target.building_level(9) if target else 0,
        "fleet_base_level": target.building_level(10) if target else 0,
        "galaxy_scanner_level": target.building_level(19) if target else 0,
        "combat_techs": techs,
        "attack_zone": az,
        "newbie_protected": baseline_pts < NEWBIE_PROTECTION_POINTS,
        "safety_margin": float(safety_margin),
        "bash_rule": "Max 3 attacks / 24h on active planets (exempt: yellow inactives & Spacia)",
        "az_rule": "Eligible targets within AZ ±2 (Top 50 bypass AZ outside Newbie Shield)",
        "combat_model": "1:1 zero-rapidfire combat; attacker retreats unless ratio >= safety margin",
        "stationed_fleet_power": round(total_fleet_power, 1),
        "stationed_defense_power": round(total_defense_power, 1),
        "total_cargo_capacity": total_cargo,
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
    clean_atk = {str(k): max(0, int(v or 0)) for k, v in attacker_ships.items() if int(v or 0) > 0}
    clean_def_ships = {
        str(k): max(0, int(v or 0)) for k, v in (defender_ships or {}).items() if int(v or 0) > 0
    }
    clean_def_defs = {
        str(k): max(0, int(v or 0)) for k, v in (defender_defense or {}).items() if int(v or 0) > 0
    }
    res_map = target_resources or {}
    report = SpyReport(
        target_coords=target_coords,
        owner="Simulated Target",
        resources=Resources(
            pig_iron=max(0, int(res_map.get("pig_iron", 0) or 0)),
            metal=max(0, int(res_map.get("metal", 0) or 0)),
            kryptonite=max(0, int(res_map.get("kryptonite", 0) or 0)),
            spice=max(0, int(res_map.get("spice", 0) or 0)),
        ),
        fleet=clean_def_ships,
        defense=clean_def_defs,
    )
    est = simulate_raid_outcome(
        clean_atk,
        report,
        attacker_researches=state.researches,
        safety_margin=max(1.0, float(safety_margin)),
    )
    plunder_map = _res_dict(est.expected_loot)
    return {
        "safe_to_attack": est.safe_to_attack,
        "win_ratio": est.power_ratio,
        "safety_margin": max(1.0, float(safety_margin)),
        "attacker_power": est.attacker_power,
        "defender_power": est.defender_power,
        "stealth_first_strike_power": est.stealth_first_strike_power,
        "cargo_capacity": est.cargo_capacity,
        "loot_spice_equivalent": est.loot_spice_equivalent,
        "expected_plunder": plunder_map,
        "total_plunder": sum(plunder_map.values()),
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


def _safe_eta_int(raw: Any, default: int = 0) -> int:
    if raw is None or isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if math.isfinite(raw) else default
    if isinstance(raw, Decimal):
        return _decimal_to_eta_int(raw, default)
    try:
        s = str(raw).strip()
        if not s:
            return default
        d = Decimal(s)
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return default
    return _decimal_to_eta_int(d, default)


def _extract_resource_map(raw: Any) -> Optional[dict[str, int]]:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return {
            rk: max(0, _safe_eta_int(raw.get(rk, 0), 0))
            for rk in _ETA_RESOURCE_KEYS
        }
    if any(hasattr(raw, rk) for rk in _ETA_RESOURCE_KEYS):
        return {
            rk: max(0, _safe_eta_int(getattr(raw, rk, 0), 0))
            for rk in _ETA_RESOURCE_KEYS
        }
    return None


def _resolve_planet_for_eta(state: GameState, action: Any) -> Optional[PlanetState]:
    planets = tuple(getattr(state, "planets", ()) or ())
    if not planets:
        return None
    raw_pid = _action_get(action, "planet_id", None)
    raw_coords = str(_action_get(action, "planet_coords", "") or "").strip()

    if raw_pid is not None and raw_pid != "":
        if isinstance(raw_pid, bool):
            return None
        if isinstance(raw_pid, float):
            if not math.isfinite(raw_pid) or not raw_pid.is_integer():
                return None
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError, OverflowError):
                return None
        elif isinstance(raw_pid, Decimal):
            if not _decimal_is_integer(raw_pid):
                return None
            max_pid_bits = max((abs(int(p.planet_id)).bit_length() for p in planets), default=0)
            if raw_pid != 0 and raw_pid.adjusted() > max(20, max_pid_bits):
                return None
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError, OverflowError, InvalidOperation, ArithmeticError):
                return None
        elif isinstance(raw_pid, int):
            pid = raw_pid
        elif isinstance(raw_pid, str):
            s = raw_pid.strip()
            if not s:
                return None
            try:
                pid = int(s)
            except (TypeError, ValueError, OverflowError):
                return None
        else:
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError, OverflowError):
                return None
        for planet in planets:
            if int(planet.planet_id) == pid:
                return planet
        return None

    if raw_coords:
        for planet in planets:
            if str(planet.coords).strip() == raw_coords:
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
            s = str(target_id).strip()
            try:
                if s.lower().startswith("b") and s[1:].isascii() and s[1:].isdigit():
                    bid = int(s[1:])
                elif s.isascii() and s.isdigit():
                    bid = int(s)
                else:
                    return None
            except (TypeError, ValueError, OverflowError):
                return None
        return bid if bid in game_data.BUILDING_BY_ID else None

    if target_name:
        info = game_data.resolve_unit("buildings", str(target_name))
        if info and info.get("id") is not None:
            bid = int(info["id"])
            return bid if bid in game_data.BUILDING_BY_ID else None
    return None


def _resolve_research_key(target_id: Any, target_name: Any) -> Optional[str]:
    if target_id is not None and target_id != "":
        if isinstance(target_id, bool):
            return None
        if isinstance(target_id, int):
            rkey = f"r{target_id}"
        else:
            s = str(target_id).strip()
            try:
                if s.lower().startswith("r") and s[1:].isascii() and s[1:].isdigit():
                    rkey = f"r{int(s[1:])}"
                elif s.isascii() and s.isdigit():
                    rkey = f"r{int(s)}"
                else:
                    return None
            except (TypeError, ValueError, OverflowError):
                return None
        return rkey if rkey in game_data.RESEARCH_BY_ID else None

    if target_name:
        info = game_data.resolve_unit("research", str(target_name))
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
            return slug_by_key.get(f"{prefix}{target_id}")
        s = str(target_id).strip()
        try:
            if s.lower().startswith(prefix) and s[1:].isascii() and s[1:].isdigit():
                return slug_by_key.get(f"{prefix}{int(s[1:])}")
            if s.isascii() and s.isdigit():
                return slug_by_key.get(f"{prefix}{int(s)}")
        except (TypeError, ValueError, OverflowError):
            return None
        if s in catalog:
            return s
        return None

    if target_name:
        info = game_data.resolve_unit(category, str(target_name))
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
    kind = str(_action_get(action, "kind", "") or "").strip().upper()
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
            base_s = float(stats.get("base_time_s", 0.0) or 0.0)
            b_divisor = max(1.0, float(dev_lvl + 1) * micro_mult * speedup)
            dur_s_raw = base_s / b_divisor
    elif kind == "RESEARCH":
        rkey = _resolve_research_key(raw_tid, raw_tname)
        tgt_lvl = max(0, _safe_eta_int(raw_tlvl, 0))
        if rkey is not None and tgt_lvl > 0:
            info = game_data.RESEARCH_BY_ID[rkey]
            stats = _research_level_stats(info, tgt_lvl)
            base_s = float(stats.get("base_time_s", 0.0) or 0.0)
            # Research intentionally ignores Microsystem accelerator (b17)
            r_divisor = max(1.0, float(lab_lvl + 1) * speedup)
            dur_s_raw = base_s / r_divisor
    elif kind == "BUILD_SHIP":
        slug = _resolve_unit_slug(
            "ships", "s", SHIP_SLUG_BY_KEY, game_data.SHIPS, raw_tid, raw_tname
        )
        has_tlvl = raw_tlvl is not None and (not isinstance(raw_tlvl, str) or raw_tlvl.strip() != "")
        raw_qty = raw_tlvl if has_tlvl else _action_get(action, "quantity", None)
        has_qty = raw_qty is not None and (not isinstance(raw_qty, str) or raw_qty.strip() != "")
        qty = 1 if not has_qty else max(0, _safe_eta_int(raw_qty, 0))
        if slug is not None and qty > 0:
            base_s = float((game_data.SHIPS.get(slug) or {}).get("base_build_time_s", 0.0) or 0.0)
            wf_divisor = max(1.0, float(max(1, wf_lvl)) * micro_mult * speedup)
            dur_s_raw = _safe_big_mul_div(qty, base_s, wf_divisor)
    elif kind in ("BUILD_DEFENSE", "DEFENSE"):
        slug = _resolve_unit_slug(
            "defense", "d", DEFENSE_SLUG_BY_KEY, game_data.DEFENSES, raw_tid, raw_tname
        )
        has_tlvl = raw_tlvl is not None and (not isinstance(raw_tlvl, str) or raw_tlvl.strip() != "")
        raw_qty = raw_tlvl if has_tlvl else _action_get(action, "quantity", None)
        has_qty = raw_qty is not None and (not isinstance(raw_qty, str) or raw_qty.strip() != "")
        qty = 1 if not has_qty else max(0, _safe_eta_int(raw_qty, 0))
        if slug is not None and qty > 0:
            base_s = float((game_data.DEFENSES.get(slug) or {}).get("base_build_time_s", 0.0) or 0.0)
            wf_divisor = max(1.0, float(max(1, wf_lvl)) * micro_mult * speedup)
            dur_s_raw = _safe_big_mul_div(qty, base_s, wf_divisor)

    if not math.isfinite(dur_s_raw) or dur_s_raw <= 0.0:
        dur_s_raw = 0.0

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

    wait_h = max(0.0, float(wait_h_raw))
    wait_s = wait_h * 3600.0
    total_s = wait_s + dur_s_raw
    total_h = total_s / 3600.0
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
