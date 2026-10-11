"""Set-based SQLAlchemy read queries for G1-G14 galaxy coverage and sector intel."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import GalaxyCoverageSummary, GalaxyIntelSummary, GalaxySectorIntelRecord
from spaceinvasion.db.models import BattleReport, GalaxySectorHot, RaidHistory
from spaceinvasion.db.repos._helpers import loads_json_list, parse_coords_gsp

_SYSTEMS_PER_GALAXY = 400


def query_galaxy_coverage(
    session: Session,
    *,
    max_galaxies: int = 14,
) -> tuple[GalaxyCoverageSummary, ...]:
    """Compute per-galaxy `G1`-`G14` scan coverage and target counts in 1 SQL `GROUP BY` query."""
    eff_max = max(1, min(14, int(max_galaxies)))
    scanned_case = case((GalaxySectorHot.last_scanned_ts > 0.0, 1), else_=0)
    rows = session.execute(
        select(
            GalaxySectorHot.galaxy,
            func.coalesce(func.sum(scanned_case), 0),
            func.coalesce(func.sum(GalaxySectorHot.scan_count), 0),
            func.coalesce(func.sum(GalaxySectorHot.occupied_count), 0),
            func.coalesce(func.sum(GalaxySectorHot.target_count), 0),
            func.coalesce(func.sum(GalaxySectorHot.inactive_count), 0),
            func.coalesce(func.sum(GalaxySectorHot.spacia_count), 0),
            func.coalesce(func.sum(GalaxySectorHot.debris_count), 0),
            func.coalesce(func.sum(GalaxySectorHot.free_count), 0),
            func.coalesce(func.sum(GalaxySectorHot.prime_colony_free_count), 0),
            func.max(case((GalaxySectorHot.last_scanned_ts > 0.0, GalaxySectorHot.last_scanned_ts), else_=None)),
        )
        .where(
            GalaxySectorHot.galaxy >= 1,
            GalaxySectorHot.galaxy <= eff_max,
            GalaxySectorHot.system >= 1,
            GalaxySectorHot.system <= _SYSTEMS_PER_GALAXY,
        )
        .group_by(GalaxySectorHot.galaxy)
        .order_by(GalaxySectorHot.galaxy.asc())
    ).all()

    by_gal: dict[int, GalaxyCoverageSummary] = {}
    for r in rows:
        gal = int(r[0])
        scanned = max(0, int(r[1] or 0))
        by_gal[gal] = GalaxyCoverageSummary(
            galaxy=gal,
            scanned_systems=scanned,
            total_systems=_SYSTEMS_PER_GALAXY,
            coverage_pct=round((scanned / _SYSTEMS_PER_GALAXY) * 100.0, 2),
            total_scans=max(0, int(r[2] or 0)),
            occupied_count=max(0, int(r[3] or 0)),
            target_count=max(0, int(r[4] or 0)),
            inactive_count=max(0, int(r[5] or 0)),
            spacia_count=max(0, int(r[6] or 0)),
            debris_count=max(0, int(r[7] or 0)),
            free_count=max(0, int(r[8] or 0)),
            prime_colony_free_count=max(0, int(r[9] or 0)),
            latest_scanned_ts=round(float(r[10]), 3) if r[10] is not None else None,
        )
    return tuple(
        by_gal.get(
            gal,
            GalaxyCoverageSummary(gal, 0, _SYSTEMS_PER_GALAXY, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, None),
        )
        for gal in range(1, eff_max + 1)
    )


def query_galaxy_sector_intel(
    session: Session,
    *,
    galaxies: Sequence[int] | None = None,
    max_galaxies: int = 14,
) -> GalaxyIntelSummary:
    """Aggregate `G1`-`G14` coverage and per-sector targets, raids, and battle loot in 4 SQL queries."""
    eff_max = max(1, min(14, int(max_galaxies)))
    req_gals: list[int] = []
    if galaxies is not None:
        for raw_g in galaxies:
            gi = int(raw_g)
            if 1 <= gi <= eff_max and gi not in req_gals:
                req_gals.append(gi)

    coverage = query_galaxy_coverage(session, max_galaxies=eff_max)
    discovered = [c.galaxy for c in coverage if c.scanned_systems > 0 or c.occupied_count > 0 or c.free_count > 0]
    active_gals = tuple(req_gals) if req_gals else (tuple(discovered) if discovered else (1,))

    hot_rows = (
        session.execute(
            select(GalaxySectorHot)
            .where(
                GalaxySectorHot.galaxy.in_(active_gals),
                GalaxySectorHot.system >= 1,
                GalaxySectorHot.system <= _SYSTEMS_PER_GALAXY,
            )
            .order_by(GalaxySectorHot.galaxy.asc(), GalaxySectorHot.system.asc())
        )
        .scalars()
        .all()
    )

    raid_sector_counts: dict[tuple[int, int], int] = {}
    for rr in session.execute(
        select(RaidHistory.target_coords, func.count())
        .where(RaidHistory.target_coords != "")
        .group_by(RaidHistory.target_coords)
    ).all():
        gsp = parse_coords_gsp(str(rr[0] or ""))
        if gsp is not None and gsp[0] in active_gals:
            key = (gsp[0], gsp[1])
            raid_sector_counts[key] = raid_sector_counts.get(key, 0) + int(rr[1] or 0)

    battle_loot_by_sec: dict[tuple[int, int], float] = {}
    for br in session.execute(
        select(BattleReport.target_coords, func.coalesce(func.sum(BattleReport.loot_spice_eq), 0.0))
        .where(BattleReport.target_coords != "")
        .group_by(BattleReport.target_coords)
    ).all():
        gsp = parse_coords_gsp(str(br[0] or ""))
        if gsp is not None and gsp[0] in active_gals:
            key = (gsp[0], gsp[1])
            battle_loot_by_sec[key] = round(battle_loot_by_sec.get(key, 0.0) + max(0.0, float(br[1] or 0.0)), 3)

    sectors = tuple(
        GalaxySectorIntelRecord(
            galaxy=int(h.galaxy),
            system=int(h.system),
            sector_key=f"{int(h.galaxy):02d}:{int(h.system):03d}",
            last_scanned_ts=round(float(h.last_scanned_ts), 3),
            scan_count=int(h.scan_count),
            occupied_count=int(h.occupied_count),
            target_count=int(h.target_count),
            inactive_count=int(h.inactive_count),
            spacia_count=int(h.spacia_count),
            debris_count=int(h.debris_count),
            free_count=int(h.free_count),
            prime_colony_free_count=int(h.prime_colony_free_count),
            raid_count=raid_sector_counts.get((int(h.galaxy), int(h.system)), 0),
            battle_loot_spice_eq=battle_loot_by_sec.get((int(h.galaxy), int(h.system)), 0.0),
            owners=tuple(str(x) for x in loads_json_list(h.owners_json)),
            spacia_coords=tuple(str(x) for x in loads_json_list(h.spacia_coords_json)),
        )
        for h in hot_rows
    )
    universe_scanned = sum(c.scanned_systems for c in coverage)
    return GalaxyIntelSummary(
        active_galaxies=active_gals,
        universe_scanned_systems=universe_scanned,
        universe_total_systems=eff_max * _SYSTEMS_PER_GALAXY,
        coverage_by_galaxy=coverage,
        sectors=sectors,
    )


__all__ = ["query_galaxy_coverage", "query_galaxy_sector_intel"]
