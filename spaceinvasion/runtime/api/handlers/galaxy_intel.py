"""Non-browser espionage, scanned sectors, combat/recycle intel, and galaxy sweep `@command` handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import time

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.runtime.api.handlers._bridge_proto import BridgeApiFacade
from spaceinvasion.runtime.api.handlers._galaxy_intel_helpers import (
    build_recycler_summary,
    calc_espionage_distance,
    compute_spice_eq,
    parse_origin_gsp,
    summarize_attack_results,
)
from spaceinvasion.runtime.api.handlers._galaxy_intel_params import (
    AttackResultsParams,
    EspionageIntelParams,
    GalaxySweepParams,
    RecyclerResultsParams,
    ScannedSectorsParams,
    UniverseResearchMaxParams,
)
from spaceinvasion.runtime.api.handlers._galaxy_intel_sectors import build_scanned_sectors_response
from spaceinvasion.runtime.api.handlers._galaxy_intel_sweep import (
    run_dry_galaxy_sweep,
    try_sync_live_research_records,
)
from spaceinvasion.runtime.api.registry import CommandRegistry
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import (
    GalaxyScanInProgressError,
    safe_api_float,
    safe_api_int,
    sanitize_json_dict,
)


def _handle_espionage_intel(bridge: BridgeApiFacade, params: EspionageIntelParams) -> dict[str, object]:
    """Return enriched spy targets, espionage reports, origin distances, and summary counts."""
    coords_str = params.coords.strip() if isinstance(params.coords, str) and params.coords.strip() else None
    with bridge.ctx.intel_read_lock:
        db, state_snap, cfg_snap = bridge.ctx.db, bridge.ctx.last_state, bridge.ctx.strategy_config
        origin_coords = ""
        if state_snap is not None:
            spy_pid = safe_api_int(cfg_snap.spy_dispatch_planet_id, 0)
            orig_p = next((p for p in state_snap.planets if p.planet_id == spy_pid), None) if spy_pid > 0 else None
            orig_p = orig_p or state_snap.active_planet() or (state_snap.planets[0] if state_snap.planets else None)
            if orig_p is not None and orig_p.coords:
                origin_coords = normalize_coords(orig_p.coords)
        orig_gsp = parse_origin_gsp(origin_coords)
        raw_targets = (
            bridge.state_cache.enrich_raid_targets(db.get_spy_targets(limit=params.limit, include_own=False))
            if db is not None
            else list(bridge.ctx.raid_targets)
        )
        targets = [
            {
                **dict(row),
                "spice_eq": compute_spice_eq(row.get("resources")),
                **calc_espionage_distance(origin_coords, orig_gsp, row.get("coords") or row.get("target_coords")),
            }
            for row in raw_targets
        ]
        raw_reports = db.get_spy_reports(coords=coords_str, limit=params.limit) if db is not None else []
        reports = [
            {
                **dict(rep),
                "spice_eq": compute_spice_eq(rep.get("resources")),
                **calc_espionage_distance(origin_coords, orig_gsp, rep.get("coords") or rep.get("target_coords")),
            }
            for rep in raw_reports
        ]
    summary = {
        "total_targets": len(targets),
        "spied_targets": sum(
            1 for t in targets if bool(t.get("has_spy_intel")) or safe_api_float(t.get("last_spied_ts", 0.0)) > 0.0
        ),
        "total_reports": len(reports),
        "spacia_count": sum(1 for t in targets if bool(t.get("is_spacia"))),
        "inactive_count": sum(1 for t in targets if bool(t.get("is_inactive"))),
        "debris_count": sum(1 for t in targets if bool(t.get("has_debris"))),
    }
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "espionage_intel",
            "origin_coords": origin_coords,
            "summary": summary,
            "targets": targets,
            "reports": reports,
        }
    )


def _handle_scanned_sectors(bridge: BridgeApiFacade, params: ScannedSectorsParams) -> dict[str, object]:
    """Return longitudinal scanned sector coverage, per-galaxy stats, and smart sweep queue."""
    if params.include_unscanned is None:
        include_unscanned = bool(params.galaxy and params.galaxy > 0)
    elif isinstance(params.include_unscanned, bool):
        include_unscanned = params.include_unscanned
    else:
        raise ApiRequestError("'scanned_sectors' field 'include_unscanned' must be a boolean.")
    if params.preview_all_galaxies is None:
        preview_all: bool | None = None
    elif isinstance(params.preview_all_galaxies, bool):
        preview_all = params.preview_all_galaxies
    else:
        raise ApiRequestError("'scanned_sectors' field 'preview_all_galaxies' must be a boolean.")
    return build_scanned_sectors_response(
        bridge,
        gal_filter=params.galaxy,
        include_unscanned=include_unscanned,
        limit=params.limit,
        req_pid=params.planet_id,
        preview_refresh_pct=params.preview_refresh_pct,
        preview_sweep_pages=params.preview_sweep_pages,
        preview_all_galaxies=preview_all,
        now_ts=time.time(),
    )


def _handle_universe_research_max(bridge: BridgeApiFacade, params: UniverseResearchMaxParams) -> dict[str, object]:
    """Return universe-wide max observed research levels merged with own levels."""
    do_sync = bool(params.sync)
    if do_sync and not params.dry_run and bridge.is_galaxy_scan_blocking_locked():
        raise GalaxyScanInProgressError(bridge.galaxy_scan_busy_error_message())
    synced_live = False
    parsed_srv: dict[str, dict[str, object]] = {}
    if do_sync and not params.dry_run:
        synced_live, parsed_srv = try_sync_live_research_records(bridge)
    with bridge.ctx.intel_read_lock:
        eff_st, db = bridge.ctx.last_state, bridge.ctx.db
        own_res = eff_st.researches if eff_st is not None else None
        own_nick = eff_st.nickname if eff_st is not None else ""
        if db is not None:
            if do_sync or parsed_srv:
                raw_rows = db.refresh_universe_research_max(
                    own_researches=own_res,
                    own_nick=own_nick,
                    server_records=parsed_srv or None,
                    source="manual_sync" if do_sync else "api",
                )
            else:
                raw_rows = db.get_universe_research_max(own_researches=own_res, own_nick=own_nick)
            rows = [dict(r) for r in raw_rows]
        else:
            rows = bridge.state_cache.get_universe_research_max_locked(eff_st)
        if do_sync:
            bridge.ctx.invalidate_dashboard_cache()
    if do_sync:
        bridge.publish_snapshot()
    msg = (
        f"Synced {len(rows)} research records ({len(parsed_srv)} server records, spy intel + own state merged)."
        if do_sync
        else f"Loaded {len(rows)} universe research records."
    )
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "universe_research_max",
            "synced": synced_live or do_sync,
            "live_pages_checked": synced_live,
            "server_records_found": len(parsed_srv),
            "rows": rows,
            "universe_research_max": rows,
            "message": msg,
        }
    )


def _handle_attack_results(bridge: BridgeApiFacade, params: AttackResultsParams) -> dict[str, object]:
    """Return battle reports, 24h raid telemetry, and active bash/target cooldown blocks."""
    coords_str = params.coords.strip() if isinstance(params.coords, str) and params.coords.strip() else None
    now_ts = time.time()
    with bridge.ctx.intel_read_lock:
        db = bridge.ctx.db
        battles = [dict(b) for b in db.get_battle_reports(coords=coords_str, limit=params.limit)] if db else []
        raid_stats: dict[str, object] = dict(db.get_raid_stats()) if db else {}
        hist_24h = raid_stats.get("history")
        if isinstance(hist_24h, Sequence) and not isinstance(hist_24h, (str, bytes)):
            raid_stats["total_24h"] = sum(
                1
                for h in hist_24h
                if isinstance(h, Mapping) and safe_api_float(h.get("attacked_ts", 0.0)) >= now_ts - 86400.0
            )
        else:
            raid_stats["total_24h"] = 0
        raw_targets = (
            bridge.state_cache.enrich_raid_targets(db.get_spy_targets(limit=300, include_own=False))
            if db is not None
            else list(bridge.ctx.raid_targets)
        )
    summary, blocked = summarize_attack_results(battles, raw_targets, now_ts)
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "attack_results",
            "summary": summary,
            "raid_stats": raid_stats,
            "battles": battles,
            "blocked_targets": blocked,
        }
    )


def _handle_recycler_results(bridge: BridgeApiFacade, params: RecyclerResultsParams) -> dict[str, object]:
    """Return recycle reports, active debris targets, and non-combat inbox messages."""
    cat_str = (
        params.category.strip()
        if isinstance(params.category, str) and params.category.strip() and params.category.strip().lower() != "all"
        else None
    )
    with bridge.ctx.intel_read_lock:
        db, state_snap = bridge.ctx.db, bridge.ctx.last_state
        recycles = [dict(r) for r in db.get_recycle_reports(limit=params.limit)] if db is not None else []
        own_coords = [str(p.coords or "") for p in (state_snap.planets if state_snap is not None else ()) if p.coords]
        raw_debris = (
            bridge.state_cache.enrich_raid_targets(
                db.get_active_debris_targets(origin_coords=own_coords, limit=200, include_own=False)
            )
            if db is not None
            else bridge.ctx.raid_targets
        )
        debris_targets = [dict(t) for t in raw_debris if bool(t.get("has_debris"))]
        other_emails = (
            [
                dict(m)
                for m in db.get_game_messages(
                    category=cat_str,
                    exclude_categories=("spy_report", "battle_report", "recycle_report") if not cat_str else (),
                    limit=params.limit,
                )
            ]
            if db is not None
            else []
        )
        raw_cats = (db.summary() if db is not None else {}).get("message_categories")
        cat_counts: dict[str, int] = (
            {str(k): safe_api_int(v, 0) for k, v in raw_cats.items()} if isinstance(raw_cats, Mapping) else {}
        )
        if len(recycles) < params.limit:
            cat_counts["recycle_report"] = len(recycles)
        cat_counts["general"] = sum(
            safe_api_int(cat_counts.get(k, 0)) for k in ("general", "player", "system", "other")
        )
        recyclers_owned = sum(
            max(0, safe_api_int(p.ships.get("s11", 0))) for p in (state_snap.planets if state_snap else ())
        )
    summary = build_recycler_summary(recycles, debris_targets, other_emails, recyclers_owned, cat_counts)
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "recycler_results",
            "summary": summary,
            "recycles": recycles,
            "debris_targets": debris_targets,
            "other_emails": other_emails,
        }
    )


def _handle_galaxy_sweep(bridge: BridgeApiFacade, params: GalaxySweepParams) -> dict[str, object]:
    """Start, stop, reset, inspect, or dry-run the background smart galaxy sweep."""
    action = (params.action or "").strip().lower()
    if action and action not in ("start", "stop", "reset", "status"):
        raise ApiRequestError(
            "Command parameter 'action' must be one of: 'start', 'stop', 'reset', 'status'.", field="action"
        )
    eff_gal = params.start_galaxy if params.start_galaxy is not None else params.galaxy
    eff_sys = params.start_system if params.start_system is not None else params.system
    if action == "status":
        with bridge.ctx.state_lock:
            state = bridge.get_galaxy_sweep_state_locked()
        return sanitize_json_dict({"ok": True, "command": "galaxy_sweep", "galaxy_sweep": state})
    if action == "stop":
        res = bridge.stop_galaxy_sweep_now()
        bridge.publish_snapshot()
        return sanitize_json_dict({"ok": True, "command": "galaxy_sweep", **res})
    if action == "reset":
        with bridge.ctx.state_lock:
            state = bridge.reset_galaxy_sweep_cursor_locked(galaxy=eff_gal or 1, system=eff_sys or 1)
        bridge.publish_snapshot()
        return sanitize_json_dict({"ok": True, "command": "galaxy_sweep", "galaxy_sweep": state})
    if action == "start":
        if params.dry_run:
            return run_dry_galaxy_sweep(
                bridge, pages=params.pages, galaxy=eff_gal, start_system=eff_sys, now_ts=time.time()
            )
        started = bridge.start_galaxy_sweep_background(
            manual=True, pages=params.pages, start_galaxy=eff_gal, start_system=eff_sys
        )
        bridge.publish_snapshot()
        return sanitize_json_dict({"ok": True, "command": "galaxy_sweep", **started})
    delay_range: tuple[float, float] | None = None
    if params.delay_min_ms is not None or params.delay_max_ms is not None:
        lo_ms = max(0, params.delay_min_ms if params.delay_min_ms is not None else 0)
        hi_ms = max(lo_ms, params.delay_max_ms if params.delay_max_ms is not None else lo_ms)
        delay_range = (lo_ms / 1000.0, hi_ms / 1000.0)
    return sanitize_json_dict(
        bridge.run_galaxy_sweep(
            pages=params.pages,
            galaxy=eff_gal,
            start_system=eff_sys,
            delay_range=delay_range,
            dry_run=bool(params.dry_run),
            priority="interactive",
            manual=True,
        )
    )


def register_galaxy_intel_handlers(registry: CommandRegistry[BridgeApiFacade]) -> None:
    """Register all 6 non-browser galaxy intel and background sweep commands."""
    registry.command(
        "espionage_intel",
        needs_browser=False,
        mutates=False,
        summary="List enriched espionage targets and reports.",
        params=EspionageIntelParams,
    )(_handle_espionage_intel)
    registry.command(
        "scanned_sectors",
        needs_browser=False,
        mutates=False,
        summary="List scanned sectors, galaxy stats, and smart sweep queue.",
        params=ScannedSectorsParams,
    )(_handle_scanned_sectors)
    registry.command(
        "universe_research_max",
        needs_browser=False,
        mutates=False,
        summary="Return universe-wide max research levels.",
        params=UniverseResearchMaxParams,
    )(_handle_universe_research_max)
    registry.command(
        "attack_results",
        needs_browser=False,
        mutates=False,
        summary="Return battle reports, raid stats, and active target blocks.",
        params=AttackResultsParams,
    )(_handle_attack_results)
    registry.command(
        "recycler_results",
        needs_browser=False,
        mutates=False,
        summary="Return recycle reports, active debris targets, and inbox messages.",
        params=RecyclerResultsParams,
    )(_handle_recycler_results)
    registry.command(
        "galaxy_sweep",
        needs_browser=False,
        mutates=True,
        summary="Start, stop, reset, inspect, or dry-run the background galaxy sweep.",
        params=GalaxySweepParams,
    )(_handle_galaxy_sweep)
