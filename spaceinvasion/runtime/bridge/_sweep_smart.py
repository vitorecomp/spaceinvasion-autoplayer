"""Smart 2-phase galaxy sweep target selection, summary cache, and P0 preemption helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
import time

from spaceinvasion.combat.cargo import plan_escape_flight
from spaceinvasion.combat.scanner import select_smart_galaxy_scan_batch
from spaceinvasion.core.model import GameState, count_allocated_normal_planets
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._intel_sectors import (
    call_get_galaxy_sector_intel,
    get_scanned_systems_for_ctx,
    max_galaxies_for_ctx,
    own_sweep_coords_for_ctx,
    sweep_target_galaxies_for_ctx,
)
from spaceinvasion.runtime.logs import append_crash_debug_log

SMART_SWEEP_SUMMARY_TTL_S: float = 60.0
SMART_SWEEP_STATE_KEYS: tuple[str, ...] = (
    "scan_phase",
    "selection_mode",
    "explored_systems",
    "unscanned_systems",
    "total_systems",
    "coverage_pct",
    "universe_explored_systems",
    "universe_total_systems",
    "universe_coverage_pct",
    "refresh_pct",
    "discovery_pct",
    "exploratory_count",
    "refresh_count",
    "scan_galaxies",
    "sample_targets",
)
_GAL_SYS_RE = re.compile(r"(\d{1,2})\s*:\s*(\d{1,3})", re.ASCII)


def has_urgent_p0_escape_flight_for_ctx(ctx: BridgeContext) -> tuple[bool, str]:
    """Return `(True, reason)` when an urgent hostile P0 Escape Flight must preempt sweeps."""
    if not bool(ctx.strategy_config.allow_fleetsave):
        return False, ""
    st = ctx.last_state
    if st is not None and any(bool(fa.hostile) for fa in (st.fleet_alerts or ())):
        active = st.active_planet()
        if active is not None:
            other_coords = next((p.coords for p in st.planets if p.planet_id != active.planet_id), active.coords)
            escape = plan_escape_flight(
                active,
                target_coords=other_coords,
                fuel_reserve_spice=ctx.strategy_config.fleet_save_fuel_reserve_spice,
            )
            if escape.feasible:
                return True, "Preempted galaxy scan for P0 hostile Escape Flight."
    if ctx.db is not None:
        try:
            pending = ctx.db.get_manual_queue(status="pending", limit=50)
        except BRIDGE_RUNTIME_ERRORS:
            pending = []
        for item in pending or ():
            if (
                isinstance(item, Mapping)
                and str(item.get("priority") or "").strip().upper() == "P0"
                and str(item.get("kind") or "").strip().upper() == "ESCAPE_FLIGHT"
            ):
                return True, "Preempted galaxy scan for pinned P0 Escape Flight."
    return False, ""


def contiguous_sweep_targets(
    start_gal: int,
    start_sys: int,
    count: int,
    *,
    rotate_galaxies: Sequence[int] = (),
) -> list[tuple[int, int]]:
    """Generate legacy contiguous cursor walk `(start_sys..400 -> 1..)`, rotating galaxies on wrap."""
    out: list[tuple[int, int]] = []
    g, s = int(start_gal), max(1, min(400, int(start_sys)))
    gals = list(rotate_galaxies)
    for _ in range(max(0, int(count))):
        out.append((g, s))
        if s >= 400:
            s = 1
            if len(gals) > 1:
                idx_g = gals.index(g) if g in gals else 0
                g = gals[(idx_g + 1) % len(gals)]
        else:
            s += 1
    return out


def current_galaxy_cursor_for_ctx(
    ctx: BridgeContext,
    sw_state: Mapping[str, object],
    *,
    galaxies: Sequence[int] = (),
) -> tuple[int, int] | None:
    """Return active `(galaxy, system)` cursor for contiguous arrow-first sweep anchoring."""
    max_g = max_galaxies_for_ctx(ctx)
    gal_set = {max(1, min(max_g, safe_api_int(g, 0))) for g in galaxies if safe_api_int(g, 0) > 0}
    live_coords = ctx.last_galaxy_browser_coords
    if live_coords is not None:
        lg, ls = live_coords
        if 1 <= lg <= max_g and 1 <= ls <= 400 and (not gal_set or lg in gal_set):
            return (lg, ls)
    if safe_api_int(sw_state.get("total_sweeps"), 0) > 0:
        cg = safe_api_int(sw_state.get("cursor_galaxy"), 0)
        cs = safe_api_int(sw_state.get("cursor_system"), 0)
        if 1 <= cg <= max_g and 1 <= cs <= 400 and (not gal_set or cg in gal_set):
            return (cg, cs)
    return None


def _attach_universe_coverage(
    out_plan: dict[str, object],
    intel: Mapping[str, object] | None,
    scanned_sys: Mapping[str, float],
    max_g: int,
) -> None:
    uni_tot = max_g * 400
    if isinstance(intel, Mapping) and intel.get("universe_explored_systems") is not None:
        uni_exp = max(0, safe_api_int(intel.get("universe_explored_systems"), 0))
        uni_tot = max(1, safe_api_int(intel.get("universe_total_systems"), uni_tot))
    else:
        uni_keys: set[tuple[int, int]] = set()
        for sk, ts_v in scanned_sys.items():
            if safe_api_float(ts_v, 0.0) <= 0.0:
                continue
            match = _GAL_SYS_RE.search(str(sk))
            if match:
                gi_u, si_u = int(match.group(1)), int(match.group(2))
                if 1 <= gi_u <= max_g and 1 <= si_u <= 400:
                    uni_keys.add((gi_u, si_u))
        uni_exp = max(len(uni_keys), safe_api_int(out_plan.get("explored_systems"), 0))
    out_plan["universe_explored_systems"] = uni_exp
    out_plan["universe_total_systems"] = uni_tot
    out_plan["universe_coverage_pct"] = round(min(100.0, (uni_exp / uni_tot) * 100.0), 2)


def plan_smart_galaxy_sweep_for_ctx(
    ctx: BridgeContext,
    sw_state: Mapping[str, object],
    *,
    batch_size: int,
    galaxies: Sequence[int],
    collect_colonies: bool,
    now_ts: float,
    sector_intel_override: Mapping[str, object] | None = None,
    explore_first_override: bool | None = None,
    refresh_pct_override: int | None = None,
    scanned_systems_override: Mapping[str, float] | None = None,
    state_override: GameState | None = None,
    raid_targets_override: Sequence[Mapping[str, object]] | None = None,
    current_cursor: tuple[int, int] | None = None,
) -> dict[str, object] | None:
    """Select the next smart sweep batch via DB sector intel + `select_smart_galaxy_scan_batch`."""
    cfg = ctx.strategy_config
    max_g = max_galaxies_for_ctx(ctx)
    st_ref = state_override if state_override is not None else ctx.last_state
    gal_list = sorted({max(1, min(max_g, safe_api_int(x, 1))) for x in galaxies if safe_api_int(x, 0) > 0})
    eff_cursor = (
        current_cursor
        if current_cursor is not None
        else current_galaxy_cursor_for_ctx(ctx, sw_state, galaxies=gal_list)
    )
    intel: Mapping[str, object] | None = sector_intel_override if isinstance(sector_intel_override, Mapping) else None
    if intel is None and ctx.db is not None:
        try:
            intel = call_get_galaxy_sector_intel(ctx.db, galaxies=gal_list or None, now_ts=now_ts, max_galaxies=max_g)
        except BRIDGE_RUNTIME_ERRORS as exc:
            intel = None
            if ctx.log_dir is not None:
                append_crash_debug_log(component="api_server.smart_galaxy_sweep", event="sector_intel_error", error=exc)
    raid_stats = st_ref.raid_stats if st_ref is not None else None
    scanned_sys = scanned_systems_override if scanned_systems_override is not None else get_scanned_systems_for_ctx(ctx)
    raid_src = raid_targets_override if raid_targets_override is not None else (ctx.raid_targets or ())
    eff_explore = (
        bool(explore_first_override) if explore_first_override is not None else bool(cfg.galaxy_scan_explore_first)
    )
    eff_ref_raw = refresh_pct_override if refresh_pct_override is not None else cfg.galaxy_scan_refresh_pct
    eff_refresh_pct = max(0, min(100, safe_api_int(eff_ref_raw, 0)))
    try:
        plan = select_smart_galaxy_scan_batch(
            own_coords=own_sweep_coords_for_ctx(ctx, state_override=st_ref),
            sector_intel=intel,
            galaxies=gal_list or None,
            scanned_systems=scanned_sys,
            raid_targets=[r for r in raid_src if isinstance(r, Mapping)],
            raid_stats=raid_stats if isinstance(raid_stats, Mapping) else None,
            batch_size=max(1, min(400 * max(1, len(gal_list)), int(batch_size))),
            explore_first=eff_explore,
            refresh_pct=eff_refresh_pct,
            near_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_near_weight, 2.0))),
            attack_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_attack_weight, 3.0))),
            collect_colonies=bool(collect_colonies),
            current_cursor=eff_cursor,
            now_ts=now_ts,
        )
    except BRIDGE_RUNTIME_ERRORS as exc:
        if ctx.log_dir is not None:
            append_crash_debug_log(component="api_server.smart_galaxy_sweep", event="select_batch_error", error=exc)
        return None
    if not isinstance(plan, Mapping):
        return None
    out_plan: dict[str, object] = dict(plan)
    _attach_universe_coverage(out_plan, intel, scanned_sys, max_g)
    return out_plan


def store_smart_sweep_summary_for_ctx(
    ctx: BridgeContext,
    plan: Mapping[str, object] | None,
    *,
    selection_mode: str,
    now_ts: float,
    sample_targets: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Store and return normalized smart-sweep telemetry summary on `ctx`."""
    if not isinstance(plan, Mapping):
        return dict(ctx.smart_sweep_summary or {})
    default_uni_tot = max_galaxies_for_ctx(ctx) * 400
    raw_samples = sample_targets if sample_targets is not None else plan.get("sample_targets")
    samples_seq = raw_samples if isinstance(raw_samples, Sequence) and not isinstance(raw_samples, (str, bytes)) else ()
    raw_gals = plan.get("galaxies")
    gals_seq = raw_gals if isinstance(raw_gals, Sequence) and not isinstance(raw_gals, (str, bytes)) else ()
    summary: dict[str, object] = {
        "scan_phase": str(plan.get("scan_phase") or plan.get("phase") or "exploratory"),
        "selection_mode": selection_mode,
        "explored_systems": max(0, safe_api_int(plan.get("explored_systems"), 0)),
        "unscanned_systems": max(0, safe_api_int(plan.get("unscanned_systems"), 0)),
        "total_systems": max(0, safe_api_int(plan.get("total_systems"), 0)),
        "coverage_pct": round(max(0.0, min(100.0, safe_api_float(plan.get("coverage_pct"), 0.0))), 2),
        "universe_explored_systems": max(0, safe_api_int(plan.get("universe_explored_systems"), 0)),
        "universe_total_systems": max(1, safe_api_int(plan.get("universe_total_systems"), default_uni_tot)),
        "universe_coverage_pct": round(max(0.0, min(100.0, safe_api_float(plan.get("universe_coverage_pct"), 0.0))), 2),
        "refresh_pct": round(max(0.0, min(100.0, safe_api_float(plan.get("refresh_pct"), 0.0))), 1),
        "discovery_pct": round(max(0.0, min(100.0, safe_api_float(plan.get("discovery_pct"), 100.0))), 1),
        "exploratory_count": max(0, safe_api_int(plan.get("exploratory_count"), 0)),
        "refresh_count": max(0, safe_api_int(plan.get("refresh_count"), 0)),
        "scan_galaxies": [safe_api_int(g, 1) for g in gals_seq if safe_api_int(g, 0) > 0],
        "sample_targets": [dict(s) for s in list(samples_seq)[:5] if isinstance(s, Mapping)],
        "smart_summary_ts": round(now_ts, 3),
    }
    ctx.smart_sweep_summary = summary
    ctx.smart_sweep_summary_ts = now_ts
    return dict(summary)


def merge_smart_sweep_summary_for_ctx(ctx: BridgeContext, st: dict[str, object]) -> dict[str, object]:
    """Attach cached smart-sweep fields onto a galaxy sweep state dictionary."""
    summary = ctx.smart_sweep_summary or {}
    default_uni_tot = max_galaxies_for_ctx(ctx) * 400
    st.setdefault("scan_phase", summary.get("scan_phase", "exploratory"))
    st.setdefault("selection_mode", summary.get("selection_mode", "smart"))
    st.setdefault("explored_systems", summary.get("explored_systems", 0))
    st.setdefault("unscanned_systems", summary.get("unscanned_systems", 0))
    st.setdefault("total_systems", summary.get("total_systems", 0))
    st.setdefault("coverage_pct", summary.get("coverage_pct", 0.0))
    st.setdefault("universe_explored_systems", summary.get("universe_explored_systems", 0))
    st.setdefault("universe_total_systems", summary.get("universe_total_systems", default_uni_tot))
    st.setdefault("universe_coverage_pct", summary.get("universe_coverage_pct", 0.0))
    st.setdefault("refresh_pct", summary.get("refresh_pct", 0.0))
    st.setdefault("discovery_pct", summary.get("discovery_pct", 100.0))
    st.setdefault("exploratory_count", summary.get("exploratory_count", 0))
    st.setdefault("refresh_count", summary.get("refresh_count", 0))
    raw_gals = summary.get("scan_galaxies")
    gals_list = list(raw_gals) if isinstance(raw_gals, Sequence) and not isinstance(raw_gals, (str, bytes)) else []
    st.setdefault("scan_galaxies", gals_list)
    raw_samples = summary.get("sample_targets")
    samples_list = (
        [dict(s) for s in raw_samples if isinstance(s, Mapping)]
        if isinstance(raw_samples, Sequence) and not isinstance(raw_samples, (str, bytes))
        else []
    )
    st.setdefault("sample_targets", samples_list)
    return st


def refresh_smart_sweep_summary_for_ctx(
    ctx: BridgeContext,
    sw_state: Mapping[str, object],
    *,
    now_ts: float,
    force: bool = False,
) -> None:
    """Refresh `ctx.smart_sweep_summary` when TTL expired or `force=True`."""
    if not force and ctx.smart_sweep_summary and (now_ts - ctx.smart_sweep_summary_ts) < SMART_SWEEP_SUMMARY_TTL_S:
        return
    if ctx.sweep_in_progress and ctx.smart_sweep_summary and not force:
        return
    cfg = ctx.strategy_config
    pages = max(1, min(400, safe_api_int(cfg.galaxy_sweep_pages, 100)))
    allocated_normal = count_allocated_normal_planets(ctx.last_state)
    max_planets_cap = max(1, safe_api_int(cfg.max_planets, 11))
    plan = plan_smart_galaxy_sweep_for_ctx(
        ctx,
        sw_state,
        batch_size=pages,
        galaxies=sweep_target_galaxies_for_ctx(ctx),
        collect_colonies=bool(allocated_normal < max_planets_cap),
        now_ts=now_ts,
    )
    if plan is not None:
        store_smart_sweep_summary_for_ctx(ctx, plan, selection_mode="smart", now_ts=now_ts)
    else:
        ctx.smart_sweep_summary_ts = now_ts


def update_live_smart_summary_for_sector(
    ctx: BridgeContext,
    skey_now: str,
    page_gal: int,
    known_scanned_keys: set[str],
    scan_gal_set: set[int],
) -> None:
    """Increment live in-memory coverage counters when a newly visited sector is recorded."""
    if skey_now in known_scanned_keys:
        return
    known_scanned_keys.add(skey_now)
    sm_live = ctx.smart_sweep_summary
    if isinstance(sm_live, dict) and sm_live:
        uni_tot = max(1, safe_api_int(sm_live.get("universe_total_systems"), max_galaxies_for_ctx(ctx) * 400))
        uni_exp = min(uni_tot, max(0, safe_api_int(sm_live.get("universe_explored_systems"), 0)) + 1)
        sm_live["universe_explored_systems"] = uni_exp
        sm_live["universe_coverage_pct"] = round(min(100.0, (uni_exp / uni_tot) * 100.0), 2)
        if page_gal in scan_gal_set:
            gal_tot = max(1, safe_api_int(sm_live.get("total_systems"), 400))
            gal_exp = min(gal_tot, max(0, safe_api_int(sm_live.get("explored_systems"), 0)) + 1)
            gal_un = max(0, gal_tot - gal_exp)
            sm_live["explored_systems"] = gal_exp
            sm_live["unscanned_systems"] = gal_un
            sm_live["coverage_pct"] = round(min(100.0, (gal_exp / gal_tot) * 100.0), 2)
            if gal_un == 0:
                sm_live["scan_phase"] = "targeted_refresh"
        cur_samples = sm_live.get("sample_targets")
        if isinstance(cur_samples, list) and cur_samples:
            sm_live["sample_targets"] = [
                s_it for s_it in cur_samples if isinstance(s_it, Mapping) and str(s_it.get("sector") or "") != skey_now
            ]
        now_t = time.time()
        sm_live["smart_summary_ts"] = round(now_t, 3)
        ctx.smart_sweep_summary_ts = now_t
    ctx.invalidate_dashboard_cache()
