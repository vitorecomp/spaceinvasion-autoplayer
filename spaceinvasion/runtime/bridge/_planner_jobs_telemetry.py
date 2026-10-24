"""Planner attack telemetry, unlock gate, jobs snapshot, and drift-replan evaluation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import time

from spaceinvasion.core.config import StrategyConfig, normalize_planner_mode
from spaceinvasion.core.model import GameState, count_allocated_normal_planets
from spaceinvasion.planner import ai as ai_mod
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._planner_jobs_state import (
    extract_plan_colony_count,
    extract_record_timestamp,
    extract_telemetry_median_spice_eq,
    get_active_ai_planner,
    get_active_config_optimizer,
    get_planner_state,
    list_field_conflicts,
)
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService

_LOGGER = logging.getLogger(__name__)


def snapshot_universe_research_max_map(state_cache: StateCacheService, state: GameState | None) -> dict[str, int]:
    """Snapshot `{research_key: universe_max_level}` for Config Optimizer inputs."""
    rows = state_cache.get_universe_research_max_locked(state)
    out: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        rkey = str(row.get("key") or "").strip()
        if rkey:
            out[rkey] = max(0, safe_api_int(row.get("universe_max_level", 0), 0))
    return out


def snapshot_attack_history(
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Snapshot raid history rows and battle-report messages from the database."""
    if ctx.db is None:
        return [], []
    raid_rows: list[dict[str, object]] = []
    try:
        rs = ledgers.get_raid_stats_cached(include_telemetry_summary=False)
        hist = rs.get("history")
        if isinstance(hist, Sequence) and not isinstance(hist, (str, bytes)):
            raid_rows = [dict(h) for h in hist if isinstance(h, Mapping)]
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to snapshot raid stats history", exc_info=True)
        raid_rows = []

    message_rows: list[dict[str, object]] = []
    try:
        for b in ctx.db.get_battle_reports(limit=500):
            if not isinstance(b, Mapping):
                continue
            flat_b: dict[str, object] = dict(b)
            loot_m = b.get("loot")
            if isinstance(loot_m, Mapping):
                for rk in ("pig_iron", "metal", "kryptonite", "spice"):
                    flat_b.setdefault(rk, loot_m.get(rk, 0))
            message_rows.append(flat_b)
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to snapshot battle reports", exc_info=True)
        message_rows = []

    if not message_rows:
        try:
            message_rows = [
                dict(m) for m in ctx.db.get_game_messages(category="battle_report", limit=500) if isinstance(m, Mapping)
            ]
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to snapshot battle report messages", exc_info=True)
            message_rows = []
    return raid_rows, message_rows


def compute_live_attack_telemetry(
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
    state: GameState | None,
) -> dict[str, object]:
    """Compute live raid telemetry (using realized 24h loot when `ai_adaptive_execution` is enabled)."""
    raid_rows, message_rows = snapshot_attack_history(ctx, ledgers)
    realized = ledgers.get_realized_loot_hourly_locked() if ctx.strategy_config.ai_adaptive_execution else None
    if realized is not None:
        raw_hl = realized.get("hourly_loot")
        hl_map = dict(raw_hl) if isinstance(raw_hl, Mapping) else None
        mph = safe_api_float(realized.get("missions_per_hour"), 0.0)
        return dict(
            ai_mod.compute_attack_telemetry(
                raid_rows,
                message_rows,
                state=state,
                realized_hourly_loot=hl_map,
                realized_missions_per_hour=mph,
            )
        )
    return dict(ai_mod.compute_attack_telemetry(raid_rows, message_rows, state=state))


def evaluate_ai_planner_gate(
    ctx: BridgeContext,
    state: GameState | None,
    *,
    cfg: StrategyConfig | None = None,
) -> dict[str, object]:
    """Evaluate whether live AI-Planner execution is unlocked and backed by an active plan."""
    eff_cfg = cfg or ctx.strategy_config
    colonies_owned = count_allocated_normal_planets(state) if state is not None else 0
    max_planets = max(1, safe_api_int(eff_cfg.max_planets, 11))
    live_ai_unlocked = colonies_owned >= max_planets
    active_rec = get_active_ai_planner(ctx)
    raw_res = active_rec.get("result") if isinstance(active_rec, Mapping) else None
    active_result: dict[str, object] | None = dict(raw_res) if isinstance(raw_res, Mapping) else None
    has_plan = active_result is not None
    active_mode = normalize_planner_mode(eff_cfg.planner_mode)
    effective_mode = active_mode
    fallback_reason = ""
    if active_mode == "ai_planner":
        if not live_ai_unlocked:
            effective_mode = "priority"
            fallback_reason = (
                f"Colonies {colonies_owned}/{max_planets} — live AI-Planner unlocks at {max_planets}/{max_planets}"
            )
        elif not has_plan:
            effective_mode = "priority"
            fallback_reason = "No active AI-Planner path generated yet — falling back to Heuristics"
    return {
        "colonies_owned": colonies_owned,
        "max_planets": max_planets,
        "live_ai_unlocked": live_ai_unlocked,
        "has_plan": has_plan,
        "active_planner_mode": active_mode,
        "effective_planner_mode": effective_mode,
        "fallback_reason": fallback_reason,
        "active_rec": active_rec,
        "active_result": active_result,
    }


def get_planner_jobs_snapshot(
    ctx: BridgeContext,
    state: GameState | None = None,
    *,
    cfg: StrategyConfig | None = None,
) -> dict[str, object]:
    """Snapshot background Config Optimizer and AI-Planner job progress, gate state, and conflicts."""
    eff_state = state if state is not None else get_planner_state(ctx)
    gate = evaluate_ai_planner_gate(ctx, eff_state, cfg=cfg)
    pending_conflicts = list_field_conflicts(ctx, status="pending")
    with ctx.planner_jobs_lock:
        co_job = dict(ctx.config_opt_job)
        ai_job = dict(ctx.ai_planner_job)
        now_t = time.time()
        for job in (co_job, ai_job):
            started = job.get("started_ts")
            if job.get("status") == "running" and isinstance(started, (int, float)) and not isinstance(started, bool):
                job["elapsed_ms"] = round(max(0.0, (now_t - float(started)) * 1000.0), 1)
    return {
        "config_optimizer_job": co_job,
        "ai_planner_job": ai_job,
        "colonies_owned": gate["colonies_owned"],
        "max_planets": gate["max_planets"],
        "live_ai_unlocked": gate["live_ai_unlocked"],
        "active_planner_mode": gate["active_planner_mode"],
        "effective_planner_mode": gate["effective_planner_mode"],
        "fallback_reason": gate["fallback_reason"],
        "pending_field_conflicts": pending_conflicts,
    }


def _detect_drift_reason(
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
    state: GameState,
    active_rec: Mapping[str, object],
    active_result: Mapping[str, object],
    active_plan_ts: float,
    eff_now: float,
) -> str | None:
    active_co = get_active_config_optimizer(ctx)
    if isinstance(active_co, Mapping):
        co_run_id = safe_api_int(active_co.get("run_id"), 0)
        ai_co_run_id = safe_api_int(active_rec.get("config_run_id"), 0)
        raw_co_res = active_co.get("result")
        co_res: Mapping[str, object] = raw_co_res if isinstance(raw_co_res, Mapping) else {}
        co_gen_at = str(co_res.get("generated_at") or "").strip()
        ai_co_gen_at = str(
            active_result.get("config_generated_at") or active_result.get("target_config_generated_at") or ""
        ).strip()
        if (co_run_id > 0 and ai_co_run_id > 0 and co_run_id != ai_co_run_id) or (
            co_gen_at and ai_co_gen_at and co_gen_at != ai_co_gen_at
        ):
            return "config_optimizer_changed"

    live_colonies = count_allocated_normal_planets(state)
    plan_colonies = extract_plan_colony_count(active_result)
    if plan_colonies > 0 and live_colonies != plan_colonies:
        return "colony_count_changed"

    plan_tel = active_result.get("attack_telemetry")
    plan_sp = extract_telemetry_median_spice_eq(plan_tel)
    live_tel = compute_live_attack_telemetry(ctx, ledgers, state)
    live_sp = extract_telemetry_median_spice_eq(live_tel)
    plan_on_realized = ledgers.telemetry_income_basis(plan_tel) == BridgeConstants.REALIZED_INCOME_BASIS
    if not plan_on_realized and (
        (plan_sp <= 0.0 and live_sp > 0.0)
        or (
            plan_sp > 0.0
            and live_sp > 0.0
            and (abs(live_sp - plan_sp) / plan_sp) >= BridgeConstants.AI_DRIFT_LOOT_SHIFT_RATIO
        )
    ):
        return "loot_shift_25pct"

    live_basis = ledgers.telemetry_income_basis(live_tel)
    plan_basis = ledgers.telemetry_income_basis(plan_tel)
    if live_basis == BridgeConstants.REALIZED_INCOME_BASIS and plan_basis in ("", "modelled"):
        return "income_basis_changed"

    if active_plan_ts > 0.0 and (eff_now - active_plan_ts) >= BridgeConstants.AI_DRIFT_REPLAN_MAX_AGE_SECONDS:
        return "plan_age_4h"
    return None


def evaluate_ai_drift_replan(
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
    state: GameState | None,
    *,
    cfg: StrategyConfig | None = None,
    now_ts: float | None = None,
    gate: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Evaluate whether adaptive AI-Planner drift triggers warrant a background replan."""
    eff_cfg = cfg or ctx.strategy_config
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    with ctx.planner_jobs_lock:
        any_running = bool(
            ctx.config_opt_job.get("status") == "running" or ctx.ai_planner_job.get("status") == "running"
        )
    last_auto_ok = (
        ctx.last_auto_ai_replan_ts <= 0.0
        or (eff_now - ctx.last_auto_ai_replan_ts) >= BridgeConstants.AI_DRIFT_REPLAN_COOLDOWN_SECONDS
    )
    adaptive_on = bool(eff_cfg.ai_adaptive_execution)
    mode_is_ai = normalize_planner_mode(eff_cfg.planner_mode) == "ai_planner"
    if gate is None and (
        not adaptive_on
        or not mode_is_ai
        or ctx.planner_closed
        or state is None
        or ctx.bot_control.get("paused")
        or ctx.bot_control.get("captcha_blocked")
        or any_running
        or not last_auto_ok
    ):
        return {
            "eligible": False,
            "cooldown_ok": bool(last_auto_ok),
            "drift_ready": bool(last_auto_ok and not any_running),
            "reason": None,
            "warm_genes": None,
            "active_plan_ts": 0.0,
            "now_ts": eff_now,
        }

    eff_gate = gate if isinstance(gate, Mapping) else evaluate_ai_planner_gate(ctx, state, cfg=eff_cfg)
    raw_rec = eff_gate.get("active_rec")
    raw_res = eff_gate.get("active_result")
    active_rec = raw_rec if isinstance(raw_rec, Mapping) else None
    active_result = raw_res if isinstance(raw_res, Mapping) else None
    active_plan_ts = extract_record_timestamp(active_rec, active_result)
    plan_cd_ok = active_plan_ts <= 0.0 or (eff_now - active_plan_ts) >= BridgeConstants.AI_DRIFT_REPLAN_COOLDOWN_SECONDS
    cooldown_ok = bool(last_auto_ok and plan_cd_ok)
    drift_ready = bool(cooldown_ok and not any_running)
    gates_ok = bool(
        adaptive_on
        and eff_gate.get("effective_planner_mode") == "ai_planner"
        and not ctx.planner_closed
        and state is not None
        and eff_gate.get("live_ai_unlocked")
        and active_rec is not None
        and active_result is not None
        and not ctx.bot_control.get("paused")
        and not ctx.bot_control.get("captcha_blocked")
        and not any_running
        and cooldown_ok
    )
    if not gates_ok or state is None or active_rec is None or active_result is None:
        return {
            "eligible": False,
            "cooldown_ok": cooldown_ok,
            "drift_ready": drift_ready,
            "reason": None,
            "warm_genes": None,
            "active_plan_ts": active_plan_ts,
            "now_ts": eff_now,
        }

    reason = _detect_drift_reason(ctx, ledgers, state, active_rec, active_result, active_plan_ts, eff_now)
    warm_genes: dict[str, object] | None = None
    if reason is not None:
        search_m = active_result.get("search")
        if isinstance(search_m, Mapping):
            raw_warm = search_m.get("tuned_genes") or search_m.get("best_parameters")
            if isinstance(raw_warm, Mapping) and raw_warm:
                warm_genes = dict(raw_warm)
    return {
        "eligible": True,
        "cooldown_ok": cooldown_ok,
        "drift_ready": drift_ready,
        "reason": reason,
        "warm_genes": warm_genes,
        "active_plan_ts": active_plan_ts,
        "now_ts": eff_now,
    }
