"""Live in-progress construction, research, and fleet-arrival extraction for ``build_path_problem``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import re

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int, coerce_optional_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.planner.ai._problem_queues import (
    parse_friendly_inbound_fleet_job,
    resolve_untagged_building_job_planet,
)
from spaceinvasion.planner.ai._types import (
    LEVEL_RE,
    TIMER_RE,
    GameStateLike,
    PlanetLike,
    extract_building_job_id_and_level,
    is_active_construction_status,
    is_queue_job_expired,
    parse_queue_remaining_seconds,
    safe_speedup,
)
from spaceinvasion.planner.ai.telemetry import fmt_path_time
from spaceinvasion.planner.config_optimizer import (
    RESOURCE_KEYS,
    build_monotonic_base_times,
    building_time_divisor,
    research_catalog,
    research_time_divisor,
)


def _build_job_row(
    p_entry: Mapping[str, object], p_idx: int, bid: int, bkey: str, tgt_lvl: int, rem_sec: float
) -> dict[str, object]:
    return {
        "kind": "build",
        "planet": p_idx,
        "planet_id": coerce_int(p_entry["planet_id"], 0),
        "planet_coords": str(p_entry["coords"]),
        "planet_name": str(p_entry["name"]),
        "key": bkey,
        "name": str(game_data.BUILDING_BY_ID[bid]["name"]),
        "level_from": int(tgt_lvl - 1),
        "level_to": int(tgt_lvl),
        "queue": p_idx,
        "remaining_seconds": round(rem_sec, 2),
        "remaining_hours": rem_sec / 3600.0,
        "remaining_text": fmt_path_time(rem_sec / 3600.0),
    }


def _research_job_row(rkey: str, tgt: int, queue_idx: int, rem_sec: float) -> dict[str, object]:
    return {
        "kind": "research",
        "planet": -1,
        "planet_id": 0,
        "planet_coords": "account",
        "planet_name": "Account",
        "key": rkey,
        "name": str(game_data.RESEARCH_BY_ID[rkey]["name"]),
        "level_from": int(tgt - 1),
        "level_to": int(tgt),
        "queue": queue_idx,
        "remaining_seconds": round(rem_sec, 2),
        "remaining_hours": rem_sec / 3600.0,
        "remaining_text": fmt_path_time(rem_sec / 3600.0),
    }


def _extract_in_progress_builds(
    state: GameStateLike,
    problem_planets: Sequence[Mapping[str, object]],
    idx_by_pid: Mapping[int, int],
    idx_by_coords: Mapping[str, int],
    *,
    state_ts: float,
    speedup: float,
    base_hours_fn: Callable[[str, int], float],
) -> list[dict[str, object]]:
    active_p, all_live = state.active_planet(), list(state.planets)
    live_by_pid = {int(p.planet_id): p for p in all_live if int(p.planet_id) > 0}
    builds_map: dict[tuple[int, str, int], dict[str, object]] = {}

    for job in state.active_queues:
        if str(job.category or "").strip().lower() != "building" or is_queue_job_expired(job, state_ts):
            continue
        bid, parsed_tgt = extract_building_job_id_and_level(job)
        origin = str(job.origin_coords or "").strip()
        p_idx = idx_by_coords.get(origin) if origin else None
        if p_idx is None and origin:
            p_idx = idx_by_coords.get(normalize_coords(origin))
        if p_idx is None and not origin:
            matched_p = resolve_untagged_building_job_planet(bid, parsed_tgt, all_live, active_p)
            if matched_p is not None:
                p_idx = idx_by_pid.get(int(matched_p.planet_id))
        if p_idx is None or bid is None or bid not in game_data.BUILDING_BY_ID:
            continue
        p_entry = problem_planets[p_idx]
        lvls: Mapping[str, object] = raw_l if isinstance((raw_l := p_entry.get("levels")), Mapping) else {}
        bkey = f"b{bid}"
        built = coerce_int(lvls.get(bkey, 0), 0)
        tgt_lvl = parsed_tgt
        if tgt_lvl is None or tgt_lvl <= built:
            m_lvl = LEVEL_RE.search(str(job.name or ""))
            tgt_lvl = max(built + 1, int(m_lvl.group(1))) if m_lvl else (built + 1)
        if tgt_lvl <= built:
            continue
        div = float(building_time_divisor(coerce_int(lvls.get("b7", 0), 0), coerce_int(lvls.get("b17", 0), 0), speedup))
        fallback_sec = max(1.0, (base_hours_fn(bkey, tgt_lvl) / max(1e-9, div)) * 3600.0)
        rem_sec = max(1.0, parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec))
        dedup_k = (p_idx, bkey, int(tgt_lvl))
        prev = builds_map.get(dedup_k)
        if prev is None or rem_sec > coerce_finite_float(prev.get("remaining_seconds"), 0.0):
            builds_map[dedup_k] = _build_job_row(p_entry, p_idx, bid, bkey, tgt_lvl, rem_sec)

    for p_entry in problem_planets:
        p_idx = coerce_int(p_entry["index"], 0)
        live_p = live_by_pid.get(coerce_int(p_entry["planet_id"], 0))
        if live_p is None:
            continue
        lvls = raw_l if isinstance((raw_l := p_entry.get("levels")), Mapping) else {}
        for off in live_p.building_offers:
            if not is_active_construction_status(off.status_text):
                continue
            bid = coerce_optional_int(off.building_id, None)
            if bid is None or bid not in game_data.BUILDING_BY_ID:
                continue
            bkey = f"b{bid}"
            built = coerce_int(lvls.get(bkey, 0), 0)
            tgt_lvl = max(built + 1, coerce_int(off.target_level, built + 1))
            if (p_idx, bkey, int(tgt_lvl)) in builds_map:
                continue
            div = float(
                building_time_divisor(coerce_int(lvls.get("b7", 0), 0), coerce_int(lvls.get("b17", 0), 0), speedup)
            )
            fallback_sec = max(1.0, (base_hours_fn(bkey, tgt_lvl) / max(1e-9, div)) * 3600.0)
            builds_map[(p_idx, bkey, int(tgt_lvl))] = _build_job_row(p_entry, p_idx, bid, bkey, tgt_lvl, fallback_sec)

    return sorted(
        builds_map.values(),
        key=lambda r: (
            coerce_int(r["planet"], 0),
            coerce_finite_float(r["remaining_seconds"], 0.0),
            coerce_int(r["level_to"], 0),
        ),
    )


def _extract_in_progress_research(
    state: GameStateLike,
    problem_planets: Sequence[Mapping[str, object]],
    *,
    state_ts: float,
    speedup: float,
    research_now: Mapping[str, int],
    base_hours_fn: Callable[[str, int], float],
    lab_planet_index: int,
) -> list[dict[str, object]]:
    name_to_rkey = {game_data.normalize_unit_key(str(info["name"])): str(rkey) for rkey, info in research_catalog()}
    lab_b8 = 0
    if 0 <= lab_planet_index < len(problem_planets) and isinstance(
        (lvls := problem_planets[lab_planet_index].get("levels")), Mapping
    ):
        lab_b8 = coerce_int(lvls.get("b8", 0), 0)
    res_div = float(research_time_divisor(lab_b8, speedup))
    res_map: dict[tuple[str, int], dict[str, object]] = {}

    for job in state.active_queues:
        if str(job.category or "").strip().lower() != "research":
            continue
        raw_name = str(job.name or "")
        clean = re.sub(
            r"\b(?:level|lvl|l)\s*\d+\b", "", TIMER_RE.sub("", re.sub(r"\(.*?\)", "", raw_name)), flags=re.IGNORECASE
        ).strip()
        rkey = name_to_rkey.get(game_data.normalize_unit_key(clean))
        if not rkey or rkey not in game_data.RESEARCH_BY_ID:
            continue
        cur = max(0, int(research_now.get(rkey, 0)))
        tgt = coerce_optional_int(job.target_level, None)
        if tgt is None or tgt <= cur:
            m_lvl = LEVEL_RE.search(raw_name)
            tgt = max(cur + 1, int(m_lvl.group(1))) if m_lvl else (cur + 1)
        if tgt <= cur:
            continue
        fallback_sec = max(1.0, (base_hours_fn(rkey, tgt) / max(1e-9, res_div)) * 3600.0)
        rem_sec = max(1.0, parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec))
        dedup_k = (rkey, int(tgt))
        prev = res_map.get(dedup_k)
        if prev is None or rem_sec > coerce_finite_float(prev.get("remaining_seconds"), 0.0):
            res_map[dedup_k] = _research_job_row(rkey, tgt, len(problem_planets), rem_sec)

    for roff in state.research_offers:
        if not roff.active:
            continue
        rkey = str(roff.research_key or "").strip()
        if not rkey or rkey not in game_data.RESEARCH_BY_ID:
            continue
        cur = max(0, int(research_now.get(rkey, 0)))
        tgt = max(cur + 1, coerce_int(roff.target_level, cur + 1))
        if (rkey, int(tgt)) in res_map:
            continue
        fallback_sec = max(1.0, (base_hours_fn(rkey, tgt) / max(1e-9, res_div)) * 3600.0)
        res_map[(rkey, int(tgt))] = _research_job_row(rkey, tgt, len(problem_planets), fallback_sec)

    return sorted(
        res_map.values(),
        key=lambda r: (coerce_finite_float(r["remaining_seconds"], 0.0), str(r["key"]), coerce_int(r["level_to"], 0)),
    )


def _extract_live_in_progress_state(
    state: GameStateLike,
    problem_planets: Sequence[Mapping[str, object]],
    *,
    speedup: float,
    research_now: Mapping[str, int],
    base_hours_fn: Callable[[str, int], float],
    lab_planet_index: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    """Extract live in-progress buildings, research, and incoming friendly fleet cargo from ``state``."""
    state_ts = max(0.0, coerce_finite_float(state.timestamp, 0.0))
    idx_by_pid: dict[int, int] = {}
    idx_by_coords: dict[str, int] = {}
    for entry in problem_planets:
        p_idx, pid = coerce_int(entry["index"], 0), coerce_int(entry["planet_id"], 0)
        if pid > 0:
            idx_by_pid[pid] = p_idx
        c = str(entry.get("coords") or "").strip()
        if c and not c.startswith("EMPTY:"):
            idx_by_coords[c] = p_idx
            idx_by_coords[normalize_coords(c)] = p_idx

    builds = _extract_in_progress_builds(
        state,
        problem_planets,
        idx_by_pid,
        idx_by_coords,
        state_ts=state_ts,
        speedup=speedup,
        base_hours_fn=base_hours_fn,
    )
    research = _extract_in_progress_research(
        state,
        problem_planets,
        state_ts=state_ts,
        speedup=speedup,
        research_now=research_now,
        base_hours_fn=base_hours_fn,
        lab_planet_index=lab_planet_index,
    )
    arrivals: list[dict[str, object]] = []
    for job in state.active_queues:
        parsed = parse_friendly_inbound_fleet_job(
            job, idx_by_coords, state_ts=state_ts, skip_expired=False, strict_hostile_text=False, use_last_coords=False
        )
        if parsed is None:
            continue
        dest_idx, cargo_tuple, is_returning, q_name = parsed
        rem_sec = max(1.0, parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=60.0))
        p_entry = problem_planets[int(dest_idx)]
        arrivals.append(
            {
                "planet": int(dest_idx),
                "planet_id": coerce_int(p_entry["planet_id"], 0),
                "planet_coords": str(p_entry["coords"]),
                "planet_name": str(p_entry["name"]),
                "mission": q_name,
                "returning": bool(is_returning),
                "remaining_seconds": round(rem_sec, 2),
                "remaining_hours": rem_sec / 3600.0,
                "remaining_text": fmt_path_time(rem_sec / 3600.0),
                "cargo": {r: int(round(cargo_tuple[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
                "cargo_tuple": cargo_tuple,
            }
        )
    arrivals.sort(key=lambda r: (coerce_finite_float(r["remaining_seconds"], 0.0), coerce_int(r["planet"], 0)))
    return builds, research, arrivals


def _cached_base_hours(cache: dict[str, dict[int, float]], key: str, level: int) -> float:
    if key not in cache:
        info = game_data.BUILDING_BY_ID[int(key[1:])] if key.startswith("b") else game_data.RESEARCH_BY_ID[key]
        lmap = info.get("levels") or {}
        cache[key] = build_monotonic_base_times(lmap, max(int(info.get("max_table_level") or len(lmap)), level))
    return float(cache[key].get(level, 0.0)) / 3600.0


def compute_in_progress_build_rem_by_planet(state: GameStateLike | None) -> dict[int, float]:
    """Return ``{planet_id: max_remaining_seconds}`` for active building jobs per normal colony."""
    if state is None:
        return {}
    state_ts, speedup = max(0.0, coerce_finite_float(state.timestamp, 0.0)), safe_speedup(state, {})
    all_live = list(state.planets)
    normal_planets = [p for p in all_live if normalize_planet_type(p) == "normal"]
    if not normal_planets:
        return {}
    active_p = state.active_planet()
    by_coords: dict[str, PlanetLike] = {}
    for p in normal_planets:
        if c := str(p.coords or "").strip():
            by_coords[c] = p
            by_coords[normalize_coords(c)] = p
    base_cache: dict[str, dict[int, float]] = {}
    rem_by_pid: dict[int, float] = {}
    seen_jobs: set[tuple[int, int, int]] = set()
    for job in state.active_queues:
        if str(job.category or "").strip().lower() != "building" or is_queue_job_expired(job, state_ts):
            continue
        bid, parsed_tgt = extract_building_job_id_and_level(job)
        origin = str(job.origin_coords or "").strip()
        target_p = by_coords.get(origin) or (by_coords.get(normalize_coords(origin)) if origin else None)
        if target_p is None and not origin:
            matched = resolve_untagged_building_job_planet(bid, parsed_tgt, all_live, active_p)
            if matched is not None and normalize_planet_type(matched) == "normal":
                target_p = matched
        if target_p is None:
            continue
        pid, fallback_sec, tgt_lvl = int(target_p.planet_id), 60.0, parsed_tgt
        if bid is not None and bid in game_data.BUILDING_BY_ID:
            built = target_p.building_level(bid)
            if tgt_lvl is None or tgt_lvl <= built:
                m_lvl = LEVEL_RE.search(str(job.name or ""))
                tgt_lvl = max(built + 1, int(m_lvl.group(1))) if m_lvl else (built + 1)
            div = float(building_time_divisor(target_p.building_level(7), target_p.building_level(17), speedup))
            fallback_sec = max(1.0, (_cached_base_hours(base_cache, f"b{bid}", tgt_lvl) / max(1e-9, div)) * 3600.0)
            seen_jobs.add((pid, int(bid), int(tgt_lvl)))
        rem_sec = max(1.0, parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec))
        rem_by_pid[pid] = max(rem_by_pid.get(pid, 0.0), round(rem_sec, 2))
    for p in normal_planets:
        pid = int(p.planet_id)
        for off in p.building_offers:
            if not is_active_construction_status(off.status_text):
                continue
            bid = coerce_optional_int(off.building_id, None)
            if bid is not None and bid in game_data.BUILDING_BY_ID:
                built = p.building_level(bid)
                tgt_lvl = max(built + 1, coerce_int(off.target_level, built + 1))
                if (pid, int(bid), int(tgt_lvl)) in seen_jobs:
                    continue
                div = float(building_time_divisor(p.building_level(7), p.building_level(17), speedup))
                sec = max(1.0, (_cached_base_hours(base_cache, f"b{bid}", tgt_lvl) / max(1e-9, div)) * 3600.0)
                rem_by_pid[pid] = max(rem_by_pid.get(pid, 0.0), round(sec, 2))
            elif pid not in rem_by_pid:
                rem_by_pid[pid] = 60.0
    return rem_by_pid


def compute_in_progress_research_rem_seconds(state: GameStateLike | None) -> float:
    """Return max remaining seconds for any active global research job (0.0 if idle)."""
    if state is None:
        return 0.0
    state_ts, speedup = max(0.0, coerce_finite_float(state.timestamp, 0.0)), safe_speedup(state, {})
    non_raid = [p for p in state.planets if normalize_planet_type(p) != "raid"]
    res_div = float(research_time_divisor(max((p.building_level(8) for p in non_raid), default=0), speedup))
    name_to_rkey = {game_data.normalize_unit_key(str(info["name"])): str(rkey) for rkey, info in research_catalog()}
    base_cache: dict[str, dict[int, float]] = {}
    max_rem, seen_res = 0.0, set[tuple[str, int]]()
    for job in state.active_queues:
        if str(job.category or "").strip().lower() != "research" or is_queue_job_expired(job, state_ts):
            continue
        raw_name = str(job.name or "")
        clean = re.sub(
            r"\b(?:level|lvl|l)\s*\d+\b", "", TIMER_RE.sub("", re.sub(r"\(.*?\)", "", raw_name)), flags=re.IGNORECASE
        ).strip()
        rkey = name_to_rkey.get(game_data.normalize_unit_key(clean))
        fallback_sec = 60.0
        if rkey and rkey in game_data.RESEARCH_BY_ID:
            cur = max(0, int(state.researches.get(rkey, 0) or 0))
            tgt = coerce_optional_int(job.target_level, None)
            if tgt is None or tgt <= cur:
                m_lvl = LEVEL_RE.search(raw_name)
                tgt = max(cur + 1, int(m_lvl.group(1))) if m_lvl else (cur + 1)
            fallback_sec = max(1.0, (_cached_base_hours(base_cache, rkey, tgt) / max(1e-9, res_div)) * 3600.0)
            seen_res.add((rkey, int(tgt)))
        max_rem = max(
            max_rem, max(1.0, parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec))
        )
    for roff in state.research_offers:
        if not roff.active:
            continue
        rkey = str(roff.research_key or "").strip()
        if rkey and rkey in game_data.RESEARCH_BY_ID:
            cur = max(0, int(state.researches.get(rkey, 0) or 0))
            tgt = max(cur + 1, coerce_int(roff.target_level, cur + 1))
            if (rkey, int(tgt)) not in seen_res:
                max_rem = max(
                    max_rem, max(1.0, (_cached_base_hours(base_cache, rkey, tgt) / max(1e-9, res_div)) * 3600.0)
                )
        elif max_rem <= 0.0:
            max_rem = 60.0
    return round(max_rem, 2)
