"""Offer merging, raid-target enrichment, and multi-planet queue stamping helpers for StateCacheService."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import time

from backend.core import catalog as game_data
from backend.core.coords import normalize_coords
from backend.core.model import GameState, PlanetState, QueueJob, ResearchOffer
from backend.planner.heuristics import _resources_from_dict
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._helpers import (
    bounded_api_str,
    safe_api_float,
    safe_api_int,
    sanitize_finite_json,
)
from backend.runtime.bridge._helpers_sig import supports_kwarg


def _rebuild_suppressed_research_offers(
    state: GameState,
    prev_offers: Sequence[ResearchOffer],
) -> tuple[ResearchOffer, ...]:
    active_p = state.active_planet()
    has_lab = active_p is not None and active_p.building_level(8) >= 1
    rebuilt: list[ResearchOffer] = []
    for offer in prev_offers:
        st_low = str(offer.status_text or "").lower()
        was_suppressed = offer.active or any(
            tok in st_low for tok in ("in research", "queue busy", "busy", "active", "cancel", "unavailable")
        )
        if not was_suppressed:
            rebuilt.append(offer)
            continue
        unmet = game_data.missing_prerequisites(
            "research",
            offer.research_key,
            buildings=active_p.buildings if active_p is not None else {},
            researches=state.researches,
        )
        check_cost = offer.cost
        if check_cost.is_zero() and offer.research_key:
            unit = game_data.resolve_unit("research", offer.research_key)
            raw_levels = unit.get("levels") if isinstance(unit, Mapping) else None
            level_entry = raw_levels.get(offer.target_level) if isinstance(raw_levels, Mapping) else None
            raw_cost = level_entry.get("cost") if isinstance(level_entry, Mapping) else None
            if isinstance(raw_cost, dict):
                check_cost = _resources_from_dict(raw_cost)
        can_afford = active_p is not None and active_p.resources.can_afford(check_cost)
        if has_lab and not unmet and can_afford and not check_cost.is_zero():
            rebuilt.append(replace(offer, cost=check_cost, active=False, can_research=True, status_text="Available"))
        elif has_lab and not unmet and not can_afford:
            rebuilt.append(
                replace(
                    offer,
                    cost=check_cost,
                    active=False,
                    can_research=False,
                    status_text="Not enough resources",
                )
            )
        else:
            rebuilt.append(
                replace(
                    offer,
                    cost=check_cost,
                    active=False,
                    can_research=False,
                    status_text="Prerequisites unmet" if unmet else "Unavailable",
                )
            )
    return tuple(rebuilt)


def merge_cached_offers_into_state(prev: GameState | None, state: GameState) -> GameState:
    """Carry forward unchanged building, shipyard, and research offers from `prev` into `state`."""
    if prev is None:
        return state
    prev_by_id = {p.planet_id: p for p in prev.planets}
    prev_b_jobs = sum(1 for q in prev.active_queues if q.category == "building")
    cur_b_jobs = sum(1 for q in state.active_queues if q.category == "building")
    queues_same = prev_b_jobs == cur_b_jobs

    changed_planets = False
    new_planets: list[PlanetState] = []
    for planet in state.planets:
        old_p = prev_by_id.get(planet.planet_id)
        if old_p is None:
            new_planets.append(planet)
            continue
        b_offers = planet.building_offers
        s_offers = planet.ship_offers
        if (
            not b_offers
            and old_p.building_offers
            and planet.buildings == old_p.buildings
            and state.researches == prev.researches
            and (queues_same or planet.planet_id != state.active_planet_id)
        ):
            b_offers = old_p.building_offers
            changed_planets = True
        if (
            not s_offers
            and old_p.ship_offers
            and planet.building_level(9) == old_p.building_level(9)
            and state.researches == prev.researches
        ):
            s_offers = old_p.ship_offers
            changed_planets = True
        if b_offers is not planet.building_offers or s_offers is not planet.ship_offers:
            new_planets.append(replace(planet, building_offers=b_offers, ship_offers=s_offers))
        else:
            new_planets.append(planet)

    prev_b8 = {p.planet_id: p.building_level(8) for p in prev.planets}
    cur_b8 = {p.planet_id: p.building_level(8) for p in state.planets}
    res_offers = state.research_offers
    changed_state = False
    if (
        not res_offers
        and prev.research_offers
        and state.researches == prev.researches
        and state.active_planet_id == prev.active_planet_id
        and cur_b8 == prev_b8
    ):
        if any(q.category == "research" for q in state.active_queues):
            res_offers = prev.research_offers
        elif any(o.active for o in prev.research_offers):
            res_offers = _rebuild_suppressed_research_offers(state, prev.research_offers)
        else:
            res_offers = prev.research_offers
        changed_state = True
    if changed_planets or changed_state:
        return replace(
            state,
            planets=tuple(new_planets) if changed_planets else state.planets,
            research_offers=res_offers,
        )
    return state


def enrich_raid_target_rows(
    ctx: BridgeContext,
    rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Attach per-target combat history and in-memory target cooldown blocks onto raid target rows."""
    if not rows:
        return ()
    per_target: Mapping[str, object] = {}
    if ctx.db is not None:
        get_stats = ctx.db.get_raid_stats
        stats = (
            get_stats(include_telemetry_summary=False)
            if supports_kwarg(get_stats, "include_telemetry_summary")
            else get_stats()
        )
        if isinstance(stats, Mapping):
            raw_pt = stats.get("per_target")
            if isinstance(raw_pt, Mapping):
                per_target = raw_pt

    enriched: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, object] = dict(raw)
        coords = normalize_coords(str(item.get("coords") or item.get("target_coords") or ""))
        if coords and coords in ctx.target_blocks:
            mem_until, mem_reason = ctx.target_blocks[coords]
            if mem_until > safe_api_float(item.get("blocked_until_ts", 0.0)):
                item["blocked_until_ts"] = round(float(mem_until), 3)
                item["block_reason"] = bounded_api_str(mem_reason, default="")
        item["has_debris"] = bool(item.get("has_debris", False))
        item["blocked_until_ts"] = safe_api_float(item.get("blocked_until_ts", 0.0))
        item["block_reason"] = bounded_api_str(item.get("block_reason") or "", default="")
        item["bash_hits"] = game_data.cap_json_int(safe_api_int(item.get("bash_hits", 0)))
        item["last_blocked_ts"] = safe_api_float(item.get("last_blocked_ts", 0.0))
        pt = per_target.get(coords) if coords else None
        if isinstance(pt, Mapping) and safe_api_int(pt.get("attempts", 0)) > 0:
            avg_loot = pt.get("avg_loot")
            tot_loot = pt.get("total_loot")
            last_loot = pt.get("last_loot")
            item.update(
                {
                    "attack_attempts": game_data.cap_json_int(safe_api_int(pt.get("attempts", 0))),
                    "attack_wins": game_data.cap_json_int(safe_api_int(pt.get("wins", 0))),
                    "attack_losses": game_data.cap_json_int(safe_api_int(pt.get("losses", 0))),
                    "attack_success_rate": safe_api_float(pt.get("success_rate", 0.0)),
                    "avg_attack_loot": sanitize_finite_json(dict(avg_loot)) if isinstance(avg_loot, Mapping) else {},
                    "avg_attack_loot_spice_eq": safe_api_float(pt.get("avg_loot_spice_eq", 0.0)),
                    "total_attack_loot": sanitize_finite_json(dict(tot_loot)) if isinstance(tot_loot, Mapping) else {},
                    "total_attack_loot_spice_eq": safe_api_float(pt.get("total_loot_spice_eq", 0.0)),
                    "recency_weighted_loot_spice_eq": safe_api_float(pt.get("recency_weighted_loot_spice_eq", 0.0)),
                    "last_attack_outcome": bounded_api_str(pt.get("last_outcome") or "", default=""),
                    "last_attack_loot": sanitize_finite_json(dict(last_loot)) if isinstance(last_loot, Mapping) else {},
                    "last_attack_loot_spice_eq": safe_api_float(pt.get("last_loot_spice_eq", 0.0)),
                    "last_attack_ts": safe_api_float(pt.get("last_attacked_ts", 0.0)),
                }
            )
        else:
            item.update(
                {
                    "attack_attempts": 0,
                    "attack_wins": 0,
                    "attack_losses": 0,
                    "attack_success_rate": None,
                    "avg_attack_loot": None,
                    "avg_attack_loot_spice_eq": None,
                    "total_attack_loot": None,
                    "total_attack_loot_spice_eq": None,
                    "recency_weighted_loot_spice_eq": None,
                    "last_attack_outcome": "",
                    "last_attack_loot": None,
                    "last_attack_loot_spice_eq": None,
                    "last_attack_ts": 0.0,
                }
            )
        enriched.append(item)
    return tuple(enriched)


def carry_forward_prev_queues_and_metadata(
    prev: GameState | None,
    state: GameState,
    *,
    fetched_buildings: bool,
    fetched_research: bool,
    fetched_home: bool,
    observed_active_planet: bool,
) -> GameState:
    """Preserve prior active-planet ID, tutorial/alerts, and multi-slot queues across partial polls."""
    if prev is not None and (not fetched_home or not observed_active_planet):
        planet_ids = {p.planet_id for p in state.planets}
        active_pid = state.active_planet_id
        if not observed_active_planet and prev.active_planet_id is not None and prev.active_planet_id in planet_ids:
            active_pid = prev.active_planet_id
        tutorial = state.tutorial
        speedup = state.speedup_factor
        newbie = state.newbie_protection
        alerts = state.fleet_alerts
        queues = state.active_queues
        if not fetched_home:
            if tutorial is None and prev.tutorial is not None:
                tutorial = prev.tutorial
            if speedup is None and prev.speedup_factor is not None:
                speedup = prev.speedup_factor
            if not newbie and prev.newbie_protection:
                newbie = True
            if not alerts and prev.fleet_alerts:
                alerts = prev.fleet_alerts
            if prev.active_queues:
                refreshed_cats: set[str] = set()
                if fetched_buildings:
                    refreshed_cats.add("building")
                if fetched_research and state.research_offers:
                    refreshed_cats.add("research")
                cur_cats = {q.category for q in queues} | refreshed_cats
                now_wall = time.time()
                extra_q = [
                    q
                    for q in prev.active_queues
                    if q.category not in cur_cats
                    and not (
                        q.category == "building"
                        and (
                            (q.finish_ts is not None and safe_api_float(q.finish_ts, 0.0) <= now_wall)
                            or (
                                q.finish_ts is None
                                and q.remaining_seconds is not None
                                and safe_api_float(q.remaining_seconds, 0.0) <= 0.0
                            )
                        )
                    )
                ]
                if extra_q:
                    queues = tuple(list(queues) + extra_q)
        state = replace(
            state,
            active_planet_id=active_pid,
            tutorial=tutorial,
            speedup_factor=speedup,
            newbie_protection=newbie,
            fleet_alerts=alerts,
            active_queues=queues,
        )

    if prev is not None and not fetched_buildings and prev.active_queues:
        state = _preserve_tail_building_slots(prev, state)
    return _stamp_and_merge_colony_building_queues(
        prev, state, fetched_buildings=fetched_buildings, fetched_home=fetched_home
    )


def _preserve_tail_building_slots(prev: GameState, state: GameState) -> GameState:
    ap_cur = state.active_planet()
    ap_prev = prev.active_planet()
    cur_norm_c = normalize_coords(ap_cur.coords or "") if ap_cur is not None else ""
    prev_norm_c = normalize_coords(ap_prev.coords or "") if ap_prev is not None else ""
    cur_b_jobs = [
        q
        for q in state.active_queues
        if q.category == "building"
        and (not normalize_coords(q.origin_coords) or normalize_coords(q.origin_coords) == cur_norm_c)
    ]
    prev_b_jobs = [
        q
        for q in prev.active_queues
        if q.category == "building"
        and ((normalize_coords(q.origin_coords) or prev_norm_c) == cur_norm_c if cur_norm_c else True)
    ]
    if len(cur_b_jobs) == 1 and len(prev_b_jobs) > 1:
        c0, p0 = cur_b_jobs[0], prev_b_jobs[0]
        same_head = (c0.building_id is not None and c0.building_id == p0.building_id) or (
            c0.target_level is not None and c0.target_level == p0.target_level and p0.name.lower() in c0.name.lower()
        )
        now_wall = time.time()
        tail_b = [
            pq for pq in prev_b_jobs[1:] if pq.queue_slot >= 2 and (pq.finish_ts is None or pq.finish_ts > now_wall)
        ]
        if same_head and tail_b:
            return replace(state, active_queues=tuple(list(state.active_queues) + tail_b))
    return state


def _stamp_and_merge_colony_building_queues(
    prev: GameState | None,
    state: GameState,
    *,
    fetched_buildings: bool,
    fetched_home: bool,
) -> GameState:
    ap_eff = state.active_planet()
    active_norm_coords = normalize_coords(ap_eff.coords or "") if ap_eff is not None else ""
    owned_coords = {normalize_coords(p.coords) for p in (state.planets or ()) if normalize_coords(p.coords)}
    stamped: list[QueueJob] = []
    changed = False
    stamp_active = bool(active_norm_coords and (fetched_buildings or fetched_home or prev is None))
    existing_slots: set[tuple[str, int]] = set()
    for job in state.active_queues or ():
        if job.category == "building":
            q_orig = normalize_coords(job.origin_coords)
            if not q_orig and stamp_active:
                job = replace(job, origin_coords=active_norm_coords)
                q_orig = active_norm_coords
                changed = True
            elif q_orig and q_orig != job.origin_coords:
                job = replace(job, origin_coords=q_orig)
                changed = True
            if q_orig:
                slot_sig = (q_orig, int(job.queue_slot or 1))
                if slot_sig in existing_slots:
                    changed = True
                    continue
                existing_slots.add(slot_sig)
        stamped.append(job)

    if prev is not None and prev.active_queues and active_norm_coords:
        now_wall = time.time()
        ap_prev = prev.active_planet()
        prev_active_norm = normalize_coords(ap_prev.coords or "") if ap_prev is not None else ""
        existing_sigs = {
            (
                normalize_coords(q.origin_coords),
                str(q.name or "").strip().lower(),
                q.target_level,
                int(q.queue_slot or 1),
            )
            for q in stamped
            if q.category == "building"
        }
        for pq in prev.active_queues:
            if pq.category != "building":
                continue
            pq_orig = normalize_coords(pq.origin_coords) or prev_active_norm
            if not pq_orig or pq_orig == active_norm_coords:
                continue
            if owned_coords and pq_orig not in owned_coords:
                continue
            if pq.finish_ts is not None:
                if safe_api_float(pq.finish_ts, 0.0) <= now_wall:
                    continue
            elif pq.remaining_seconds is not None and safe_api_float(pq.remaining_seconds, 0.0) <= 0.0:
                continue
            slot_sig_b = (pq_orig, int(pq.queue_slot or 1))
            sig_b = (pq_orig, str(pq.name or "").strip().lower(), pq.target_level, int(pq.queue_slot or 1))
            if slot_sig_b in existing_slots or sig_b in existing_sigs:
                continue
            existing_slots.add(slot_sig_b)
            existing_sigs.add(sig_b)
            stamped.append(replace(pq, origin_coords=pq_orig) if pq.origin_coords != pq_orig else pq)
            changed = True

    return replace(state, active_queues=tuple(stamped)) if changed else state
