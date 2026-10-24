"""Universe galaxy bounds, sector intel queries, galaxy slot persistence, and galaxy scope."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
import re
import time

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, normalize_planet_type
from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.parsing import GalaxySlot
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    bounded_api_str,
    safe_api_float,
    safe_api_int,
)
from spaceinvasion.runtime.bridge._helpers_sig import supports_kwarg
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.client import GameBrowserClient

_LOGGER = logging.getLogger(__name__)
_MAX_GALAXIES_RE = re.compile(r"\bmaxGalaxies\b\s*[:=]\s*(\d{1,2})\b", re.ASCII)
_GAL_SYS_RE = re.compile(r"(\d{1,2})\s*:\s*(\d{1,3})", re.ASCII)


def max_galaxies_for_ctx(ctx: BridgeContext) -> int:
    """Return observed universe max galaxy count, defaulting to 14."""
    mg = ctx.universe_max_galaxies
    if isinstance(mg, int) and not isinstance(mg, bool) and 1 <= mg <= 14:
        return mg
    return 14


def get_scanned_systems_for_ctx(ctx: BridgeContext) -> dict[str, float]:
    """Return scanned system timestamps from DB when present or from memory."""
    if ctx.db is not None:
        return dict(ctx.db.get_galaxy_scans())
    return dict(ctx.scanned_systems)


def observe_galaxy_universe_bounds_for_ctx(
    ctx: BridgeContext,
    *,
    client: GameBrowserClient | None = None,
    html: str | None = None,
) -> int:
    """Observe `maxGalaxies` from browser client or galaxy HTML and persist changes."""
    obs: int | None = None
    if client is not None:
        try:
            raw_c = client.last_galaxy_max_galaxies
        except AttributeError:
            raw_c = None
        if isinstance(raw_c, int) and not isinstance(raw_c, bool) and 1 <= raw_c <= 14:
            obs = raw_c
    if obs is None and isinstance(html, str) and html and "SI_TPL.galaxy" in html:
        m_mg = _MAX_GALAXIES_RE.search(html)
        if m_mg:
            cand = safe_api_int(m_mg.group(1), 0)
            if 1 <= cand <= 14:
                obs = cand
    if obs is not None and obs != ctx.universe_max_galaxies:
        ctx.universe_max_galaxies = obs
        if ctx.db is not None:
            try:
                ctx.db.save_meta_json("universe_max_galaxies", obs)
            except BRIDGE_RUNTIME_ERRORS:
                _LOGGER.warning("Failed to persist universe_max_galaxies=%s", obs, exc_info=True)
        ctx.smart_sweep_summary = {}
        ctx.smart_sweep_summary_ts = 0.0
        ctx.invalidate_dashboard_cache()
    return max_galaxies_for_ctx(ctx)


def persist_galaxy_slots_for_ctx(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    slots: Sequence[GalaxySlot],
    *,
    refresh_targets: bool = True,
) -> int:
    """Persist parsed galaxy slots and debris amounts to DB or in-memory raid targets."""
    if not slots:
        return 0
    for slot in slots:
        norm_sc = normalize_coords(slot.coords or "")
        if not norm_sc:
            continue
        if slot.has_debris:
            dp = max(0, safe_api_int(slot.debris_pig_iron, 0))
            dm = max(0, safe_api_int(slot.debris_metal, 0))
            dk = max(0, safe_api_int(slot.debris_kryptonite, 0))
            ds = max(0, safe_api_int(slot.debris_spice, 0))
            if dp + dm + dk + ds > 0:
                ctx.galaxy_debris_amounts[norm_sc] = {"pig_iron": dp, "metal": dm, "kryptonite": dk, "spice": ds}
        else:
            ctx.galaxy_debris_amounts.pop(norm_sc, None)
    if ctx.db is not None:
        saved = int(ctx.db.record_galaxy_slots(slots))
        if refresh_targets:
            state_cache.refresh_raid_targets_locked(limit=250)
        return saved
    return _persist_galaxy_slots_in_memory(ctx, state_cache, slots)


def _persist_galaxy_slots_in_memory(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    slots: Sequence[GalaxySlot],
) -> int:
    by_coords: dict[str, dict[str, object]] = {
        normalize_coords(str(r.get("coords") or r.get("target_coords") or "")): dict(r)
        for r in ctx.raid_targets
        if (r.get("coords") or r.get("target_coords"))
    }
    now_ts = round(time.time(), 3)
    saved = 0
    for slot in slots:
        norm_sc = normalize_coords(slot.coords)
        if slot.status_color == "free" and not slot.is_spacia and not slot.has_debris:
            by_coords.pop(norm_sc, None)
            continue
        status_color = bounded_api_str(slot.status_color or "yellow", default="yellow")
        prev = by_coords.get(norm_sc, {})
        reset_spacia = status_color == "free" or (bool(prev.get("is_spacia", False)) and not slot.is_spacia)
        by_coords[norm_sc] = {
            "coords": norm_sc,
            "owner": bounded_api_str(slot.owner or "", default=""),
            "planet_name": bounded_api_str(slot.name or "", default=""),
            "status_color": status_color,
            "is_inactive": bool(status_color == "yellow" or slot.is_spacia),
            "is_spacia": bool(slot.is_spacia),
            "has_debris": bool(slot.has_debris),
            "points": None if reset_spacia else prev.get("points"),
            "resources": (
                {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
                if reset_spacia
                else (prev.get("resources") or {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0})
            ),
            "fleet": {} if reset_spacia else (prev.get("fleet") or {}),
            "defense": {} if reset_spacia else (prev.get("defense") or {}),
            "dug_in": False if reset_spacia else bool(prev.get("dug_in", False)),
            "last_scanned_ts": now_ts,
            "last_spied_ts": 0.0 if reset_spacia else safe_api_float(prev.get("last_spied_ts"), 0.0),
            "has_spy_intel": False if reset_spacia else bool(prev.get("has_spy_intel", False)),
            "blocked_until_ts": safe_api_float(prev.get("blocked_until_ts"), 0.0),
            "block_reason": bounded_api_str(prev.get("block_reason") or "", default=""),
            "bash_hits": safe_api_int(prev.get("bash_hits"), 0),
            "last_blocked_ts": safe_api_float(prev.get("last_blocked_ts"), 0.0),
        }
        saved += 1
    ctx.raid_targets = state_cache.enrich_raid_targets(list(by_coords.values())[:250])
    return saved


def call_get_galaxy_sector_intel(
    db_obj: GameDatabase | None,
    *,
    galaxies: Sequence[int] | None,
    now_ts: float,
    max_galaxies: int,
) -> Mapping[str, object] | None:
    """Query `db.get_galaxy_sector_intel` with optional `max_galaxies` compatibility."""
    if db_obj is None:
        return None
    eff_max = max(1, min(14, int(max_galaxies)))
    if supports_kwarg(db_obj.get_galaxy_sector_intel, "max_galaxies"):
        raw = db_obj.get_galaxy_sector_intel(galaxies=galaxies, now_ts=now_ts, max_galaxies=eff_max)
    else:
        raw = db_obj.get_galaxy_sector_intel(galaxies=galaxies, now_ts=now_ts)
    return raw if isinstance(raw, Mapping) else None


def own_sweep_coords_for_ctx(ctx: BridgeContext, *, state_override: GameState | None = None) -> list[str]:
    """Return owned non-raid planet coordinates ordered with active planet first."""
    out: list[str] = []
    st = state_override if state_override is not None else ctx.last_state
    if st is None:
        return out
    ap = st.active_planet()
    ordered = ([ap] if ap is not None else []) + [p for p in st.planets if ap is None or p.planet_id != ap.planet_id]
    for planet in ordered:
        if normalize_planet_type(planet) == "raid":
            continue
        coords = str(planet.coords or "").strip()
        if coords and coords not in out:
            out.append(coords)
    return out


def owned_sweep_galaxies_for_ctx(ctx: BridgeContext, *, state_override: GameState | None = None) -> list[int]:
    """Return unique galaxies hosting owned non-raid planets (default `[1]`)."""
    gals: list[int] = []
    max_g = max_galaxies_for_ctx(ctx)
    st = state_override if state_override is not None else ctx.last_state
    if st is not None:
        for planet in st.planets:
            if normalize_planet_type(planet) == "raid":
                continue
            match = _GAL_SYS_RE.search(str(planet.coords or ""))
            if match:
                gal = max(1, min(max_g, int(match.group(1))))
                if gal not in gals:
                    gals.append(gal)
    return gals or [1]


def sweep_target_galaxies_for_ctx(
    ctx: BridgeContext,
    *,
    explicit_galaxy: int | None = None,
    refresh_pct_override: int | None = None,
    all_galaxies_override: bool | None = None,
    state_override: GameState | None = None,
    scanned_systems_override: Mapping[str, float] | None = None,
) -> list[int]:
    """Compute which galaxy numbers are currently in scope for smart galaxy scanning."""
    max_g = max_galaxies_for_ctx(ctx)
    if explicit_galaxy is not None:
        return [max(1, min(max_g, int(explicit_galaxy)))]
    cfg = ctx.strategy_config
    scan_all = bool(all_galaxies_override) if all_galaxies_override is not None else bool(cfg.galaxy_scan_all_galaxies)
    if scan_all:
        return list(range(1, max_g + 1))
    owned = owned_sweep_galaxies_for_ctx(ctx, state_override=state_override)
    raw_ref = refresh_pct_override if refresh_pct_override is not None else cfg.galaxy_scan_refresh_pct
    refresh_pct_cfg = max(0, min(100, safe_api_int(raw_ref, 0)))
    if 0 < refresh_pct_cfg < 100:
        scanned = scanned_systems_override if scanned_systems_override is not None else get_scanned_systems_for_ctx(ctx)
        owned_set = set(owned)
        owned_scanned: set[tuple[int, int]] = set()
        for sk, ts_v in scanned.items():
            if safe_api_float(ts_v, 0.0) <= 0.0:
                continue
            match = _GAL_SYS_RE.search(str(sk))
            if match:
                gi_u, si_u = int(match.group(1)), int(match.group(2))
                if gi_u in owned_set and 1 <= si_u <= 400:
                    owned_scanned.add((gi_u, si_u))
        if len(owned_scanned) >= len(owned_set) * 400:
            return list(range(1, max_g + 1))
    return owned


def build_scanned_sectors_summary(
    *,
    cfg: StrategyConfig,
    rows_out: Sequence[dict[str, object]],
    scan_queue: Sequence[dict[str, object]],
    intel: Mapping[str, object],
    sweep_summary: Mapping[str, object],
    smart_plan: Mapping[str, object] | None,
    queue_galaxies: Sequence[int],
    total_scanned_all: int,
    max_g: int,
    is_preview: bool,
    sweep_pages: int,
    refresh_pct_cfg: int,
    explore_first_cfg: bool,
    scan_all_gals_cfg: bool,
    saved_sweep_pages: int,
    saved_refresh_pct: int,
    saved_explore_first: bool,
    saved_scan_all_gals: bool,
) -> dict[str, object]:
    """Assemble the aggregate `summary` section of `scanned_sectors`."""
    eff_refresh_pct = refresh_pct_cfg if refresh_pct_cfg > 0 else (0 if explore_first_cfg else 100)
    saved_eff_refresh_pct = saved_refresh_pct if saved_refresh_pct > 0 else (0 if saved_explore_first else 100)
    planned_refresh_pages = int(round(sweep_pages * (eff_refresh_pct / 100.0)))
    planned_discovery_pages = max(0, sweep_pages - planned_refresh_pages)
    scanned_rows = [r for r in rows_out if r["scanned"]]
    total_occ = sum(safe_api_int(r["occupied_count"], 0) for r in scanned_rows)
    uni_exp = max(
        total_scanned_all,
        safe_api_int(
            intel.get("universe_explored_systems"), safe_api_int(sweep_summary.get("universe_explored_systems"), 0)
        ),
    )
    uni_tot = max(1, safe_api_int(intel.get("universe_total_systems"), max_g * 400))
    q_disc_cnt = sum(1 for q in scan_queue if q["phase"] == "exploratory")
    q_ref_cnt = len(scan_queue) - q_disc_cnt
    scope_src: Mapping[str, object] = smart_plan if (is_preview and isinstance(smart_plan, Mapping)) else sweep_summary
    return {
        "scanned_sectors": len(scanned_rows),
        "unscanned_sectors_in_view": len(rows_out) - len(scanned_rows),
        "unscanned_total_all_galaxies": max(0, uni_tot - uni_exp),
        "total_scanned_all_galaxies": total_scanned_all,
        "explored_systems": safe_api_int(scope_src.get("explored_systems"), total_scanned_all),
        "unscanned_systems": safe_api_int(scope_src.get("unscanned_systems"), 0),
        "total_systems": safe_api_int(scope_src.get("total_systems"), len(queue_galaxies) * 400),
        "coverage_pct": round(safe_api_float(scope_src.get("coverage_pct"), 0.0), 2),
        "universe_explored_systems": uni_exp,
        "universe_total_systems": uni_tot,
        "universe_coverage_pct": round(min(100.0, (uni_exp / uni_tot) * 100.0), 2),
        "total_occupied": total_occ,
        "avg_occupied": round(total_occ / len(scanned_rows), 1) if scanned_rows else 0.0,
        "total_free": sum(safe_api_int(r["free_count"], 0) for r in scanned_rows),
        "total_prime_free": sum(safe_api_int(r["prime_colony_free_count"], 0) for r in scanned_rows),
        "total_debris_fields": sum(safe_api_int(r["debris_count"], 0) for r in scanned_rows),
        "sectors_with_debris": sum(1 for r in scanned_rows if safe_api_int(r["debris_count"], 0) > 0),
        "total_targets": sum(safe_api_int(r["target_count"], 0) for r in scanned_rows),
        "total_spacia": sum(safe_api_int(r["spacia_count"], 0) for r in scanned_rows),
        "total_inactives": sum(safe_api_int(r["inactive_count"], 0) for r in scanned_rows),
        "stale_over_1h_count": sum(
            1 for r in scanned_rows if r["age_seconds"] is not None and safe_api_float(r["age_seconds"], 0.0) >= 3600.0
        ),
        "total_raids": sum(safe_api_int(r["direct_raid_count"], 0) for r in scanned_rows),
        "sweep_pages": sweep_pages,
        "sweep_interval_seconds": safe_api_int(cfg.galaxy_sweep_interval_seconds, 270),
        "allow_galaxy_scan": bool(cfg.allow_galaxy_scan),
        "explore_first": explore_first_cfg,
        "scan_all_galaxies": scan_all_gals_cfg,
        "refresh_pct": refresh_pct_cfg,
        "discovery_pct": 100 - refresh_pct_cfg,
        "effective_refresh_pct": eff_refresh_pct,
        "effective_discovery_pct": 100 - eff_refresh_pct,
        "saved_refresh_pct": saved_eff_refresh_pct,
        "saved_sweep_pages": saved_sweep_pages,
        "saved_scan_all_galaxies": saved_scan_all_gals,
        "planned_refresh_pages": planned_refresh_pages,
        "planned_discovery_pages": planned_discovery_pages,
        "next_batch_refresh_count": q_ref_cnt,
        "next_batch_discovery_count": q_disc_cnt,
        "queue_count": len(scan_queue),
        "queue_discovery_count": q_disc_cnt,
        "queue_refresh_count": q_ref_cnt,
        "queue_galaxies": list(queue_galaxies),
        "scan_phase": str(scope_src.get("scan_phase") or scope_src.get("phase") or "exploratory"),
    }
