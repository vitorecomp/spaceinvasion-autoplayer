"""Colony candidate merging onto `GameState` and `colony_candidates` API payload builder."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from spaceinvasion.combat.fleet import normalize_coords
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, count_allocated_normal_planets
from spaceinvasion.planner.strategy import _tutorial_colonize_task
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int, sanitize_json_dict
from spaceinvasion.runtime.bridge._intel_colony_ledger import build_colony_attempts_payload
from spaceinvasion.runtime.bridge._intel_messages import own_planet_coords_for_ctx


def _merge_single_colony_candidate(
    ctx: BridgeContext,
    cand: Mapping[str, object],
    by_c: dict[str, dict[str, object]],
    ordered_rt: list[dict[str, object]],
    discarded_set: set[str],
) -> bool:
    nc = normalize_coords(str(cand.get("coords") or ""))
    if not nc or nc in discarded_set:
        return False
    cand_blk = safe_api_float(cand.get("blocked_until_ts"), 0.0)
    cand_rsn = str(cand.get("block_reason") or "")
    if nc in ctx.target_blocks and float(ctx.target_blocks[nc][0]) > cand_blk:
        cand_blk = float(ctx.target_blocks[nc][0])
        cand_rsn = str(ctx.target_blocks[nc][1])
    if nc in by_c:
        cur = by_c[nc]
        changed = False
        uf = cand.get("uninhabited_fields")
        if uf is not None and cur.get("uninhabited_fields") is None:
            cur["uninhabited_fields"] = safe_api_int(uf, 0)
            changed = True
        for ek in ("colonize_min_fields", "colonize_max_fields", "temperature"):
            ev = cand.get(ek)
            if ev is not None and cur.get(ek) is None:
                cur[ek] = safe_api_int(ev, 0)
                changed = True
        if cand_blk > safe_api_float(cur.get("blocked_until_ts"), 0.0):
            cur["blocked_until_ts"] = cand_blk
            cur["block_reason"] = cand_rsn
            changed = True
        return changed
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
        "last_scanned_ts": safe_api_float(cand.get("last_scanned_ts"), 0.0),
        "last_spied_ts": safe_api_float(cand.get("last_spied_ts"), 0.0),
        "blocked_until_ts": cand_blk,
        "block_reason": cand_rsn,
        "has_spy_intel": bool(cand.get("probed", False)),
    }
    by_c[nc] = free_entry
    ordered_rt.append(free_entry)
    return True


def merge_colony_candidates_for_ctx(
    ctx: BridgeContext,
    state: GameState,
    cfg: StrategyConfig,
) -> GameState:
    """Merge uninhabited colony candidates from DB into `state.raid_targets` when expansion is open."""
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
        exclude_coords=own_planet_coords_for_ctx(ctx, state),
        limit=200,
    )
    by_c: dict[str, dict[str, object]] = {}
    ordered_rt: list[dict[str, object]] = []
    changed_rt = False
    for r_item in state.raid_targets or ():
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
        if isinstance(cand, Mapping) and _merge_single_colony_candidate(ctx, cand, by_c, ordered_rt, discarded_set):
            added_any = True
    return replace(state, raid_targets=tuple(ordered_rt)) if added_any else state


def build_colony_candidates_payload(
    ctx: BridgeContext,
    *,
    limit: int = 50,
    min_fields: int | None = None,
    include_discarded: bool = False,
) -> dict[str, object]:
    """Build the lock-free `colony_candidates` API response payload under `ctx.intel_read_lock`."""
    with ctx.intel_read_lock:
        state_snap = ctx.last_state
        cfg_snap = ctx.strategy_config
        db_snap = ctx.db
        ap = state_snap.active_planet() if state_snap is not None else None
        origin_coords = ap.coords if ap is not None else ""
        eff_min_fields = (
            max(50, safe_api_int(min_fields, 300))
            if min_fields is not None
            else max(50, safe_api_int(cfg_snap.min_colony_fields, 300))
        )
        own_coords_snap = own_planet_coords_for_ctx(ctx, state_snap)
        candidates = (
            db_snap.get_colony_candidates(
                origin_coords=origin_coords,
                min_fields_target=eff_min_fields,
                exclude_coords=own_coords_snap,
                include_discarded=include_discarded,
                limit=int(limit),
            )
            if db_snap is not None
            else []
        )
        discarded_count = len(db_snap.get_discarded_colony_coords()) if db_snap is not None else 0
        grade_counts: dict[str, int] = {"S": 0, "A+": 0, "A": 0, "B": 0, "C": 0, "D": 0, "?": 0}
        for cand in candidates:
            grade_key = str(cand.get("grade") or "?")
            grade_counts[grade_key] = grade_counts.get(grade_key, 0) + 1
        probed_count = sum(1 for cand in candidates if bool(cand.get("probed")))
        unprobed_count = len(candidates) - probed_count
        qualified_count = sum(1 for cand in candidates if bool(cand.get("meets_target")))
        colony_ships = 0
        spy_probes = 0
        planet_count = 0
        allocated_normal_planets = 0
        max_planets = max(1, safe_api_int(cfg_snap.max_planets, 11))
        if state_snap is not None:
            planet_count = len(state_snap.planets)
            allocated_normal_planets = count_allocated_normal_planets(state_snap)
            for planet in state_snap.planets:
                colony_ships += max(0, safe_api_int(planet.ships.get("s8", 0)))
                spy_probes += max(0, safe_api_int(planet.ships.get("s10", 0)))
        needed_colonies = max(0, max_planets - allocated_normal_planets)
        attempts_payload = build_colony_attempts_payload(ctx)
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "colony_candidates",
                "origin_coords": origin_coords,
                "min_colony_fields": eff_min_fields,
                "allow_colonize": bool(cfg_snap.allow_colonize),
                "allow_galaxy_scan": bool(cfg_snap.allow_galaxy_scan),
                "colony_ships": colony_ships,
                "spy_probes": spy_probes,
                "planet_count": planet_count,
                "allocated_normal_planets": allocated_normal_planets,
                "max_planets": max_planets,
                "needed_colonies": needed_colonies,
                "all_planets_allocated": allocated_normal_planets >= max_planets,
                "grade_counts": grade_counts,
                "probed_count": probed_count,
                "unprobed_count": unprobed_count,
                "qualified_count": qualified_count,
                "discarded_count": discarded_count,
                "candidates": candidates,
                **attempts_payload,
            }
        )
