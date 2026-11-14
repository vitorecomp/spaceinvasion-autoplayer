"""Cached DB summary, raid stats, colony candidate merge, and state telemetry attachment helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
import time

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, count_allocated_normal_planets
from spaceinvasion.planner.heuristics import _tutorial_colonize_task
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._helpers_sig import supports_kwarg
from spaceinvasion.runtime.bridge._ledgers_queue_enrich import enrich_active_queues
from spaceinvasion.runtime.bridge._ledgers_transmitter import get_transmitter_status


def get_db_summary_cached(ctx: BridgeContext) -> dict[str, object] | None:
    """Return cached `db.summary()` keyed by database change token."""
    if ctx.db is None:
        return None
    tok = ctx.db_total_changes_token()
    ckey = (tok, ctx.db.summary) if tok is not None else None
    if ckey is not None and ctx.db_summary_cache_key == ckey and ctx.db_summary_cache is not None:
        return dict(ctx.db_summary_cache)
    raw = ctx.db.summary()
    clean = sanitize_json_dict(raw) if isinstance(raw, Mapping) else None
    if ckey is not None and clean is not None:
        ctx.db_summary_cache_key = ckey
        ctx.db_summary_cache = clean
        return dict(clean)
    return clean


def get_raid_stats_cached(ctx: BridgeContext, *, include_telemetry_summary: bool = True) -> dict[str, object]:
    """Return cached raid stats with automatic 10-second `blocked_targets` remaining-time refresh."""
    if ctx.db is None:
        return {}
    get_stats = ctx.db.get_raid_stats
    if not include_telemetry_summary:
        raw_rs = (
            get_stats(include_telemetry_summary=False)
            if supports_kwarg(get_stats, "include_telemetry_summary")
            else get_stats()
        )
        return dict(raw_rs) if isinstance(raw_rs, Mapping) else {}
    tok = ctx.db_total_changes_token()
    rs_ckey = (tok, get_stats) if tok is not None else None
    now_wall = time.time()
    if rs_ckey is not None and ctx.raid_stats_cache_key == rs_ckey and ctx.raid_stats_cache is not None:
        stats = ctx.raid_stats_cache
        raw_bt = stats.get("blocked_targets")
        if isinstance(raw_bt, Mapping) and raw_bt:
            needs_refresh = (now_wall - ctx.raid_stats_cache_ts) >= 10.0 or any(
                safe_api_float(v.get("blocked_until_ts", 0.0)) <= now_wall
                for v in raw_bt.values()
                if isinstance(v, Mapping)
            )
            if needs_refresh:
                fresh_bt: dict[str, dict[str, object]] = {}
                for bk, bv in raw_bt.items():
                    if not isinstance(bv, Mapping):
                        continue
                    b_until = safe_api_float(bv.get("blocked_until_ts", 0.0))
                    if b_until > now_wall:
                        nbv = dict(bv)
                        nbv["remaining_seconds"] = round(max(0.0, b_until - now_wall), 1)
                        fresh_bt[str(bk)] = nbv
                stats = dict(stats)
                stats["blocked_targets"] = fresh_bt
                ctx.raid_stats_cache = stats
                ctx.raid_stats_cache_ts = now_wall
        return dict(stats)
    stats_raw = get_stats()
    stats_dict: dict[str, object] = dict(stats_raw) if isinstance(stats_raw, Mapping) else {}
    if rs_ckey is not None:
        ctx.raid_stats_cache_key = rs_ckey
        ctx.raid_stats_cache = dict(stats_dict)
        ctx.raid_stats_cache_ts = now_wall
    return stats_dict


def own_planet_coords(ctx: BridgeContext, state: GameState | None = None) -> set[str]:
    """Return normalized coordinates of all planets belonging to the player."""
    coords: set[str] = set()
    for src in (state, ctx.last_state):
        if src is None:
            continue
        for p in src.planets:
            nc = normalize_coords(p.coords)
            if nc:
                coords.add(nc)
    return coords


def merge_colony_candidates_if_needed(ctx: BridgeContext, state: GameState, cfg: StrategyConfig) -> GameState:
    """Merge uncolonized galaxy colony candidates into `state.raid_targets` when below `max_planets`."""
    if ctx.db is None:
        return state
    max_p = max(1, safe_api_int(cfg.max_planets, 11))
    if count_allocated_normal_planets(state) >= max_p:
        return state
    if not (cfg.allow_colonize or cfg.allow_galaxy_scan or _tutorial_colonize_task(state) is not None):
        return state
    discarded_set = set(ctx.db.get_discarded_colony_coords())
    ap = state.active_planet()
    origin_c = ap.coords if ap is not None else ""
    min_f = max(50, safe_api_int(cfg.min_colony_fields, 300))
    col_cands = ctx.db.get_colony_candidates(
        origin_coords=origin_c,
        min_fields_target=min_f,
        exclude_coords=own_planet_coords(ctx, state),
        limit=200,
    )
    by_c: dict[str, dict[str, object]] = {}
    ordered_rt: list[dict[str, object]] = []
    changed_rt = False
    for r_item in state.raid_targets:
        if not isinstance(r_item, Mapping):
            continue
        d_item: dict[str, object] = dict(r_item)
        nc = normalize_coords(str(d_item.get("coords") or d_item.get("target_coords") or ""))
        if nc and nc in discarded_set and str(d_item.get("status_color") or "").lower() == "free":
            changed_rt = True
            continue
        if nc:
            by_c[nc] = d_item
        ordered_rt.append(d_item)
    if not col_cands:
        return replace(state, raid_targets=tuple(ordered_rt)) if changed_rt else state
    added_any = changed_rt
    for cand in col_cands:
        if not isinstance(cand, Mapping):
            continue
        nc = normalize_coords(str(cand.get("coords") or ""))
        if not nc or nc in discarded_set:
            continue
        cand_blk = safe_api_float(cand.get("blocked_until_ts", 0.0))
        cand_rsn = str(cand.get("block_reason") or "")
        if nc in ctx.target_blocks and float(ctx.target_blocks[nc][0]) > cand_blk:
            cand_blk = float(ctx.target_blocks[nc][0])
            cand_rsn = str(ctx.target_blocks[nc][1])
        if nc in by_c:
            cur_entry = by_c[nc]
            uf = cand.get("uninhabited_fields")
            if uf is not None and cur_entry.get("uninhabited_fields") is None:
                cur_entry["uninhabited_fields"] = safe_api_int(uf, 0)
                added_any = True
            for ek in ("colonize_min_fields", "colonize_max_fields", "temperature"):
                ev = cand.get(ek)
                if ev is not None and cur_entry.get(ek) is None:
                    cur_entry[ek] = safe_api_int(ev, 0)
                    added_any = True
            if cand_blk > safe_api_float(cur_entry.get("blocked_until_ts", 0.0)):
                cur_entry["blocked_until_ts"] = cand_blk
                cur_entry["block_reason"] = cand_rsn
                added_any = True
            continue
        free_entry: dict[str, object] = {
            "coords": nc,
            "owner": "",
            "planet_name": "",
            "status_color": "free",
            "is_inactive": False,
            "is_spacia": False,
            "has_debris": False,
            "points": None,
            "uninhabited_fields": cand.get("uninhabited_fields"),
            "colonize_min_fields": cand.get("colonize_min_fields"),
            "colonize_max_fields": cand.get("colonize_max_fields"),
            "temperature": cand.get("temperature"),
            "last_scanned_ts": safe_api_float(cand.get("last_scanned_ts", 0.0)),
            "last_spied_ts": safe_api_float(cand.get("last_spied_ts", 0.0)),
            "blocked_until_ts": cand_blk,
            "block_reason": cand_rsn,
            "has_spy_intel": bool(cand.get("probed", False)),
        }
        by_c[nc] = free_entry
        ordered_rt.append(free_entry)
        added_any = True
    return replace(state, raid_targets=tuple(ordered_rt)) if added_any else state


def attach_raid_stats(
    ctx: BridgeContext,
    state: GameState,
    *,
    on_persist_runtime_state: Callable[[], None] | None = None,
) -> GameState:
    """Attach cached raid stats, transmitter status, colony candidates, and enriched active queues onto `state`."""
    existing_rs = state.raid_stats
    base_rs: dict[str, object] = dict(existing_rs) if isinstance(existing_rs, Mapping) else {}
    if ctx.db is not None:
        base_rs.update(get_raid_stats_cached(ctx, include_telemetry_summary=True))
        base_rs["transmitter_status"] = get_transmitter_status(ctx)
    elif ctx.strategy_config.allow_transmitter:
        base_rs["transmitter_status"] = get_transmitter_status(ctx)
    enriched = replace(state, raid_stats=base_rs) if base_rs != existing_rs else state
    enriched = merge_colony_candidates_if_needed(ctx, enriched, ctx.strategy_config)
    return enrich_active_queues(ctx, enriched, on_persist_runtime_state=on_persist_runtime_state)
