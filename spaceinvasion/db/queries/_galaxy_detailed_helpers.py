"""Private aggregation helpers for `query_galaxy_sector_intel_detailed`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spaceinvasion.core.catalog import cap_json_int
from spaceinvasion.db.models import BattleReport, GalaxySectorHot, RaidHistory
from spaceinvasion.db.repos._helpers import (
    is_win_outcome,
    loads_json_dict,
    loads_json_list,
    normalize_coords,
    normalize_loot_dict,
    parse_coords_gsp,
    sanitize_text,
    spice_equivalent_from_counts,
    to_int,
)

SYSTEMS_PER_GALAXY = 400


@dataclass(frozen=True)
class SectorHotRow:
    """Decoded hot galaxy sector counters and owner maps."""

    sector_key: str
    last_ts: float
    scan_count: int
    occupied_count: int
    target_count: int
    inactive_count: int
    spacia_count: int
    debris_count: int
    free_count: int
    prime_colony_free_count: int
    owners: tuple[str, ...]
    owner_planet_counts: tuple[tuple[str, str, int], ...]


@dataclass
class OwnerAgg:
    """Mutable per-owner raid aggregation accumulator."""

    owner: str
    raid_count: int = 0
    raid_wins: int = 0
    raid_24h_count: int = 0
    raid_7d_count: int = 0
    recency_raid_score: float = 0.0
    loot_spice_eq: float = 0.0
    last_attacked_ts: float = 0.0


@dataclass
class SectorRaidAgg:
    """Mutable per-sector direct raid aggregation accumulator."""

    direct_raid_count: int = 0
    direct_raid_wins: int = 0
    direct_raid_24h_count: int = 0
    direct_raid_7d_count: int = 0
    direct_recency_raid_score: float = 0.0
    direct_loot_spice_eq: float = 0.0
    last_attacked_ts: float = 0.0


def parse_hot_sector_maps(
    hot_rows: Sequence[GalaxySectorHot],
    own_coords: set[str],
    active_gal_set: set[int],
    eff_max_g: int,
) -> tuple[
    set[str],
    dict[str, str],
    dict[str, str],
    dict[str, set[str]],
    dict[tuple[int, int], SectorHotRow],
    int,
]:
    """Decode `galaxy_sector_hot` rows into lookup maps and active-galaxy sector summaries."""
    spacia_coords: set[str] = set()
    coord_owner: dict[str, str] = {}
    owner_display: dict[str, str] = {}
    owner_sectors_all: dict[str, set[str]] = {}
    active_sectors: dict[tuple[int, int], SectorHotRow] = {}
    explored_active = 0

    for hr in hot_rows:
        g_i, s_i = int(hr.galaxy), int(hr.system)
        if not (1 <= g_i <= eff_max_g and 1 <= s_i <= SYSTEMS_PER_GALAXY):
            continue
        skey = f"{g_i:02d}:{s_i:03d}"
        last_ts = float(hr.last_scanned_ts or 0.0)
        for sc in loads_json_list(hr.spacia_coords_json):
            if sc:
                spacia_coords.add(str(sc))
        for nc_k, ov_raw in loads_json_dict(hr.coord_owners_json).items():
            ov = str(ov_raw or "")
            if nc_k and ov and nc_k not in own_coords and nc_k not in spacia_coords:
                coord_owner[nc_k] = ov
                ok = ov.lower()
                owner_display.setdefault(ok, ov)
                owner_sectors_all.setdefault(ok, set()).add(skey)
        if g_i not in active_gal_set:
            continue
        if last_ts > 0.0:
            explored_active += 1
        owners_tup = tuple(str(x) for x in loads_json_list(hr.owners_json))
        opc_items = tuple(
            (str(k).lower(), str(k), cnt)
            for k, v in loads_json_dict(hr.owner_planet_counts_json).items()
            if (cnt := to_int(v, 0)) > 0
        )
        active_sectors[(g_i, s_i)] = SectorHotRow(
            sector_key=skey,
            last_ts=round(last_ts, 3),
            scan_count=cap_json_int(max(0, int(hr.scan_count))),
            occupied_count=cap_json_int(max(0, int(hr.occupied_count))),
            target_count=cap_json_int(max(0, int(hr.target_count))),
            inactive_count=cap_json_int(max(0, int(hr.inactive_count))),
            spacia_count=cap_json_int(max(0, int(hr.spacia_count))),
            debris_count=cap_json_int(max(0, int(hr.debris_count))),
            free_count=cap_json_int(max(0, int(hr.free_count))),
            prime_colony_free_count=cap_json_int(max(0, int(hr.prime_colony_free_count))),
            owners=owners_tup,
            owner_planet_counts=opc_items,
        )
    return spacia_coords, coord_owner, owner_display, owner_sectors_all, active_sectors, explored_active


def merge_battle_reports(
    session: Session,
    *,
    own_coords: set[str],
    spacia_coords: set[str],
    coord_owner: dict[str, str],
    owner_display: dict[str, str],
    owner_sectors_all: dict[str, set[str]],
    active_gal_set: set[int],
    eff_max_g: int,
) -> tuple[dict[str, float], dict[tuple[int, int], dict[str, set[str]]]]:
    """Aggregate `battle_reports` loot and backfill defender ownership onto unscanned coords."""
    battle_rows = session.execute(
        select(
            BattleReport.target_coords,
            BattleReport.defender,
            func.coalesce(func.sum(BattleReport.loot_spice_eq), 0.0),
        )
        .where(BattleReport.target_coords != "")
        .group_by(BattleReport.target_coords, BattleReport.defender)
    ).all()
    battle_loot_by_coord: dict[str, float] = {}
    battle_extra_owners: dict[tuple[int, int], dict[str, set[str]]] = {}
    for row in battle_rows:
        nc_b = normalize_coords(str(row[0] or ""))
        gsp = parse_coords_gsp(nc_b)
        if gsp is None:
            continue
        gb_i, sb_i, _ = gsp
        def_name = sanitize_text(row[1] or "").strip()
        sum_sp = max(0.0, float(row[2] or 0.0))
        battle_loot_by_coord[nc_b] = battle_loot_by_coord.get(nc_b, 0.0) + sum_sp
        if (
            def_name
            and def_name.lower() != "spacia"
            and nc_b not in own_coords
            and nc_b not in spacia_coords
            and nc_b not in coord_owner
        ):
            coord_owner[nc_b] = def_name
            ok_b = def_name.lower()
            owner_display.setdefault(ok_b, def_name)
            if 1 <= gb_i <= eff_max_g and 1 <= sb_i <= SYSTEMS_PER_GALAXY:
                owner_sectors_all.setdefault(ok_b, set()).add(f"{gb_i:02d}:{sb_i:03d}")
                if gb_i in active_gal_set:
                    battle_extra_owners.setdefault((gb_i, sb_i), {}).setdefault(ok_b, set()).add(nc_b)
    return battle_loot_by_coord, battle_extra_owners


def aggregate_raids_for_sectors(
    session: Session,
    *,
    eff_now: float,
    own_coords: set[str],
    spacia_coords: set[str],
    coord_owner: dict[str, str],
    owner_display: dict[str, str],
    active_gal_set: set[int],
    battle_loot_by_coord: dict[str, float],
) -> tuple[dict[str, OwnerAgg], dict[tuple[int, int], SectorRaidAgg]]:
    """Aggregate raid counts, recency decay, and loot by owner and sector."""
    raid_rows = session.execute(
        select(RaidHistory.target_coords, RaidHistory.attacked_ts, RaidHistory.outcome, RaidHistory.loot_json).order_by(
            RaidHistory.attacked_ts.asc(), RaidHistory.id.asc()
        )
    ).all()
    owner_agg: dict[str, OwnerAgg] = {}
    sector_agg: dict[tuple[int, int], SectorRaidAgg] = {}
    raid_loot_by_coord: dict[str, float] = {}

    for rr in raid_rows:
        nc_r = normalize_coords(str(rr[0] or ""))
        if not nc_r or nc_r in own_coords:
            continue
        gsp = parse_coords_gsp(nc_r)
        if gsp is None:
            continue
        gr_i, sr_i, _ = gsp
        rts = float(rr[1] or 0.0)
        age_r = max(0.0, eff_now - rts) if rts > 0.0 else 0.0
        decay = 0.25 + 0.75 * math.exp(-age_r / (7.0 * 86400.0))
        loot = normalize_loot_dict(str(rr[3] or "{}"))
        sp_eq = round(
            spice_equivalent_from_counts(loot["pig_iron"], loot["metal"], loot["kryptonite"], loot["spice"]), 3
        )
        won = is_win_outcome(str(rr[2] or ""), sp_eq)
        raid_loot_by_coord[nc_r] = raid_loot_by_coord.get(nc_r, 0.0) + sp_eq

        owner_s = coord_owner.get(nc_r, "")
        if owner_s and nc_r not in spacia_coords and owner_s.lower() != "spacia":
            okey = owner_s.lower()
            oa = owner_agg.setdefault(okey, OwnerAgg(owner=owner_display.get(okey, owner_s)))
            oa.raid_count += 1
            oa.raid_wins += 1 if won else 0
            oa.raid_24h_count += 1 if (rts > 0.0 and age_r <= 86400.0) else 0
            oa.raid_7d_count += 1 if (rts > 0.0 and age_r <= 7.0 * 86400.0) else 0
            oa.recency_raid_score += decay
            oa.loot_spice_eq += sp_eq
            oa.last_attacked_ts = max(oa.last_attacked_ts, rts)

        if gr_i in active_gal_set and 1 <= sr_i <= SYSTEMS_PER_GALAXY:
            sa = sector_agg.setdefault((gr_i, sr_i), SectorRaidAgg())
            sa.direct_raid_count += 1
            sa.direct_raid_wins += 1 if won else 0
            sa.direct_raid_24h_count += 1 if (rts > 0.0 and age_r <= 86400.0) else 0
            sa.direct_raid_7d_count += 1 if (rts > 0.0 and age_r <= 7.0 * 86400.0) else 0
            sa.direct_recency_raid_score += decay
            sa.direct_loot_spice_eq += sp_eq
            sa.last_attacked_ts = max(sa.last_attacked_ts, rts)

    for nc_b, b_sp in battle_loot_by_coord.items():
        extra_sp = b_sp - raid_loot_by_coord.get(nc_b, 0.0)
        if extra_sp <= 0.0:
            continue
        gsp_b = parse_coords_gsp(nc_b)
        if gsp_b is not None and (gsp_b[0], gsp_b[1]) in sector_agg:
            sector_agg[(gsp_b[0], gsp_b[1])].direct_loot_spice_eq += extra_sp
        owner_s = coord_owner.get(nc_b, "")
        if owner_s and owner_s.lower() in owner_agg:
            owner_agg[owner_s.lower()].loot_spice_eq += extra_sp
    return owner_agg, sector_agg


__all__ = [
    "SYSTEMS_PER_GALAXY",
    "OwnerAgg",
    "SectorHotRow",
    "SectorRaidAgg",
    "aggregate_raids_for_sectors",
    "merge_battle_reports",
    "parse_hot_sector_maps",
]
