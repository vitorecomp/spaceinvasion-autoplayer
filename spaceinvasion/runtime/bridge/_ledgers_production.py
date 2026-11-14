"""Empire hourly production snapshots and loot/recycle time-series telemetry helpers."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import time

from spaceinvasion.combat.points import estimate_hourly_economy
from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, normalize_planet_type
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    safe_api_float,
    safe_api_int,
    sanitize_finite_json,
    sanitize_json_dict,
)

_LOGGER = logging.getLogger(__name__)


def count_active_builders(state: GameState | None, *, now_ts: float | None = None) -> int:
    """Count distinct non-raid owned colonies with an in-progress building job."""
    if state is None or not state.planets:
        return 0
    now_w = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    pid_by_coords: dict[str, int] = {}
    owned_pids: set[int] = set()
    for p in state.planets:
        if normalize_planet_type(p) == "raid":
            continue
        pid = safe_api_int(p.planet_id, 0)
        if pid <= 0:
            continue
        owned_pids.add(pid)
        nc = normalize_coords(p.coords)
        if nc:
            pid_by_coords[nc] = pid
    active_pid = safe_api_int(state.active_planet_id, 0)
    busy_pids: set[int] = set()
    for q in state.active_queues:
        if q.category != "building":
            continue
        if q.finish_ts is not None:
            if safe_api_float(q.finish_ts, 0.0) <= now_w:
                continue
        elif q.remaining_seconds is not None and safe_api_float(q.remaining_seconds, 0.0) <= 0.0:
            continue
        q_orig = normalize_coords(q.origin_coords)
        if q_orig and q_orig in pid_by_coords:
            busy_pids.add(pid_by_coords[q_orig])
        elif not q_orig and active_pid in owned_pids:
            busy_pids.add(active_pid)
    return len(busy_pids)


def compute_current_hourly_production(state: GameState | None, *, now_ts: float | None = None) -> dict[str, object]:
    """Compute the current per-minute empire hourly production telemetry snapshot."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    minute_bucket = int(eff_now // 60) * 60
    if state is None or not state.planets:
        return {
            "minute_bucket": minute_bucket,
            "recorded_at": round(eff_now, 3),
            "pig_iron_per_hour": 0.0,
            "metal_per_hour": 0.0,
            "kryptonite_per_hour": 0.0,
            "spice_per_hour": 0.0,
            "total_per_hour": 0.0,
            "speq_per_hour": 0.0,
            "planet_count": 0,
            "active_builders": 0,
        }
    econ = estimate_hourly_economy(state)
    pig_hr = round(safe_api_float(econ.get("pig_iron_per_hr"), 0.0), 1)
    met_hr = round(max(0.0, safe_api_float(econ.get("metal_per_hr"), 0.0)), 1)
    kry_hr = round(max(0.0, safe_api_float(econ.get("kryptonite_per_hr"), 0.0)), 1)
    spi_hr = round(max(0.0, safe_api_float(econ.get("spice_per_hr"), 0.0)), 1)
    tot_hr = round(
        max(0.0, safe_api_float(econ.get("net_resources_per_hr"), max(0.0, pig_hr) + met_hr + kry_hr + spi_hr)), 1
    )
    speq_hr = round(max(0.0, safe_api_float(econ.get("spice_eq_per_hr"), 0.0)), 1)
    p_cnt = sum(1 for p in state.planets if normalize_planet_type(p) != "raid")
    b_cnt = count_active_builders(state, now_ts=eff_now)
    return {
        "minute_bucket": minute_bucket,
        "recorded_at": round(eff_now, 3),
        "pig_iron_per_hour": pig_hr,
        "metal_per_hour": met_hr,
        "kryptonite_per_hour": kry_hr,
        "spice_per_hour": spi_hr,
        "total_per_hour": tot_hr,
        "speq_per_hour": speq_hr,
        "planet_count": p_cnt,
        "active_builders": b_cnt,
    }


def record_hourly_production(
    ctx: BridgeContext,
    state: GameState | None,
    *,
    now_ts: float | None = None,
) -> dict[str, object]:
    """Persist one per-minute empire hourly production snapshot when economy signals are present."""
    snap = compute_current_hourly_production(state, now_ts=now_ts)
    if ctx.db is None or state is None or not state.planets:
        return snap
    has_economy_signal = (
        safe_api_float(snap.get("total_per_hour"), 0.0) > 0.0
        or safe_api_float(snap.get("speq_per_hour"), 0.0) > 0.0
        or any(bool(p.buildings) or bool(p.economy_rates) for p in state.planets)
    )
    if not has_economy_signal:
        return snap
    m_bucket = safe_api_int(snap["minute_bucket"], 0)
    if m_bucket == ctx.last_recorded_prod_minute:
        return snap
    try:
        ctx.db.record_hourly_production(
            pig_iron_per_hour=safe_api_float(snap["pig_iron_per_hour"], 0.0),
            metal_per_hour=safe_api_float(snap["metal_per_hour"], 0.0),
            kryptonite_per_hour=safe_api_float(snap["kryptonite_per_hour"], 0.0),
            spice_per_hour=safe_api_float(snap["spice_per_hour"], 0.0),
            total_per_hour=safe_api_float(snap["total_per_hour"], 0.0),
            speq_per_hour=safe_api_float(snap["speq_per_hour"], 0.0),
            planet_count=safe_api_int(snap["planet_count"], 0),
            active_builders=safe_api_int(snap["active_builders"], 0),
            ts=safe_api_float(snap["recorded_at"], time.time()),
        )
        ctx.last_recorded_prod_minute = m_bucket
        ctx.hourly_prod_cache_key = None
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to record hourly production snapshot", exc_info=True)
    return snap


def get_hourly_production_history(
    ctx: BridgeContext,
    hours: int = 168,
    *,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """Return cached chronological per-minute empire hourly production history."""
    if ctx.db is None:
        return []
    eff_limit = int(limit) if limit is not None else min(1440, max(1, safe_api_int(hours, 24) * 60))
    tok = ctx.db_total_changes_token()
    ckey = (tok, eff_limit) if tok is not None else None
    if ckey is not None and ctx.hourly_prod_cache_key == ckey:
        return [dict(r) for r in ctx.hourly_prod_cache]
    try:
        rows = ctx.db.get_hourly_production_history(limit=eff_limit)
        raw_sanitized = sanitize_finite_json([dict(r) for r in rows if isinstance(r, Mapping)])
        clean: list[dict[str, object]] = (
            [dict(r) for r in raw_sanitized if isinstance(r, Mapping)] if isinstance(raw_sanitized, list) else []
        )
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to read hourly production history", exc_info=True)
        clean = []
    if ckey is not None:
        ctx.hourly_prod_cache_key = ckey
        ctx.hourly_prod_cache = clean
    return [dict(r) for r in clean]


def _empty_loot_recycle_payload(end_bucket: int, win_hours: int) -> dict[str, object]:
    start_bucket = end_bucket - (win_hours - 1) * 3600
    empty_hist: list[dict[str, object]] = [
        {
            "hour_bucket": hb,
            "stolen_pig_iron": 0.0,
            "stolen_metal": 0.0,
            "stolen_kryptonite": 0.0,
            "stolen_spice": 0.0,
            "stolen_total": 0.0,
            "recycled_pig_iron": 0.0,
            "recycled_metal": 0.0,
            "recycled_kryptonite": 0.0,
            "recycled_spice": 0.0,
            "recycled_total": 0.0,
            "total": 0.0,
            "attack_count": 0,
            "recycle_count": 0,
            "updated_at": 0.0,
        }
        for hb in range(start_bucket, end_bucket + 3600, 3600)
    ]
    return {
        "history": empty_hist,
        "summary": {
            "total_stolen": 0.0,
            "total_recycled": 0.0,
            "grand_total": 0.0,
            "attack_count": 0,
            "recycle_count": 0,
            "window_hours": win_hours,
            "stolen_pig_iron": 0.0,
            "stolen_metal": 0.0,
            "stolen_kryptonite": 0.0,
            "stolen_spice": 0.0,
            "recycled_pig_iron": 0.0,
            "recycled_metal": 0.0,
            "recycled_kryptonite": 0.0,
            "recycled_spice": 0.0,
        },
    }


def _copy_loot_recycle_dict(cached: Mapping[str, object]) -> dict[str, object]:
    raw_h = cached.get("history")
    raw_s = cached.get("summary")
    hist = [dict(r) for r in raw_h if isinstance(r, Mapping)] if isinstance(raw_h, list) else []
    summ = dict(raw_s) if isinstance(raw_s, Mapping) else {}
    return {"history": hist, "summary": summ}


def get_loot_recycle_hourly(
    ctx: BridgeContext,
    hours: int = 168,
    *,
    now_ts: float | None = None,
) -> dict[str, object]:
    """Return cached hourly loot and recycler telemetry over the requested trailing window."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    win_hours = max(1, min(168, safe_api_int(hours, 24)))
    end_bucket = int(eff_now // 3600) * 3600
    if ctx.db is None:
        return _empty_loot_recycle_payload(end_bucket, win_hours)
    tok = ctx.db_total_changes_token()
    ckey = (tok, end_bucket, win_hours) if (tok is not None and now_ts is None) else None
    if ckey is not None and ctx.loot_recycle_cache_key == ckey and ctx.loot_recycle_cache is not None:
        return _copy_loot_recycle_dict(ctx.loot_recycle_cache)
    try:
        payload = ctx.db.get_loot_recycle_hourly(hours=win_hours, now_ts=eff_now)
        if not isinstance(payload, Mapping):
            raise ValueError("invalid loot_recycle_hourly payload")
        raw_hist = payload.get("history")
        raw_sum = payload.get("summary")
        clean = sanitize_json_dict(
            {
                "history": [dict(r) for r in raw_hist if isinstance(r, Mapping)] if isinstance(raw_hist, list) else [],
                "summary": dict(raw_sum) if isinstance(raw_sum, Mapping) else {},
            }
        )
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to read loot_recycle_hourly telemetry", exc_info=True)
        return {
            "history": [],
            "summary": {
                "total_stolen": 0.0,
                "total_recycled": 0.0,
                "grand_total": 0.0,
                "attack_count": 0,
                "recycle_count": 0,
                "window_hours": win_hours,
            },
        }
    if ckey is not None:
        tok_after = ctx.db_total_changes_token()
        ctx.loot_recycle_cache_key = (tok_after, end_bucket, win_hours) if tok_after is not None else ckey
        ctx.loot_recycle_cache = clean
    return _copy_loot_recycle_dict(clean)


def get_realized_loot_hourly(ctx: BridgeContext) -> dict[str, object] | None:
    """Compute realized hourly raid loot plus recycling over the trailing 24h window."""
    if ctx.db is None:
        return None
    tel = get_loot_recycle_hourly(ctx, BridgeConstants.REALIZED_LOOT_WINDOW_HOURS)
    summ = tel.get("summary")
    if not isinstance(summ, Mapping) or "stolen_metal" not in summ or "recycled_metal" not in summ:
        return None
    win_h = float(max(1, safe_api_int(summ.get("window_hours"), BridgeConstants.REALIZED_LOOT_WINDOW_HOURS)))
    hourly: dict[str, float] = {}
    for rk in ("pig_iron", "metal", "kryptonite", "spice"):
        got = max(0.0, safe_api_float(summ.get(f"stolen_{rk}"), 0.0)) + max(
            0.0, safe_api_float(summ.get(f"recycled_{rk}"), 0.0)
        )
        hourly[rk] = round(got / win_h, 2)
    missions = max(0, safe_api_int(summ.get("attack_count"), 0)) + max(0, safe_api_int(summ.get("recycle_count"), 0))
    return {"hourly_loot": hourly, "missions_per_hour": round(float(missions) / win_h, 4), "window_hours": win_h}


def overlay_realized_loot(ctx: BridgeContext, atk_tel: Mapping[str, object] | None) -> dict[str, object]:
    """Overlay realized 24h loot onto an attack telemetry dict, preserving modelled hourly loot."""
    realized = get_realized_loot_hourly(ctx)
    if realized is None:
        return atk_tel if isinstance(atk_tel, dict) else (dict(atk_tel) if isinstance(atk_tel, Mapping) else {})
    out: dict[str, object] = dict(atk_tel) if isinstance(atk_tel, Mapping) else {}
    raw_hl = out.get("hourly_loot")
    if str(out.get("income_basis") or "") != BridgeConstants.REALIZED_INCOME_BASIS and isinstance(raw_hl, Mapping):
        out["modelled_hourly_loot"] = dict(raw_hl)
    raw_real_hl = realized.get("hourly_loot")
    hourly: dict[str, float] = (
        {str(k): safe_api_float(v, 0.0) for k, v in raw_real_hl.items()} if isinstance(raw_real_hl, Mapping) else {}
    )
    hourly["spice_eq"] = round(float(game_data.spice_equivalent(hourly)), 2)
    out["hourly_loot"] = hourly
    out["income_basis"] = BridgeConstants.REALIZED_INCOME_BASIS
    return out


def telemetry_income_basis(tel: object) -> str:
    """Extract normalized `income_basis` tag from a telemetry mapping."""
    if not isinstance(tel, Mapping):
        return ""
    return str(tel.get("income_basis") or "").strip().lower()
