"""Hydration helpers for loading persisted bridge runtime state from PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import math

from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    bounded_api_str,
    safe_api_float,
    safe_api_int,
)

_LOGGER = logging.getLogger(__name__)


def hydrate_bot_control_and_scalars(ctx: BridgeContext, rt: Mapping[str, object]) -> None:
    """Hydrate bot control flags, economy timestamps, overflow pauses, and unread counters."""
    bc = rt.get("bot_control")
    if isinstance(bc, Mapping):
        ctx.bot_control["paused"] = bool(bc.get("paused", False))
        raw_mo = bc.get("mode_override")
        ctx.bot_control["mode_override"] = raw_mo if raw_mo in (None, "live", "dry-run") else None
        ctx.bot_control["captcha_blocked"] = bool(bc.get("captcha_blocked", False))
        raw_cm = bc.get("captcha_message")
        ctx.bot_control["captcha_message"] = bounded_api_str(raw_cm, default="") if raw_cm else None

    econ_ts = rt.get("economy_sync_timestamps")
    if isinstance(econ_ts, Mapping):
        for raw_pid, raw_ts in econ_ts.items():
            pid_i = safe_api_int(raw_pid, 0)
            ts_f = safe_api_float(raw_ts, 0.0)
            if pid_i > 0 and ts_f > 0.0:
                ctx.economy_sync_timestamps[pid_i] = ts_f

    overflow_paused = rt.get("metal_overflow_paused_planets")
    if isinstance(overflow_paused, Mapping):
        for raw_pid, raw_ts in overflow_paused.items():
            pid_i = safe_api_int(raw_pid, 0)
            ts_f = safe_api_float(raw_ts, 0.0)
            if pid_i > 0 and ts_f > 0.0:
                ctx.metal_overflow_paused_planets[pid_i] = ts_f

    ctx.last_synced_unread_total = max(0, safe_api_int(rt.get("last_synced_unread_total", 0), 0))
    ctx.pending_spy_sync = bool(rt.get("pending_spy_sync", False))
    last_url = rt.get("last_url")
    if isinstance(last_url, str) and last_url:
        ctx.last_url = bounded_api_str(last_url, default="")


def hydrate_timers_and_slots(
    ctx: BridgeContext,
    rt: Mapping[str, object],
    *,
    now_wall: float,
    now_mono: float,
) -> None:
    """Hydrate wall-clock cooldowns, observed fleet slots, and sync monotonic timestamps."""
    fsc_wall = rt.get("fleet_slot_cooldown_until_wall")
    if isinstance(fsc_wall, Mapping):
        for raw_pid, raw_until in fsc_wall.items():
            pid_i = safe_api_int(raw_pid, 0)
            until_w = safe_api_float(raw_until, 0.0)
            rem = until_w - now_wall
            if pid_i > 0 and 0.0 < rem <= 86400.0:
                ctx.fleet_slot_cooldown_until[pid_i] = now_mono + rem

    raw_obs_off = rt.get("observed_fleet_slot_base_offset")
    if isinstance(raw_obs_off, int) and not isinstance(raw_obs_off, bool) and raw_obs_off in (0, 1):
        ctx.observed_fleet_slot_base_offset = raw_obs_off

    raw_obs_pid_slots = rt.get("observed_fleet_slots_by_pid")
    if isinstance(raw_obs_pid_slots, Mapping):
        for raw_pid_s, raw_pair in raw_obs_pid_slots.items():
            pid_i = safe_api_int(raw_pid_s, 0)
            if (
                pid_i > 0
                and isinstance(raw_pair, Sequence)
                and not isinstance(raw_pair, (str, bytes))
                and len(raw_pair) == 2
            ):
                b10_v = safe_api_int(raw_pair[0], -1)
                av_v = safe_api_int(raw_pair[1], -1)
                if b10_v >= 0 and av_v >= 0:
                    ctx.observed_fleet_slots_by_pid[pid_i] = (b10_v, av_v)

    def _wall_to_mono(wall_key: str) -> float:
        wv = safe_api_float(rt.get(wall_key, 0.0), 0.0)
        if wv <= 0.0:
            return 0.0
        age = now_wall - wv
        if 0.0 <= age <= 86400.0:
            return max(0.001, now_mono - age)
        return 0.0

    ctx.last_spio_sync_at = _wall_to_mono("spio_sync_at_wall")
    ctx.last_research_sync_at = _wall_to_mono("research_sync_at_wall")
    ctx.last_shipyard_sync_at = _wall_to_mono("shipyard_sync_at_wall")
    ctx.last_spy_dispatched_at = _wall_to_mono("spy_dispatched_at_wall")


def hydrate_transport_and_spend_ledgers(ctx: BridgeContext, rt: Mapping[str, object]) -> None:
    """Hydrate delivered transport events, infra transport spend, spare ship spend, and AI replan state."""
    raw_deliv = rt.get("delivered_transport_events")
    loaded_deliv: list[dict[str, object]] = []
    seen_deliv_keys: set[str] = set()
    if isinstance(raw_deliv, Sequence) and not isinstance(raw_deliv, (str, bytes)):
        for ent in raw_deliv:
            if not isinstance(ent, Mapping):
                continue
            ek = str(ent.get("key") or "").strip()[:160]
            ets = safe_api_float(ent.get("ts"), 0.0)
            ecargo = max(0, safe_api_int(ent.get("cargo"), 0))
            efleet = max(0, safe_api_int(ent.get("fleet_cargo"), 0))
            if not ek or ek in seen_deliv_keys or ecargo <= 0 or ets <= 0.0:
                continue
            seen_deliv_keys.add(ek)
            loaded_deliv.append({"key": ek, "ts": round(ets, 3), "cargo": ecargo, "fleet_cargo": efleet})
    ctx.delivered_transport_events = loaded_deliv

    raw_spend = rt.get("infra_transport_spend_ledger")
    loaded_spend: list[dict[str, object]] = []
    if isinstance(raw_spend, Sequence) and not isinstance(raw_spend, (str, bytes)):
        for ent in raw_spend:
            if not isinstance(ent, Mapping):
                continue
            sts = safe_api_float(ent.get("ts"), 0.0)
            smet = max(0, safe_api_int(ent.get("metal"), 0))
            skry = max(0, safe_api_int(ent.get("kryptonite"), 0))
            if sts > 0.0 and (smet > 0 or skry > 0):
                spend_row: dict[str, object] = {"ts": round(sts, 3), "metal": smet, "kryptonite": skry}
                scargo = max(0, safe_api_int(ent.get("cargo"), 0))
                if scargo > 0:
                    spend_row["cargo"] = scargo
                loaded_spend.append(spend_row)
    ctx.infra_transport_spend_ledger = loaded_spend

    raw_last_spend = safe_api_float(rt.get("infra_transport_last_spend_ts", 0.0), 0.0)
    if math.isfinite(raw_last_spend) and raw_last_spend > 0.0:
        ctx.infra_transport_last_spend_ts = raw_last_spend

    raw_spare_spend = rt.get("spare_ship_spend_ledger")
    loaded_spare: list[dict[str, object]] = []
    if isinstance(raw_spare_spend, Sequence) and not isinstance(raw_spare_spend, (str, bytes)):
        for ent in raw_spare_spend:
            if not isinstance(ent, Mapping):
                continue
            sp_ts = safe_api_float(ent.get("ts"), 0.0)
            sp_row = {rk: max(0, safe_api_int(ent.get(rk), 0)) for rk in ("pig_iron", "metal", "kryptonite", "spice")}
            if sp_ts > 0.0 and any(v > 0 for v in sp_row.values()):
                loaded_spare.append({"ts": round(sp_ts, 3), **sp_row})
    ctx.spare_ship_spend_ledger = loaded_spare

    raw_replan_ts = safe_api_float(rt.get("last_auto_ai_replan_ts", 0.0), 0.0)
    if math.isfinite(raw_replan_ts) and raw_replan_ts > 0.0:
        ctx.last_auto_ai_replan_ts = raw_replan_ts
    raw_replan_reason = rt.get("last_auto_ai_replan_reason")
    if isinstance(raw_replan_reason, str) and raw_replan_reason.strip():
        ctx.last_auto_ai_replan_reason = bounded_api_str(raw_replan_reason, default="")[:128]


def hydrate_adaptive_idle_buckets(ctx: BridgeContext, rt: Mapping[str, object]) -> None:
    """Hydrate 10-minute adaptive idle-bucket diagnostics from persisted runtime state."""
    raw_buckets = rt.get("adaptive_idle_buckets")
    loaded_buckets: dict[int, dict[str, object]] = {}
    bucket_items: list[tuple[object, object]] = []
    if isinstance(raw_buckets, Mapping):
        bucket_items = list(raw_buckets.items())
    elif isinstance(raw_buckets, Sequence) and not isinstance(raw_buckets, (str, bytes)):
        bucket_items = [
            (ent.get("bucket"), ent)
            for ent in raw_buckets
            if isinstance(ent, Mapping) and ent.get("bucket") is not None
        ]
    for raw_bk, raw_ent in bucket_items:
        if not isinstance(raw_ent, Mapping):
            continue
        bk_i = safe_api_int(raw_bk, 0) or safe_api_int(raw_ent.get("bucket"), 0)
        if bk_i <= 0:
            continue
        raw_cnts = raw_ent.get("counts")
        clean_cnts: dict[str, int] = {}
        if isinstance(raw_cnts, Mapping):
            for ck, cv in raw_cnts.items():
                ck_s = str(ck or "").strip()
                if ck_s:
                    clean_cnts[ck_s] = max(0, safe_api_int(cv, 0))
        tot_col = max(0, safe_api_int(raw_ent.get("total_colonies", 0), 0))
        if tot_col <= 0 and clean_cnts:
            tot_col = sum(clean_cnts.values())
        if tot_col <= 0:
            continue
        loaded_buckets[bk_i] = {
            "bucket": bk_i,
            "ts": round(safe_api_float(raw_ent.get("ts"), float(bk_i * 600)), 3),
            "total_colonies": tot_col,
            "active_builders": max(0, safe_api_int(raw_ent.get("active_builders", clean_cnts.get("building", 0)), 0)),
            "idle_with_affordable": max(0, safe_api_int(raw_ent.get("idle_with_affordable", 0), 0)),
            "ready_not_started": max(
                0,
                safe_api_int(raw_ent.get("ready_not_started", clean_cnts.get("ready_not_started", 0)), 0),
            ),
            "counts": clean_cnts,
        }
    ctx.adaptive_idle_buckets = loaded_buckets


def hydrate_db_auxiliary_caches(ctx: BridgeContext, db: GameDatabase) -> None:
    """Load persisted custom planet names, debris amounts, transport dispatches, and galaxy bounds."""
    try:
        loaded_names = db.get_custom_planet_names()
        if isinstance(loaded_names, Mapping):
            ctx.custom_planet_names = {
                str(k): str(v) for k, v in loaded_names.items() if str(k).strip() and str(v).strip()
            }
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to load persisted custom planet names", exc_info=True)

    try:
        loaded_deb = db.get_galaxy_debris_amounts()
        if isinstance(loaded_deb, Mapping):
            ctx.galaxy_debris_amounts = {
                str(k): {str(rk): safe_api_int(rv, 0) for rk, rv in v.items()}
                for k, v in loaded_deb.items()
                if isinstance(v, Mapping)
            }
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to load persisted galaxy debris amounts", exc_info=True)

    hydrated_dispatches: list[dict[str, object]] = []
    try:
        rows_tr = db.get_recent_transport_dispatches(max_age_seconds=86400.0, limit=120)
        if isinstance(rows_tr, Sequence):
            hydrated_dispatches.extend(dict(r) for r in rows_tr if isinstance(r, Mapping))
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to load persisted transport dispatches", exc_info=True)

    try:
        rows_fb = db.get_recent_transport_actions_fallback(max_age_seconds=86400.0, limit=60)
        if isinstance(rows_fb, Sequence):
            existing_sigs = {
                (
                    str(r.get("origin_coords") or ""),
                    str(r.get("dest_coords") or ""),
                    int(round(safe_api_float(r.get("ts"), 0.0) / 60.0)),
                )
                for r in hydrated_dispatches
            }
            for fb in rows_fb:
                if not isinstance(fb, Mapping):
                    continue
                sig = (
                    str(fb.get("origin_coords") or ""),
                    str(fb.get("dest_coords") or ""),
                    int(round(safe_api_float(fb.get("ts"), 0.0) / 60.0)),
                )
                if sig not in existing_sigs:
                    hydrated_dispatches.append(dict(fb))
                    existing_sigs.add(sig)
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to load transport fallback actions", exc_info=True)

    hydrated_dispatches.sort(key=lambda r: safe_api_float(r.get("ts"), 0.0))
    ctx.recent_transport_dispatches = hydrated_dispatches[-120:]

    try:
        raw_mg = db.load_meta_json("universe_max_galaxies")
        mg_val = safe_api_int(raw_mg, 0)
        if 1 <= mg_val <= 14:
            ctx.universe_max_galaxies = mg_val
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to load persisted universe_max_galaxies", exc_info=True)
