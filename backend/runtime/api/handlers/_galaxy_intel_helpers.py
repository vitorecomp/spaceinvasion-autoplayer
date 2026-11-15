"""Shared distance, Spice-equivalent, galaxy-scope, and smart-sweep planning helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import re

from backend.combat.scanner import select_smart_galaxy_scan_batch
from backend.core.coords import coords_distance, normalize_coords
from backend.core.model import GameState, normalize_planet_type
from backend.runtime.api.handlers._bridge_proto import BridgeApiFacade
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    safe_api_float,
    safe_api_int,
)

_LOGGER = logging.getLogger(__name__)
_GSP_RE: re.Pattern[str] = re.compile(r"(\d+):(\d+):(\d+)", re.ASCII)
GS_SEARCH_RE: re.Pattern[str] = re.compile(r"(\d{1,2})\s*:\s*(\d{1,3})", re.ASCII)


def parse_origin_gsp(origin_coords: str) -> tuple[int, int, int] | None:
    """Parse canonical `GG:SSS:PP` coordinates into `(galaxy, system, position)`."""
    match = _GSP_RE.fullmatch(origin_coords) if origin_coords else None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def calc_espionage_distance(
    origin_coords: str,
    orig_gsp: tuple[int, int, int] | None,
    target_raw: object,
) -> dict[str, object]:
    """Compute distance and delta fields relative to `origin_coords` for spy targets and reports."""
    norm_t = normalize_coords(target_raw) if target_raw else ""
    match = _GSP_RE.fullmatch(norm_t) if norm_t else None
    if orig_gsp is None or match is None:
        return {
            "distance": None,
            "system_distance": None,
            "galaxy_delta": None,
            "system_delta": None,
            "position_delta": None,
        }
    g_diff = abs(orig_gsp[0] - int(match.group(1)))
    s_diff = abs(orig_gsp[1] - int(match.group(2)))
    p_diff = abs(orig_gsp[2] - int(match.group(3)))
    return {
        "distance": round(float(coords_distance(origin_coords, norm_t)), 1),
        "system_distance": int(g_diff * 400 + s_diff),
        "galaxy_delta": int(g_diff),
        "system_delta": int(s_diff),
        "position_delta": int(p_diff),
    }


def compute_spice_eq(raw_resources: object) -> float:
    """Compute Spice-equivalent value from a resource mapping."""
    res_d = raw_resources if isinstance(raw_resources, Mapping) else {}
    pig = safe_api_float(res_d.get("pig_iron", 0.0))
    met = safe_api_float(res_d.get("metal", 0.0))
    kry = safe_api_float(res_d.get("kryptonite", 0.0))
    spi = safe_api_float(res_d.get("spice", 0.0))
    return round(pig / 6.0 + met / 2.5 + kry / 3.0 + spi, 1)


def max_galaxies_for_ctx(ctx: BridgeContext) -> int:
    """Return observed universe galaxy count bounded to `[1, 14]`."""
    mg = ctx.universe_max_galaxies
    return mg if isinstance(mg, int) and not isinstance(mg, bool) and 1 <= mg <= 14 else 14


def own_sweep_coords(state: GameState | None) -> list[str]:
    """Return deduplicated non-raid planet coordinates starting with the active planet."""
    if state is None:
        return []
    ap = state.active_planet()
    ordered = ([ap] if ap is not None else []) + [p for p in state.planets if ap is None or p.planet_id != ap.planet_id]
    out: list[str] = []
    for planet in ordered:
        if normalize_planet_type(planet) == "raid":
            continue
        coords = str(planet.coords or "").strip()
        if coords and coords not in out:
            out.append(coords)
    return out


def owned_sweep_galaxies(ctx: BridgeContext, state: GameState | None) -> list[int]:
    """Return ordered galaxy numbers occupied by owned non-raid planets (default `[1]`)."""
    max_g = max_galaxies_for_ctx(ctx)
    gals: list[int] = []
    if state is not None:
        for planet in state.planets:
            if normalize_planet_type(planet) == "raid":
                continue
            match = GS_SEARCH_RE.search(str(planet.coords or ""))
            if match:
                gal = max(1, min(max_g, int(match.group(1))))
                if gal not in gals:
                    gals.append(gal)
    return gals or [1]


def sweep_target_galaxies(
    ctx: BridgeContext,
    *,
    explicit_galaxy: int | None = None,
    refresh_pct_override: int | None = None,
    all_galaxies_override: bool | None = None,
    state: GameState | None = None,
    scanned_systems: Mapping[str, float] | None = None,
) -> list[int]:
    """Determine which galaxies are in scope for the next smart galaxy sweep batch."""
    max_g = max_galaxies_for_ctx(ctx)
    if explicit_galaxy is not None:
        return [max(1, min(max_g, int(explicit_galaxy)))]
    cfg = ctx.strategy_config
    scan_all = bool(all_galaxies_override) if all_galaxies_override is not None else bool(cfg.galaxy_scan_all_galaxies)
    if scan_all:
        return list(range(1, max_g + 1))
    owned = owned_sweep_galaxies(ctx, state)
    refresh_raw = refresh_pct_override if refresh_pct_override is not None else cfg.galaxy_scan_refresh_pct
    refresh_pct = max(0, min(100, safe_api_int(refresh_raw, 0)))
    if 0 < refresh_pct < 100:
        scanned = scanned_systems if scanned_systems is not None else ctx.scanned_systems
        owned_set = set(owned)
        owned_scanned: set[tuple[int, int]] = set()
        for skey, ts_v in scanned.items():
            if safe_api_float(ts_v, 0.0) <= 0.0:
                continue
            match = GS_SEARCH_RE.search(str(skey))
            if match:
                gi, si = int(match.group(1)), int(match.group(2))
                if gi in owned_set and 1 <= si <= 400:
                    owned_scanned.add((gi, si))
        if len(owned_scanned) >= len(owned_set) * 400:
            return list(range(1, max_g + 1))
    return owned


def _current_cursor_locked(bridge: BridgeApiFacade, galaxies: Sequence[int]) -> tuple[int, int] | None:
    ctx = bridge.ctx
    max_g = max_galaxies_for_ctx(ctx)
    gal_set = {max(1, min(max_g, safe_api_int(g, 0))) for g in galaxies if safe_api_int(g, 0) > 0}
    live_coords = ctx.last_galaxy_browser_coords
    if live_coords is not None:
        lg, ls = live_coords
        if 1 <= lg <= max_g and 1 <= ls <= 400 and (not gal_set or lg in gal_set):
            return (lg, ls)
    sw_st = bridge.get_galaxy_sweep_state_locked()
    if safe_api_int(sw_st.get("total_sweeps"), 0) > 0:
        cg = safe_api_int(sw_st.get("cursor_galaxy"), 0)
        cs = safe_api_int(sw_st.get("cursor_system"), 0)
        if 1 <= cg <= max_g and 1 <= cs <= 400 and (not gal_set or cg in gal_set):
            return (cg, cs)
    return None


def _compute_universe_coverage(
    intel: Mapping[str, object] | None,
    scanned_src: Mapping[str, float],
    plan_explored: int,
    max_g: int,
) -> tuple[int, int, float]:
    uni_tot = max_g * 400
    if isinstance(intel, Mapping) and intel.get("universe_explored_systems") is not None:
        uni_exp = max(0, safe_api_int(intel.get("universe_explored_systems"), 0))
        uni_tot = max(1, safe_api_int(intel.get("universe_total_systems"), uni_tot))
    else:
        uni_keys: set[tuple[int, int]] = set()
        for sk, ts_v in scanned_src.items():
            if safe_api_float(ts_v, 0.0) <= 0.0:
                continue
            match = GS_SEARCH_RE.search(str(sk))
            if match:
                gi, si = int(match.group(1)), int(match.group(2))
                if 1 <= gi <= max_g and 1 <= si <= 400:
                    uni_keys.add((gi, si))
        uni_exp = max(len(uni_keys), plan_explored)
    return uni_exp, uni_tot, round(min(100.0, (uni_exp / uni_tot) * 100.0), 2)


def plan_smart_sweep_locked(
    bridge: BridgeApiFacade,
    *,
    batch_size: int,
    galaxies: Sequence[int],
    collect_colonies: bool,
    now_ts: float,
    sector_intel: Mapping[str, object] | None = None,
    explore_first: bool | None = None,
    refresh_pct: int | None = None,
    scanned_systems: Mapping[str, float] | None = None,
    state: GameState | None = None,
    raid_targets: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object] | None:
    """Compute the next smart 2-phase galaxy sweep batch without touching the browser."""
    ctx = bridge.ctx
    cfg = ctx.strategy_config
    max_g = max_galaxies_for_ctx(ctx)
    st_ref = state if state is not None else ctx.last_state
    gal_list = sorted({max(1, min(max_g, safe_api_int(g, 1))) for g in galaxies if safe_api_int(g, 0) > 0})
    intel: Mapping[str, object] | None = sector_intel
    if intel is None and ctx.db is not None:
        try:
            raw_intel = ctx.db.get_galaxy_sector_intel(galaxies=gal_list or None, now_ts=now_ts, max_galaxies=max_g)
            intel = raw_intel if isinstance(raw_intel, Mapping) else None
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to query galaxy sector intel for smart sweep", exc_info=True)
            intel = None
    scanned_src = scanned_systems if scanned_systems is not None else ctx.scanned_systems
    raid_src = raid_targets if raid_targets is not None else ctx.raid_targets
    eff_explore = bool(explore_first) if explore_first is not None else bool(cfg.galaxy_scan_explore_first)
    raw_ref = refresh_pct if refresh_pct is not None else cfg.galaxy_scan_refresh_pct
    eff_refresh = max(0, min(100, safe_api_int(raw_ref, 0)))
    raid_stats = st_ref.raid_stats if st_ref is not None and isinstance(st_ref.raid_stats, Mapping) else None
    try:
        plan = select_smart_galaxy_scan_batch(
            own_coords=own_sweep_coords(st_ref),
            sector_intel=intel,
            galaxies=gal_list or None,
            scanned_systems=scanned_src,
            raid_targets=[r for r in raid_src if isinstance(r, Mapping)],
            raid_stats=raid_stats,
            batch_size=max(1, min(400 * max(1, len(gal_list)), int(batch_size))),
            explore_first=eff_explore,
            refresh_pct=eff_refresh,
            near_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_near_weight, 2.0))),
            attack_weight=max(0.0, min(20.0, safe_api_float(cfg.galaxy_scan_attack_weight, 3.0))),
            collect_colonies=bool(collect_colonies),
            current_cursor=_current_cursor_locked(bridge, gal_list),
            now_ts=now_ts,
        )
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Smart galaxy sweep selection failed; falling back", exc_info=True)
        return None
    if not isinstance(plan, Mapping):
        return None
    out_plan: dict[str, object] = dict(plan)
    uni_exp, uni_tot, uni_cov = _compute_universe_coverage(
        intel, scanned_src, safe_api_int(out_plan.get("explored_systems"), 0), max_g
    )
    out_plan["universe_explored_systems"] = uni_exp
    out_plan["universe_total_systems"] = uni_tot
    out_plan["universe_coverage_pct"] = uni_cov
    return out_plan


def store_smart_sweep_summary_locked(
    ctx: BridgeContext,
    plan: Mapping[str, object] | None,
    *,
    selection_mode: str,
    now_ts: float,
    sample_targets: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Cache the latest smart-sweep summary fields onto `BridgeContext`."""
    if not isinstance(plan, Mapping):
        return dict(ctx.smart_sweep_summary)
    default_uni_tot = max_galaxies_for_ctx(ctx) * 400
    raw_samples = sample_targets if sample_targets is not None else plan.get("sample_targets")
    samples_list = (
        list(raw_samples) if isinstance(raw_samples, Sequence) and not isinstance(raw_samples, (str, bytes)) else []
    )
    raw_gals = plan.get("galaxies")
    gals_seq = list(raw_gals) if isinstance(raw_gals, Sequence) and not isinstance(raw_gals, (str, bytes)) else []
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
        "sample_targets": [dict(s) for s in samples_list[:5] if isinstance(s, Mapping)],
        "smart_summary_ts": round(now_ts, 3),
    }
    ctx.smart_sweep_summary = summary
    ctx.smart_sweep_summary_ts = now_ts
    return dict(summary)


def build_recycler_summary(
    recycles: Sequence[Mapping[str, object]],
    debris_targets: Sequence[Mapping[str, object]],
    other_emails: Sequence[Mapping[str, object]],
    recyclers_owned: int,
    cat_counts: Mapping[str, int],
) -> dict[str, object]:
    """Compute aggregate harvest totals and message-category counters for `recycler_results`."""
    return {
        "total_runs": len(recycles),
        "total_pig_iron": sum(safe_api_int(r.get("harvested_pig_iron", 0)) for r in recycles),
        "total_metal": sum(safe_api_int(r.get("harvested_metal", 0)) for r in recycles),
        "total_kryptonite": sum(safe_api_int(r.get("harvested_kryptonite", 0)) for r in recycles),
        "total_spice": sum(safe_api_int(r.get("harvested_spice", 0)) for r in recycles),
        "total_spice_eq": round(sum(safe_api_float(r.get("spice_eq", 0.0)) for r in recycles), 1),
        "active_debris_count": len(debris_targets),
        "other_email_count": len(other_emails),
        "recyclers_owned": recyclers_owned,
        "category_counts": dict(cat_counts),
    }


def summarize_attack_results(
    battles: Sequence[Mapping[str, object]],
    raw_targets: Sequence[Mapping[str, object]],
    now_ts: float,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Compute battle outcome totals and sorted active bash/target block rows."""
    blocked: list[dict[str, object]] = []
    for row in raw_targets:
        b_until, b_hits = safe_api_float(row.get("blocked_until_ts", 0.0)), safe_api_int(row.get("bash_hits", 0))
        if b_until > now_ts or b_hits > 0:
            blocked.append(
                {
                    **dict(row),
                    "remaining_seconds": max(0.0, round(b_until - now_ts, 1)),
                    "active_block": b_until > now_ts,
                }
            )
    blocked.sort(
        key=lambda x: (
            0 if x.get("active_block") else 1,
            -safe_api_float(x.get("remaining_seconds", 0.0)),
            -safe_api_int(x.get("bash_hits", 0)),
        )
    )

    def _nested_sp(b: Mapping[str, object], k: str) -> float:
        nested = b.get(k)
        return safe_api_float(nested.get("spice_eq", 0.0)) if isinstance(nested, Mapping) else 0.0

    wins = sum(1 for b in battles if b.get("outcome") == "win")
    losses = sum(1 for b in battles if b.get("outcome") == "loss")
    summary: dict[str, object] = {
        "total_battles": len(battles),
        "wins": wins,
        "losses": losses,
        "draws": len(battles) - wins - losses,
        "total_loot_spice_eq": round(sum(_nested_sp(b, "loot") for b in battles), 1),
        "total_losses_spice_eq": round(sum(_nested_sp(b, "attacker_losses") for b in battles), 1),
        "active_blocks_count": sum(1 for bt in blocked if bt.get("active_block")),
    }
    return summary, blocked
