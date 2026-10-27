#!/usr/bin/env python3
"""Read-only check of adaptive AI-Planner execution (Plan v2, v3 and v4, `ai_adaptive_execution`).

Prints the measurements that `docs/notes/adaptive-execution-plan-v2.md` uses to decide whether the
plan worked. It talks only to the local HTTP bridge: the cached `GET /dashboard?refresh=false` and
the `ai_planner_get` / `fleet_planner_get` / `notifications_list` reads, which work on the state the
bridge already holds. It makes no browser page views and sends no game actions.

Usage (stack running):
    python3 scripts/validate_adaptive_execution.py [--api-url http://127.0.0.1:8787] [--no-record]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Optional
import urllib.request

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from _validate_adaptive_sections import (  # noqa: E402
    BASELINE_BUILDERS_BUSY,
    BASELINE_COLONIES,
    BASELINE_METAL_PER_HOUR,
    EMPIRE_SMELT_TAG,
    FILL_PREFIX,
    FURNACE_LEVELS_AT_FOLLOWUP,
    FURNACE_LEVELS_AT_V4,
    GATE_CARGO_DELIVERED_PCT,
    GATE_PREQUEUE_PCT,
    LOAN_TAG,
    MIN_RUNNING_HOURS_TO_JUDGE,
    PLAN_V3_BUILDERS_BUSY,
    PLAN_V3_DELIVERED_CARGO_PER_HOUR,
    PLAN_V3_IDLE_AFFORDABLE_PCT,
    PLAN_V3_LEVELS_PER_HOUR,
    PLAN_V3_METAL_PER_HOUR,
    RAID_ALLOWANCE_CARGO,
    RES_KEYS,
    STOPPED_GAP_SECONDS,
    UTC,
    V4_BUILDERS_NO_EFFECT,
    V4_BUILDERS_WORKED,
    V4_IDLE_AFFORDABLE_MAX_PCT,
    V4_LEVELS_PER_HOUR,
    V4_METAL_SHARE_OF_CAPACITY,
    ReportMetrics,
    coerce_num,
    emit,
    fmt_ts,
    report_builders_and_idle,
    report_furnaces,
    report_plan_and_adherence,
    report_running_time,
    report_transports,
)
from _validate_adaptive_summary import (  # noqa: E402
    report_raid_and_plan_v4,
    report_summary,
)

T_CODE_LANDED = dt.datetime(2026, 11, 19, 12, 56, 58, tzinfo=UTC).timestamp()
T_SWITCH_ON = dt.datetime(2026, 11, 19, 14, 36, 33, tzinfo=UTC).timestamp()
T_FOLLOWUP_LANDED = dt.datetime(2026, 11, 19, 18, 26, 36, tzinfo=UTC).timestamp()
T_V4_LANDED = dt.datetime(2026, 11, 20, 7, 51, 42, tzinfo=UTC).timestamp()

FIELDS_REFERENCE: tuple[tuple[str, float, int], ...] = (
    ("before, switch off", dt.datetime(2026, 11, 19, 6, 44, 0, tzinfo=UTC).timestamp(), 1057),
    ("hand-off, switch on 1.9 h", dt.datetime(2026, 11, 19, 16, 30, 0, tzinfo=UTC).timestamp(), 1098),
    ("plan v4 landed", T_V4_LANDED, 1242),
)

FURNACE_CAPACITY_READINGS: tuple[tuple[float, float], ...] = (
    (T_V4_LANDED, 427_080.0),
    (dt.datetime(2026, 11, 20, 7, 58, tzinfo=UTC).timestamp(), 427_080.0),
    (dt.datetime(2026, 11, 20, 8, 23, tzinfo=UTC).timestamp(), 466_600.0),
    (dt.datetime(2026, 11, 20, 8, 39, tzinfo=UTC).timestamp(), 476_620.0),
    (dt.datetime(2026, 11, 20, 8, 53, tzinfo=UTC).timestamp(), 512_440.0),
)
CAPACITY_LOG = Path.home() / ".cache" / "spaceinvasion-autoplayer" / "furnace_capacity_readings.jsonl"

_fmt = fmt_ts
_num = coerce_num


def _get(base: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    url = f"{base}{path}"
    req = (
        urllib.request.Request(url)
        if payload is None
        else urllib.request.Request(
            url, data=json.dumps(payload, allow_nan=False).encode(), headers={"Content-Type": "application/json"}
        )
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data: object = json.loads(resp.read().decode())
        return dict(data) if isinstance(data, dict) else {}


def _weighted_builders(rows: list[tuple[float, int]], lo: float, hi: float, now: float) -> tuple[float, float, int]:
    """Return `(time-weighted mean busy builders, running hours covered, points)` for `[lo, hi)`."""
    num = den = 0.0
    count = 0
    for idx, (ts, busy) in enumerate(rows):
        if not lo <= ts < hi:
            continue
        nxt = rows[idx + 1][0] if idx + 1 < len(rows) else now
        weight = max(0.0, min(STOPPED_GAP_SECONDS, min(nxt, hi) - ts))
        num += busy * weight
        den += weight
        count += 1
    return (num / den if den > 0 else float("nan")), den / 3600.0, count


def _furnace_capacity_per_hour(
    planets: list[dict[str, Any]], economies: dict[str, Any], speedup: float
) -> Optional[float]:
    """Metal per hour with every furnace at 100% (needs the repo's game data; `None` when unavailable)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from spaceinvasion.core import catalog as game_data
    except (ImportError, OSError, AttributeError, ValueError, TypeError):
        return None
    total = 0.0
    for p in planets:
        blds = p.get("buildings") or {}
        level = int(_num(blds.get("b2", blds.get("2"))))
        ratio = _num((economies.get(str(p.get("planet_id"))) or {}).get("prod_ratio"), 1.0) or 1.0
        total += float(game_data.blast_furnace_pig_iron_used(level)) * speedup * ratio / 2.0
    return total


def _read_capacity_log(path: Path) -> list[tuple[float, float]]:
    """Furnace capacity readings earlier runs left in `path` as `(time, Metal per hour)`; none if unreadable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[tuple[float, float]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ts, cap = _num(row.get("ts")), _num(row.get("capacity"))
        if ts > 0 and cap > 0:
            out.append((ts, cap))
    return out


def _append_capacity_log(path: Path, ts: float, capacity: float, furnace_levels: int) -> bool:
    """Add this run's reading to `path`; `False` when the file cannot be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            row = {"ts": round(ts, 1), "capacity": round(capacity, 1), "furnace_levels": furnace_levels}
            fh.write(json.dumps(row, allow_nan=False) + "\n")
        return True
    except OSError:
        return False


def _capacity_range_at(readings: list[tuple[float, float]], ts: float) -> tuple[float, float, float]:
    """Return `(lowest, estimate, highest)` furnace capacity at `ts` from `readings` sorted by time."""
    if ts <= readings[0][0]:
        return readings[0][1], readings[0][1], readings[0][1]
    for (t0, c0), (t1, c1) in zip(readings, readings[1:], strict=False):
        if t0 <= ts <= t1:
            frac = (ts - t0) / (t1 - t0) if t1 > t0 else 1.0
            return min(c0, c1), c0 + (c1 - c0) * frac, max(c0, c1)
    return readings[-1][1], readings[-1][1], readings[-1][1]


def _capacity_shares(
    points: list[tuple[float, float]], readings: list[tuple[float, float]]
) -> tuple[float, float, float]:
    """Return `(lowest, estimate, highest)` share of the furnace capacity that `points` used."""
    made = cap_low = cap_est = cap_high = peak = 0.0
    for ts, metal in points:
        low, est, high = _capacity_range_at(readings, ts)
        peak = max(peak, metal)
        low = max(low, peak)
        made += metal
        cap_low += low
        cap_est += max(est, low)
        cap_high += max(high, low)
    if cap_low <= 0:
        return float("nan"), float("nan"), float("nan")
    return made / cap_high, made / cap_est, made / cap_low


def _report_progress_and_capacity(
    dash: dict[str, Any],
    state: dict[str, Any],
    planets: list[dict[str, Any]],
    series: list[tuple[float, int, float]],
    periods: tuple[tuple[str, float, float], ...],
    capacity: float | None,
    capacity_log: Path,
    no_record: bool,
    now: float,
) -> tuple[float, float, float, float, float, list[tuple[float, float]]]:
    emit("\n[7] Building levels completed")
    fields = sum(int(_num(p.get("fields_used"))) for p in planets)
    emit(f"    now: {fields}")
    levels_per_hour_v4 = float("nan")
    for label, ref_ts, ref_fields in FIELDS_REFERENCE:
        hours = (now - ref_ts) / 3600.0
        if hours > 0:
            rate = (fields - ref_fields) / hours
            emit(
                f"    since {label} ({_fmt(ref_ts)}, {ref_fields}): +{fields - ref_fields} in {hours:.1f} h = {rate:.2f} per hour"
            )
            if ref_ts == T_V4_LANDED:
                levels_per_hour_v4 = rate
    emit(f"    plan v3 made {PLAN_V3_LEVELS_PER_HOUR} per hour (5.1 before the switch)")
    emit(f"    research levels: {state.get('researches')}")
    furnace_levels = sum(
        int(_num((p.get("buildings") or {}).get("b2", (p.get("buildings") or {}).get("2")))) for p in planets
    )
    emit(
        f"    Blast furnace levels, all colonies: {furnace_levels} "
        f"({FURNACE_LEVELS_AT_FOLLOWUP} when plan v3 landed, {FURNACE_LEVELS_AT_V4} when plan v4 landed)"
    )
    emit("    Metal produced per hour while running (mean of the recorded points):")
    metal_v4 = float("nan")
    for label, lo, hi in periods:
        vals = [metal for ts, _busy, metal in series if lo <= ts < hi]
        if vals:
            emit(
                f"      {label:26s}: {statistics.mean(vals):>9,.0f}  median {statistics.median(vals):>9,.0f}  ({len(vals)} points)"
            )
            if label.startswith("E"):
                metal_v4 = statistics.mean(vals)
    now_prod = dash.get("current_hourly_production") or {}
    emit(
        f"      now: Metal {_num(now_prod.get('metal_per_hour')):,.0f}  Pig iron {_num(now_prod.get('pig_iron_per_hour')):,.0f}  "
        f"Kryptonite {_num(now_prod.get('kryptonite_per_hour')):,.0f}  Spice {_num(now_prod.get('spice_per_hour')):,.0f}"
        f"   (before the switch: Metal {BASELINE_METAL_PER_HOUR:,}; plan v3: {PLAN_V3_METAL_PER_HOUR:,})"
    )
    share_low = share_est = share_high = float("nan")
    readings: list[tuple[float, float]] = []
    if capacity:
        logged = [r for r in _read_capacity_log(capacity_log) if T_V4_LANDED <= r[0] <= now]
        readings = sorted(
            r for r in (set(FURNACE_CAPACITY_READINGS) | set(logged) | {(now, float(capacity))}) if r[0] <= now
        )
        points_v4 = [(ts, metal) for ts, _busy, metal in series if ts >= T_V4_LANDED]
        if points_v4:
            share_low, share_est, share_high = _capacity_shares(points_v4, readings)
        kept = (not no_record) and _append_capacity_log(capacity_log, now, float(capacity), furnace_levels)
        emit(
            f"    furnace capacity: {readings[0][1]:,.0f} Metal per hour at the landing, {capacity:,.0f} now; "
            f"{len(readings)} readings since the landing ({len(logged)} from earlier runs on this machine)"
        )
        emit(f"      this run's reading {'was added to' if kept else 'was not added to'} {capacity_log}")
        if share_est == share_est:
            emit(
                f"      plan v4 made {100.0 * share_est:.0f}% of what the furnaces could make at the time "
                f"(between {100.0 * share_low:.0f}% and {100.0 * share_high:.0f}%, depending on when the levels between two readings were built)"
            )
    return levels_per_hour_v4, metal_v4, share_low, share_est, share_high, readings


def main() -> int:
    desc = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("--api-url", default="http://127.0.0.1:8787")
    parser.add_argument("--capacity-log", type=Path, default=CAPACITY_LOG, help="furnace capacity reading log")
    parser.add_argument("--no-record", action="store_true", help="do not append this run's furnace capacity")
    args = parser.parse_args()
    base, now = args.api_url.rstrip("/"), time.time()

    dash = _get(base, "/dashboard?refresh=false")
    ai = _get(base, "/command", {"command": "ai_planner_get"})
    notes = _get(base, "/command", {"command": "notifications_list", "status": "all", "limit": 200})
    fleet_view = _get(base, "/command", {"command": "fleet_planner_get"})
    health = _get(base, "/health")

    state, cfg = dash.get("state") or {}, dash.get("config") or {}
    planets, queues = state.get("planets") or [], state.get("active_queues") or []
    adaptive, economies, plan = (
        ai.get("adaptive_status") or {},
        dash.get("planet_economies") or {},
        dash.get("plan") or [],
    )

    emit(
        f"now {_fmt(now)} | {(now - T_SWITCH_ON) / 3600.0:.1f} h since the switch was turned on, "
        f"{(now - T_FOLLOWUP_LANDED) / 3600.0:.1f} h since plan v3 landed, {(now - T_V4_LANDED) / 3600.0:.1f} h since plan v4 landed"
    )
    emit(f"switch ai_adaptive_execution={cfg.get('ai_adaptive_execution')}  planner_mode={cfg.get('planner_mode')}")
    if not cfg.get("ai_adaptive_execution"):
        emit("  WARNING: the switch is off; everything below describes the old behaviour.")
    if len(planets) != BASELINE_COLONIES:
        emit(
            f"  WARNING: {len(planets)} colonies now, {BASELINE_COLONIES} at the baseline. "
            "Compare shares of builders busy, not counts."
        )

    series = sorted(
        (_num(h.get("recorded_at")), int(_num(h.get("active_builders"))), _num(h.get("metal_per_hour")))
        for h in (dash.get("hourly_production_history") or [])
        if int(_num(h.get("planet_count"))) >= BASELINE_COLONIES and _num(h.get("recorded_at")) > 0
    )
    periods = (
        ("A before the code landed", 0.0, T_CODE_LANDED),
        ("B code landed, switch off", T_CODE_LANDED, T_SWITCH_ON),
        ("C switch on, plan v2 only", T_SWITCH_ON, T_FOLLOWUP_LANDED),
        ("D switch on, plan v3", T_FOLLOWUP_LANDED, T_V4_LANDED),
        ("E switch on, plan v4", T_V4_LANDED, float("inf")),
    )
    rows, stops, after_v4 = report_running_time(series, notes, now, T_SWITCH_ON, T_V4_LANDED)
    on_mean, on_cov, idle_aff, ready_gap = report_builders_and_idle(
        rows, periods, adaptive, now, T_V4_LANDED, _weighted_builders
    )
    capacity = _furnace_capacity_per_hour(planets, economies, _num(state.get("speedup_factor"), 1.0) or 1.0)
    furnaces_on = report_furnaces(planets, adaptive, economies, capacity)
    parked_large, flying_large, delivered_pct = report_transports(planets, queues, adaptive, ai, plan)
    adherence_txt = report_plan_and_adherence(base, dash, ai, state, planets, adaptive, now, _get)
    levels_v4, metal_v4, share_low, share_est, share_high, readings = _report_progress_and_capacity(
        dash, state, planets, series, periods, capacity, args.capacity_log, args.no_record, now
    )
    live_basis, plan_basis, loot_share = report_raid_and_plan_v4(
        dash, ai, fleet_view, state, planets, queues, plan, health, now
    )
    metrics = ReportMetrics(
        stops=stops,
        after_v4_gates=after_v4,
        on_mean=on_mean,
        on_cov=on_cov,
        idle_aff=idle_aff,
        ready_gap=ready_gap,
        furnaces_on=furnaces_on,
        capacity=capacity,
        parked_large=parked_large,
        flying_large=flying_large,
        delivered_pct=delivered_pct,
        adherence_txt=adherence_txt,
        levels_per_hour_v4=levels_v4,
        metal_v4=metal_v4,
        share_low=share_low,
        share_est=share_est,
        share_high=share_high,
        readings=readings,
        live_basis=live_basis,
        plan_basis=plan_basis,
        loot_share=loot_share,
    )
    report_summary(metrics, adaptive, planets, now, T_V4_LANDED)
    return 0


__all__ = [
    "BASELINE_BUILDERS_BUSY",
    "BASELINE_COLONIES",
    "BASELINE_METAL_PER_HOUR",
    "CAPACITY_LOG",
    "EMPIRE_SMELT_TAG",
    "FIELDS_REFERENCE",
    "FILL_PREFIX",
    "FURNACE_CAPACITY_READINGS",
    "FURNACE_LEVELS_AT_FOLLOWUP",
    "FURNACE_LEVELS_AT_V4",
    "GATE_CARGO_DELIVERED_PCT",
    "GATE_PREQUEUE_PCT",
    "LOAN_TAG",
    "MIN_RUNNING_HOURS_TO_JUDGE",
    "PLAN_V3_BUILDERS_BUSY",
    "PLAN_V3_DELIVERED_CARGO_PER_HOUR",
    "PLAN_V3_IDLE_AFFORDABLE_PCT",
    "PLAN_V3_LEVELS_PER_HOUR",
    "PLAN_V3_METAL_PER_HOUR",
    "RAID_ALLOWANCE_CARGO",
    "RES_KEYS",
    "STOPPED_GAP_SECONDS",
    "T_CODE_LANDED",
    "T_FOLLOWUP_LANDED",
    "T_SWITCH_ON",
    "T_V4_LANDED",
    "V4_BUILDERS_NO_EFFECT",
    "V4_BUILDERS_WORKED",
    "V4_IDLE_AFFORDABLE_MAX_PCT",
    "V4_LEVELS_PER_HOUR",
    "V4_METAL_SHARE_OF_CAPACITY",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
