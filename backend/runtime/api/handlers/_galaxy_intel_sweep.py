"""Dry-run galaxy sweep preview and live research-record sync helpers for `galaxy_intel`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import random

from backend.core.model import count_allocated_normal_planets
from backend.parsing.account_pages import parse_universe_research_records
from backend.runtime.api.handlers._bridge_proto import BridgeApiFacade
from backend.runtime.api.handlers._galaxy_intel_helpers import (
    GS_SEARCH_RE,
    max_galaxies_for_ctx,
    owned_sweep_galaxies,
    plan_smart_sweep_locked,
    store_smart_sweep_summary_locked,
    sweep_target_galaxies,
)
from backend.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    safe_api_float,
    safe_api_int,
    sanitize_json_dict,
)
from backend.runtime.bridge._helpers_sig import call_with_optional_priority
from backend.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from backend.session.client import RateLimitError

_LOGGER = logging.getLogger(__name__)
_SYNC_RESEARCH_ERRORS: tuple[type[Exception], ...] = (SpaceInvasionAuthError, *BRIDGE_RUNTIME_ERRORS)


def try_sync_live_research_records(bridge: BridgeApiFacade) -> tuple[bool, dict[str, dict[str, object]]]:
    """Fetch live server research records when `sync=True` and browser access is available."""
    ctx = bridge.ctx
    synced_live = False
    parsed_srv: dict[str, dict[str, object]] = {}
    if not ctx.is_browser_port_open() or ctx.bot_control.get("captcha_blocked") or ctx.rate_limit_remaining() > 0.0:
        return synced_live, parsed_srv
    try:
        with ctx.client_factory(ctx.debug_port) as client:
            call_with_optional_priority(client.ensure_in_game, priority="interactive")
            for act_name in ("internalStatistics", "internalToplist", "internalUniverse"):
                if ctx.rate_limit_remaining() > 0.0:
                    break
                try:
                    html_rec = call_with_optional_priority(client.navigate_menu, act_name, priority="interactive")
                except (RateLimitError, AntiBotChallengeError, SpaceInvasionAuthError):
                    raise
                except BRIDGE_RUNTIME_ERRORS:
                    _LOGGER.debug("Skipping research sync page %s after navigation error", act_name, exc_info=True)
                    html_rec = ""
                if isinstance(html_rec, str) and html_rec:
                    synced_live = True
                    found_recs = parse_universe_research_records(html_rec)
                    if found_recs:
                        for rk, rv in found_recs.items():
                            parsed_srv[rk] = dict(rv)
                        break
    except RateLimitError as rl_exc:
        ctx.record_server_rate_limit(rl_exc)
    except AntiBotChallengeError as ab_exc:
        bridge.lifecycle.record_antibot_pause(ab_exc)
        bridge.publish_snapshot()
    except _SYNC_RESEARCH_ERRORS:
        _LOGGER.warning("Live universe_research_max sync fallback failed", exc_info=True)
    return synced_live, parsed_srv


def _contiguous_targets(
    start_gal: int,
    start_sys: int,
    count: int,
    rotate_galaxies: Sequence[int],
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    gal, sys_i = int(start_gal), max(1, min(400, int(start_sys)))
    gals = list(rotate_galaxies)
    for _ in range(max(0, int(count))):
        out.append((gal, sys_i))
        if sys_i >= 400:
            sys_i = 1
            if len(gals) > 1:
                idx = gals.index(gal) if gal in gals else 0
                gal = gals[(idx + 1) % len(gals)]
        else:
            sys_i += 1
    return out


def _resolve_initial_cursor(
    bridge: BridgeApiFacade,
    sw_state: Mapping[str, object],
    owned_gals: Sequence[int],
    *,
    galaxy: int | None,
    start_system: int | None,
    total_pages: int,
    max_g: int,
) -> tuple[int, int]:
    default_gal = safe_api_int(sw_state.get("cursor_galaxy"), owned_gals[0])
    cur_gal = max(1, min(max_g, int(galaxy) if galaxy is not None else default_gal))
    if galaxy is None and cur_gal not in owned_gals:
        cur_gal = owned_gals[0]
    if start_system is not None:
        return cur_gal, max(1, min(400, int(start_system)))
    if safe_api_int(sw_state.get("total_sweeps"), 0) == 0 and bridge.ctx.last_state is not None:
        st = bridge.ctx.last_state
        ap = st.active_planet() or (st.planets[0] if st.planets else None)
        match = GS_SEARCH_RE.search(str(ap.coords if ap is not None else ""))
        if match:
            eff_gal = int(match.group(1)) if galaxy is None else cur_gal
            eff_sys = ((int(match.group(2)) - 1 - (total_pages // 2) + random.randint(-3, 3)) % 400) + 1
            return eff_gal, eff_sys
        return cur_gal, safe_api_int(sw_state.get("cursor_system"), 1)
    return cur_gal, max(1, min(400, safe_api_int(sw_state.get("cursor_system"), 1)))


def _select_dry_targets_locked(
    bridge: BridgeApiFacade,
    *,
    cur_gal: int,
    cur_sys: int,
    total_pages: int,
    galaxy: int | None,
    start_system: int | None,
    owned_gals: Sequence[int],
    collect_colonies: bool,
    max_g: int,
    sweep_started_ts: float,
) -> tuple[list[tuple[int, int]], tuple[int, int] | None, str, dict[str, object] | None, dict[str, object]]:
    ctx = bridge.ctx
    smart_plan: dict[str, object] | None = None
    sweep_targets: list[tuple[int, int]] = []
    contiguous_tail: tuple[int, int] | None = None
    if start_system is None:
        smart_plan = plan_smart_sweep_locked(
            bridge,
            batch_size=total_pages,
            galaxies=sweep_target_galaxies(ctx, explicit_galaxy=galaxy, state=ctx.last_state),
            collect_colonies=collect_colonies,
            now_ts=sweep_started_ts,
        )
        seen_tg: set[tuple[int, int]] = set()
        raw_sel = (smart_plan or {}).get("selected_sectors")
        for sec in raw_sel if isinstance(raw_sel, Sequence) and not isinstance(raw_sel, (str, bytes)) else ():
            if not isinstance(sec, Mapping):
                continue
            tg = (safe_api_int(sec.get("galaxy"), 0), safe_api_int(sec.get("system"), 0))
            if 1 <= tg[0] <= max_g and 1 <= tg[1] <= 400 and tg not in seen_tg:
                seen_tg.add(tg)
                sweep_targets.append(tg)
        sweep_targets = sweep_targets[:total_pages]
    if sweep_targets:
        summary = store_smart_sweep_summary_locked(ctx, smart_plan, selection_mode="smart", now_ts=sweep_started_ts)
        return sweep_targets, None, "smart", smart_plan, summary
    full_seq = _contiguous_targets(cur_gal, cur_sys, total_pages + 1, owned_gals if galaxy is None else ())
    sweep_targets = full_seq[:total_pages]
    contiguous_tail = full_seq[total_pages] if len(full_seq) > total_pages else None
    if smart_plan is None:
        smart_plan = plan_smart_sweep_locked(
            bridge,
            batch_size=1,
            galaxies=[max(1, min(max_g, cur_gal))] if galaxy is not None else owned_gals,
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
        for tg, ts_c in sweep_targets[:5]
    ]
    summary = store_smart_sweep_summary_locked(
        ctx, smart_plan, selection_mode="contiguous", now_ts=sweep_started_ts, sample_targets=samples
    )
    return sweep_targets, contiguous_tail, "contiguous", smart_plan, summary


def _format_dry_sweep_response(
    *,
    sweep_targets: Sequence[tuple[int, int]],
    start_coords: tuple[int, int],
    end_coords: tuple[int, int],
    selection_mode: str,
    smart_plan: Mapping[str, object] | None,
    smart_summary: Mapping[str, object],
    collect_colonies: bool,
    allocated_normal: int,
    max_planets_cap: int,
    cfg_delay_s: float,
    cfg_jitter_s: float,
    dry_state: Mapping[str, object],
) -> dict[str, object]:
    cur_gal, cur_sys = start_coords
    end_gal, end_sys = end_coords
    scan_phase = str(smart_summary.get("scan_phase") or "exploratory")
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "galaxy_sweep",
            "executed": False,
            "dry_run": True,
            "pages_planned": len(sweep_targets),
            "start_galaxy": cur_gal,
            "start_system": cur_sys,
            "end_galaxy": end_gal,
            "end_system": end_sys,
            "selection_mode": selection_mode,
            "scan_phase": scan_phase,
            "explored_systems": smart_summary.get("explored_systems", 0),
            "unscanned_systems": smart_summary.get("unscanned_systems", 0),
            "total_systems": smart_summary.get("total_systems", 0),
            "coverage_pct": smart_summary.get("coverage_pct", 0.0),
            "exploratory_count": safe_api_int((smart_plan or {}).get("exploratory_count"), 0)
            if selection_mode == "smart"
            else 0,
            "refresh_count": safe_api_int((smart_plan or {}).get("refresh_count"), 0)
            if selection_mode == "smart"
            else 0,
            "sample_targets": smart_summary.get("sample_targets", []),
            "planned_targets": [f"{g:02d}:{s:03d}" for g, s in sweep_targets[:400]],
            "collect_colonies": collect_colonies,
            "allocated_normal_planets": allocated_normal,
            "max_planets": max_planets_cap,
            "click_delay_seconds": cfg_delay_s,
            "click_jitter_seconds": cfg_jitter_s,
            "galaxy_sweep": dict(dry_state),
            "message": (
                f"[DRY-RUN] Would sweep {len(sweep_targets)} galaxy pages "
                f"({selection_mode} {scan_phase}, coverage {smart_summary.get('coverage_pct', 0.0)}%) "
                f"starting at [{cur_gal:02d}:{cur_sys:03d}] (colonies={'ON' if collect_colonies else 'FULL'})."
            ),
        }
    )


def run_dry_galaxy_sweep(
    bridge: BridgeApiFacade,
    *,
    pages: int | None,
    galaxy: int | None,
    start_system: int | None,
    now_ts: float,
) -> dict[str, object]:
    """Compute a deterministic dry-run galaxy sweep preview without touching the browser."""
    sweep_started_ts = round(now_ts, 3)
    with bridge.ctx.state_lock:
        ctx = bridge.ctx
        if ctx.bot_control.get("captcha_blocked"):
            return sanitize_json_dict(
                {
                    "ok": True,
                    "command": "galaxy_sweep",
                    "executed": False,
                    "dry_run": True,
                    "captcha_blocked": True,
                    "message": "Paused for CAPTCHA — confirm solved in UI before sweeping galaxy.",
                    "galaxy_sweep": bridge.get_galaxy_sweep_state_locked(),
                }
            )
        cfg = ctx.strategy_config
        total_pages = max(1, min(400, int(pages if pages is not None else cfg.galaxy_sweep_pages)))
        max_g = max_galaxies_for_ctx(ctx)
        owned_gals = owned_sweep_galaxies(ctx, ctx.last_state)
        cur_gal, cur_sys = _resolve_initial_cursor(
            bridge,
            bridge.get_galaxy_sweep_state_locked(),
            owned_gals,
            galaxy=galaxy,
            start_system=start_system,
            total_pages=total_pages,
            max_g=max_g,
        )
        allocated_normal = count_allocated_normal_planets(ctx.last_state)
        max_planets_cap = max(1, safe_api_int(cfg.max_planets, 11))
        collect_colonies = allocated_normal < max_planets_cap
        cfg_delay_s = round(max(0.2, min(60.0, safe_api_float(cfg.galaxy_scan_click_delay_seconds, 1.5))), 3)
        cfg_jitter_s = round(max(0.0, min(30.0, safe_api_float(cfg.galaxy_scan_click_jitter_seconds, 0.6))), 3)
        updates: dict[str, object] = {
            "last_sweep_started_ts": sweep_started_ts,
            "click_delay_seconds": cfg_delay_s,
            "click_jitter_seconds": cfg_jitter_s,
        }
        ctx.galaxy_sweep_mem.update(updates)
        if ctx.db is not None:
            ctx.db.save_galaxy_sweep_state(updates, ts=sweep_started_ts)

        sweep_targets, contiguous_tail, selection_mode, smart_plan, smart_summary = _select_dry_targets_locked(
            bridge,
            cur_gal=cur_gal,
            cur_sys=cur_sys,
            total_pages=total_pages,
            galaxy=galaxy,
            start_system=start_system,
            owned_gals=owned_gals,
            collect_colonies=collect_colonies,
            max_g=max_g,
            sweep_started_ts=sweep_started_ts,
        )
        start_coords = sweep_targets[0]
        end_coords = contiguous_tail if contiguous_tail is not None else sweep_targets[-1]
        dry_state = dict(bridge.get_galaxy_sweep_state_locked())
        for sk_k, sk_v in smart_summary.items():
            if sk_k != "smart_summary_ts":
                dry_state.setdefault(sk_k, sk_v)
        dry_state["last_sweep_started_ts"] = sweep_started_ts

    return _format_dry_sweep_response(
        sweep_targets=sweep_targets,
        start_coords=start_coords,
        end_coords=end_coords,
        selection_mode=selection_mode,
        smart_plan=smart_plan,
        smart_summary=smart_summary,
        collect_colonies=collect_colonies,
        allocated_normal=allocated_normal,
        max_planets_cap=max_planets_cap,
        cfg_delay_s=cfg_delay_s,
        cfg_jitter_s=cfg_jitter_s,
        dry_state=dry_state,
    )
