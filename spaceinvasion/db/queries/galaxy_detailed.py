"""Detailed G1-G14 sector intel aggregation matching the HTTP dashboard & planner contract."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from spaceinvasion.core.catalog import cap_json_int
from spaceinvasion.db.models import EmpirePlanetHot, GalaxySectorHot
from spaceinvasion.db.queries._galaxy_detailed_helpers import (
    SYSTEMS_PER_GALAXY,
    OwnerAgg,
    SectorHotRow,
    SectorRaidAgg,
    aggregate_raids_for_sectors,
    merge_battle_reports,
    parse_hot_sector_maps,
)
from spaceinvasion.db.repos._helpers import normalize_coords, round_ts, to_int


def _build_sector_dict(
    gsk: tuple[int, int],
    *,
    hs: SectorHotRow | None,
    ra: SectorRaidAgg | None,
    extra_bo: dict[str, set[str]] | None,
    owner_display: dict[str, str],
    owner_agg: dict[str, OwnerAgg],
    eff_now: float,
) -> dict[str, object]:
    gal_i, sys_i = gsk
    skey = hs.sector_key if hs is not None else f"{gal_i:02d}:{sys_i:03d}"
    last_ts = hs.last_ts if hs is not None else 0.0
    scanned = last_ts > 0.0
    age_s = round(max(0.0, eff_now - last_ts), 3) if scanned else None

    merged_items: list[tuple[str, str, int]] = []
    if extra_bo:
        merged_ok: dict[str, int] = {ok: cnt for ok, _disp, cnt in (hs.owner_planet_counts if hs is not None else ())}
        for ok_b, cset in extra_bo.items():
            merged_ok[ok_b] = merged_ok.get(ok_b, 0) + len(cset)
        for ok in sorted(merged_ok):
            if merged_ok[ok] > 0:
                merged_items.append((ok, owner_display.get(ok, ok), merged_ok[ok]))
    elif hs is not None:
        merged_items = list(hs.owner_planet_counts)

    owners_list = (
        [disp for _ok, disp, _cnt in merged_items] if extra_bo else (list(hs.owners) if hs is not None else [])
    )
    opc_out = {disp: cnt for _ok, disp, cnt in merged_items}
    freq_owners: list[dict[str, object]] = []
    affinity_cnt = 0
    for ok, disp, pcnt in merged_items:
        oa = owner_agg.get(ok)
        if oa is not None and oa.raid_count > 0:
            affinity_cnt += oa.raid_count
            freq_owners.append(
                {
                    "owner": disp,
                    "raid_count": cap_json_int(oa.raid_count),
                    "planets_in_sector": cap_json_int(pcnt),
                    "loot_spice_eq": round(oa.loot_spice_eq, 2),
                }
            )
    if len(freq_owners) > 1:
        freq_owners.sort(key=lambda item: (-to_int(item["raid_count"], 0), str(item["owner"])))

    return {
        "sector": skey,
        "galaxy": cap_json_int(gal_i),
        "system": cap_json_int(sys_i),
        "scanned": scanned,
        "last_scanned_ts": last_ts,
        "age_seconds": age_s,
        "scan_count": hs.scan_count if hs is not None else 0,
        "occupied_count": hs.occupied_count if hs is not None else 0,
        "target_count": hs.target_count if hs is not None else 0,
        "inactive_count": hs.inactive_count if hs is not None else 0,
        "spacia_count": hs.spacia_count if hs is not None else 0,
        "debris_count": hs.debris_count if hs is not None else 0,
        "free_count": hs.free_count if hs is not None else 0,
        "prime_colony_free_count": hs.prime_colony_free_count if hs is not None else 0,
        "direct_raid_count": cap_json_int(ra.direct_raid_count) if ra is not None else 0,
        "direct_raid_wins": cap_json_int(ra.direct_raid_wins) if ra is not None else 0,
        "direct_raid_24h_count": cap_json_int(ra.direct_raid_24h_count) if ra is not None else 0,
        "direct_raid_7d_count": cap_json_int(ra.direct_raid_7d_count) if ra is not None else 0,
        "direct_recency_raid_score": round(ra.direct_recency_raid_score, 4) if ra is not None else 0.0,
        "direct_loot_spice_eq": round(ra.direct_loot_spice_eq, 2) if ra is not None else 0.0,
        "last_attacked_ts": round(ra.last_attacked_ts, 3) if ra is not None else 0.0,
        "owners": owners_list,
        "owner_planet_counts": opc_out,
        "frequent_owners": freq_owners,
        "owner_affinity_raid_count": cap_json_int(affinity_cnt),
    }


def _format_owner_stats(
    owner_agg: dict[str, OwnerAgg],
    owner_sectors_all: dict[str, set[str]],
) -> tuple[dict[str, int], dict[str, dict[str, object]]]:
    sorted_owners = sorted(
        owner_agg.items(),
        key=lambda kv: (-kv[1].raid_count, -kv[1].loot_spice_eq, kv[1].owner),
    )
    attack_counts: dict[str, int] = {}
    raid_stats: dict[str, dict[str, object]] = {}
    for okey, oa in sorted_owners:
        attack_counts[oa.owner] = cap_json_int(oa.raid_count)
        raid_stats[oa.owner] = {
            "owner": oa.owner,
            "raid_count": cap_json_int(oa.raid_count),
            "raid_wins": cap_json_int(oa.raid_wins),
            "raid_24h_count": cap_json_int(oa.raid_24h_count),
            "raid_7d_count": cap_json_int(oa.raid_7d_count),
            "recency_raid_score": round(oa.recency_raid_score, 4),
            "loot_spice_eq": round(oa.loot_spice_eq, 2),
            "last_attacked_ts": round(oa.last_attacked_ts, 3),
            "known_sectors": sorted(owner_sectors_all.get(okey, set())),
        }
    return attack_counts, raid_stats


def query_galaxy_sector_intel_detailed(
    session: Session,
    *,
    galaxies: Sequence[int] | None = None,
    now_ts: float | None = None,
    max_galaxies: int = 14,
) -> dict[str, object]:
    """Compute the full G1-G14 sector intel & player raid-affinity map in 4 SQL queries."""
    eff_now = round_ts(now_ts)
    eff_max_g = max(1, min(14, int(max_galaxies)))
    req_gals: list[int] = []
    if galaxies is not None:
        for g_raw in galaxies:
            gi = int(g_raw)
            if 1 <= gi <= eff_max_g and gi not in req_gals:
                req_gals.append(gi)

    hot_rows = list(session.execute(select(GalaxySectorHot)).scalars().all())
    own_coords = {
        nc
        for raw in session.execute(select(EmpirePlanetHot.coords).where(EmpirePlanetHot.is_active == 1)).scalars().all()
        if (nc := normalize_coords(str(raw or "")))
    }

    discovered_gals: set[int] = set()
    universe_scanned = 0
    for hr in hot_rows:
        g_i, s_i = int(hr.galaxy), int(hr.system)
        if 1 <= g_i <= eff_max_g:
            discovered_gals.add(g_i)
            if 1 <= s_i <= SYSTEMS_PER_GALAXY and float(hr.last_scanned_ts or 0.0) > 0.0:
                universe_scanned += 1
    active_galaxies = req_gals if req_gals else (sorted(discovered_gals) or [1])
    active_gal_set = set(active_galaxies)

    spacia_coords, coord_owner, owner_display, owner_sectors_all, active_sectors, explored_active = (
        parse_hot_sector_maps(hot_rows, own_coords, active_gal_set, eff_max_g)
    )
    battle_loot, battle_extra_owners = merge_battle_reports(
        session,
        own_coords=own_coords,
        spacia_coords=spacia_coords,
        coord_owner=coord_owner,
        owner_display=owner_display,
        owner_sectors_all=owner_sectors_all,
        active_gal_set=active_gal_set,
        eff_max_g=eff_max_g,
    )
    owner_agg, sector_agg = aggregate_raids_for_sectors(
        session,
        eff_now=eff_now,
        own_coords=own_coords,
        spacia_coords=spacia_coords,
        coord_owner=coord_owner,
        owner_display=owner_display,
        active_gal_set=active_gal_set,
        battle_loot_by_coord=battle_loot,
    )

    all_gsks = set(active_sectors) | set(sector_agg) | set(battle_extra_owners)
    sectors_out = {
        f"{gsk[0]:02d}:{gsk[1]:03d}": _build_sector_dict(
            gsk,
            hs=active_sectors.get(gsk),
            ra=sector_agg.get(gsk),
            extra_bo=battle_extra_owners.get(gsk),
            owner_display=owner_display,
            owner_agg=owner_agg,
            eff_now=eff_now,
        )
        for gsk in sorted(all_gsks)
    }
    owner_attack_counts, owner_raid_stats = _format_owner_stats(owner_agg, owner_sectors_all)
    total_sys = len(active_galaxies) * SYSTEMS_PER_GALAXY
    univ_total = eff_max_g * SYSTEMS_PER_GALAXY
    return {
        "now_ts": eff_now,
        "galaxies": [cap_json_int(g) for g in active_galaxies],
        "explored_systems": cap_json_int(explored_active),
        "unscanned_systems": cap_json_int(max(0, total_sys - explored_active)),
        "total_systems": cap_json_int(total_sys),
        "coverage_pct": round((explored_active / total_sys) * 100.0, 2) if total_sys > 0 else 0.0,
        "universe_explored_systems": cap_json_int(universe_scanned),
        "universe_total_systems": cap_json_int(univ_total),
        "universe_coverage_pct": round((universe_scanned / univ_total) * 100.0, 2) if univ_total > 0 else 0.0,
        "owner_attack_counts": owner_attack_counts,
        "owner_raid_stats": owner_raid_stats,
        "sectors": sectors_out,
    }


__all__ = ["query_galaxy_sector_intel_detailed"]
