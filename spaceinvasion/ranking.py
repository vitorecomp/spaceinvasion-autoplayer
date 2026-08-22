"""Ranking evolution tracker, points estimator, Attack Zone classifier, and growth forecasts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Optional

from spaceinvasion import game_data
from spaceinvasion.fleet import DEFENSE_SLUG_BY_KEY, SHIP_SLUG_BY_KEY
from spaceinvasion.state import GameState

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


def estimate_hourly_economy(state: GameState) -> dict[str, float]:
    """Compute empire-wide hourly net production and potential point gain per hour."""
    mining_tech = int((state.researches or {}).get("r1", 0) or 0)
    speedup = float(state.speedup_factor or 1.0)
    if speedup <= 0:
        speedup = 1.0

    net_pig = 0.0
    net_met = 0.0
    net_kryp = 0.0
    net_spice = 0.0

    for planet in state.planets:
        prod_ratio = 1.0
        if planet.energy.used > 0 and planet.energy.produced < planet.energy.used:
            prod_ratio = max(0.0, planet.energy.produced / planet.energy.used)

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
        )
        p_met = game_data.mine_production_per_hour(2, b2) * factor
        used_pig = game_data.blast_furnace_pig_iron_used(b2) * factor
        p_kryp = (
            game_data.mine_production_per_hour(
                3, b3, temperature=planet.temperature, mining_tech_level=mining_tech
            )
            * factor
        )
        p_spice = (
            game_data.mine_production_per_hour(
                4, b4, temperature=planet.temperature, mining_tech_level=mining_tech
            )
            * factor
        )

        net_pig += p_pig - used_pig
        net_met += p_met
        net_kryp += p_kryp
        net_spice += p_spice

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


def _format_eta(hours: Optional[float]) -> str:
    if hours is None:
        return "—"
    if hours <= 0:
        return "Reached"
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
