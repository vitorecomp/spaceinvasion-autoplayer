#!/usr/bin/env python3
"""Read-only check of adaptive AI-Planner execution (Plan v2 and v3, `ai_adaptive_execution`).

Prints the measurements that `docs/notes/adaptive-execution-plan-v2.md` uses to decide whether the
plan worked. It talks only to the local HTTP bridge: the cached `GET /dashboard?refresh=false` and
the `ai_planner_get` / `fleet_planner_get` / `notifications_list` reads, which work on the state the
bridge already holds. It makes no browser page views, sends no game actions, and writes nothing.

Usage (stack running):
    python3 scripts/validate_adaptive_execution.py [--api-url http://127.0.0.1:8787]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import time
import urllib.request
from typing import Any, Optional

UTC = dt.timezone.utc

# Fixed reference times (UTC). The operator's local time is UTC-03:00.
T_CODE_LANDED = dt.datetime(2026, 11, 19, 12, 56, 58, tzinfo=UTC).timestamp()  # first Plan v2 merge into main
T_SWITCH_ON = dt.datetime(2026, 11, 19, 14, 36, 33, tzinfo=UTC).timestamp()  # ai_adaptive_execution -> true
# Plan v3 follow-up live: realized raid income, hourly ship budgets, batched transport orders.
T_FOLLOWUP_LANDED = dt.datetime(2026, 11, 19, 18, 26, 36, tzinfo=UTC).timestamp()  # commit 6ea7736 live

# A gap longer than this between two recorded points means the bot was not running
# (in practice: stopped at an anti-bot gate until the operator cleared it).
STOPPED_GAP_SECONDS = 300.0

# Building levels completed across all colonies (`fields_used` equals the sum of building levels).
FIELDS_REFERENCE: tuple[tuple[str, float, int], ...] = (
    ("before, switch off", dt.datetime(2026, 11, 19, 6, 44, 0, tzinfo=UTC).timestamp(), 1057),
    ("hand-off, switch on 1.9 h", dt.datetime(2026, 11, 19, 16, 30, 0, tzinfo=UTC).timestamp(), 1098),
)

# Baseline measured before any Plan v2 code landed (period A below, 6.24 running hours).
BASELINE_BUILDERS_BUSY = 2.10  # of 11, time-weighted over running time
BASELINE_COLONIES = 11
# Metal the furnaces produced per hour while the bot ran, before the switch (405 points; median 0).
BASELINE_METAL_PER_HOUR = 50_556
# Sum of Blast furnace levels over all colonies when the Plan v3 follow-up landed.
FURNACE_LEVELS_AT_FOLLOWUP = 150

# Decision thresholds. The first two are the assistant's proposal; the last three come from plan v2.
WORKED_AT_LEAST = 2.0 * BASELINE_BUILDERS_BUSY
NO_EFFECT_BELOW = 1.25 * BASELINE_BUILDERS_BUSY
GATE_FILL_RULE_PCT = 15.0  # idle with an affordable target step, share of colony time
GATE_PREQUEUE_PCT = 5.0  # ready but not started, share of colony time
GATE_CARGO_DELIVERED_PCT = 80.0  # delivered vs planned cargo per hour

_HULL_RE = re.compile(r"(\d[\d.,]*)\s*x?\s*(Small transport|Large transport|Transmitter)", re.IGNORECASE)


def _get(base: str, path: str, payload: Optional[dict] = None) -> dict:
    url = f"{base}{path}"
    req = (
        urllib.request.Request(url)
        if payload is None
        else urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", default="http://127.0.0.1:8787")
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

    print(
        f"now {_fmt(now)} | {(now - T_SWITCH_ON) / 3600.0:.1f} h since the switch was turned on, "
        f"{(now - T_FOLLOWUP_LANDED) / 3600.0:.1f} h since the plan v3 follow-up landed"
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
    if rows:
        span_h = (rows[-1][0] - rows[0][0]) / 3600.0
        stops = [(a, b) for (a, _), (b, _) in zip(rows, rows[1:]) if b - a > STOPPED_GAP_SECONDS]
        stopped_h = sum(b - a for a, b in stops) / 3600.0
        print(f"    series {_fmt(rows[0][0])} .. {_fmt(rows[-1][0])}: {len(rows)} points over {span_h:.1f} h")
        print(f"    stopped for {stopped_h:.1f} h of that ({100.0 * stopped_h / span_h if span_h else 0:.0f}%), in {len(stops)} stops:")
        for a, b in stops:
            print(f"      {_fmt(a)} -> {_fmt(b)}  ({(b - a) / 60.0:.0f} min){'  [after switch-on]' if a >= T_SWITCH_ON else ''}")
    gates = sorted(
        _num(it.get("created_ts"))
        for it in (notes.get("items") or [])
        if str(it.get("category") or "") == "captcha_gate" and _num(it.get("created_ts")) > 0
    )
    since = rows[0][0] if rows else now - 86400.0
    before = [g for g in gates if since <= g < T_SWITCH_ON]
    after = [g for g in gates if g >= T_SWITCH_ON]
    print(f"    anti-bot gate alerts since {_fmt(since)}: {len(before)} before the switch, {len(after)} after")
    print(f"      after: {[_fmt(g) for g in after]}")

    # ------------------------------------------------------------------ 2. builders busy
    print("\n[2] Builders busy while running (time-weighted, of 11 colonies)")
    periods = (
        ("A before the code landed", 0.0, T_CODE_LANDED),
        ("B code landed, switch off", T_CODE_LANDED, T_SWITCH_ON),
        ("C switch on, plan v2 only", T_SWITCH_ON, T_FOLLOWUP_LANDED),
        ("D switch on, plan v3", T_FOLLOWUP_LANDED, float("inf")),
    )
    on_mean = float("nan")
    on_cov = 0.0
    for label, lo, hi in periods:
        mean, cov, cnt = _weighted_builders(rows, lo, hi, now)
        pts = [b for ts, b in rows if lo <= ts < hi]
        extra = f"  median {statistics.median(pts):.0f}  max {max(pts)}" if pts else ""
        print(f"    {label:26s}: {mean:5.2f}  over {cov:5.2f} running hours, {cnt} points{extra}")
        if label.startswith("D"):
            on_mean, on_cov = mean, cov
    for win_h in (6, 12, 24, 48):
        lo = max(T_FOLLOWUP_LANDED, now - win_h * 3600.0)
        if now - T_FOLLOWUP_LANDED >= win_h * 3600.0:
            mean, cov, cnt = _weighted_builders(rows, lo, float("inf"), now)
            print(f"    plan v3, last {win_h:2d} h        : {mean:5.2f}  over {cov:5.2f} running hours, {cnt} points")
    print(f"    baseline {BASELINE_BUILDERS_BUSY:.2f} | worked at {WORKED_AT_LEAST:.1f} or more | no effect below {NO_EFFECT_BELOW:.1f}")

    # ------------------------------------------------------------------ 3. colony states
    print("\n[3] Why colonies are idle")
    print(f"    now: {adaptive.get('active_builders')}/{adaptive.get('total_colonies')} building  {adaptive.get('counts')}")
    window = adaptive.get("window_24h") or {}
    idle_aff = _num(window.get("idle_with_affordable_share_pct"), float("nan"))
    ready_gap = _num(window.get("ready_not_started_share_pct"), float("nan"))
    print(f"    recorded window: {window.get('bucket_count')} ten-minute buckets, {window.get('colony_samples')} colony samples")
    print(f"    shares %: {window.get('state_shares_pct')}")
    print(f"    idle with an affordable target step: {idle_aff}%   ready but not started: {ready_gap}%")

    # ------------------------------------------------------------------ 4. furnaces
    print("\n[4] Blast furnaces")
    diag = {c.get("coords"): c for c in (adaptive.get("colonies") or [])}
    furnaces_on = 0
    pig_at_cap = 0
    for p in planets:
        pct = int(_num((p.get("economy_rates") or {}).get("resource2")))
        furnaces_on += 1 if pct > 0 else 0
        res, sto = p.get("resources") or {}, p.get("storage") or {}
        pig, pig_cap = int(_num(res.get("pig_iron"))), int(_num(sto.get("pig_iron")))
        pig_at_cap += 1 if pig_cap and pig >= pig_cap else 0
        c = diag.get(p.get("coords")) or {}
        print(
            f"    {p.get('coords')} furnace {pct:3d}%  metal {int(_num(res.get('metal'))):>9,}  pig {pig:>9,}/{pig_cap:<9,} "
            f"energy margin {int(_num(c.get('b2_energy_margin_at_100'))):>5}  {str(c.get('state')):18s} {str(c.get('reason'))[:60]}"
        )
    print(f"    furnaces on: {furnaces_on}/{len(planets)}   colonies with Pig iron at the warehouse cap: {pig_at_cap}")

    # ------------------------------------------------------------------ 5. transports
    print("\n[5] Transport fleet against the plan")
    parked = {"s1": 0, "s2": 0, "s3": 0}
    for p in planets:
        for key in parked:
            parked[key] += int(_num((p.get("ships") or {}).get(key)))
    flying = {"small transport": 0, "large transport": 0, "transmitter": 0}
    for q in queues:
        if q.get("category") == "fleet":
            for cnt_txt, kind in _HULL_RE.findall(str(q.get("detail") or "")):
                flying[kind.lower()] += int(cnt_txt.replace(".", "").replace(",", ""))
    fleet = adaptive.get("transport_fleet") or {}
    demand = ai.get("transport_demand") or {}
    delivered_pct = _num(fleet.get("delivered_vs_planned_pct"), float("nan"))
    print(f"    Small transports {parked['s1']} parked + {flying['small transport']} flying   Large transports {parked['s2']} parked + {flying['large transport']} flying")
    print(f"    cargo capacity {fleet.get('total_fleet_cargo')} of target {fleet.get('target_fleet_cargo')}")
    print(
        f"    planned {demand.get('cargo_per_hour_24h')}/h   delivered {fleet.get('delivered_cargo_per_hour_24h')}/h "
        f"({delivered_pct}% of plan, {fleet.get('delivered_flights_24h')} flights)   utilization {fleet.get('realized_utilization')}"
    )
    print(
        f"    target sized at utilization {fleet.get('sizing_utilization')}   "
        f"cargo ordered in the last hour {fleet.get('ordered_cargo_last_hour')}"
    )
    plan = dash.get("plan") or []
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
        sched_done = sched_started = built = 0
        page = 1
        while page <= 10:
            resp = _get(base, "/command", {"command": "ai_planner_get", "page": page, "page_size": 200})
            items = (resp.get("steps_page") or {}).get("items") or []
            if not items:
                break
            for step in items:
                kind = str(step.get("kind") or "").upper()
                if kind not in ("BUILD", "RESEARCH") or _num(step.get("start_sec")) > elapsed:
                    continue
                sched_started += 1
                sched_done += 1 if _num(step.get("finish_sec")) <= elapsed else 0
                key = str(step.get("entity_key") or "")
                live = (
                    research.get(key, 0)
                    if kind == "RESEARCH"
                    else levels.get((int(_num(step.get("planet_id"))), key), 0)
                )
                built += 1 if live >= int(_num(step.get("target_level"))) else 0
            if _num(items[-1].get("start_sec")) > elapsed:
                break
            page += 1
        adherence_txt = f"{built} built of {sched_done} the plan scheduled to be finished ({sched_started} scheduled to have started)"
        print(f"    plan adherence after {elapsed / 3600.0:.1f} h: {adherence_txt}")

    # ------------------------------------------------------------------ 7. progress
    print("\n[7] Building levels completed")
    fields = sum(int(_num(p.get("fields_used"))) for p in planets)
    print(f"    now: {fields}")
    for label, ref_ts, ref_fields in FIELDS_REFERENCE:
        hours = (now - ref_ts) / 3600.0
        if hours > 0:
            print(f"    since {label} ({_fmt(ref_ts)}, {ref_fields}): +{fields - ref_fields} in {hours:.1f} h = {(fields - ref_fields) / hours:.2f} per hour")
    print(f"    research levels: {state.get('researches')}")
    furnace_levels = sum(
        int(_num((p.get("buildings") or {}).get("b2", (p.get("buildings") or {}).get("2")))) for p in planets
    )
    print(f"    Blast furnace levels, all colonies: {furnace_levels} ({FURNACE_LEVELS_AT_FOLLOWUP} when plan v3 landed)")
    print("    Metal produced per hour while running (mean of the recorded points):")
    for label, lo, hi in periods:
        vals = [metal for ts, _busy, metal in series if lo <= ts < hi]
        if vals:
            print(f"      {label:26s}: {statistics.mean(vals):>9,.0f}  median {statistics.median(vals):>9,.0f}  ({len(vals)} points)")
    now_prod = dash.get("current_hourly_production") or {}
    print(
        f"      now: Metal {_num(now_prod.get('metal_per_hour')):,.0f}  Pig iron {_num(now_prod.get('pig_iron_per_hour')):,.0f}  "
        f"Kryptonite {_num(now_prod.get('kryptonite_per_hour')):,.0f}  Spice {_num(now_prod.get('spice_per_hour')):,.0f}"
        f"   (before the switch: Metal {BASELINE_METAL_PER_HOUR:,})"
    )

    # ------------------------------------------------------------------ 8. request pressure
    print("\n[8] Page-view limit")
    logs = dash.get("logs") or {}
    api_lines = str(logs.get("game_api") or "").splitlines()
    cmd_lines = [ln for ln in str(logs.get("commander") or "").splitlines() if ln.strip().startswith("[20")]
    hits = [ln for ln in api_lines if " 429 " in ln]
    print(f"    API log tail: {len(api_lines)} lines, {len(hits)} rejected by the page-view limit")
    for ln in hits[-4:]:
        print(f"      {ln.strip()[:150]}")
    print(f"    last commander log entry: {cmd_lines[-1].strip()[:34] if cmd_lines else 'none'}")
    print(f"    page views in the last 60 s: {health.get('page_views_last_60s')} of {health.get('page_view_limit_per_minute')}")
    ctrl = health.get("bot_control") or {}
    print(f"    health ok={health.get('ok')} in_game={health.get('in_game')} paused={ctrl.get('paused')} gate_active={ctrl.get('captcha_blocked')}")

    # ------------------------------------------------------------------ 9. raid income and ship budgets
    print("\n[9] Raid income the planner and the ship budgets assume")
    plan_tel = ai.get("attack_telemetry") or {}
    live_tel = ai.get("live_attack_telemetry") or {}
    plan_basis = str(plan_tel.get("income_basis") or "modelled")
    live_basis = str(live_tel.get("income_basis") or "modelled")
    print(f"    basis: live telemetry {live_basis}   active plan {plan_basis} ({plan_tel.get('pace_set_by')})")

    def _loot(tel: dict, key: str) -> str:
        vals = tel.get(key) or {}
        return "  ".join(f"{rk} {_num(vals.get(rk)):,.0f}" for rk in ("pig_iron", "metal", "kryptonite", "spice"))

    loot_24h = dash.get("loot_recycle_summary") or {}
    win_h = max(1.0, _num(loot_24h.get("window_hours"), 24.0))
    received = {
        rk: (_num(loot_24h.get(f"stolen_{rk}")) + _num(loot_24h.get(f"recycled_{rk}"))) / win_h
        for rk in ("pig_iron", "metal", "kryptonite", "spice")
    }
    print("    received per hour, last 24 h: " + "  ".join(f"{rk} {val:,.0f}" for rk, val in received.items()))
    print(f"      from {loot_24h.get('attack_count')} raids and {loot_24h.get('recycle_count')} recycling runs")
    print(f"    the active plan assumes per hour: {_loot(plan_tel, 'hourly_loot')}")
    if live_tel.get("modelled_hourly_loot"):
        print(f"    every-slot-raiding model would say: {_loot(live_tel, 'modelled_hourly_loot')}")
    loot_share = _num(summary.get("loot_share_of_cost_spent"), float("nan"))
    print(
        f"    in the active plan, raid income pays for {100.0 * loot_share:.0f}% of what it spends; "
        f"furnaces smelt {_num(summary.get('smelting_hours_total')):,.0f} of {_num(summary.get('horizon_hours')):,.0f} h"
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

    # ------------------------------------------------------------------ summary
    print("\nSummary")
    if on_cov < 12.0:
        print(f"  builders busy under plan v3: {on_mean:.2f} over only {on_cov:.1f} running hours. Too little to judge (need 12 or more).")
    elif on_mean >= WORKED_AT_LEAST:
        print(f"  builders busy under plan v3: {on_mean:.2f} over {on_cov:.1f} running hours. At least twice the baseline.")
    elif on_mean < NO_EFFECT_BELOW:
        print(f"  builders busy under plan v3: {on_mean:.2f} over {on_cov:.1f} running hours. No measurable effect.")
    else:
        print(f"  builders busy under plan v3: {on_mean:.2f} over {on_cov:.1f} running hours. Better than baseline, short of double.")
    print(f"  raid income basis: live {live_basis}, active plan {plan_basis}; raid income pays for {100.0 * loot_share:.0f}% of the plan's spending")
    print(f"  Blast furnace levels: {furnace_levels} ({FURNACE_LEVELS_AT_FOLLOWUP} when plan v3 landed)")
    print(f"  fill rule gate (>{GATE_FILL_RULE_PCT:.0f}%): {idle_aff}%   pre-queue gate (>{GATE_PREQUEUE_PCT:.0f}%): {ready_gap}%   cargo gate (<{GATE_CARGO_DELIVERED_PCT:.0f}%): {delivered_pct}%")
    print(f"  Large transports: {parked['s2'] + flying['large transport']}   furnaces on: {furnaces_on}/{len(planets)}   gate alerts after switch-on: {len(after)}")
    print(f"  plan adherence: {adherence_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
