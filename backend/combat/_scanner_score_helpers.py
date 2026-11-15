"""Formatting, distance, owner-affinity, and unscanned-sector builders for smart galaxy scan."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any, Optional


def format_age_short(age_seconds: Optional[float]) -> str:
    if age_seconds is None or not math.isfinite(age_seconds):
        return "unscanned"
    sec = max(0, int(round(age_seconds)))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec / 3600.0:.1f}h" if sec < 86400 else f"{sec / 86400.0:.1f}d"


def sector_distance(
    sys_num: int,
    same_gal_cols: tuple[int, ...],
    other_gal_cols: tuple[tuple[int, int], ...],
    n_sys: int,
) -> int:
    best_d = 10**9
    for cs in same_gal_cols:
        delta = abs(sys_num - cs)
        best_d = min(best_d, delta, n_sys - delta)
    for g_pen, cs in other_gal_cols:
        delta = abs(sys_num - cs)
        best_d = min(best_d, g_pen + min(delta, n_sys - delta))
    return best_d if best_d < 10**9 else 0


def compute_owner_affinity(
    sec: Mapping[str, Any],
    owner_stats: Mapping[str, Mapping[str, Any]],
) -> tuple[float, int, list[str]]:
    if not sec["owner_planet_counts"]:
        return 0.0, 0, []
    entries = sorted(
        (
            (
                int(os_i["raid_count"]),
                float(os_i["loot_spice_eq"]),
                str(os_i["owner"] or sec["owner_display"].get(ok, ok)),
                int(pcnt),
            )
            for ok, pcnt in sec["owner_planet_counts"].items()
            if (os_i := owner_stats.get(ok)) is not None and int(os_i["raid_count"]) > 0
        ),
        key=lambda t: (-t[0], -t[1], t[2]),
    )
    affinity_sig = 0.0
    owner_aff_raids = 0
    freq_owners: list[str] = []
    for o_raids, o_loot, o_disp, pcnt in entries:
        owner_aff_raids += o_raids
        affinity_sig += (math.log1p(float(o_raids)) + 0.25 * math.log1p(max(0.0, o_loot) / 10000.0)) * (
            1.0 + 0.35 * max(0, pcnt - 1)
        )
        freq_owners.append(f"{o_disp}({o_raids})")
    return affinity_sig, owner_aff_raids, freq_owners


def format_scanned_reason(
    *,
    phase: str,
    dist: int,
    same_gal: bool,
    eff_near_w: float,
    direct_raids: int,
    freq_owners: Sequence[str],
    debris_cnt: int,
    target_cnt: int,
    collect_colonies: bool,
    prime_free: int,
    age_s: Optional[float],
) -> str:
    if phase == "exploratory":
        return f"unscanned sector (dist {dist})"
    tags = [
        "near d=" + str(dist)
        if (same_gal and dist <= 25 and eff_near_w > 0.0)
        else (f"d={dist}" if same_gal else f"cross-gal d={dist}")
    ]
    if direct_raids > 0:
        tags.append(f"raided {direct_raids}x")
    if freq_owners:
        tags.append("freq-target " + ",".join(freq_owners[:2]))
    if debris_cnt > 0:
        tags.append(f"debris x{debris_cnt}")
    elif target_cnt > 0:
        tags.append(f"targets x{target_cnt}")
    elif collect_colonies and prime_free > 0:
        tags.append(f"colony-slots x{prime_free}")
    tags.append(f"age {format_age_short(age_s)}")
    return ", ".join(tags)


def build_unscanned_sector(
    skey: str,
    gal: int,
    sys_num: int,
    dist: int,
    same_gal: bool,
    ring_offset: int,
    near_score: float,
    default_staleness: float,
    eff_near_w: float,
    explore_first: bool,
) -> dict[str, Any]:
    importance = 1.0 + near_score
    reason = (
        f"unscanned sector (dist {dist})"
        if explore_first
        else (
            f"near d={dist}, age never"
            if same_gal and dist <= 25 and eff_near_w > 0.0
            else (f"d={dist}, age never" if same_gal else f"cross-gal d={dist}, age never")
        )
    )
    return {
        "sector": skey,
        "galaxy": gal,
        "system": sys_num,
        "phase": "exploratory" if explore_first else "targeted_refresh",
        "scanned": False,
        "is_fresh": False,
        "score": round(importance * default_staleness, 4),
        "importance": round(importance, 4),
        "near_score": round(near_score, 4),
        "attack_score": 0.0,
        "secondary_score": 0.0,
        "staleness_factor": round(default_staleness, 4),
        "distance": dist,
        "last_scanned_ts": 0.0,
        "age_seconds": None,
        "direct_raid_count": 0,
        "direct_loot_spice_eq": 0.0,
        "owner_affinity_raid_count": 0,
        "frequent_owners": [],
        "debris_count": 0,
        "target_count": 0,
        "free_count": 0,
        "reason": reason,
        "_same_gal": 0 if same_gal else 1,
        "_ring_offset": ring_offset,
    }


__all__ = [
    "build_unscanned_sector",
    "compute_owner_affinity",
    "format_age_short",
    "format_scanned_reason",
    "sector_distance",
]
