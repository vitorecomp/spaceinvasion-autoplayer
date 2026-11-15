"""Adaptive colony-idle 10-minute bucket tracking and 24h diagnostic status helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import time

from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlannedAction, Resources
from backend.planner import ai as ai_mod
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._context_init import BridgeConstants
from backend.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_float, safe_api_int

_LOGGER = logging.getLogger(__name__)


def prune_adaptive_idle_buckets(ctx: BridgeContext, now_ts: float | None = None) -> None:
    """Prune 10-minute adaptive colony-idle diagnostic buckets older than 24h."""
    if not ctx.adaptive_idle_buckets:
        return
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    cur_bucket = int(eff_now // BridgeConstants.ADAPTIVE_IDLE_BUCKET_SECONDS)
    min_bucket = cur_bucket - (BridgeConstants.ADAPTIVE_IDLE_MAX_BUCKETS - 1)
    kept = {
        bk: ent
        for bk, ent in ctx.adaptive_idle_buckets.items()
        if isinstance(bk, int) and min_bucket <= bk <= cur_bucket + 1
    }
    if len(kept) > BridgeConstants.ADAPTIVE_IDLE_MAX_BUCKETS:
        top_keys = sorted(kept.keys())[-BridgeConstants.ADAPTIVE_IDLE_MAX_BUCKETS :]
        kept = {bk: kept[bk] for bk in top_keys}
    ctx.adaptive_idle_buckets = kept


def record_adaptive_idle_bucket(
    ctx: BridgeContext,
    state: GameState | None,
    active_plan_result: Mapping[str, object] | None,
    *,
    ai_actions: Sequence[PlannedAction] = (),
    cfg: StrategyConfig | None = None,
    field_conflicts: Sequence[Mapping[str, object]] = (),
    inbound_cargo_by_planet: Mapping[int, Resources] | None = None,
    now_ts: float | None = None,
) -> bool:
    """Record at most one 10-minute colony-idle diagnostic tally per bucket (last 24h)."""
    if state is None or not state.planets or not isinstance(active_plan_result, Mapping):
        return False
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    bucket_idx = int(eff_now // BridgeConstants.ADAPTIVE_IDLE_BUCKET_SECONDS)
    if bucket_idx in ctx.adaptive_idle_buckets:
        return False
    try:
        diag = ai_mod.diagnose_ai_colony_idle_states(
            state,
            active_plan_result,
            ai_actions=ai_actions,
            config=cfg or ctx.strategy_config,
            field_conflicts=field_conflicts,
            inbound_cargo_by_planet=inbound_cargo_by_planet,
        )
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Adaptive colony-idle diagnosis failed for bucket %s", bucket_idx, exc_info=True)
        return False
    if not isinstance(diag, Mapping):
        return False
    total_col = max(0, safe_api_int(diag.get("total_colonies", 0), 0))
    if total_col <= 0:
        return False
    raw_cnts = diag.get("counts")
    clean_cnts: dict[str, int] = {k: 0 for k in BridgeConstants.ADAPTIVE_IDLE_STATE_KEYS}
    if isinstance(raw_cnts, Mapping):
        for ck, cv in raw_cnts.items():
            ck_s = str(ck or "").strip()
            if ck_s:
                clean_cnts[ck_s] = max(0, safe_api_int(cv, 0))
    ctx.adaptive_idle_buckets[bucket_idx] = {
        "bucket": bucket_idx,
        "ts": round(eff_now, 3),
        "total_colonies": total_col,
        "active_builders": max(0, safe_api_int(diag.get("active_builders", 0), 0)),
        "idle_with_affordable": max(0, safe_api_int(diag.get("idle_with_affordable_count", 0), 0)),
        "ready_not_started": max(0, safe_api_int(diag.get("ready_not_started_count", 0), 0)),
        "counts": clean_cnts,
    }
    prune_adaptive_idle_buckets(ctx, eff_now)
    return True


def summarize_adaptive_idle_window_24h(ctx: BridgeContext, now_ts: float | None = None) -> dict[str, object]:
    """Aggregate trailing 24h adaptive colony-idle diagnostic buckets into percentage shares."""
    prune_adaptive_idle_buckets(ctx, now_ts)
    buckets = list(ctx.adaptive_idle_buckets.values())
    colony_samples = 0
    idle_aff_sum = 0
    ready_sum = 0
    state_totals: dict[str, int] = {k: 0 for k in BridgeConstants.ADAPTIVE_IDLE_STATE_KEYS}
    for ent in buckets:
        if not isinstance(ent, Mapping):
            continue
        colony_samples += max(0, safe_api_int(ent.get("total_colonies", 0), 0))
        idle_aff_sum += max(0, safe_api_int(ent.get("idle_with_affordable", 0), 0))
        ready_sum += max(0, safe_api_int(ent.get("ready_not_started", 0), 0))
        raw_c = ent.get("counts")
        if isinstance(raw_c, Mapping):
            for ck, cv in raw_c.items():
                ck_s = str(ck or "").strip()
                if ck_s:
                    state_totals[ck_s] = state_totals.get(ck_s, 0) + max(0, safe_api_int(cv, 0))
    if colony_samples > 0:
        idle_aff_pct = round(100.0 * float(idle_aff_sum) / float(colony_samples), 2)
        ready_pct = round(100.0 * float(ready_sum) / float(colony_samples), 2)
        shares = {st: round(100.0 * float(cnt) / float(colony_samples), 2) for st, cnt in state_totals.items()}
    else:
        idle_aff_pct = 0.0
        ready_pct = 0.0
        shares = {st: 0.0 for st in state_totals}
    return {
        "bucket_count": len(buckets),
        "colony_samples": colony_samples,
        "idle_with_affordable_share_pct": idle_aff_pct,
        "ready_not_started_share_pct": ready_pct,
        "state_shares_pct": shares,
    }


def build_adaptive_status(
    ctx: BridgeContext,
    state: GameState | None,
    active_result: Mapping[str, object] | None,
    *,
    cfg: StrategyConfig | None = None,
    field_conflicts: Sequence[Mapping[str, object]] = (),
    custom_names: Mapping[str, str] | None = None,
    transport_fleet: dict[str, object] | None = None,
    auto_replan: dict[str, object] | None = None,
    now_ts: float | None = None,
) -> dict[str, object]:
    """Assemble the complete `adaptive_status` block for `ai_planner_get`."""
    eff_cfg = cfg or ctx.strategy_config
    names_map = custom_names if custom_names is not None else ctx.custom_planet_names
    window_24h = summarize_adaptive_idle_window_24h(ctx, now_ts)
    if state is not None:
        try:
            raw_diag = ai_mod.diagnose_ai_colony_idle_states(
                state,
                active_result if isinstance(active_result, Mapping) else {},
                config=eff_cfg,
                field_conflicts=field_conflicts,
            )
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.debug("Adaptive status diagnosis failed; returning empty summary", exc_info=True)
            raw_diag = None
        if isinstance(raw_diag, Mapping):
            diag_cols: list[dict[str, object]] = []
            raw_cols = raw_diag.get("colonies")
            if isinstance(raw_cols, Sequence) and not isinstance(raw_cols, (str, bytes)):
                for col_item in raw_cols:
                    if not isinstance(col_item, Mapping):
                        continue
                    cd: dict[str, object] = dict(col_item)
                    cpid = safe_api_int(cd.get("planet_id"), 0)
                    if cpid > 0 and names_map.get(str(cpid)):
                        cd["name"] = names_map[str(cpid)]
                    diag_cols.append(cd)
            raw_counts = raw_diag.get("counts")
            counts_clean = (
                {str(k): max(0, safe_api_int(v, 0)) for k, v in raw_counts.items()}
                if isinstance(raw_counts, Mapping)
                else {}
            )
            return {
                "enabled": bool(eff_cfg.ai_adaptive_execution),
                "active_builders": max(0, safe_api_int(raw_diag.get("active_builders", 0), 0)),
                "total_colonies": max(0, safe_api_int(raw_diag.get("total_colonies", len(diag_cols)), len(diag_cols))),
                "colonies": diag_cols,
                "counts": counts_clean,
                "idle_with_affordable_count": max(0, safe_api_int(raw_diag.get("idle_with_affordable_count", 0), 0)),
                "ready_not_started_count": max(0, safe_api_int(raw_diag.get("ready_not_started_count", 0), 0)),
                "window_24h": window_24h,
                "transport_fleet": transport_fleet,
                "auto_replan": auto_replan,
            }
    return {
        "enabled": bool(eff_cfg.ai_adaptive_execution),
        "active_builders": 0,
        "total_colonies": 0,
        "colonies": [],
        "counts": {k: 0 for k in BridgeConstants.ADAPTIVE_IDLE_STATE_KEYS},
        "idle_with_affordable_count": 0,
        "ready_not_started_count": 0,
        "window_24h": window_24h,
        "transport_fleet": transport_fleet,
        "auto_replan": auto_replan,
    }
