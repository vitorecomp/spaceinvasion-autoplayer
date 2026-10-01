#!/usr/bin/env python3
"""Read-only check of adaptive AI-Planner execution (Plan v2, v3 and v4, `ai_adaptive_execution`).

Prints the measurements that `docs/notes/adaptive-execution-plan-v2.md` uses to decide whether the
plan worked. It talks only to the local HTTP bridge: the cached `GET /dashboard?refresh=false` and
the `ai_planner_get` / `fleet_planner_get` / `notifications_list` reads, which work on the state the
bridge already holds. It makes no browser page views and sends no game actions.

It writes one thing: a line per run in `~/.cache/spaceinvasion-autoplayer/furnace_capacity_readings.jsonl`,
outside the repository, holding the Metal per hour the furnaces could make at that moment. The recorded
series does not hold that figure and mark 1 needs it over time. `--no-record` skips the line.

Usage (stack running):
    python3 scripts/validate_adaptive_execution.py [--api-url http://127.0.0.1:8787] [--no-record]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

UTC = dt.timezone.utc

# Fixed reference times (UTC). The operator's local time is UTC-03:00.
T_CODE_LANDED = dt.datetime(2026, 11, 19, 12, 56, 58, tzinfo=UTC).timestamp()  # first Plan v2 merge into main
T_SWITCH_ON = dt.datetime(2026, 11, 19, 14, 36, 33, tzinfo=UTC).timestamp()  # ai_adaptive_execution -> true
# Plan v3 follow-up live: realized raid income, hourly ship budgets, batched transport orders.
T_FOLLOWUP_LANDED = dt.datetime(2026, 11, 19, 18, 26, 36, tzinfo=UTC).timestamp()  # commit 6ea7736 live
# Plan v4 live: empire smelting arm, live fill rule, plan-order loans, logistics target without raiding hulls.
T_V4_LANDED = dt.datetime(2026, 11, 20, 7, 51, 42, tzinfo=UTC).timestamp()  # commit 17a66a6 live

# A gap longer than this between two recorded points means the bot was not running
# (in practice: stopped at an anti-bot gate until the operator cleared it, or restarting).
STOPPED_GAP_SECONDS = 300.0

# Building levels completed across all colonies (`fields_used` equals the sum of building levels).
FIELDS_REFERENCE: tuple[tuple[str, float, int], ...] = (
    ("before, switch off", dt.datetime(2026, 11, 19, 6, 44, 0, tzinfo=UTC).timestamp(), 1057),
    ("hand-off, switch on 1.9 h", dt.datetime(2026, 11, 19, 16, 30, 0, tzinfo=UTC).timestamp(), 1098),
    ("plan v4 landed", T_V4_LANDED, 1242),
)

# Baseline measured before any Plan v2 code landed (period A below, 6.24 running hours).
BASELINE_BUILDERS_BUSY = 2.10  # of 11, time-weighted over running time
BASELINE_COLONIES = 11
# Metal the furnaces produced per hour while the bot ran, before the switch (405 points; median 0).
BASELINE_METAL_PER_HOUR = 50_556
# Sum of Blast furnace levels over all colonies when the Plan v3 follow-up landed, and when plan v4 landed.
FURNACE_LEVELS_AT_FOLLOWUP = 150
FURNACE_LEVELS_AT_V4 = 174

# Plan v3 as measured on 2026-11-20 at 06:15 UTC (period D then: 11.18 running hours).
PLAN_V3_BUILDERS_BUSY = 1.76
PLAN_V3_METAL_PER_HOUR = 245_390
PLAN_V3_LEVELS_PER_HOUR = 9.3
PLAN_V3_IDLE_AFFORDABLE_PCT = 78.6
PLAN_V3_DELIVERED_CARGO_PER_HOUR = 140_803

# Pass marks for plan v4, set by the assistant on 2026-11-20 (the operator asked not to be asked).
# They are judged on period E after at least 24 hours and 12 running hours. The empire is limited by
# its income, not by its builders, so the marks are about using what it has.
V4_METAL_SHARE_OF_CAPACITY = 0.80  # Metal made while running / what the furnaces could make at the time
V4_BUILDERS_WORKED = 3.0  # builders busy of 11, time-weighted while running
V4_BUILDERS_NO_EFFECT = 2.3
V4_LEVELS_PER_HOUR = 12.0  # building levels completed per elapsed hour
V4_IDLE_AFFORDABLE_MAX_PCT = 50.0  # idle with an affordable target step, share of colony time
MIN_RUNNING_HOURS_TO_JUDGE = 12.0

# Metal per hour all furnaces could make, as this script read it on 2026-11-20. Plan v4 builds furnace
# levels itself (174 at the landing, 183 an hour later), so the capacity is not one number: a period's
# mean divided by the capacity at the end understates how much of it was used. Mark 1 compares every
# recorded point with the capacity at its own time. The series does not record the capacity, so it is
# read off these points, the lines earlier runs left in `CAPACITY_LOG`, and the reading of the run itself.
FURNACE_CAPACITY_READINGS: tuple[tuple[float, float], ...] = (
    (T_V4_LANDED, 427_080.0),  # same 174 furnace levels as at 07:58
    (dt.datetime(2026, 11, 20, 7, 58, tzinfo=UTC).timestamp(), 427_080.0),
    (dt.datetime(2026, 11, 20, 8, 23, tzinfo=UTC).timestamp(), 466_600.0),
    (dt.datetime(2026, 11, 20, 8, 39, tzinfo=UTC).timestamp(), 476_620.0),
    (dt.datetime(2026, 11, 20, 8, 53, tzinfo=UTC).timestamp(), 512_440.0),
)
CAPACITY_LOG = Path.home() / ".cache" / "spaceinvasion-autoplayer" / "furnace_capacity_readings.jsonl"

# Gates plan v2 defined for its optional steps (kept for reference).
GATE_PREQUEUE_PCT = 5.0  # ready but not started, share of colony time
GATE_CARGO_DELIVERED_PCT = 80.0  # delivered vs planned cargo per hour

RES_KEYS = ("pig_iron", "metal", "kryptonite", "spice")
# Plan v4 fleet ceiling: twice the plan's cargo target, and never more than this above it.
RAID_ALLOWANCE_CARGO = 900_000
FILL_PREFIX = "AI-Planner fill #"
LOAN_TAG = "[Plan order loan]"
EMPIRE_SMELT_TAG = "AI-Planner empire"

_HULL_RE = re.compile(r"(\d[\d.,]*)\s*x?\s*(Small transport|Large transport|Transmitter)", re.IGNORECASE)
_HULL_CARGO = {"small transport": 6_000, "large transport": 30_000, "transmitter": 250_000}


def _get(base: str, path: str, payload: Optional[dict] = None) -> dict:
    url = f"{base}{path}"
    req = (
        urllib.request.Request(url)
        if payload is None
        else urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _fmt(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _num(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


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


def _furnace_capacity_per_hour(planets: list[dict], economies: dict, speedup: float) -> Optional[float]:
    """Metal per hour with every furnace at 100% (needs the repo's game data; `None` when it cannot be loaded)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from spaceinvasion.core import game_data  # noqa: PLC0415 - optional, read-only table lookup
    except Exception:  # noqa: BLE001 - the script still works without the table
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
    """Add this run's reading to `path`; `False` when the file cannot be written (the run goes on)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            row = {"ts": round(ts, 1), "capacity": round(capacity, 1), "furnace_levels": furnace_levels}
            fh.write(json.dumps(row) + "\n")
        return True
    except OSError:
        return False


def _capacity_range_at(readings: list[tuple[float, float]], ts: float) -> tuple[float, float, float]:
    """Return `(lowest, estimate, highest)` furnace capacity at `ts` from `readings` sorted by time.

    Between two readings the capacity is taken to lie between them, since furnace levels are built and
    not removed, and the estimate is the straight line from one to the other.
    """
    if ts <= readings[0][0]:
        return readings[0][1], readings[0][1], readings[0][1]
    for (t0, c0), (t1, c1) in zip(readings, readings[1:]):
        if t0 <= ts <= t1:
            frac = (ts - t0) / (t1 - t0) if t1 > t0 else 1.0
            return min(c0, c1), c0 + (c1 - c0) * frac, max(c0, c1)
    return readings[-1][1], readings[-1][1], readings[-1][1]


def _capacity_shares(
    points: list[tuple[float, float]], readings: list[tuple[float, float]]
) -> tuple[float, float, float]:
    """Return `(lowest, estimate, highest)` share of the furnace capacity that `points` used.

    `points` are `(time, Metal per hour)` of one period in time order. The share is the Metal made over
    the Metal that could have been made, with the capacity taken at the time of each point. A point
    also shows the capacity was at least what was being made, then and from then on.
    """
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--capacity-log", type=Path, default=CAPACITY_LOG, help="file that keeps the furnace capacity read by each run"
    )
    parser.add_argument("--no-record", action="store_true", help="do not add this run's furnace capacity to that file")
    args = parser.parse_args()
    base = args.api_url.rstrip("/")

    now = time.time()
    dash = _get(base, "/dashboard?refresh=false")
    ai = _get(base, "/command", {"command": "ai_planner_get"})
    notes = _get(base, "/command", {"command": "notifications_list", "status": "all", "limit": 200})
    fleet_view = _get(base, "/command", {"command": "fleet_planner_get"})
    health = _get(base, "/health")

    state = dash.get("state") or {}
    cfg = dash.get("config") or {}
    planets = state.get("planets") or []
    queues = state.get("active_queues") or []
    adaptive = ai.get("adaptive_status") or {}
    economies = dash.get("planet_economies") or {}
    plan = dash.get("plan") or []

    print(
        f"now {_fmt(now)} | {(now - T_SWITCH_ON) / 3600.0:.1f} h since the switch was turned on, "
        f"{(now - T_FOLLOWUP_LANDED) / 3600.0:.1f} h since plan v3 landed, {(now - T_V4_LANDED) / 3600.0:.1f} h since plan v4 landed"
    )
    print(f"switch ai_adaptive_execution={cfg.get('ai_adaptive_execution')}  planner_mode={cfg.get('planner_mode')}")
    if not cfg.get("ai_adaptive_execution"):
        print("  WARNING: the switch is off; everything below describes the old behaviour.")
    if len(planets) != BASELINE_COLONIES:
        print(
            f"  WARNING: {len(planets)} colonies now, {BASELINE_COLONIES} at the baseline. "
            "Compare shares of builders busy, not counts."
        )

    # ------------------------------------------------------------------ 1. running time and stops
    series = sorted(
        (_num(h.get("recorded_at")), int(_num(h.get("active_builders"))), _num(h.get("metal_per_hour")))
        for h in (dash.get("hourly_production_history") or [])
        if int(_num(h.get("planet_count"))) >= BASELINE_COLONIES and _num(h.get("recorded_at")) > 0
    )
    rows = [(ts, busy) for ts, busy, _metal in series]
    print("\n[1] Running time (the series is only recorded while the bot runs)")
    stops: list[tuple[float, float]] = []
    if rows:
        span_h = (rows[-1][0] - rows[0][0]) / 3600.0
        stops = [(a, b) for (a, _), (b, _) in zip(rows, rows[1:]) if b - a > STOPPED_GAP_SECONDS]
        stopped_h = sum(b - a for a, b in stops) / 3600.0
        print(f"    series {_fmt(rows[0][0])} .. {_fmt(rows[-1][0])}: {len(rows)} points over {span_h:.1f} h")
        print(f"    stopped for {stopped_h:.1f} h of that ({100.0 * stopped_h / span_h if span_h else 0:.0f}%), in {len(stops)} stops:")
        for a, b in stops:
            tag = "  [plan v4]" if a >= T_V4_LANDED else ("  [after switch-on]" if a >= T_SWITCH_ON else "")
            print(f"      {_fmt(a)} -> {_fmt(b)}  ({(b - a) / 60.0:.0f} min){tag}")
    gates = sorted(
        _num(it.get("created_ts"))
        for it in (notes.get("items") or [])
        if str(it.get("category") or "") == "captcha_gate" and _num(it.get("created_ts")) > 0
    )
    since = rows[0][0] if rows else now - 86400.0
    before = [g for g in gates if since <= g < T_SWITCH_ON]
    after = [g for g in gates if g >= T_SWITCH_ON]
    after_v4 = [g for g in gates if g >= T_V4_LANDED]
    print(f"    anti-bot gate alerts since {_fmt(since)}: {len(before)} before the switch, {len(after)} after, {len(after_v4)} since plan v4")
    print(f"      after: {[_fmt(g) for g in after]}")

    # ------------------------------------------------------------------ 2. builders busy
    print("\n[2] Builders busy while running (time-weighted, of 11 colonies)")
    periods = (
        ("A before the code landed", 0.0, T_CODE_LANDED),
        ("B code landed, switch off", T_CODE_LANDED, T_SWITCH_ON),
        ("C switch on, plan v2 only", T_SWITCH_ON, T_FOLLOWUP_LANDED),
        ("D switch on, plan v3", T_FOLLOWUP_LANDED, T_V4_LANDED),
        ("E switch on, plan v4", T_V4_LANDED, float("inf")),
    )
    on_mean = float("nan")
    on_cov = 0.0
    for label, lo, hi in periods:
        mean, cov, cnt = _weighted_builders(rows, lo, hi, now)
        pts = [b for ts, b in rows if lo <= ts < hi]
        extra = f"  median {statistics.median(pts):.0f}  max {max(pts)}" if pts else ""
        print(f"    {label:26s}: {mean:5.2f}  over {cov:5.2f} running hours, {cnt} points{extra}")
        if label.startswith("E"):
            on_mean, on_cov = mean, cov
    for win_h in (6, 12, 24, 48):
        lo = max(T_V4_LANDED, now - win_h * 3600.0)
        if now - T_V4_LANDED >= win_h * 3600.0:
            mean, cov, cnt = _weighted_builders(rows, lo, float("inf"), now)
            print(f"    plan v4, last {win_h:2d} h        : {mean:5.2f}  over {cov:5.2f} running hours, {cnt} points")
    print(
        f"    before any change {BASELINE_BUILDERS_BUSY:.2f} | plan v3 {PLAN_V3_BUILDERS_BUSY:.2f} | "
        f"plan v4 worked at {V4_BUILDERS_WORKED:.1f} or more | no effect below {V4_BUILDERS_NO_EFFECT:.1f}"
    )

    # ------------------------------------------------------------------ 3. colony states
    print("\n[3] Why colonies are idle")
    print(f"    now: {adaptive.get('active_builders')}/{adaptive.get('total_colonies')} building  {adaptive.get('counts')}")
    window = adaptive.get("window_24h") or {}
    idle_aff = _num(window.get("idle_with_affordable_share_pct"), float("nan"))
    ready_gap = _num(window.get("ready_not_started_share_pct"), float("nan"))
    print(f"    recorded window: {window.get('bucket_count')} ten-minute buckets, {window.get('colony_samples')} colony samples")
    print(f"    shares %: {window.get('state_shares_pct')}")
    print(
        f"    idle with an affordable target step: {idle_aff}% (plan v3: {PLAN_V3_IDLE_AFFORDABLE_PCT}%)   "
        f"ready but not started: {ready_gap}%"
    )
    print("    (the window is the last 24 h of recorded buckets: it mixes plan v3 and plan v4 until 24 h after the landing)")

    # ------------------------------------------------------------------ 4. furnaces
    print("\n[4] Blast furnaces")
    diag = {c.get("coords"): c for c in (adaptive.get("colonies") or [])}
    furnaces_on = 0
    pig_at_cap = 0
    metal_now = 0.0
    for p in planets:
        pct = int(_num((p.get("economy_rates") or {}).get("resource2")))
        furnaces_on += 1 if pct > 0 else 0
        res, sto = p.get("resources") or {}, p.get("storage") or {}
        pig, pig_cap = int(_num(res.get("pig_iron"))), int(_num(sto.get("pig_iron")))
        pig_at_cap += 1 if pig_cap and pig >= pig_cap else 0
        c = diag.get(p.get("coords")) or {}
        econ = economies.get(str(p.get("planet_id"))) or {}
        metal_now += _num(econ.get("metal_per_hr"))
        print(
            f"    {p.get('coords')} furnace {pct:3d}%  metal {int(_num(res.get('metal'))):>9,}  pig {pig:>9,}/{pig_cap:<9,} "
            f"energy margin {int(_num(c.get('b2_energy_margin_at_100'))):>5}  {str(c.get('state')):18s} {str(c.get('reason'))[:60]}"
        )
    capacity = _furnace_capacity_per_hour(planets, economies, _num(state.get("speedup_factor"), 1.0) or 1.0)
    print(f"    furnaces on: {furnaces_on}/{len(planets)}   colonies with Pig iron at the warehouse cap: {pig_at_cap}")
    if capacity:
        print(f"    Metal per hour now {metal_now:,.0f} of {capacity:,.0f} the furnaces can make ({100.0 * metal_now / capacity:.0f}%)")
    else:
        print(f"    Metal per hour now {metal_now:,.0f} (furnace capacity unknown: the repo's game data could not be loaded)")

    # ------------------------------------------------------------------ 5. transports
    print("\n[5] Transport fleet against the plan")
    parked = {"s1": 0, "s2": 0, "s3": 0}
    for p in planets:
        for key in parked:
            parked[key] += int(_num((p.get("ships") or {}).get(key)))
    flying = {"small transport": 0, "large transport": 0, "transmitter": 0}
    by_mission: dict[str, dict[str, int]] = {}
    for q in queues:
        if q.get("category") != "fleet":
            continue
        mission = (str(q.get("name") or "").split() or ["?"])[0].lower()
        for cnt_txt, kind in _HULL_RE.findall(str(q.get("detail") or "")):
            n = int(cnt_txt.replace(".", "").replace(",", ""))
            flying[kind.lower()] += n
            bucket = by_mission.setdefault(mission, {"small transport": 0, "large transport": 0, "transmitter": 0})
            bucket[kind.lower()] += n
    fleet = adaptive.get("transport_fleet") or {}
    demand = ai.get("transport_demand") or {}
    delivered_pct = _num(fleet.get("delivered_vs_planned_pct"), float("nan"))
    print(f"    Small transports {parked['s1']} parked + {flying['small transport']} flying   Large transports {parked['s2']} parked + {flying['large transport']} flying   Transmitters {parked['s3']} parked + {flying['transmitter']} flying")
    for mission, hulls in sorted(by_mission.items()):
        cargo = sum(_HULL_CARGO[k] * v for k, v in hulls.items())
        print(f"      flying as {mission:10s}: {hulls['small transport']:3d} Small  {hulls['large transport']:3d} Large  {hulls['transmitter']:2d} Transmitter  = {cargo:>9,} cargo")
    raid_cargo = sum(_HULL_CARGO[k] * v for k, v in (by_mission.get("attack") or {}).items())
    total_cargo = _num(fleet.get("total_fleet_cargo"))
    target_cargo = _num(fleet.get("target_fleet_cargo"))
    print(f"    cargo capacity {fleet.get('total_fleet_cargo')} of target {fleet.get('target_fleet_cargo')}")
    print(
        f"    plan v4 counts {total_cargo - raid_cargo:,.0f} toward the target ({raid_cargo:,.0f} is away on raids); "
        f"the whole fleet stops growing at {min(2.0 * target_cargo, target_cargo + RAID_ALLOWANCE_CARGO):,.0f}"
    )
    print(
        f"    planned {demand.get('cargo_per_hour_24h')}/h   delivered {fleet.get('delivered_cargo_per_hour_24h')}/h "
        f"({delivered_pct}% of plan, {fleet.get('delivered_flights_24h')} flights; plan v3: {PLAN_V3_DELIVERED_CARGO_PER_HOUR:,}/h)   utilization {fleet.get('realized_utilization')}"
    )
    print(
        f"    target sized at utilization {fleet.get('sizing_utilization')}   "
        f"cargo ordered in the last hour {fleet.get('ordered_cargo_last_hour')}"
    )
    executable = [a for a in plan if a.get("executable_now")]
    first_ship = next((i for i, a in enumerate(executable) if a.get("kind") == "BUILD_SHIP"), None)
    ahead: dict[str, int] = {}
    for a in executable[: first_ship if first_ship is not None else len(executable)]:
        ahead[str(a.get("kind"))] = ahead.get(str(a.get("kind")), 0) + 1
    print(f"    executable actions queued ahead of the first ship build: {ahead or 'none'}")
    for a in plan:
        if a.get("kind") == "BUILD_SHIP" and "[Plan infra cargo target]" in str(a.get("reason")):
            print(f"    {a.get('planet_coords')} {a.get('target_name')} executable={a.get('executable_now')}  {str(a.get('reason'))[:90]}")

    # ------------------------------------------------------------------ 6. plan and re-tune
    print("\n[6] Plan and background re-tune")
    job = (dash.get("planner_status") or {}).get("ai_planner_job") or {}
    summary = ai.get("active_summary") or {}
    plan_ts = _num(summary.get("live_snapshot_ts"))
    if plan_ts:
        print(f"    active plan from {_fmt(plan_ts)} ({(now - plan_ts) / 3600.0:.1f} h old), {summary.get('actions_count')} steps")
    print(f"    last planner job: {job.get('status')}, trigger {job.get('trigger_source')}, run {job.get('run_id')}")
    print(f"    auto re-tune: {adaptive.get('auto_replan')}")

    # Plan adherence: what the active plan scheduled to be finished by now against what is built.
    adherence_txt = "n/a"
    if plan_ts and now > plan_ts:
        elapsed = now - plan_ts
        levels: dict[tuple[int, str], int] = {}
        for p in planets:
            for key, lvl in (p.get("buildings") or {}).items():
                norm = str(key) if str(key).startswith("b") else f"b{key}"
                levels[(int(_num(p.get("planet_id"))), norm)] = int(_num(lvl))
        research = {str(k): int(_num(v)) for k, v in (state.get("researches") or {}).items()}
        sched_done = sched_started = built = built_ahead = 0
        page = 1
        while page <= 10:
            resp = _get(base, "/command", {"command": "ai_planner_get", "page": page, "page_size": 200})
            items = (resp.get("steps_page") or {}).get("items") or []
            if not items:
                break
            for step in items:
                kind = str(step.get("kind") or "").upper()
                if kind not in ("BUILD", "RESEARCH"):
                    continue
                key = str(step.get("entity_key") or "")
                live = (
                    research.get(key, 0)
                    if kind == "RESEARCH"
                    else levels.get((int(_num(step.get("planet_id"))), key), 0)
                )
                done = live >= int(_num(step.get("target_level")))
                if _num(step.get("start_sec")) > elapsed:
                    built_ahead += 1 if done else 0
                    continue
                sched_started += 1
                sched_done += 1 if _num(step.get("finish_sec")) <= elapsed else 0
                built += 1 if done else 0
            page += 1
        adherence_txt = (
            f"{built} built of {sched_done} the plan scheduled to be finished ({sched_started} scheduled to have started); "
            f"{built_ahead} more built ahead of their scheduled start"
        )
        print(f"    plan adherence after {elapsed / 3600.0:.1f} h: {adherence_txt}")

    # ------------------------------------------------------------------ 7. progress
    print("\n[7] Building levels completed")
    fields = sum(int(_num(p.get("fields_used"))) for p in planets)
    print(f"    now: {fields}")
    levels_per_hour_v4 = float("nan")
    for label, ref_ts, ref_fields in FIELDS_REFERENCE:
        hours = (now - ref_ts) / 3600.0
        if hours > 0:
            rate = (fields - ref_fields) / hours
            print(f"    since {label} ({_fmt(ref_ts)}, {ref_fields}): +{fields - ref_fields} in {hours:.1f} h = {rate:.2f} per hour")
            if ref_ts == T_V4_LANDED:
                levels_per_hour_v4 = rate
    print(f"    plan v3 made {PLAN_V3_LEVELS_PER_HOUR} per hour (5.1 before the switch)")
    print(f"    research levels: {state.get('researches')}")
    furnace_levels = sum(
        int(_num((p.get("buildings") or {}).get("b2", (p.get("buildings") or {}).get("2")))) for p in planets
    )
    print(
        f"    Blast furnace levels, all colonies: {furnace_levels} "
        f"({FURNACE_LEVELS_AT_FOLLOWUP} when plan v3 landed, {FURNACE_LEVELS_AT_V4} when plan v4 landed)"
    )
    print("    Metal produced per hour while running (mean of the recorded points):")
    metal_v4 = float("nan")
    for label, lo, hi in periods:
        vals = [metal for ts, _busy, metal in series if lo <= ts < hi]
        if vals:
            print(f"      {label:26s}: {statistics.mean(vals):>9,.0f}  median {statistics.median(vals):>9,.0f}  ({len(vals)} points)")
            if label.startswith("E"):
                metal_v4 = statistics.mean(vals)
    now_prod = dash.get("current_hourly_production") or {}
    print(
        f"      now: Metal {_num(now_prod.get('metal_per_hour')):,.0f}  Pig iron {_num(now_prod.get('pig_iron_per_hour')):,.0f}  "
        f"Kryptonite {_num(now_prod.get('kryptonite_per_hour')):,.0f}  Spice {_num(now_prod.get('spice_per_hour')):,.0f}"
        f"   (before the switch: Metal {BASELINE_METAL_PER_HOUR:,}; plan v3: {PLAN_V3_METAL_PER_HOUR:,})"
    )
    # Share of the furnace capacity plan v4 used, with the capacity taken at the time of each point.
    share_low = share_est = share_high = float("nan")
    readings: list[tuple[float, float]] = []
    if capacity:
        logged = [r for r in _read_capacity_log(args.capacity_log) if T_V4_LANDED <= r[0] <= now]
        readings = sorted(set(FURNACE_CAPACITY_READINGS) | set(logged) | {(now, float(capacity))})
        readings = [r for r in readings if r[0] <= now]
        points_v4 = [(ts, metal) for ts, _busy, metal in series if ts >= T_V4_LANDED]
        if points_v4:
            share_low, share_est, share_high = _capacity_shares(points_v4, readings)
        kept = (not args.no_record) and _append_capacity_log(args.capacity_log, now, float(capacity), furnace_levels)
        print(
            f"    furnace capacity: {readings[0][1]:,.0f} Metal per hour at the landing, {capacity:,.0f} now; "
            f"{len(readings)} readings since the landing ({len(logged)} from earlier runs on this machine)"
        )
        print(
            f"      this run's reading {'was added to' if kept else 'was not added to'} {args.capacity_log}"
        )
        if share_est == share_est:
            print(
                f"      plan v4 made {100.0 * share_est:.0f}% of what the furnaces could make at the time "
                f"(between {100.0 * share_low:.0f}% and {100.0 * share_high:.0f}%, depending on when the levels between two readings were built)"
            )

    # ------------------------------------------------------------------ 8. request pressure
    print("\n[8] Page-view limit")
    print(f"    page views in the last 60 s: {health.get('page_views_last_60s')} of {health.get('page_view_limit_per_minute')}")
    ctrl = health.get("bot_control") or {}
    print(f"    health ok={health.get('ok')} in_game={health.get('in_game')} paused={ctrl.get('paused')} gate_active={ctrl.get('captcha_blocked')}")
    recent = (dash.get("db_summary") or {}).get("recent_actions") or []
    if recent:
        newest = max(_num(r.get("ts")) for r in recent)
        failed = sum(1 for r in recent if not r.get("executed"))
        print(f"    last {len(recent)} recorded actions: newest {(now - newest) / 60.0:.0f} min ago, {failed} did not execute")

    # ------------------------------------------------------------------ 9. raid income and ship budgets
    print("\n[9] Raid income the planner and the ship budgets assume")
    plan_tel = ai.get("attack_telemetry") or {}
    live_tel = ai.get("live_attack_telemetry") or {}
    plan_basis = str(plan_tel.get("income_basis") or "modelled")
    live_basis = str(live_tel.get("income_basis") or "modelled")
    print(f"    basis: live telemetry {live_basis}   active plan {plan_basis} ({plan_tel.get('pace_set_by')})")

    def _loot(tel: dict, key: str) -> str:
        vals = tel.get(key) or {}
        return "  ".join(f"{rk} {_num(vals.get(rk)):,.0f}" for rk in RES_KEYS)

    loot_24h = dash.get("loot_recycle_summary") or {}
    win_h = max(1.0, _num(loot_24h.get("window_hours"), 24.0))
    received = {
        rk: (_num(loot_24h.get(f"stolen_{rk}")) + _num(loot_24h.get(f"recycled_{rk}"))) / win_h for rk in RES_KEYS
    }
    print("    received per hour, last 24 h: " + "  ".join(f"{rk} {val:,.0f}" for rk, val in received.items()))
    raids = int(_num(loot_24h.get("attack_count")))
    stolen = _num(loot_24h.get("total_stolen"))
    print(
        f"      from {raids} raids ({stolen:,.0f} stolen, {stolen / raids if raids else 0:,.0f} per raid, {stolen / win_h:,.0f} per hour) "
        f"and {loot_24h.get('recycle_count')} recycling runs ({_num(loot_24h.get('total_recycled')):,.0f})"
    )
    print(f"    the active plan assumes per hour: {_loot(plan_tel, 'hourly_loot')}")
    if live_tel.get("modelled_hourly_loot"):
        print(f"    every-slot-raiding model would say: {_loot(live_tel, 'modelled_hourly_loot')}")
    loot_share = _num(summary.get("loot_share_of_cost_spent"), float("nan"))
    print(
        f"    in the active plan, raid income pays for {100.0 * loot_share:.0f}% of what it spends; "
        f"furnaces smelt {_num(summary.get('smelting_hours_total')):,.0f} of {_num(summary.get('horizon_hours')):,.0f} h"
    )
    targets = state.get("raid_targets") or []
    spacia_stock = {rk: 0.0 for rk in RES_KEYS}
    spacia_n = 0
    for tgt in targets:
        res = tgt.get("resources") if isinstance(tgt.get("resources"), dict) else {}
        if tgt.get("is_spacia") and sum(_num(res.get(rk)) for rk in RES_KEYS) > 0:
            spacia_n += 1
            for rk in RES_KEYS:
                spacia_stock[rk] += _num(res.get(rk))
    print(
        f"    spy reports on {spacia_n} Spacia targets hold: "
        + "  ".join(f"{rk} {val:,.0f}" for rk, val in spacia_stock.items())
    )
    budget = fleet_view.get("spare_budget") or {}

    def _res(key: str) -> str:
        vals = budget.get(key) or {}
        return "  ".join(f"{rk} {_num(vals.get(rk)):,.0f}" for rk in ("metal", "kryptonite") if rk in vals) or "n/a"

    print(f"    ship budgets, income basis {budget.get('income_basis') or 'modelled'}:")
    print(f"      income per hour:             {_res('hourly_income')}")
    print(f"      spare % rate per hour:       {_res('hourly_spare_allowance')}")
    print(
        f"      spare % available now:       {_res('available_budget')}"
        f"   (spent in the last {budget.get('spare_window_hours') or '?'} h: {_res('spare_spend_in_window')})"
    )
    print(f"      25% transport allowance now: {_res('infra_transport_budget')}")

    # ------------------------------------------------------------------ 10. plan v4 at work
    print("\n[10] Plan v4 in the live plan right now")
    fills = [a for a in plan if str(a.get("reason") or "").startswith(FILL_PREFIX)]
    loans = [a for a in plan if a.get("kind") == "TRANSPORT" and LOAN_TAG in str(a.get("reason"))]
    smelt = [a for a in plan if a.get("kind") == "THROTTLE_BUILDING" and EMPIRE_SMELT_TAG in str(a.get("reason"))]
    print(f"    fill steps ready to start: {len(fills)}   plan-order loans: {len(loans)}   furnaces being turned on for the empire: {len(smelt)}")
    for a in fills + loans:
        print(f"      {a.get('planet_coords')} {str(a.get('reason'))[:150]}")
    next_steps = [
        a for a in plan
        if a.get("priority") == "P1" and a.get("kind") in ("BUILD", "WAIT_RESOURCES", "RESEARCH")
        and str(a.get("reason") or "").startswith("AI-Planner")
    ]
    stock = {rk: sum(_num((p.get("resources") or {}).get(rk)) for p in planets) for rk in RES_KEYS}
    flying_cargo = {rk: 0.0 for rk in RES_KEYS}
    for q in queues:
        cargo = q.get("cargo") if isinstance(q.get("cargo"), dict) else None
        name = str(q.get("name") or "").lower()
        if q.get("category") == "fleet" and cargo and name.startswith(("transport", "station")):
            for rk in RES_KEYS:
                flying_cargo[rk] += _num(cargo.get(rk))
    need = {rk: sum(_num((a.get("cost") or {}).get(rk)) for a in next_steps) for rk in RES_KEYS}
    prod = {
        "pig_iron": _num(now_prod.get("pig_iron_per_hour")),
        "metal": _num(now_prod.get("metal_per_hour")),
        "kryptonite": _num(now_prod.get("kryptonite_per_hour")),
        "spice": _num(now_prod.get("spice_per_hour")),
    }
    print(f"    empire stock against the cost of every colony's next step ({len(next_steps)} steps, fills included):")
    for rk in RES_KEYS:
        pooled = stock[rk] + flying_cargo[rk]
        hours = stock[rk] / prod[rk] if prod[rk] > 0 else float("nan")
        print(
            f"      {rk:10s} stock {stock[rk]:>12,.0f} + in flight {flying_cargo[rk]:>10,.0f}   next steps {need[rk]:>12,.0f}   "
            f"spare {pooled - need[rk]:>13,.0f}   stock = {hours:5.1f} h of production"
        )

    # ------------------------------------------------------------------ summary
    print("\nSummary (plan v4, period E)")
    hours_v4 = (now - T_V4_LANDED) / 3600.0
    ready = hours_v4 >= 24.0 and on_cov >= MIN_RUNNING_HOURS_TO_JUDGE
    if not ready:
        print(
            f"  {hours_v4:.1f} h since the landing, {on_cov:.1f} running hours: too little to judge "
            f"(needs 24 h and {MIN_RUNNING_HOURS_TO_JUDGE:.0f} running hours). The readings below are provisional."
        )

    def _verdict(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    if share_est == share_est and capacity:
        if share_low >= V4_METAL_SHARE_OF_CAPACITY:
            verdict_1 = "PASS"
        elif share_high < V4_METAL_SHARE_OF_CAPACITY:
            verdict_1 = "FAIL"
        else:
            leaning = "pass" if share_est >= V4_METAL_SHARE_OF_CAPACITY else "fail"
            verdict_1 = f"NOT SETTLED (the estimate would {leaning}; the range crosses the mark)"
        print(
            f"  1. furnaces used:  {metal_v4:,.0f} Metal per hour = {100.0 * share_est:.0f}% of what the furnaces could make at the time, "
            f"between {100.0 * share_low:.0f}% and {100.0 * share_high:.0f}% "
            f"(mark {100.0 * V4_METAL_SHARE_OF_CAPACITY:.0f}%; plan v3 made {PLAN_V3_METAL_PER_HOUR:,})  {verdict_1}"
        )
        print(
            f"     against the capacity now alone: {100.0 * metal_v4 / capacity:.0f}% "
            f"(capacity went from {readings[0][1]:,.0f} to {capacity:,.0f} since the landing; {len(readings)} readings)"
        )
    else:
        why = "no recorded points in period E" if capacity else "capacity unknown"
        print(f"  1. furnaces used:  {metal_v4:,.0f} Metal per hour ({why}; plan v3 made {PLAN_V3_METAL_PER_HOUR:,})")
    if on_mean != on_mean:
        print("  2. builders busy:  no recorded points yet")
    elif on_mean >= V4_BUILDERS_WORKED:
        print(f"  2. builders busy:  {on_mean:.2f} over {on_cov:.1f} running hours (mark {V4_BUILDERS_WORKED:.1f}; plan v3 {PLAN_V3_BUILDERS_BUSY})  PASS")
    elif on_mean < V4_BUILDERS_NO_EFFECT:
        print(f"  2. builders busy:  {on_mean:.2f} over {on_cov:.1f} running hours (below {V4_BUILDERS_NO_EFFECT}: no measurable effect)  FAIL")
    else:
        print(f"  2. builders busy:  {on_mean:.2f} over {on_cov:.1f} running hours (better than before, short of {V4_BUILDERS_WORKED:.1f})  PARTIAL")
    print(
        f"  3. building levels: {levels_per_hour_v4:.2f} per hour since the landing "
        f"(mark {V4_LEVELS_PER_HOUR:.0f}; plan v3 {PLAN_V3_LEVELS_PER_HOUR})  {_verdict(levels_per_hour_v4 >= V4_LEVELS_PER_HOUR)}"
    )
    print(
        f"  4. idle with an affordable step: {idle_aff}% of colony time, last 24 h "
        f"(mark {V4_IDLE_AFFORDABLE_MAX_PCT:.0f}% or less; plan v3 {PLAN_V3_IDLE_AFFORDABLE_PCT}%)  {_verdict(idle_aff <= V4_IDLE_AFFORDABLE_MAX_PCT)}"
    )
    print(f"  raid income basis: live {live_basis}, active plan {plan_basis}; raid income pays for {100.0 * loot_share:.0f}% of the plan's spending")
    print(f"  cargo delivered between colonies: {fleet.get('delivered_cargo_per_hour_24h')}/h = {delivered_pct}% of the plan (plan v3: {PLAN_V3_DELIVERED_CARGO_PER_HOUR:,}/h)")
    print(f"  pre-queue gate (>{GATE_PREQUEUE_PCT:.0f}%): {ready_gap}%   cargo gate (<{GATE_CARGO_DELIVERED_PCT:.0f}%): {delivered_pct}%")
    print(
        f"  Large transports: {parked['s2'] + flying['large transport']}   furnaces on: {furnaces_on}/{len(planets)}   "
        f"gate alerts since plan v4: {len(after_v4)}   stops since plan v4: {sum(1 for a, _b in stops if a >= T_V4_LANDED)}"
    )
    print(f"  plan adherence: {adherence_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
