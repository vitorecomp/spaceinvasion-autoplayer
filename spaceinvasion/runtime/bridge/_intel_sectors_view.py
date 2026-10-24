"""Scanned-sectors summary and top-level `build_scanned_sectors_payload` assembly."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import time

from spaceinvasion.combat.scanner import score_galaxy_scan_sectors, select_smart_galaxy_scan_batch
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState, count_allocated_normal_planets
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._intel_sectors import (
    build_scanned_sectors_summary,
    call_get_galaxy_sector_intel,
    get_scanned_systems_for_ctx,
    max_galaxies_for_ctx,
    own_sweep_coords_for_ctx,
    owned_sweep_galaxies_for_ctx,
    sweep_target_galaxies_for_ctx,
)
from spaceinvasion.runtime.bridge._intel_sectors_rows import (
    aggregate_galaxy_stats,
    build_scan_queue,
    collect_and_sort_sector_rows,
    resolve_origin_sector,
)


def _resolve_smart_plan_for_view(
    ctx: BridgeContext,
    *,
    sweep_pages: int,
    queue_galaxies: Sequence[int],
    collect_colonies: bool,
    now_ts: float,
    intel: Mapping[str, object],
    explore_first: bool,
    refresh_pct: int,
    scanned_snap: Mapping[str, float],
    state_snap: GameState | None,
    raid_targets_snap: Sequence[Mapping[str, object]],
    on_plan_smart_sweep: Callable[..., dict[str, object] | None] | None,
) -> dict[str, object] | None:
    if on_plan_smart_sweep is not None:
        return on_plan_smart_sweep(
            batch_size=sweep_pages,
            galaxies=queue_galaxies,
            collect_colonies=collect_colonies,
            now_ts=now_ts,
            sector_intel_override=intel or None,
            explore_first_override=explore_first,
            refresh_pct_override=refresh_pct,
            scanned_systems_override=scanned_snap,
            state_override=state_snap,
            raid_targets_override=raid_targets_snap,
        )
    cfg = ctx.strategy_config
    raid_stats = state_snap.raid_stats if state_snap is not None else None
    raw = select_smart_galaxy_scan_batch(
        own_coords=own_sweep_coords_for_ctx(ctx, state_override=state_snap),
        sector_intel=intel or None,
        galaxies=queue_galaxies or None,
        scanned_systems=scanned_snap,
        raid_targets=raid_targets_snap,
        raid_stats=raid_stats if isinstance(raid_stats, Mapping) else None,
        batch_size=max(1, min(400 * max(1, len(queue_galaxies)), int(sweep_pages))),
        explore_first=explore_first,
        refresh_pct=refresh_pct,
        near_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_near_weight, 2.0))),
        attack_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_attack_weight, 3.0))),
        collect_colonies=collect_colonies,
        now_ts=now_ts,
    )
    return dict(raw) if isinstance(raw, Mapping) else None


def _score_sectors_for_view(
    ctx: BridgeContext,
    *,
    score_galaxies: Sequence[int],
    state_snap: GameState | None,
    origin_coords: str,
    intel: Mapping[str, object],
    scanned_snap: Mapping[str, float],
    raid_targets_snap: Sequence[Mapping[str, object]],
    collect_colonies: bool,
    now_ts: float,
) -> dict[str, Mapping[str, object]]:
    score_by_sector: dict[str, Mapping[str, object]] = {}
    if not score_galaxies:
        return score_by_sector
    cfg = ctx.strategy_config
    raid_stats = state_snap.raid_stats if state_snap is not None else None
    scored_rows = score_galaxy_scan_sectors(
        own_coords=own_sweep_coords_for_ctx(ctx, state_override=state_snap)
        or ([origin_coords] if origin_coords else ()),
        sector_intel=intel,
        galaxies=score_galaxies,
        scanned_systems=scanned_snap,
        raid_targets=raid_targets_snap,
        raid_stats=raid_stats if isinstance(raid_stats, Mapping) else None,
        explore_first=False,
        near_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_near_weight, 2.0))),
        attack_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_attack_weight, 3.0))),
        collect_colonies=collect_colonies,
        now_ts=now_ts,
    )
    for sc in scored_rows:
        skey_sc = str(sc.get("sector") or "")
        if skey_sc:
            score_by_sector[skey_sc] = sc
    return score_by_sector


def _resolve_sector_preview_settings(
    cfg: StrategyConfig,
    *,
    preview_refresh_pct: int | None,
    preview_sweep_pages: int | None,
    preview_all_galaxies: bool | None,
) -> tuple[int, int, bool, bool, int, int, bool, bool]:
    saved_pages = max(1, min(400, safe_api_int(cfg.galaxy_sweep_pages, 100)))
    saved_ref = max(0, min(100, safe_api_int(cfg.galaxy_scan_refresh_pct, 0)))
    saved_explore = bool(cfg.galaxy_scan_explore_first)
    saved_all = bool(cfg.galaxy_scan_all_galaxies)
    sweep_pages = preview_sweep_pages if preview_sweep_pages is not None else saved_pages
    ref_cfg = preview_refresh_pct if preview_refresh_pct is not None else saved_ref
    exp_cfg = (ref_cfg < 100) if preview_refresh_pct is not None else saved_explore
    all_cfg = preview_all_galaxies if preview_all_galaxies is not None else saved_all
    return saved_pages, saved_ref, saved_explore, saved_all, sweep_pages, ref_cfg, exp_cfg, all_cfg


def _assemble_scanned_sectors_response(
    ctx: BridgeContext,
    *,
    galaxy: int | None,
    limit: int,
    eff_inc: bool,
    is_preview: bool,
    now_ts: float,
    origin_coords: str,
    orig_gs: tuple[int, int] | None,
    max_g: int,
    intel: Mapping[str, object],
    sectors_map: Mapping[str, object],
    smart_plan: Mapping[str, object] | None,
    sw_sum: Mapping[str, object],
    all_gals: list[int],
    owned_gals: list[int],
    queue_gals: list[int],
    collect_col: bool,
    state_snap: GameState | None,
    raid_snap: list[Mapping[str, object]],
    scanned_snap: dict[str, float],
    settings: tuple[int, int, bool, bool, int, int, bool, bool],
) -> dict[str, object]:
    saved_pages, saved_ref, saved_explore, saved_all, sweep_pages, ref_cfg, exp_cfg, all_cfg = settings
    scan_queue, q_by_sec, q_by_gal = build_scan_queue(
        smart_plan, sectors_map, all_gals, max_g=max_g, now_ts=now_ts, orig_gs=orig_gs
    )
    gal_stats, scanned_gals, scanned_by_key = aggregate_galaxy_stats(
        sectors_map, all_gals, set(owned_gals), set(queue_gals), q_by_gal, max_g=max_g
    )
    score_gals = (
        [galaxy] if (galaxy and galaxy > 0) else (all_gals if eff_inc else sorted(scanned_gals | set(queue_gals)))
    )
    score_by_sec = _score_sectors_for_view(
        ctx,
        score_galaxies=score_gals,
        state_snap=state_snap,
        origin_coords=origin_coords,
        intel=intel,
        scanned_snap=scanned_snap,
        raid_targets_snap=raid_snap,
        collect_colonies=collect_col,
        now_ts=now_ts,
    )
    rows_out = collect_and_sort_sector_rows(
        scanned_by_key,
        all_gals,
        gal_filter=galaxy,
        include_unscanned=eff_inc,
        limit=limit,
        max_g=max_g,
        now_ts=now_ts,
        orig_gs=orig_gs,
        score_by_sector=score_by_sec,
        queue_by_sector=q_by_sec,
    )
    summary_out = build_scanned_sectors_summary(
        cfg=ctx.strategy_config,
        rows_out=rows_out,
        scan_queue=scan_queue,
        intel=intel,
        sweep_summary=sw_sum,
        smart_plan=smart_plan,
        queue_galaxies=queue_gals,
        total_scanned_all=len(scanned_by_key),
        max_g=max_g,
        is_preview=is_preview,
        sweep_pages=sweep_pages,
        refresh_pct_cfg=ref_cfg,
        explore_first_cfg=exp_cfg,
        scan_all_gals_cfg=all_cfg,
        saved_sweep_pages=saved_pages,
        saved_refresh_pct=saved_ref,
        saved_explore_first=saved_explore,
        saved_scan_all_gals=saved_all,
    )
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "scanned_sectors",
            "preview": is_preview,
            "origin_coords": origin_coords,
            "galaxy_filter": galaxy or 0,
            "include_unscanned": eff_inc,
            "galaxies_available": all_gals,
            "galaxies_with_scans": sorted(scanned_gals),
            "galaxy_stats": gal_stats,
            "scan_queue": scan_queue,
            "summary": summary_out,
            "sectors": rows_out[: int(limit)],
        }
    )


def build_scanned_sectors_payload(
    ctx: BridgeContext,
    *,
    galaxy: int | None = None,
    limit: int = 200,
    include_unscanned: bool | None = None,
    planet_id: int | None = None,
    preview_refresh_pct: int | None = None,
    preview_sweep_pages: int | None = None,
    preview_all_galaxies: bool | None = None,
    on_plan_smart_sweep: Callable[..., dict[str, object] | None] | None = None,
    on_store_smart_summary: Callable[..., dict[str, object]] | None = None,
    on_get_sweep_state: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build the full `scanned_sectors` command payload under `ctx.intel_read_lock`."""
    eff_inc = bool(galaxy and galaxy > 0) if include_unscanned is None else bool(include_unscanned)
    is_preview = preview_refresh_pct is not None or preview_sweep_pages is not None or preview_all_galaxies is not None
    with ctx.intel_read_lock:
        now_ts = time.time()
        cfg = ctx.strategy_config
        state_snap = ctx.last_state
        raid_snap: list[Mapping[str, object]] = [r for r in (ctx.raid_targets or ()) if isinstance(r, Mapping)]
        scanned_snap = get_scanned_systems_for_ctx(ctx)
        origin_coords, orig_gs = resolve_origin_sector(state_snap, planet_id)
        max_g = max_galaxies_for_ctx(ctx)
        intel = call_get_galaxy_sector_intel(ctx.db, galaxies=None, now_ts=now_ts, max_galaxies=max_g) or {}
        raw_sec = intel.get("sectors")
        sectors_map: Mapping[str, object] = raw_sec if isinstance(raw_sec, Mapping) else {}
        settings = _resolve_sector_preview_settings(
            cfg,
            preview_refresh_pct=preview_refresh_pct,
            preview_sweep_pages=preview_sweep_pages,
            preview_all_galaxies=preview_all_galaxies,
        )
        _, _, _, _, sweep_pages, ref_cfg, exp_cfg, all_cfg = settings
        all_gals = list(range(1, max_g + 1))
        owned_gals = owned_sweep_galaxies_for_ctx(ctx, state_override=state_snap)
        queue_gals = sweep_target_galaxies_for_ctx(
            ctx,
            refresh_pct_override=ref_cfg,
            all_galaxies_override=all_cfg,
            state_override=state_snap,
            scanned_systems_override=scanned_snap,
        )
        collect_col = count_allocated_normal_planets(state_snap) < max(1, safe_api_int(cfg.max_planets, 11))
        smart_plan = _resolve_smart_plan_for_view(
            ctx,
            sweep_pages=sweep_pages,
            queue_galaxies=queue_gals,
            collect_colonies=collect_col,
            now_ts=now_ts,
            intel=intel,
            explore_first=exp_cfg,
            refresh_pct=ref_cfg,
            scanned_snap=scanned_snap,
            state_snap=state_snap,
            raid_targets_snap=raid_snap,
            on_plan_smart_sweep=on_plan_smart_sweep,
        )
        if smart_plan is not None and not is_preview and on_store_smart_summary is not None:
            on_store_smart_summary(smart_plan, selection_mode="smart", now_ts=now_ts)
        sw_sum = (
            on_get_sweep_state(now_ts=now_ts, include_smart=False)
            if on_get_sweep_state is not None
            else dict(ctx.smart_sweep_summary)
        )
        return _assemble_scanned_sectors_response(
            ctx,
            galaxy=galaxy,
            limit=limit,
            eff_inc=eff_inc,
            is_preview=is_preview,
            now_ts=now_ts,
            origin_coords=origin_coords,
            orig_gs=orig_gs,
            max_g=max_g,
            intel=intel,
            sectors_map=sectors_map,
            smart_plan=smart_plan,
            sw_sum=sw_sum,
            all_gals=all_gals,
            owned_gals=owned_gals,
            queue_gals=queue_gals,
            collect_col=collect_col,
            state_snap=state_snap,
            raid_snap=raid_snap,
            scanned_snap=scanned_snap,
            settings=settings,
        )
