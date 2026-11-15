"""Galaxy sector priority scoring helpers for smart scanning."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import time
from typing import Any, Optional

from backend.combat._scanner_ingest import (
    ensure_sector_entry as _ensure_sector_entry,
)
from backend.combat._scanner_ingest import (
    extract_own_colonies_and_coords as extract_own_colonies_and_coords,
)
from backend.combat._scanner_ingest import (
    ingest_raid_history_fallback as _ingest_raid_history_fallback,
)
from backend.combat._scanner_ingest import (
    ingest_raid_targets as _ingest_raid_targets,
)
from backend.combat._scanner_ingest import (
    ingest_scanned_systems as _ingest_scanned_systems,
)
from backend.combat._scanner_ingest import (
    ingest_sector_intel_mapping as _ingest_sector_intel_mapping,
)
from backend.combat._scanner_ingest import (
    parse_gal_sys_pair as parse_gal_sys_pair,
)
from backend.combat._scanner_ingest import (
    record_owner_raid as _record_owner_raid,
)
from backend.combat._scanner_ingest import (
    resolve_active_galaxies as _resolve_active_galaxies,
)
from backend.combat._scanner_score_helpers import (
    build_unscanned_sector as _build_unscanned_sector,
)
from backend.combat._scanner_score_helpers import (
    compute_owner_affinity as _compute_owner_affinity,
)
from backend.combat._scanner_score_helpers import (
    format_age_short as format_age_short,
)
from backend.combat._scanner_score_helpers import (
    format_scanned_reason as _format_scanned_reason,
)
from backend.combat._scanner_score_helpers import (
    sector_distance as _sector_distance,
)
from backend.core.catalog import safe_finite_float
from backend.core.coercion import coerce_int


def _score_scanned_sector(
    skey: str,
    gal: int,
    sys_num: int,
    dist: int,
    same_gal: bool,
    ring_offset: int,
    sec: dict[str, Any],
    owner_stats: Mapping[str, Mapping[str, Any]],
    near_score: float,
    eff_near_w: float,
    eff_attack_w: float,
    eff_fresh_s: float,
    explore_first: bool,
    collect_colonies: bool,
    eff_now: float,
) -> dict[str, Any]:
    last_ts = safe_finite_float(sec["last_scanned_ts"], 0.0)
    scanned = last_ts > 0.0
    age_s = round(max(0.0, eff_now - last_ts), 3) if scanned else None
    is_fresh = bool(scanned and age_s is not None and age_s < eff_fresh_s)
    direct_raids = int(sec["direct_raid_count"])
    direct_recency = float(sec["direct_recency_raid_score"])
    direct_loot = float(sec["direct_loot_spice_eq"])
    debris_cnt, inact_cnt, spacia_cnt = int(sec["debris_count"]), int(sec["inactive_count"]), int(sec["spacia_count"])
    target_cnt = max(int(sec["target_count"]), inact_cnt + spacia_cnt)
    free_cnt, prime_free = int(sec["free_count"]), int(sec["prime_colony_free_count"])
    affinity_sig, owner_aff_raids, freq_owners = _compute_owner_affinity(sec, owner_stats)
    attack_score = eff_attack_w * (
        1.6 * math.log1p(max(0.0, max(direct_recency, 0.6 * direct_raids)))
        + 0.35 * math.log1p(max(0.0, direct_loot) / 5000.0)
        + 1.4 * affinity_sig
    )
    secondary_score = (
        min(3.0, 1.25 * debris_cnt)
        + min(2.5, 0.55 * inact_cnt + 0.75 * spacia_cnt)
        + (min(1.8, 0.45 * prime_free + 0.10 * min(6, free_cnt)) if collect_colonies else 0.0)
    )
    importance = 1.0 + near_score + attack_score + secondary_score
    staleness_factor = max(60.0, age_s if (scanned and age_s is not None) else (7.0 * 86400.0)) / 1800.0
    phase = "exploratory" if (explore_first and not scanned) else "targeted_refresh"
    reason = _format_scanned_reason(
        phase=phase,
        dist=dist,
        same_gal=same_gal,
        eff_near_w=eff_near_w,
        direct_raids=direct_raids,
        freq_owners=freq_owners,
        debris_cnt=debris_cnt,
        target_cnt=target_cnt,
        collect_colonies=collect_colonies,
        prime_free=prime_free,
        age_s=age_s,
    )
    return {
        "sector": skey,
        "galaxy": gal,
        "system": sys_num,
        "phase": phase,
        "scanned": scanned,
        "is_fresh": is_fresh,
        "score": round(importance * staleness_factor, 4),
        "importance": round(importance, 4),
        "near_score": round(near_score, 4),
        "attack_score": round(attack_score, 4),
        "secondary_score": round(secondary_score, 4),
        "staleness_factor": round(staleness_factor, 4),
        "distance": int(dist),
        "last_scanned_ts": round(last_ts, 3),
        "age_seconds": age_s,
        "direct_raid_count": direct_raids,
        "direct_loot_spice_eq": round(direct_loot, 2),
        "owner_affinity_raid_count": owner_aff_raids,
        "frequent_owners": freq_owners,
        "debris_count": debris_cnt,
        "target_count": target_cnt,
        "free_count": free_cnt,
        "reason": reason,
        "_same_gal": 0 if same_gal else 1,
        "_ring_offset": ring_offset,
    }


def _score_active_galaxies(
    active_galaxies: Sequence[int],
    eff_colonies: Sequence[tuple[int, int]],
    sectors_data: Mapping[str, dict[str, Any]],
    owner_stats: Mapping[str, Mapping[str, Any]],
    *,
    n_sys: int,
    rad_cap: int,
    eff_near_w: float,
    eff_attack_w: float,
    eff_fresh_s: float,
    explore_first: bool,
    collect_colonies: bool,
    eff_now: float,
) -> list[dict[str, Any]]:
    colony_gal_set = {cg for cg, _ in eff_colonies}
    primary_sys_by_gal: dict[int, int] = {}
    for cg, cs in eff_colonies:
        primary_sys_by_gal.setdefault(cg, cs)
    scored: list[dict[str, Any]] = []
    near_scale, default_staleness = eff_near_w * 2.5, (7.0 * 86400.0) / 1800.0
    col_int = [(int(cg), int(cs)) for cg, cs in eff_colonies]
    for gal in active_galaxies:
        anchor_sys, same_gal = primary_sys_by_gal.get(gal, eff_colonies[0][1]), gal in colony_gal_set
        same_gal_cols = tuple(cs for cg, cs in col_int if cg == gal)
        other_gal_cols = tuple((250 * abs(gal - cg), cs) for cg, cs in col_int if cg != gal)
        for sys_num in range(1, n_sys + 1):
            dist = _sector_distance(sys_num, same_gal_cols, other_gal_cols, n_sys)
            if rad_cap >= 0 and dist > rad_cap:
                continue
            skey = f"{gal:02d}:{sys_num:03d}"
            near_score = near_scale * ((1.0 / (1.0 + dist / 20.0)) if same_gal else (0.15 / (1.0 + dist / 200.0)))
            ring_offset = (sys_num - anchor_sys) % n_sys
            if (sec := sectors_data.get(skey)) is None:
                scored.append(
                    _build_unscanned_sector(
                        skey,
                        gal,
                        sys_num,
                        dist,
                        same_gal,
                        ring_offset,
                        near_score,
                        default_staleness,
                        eff_near_w,
                        explore_first,
                    )
                )
            else:
                scored.append(
                    _score_scanned_sector(
                        skey,
                        gal,
                        sys_num,
                        dist,
                        same_gal,
                        ring_offset,
                        sec,
                        owner_stats,
                        near_score,
                        eff_near_w,
                        eff_attack_w,
                        eff_fresh_s,
                        explore_first,
                        collect_colonies,
                        eff_now,
                    )
                )
    return scored


def _sort_and_clean_scored(scored: list[dict[str, Any]], *, explore_first: bool) -> list[dict[str, Any]]:
    scored.sort(
        key=(
            lambda it: (
                0 if not it["scanned"] else (1 if not it["is_fresh"] else 2),
                it["_same_gal"] if not it["scanned"] else 0,
                it["distance"] if not it["scanned"] else -it["score"],
                it["_ring_offset"] if not it["scanned"] else -it["importance"],
                it["distance"],
                it["galaxy"],
                it["system"],
            )
        )
        if explore_first
        else (
            lambda it: (
                0 if not it["is_fresh"] else 1,
                -it["score"],
                -it["importance"],
                it["distance"],
                it["galaxy"],
                it["system"],
            )
        )
    )
    for it in scored:
        it.pop("_same_gal", None)
        it.pop("_ring_offset", None)
    return scored


def score_galaxy_scan_sectors(
    *,
    own_coords: Sequence[str] | str = (),
    sector_intel: Optional[Mapping[str, Any]] = None,
    galaxies: Optional[Sequence[int]] = None,
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]] = None,
    raid_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    explore_first: bool = True,
    near_weight: float = 2.0,
    attack_weight: float = 3.0,
    collect_colonies: bool = True,
    min_freshness_seconds: float = 120.0,
    max_radius: Optional[int] = None,
    systems_per_galaxy: int = 400,
    now_ts: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Score and rank galaxy solar systems ``(1..400)`` for 2-phase smart scanning."""
    eff_now = (
        safe_finite_float(now_ts, 0.0)
        or (safe_finite_float(sector_intel.get("now_ts"), 0.0) if isinstance(sector_intel, Mapping) else 0.0)
        or time.time()
    )
    n_sys = max(1, coerce_int(systems_per_galaxy, 400))
    eff_near_w = max(0.0, min(20.0, safe_finite_float(near_weight, 2.0)))
    eff_attack_w = max(0.0, min(20.0, safe_finite_float(attack_weight, 3.0)))
    eff_fresh_s = max(0.0, safe_finite_float(min_freshness_seconds, 120.0))
    rad_cap = coerce_int(max_radius, -1) if max_radius is not None else -1
    own_colonies, own_norm_coords = extract_own_colonies_and_coords(own_coords, systems_per_galaxy=n_sys)
    active_galaxies = _resolve_active_galaxies(galaxies, own_colonies, sector_intel)
    sectors_data: dict[str, dict[str, Any]] = {}
    owner_stats: dict[str, dict[str, Any]] = {}
    if isinstance(sector_intel, Mapping):
        _ingest_sector_intel_mapping(sector_intel, sectors_data, owner_stats, n_sys)
    if scanned_systems is not None:
        _ingest_scanned_systems(scanned_systems, sectors_data, n_sys, eff_now)
    coord_owner, coord_spacia = _ingest_raid_targets(raid_targets, own_norm_coords, sectors_data, n_sys)
    if sector_intel is None:
        _ingest_raid_history_fallback(
            raid_stats,
            raid_history,
            own_norm_coords,
            coord_owner,
            coord_spacia,
            sectors_data,
            owner_stats,
            n_sys,
            eff_now,
        )
    scored = _score_active_galaxies(
        active_galaxies,
        own_colonies or [(active_galaxies[0], 1)],
        sectors_data,
        owner_stats,
        n_sys=n_sys,
        rad_cap=rad_cap,
        eff_near_w=eff_near_w,
        eff_attack_w=eff_attack_w,
        eff_fresh_s=eff_fresh_s,
        explore_first=explore_first,
        collect_colonies=collect_colonies,
        eff_now=eff_now,
    )
    return _sort_and_clean_scored(scored, explore_first=explore_first)


__all__ = [
    "_ensure_sector_entry",
    "_ingest_raid_history_fallback",
    "_ingest_raid_targets",
    "_ingest_scanned_systems",
    "_ingest_sector_intel_mapping",
    "_record_owner_raid",
    "_resolve_active_galaxies",
    "_score_scanned_sector",
    "extract_own_colonies_and_coords",
    "format_age_short",
    "parse_gal_sys_pair",
    "score_galaxy_scan_sectors",
]
