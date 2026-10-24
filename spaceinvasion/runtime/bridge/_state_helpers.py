"""Sync-flag calculation, universe research max query, and raid-target refresh helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from spaceinvasion.combat.points import record_rank_snapshot
from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, PlanetState
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import safe_api_int
from spaceinvasion.runtime.bridge._state_offers import enrich_raid_target_rows


def compute_snapshot_sync_flags(ctx: BridgeContext, *, include_buildings: bool, now: float) -> tuple[bool, bool]:
    """Determine whether live research and shipyard pages should be fetched on this state read."""
    batch_iv = max(5.0, float(ctx.strategy_config.action_batch_interval_seconds))
    has_cached_res = bool(ctx.last_state is not None and ctx.last_state.research_offers)
    last_active = ctx.last_state.active_planet() if ctx.last_state is not None else None
    has_cached_ships = bool(last_active is not None and last_active.ship_offers)
    eff_res_ttl = (
        max(BridgeConstants.RESEARCH_SYNC_TTL_S, batch_iv * 3.0)
        if has_cached_res
        else BridgeConstants.RESEARCH_SYNC_TTL_S
    )
    eff_ship_ttl = (
        max(BridgeConstants.SHIPYARD_SYNC_TTL_S, batch_iv * 3.0)
        if has_cached_ships
        else BridgeConstants.SHIPYARD_SYNC_TTL_S
    )
    research_due = ctx.last_research_sync_at <= 0.0 or (now - ctx.last_research_sync_at) >= eff_res_ttl
    shipyard_due = ctx.last_shipyard_sync_at <= 0.0 or (now - ctx.last_shipyard_sync_at) >= eff_ship_ttl
    cold_boot = ctx.last_state is None or ctx.last_snapshot_at < 0.0
    need_research = bool(
        include_buildings
        and ((cold_boot and not has_cached_res) or (ctx.strategy_config.allow_research and research_due))
    )
    need_shipyard = bool(
        include_buildings
        and ((cold_boot and not has_cached_ships) or (ctx.strategy_config.allow_shipyard and shipyard_due))
    )
    if ctx.last_state is not None and include_buildings:
        cur_pid = safe_api_int(last_active.planet_id) if last_active is not None else 0
        cur_b8 = last_active.building_level(8) if last_active is not None else 0
        cur_b9 = last_active.building_level(9) if last_active is not None else 0
        cur_res_sig = tuple(
            sorted((game_data.safe_key(k), safe_api_int(v)) for k, v in (ctx.last_state.researches or {}).items())
        )
        cur_res_busy = any(q.category == "research" for q in ctx.last_state.active_queues)
        if cur_b8 <= 0 or cur_res_busy:
            need_research = False
        if cur_b9 <= 0:
            need_shipyard = False
        if (
            last_active is not None
            and cur_b9 > 0
            and not last_active.ship_offers
            and ctx.last_shipyard_sync_sig != (cur_pid, cur_b9, cur_res_sig)
        ):
            need_shipyard = True
        if (
            cur_b8 > 0
            and not ctx.last_state.research_offers
            and ctx.last_research_sync_sig != (cur_b8, cur_res_sig, cur_res_busy)
        ):
            need_research = True
    return need_research, need_shipyard


def refresh_raid_targets_for_ctx(
    ctx: BridgeContext,
    *,
    limit: int = 100,
    debris_limit: int = 60,
    state_override: GameState | None = None,
) -> tuple[dict[str, object], ...]:
    """Merge top spy targets with proximity-ranked active debris fields."""
    db = ctx.db
    if db is None:
        ctx.raid_targets = enrich_raid_target_rows(ctx, ctx.raid_targets)
        return ctx.raid_targets
    st = state_override if state_override is not None else ctx.last_state
    own_coords = [str(p.coords or "") for p in (st.planets if st is not None else ()) if p.coords]
    tok = ctx.db_total_changes_token()
    rt_ckey = (
        (tok, int(limit), int(debris_limit), tuple(own_coords), tuple(sorted(ctx.target_blocks.items())))
        if tok is not None
        else None
    )
    if rt_ckey is not None and ctx.raid_targets_cache_key == rt_ckey:
        return ctx.raid_targets
    spy_rows = db.get_spy_targets(limit=limit)
    merged: list[dict[str, object]] = []
    seen_coords: set[str] = set()
    if debris_limit > 0:
        for drow in db.get_active_debris_targets(origin_coords=own_coords, limit=debris_limit, include_own=False):
            if not isinstance(drow, Mapping):
                continue
            nc = normalize_coords(str(drow.get("coords") or drow.get("target_coords") or ""))
            if nc and nc not in seen_coords:
                seen_coords.add(nc)
                merged.append(dict(drow))
    for srow in spy_rows:
        if not isinstance(srow, Mapping):
            continue
        nc = normalize_coords(str(srow.get("coords") or srow.get("target_coords") or ""))
        if nc and nc in seen_coords:
            continue
        if nc:
            seen_coords.add(nc)
        merged.append(dict(srow))
    ctx.raid_targets = enrich_raid_target_rows(ctx, merged)
    ctx.raid_targets_cache_key = rt_ckey
    return ctx.raid_targets


def build_universe_research_max(
    ctx: BridgeContext,
    state: GameState | None = None,
) -> list[dict[str, object]]:
    """Return universe-wide max observed research levels merged with own levels."""
    eff_state = state if state is not None else ctx.last_state
    own_res = eff_state.researches if eff_state is not None else None
    own_nick = eff_state.nickname if eff_state is not None else ""
    if ctx.db is not None:
        return [
            dict(row)
            for row in ctx.db.get_universe_research_max(own_researches=own_res, own_nick=own_nick)
            if isinstance(row, Mapping)
        ]
    rows: list[dict[str, object]] = []
    own_map = dict(own_res) if isinstance(own_res, Mapping) else {}
    for slug, info in sorted(
        (game_data.RESEARCHES or {}).items(),
        key=lambda kv: safe_api_int((kv[1] or {}).get("id"), 999),
    ):
        if not isinstance(info, Mapping):
            continue
        rid = safe_api_int(info.get("id"), 0)
        if rid <= 0:
            continue
        rkey = f"r{rid}"
        own_l = max(0, safe_api_int(own_map.get(rkey, own_map.get(slug, 0)), 0))
        raw_levels = info.get("levels")
        levels_d: Mapping[object, object] = raw_levels if isinstance(raw_levels, Mapping) else {}
        cat_max = max((int(str(k)) for k in levels_d if str(k).isdigit()), default=0) or None
        rows.append(
            {
                "research_id": rid,
                "key": rkey,
                "slug": str(slug),
                "name": str(info.get("name") or slug),
                "own_level": own_l,
                "universe_max_level": own_l,
                "spied_max_level": 0,
                "server_record_level": None,
                "catalog_max_level": cat_max,
                "gap_to_max": 0,
                "holder": (own_nick or "You") if own_l > 0 else "",
                "holder_coords": "",
                "updated_ts": 0.0,
            }
        )
    return rows


def merge_economy_rates_for_ctx(ctx: BridgeContext, state: GameState) -> GameState:
    """Merge cached and executor-observed economy rates and raid targets onto `state`."""
    for pid, rates in ctx.live_executor.last_economy_rates.items():
        if rates:
            ctx.economy_rates[int(pid)] = dict(rates)
    changed = False
    updated_planets: list[PlanetState] = []
    for planet in state.planets:
        if planet.economy_rates:
            prev_cached = ctx.economy_rates.get(planet.planet_id)
            new_rates = dict(planet.economy_rates)
            ctx.economy_rates[planet.planet_id] = new_rates
            if ctx.db is not None and prev_cached != new_rates:
                ctx.db.save_economy_rates(planet.planet_id, planet.coords, new_rates)
            updated_planets.append(planet)
        elif planet.planet_id in ctx.economy_rates:
            changed = True
            updated_planets.append(replace(planet, economy_rates=dict(ctx.economy_rates[planet.planet_id])))
        else:
            updated_planets.append(planet)
    if ctx.db is not None:
        refresh_raid_targets_for_ctx(ctx, limit=100, state_override=state)
    out_state = replace(state, planets=tuple(updated_planets)) if changed else state
    if ctx.raid_targets and out_state.raid_targets != ctx.raid_targets:
        out_state = replace(out_state, raid_targets=ctx.raid_targets)
    return out_state


def sync_observed_active_planet_for_ctx(
    ctx: BridgeContext,
    fallback_state: GameState,
    *,
    obs_pid: int | None,
    obs_confirmed: bool | None,
) -> None:
    """Sync `active_planet_id` and confirmation flags after an action observes the browser planet."""
    target_state = ctx.last_state or fallback_state
    planet_ids = {p.planet_id for p in target_state.planets}
    if obs_confirmed is False:
        if len(target_state.planets) > 1:
            ctx.active_planet_confirmed = False
    elif obs_confirmed is True:
        ctx.active_planet_confirmed = True
        if (
            isinstance(obs_pid, int)
            and not isinstance(obs_pid, bool)
            and obs_pid in planet_ids
            and target_state.active_planet_id != obs_pid
        ):
            was_recorded = target_state is ctx.last_recorded_state_obj
            if len(target_state.planets) > 1:
                ctx.last_research_sync_sig = None
                ctx.last_state = replace(target_state, active_planet_id=obs_pid, research_offers=())
            else:
                ctx.last_state = replace(target_state, active_planet_id=obs_pid)
            if was_recorded:
                ctx.last_recorded_state_obj = ctx.last_state
            active_p = ctx.last_state.active_planet()
            if active_p is None or not active_p.building_offers:
                ctx.last_buildings_fetched = False
    elif len(target_state.planets) <= 1:
        ctx.active_planet_confirmed = True


def commit_recorded_state_metadata(
    ctx: BridgeContext,
    prev: GameState | None,
    merged: GameState,
    *,
    now: float,
    fetched_buildings: bool,
    fetched_research: bool,
    fetched_shipyard: bool,
    update_snapshot_ts: bool,
    persist_runtime_fn: Callable[[], None],
) -> None:
    """Update sync caches, record rank/state snapshots in DB, and persist runtime state."""
    ctx.last_state = merged
    ctx.last_recorded_state_obj = merged
    ctx.invalidate_dashboard_cache()
    if update_snapshot_ts:
        ctx.last_snapshot_at = now
    active_p = merged.active_planet()
    if fetched_buildings:
        ctx.last_buildings_fetched = True
    elif active_p is None or not active_p.building_offers:
        ctx.last_buildings_fetched = False

    b8_lvl = active_p.building_level(8) if active_p is not None else 0
    b9_lvl = active_p.building_level(9) if active_p is not None else 0
    pid_sig = safe_api_int(active_p.planet_id) if active_p is not None else 0
    res_sig = tuple(sorted((game_data.safe_key(k), safe_api_int(v)) for k, v in (merged.researches or {}).items()))
    res_busy = any(q.category == "research" for q in merged.active_queues)
    if fetched_research:
        ctx.last_research_sync_at = now
        ctx.last_research_sync_sig = (b8_lvl, res_sig, res_busy)
    elif not merged.research_offers and prev is not None and prev.active_planet_id != merged.active_planet_id:
        ctx.last_research_sync_sig = None
    if fetched_shipyard:
        ctx.last_shipyard_sync_at = now
        ctx.last_shipyard_sync_sig = (pid_sig, b9_lvl, res_sig)
    if ctx.db is not None:
        ctx.rank_samples = [dict(r) for r in ctx.db.record_state_snapshot(merged) if isinstance(r, Mapping)]
        ctx.db.record_full_state_snapshot(merged, (), source="bridge", min_interval_seconds=600.0)
        persist_runtime_fn()
    else:
        ctx.rank_samples = [
            dict(r) for r in record_rank_snapshot(ctx.rank_history_path, merged) if isinstance(r, Mapping)
        ]
