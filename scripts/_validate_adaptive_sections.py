"""Report-section helpers (sections [1]–[6]) for `scripts/validate_adaptive_execution.py`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import datetime as dt
import re
import statistics
import sys
from typing import Any

UTC = dt.timezone.utc
RES_KEYS = ("pig_iron", "metal", "kryptonite", "spice")
STOPPED_GAP_SECONDS = 300.0
BASELINE_COLONIES = 11
BASELINE_BUILDERS_BUSY = 2.10
BASELINE_METAL_PER_HOUR = 50_556
FURNACE_LEVELS_AT_FOLLOWUP = 150
FURNACE_LEVELS_AT_V4 = 174
PLAN_V3_BUILDERS_BUSY = 1.76
PLAN_V3_METAL_PER_HOUR = 245_390
PLAN_V3_LEVELS_PER_HOUR = 9.3
PLAN_V3_IDLE_AFFORDABLE_PCT = 78.6
PLAN_V3_DELIVERED_CARGO_PER_HOUR = 140_803
V4_METAL_SHARE_OF_CAPACITY = 0.80
V4_BUILDERS_WORKED = 3.0
V4_BUILDERS_NO_EFFECT = 2.3
V4_LEVELS_PER_HOUR = 12.0
V4_IDLE_AFFORDABLE_MAX_PCT = 50.0
MIN_RUNNING_HOURS_TO_JUDGE = 12.0
GATE_PREQUEUE_PCT = 5.0
GATE_CARGO_DELIVERED_PCT = 80.0
RAID_ALLOWANCE_CARGO = 900_000
FILL_PREFIX = "AI-Planner fill #"
LOAN_TAG = "[Plan order loan]"
EMPIRE_SMELT_TAG = "AI-Planner empire"

_HULL_RE = re.compile(r"(\d[\d.,]*)\s*x?\s*(Small transport|Large transport|Transmitter)", re.IGNORECASE)
_HULL_CARGO = {"small transport": 6_000, "large transport": 30_000, "transmitter": 250_000}


def emit(line: str) -> None:
    """Write a report line to stdout without using `print()`."""
    sys.stdout.write(f"{line}\n")


def fmt_ts(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


def coerce_num(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@dataclass
class ReportMetrics:
    """Aggregated measurements passed from individual report sections into the final summary."""

    stops: list[tuple[float, float]]
    after_v4_gates: list[float]
    on_mean: float = float("nan")
    on_cov: float = 0.0
    idle_aff: float = float("nan")
    ready_gap: float = float("nan")
    furnaces_on: int = 0
    capacity: float | None = None
    parked_large: int = 0
    flying_large: int = 0
    delivered_pct: float = float("nan")
    adherence_txt: str = "n/a"
    levels_per_hour_v4: float = float("nan")
    metal_v4: float = float("nan")
    share_low: float = float("nan")
    share_est: float = float("nan")
    share_high: float = float("nan")
    readings: list[tuple[float, float]] | None = None
    live_basis: str = "modelled"
    plan_basis: str = "modelled"
    loot_share: float = float("nan")


def report_running_time(
    series: list[tuple[float, int, float]],
    notes: dict[str, Any],
    now: float,
    t_switch_on: float,
    t_v4_landed: float,
) -> tuple[list[tuple[float, int]], list[tuple[float, float]], list[float]]:
    rows = [(ts, busy) for ts, busy, _metal in series]
    emit("\n[1] Running time (the series is only recorded while the bot runs)")
    stops: list[tuple[float, float]] = []
    if rows:
        span_h = (rows[-1][0] - rows[0][0]) / 3600.0
        stops = [(a, b) for (a, _), (b, _) in zip(rows, rows[1:], strict=False) if b - a > STOPPED_GAP_SECONDS]
        stopped_h = sum(b - a for a, b in stops) / 3600.0
        emit(f"    series {fmt_ts(rows[0][0])} .. {fmt_ts(rows[-1][0])}: {len(rows)} points over {span_h:.1f} h")
        pct = 100.0 * stopped_h / span_h if span_h else 0.0
        emit(f"    stopped for {stopped_h:.1f} h of that ({pct:.0f}%), in {len(stops)} stops:")
        for a, b in stops:
            tag = "  [plan v4]" if a >= t_v4_landed else ("  [after switch-on]" if a >= t_switch_on else "")
            emit(f"      {fmt_ts(a)} -> {fmt_ts(b)}  ({(b - a) / 60.0:.0f} min){tag}")
    gates = sorted(
        coerce_num(it.get("created_ts"))
        for it in (notes.get("items") or [])
        if str(it.get("category") or "") == "captcha_gate" and coerce_num(it.get("created_ts")) > 0
    )
    since = rows[0][0] if rows else now - 86400.0
    before = [g for g in gates if since <= g < t_switch_on]
    after = [g for g in gates if g >= t_switch_on]
    after_v4 = [g for g in gates if g >= t_v4_landed]
    emit(
        f"    anti-bot gate alerts since {fmt_ts(since)}: {len(before)} before the switch, "
        f"{len(after)} after, {len(after_v4)} since plan v4"
    )
    emit(f"      after: {[fmt_ts(g) for g in after]}")
    return rows, stops, after_v4


def report_builders_and_idle(
    rows: list[tuple[float, int]],
    periods: tuple[tuple[str, float, float], ...],
    adaptive: dict[str, Any],
    now: float,
    t_v4_landed: float,
    weighted_fn: Callable[[list[tuple[float, int]], float, float, float], tuple[float, float, int]],
) -> tuple[float, float, float, float]:
    emit("\n[2] Builders busy while running (time-weighted, of 11 colonies)")
    on_mean = float("nan")
    on_cov = 0.0
    for label, lo, hi in periods:
        mean, cov, cnt = weighted_fn(rows, lo, hi, now)
        pts = [b for ts, b in rows if lo <= ts < hi]
        extra = f"  median {statistics.median(pts):.0f}  max {max(pts)}" if pts else ""
        emit(f"    {label:26s}: {mean:5.2f}  over {cov:5.2f} running hours, {cnt} points{extra}")
        if label.startswith("E"):
            on_mean, on_cov = mean, cov
    for win_h in (6, 12, 24, 48):
        lo = max(t_v4_landed, now - win_h * 3600.0)
        if now - t_v4_landed >= win_h * 3600.0:
            mean, cov, cnt = weighted_fn(rows, lo, float("inf"), now)
            emit(f"    plan v4, last {win_h:2d} h        : {mean:5.2f}  over {cov:5.2f} running hours, {cnt} points")
    emit(
        f"    before any change {BASELINE_BUILDERS_BUSY:.2f} | plan v3 {PLAN_V3_BUILDERS_BUSY:.2f} | "
        f"plan v4 worked at {V4_BUILDERS_WORKED:.1f} or more | no effect below {V4_BUILDERS_NO_EFFECT:.1f}"
    )

    emit("\n[3] Why colonies are idle")
    emit(
        f"    now: {adaptive.get('active_builders')}/{adaptive.get('total_colonies')} building  {adaptive.get('counts')}"
    )
    window = adaptive.get("window_24h") or {}
    idle_aff = coerce_num(window.get("idle_with_affordable_share_pct"), float("nan"))
    ready_gap = coerce_num(window.get("ready_not_started_share_pct"), float("nan"))
    emit(
        f"    recorded window: {window.get('bucket_count')} ten-minute buckets, "
        f"{window.get('colony_samples')} colony samples"
    )
    emit(f"    shares %: {window.get('state_shares_pct')}")
    emit(
        f"    idle with an affordable target step: {idle_aff}% (plan v3: {PLAN_V3_IDLE_AFFORDABLE_PCT}%)   "
        f"ready but not started: {ready_gap}%"
    )
    emit(
        "    (the window is the last 24 h of recorded buckets: it mixes plan v3 and plan v4 until 24 h after the landing)"
    )
    return on_mean, on_cov, idle_aff, ready_gap


def report_furnaces(
    planets: list[dict[str, Any]],
    adaptive: dict[str, Any],
    economies: dict[str, Any],
    capacity: float | None,
) -> int:
    emit("\n[4] Blast furnaces")
    diag = {c.get("coords"): c for c in (adaptive.get("colonies") or [])}
    furnaces_on = 0
    pig_at_cap = 0
    metal_now = 0.0
    for p in planets:
        pct = int(coerce_num((p.get("economy_rates") or {}).get("resource2")))
        furnaces_on += 1 if pct > 0 else 0
        res, sto = p.get("resources") or {}, p.get("storage") or {}
        pig, pig_cap = int(coerce_num(res.get("pig_iron"))), int(coerce_num(sto.get("pig_iron")))
        pig_at_cap += 1 if pig_cap and pig >= pig_cap else 0
        c = diag.get(p.get("coords")) or {}
        econ = economies.get(str(p.get("planet_id"))) or {}
        metal_now += coerce_num(econ.get("metal_per_hr"))
        emit(
            f"    {p.get('coords')} furnace {pct:3d}%  metal {int(coerce_num(res.get('metal'))):>9,}  "
            f"pig {pig:>9,}/{pig_cap:<9,} energy margin {int(coerce_num(c.get('b2_energy_margin_at_100'))):>5}  "
            f"{str(c.get('state')):18s} {str(c.get('reason'))[:60]}"
        )
    emit(f"    furnaces on: {furnaces_on}/{len(planets)}   colonies with Pig iron at the warehouse cap: {pig_at_cap}")
    if capacity:
        emit(
            f"    Metal per hour now {metal_now:,.0f} of {capacity:,.0f} the furnaces can make "
            f"({100.0 * metal_now / capacity:.0f}%)"
        )
    else:
        emit(
            f"    Metal per hour now {metal_now:,.0f} "
            "(furnace capacity unknown: the repo's game data could not be loaded)"
        )
    return furnaces_on


def report_transports(
    planets: list[dict[str, Any]],
    queues: list[dict[str, Any]],
    adaptive: dict[str, Any],
    ai: dict[str, Any],
    plan: list[dict[str, Any]],
) -> tuple[int, int, float]:
    emit("\n[5] Transport fleet against the plan")
    parked = {"s1": 0, "s2": 0, "s3": 0}
    for p in planets:
        for key in parked:
            parked[key] += int(coerce_num((p.get("ships") or {}).get(key)))
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
    delivered_pct = coerce_num(fleet.get("delivered_vs_planned_pct"), float("nan"))
    emit(
        f"    Small transports {parked['s1']} parked + {flying['small transport']} flying   "
        f"Large transports {parked['s2']} parked + {flying['large transport']} flying   "
        f"Transmitters {parked['s3']} parked + {flying['transmitter']} flying"
    )
    for mission, hulls in sorted(by_mission.items()):
        cargo = sum(_HULL_CARGO[k] * v for k, v in hulls.items())
        emit(
            f"      flying as {mission:10s}: {hulls['small transport']:3d} Small  "
            f"{hulls['large transport']:3d} Large  {hulls['transmitter']:2d} Transmitter  = {cargo:>9,} cargo"
        )
    raid_cargo = sum(_HULL_CARGO[k] * v for k, v in (by_mission.get("attack") or {}).items())
    total_cargo, target_cargo = coerce_num(fleet.get("total_fleet_cargo")), coerce_num(fleet.get("target_fleet_cargo"))
    emit(f"    cargo capacity {fleet.get('total_fleet_cargo')} of target {fleet.get('target_fleet_cargo')}")
    emit(
        f"    plan v4 counts {total_cargo - raid_cargo:,.0f} toward the target ({raid_cargo:,.0f} is away on raids); "
        f"the whole fleet stops growing at {min(2.0 * target_cargo, target_cargo + RAID_ALLOWANCE_CARGO):,.0f}"
    )
    emit(
        f"    planned {demand.get('cargo_per_hour_24h')}/h   delivered {fleet.get('delivered_cargo_per_hour_24h')}/h "
        f"({delivered_pct}% of plan, {fleet.get('delivered_flights_24h')} flights; "
        f"plan v3: {PLAN_V3_DELIVERED_CARGO_PER_HOUR:,}/h)   utilization {fleet.get('realized_utilization')}"
    )
    emit(
        f"    target sized at utilization {fleet.get('sizing_utilization')}   "
        f"cargo ordered in the last hour {fleet.get('ordered_cargo_last_hour')}"
    )
    executable = [a for a in plan if a.get("executable_now")]
    first_ship = next((i for i, a in enumerate(executable) if a.get("kind") == "BUILD_SHIP"), None)
    ahead: dict[str, int] = {}
    for a in executable[: first_ship if first_ship is not None else len(executable)]:
        ahead[str(a.get("kind"))] = ahead.get(str(a.get("kind")), 0) + 1
    emit(f"    executable actions queued ahead of the first ship build: {ahead or 'none'}")
    for a in plan:
        if a.get("kind") == "BUILD_SHIP" and "[Plan infra cargo target]" in str(a.get("reason")):
            emit(
                f"    {a.get('planet_coords')} {a.get('target_name')} "
                f"executable={a.get('executable_now')}  {str(a.get('reason'))[:90]}"
            )
    return parked["s2"], flying["large transport"], delivered_pct


def report_plan_and_adherence(
    base: str,
    dash: dict[str, Any],
    ai: dict[str, Any],
    state: dict[str, Any],
    planets: list[dict[str, Any]],
    adaptive: dict[str, Any],
    now: float,
    get_fn: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> str:
    emit("\n[6] Plan and background re-tune")
    job = (dash.get("planner_status") or {}).get("ai_planner_job") or {}
    summary = ai.get("active_summary") or {}
    plan_ts = coerce_num(summary.get("live_snapshot_ts"))
    if plan_ts:
        emit(
            f"    active plan from {fmt_ts(plan_ts)} ({(now - plan_ts) / 3600.0:.1f} h old), "
            f"{summary.get('actions_count')} steps"
        )
    emit(f"    last planner job: {job.get('status')}, trigger {job.get('trigger_source')}, run {job.get('run_id')}")
    emit(f"    auto re-tune: {adaptive.get('auto_replan')}")

    if not (plan_ts and now > plan_ts):
        return "n/a"
    elapsed = now - plan_ts
    levels: dict[tuple[int, str], int] = {}
    for p in planets:
        for key, lvl in (p.get("buildings") or {}).items():
            norm = str(key) if str(key).startswith("b") else f"b{key}"
            levels[(int(coerce_num(p.get("planet_id"))), norm)] = int(coerce_num(lvl))
    research = {str(k): int(coerce_num(v)) for k, v in (state.get("researches") or {}).items()}
    sched_done = sched_started = built = built_ahead = 0
    for page in range(1, 11):
        resp = get_fn(base, "/command", {"command": "ai_planner_get", "page": page, "page_size": 200})
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
                else levels.get((int(coerce_num(step.get("planet_id"))), key), 0)
            )
            done = live >= int(coerce_num(step.get("target_level")))
            if coerce_num(step.get("start_sec")) > elapsed:
                built_ahead += 1 if done else 0
                continue
            sched_started += 1
            sched_done += 1 if coerce_num(step.get("finish_sec")) <= elapsed else 0
            built += 1 if done else 0
    adherence_txt = (
        f"{built} built of {sched_done} the plan scheduled to be finished ({sched_started} scheduled to have started); "
        f"{built_ahead} more built ahead of their scheduled start"
    )
    emit(f"    plan adherence after {elapsed / 3600.0:.1f} h: {adherence_txt}")
    return adherence_txt
