"""Pure SpaceInvasion Classic Attack Zone (AZ), points estimation, and ranking progression math.

Zero database imports (Phase 2.5): callers pass rank history samples explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import Path

from spaceinvasion.combat._combat_overview import build_combat_overview_payload
from spaceinvasion.combat._points_helpers import (
    MAX_ETA_FLOAT,
    MAX_JSON_SAFE,
    POINTS_PER_RESOURCE_DIVISOR,
    cap_eta_float,
    estimate_hourly_economy,
    safe_big_float,
    safe_big_mul_div,
    safe_eta_str,
    safe_nonneg_int,
    safe_optional_finite_float,
    safe_optional_finite_int,
)
from spaceinvasion.combat._points_helpers import (
    estimate_planet_hourly_economy as estimate_planet_hourly_economy,
)
from spaceinvasion.combat._points_helpers import (
    format_eta as format_eta,
)
from spaceinvasion.combat._rank_history import (
    load_rank_history as load_rank_history,
)
from spaceinvasion.combat._rank_history import (
    record_rank_snapshot as _record_rank_snapshot_impl,
)
from spaceinvasion.core.catalog import (
    DEFENSE_SLUG_BY_KEY,
    DEFENSES,
    SHIP_SLUG_BY_KEY,
    SHIPS,
    building_cost,
    resolve_unit,
    safe_finite_float,
    sanitize_json_tree,
    spice_equivalent,
)
from spaceinvasion.core.model import GameState, PlanetState

NEWBIE_PROTECTION_POINTS = 10_000


@dataclass(frozen=True)
class AttackZoneBand:
    """SpaceInvasion Classic Attack Zone point band (`internalUniverse`)."""

    zone: int
    min_pts: int
    max_pts: int | None

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


def attack_zone_for_points(points: float) -> dict[str, object]:
    """Return current and next Attack Zone metadata for `points`."""
    pts = max(0.0, safe_finite_float(points, 0.0))
    chosen = ATTACK_ZONES[-1]
    next_band: AttackZoneBand | None = None
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


def _sum_cost_resources(cost: Mapping[str, object], totals: dict[str, int]) -> None:
    for res_key in ("pig_iron", "metal", "kryptonite", "spice"):
        totals[res_key] += safe_nonneg_int(cost.get(res_key, 0), 0)


def estimate_account_points(state: GameState) -> dict[str, float]:
    """Estimate empire points (1 pt / 1,000 resources) and Spice value across buildings, research, and fleet."""
    bld_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    flt_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    res_res = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}

    for planet in state.planets:
        for bkey, lvl in (planet.buildings or {}).items():
            bkey_s = safe_eta_str(bkey).strip()
            if bkey_s.startswith("b"):
                bkey_s = bkey_s[1:]
            if not (bkey_s.isascii() and bkey_s.isdigit() and len(bkey_s) <= 6):
                continue
            bid = int(bkey_s)
            for lvl_step in range(1, min(1000, safe_nonneg_int(lvl, 0)) + 1):
                _sum_cost_resources(building_cost(bid, lvl_step), bld_res)

        for skey, qty in (planet.ships or {}).items():
            count = safe_nonneg_int(qty, 0)
            slug = SHIP_SLUG_BY_KEY.get(skey)
            unit = SHIPS.get(slug or "") if (count > 0 and slug) else None
            base_cost = unit.get("base_cost") if isinstance(unit, Mapping) else None
            if isinstance(base_cost, Mapping):
                for rk in flt_res:
                    flt_res[rk] += safe_nonneg_int(base_cost.get(rk, 0), 0) * count

        for dkey, qty in (planet.defense or {}).items():
            count = safe_nonneg_int(qty, 0)
            slug = DEFENSE_SLUG_BY_KEY.get(dkey)
            unit = DEFENSES.get(slug or "") if (count > 0 and slug) else None
            base_cost = unit.get("base_cost") if isinstance(unit, Mapping) else None
            if isinstance(base_cost, Mapping):
                for rk in flt_res:
                    flt_res[rk] += safe_nonneg_int(base_cost.get(rk, 0), 0) * count

    for rkey, lvl in (state.researches or {}).items():
        info = resolve_unit("research", rkey)
        levels = info.get("levels") if isinstance(info, Mapping) else None
        if not isinstance(levels, Mapping):
            continue
        for lvl_step in range(1, min(1000, safe_nonneg_int(lvl, 0)) + 1):
            row = levels.get(lvl_step)
            cost = row.get("cost") if isinstance(row, Mapping) else None
            if isinstance(cost, Mapping):
                _sum_cost_resources(cost, res_res)

    bld_pts = cap_eta_float(safe_big_mul_div(sum(bld_res.values()), 1.0, POINTS_PER_RESOURCE_DIVISOR))
    res_pts = cap_eta_float(safe_big_mul_div(sum(res_res.values()), 1.0, POINTS_PER_RESOURCE_DIVISOR))
    flt_pts = cap_eta_float(safe_big_mul_div(sum(flt_res.values()), 1.0, POINTS_PER_RESOURCE_DIVISOR))
    total_pts = cap_eta_float(bld_pts + res_pts + flt_pts)

    combined = {k: bld_res[k] + res_res[k] + flt_res[k] for k in bld_res}
    for planet in state.planets:
        combined["pig_iron"] += safe_nonneg_int(planet.resources.pig_iron, 0)
        combined["metal"] += safe_nonneg_int(planet.resources.metal, 0)
        combined["kryptonite"] += safe_nonneg_int(planet.resources.kryptonite, 0)
        combined["spice"] += safe_nonneg_int(planet.resources.spice, 0)

    return {
        "estimated_points": round(total_pts, 1),
        "buildings_points": round(bld_pts, 1),
        "research_points": round(res_pts, 1),
        "fleet_defense_points": round(flt_pts, 1),
        "spice_value": round(cap_eta_float(spice_equivalent(combined)), 1),
    }


def simulate_compound_points(start_pts: float, steady_rate_hr: float, total_hours: int) -> float:
    """Estimate cumulative score with continuous mine reinvestment attenuated by upgrade scaling."""
    pts = cap_eta_float(max(0.0, start_pts))
    rate = cap_eta_float(max(0.0, steady_rate_hr))
    for _ in range(max(0, total_hours)):
        pts += rate
        if not math.isfinite(pts) or pts >= MAX_ETA_FLOAT:
            return MAX_ETA_FLOAT
        if rate > 0:
            reinvest_boost = min(0.045, 30.0 / max(180.0, pts))
            rate = cap_eta_float(rate * (1.0 + reinvest_boost))
    return cap_eta_float(pts)


def solve_compound_eta_hours(
    start_pts: float,
    steady_rate_hr: float,
    target_pts: float,
    max_hours: int = 24 * 30,
) -> float | None:
    """Solve hours until compound points reach `target_pts`, or `None` if beyond `max_hours`."""
    if start_pts >= target_pts:
        return 0.0
    if not math.isfinite(steady_rate_hr) or steady_rate_hr <= 0:
        return None
    pts = cap_eta_float(max(0.0, start_pts))
    rate = cap_eta_float(steady_rate_hr)
    for h in range(1, max_hours + 1):
        pts += rate
        if not math.isfinite(pts) or pts >= target_pts:
            return float(h)
        reinvest_boost = min(0.045, 30.0 / max(180.0, pts))
        rate = cap_eta_float(rate * (1.0 + reinvest_boost))
    return None


def _build_ranking_horizons(baseline_pts: float, rate_hr: float) -> list[dict[str, object]]:
    horizon_defs = (
        ("1h", "+1 Hour", 1),
        ("6h", "+6 Hours", 6),
        ("24h", "+24 Hours", 24),
        ("72h", "+3 Days", 72),
        ("168h", "+7 Days", 168),
    )
    horizons: list[dict[str, object]] = []
    for key, label, hours in horizon_defs:
        steady_total = round(cap_eta_float(baseline_pts + rate_hr * hours), 1)
        compound_total = round(cap_eta_float(simulate_compound_points(baseline_pts, rate_hr, hours)), 1)
        horizons.append(
            {
                "key": key,
                "label": label,
                "hours": hours,
                "steady_points": steady_total,
                "steady_delta": round(cap_eta_float(steady_total - baseline_pts, allow_negative=True), 1),
                "steady_az": attack_zone_for_points(steady_total)["label"],
                "compound_points": compound_total,
                "compound_delta": round(cap_eta_float(compound_total - baseline_pts, allow_negative=True), 1),
                "compound_az": attack_zone_for_points(compound_total)["label"],
            }
        )
    return horizons


def _build_ranking_milestones(
    baseline_pts: float,
    rate_hr: float,
    az: Mapping[str, object],
) -> list[dict[str, object]]:
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
        if isinstance(nxt_zone_pts, int) and nxt_zone_pts > 0:
            upcoming = [(f"{az['next_zone']} Entry", nxt_zone_pts)]

    milestones: list[dict[str, object]] = []
    for name, target in upcoming[:4]:
        missing_pts = round(cap_eta_float(max(0.0, target - baseline_pts)), 1)
        steady_h = cap_eta_float(missing_pts / rate_hr) if rate_hr > 0 else None
        comp_h = solve_compound_eta_hours(baseline_pts, rate_hr, float(target))
        milestones.append(
            {
                "label": name,
                "target_points": target,
                "missing_points": missing_pts,
                "steady_eta": format_eta(steady_h),
                "compound_eta": format_eta(comp_h),
            }
        )
    return milestones


def _compute_observed_trend(history: Sequence[Mapping[str, object]] | None) -> dict[str, object]:
    samples: list[dict[str, object]] = [
        sanitize_json_tree(s, max_int=MAX_JSON_SAFE, nonfinite_float=None)
        for s in (history or ())
        if isinstance(s, Mapping)
    ]
    observed_delta_pts: float | None = None
    observed_delta_rank: int | None = None
    observed_rate_hr: float | None = None
    elapsed_hours = 0.0
    if len(samples) >= 2:
        first, last = samples[0], samples[-1]
        dt_sec = cap_eta_float(
            safe_finite_float(last.get("ts", 0.0), 0.0) - safe_finite_float(first.get("ts", 0.0), 0.0),
            allow_negative=True,
        )
        elapsed_hours = cap_eta_float(max(0.0, dt_sec / 3600.0))
        p0 = safe_optional_finite_float(first.get("server_points"))
        p1 = safe_optional_finite_float(last.get("server_points"))
        if p0 is not None and p1 is not None and p1 != p0:
            raw_delta = p1 - p0
        else:
            raw_delta = safe_finite_float(last.get("estimated_points", 0.0), 0.0) - safe_finite_float(
                first.get("estimated_points", 0.0), 0.0
            )
        observed_delta_pts = round(cap_eta_float(raw_delta, allow_negative=True), 1)
        if elapsed_hours >= 0.05 and math.isfinite(observed_delta_pts):
            rate_val = observed_delta_pts / elapsed_hours
            observed_rate_hr = (
                round(cap_eta_float(rate_val, allow_negative=True), 2) if math.isfinite(rate_val) else None
            )
        r0 = safe_optional_finite_int(first.get("server_rank"))
        r1 = safe_optional_finite_int(last.get("server_rank"))
        if r0 is not None and r1 is not None:
            observed_delta_rank = max(-MAX_JSON_SAFE, min(MAX_JSON_SAFE, r0 - r1))

    return {
        "sample_count": len(samples),
        "elapsed_hours": round(cap_eta_float(elapsed_hours), 2),
        "delta_points": observed_delta_pts,
        "rank_climbed": observed_delta_rank,
        "observed_pts_per_hr": observed_rate_hr,
        "samples": samples[-20:],
    }


def build_ranking_evolution(
    state: GameState,
    history: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Build current rank metrics, Attack Zone progress, forecasts, and observed trend (DB-free)."""
    est = estimate_account_points(state)
    econ = estimate_hourly_economy(state)
    est_pts = safe_big_float(est["estimated_points"])
    server_pts = (
        min(MAX_JSON_SAFE, safe_nonneg_int(state.account_points, 0)) if state.account_points is not None else None
    )
    server_rank = safe_optional_finite_int(state.account_rank)
    if server_rank is not None:
        server_rank = max(0, min(MAX_JSON_SAFE, server_rank))
    baseline_pts = cap_eta_float(max(safe_big_float(server_pts or 0), est_pts))

    az = attack_zone_for_points(baseline_pts)
    rate_hr = cap_eta_float(safe_finite_float(econ["pts_per_hr_steady"], 0.0))
    newbie_rem = cap_eta_float(max(0.0, NEWBIE_PROTECTION_POINTS - baseline_pts))

    return {
        "current": {
            "server_points": server_pts,
            "server_rank": server_rank,
            "baseline_points": round(baseline_pts, 1),
            **est,
            "attack_zone": az,
            "newbie_protected": baseline_pts < NEWBIE_PROTECTION_POINTS,
            "newbie_remaining_pts": round(newbie_rem, 1),
            "newbie_eta_steady": format_eta(cap_eta_float(newbie_rem / rate_hr) if rate_hr > 0 else None),
            "newbie_eta_compound": format_eta(
                solve_compound_eta_hours(baseline_pts, rate_hr, float(NEWBIE_PROTECTION_POINTS))
            ),
        },
        "rates": econ,
        "horizons": _build_ranking_horizons(baseline_pts, rate_hr),
        "milestones": _build_ranking_milestones(baseline_pts, rate_hr, az),
        "observed_trend": _compute_observed_trend(history),
    }


def record_rank_snapshot(
    history_path: Path | None,
    state: GameState,
    *,
    max_entries: int = 240,
    min_interval_seconds: float = 300.0,
) -> list[dict[str, object]]:
    """Append a deduplicated rank/points observation to ``history_path`` (JSON) and return samples."""
    return _record_rank_snapshot_impl(
        history_path,
        state,
        estimate_account_points,
        max_entries=max_entries,
        min_interval_seconds=min_interval_seconds,
    )


def build_combat_overview(
    state: GameState,
    planet: PlanetState | None = None,
    *,
    safety_margin: float = 1.4,
) -> dict[str, object]:
    """Build combat readiness, AZ bounds, stationed ships/defense, and Escape flight data."""
    est = estimate_account_points(state)
    srv_pts = safe_nonneg_int(state.account_points, 0) if state.account_points is not None else 0
    baseline_pts = max(safe_big_float(srv_pts), safe_big_float(est["estimated_points"]))
    az = attack_zone_for_points(baseline_pts)
    return build_combat_overview_payload(
        state,
        planet,
        baseline_pts=baseline_pts,
        attack_zone=az,
        newbie_protection_points=NEWBIE_PROTECTION_POINTS,
        safety_margin=safety_margin,
    )
