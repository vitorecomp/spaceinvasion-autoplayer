"""Scanned-sectors summary and orchestrator for `galaxy_intel`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from spaceinvasion.combat.fleet import score_galaxy_scan_sectors
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.state import GameState, count_allocated_normal_planets
from spaceinvasion.runtime.api.handlers._bridge_proto import BridgeApiFacade
from spaceinvasion.runtime.api.handlers._galaxy_intel_helpers import (
    max_galaxies_for_ctx,
    own_sweep_coords,
    owned_sweep_galaxies,
    parse_origin_gsp,
    plan_smart_sweep_locked,
    store_smart_sweep_summary_locked,
    sweep_target_galaxies,
)
from spaceinvasion.runtime.api.handlers._galaxy_intel_sector_rows import (
    aggregate_galaxy_stats,
    build_scan_queue,
    collect_sector_rows,
)
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int, sanitize_json_dict


@dataclass(frozen=True)
class _SectorConfigSnapshot:
    sweep_pages: int
    refresh_cfg: int
    explore_cfg: bool
    scan_all_cfg: bool
    saved_pages: int
    saved_refresh: int
    saved_explore: bool
    saved_all: bool
    sweep_interval: int
    allow_scan: bool


def _resolve_origin_coords(state_snap: GameState | None, req_pid: int | None) -> str:
    if state_snap is None:
        return ""
    orig_p = next((p for p in state_snap.planets if p.planet_id == req_pid), None) if req_pid is not None else None
    orig_p = orig_p or state_snap.active_planet() or (state_snap.planets[0] if state_snap.planets else None)
    return normalize_coords(orig_p.coords) if orig_p is not None and orig_p.coords else ""


def _resolve_sector_config(
    bridge: BridgeApiFacade,
    *,
    preview_refresh_pct: int | None,
    preview_sweep_pages: int | None,
    preview_all_galaxies: bool | None,
) -> _SectorConfigSnapshot:
    cfg = bridge.ctx.strategy_config
    saved_pages = max(1, min(400, safe_api_int(cfg.galaxy_sweep_pages, 100)))
    saved_refresh = max(0, min(100, safe_api_int(cfg.galaxy_scan_refresh_pct, 0)))
    saved_explore = bool(cfg.galaxy_scan_explore_first)
    saved_all = bool(cfg.galaxy_scan_all_galaxies)
    refresh_cfg = preview_refresh_pct if preview_refresh_pct is not None else saved_refresh
    return _SectorConfigSnapshot(
        sweep_pages=preview_sweep_pages if preview_sweep_pages is not None else saved_pages,
        refresh_cfg=refresh_cfg,
        explore_cfg=(refresh_cfg < 100) if preview_refresh_pct is not None else saved_explore,
        scan_all_cfg=preview_all_galaxies if preview_all_galaxies is not None else saved_all,
        saved_pages=saved_pages,
        saved_refresh=saved_refresh,
        saved_explore=saved_explore,
        saved_all=saved_all,
        sweep_interval=safe_api_int(cfg.galaxy_sweep_interval_seconds, 270),
        allow_scan=bool(cfg.allow_galaxy_scan),
    )


def _score_sectors_map(
    bridge: BridgeApiFacade,
    *,
    score_gals: Sequence[int],
    state_snap: GameState | None,
    origin_coords: str,
    intel: Mapping[str, object],
    scanned_snap: Mapping[str, float],
    raid_snap: Sequence[Mapping[str, object]],
    collect_colonies: bool,
    now_ts: float,
) -> dict[str, Mapping[str, object]]:
    if not score_gals:
        return {}
    cfg = bridge.ctx.strategy_config
    raid_st = state_snap.raid_stats if state_snap is not None and isinstance(state_snap.raid_stats, Mapping) else None
    scored = score_galaxy_scan_sectors(
        own_coords=own_sweep_coords(state_snap) or ([origin_coords] if origin_coords else ()),
        sector_intel=intel,
        galaxies=score_gals,
        scanned_systems=scanned_snap,
        raid_targets=raid_snap,
        raid_stats=raid_st,
        explore_first=False,
        near_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_near_weight, 2.0))),
        attack_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_attack_weight, 3.0))),
        collect_colonies=collect_colonies,
        now_ts=now_ts,
    )
    return {str(sc.get("sector") or ""): sc for sc in scored if sc.get("sector")}


def _build_sectors_summary(
    *,
    rows_out: Sequence[Mapping[str, object]],
    total_scanned_all: int,
    intel: Mapping[str, object],
    sweep_summary: Mapping[str, object],
    smart_plan: Mapping[str, object] | None,
    is_preview: bool,
    scan_queue: Sequence[Mapping[str, object]],
    queue_gals: Sequence[int],
    max_g: int,
    snap: _SectorConfigSnapshot,
) -> dict[str, object]:
    eff_refresh = snap.refresh_cfg if snap.refresh_cfg > 0 else (0 if snap.explore_cfg else 100)
    saved_eff_refresh = snap.saved_refresh if snap.saved_refresh > 0 else (0 if snap.saved_explore else 100)
    planned_refresh = int(round(snap.sweep_pages * (eff_refresh / 100.0)))
    scanned_rows = [r for r in rows_out if r["scanned"]]
    total_occ = sum(safe_api_int(r.get("occupied_count"), 0) for r in scanned_rows)
    fallback_exp = safe_api_int(sweep_summary.get("universe_explored_systems"), 0)
    uni_exp = max(total_scanned_all, safe_api_int(intel.get("universe_explored_systems"), fallback_exp))
    uni_tot = max(1, safe_api_int(intel.get("universe_total_systems"), max_g * 400))
    q_disc = sum(1 for q in scan_queue if q.get("phase") == "exploratory")
    q_ref = len(scan_queue) - q_disc
    scope_src: Mapping[str, object] = smart_plan if (is_preview and isinstance(smart_plan, Mapping)) else sweep_summary
    stale_1h = sum(
        1
        for r in scanned_rows
        if r.get("age_seconds") is not None and safe_api_float(r.get("age_seconds"), 0.0) >= 3600.0
    )
    return {
        "scanned_sectors": len(scanned_rows),
        "unscanned_sectors_in_view": len(rows_out) - len(scanned_rows),
        "unscanned_total_all_galaxies": max(0, uni_tot - uni_exp),
        "total_scanned_all_galaxies": total_scanned_all,
        "explored_systems": safe_api_int(scope_src.get("explored_systems"), total_scanned_all),
        "unscanned_systems": safe_api_int(scope_src.get("unscanned_systems"), 0),
        "total_systems": safe_api_int(scope_src.get("total_systems"), len(queue_gals) * 400),
        "coverage_pct": round(safe_api_float(scope_src.get("coverage_pct"), 0.0), 2),
        "universe_explored_systems": uni_exp,
        "universe_total_systems": uni_tot,
        "universe_coverage_pct": round(min(100.0, (uni_exp / uni_tot) * 100.0), 2),
        "total_occupied": total_occ,
        "avg_occupied": round(total_occ / len(scanned_rows), 1) if scanned_rows else 0.0,
        "total_free": sum(safe_api_int(r.get("free_count"), 0) for r in scanned_rows),
        "total_prime_free": sum(safe_api_int(r.get("prime_colony_free_count"), 0) for r in scanned_rows),
        "total_debris_fields": sum(safe_api_int(r.get("debris_count"), 0) for r in scanned_rows),
        "sectors_with_debris": sum(1 for r in scanned_rows if safe_api_int(r.get("debris_count"), 0) > 0),
        "total_targets": sum(safe_api_int(r.get("target_count"), 0) for r in scanned_rows),
        "total_spacia": sum(safe_api_int(r.get("spacia_count"), 0) for r in scanned_rows),
        "total_inactives": sum(safe_api_int(r.get("inactive_count"), 0) for r in scanned_rows),
        "stale_over_1h_count": stale_1h,
        "total_raids": sum(safe_api_int(r.get("direct_raid_count"), 0) for r in scanned_rows),
        "sweep_pages": snap.sweep_pages,
        "sweep_interval_seconds": snap.sweep_interval,
        "allow_galaxy_scan": snap.allow_scan,
        "explore_first": snap.explore_cfg,
        "scan_all_galaxies": snap.scan_all_cfg,
        "refresh_pct": snap.refresh_cfg,
        "discovery_pct": 100 - snap.refresh_cfg,
        "effective_refresh_pct": eff_refresh,
        "effective_discovery_pct": 100 - eff_refresh,
        "saved_refresh_pct": saved_eff_refresh,
        "saved_sweep_pages": snap.saved_pages,
        "saved_scan_all_galaxies": snap.saved_all,
        "planned_refresh_pages": planned_refresh,
        "planned_discovery_pages": max(0, snap.sweep_pages - planned_refresh),
        "next_batch_refresh_count": q_ref,
        "next_batch_discovery_count": q_disc,
        "queue_count": len(scan_queue),
        "queue_discovery_count": q_disc,
        "queue_refresh_count": q_ref,
        "queue_galaxies": list(queue_gals),
        "scan_phase": str(scope_src.get("scan_phase") or scope_src.get("phase") or "exploratory"),
    }


def _load_sector_intel_locked(
    bridge: BridgeApiFacade,
    *,
    snap: _SectorConfigSnapshot,
    is_preview: bool,
    now_ts: float,
) -> tuple[
    int,
    Mapping[str, object],
    Mapping[str, object],
    list[int],
    list[int],
    list[int],
    bool,
    dict[str, object] | None,
    dict[str, object],
]:
    ctx = bridge.ctx
    state_snap, db_snap = ctx.last_state, ctx.db
    raid_snap = [r for r in ctx.raid_targets if isinstance(r, Mapping)]
    scanned_snap = dict(ctx.scanned_systems)
    max_g = max_galaxies_for_ctx(ctx)
    raw_intel = db_snap.get_galaxy_sector_intel(galaxies=None, now_ts=now_ts, max_galaxies=max_g) if db_snap else None
    intel: Mapping[str, object] = raw_intel if isinstance(raw_intel, Mapping) else {}
    raw_sec = intel.get("sectors")
    sectors_map: Mapping[str, object] = raw_sec if isinstance(raw_sec, Mapping) else {}
    all_gals = list(range(1, max_g + 1))
    owned_gals = owned_sweep_galaxies(ctx, state_snap)
    queue_gals = sweep_target_galaxies(
        ctx,
        refresh_pct_override=snap.refresh_cfg,
        all_galaxies_override=snap.scan_all_cfg,
        state=state_snap,
        scanned_systems=scanned_snap,
    )
    collect_colonies = count_allocated_normal_planets(state_snap) < max(
        1, safe_api_int(ctx.strategy_config.max_planets, 11)
    )
    smart_plan = plan_smart_sweep_locked(
        bridge,
        batch_size=snap.sweep_pages,
        galaxies=queue_gals,
        collect_colonies=collect_colonies,
        now_ts=now_ts,
        sector_intel=intel or None,
        explore_first=snap.explore_cfg,
        refresh_pct=snap.refresh_cfg,
        scanned_systems=scanned_snap,
        state=state_snap,
        raid_targets=raid_snap,
    )
    if smart_plan is not None and not is_preview:
        store_smart_sweep_summary_locked(ctx, smart_plan, selection_mode="smart", now_ts=now_ts)
    sweep_summary = bridge.get_galaxy_sweep_state_locked()
    return max_g, intel, sectors_map, all_gals, owned_gals, queue_gals, collect_colonies, smart_plan, sweep_summary


def build_scanned_sectors_response(
    bridge: BridgeApiFacade,
    *,
    gal_filter: int | None,
    include_unscanned: bool,
    limit: int,
    req_pid: int | None,
    preview_refresh_pct: int | None,
    preview_sweep_pages: int | None,
    preview_all_galaxies: bool | None,
    now_ts: float,
) -> dict[str, object]:
    """Assemble the complete `scanned_sectors` telemetry and smart-queue payload."""
    is_preview = preview_refresh_pct is not None or preview_sweep_pages is not None or preview_all_galaxies is not None
    with bridge.ctx.intel_read_lock:
        ctx = bridge.ctx
        state_snap = ctx.last_state
        raid_snap = [r for r in ctx.raid_targets if isinstance(r, Mapping)]
        scanned_snap = dict(ctx.scanned_systems)
        origin_coords = _resolve_origin_coords(state_snap, req_pid)
        orig_gsp = parse_origin_gsp(origin_coords)
        orig_gs = (orig_gsp[0], orig_gsp[1]) if orig_gsp is not None else None
        snap = _resolve_sector_config(
            bridge,
            preview_refresh_pct=preview_refresh_pct,
            preview_sweep_pages=preview_sweep_pages,
            preview_all_galaxies=preview_all_galaxies,
        )
        max_g, intel, sectors_map, all_gals, owned_gals, queue_gals, collect_colonies, smart_plan, sweep_summary = (
            _load_sector_intel_locked(bridge, snap=snap, is_preview=is_preview, now_ts=now_ts)
        )
        scan_queue, queue_by_sec, queued_by_gal = build_scan_queue(
            smart_plan, sectors_map, all_gals, orig_gs, max_g=max_g, now_ts=now_ts
        )
        galaxy_stats, scanned_by_key, scanned_gals = aggregate_galaxy_stats(
            sectors_map, all_gals, set(owned_gals), set(queue_gals), queued_by_gal, max_g
        )
        score_gals = (
            [gal_filter]
            if (gal_filter and gal_filter > 0)
            else (all_gals if include_unscanned else sorted(scanned_gals | set(queue_gals)))
        )
        score_by_sec = _score_sectors_map(
            bridge,
            score_gals=score_gals,
            state_snap=state_snap,
            origin_coords=origin_coords,
            intel=intel,
            scanned_snap=scanned_snap,
            raid_snap=raid_snap,
            collect_colonies=collect_colonies,
            now_ts=now_ts,
        )
        rows_out = collect_sector_rows(
            gal_filter=gal_filter,
            include_unscanned=include_unscanned,
            limit=limit,
            all_galaxies=all_gals,
            max_g=max_g,
            scanned_by_key=scanned_by_key,
            score_by_sector=score_by_sec,
            queue_by_sector=queue_by_sec,
            orig_gs=orig_gs,
            now_ts=now_ts,
        )
        summary_out = _build_sectors_summary(
            rows_out=rows_out,
            total_scanned_all=len(scanned_by_key),
            intel=intel,
            sweep_summary=sweep_summary,
            smart_plan=smart_plan,
            is_preview=is_preview,
            scan_queue=scan_queue,
            queue_gals=queue_gals,
            max_g=max_g,
            snap=snap,
        )
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "scanned_sectors",
            "preview": is_preview,
            "origin_coords": origin_coords,
            "galaxy_filter": gal_filter or 0,
            "include_unscanned": include_unscanned,
            "galaxies_available": all_gals,
            "galaxies_with_scans": sorted(scanned_gals),
            "galaxy_stats": galaxy_stats,
            "scan_queue": scan_queue,
            "summary": summary_out,
            "sectors": rows_out[:limit],
        }
    )
