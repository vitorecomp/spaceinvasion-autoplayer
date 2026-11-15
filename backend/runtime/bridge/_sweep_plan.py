"""Galaxy sweep batch planning, initial cursor resolution, and dry-run payload builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import random
import re

from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._helpers import safe_api_float, safe_api_int, sanitize_json_dict
from backend.runtime.bridge._intel_sectors import (
    get_scanned_systems_for_ctx,
    max_galaxies_for_ctx,
    owned_sweep_galaxies_for_ctx,
    sweep_target_galaxies_for_ctx,
)
from backend.runtime.bridge._sweep_smart import (
    contiguous_sweep_targets,
    plan_smart_galaxy_sweep_for_ctx,
    store_smart_sweep_summary_for_ctx,
)

_GAL_SYS_RE = re.compile(r"(\d{1,2}):(\d{1,3})", re.ASCII)


@dataclass
class SweepBatchPlan:
    """Prepared batch of target sectors and telemetry for one `run_galaxy_sweep` invocation."""

    sweep_targets: list[tuple[int, int]]
    contiguous_tail: tuple[int, int] | None
    selection_mode: str
    scan_phase: str
    smart_plan: dict[str, object] | None
    smart_summary: dict[str, object]
    known_scanned_keys: set[str]
    scan_gal_set: set[int]
    collect_colonies: bool
    allocated_normal: int
    max_planets_cap: int
    cfg_delay_s: float
    cfg_jitter_s: float
    cur_gal: int
    cur_sys: int


@dataclass
class SweepRunCounters:
    """Mutable progress counters accumulated across a live galaxy sweep."""

    pages_scanned: int = 0
    players_found: int = 0
    colonies_found: int = 0
    debris_found: int = 0
    arrow_steps_used: int = 0
    prev_browser_coords: tuple[int, int] | None = None


def resolve_initial_cursor(
    ctx: BridgeContext,
    sw_state: Mapping[str, object],
    owned_gals: Sequence[int],
    *,
    galaxy: int | None,
    start_system: int | None,
    total_pages: int,
    max_g: int,
) -> tuple[int, int]:
    """Resolve initial `(galaxy, system)` cursor before smart or contiguous batch planning."""
    cur_gal = max(
        1,
        min(
            max_g,
            int(galaxy) if galaxy is not None else safe_api_int(sw_state.get("cursor_galaxy"), owned_gals[0]),
        ),
    )
    if galaxy is None and cur_gal not in owned_gals:
        cur_gal = owned_gals[0]
    if start_system is not None:
        return cur_gal, max(1, min(400, int(start_system)))
    if safe_api_int(sw_state.get("total_sweeps"), 0) == 0 and ctx.last_state is not None:
        ap = ctx.last_state.active_planet() or (ctx.last_state.planets[0] if ctx.last_state.planets else None)
        match = _GAL_SYS_RE.search(str(ap.coords if ap is not None else ""))
        if match:
            if galaxy is None:
                cur_gal = int(match.group(1))
            own_sys = int(match.group(2))
            jitter = random.randint(-3, 3)
            return cur_gal, ((own_sys - 1 - (total_pages // 2) + jitter) % 400) + 1
    return cur_gal, max(1, min(400, safe_api_int(sw_state.get("cursor_system"), 1)))


def _select_smart_targets(
    ctx: BridgeContext,
    sw_state: Mapping[str, object],
    *,
    total_pages: int,
    galaxy: int | None,
    collect_colonies: bool,
    sweep_started_ts: float,
    max_g: int,
) -> tuple[dict[str, object] | None, list[tuple[int, int]]]:
    smart_plan = plan_smart_galaxy_sweep_for_ctx(
        ctx,
        sw_state,
        batch_size=total_pages,
        galaxies=sweep_target_galaxies_for_ctx(ctx, explicit_galaxy=galaxy),
        collect_colonies=collect_colonies,
        now_ts=sweep_started_ts,
    )
    seen_tg: set[tuple[int, int]] = set()
    targets: list[tuple[int, int]] = []
    raw_sel = smart_plan.get("selected_sectors") if isinstance(smart_plan, Mapping) else None
    selected = raw_sel if isinstance(raw_sel, Sequence) and not isinstance(raw_sel, (str, bytes)) else ()
    for sec in selected:
        if not isinstance(sec, Mapping):
            continue
        tg = (safe_api_int(sec.get("galaxy"), 0), safe_api_int(sec.get("system"), 0))
        if 1 <= tg[0] <= max_g and 1 <= tg[1] <= 400 and tg not in seen_tg:
            seen_tg.add(tg)
            targets.append(tg)
    return smart_plan, targets[:total_pages]


def _build_contiguous_fallback(
    ctx: BridgeContext,
    sw_state: Mapping[str, object],
    *,
    cur_gal: int,
    cur_sys: int,
    total_pages: int,
    galaxy: int | None,
    owned_gals: Sequence[int],
    collect_colonies: bool,
    sweep_started_ts: float,
    max_g: int,
    smart_plan: dict[str, object] | None,
) -> tuple[list[tuple[int, int]], tuple[int, int] | None, dict[str, object] | None, dict[str, object]]:
    rotate_gals = list(owned_gals) if galaxy is None else []
    full_seq = contiguous_sweep_targets(cur_gal, cur_sys, total_pages + 1, rotate_galaxies=rotate_gals)
    targets = full_seq[:total_pages]
    tail = full_seq[total_pages] if len(full_seq) > total_pages else None
    if smart_plan is None:
        smart_plan = plan_smart_galaxy_sweep_for_ctx(
            ctx,
            sw_state,
            batch_size=1,
            galaxies=[max(1, min(max_g, int(cur_gal)))] if galaxy is not None else list(owned_gals),
            collect_colonies=collect_colonies,
            now_ts=sweep_started_ts,
        )
    samples: list[dict[str, object]] = [
        {
            "sector": f"{tg:02d}:{ts_c:03d}",
            "galaxy": tg,
            "system": ts_c,
            "phase": "contiguous",
            "score": 0.0,
            "distance": 0,
            "direct_raid_count": 0,
            "owner_affinity_raid_count": 0,
            "reason": "explicit start_system contiguous walk",
        }
        for tg, ts_c in targets[:5]
    ]
    summary = store_smart_sweep_summary_for_ctx(
        ctx, smart_plan, selection_mode="contiguous", now_ts=sweep_started_ts, sample_targets=samples
    )
    return targets, tail, smart_plan, summary


def prepare_sweep_batch_locked(
    ctx: BridgeContext,
    sw_state: Mapping[str, object],
    *,
    pages: int | None,
    galaxy: int | None,
    start_system: int | None,
    sweep_started_ts: float,
    allocated_normal: int,
) -> SweepBatchPlan:
    """Compute smart or contiguous sweep target coordinates and initial telemetry."""
    cfg = ctx.strategy_config
    total_pages = max(1, min(400, int(pages if pages is not None else safe_api_int(cfg.galaxy_sweep_pages, 100))))
    max_g = max_galaxies_for_ctx(ctx)
    owned_gals = owned_sweep_galaxies_for_ctx(ctx)
    cur_gal, cur_sys = resolve_initial_cursor(
        ctx, sw_state, owned_gals, galaxy=galaxy, start_system=start_system, total_pages=total_pages, max_g=max_g
    )
    max_planets_cap = max(1, safe_api_int(cfg.max_planets, 11))
    collect_colonies = bool(allocated_normal < max_planets_cap)
    cfg_delay_s = round(max(0.2, min(60.0, safe_api_float(cfg.galaxy_scan_click_delay_seconds, 1.5))), 3)
    cfg_jitter_s = round(max(0.0, min(30.0, safe_api_float(cfg.galaxy_scan_click_jitter_seconds, 0.6))), 3)

    smart_plan: dict[str, object] | None = None
    sweep_targets: list[tuple[int, int]] = []
    contiguous_tail: tuple[int, int] | None = None
    if start_system is None:
        smart_plan, sweep_targets = _select_smart_targets(
            ctx,
            sw_state,
            total_pages=total_pages,
            galaxy=galaxy,
            collect_colonies=collect_colonies,
            sweep_started_ts=sweep_started_ts,
            max_g=max_g,
        )
    selection_mode = "smart" if sweep_targets else "contiguous"
    if not sweep_targets:
        sweep_targets, contiguous_tail, smart_plan, smart_summary = _build_contiguous_fallback(
            ctx,
            sw_state,
            cur_gal=cur_gal,
            cur_sys=cur_sys,
            total_pages=total_pages,
            galaxy=galaxy,
            owned_gals=owned_gals,
            collect_colonies=collect_colonies,
            sweep_started_ts=sweep_started_ts,
            max_g=max_g,
            smart_plan=smart_plan,
        )
    else:
        smart_summary = store_smart_sweep_summary_for_ctx(
            ctx, smart_plan, selection_mode="smart", now_ts=sweep_started_ts
        )

    cur_gal, cur_sys = sweep_targets[0]
    known_keys = {str(k) for k, ts_v in get_scanned_systems_for_ctx(ctx).items() if safe_api_float(ts_v, 0.0) > 0.0}
    raw_sg = smart_summary.get("scan_galaxies")
    sg_seq = raw_sg if isinstance(raw_sg, Sequence) and not isinstance(raw_sg, (str, bytes)) else owned_gals
    scan_gal_set = {safe_api_int(g, 0) for g in sg_seq if safe_api_int(g, 0) > 0}
    return SweepBatchPlan(
        sweep_targets=sweep_targets,
        contiguous_tail=contiguous_tail,
        selection_mode=selection_mode,
        scan_phase=str(smart_summary.get("scan_phase") or "exploratory"),
        smart_plan=smart_plan,
        smart_summary=smart_summary,
        known_scanned_keys=known_keys,
        scan_gal_set=scan_gal_set,
        collect_colonies=collect_colonies,
        allocated_normal=allocated_normal,
        max_planets_cap=max_planets_cap,
        cfg_delay_s=cfg_delay_s,
        cfg_jitter_s=cfg_jitter_s,
        cur_gal=cur_gal,
        cur_sys=cur_sys,
    )


def build_dry_run_sweep_payload(batch: SweepBatchPlan, dry_state: dict[str, object]) -> dict[str, object]:
    """Build exact dry-run response dictionary for `galaxy_sweep`."""
    end_gal, end_sys = batch.contiguous_tail if batch.contiguous_tail is not None else batch.sweep_targets[-1]
    planned_pages = len(batch.sweep_targets)
    plan_map = batch.smart_plan or {}
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "galaxy_sweep",
            "executed": False,
            "dry_run": True,
            "pages_planned": planned_pages,
            "start_galaxy": batch.cur_gal,
            "start_system": batch.cur_sys,
            "end_galaxy": end_gal,
            "end_system": end_sys,
            "selection_mode": batch.selection_mode,
            "scan_phase": batch.scan_phase,
            "explored_systems": batch.smart_summary.get("explored_systems", 0),
            "unscanned_systems": batch.smart_summary.get("unscanned_systems", 0),
            "total_systems": batch.smart_summary.get("total_systems", 0),
            "coverage_pct": batch.smart_summary.get("coverage_pct", 0.0),
            "exploratory_count": safe_api_int(plan_map.get("exploratory_count"), 0)
            if batch.selection_mode == "smart"
            else 0,
            "refresh_count": safe_api_int(plan_map.get("refresh_count"), 0) if batch.selection_mode == "smart" else 0,
            "sample_targets": batch.smart_summary.get("sample_targets", []),
            "planned_targets": [f"{g:02d}:{s:03d}" for g, s in batch.sweep_targets[:400]],
            "collect_colonies": batch.collect_colonies,
            "allocated_normal_planets": batch.allocated_normal,
            "max_planets": batch.max_planets_cap,
            "click_delay_seconds": batch.cfg_delay_s,
            "click_jitter_seconds": batch.cfg_jitter_s,
            "galaxy_sweep": dry_state,
            "message": (
                f"[DRY-RUN] Would sweep {planned_pages} galaxy pages "
                f"({batch.selection_mode} {batch.scan_phase}, coverage {batch.smart_summary.get('coverage_pct', 0.0)}%) "
                f"starting at [{batch.cur_gal:02d}:{batch.cur_sys:03d}] "
                f"(colonies={'ON' if batch.collect_colonies else 'FULL'})."
            ),
        }
    )
