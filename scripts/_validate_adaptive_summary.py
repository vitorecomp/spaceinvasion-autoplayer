"""Report-section helpers (sections [8]–[10] and summary) for `scripts/validate_adaptive_execution.py`."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from _validate_adaptive_sections import (  # noqa: E402
    EMPIRE_SMELT_TAG,
    FILL_PREFIX,
    GATE_CARGO_DELIVERED_PCT,
    GATE_PREQUEUE_PCT,
    LOAN_TAG,
    MIN_RUNNING_HOURS_TO_JUDGE,
    PLAN_V3_BUILDERS_BUSY,
    PLAN_V3_DELIVERED_CARGO_PER_HOUR,
    PLAN_V3_IDLE_AFFORDABLE_PCT,
    PLAN_V3_LEVELS_PER_HOUR,
    PLAN_V3_METAL_PER_HOUR,
    RES_KEYS,
    V4_BUILDERS_NO_EFFECT,
    V4_BUILDERS_WORKED,
    V4_IDLE_AFFORDABLE_MAX_PCT,
    V4_LEVELS_PER_HOUR,
    V4_METAL_SHARE_OF_CAPACITY,
    ReportMetrics,
    coerce_num,
    emit,
)


def report_raid_and_plan_v4(
    dash: dict[str, Any],
    ai: dict[str, Any],
    fleet_view: dict[str, Any],
    state: dict[str, Any],
    planets: list[dict[str, Any]],
    queues: list[dict[str, Any]],
    plan: list[dict[str, Any]],
    health: dict[str, Any],
    now: float,
) -> tuple[str, str, float]:
    emit("\n[8] Page-view limit")
    emit(
        f"    page views in the last 60 s: {health.get('page_views_last_60s')} of "
        f"{health.get('page_view_limit_per_minute')}"
    )
    ctrl = health.get("bot_control") or {}
    emit(
        f"    health ok={health.get('ok')} in_game={health.get('in_game')} "
        f"paused={ctrl.get('paused')} gate_active={ctrl.get('captcha_blocked')}"
    )
    recent = (dash.get("db_summary") or {}).get("recent_actions") or []
    if recent:
        newest = max(coerce_num(r.get("ts")) for r in recent)
        failed = sum(1 for r in recent if not r.get("executed"))
        emit(
            f"    last {len(recent)} recorded actions: newest {(now - newest) / 60.0:.0f} min ago, "
            f"{failed} did not execute"
        )

    emit("\n[9] Raid income the planner and the ship budgets assume")
    summary = ai.get("active_summary") or {}
    plan_tel, live_tel = ai.get("attack_telemetry") or {}, ai.get("live_attack_telemetry") or {}
    plan_basis, live_basis = (
        str(plan_tel.get("income_basis") or "modelled"),
        str(live_tel.get("income_basis") or "modelled"),
    )
    emit(f"    basis: live telemetry {live_basis}   active plan {plan_basis} ({plan_tel.get('pace_set_by')})")

    def _loot(tel: dict[str, Any], key: str) -> str:
        vals = tel.get(key) or {}
        return "  ".join(f"{rk} {coerce_num(vals.get(rk)):,.0f}" for rk in RES_KEYS)

    loot_24h = dash.get("loot_recycle_summary") or {}
    win_h = max(1.0, coerce_num(loot_24h.get("window_hours"), 24.0))
    received = {
        rk: (coerce_num(loot_24h.get(f"stolen_{rk}")) + coerce_num(loot_24h.get(f"recycled_{rk}"))) / win_h
        for rk in RES_KEYS
    }
    emit("    received per hour, last 24 h: " + "  ".join(f"{rk} {val:,.0f}" for rk, val in received.items()))
    raids, stolen = int(coerce_num(loot_24h.get("attack_count"))), coerce_num(loot_24h.get("total_stolen"))
    emit(
        f"      from {raids} raids ({stolen:,.0f} stolen, {stolen / raids if raids else 0:,.0f} per raid, "
        f"{stolen / win_h:,.0f} per hour) and {loot_24h.get('recycle_count')} recycling runs "
        f"({coerce_num(loot_24h.get('total_recycled')):,.0f})"
    )
    emit(f"    the active plan assumes per hour: {_loot(plan_tel, 'hourly_loot')}")
    if live_tel.get("modelled_hourly_loot"):
        emit(f"    every-slot-raiding model would say: {_loot(live_tel, 'modelled_hourly_loot')}")
    loot_share = coerce_num(summary.get("loot_share_of_cost_spent"), float("nan"))
    emit(
        f"    in the active plan, raid income pays for {100.0 * loot_share:.0f}% of what it spends; "
        f"furnaces smelt {coerce_num(summary.get('smelting_hours_total')):,.0f} of "
        f"{coerce_num(summary.get('horizon_hours')):,.0f} h"
    )
    _report_spacia_and_budgets(state, fleet_view)
    _report_plan_v4_live(dash, planets, queues, plan)
    return live_basis, plan_basis, loot_share


def _report_spacia_and_budgets(state: dict[str, Any], fleet_view: dict[str, Any]) -> None:
    spacia_stock = {rk: 0.0 for rk in RES_KEYS}
    spacia_n = 0
    for tgt in state.get("raid_targets") or []:
        res = tgt.get("resources") if isinstance(tgt.get("resources"), dict) else {}
        if tgt.get("is_spacia") and sum(coerce_num(res.get(rk)) for rk in RES_KEYS) > 0:
            spacia_n += 1
            for rk in RES_KEYS:
                spacia_stock[rk] += coerce_num(res.get(rk))
    emit(
        f"    spy reports on {spacia_n} Spacia targets hold: "
        + "  ".join(f"{rk} {val:,.0f}" for rk, val in spacia_stock.items())
    )
    budget = fleet_view.get("spare_budget") or {}

    def _res(key: str) -> str:
        vals = budget.get(key) or {}
        return (
            "  ".join(f"{rk} {coerce_num(vals.get(rk)):,.0f}" for rk in ("metal", "kryptonite") if rk in vals) or "n/a"
        )

    emit(f"    ship budgets, income basis {budget.get('income_basis') or 'modelled'}:")
    emit(f"      income per hour:             {_res('hourly_income')}")
    emit(f"      spare % rate per hour:       {_res('hourly_spare_allowance')}")
    emit(
        f"      spare % available now:       {_res('available_budget')}"
        f"   (spent in the last {budget.get('spare_window_hours') or '?'} h: {_res('spare_spend_in_window')})"
    )
    emit(f"      25% transport allowance now: {_res('infra_transport_budget')}")


def _report_plan_v4_live(
    dash: dict[str, Any],
    planets: list[dict[str, Any]],
    queues: list[dict[str, Any]],
    plan: list[dict[str, Any]],
) -> None:
    emit("\n[10] Plan v4 in the live plan right now")
    fills = [a for a in plan if str(a.get("reason") or "").startswith(FILL_PREFIX)]
    loans = [a for a in plan if a.get("kind") == "TRANSPORT" and LOAN_TAG in str(a.get("reason"))]
    smelt = [a for a in plan if a.get("kind") == "THROTTLE_BUILDING" and EMPIRE_SMELT_TAG in str(a.get("reason"))]
    emit(
        f"    fill steps ready to start: {len(fills)}   plan-order loans: {len(loans)}   "
        f"furnaces being turned on for the empire: {len(smelt)}"
    )
    for a in fills + loans:
        emit(f"      {a.get('planet_coords')} {str(a.get('reason'))[:150]}")
    next_steps = [
        a
        for a in plan
        if a.get("priority") == "P1"
        and a.get("kind") in ("BUILD", "WAIT_RESOURCES", "RESEARCH")
        and str(a.get("reason") or "").startswith("AI-Planner")
    ]
    stock = {rk: sum(coerce_num((p.get("resources") or {}).get(rk)) for p in planets) for rk in RES_KEYS}
    flying_cargo = {rk: 0.0 for rk in RES_KEYS}
    for q in queues:
        cargo = q.get("cargo") if isinstance(q.get("cargo"), dict) else None
        name = str(q.get("name") or "").lower()
        if q.get("category") == "fleet" and cargo and name.startswith(("transport", "station")):
            for rk in RES_KEYS:
                flying_cargo[rk] += coerce_num(cargo.get(rk))
    need = {rk: sum(coerce_num((a.get("cost") or {}).get(rk)) for a in next_steps) for rk in RES_KEYS}
    now_prod = dash.get("current_hourly_production") or {}
    prod = {rk: coerce_num(now_prod.get(f"{rk}_per_hour")) for rk in RES_KEYS}
    emit(f"    empire stock against the cost of every colony's next step ({len(next_steps)} steps, fills included):")
    for rk in RES_KEYS:
        pooled = stock[rk] + flying_cargo[rk]
        hours = stock[rk] / prod[rk] if prod[rk] > 0 else float("nan")
        emit(
            f"      {rk:10s} stock {stock[rk]:>12,.0f} + in flight {flying_cargo[rk]:>10,.0f}   "
            f"next steps {need[rk]:>12,.0f}   spare {pooled - need[rk]:>13,.0f}   stock = {hours:5.1f} h of production"
        )


def report_summary(
    metrics: ReportMetrics,
    adaptive: dict[str, Any],
    planets: list[dict[str, Any]],
    now: float,
    t_v4_landed: float,
) -> None:
    emit("\nSummary (plan v4, period E)")
    hours_v4 = (now - t_v4_landed) / 3600.0
    if not (hours_v4 >= 24.0 and metrics.on_cov >= MIN_RUNNING_HOURS_TO_JUDGE):
        emit(
            f"  {hours_v4:.1f} h since the landing, {metrics.on_cov:.1f} running hours: too little to judge "
            f"(needs 24 h and {MIN_RUNNING_HOURS_TO_JUDGE:.0f} running hours). The readings below are provisional."
        )

    def _verdict(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    readings = metrics.readings or []
    if metrics.share_est == metrics.share_est and metrics.capacity:
        if metrics.share_low >= V4_METAL_SHARE_OF_CAPACITY:
            verdict_1 = "PASS"
        elif metrics.share_high < V4_METAL_SHARE_OF_CAPACITY:
            verdict_1 = "FAIL"
        else:
            leaning = "pass" if metrics.share_est >= V4_METAL_SHARE_OF_CAPACITY else "fail"
            verdict_1 = f"NOT SETTLED (the estimate would {leaning}; the range crosses the mark)"
        emit(
            f"  1. furnaces used:  {metrics.metal_v4:,.0f} Metal per hour = {100.0 * metrics.share_est:.0f}% "
            f"of what the furnaces could make at the time, between {100.0 * metrics.share_low:.0f}% and "
            f"{100.0 * metrics.share_high:.0f}% (mark {100.0 * V4_METAL_SHARE_OF_CAPACITY:.0f}%; "
            f"plan v3 made {PLAN_V3_METAL_PER_HOUR:,})  {verdict_1}"
        )
        emit(
            f"     against the capacity now alone: {100.0 * metrics.metal_v4 / metrics.capacity:.0f}% "
            f"(capacity went from {readings[0][1]:,.0f} to {metrics.capacity:,.0f} since the landing; "
            f"{len(readings)} readings)"
        )
    else:
        why = "no recorded points in period E" if metrics.capacity else "capacity unknown"
        emit(
            f"  1. furnaces used:  {metrics.metal_v4:,.0f} Metal per hour ({why}; plan v3 made {PLAN_V3_METAL_PER_HOUR:,})"
        )
    if metrics.on_mean != metrics.on_mean:
        emit("  2. builders busy:  no recorded points yet")
    elif metrics.on_mean >= V4_BUILDERS_WORKED:
        emit(
            f"  2. builders busy:  {metrics.on_mean:.2f} over {metrics.on_cov:.1f} running hours "
            f"(mark {V4_BUILDERS_WORKED:.1f}; plan v3 {PLAN_V3_BUILDERS_BUSY})  PASS"
        )
    elif metrics.on_mean < V4_BUILDERS_NO_EFFECT:
        emit(
            f"  2. builders busy:  {metrics.on_mean:.2f} over {metrics.on_cov:.1f} running hours "
            f"(below {V4_BUILDERS_NO_EFFECT}: no measurable effect)  FAIL"
        )
    else:
        emit(
            f"  2. builders busy:  {metrics.on_mean:.2f} over {metrics.on_cov:.1f} running hours "
            f"(better than before, short of {V4_BUILDERS_WORKED:.1f})  PARTIAL"
        )
    emit(
        f"  3. building levels: {metrics.levels_per_hour_v4:.2f} per hour since the landing "
        f"(mark {V4_LEVELS_PER_HOUR:.0f}; plan v3 {PLAN_V3_LEVELS_PER_HOUR})  "
        f"{_verdict(metrics.levels_per_hour_v4 >= V4_LEVELS_PER_HOUR)}"
    )
    emit(
        f"  4. idle with an affordable step: {metrics.idle_aff}% of colony time, last 24 h "
        f"(mark {V4_IDLE_AFFORDABLE_MAX_PCT:.0f}% or less; plan v3 {PLAN_V3_IDLE_AFFORDABLE_PCT}%)  "
        f"{_verdict(metrics.idle_aff <= V4_IDLE_AFFORDABLE_MAX_PCT)}"
    )
    emit(
        f"  raid income basis: live {metrics.live_basis}, active plan {metrics.plan_basis}; "
        f"raid income pays for {100.0 * metrics.loot_share:.0f}% of the plan's spending"
    )
    fleet = adaptive.get("transport_fleet") or {}
    emit(
        f"  cargo delivered between colonies: {fleet.get('delivered_cargo_per_hour_24h')}/h = "
        f"{metrics.delivered_pct}% of the plan (plan v3: {PLAN_V3_DELIVERED_CARGO_PER_HOUR:,}/h)"
    )
    emit(
        f"  pre-queue gate (>{GATE_PREQUEUE_PCT:.0f}%): {metrics.ready_gap}%   "
        f"cargo gate (<{GATE_CARGO_DELIVERED_PCT:.0f}%): {metrics.delivered_pct}%"
    )
    v4_stops = sum(1 for a, _b in metrics.stops if a >= t_v4_landed)
    emit(
        f"  Large transports: {metrics.parked_large + metrics.flying_large}   "
        f"furnaces on: {metrics.furnaces_on}/{len(planets)}   "
        f"gate alerts since plan v4: {len(metrics.after_v4_gates)}   stops since plan v4: {v4_stops}"
    )
    emit(f"  plan adherence: {metrics.adherence_txt}")
