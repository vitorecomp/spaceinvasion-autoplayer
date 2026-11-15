"""Batched SQLAlchemy refresh helper for `galaxy_sector_hot`."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, literal, select, tuple_, union
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import (
    EmpirePlanetHot,
    GalaxyScan,
    GalaxyScanHistory,
    GalaxySectorHot,
    GalaxySlot,
    PlanetSnapshot,
    SpyTarget,
)
from backend.db.repos._helpers import (
    dumps_json_unsorted,
    normalize_coords,
    parse_coords_gsp,
    round_ts,
    sanitize_text,
)


def _load_own_coords(session: Session) -> set[str]:
    ep_stmt = select(EmpirePlanetHot.coords, literal(1).label("prio")).where(EmpirePlanetHot.coords != "")
    own_spy_stmt = select(SpyTarget.coords, literal(1).label("prio")).where(SpyTarget.is_own_planet == 1)
    snap_stmt = select(PlanetSnapshot.coords, literal(2).label("prio")).where(PlanetSnapshot.coords != "").distinct()
    raw_rows = session.execute(union(ep_stmt, own_spy_stmt, snap_stmt)).all()
    primary = [str(r[0]) for r in raw_rows if int(r[1]) == 1]
    rows = primary if primary else [str(r[0]) for r in raw_rows]
    out: set[str] = set()
    for raw in rows:
        nc = normalize_coords(str(raw or ""))
        if parse_coords_gsp(nc) is not None:
            out.add(nc)
    return out


def _accumulate_slot_or_spy(
    *,
    nc: str,
    pos_i: int,
    owner_s: str,
    scolor: str,
    is_inactive: bool,
    is_spacia: bool,
    has_debris: bool,
    uf: int,
    cmax: int,
    from_spy: bool,
    own_coords: set[str],
    sets: tuple[set[str], set[str], set[str], set[str], set[str], set[str]],
    owners: tuple[dict[str, str], dict[str, set[str]], dict[str, str]],
) -> None:
    occ_set, debris_set, inact_set, spacia_set, free_set, prime_set = sets
    owner_display, owner_coords, coord_owner = owners
    if scolor != "free":
        occ_set.add(nc)
    if has_debris:
        debris_set.add(nc)
    if nc in own_coords:
        return
    if is_spacia or owner_s.lower() == "spacia":
        spacia_set.add(nc)
    elif scolor == "yellow" or (is_inactive and scolor != "free"):
        inact_set.add(nc)
    if owner_s and scolor != "free" and not is_spacia and owner_s.lower() != "spacia":
        okey = owner_s.lower()
        if from_spy:
            owner_display[okey] = owner_s
        else:
            owner_display.setdefault(okey, owner_s)
        owner_coords.setdefault(okey, set()).add(nc)
        coord_owner[nc] = owner_s
    if scolor == "free":
        free_set.add(nc)
        if from_spy:
            if uf >= 300 or cmax >= 300 or (uf == 0 and cmax == 0 and 7 <= pos_i <= 12):
                prime_set.add(nc)
        elif cmax >= 300 or (cmax == 0 and 7 <= pos_i <= 12):
            prime_set.add(nc)


def _fetch_sector_sources(
    session: Session,
    valid_sectors: Sequence[tuple[int, int]],
) -> tuple[
    dict[tuple[int, int], tuple[float, int, int, int]],
    dict[tuple[int, int], tuple[int, float, int]],
    dict[tuple[int, int], list[tuple[object, ...]]],
    dict[tuple[int, int], list[tuple[object, ...]]],
]:
    sector_prefixes = [f"{g:02d}:{s:03d}" for g, s in valid_sectors]
    scan_map: dict[tuple[int, int], tuple[float, int, int, int]] = {}
    for scan_r in session.execute(
        select(
            GalaxyScan.galaxy,
            GalaxyScan.system,
            GalaxyScan.last_scanned_ts,
            GalaxyScan.occupied_count,
            GalaxyScan.target_count,
            GalaxyScan.free_count,
        ).where(tuple_(GalaxyScan.galaxy, GalaxyScan.system).in_(valid_sectors))
    ).all():
        scan_map[(int(scan_r[0]), int(scan_r[1]))] = (
            float(scan_r[2] or 0.0),
            max(0, int(scan_r[3] or 0)),
            max(0, int(scan_r[4] or 0)),
            max(0, int(scan_r[5] or 0)),
        )

    hist_map: dict[tuple[int, int], tuple[int, float, int]] = {}
    for hist_r in session.execute(
        select(
            GalaxyScanHistory.galaxy,
            GalaxyScanHistory.system,
            func.count(),
            func.max(GalaxyScanHistory.ts),
            func.max(GalaxyScanHistory.debris_count),
        )
        .where(tuple_(GalaxyScanHistory.galaxy, GalaxyScanHistory.system).in_(valid_sectors))
        .group_by(GalaxyScanHistory.galaxy, GalaxyScanHistory.system)
    ).all():
        hist_map[(int(hist_r[0]), int(hist_r[1]))] = (
            max(0, int(hist_r[2] or 0)),
            float(hist_r[3] or 0.0),
            max(0, int(hist_r[4] or 0)),
        )

    slots_by_sec: dict[tuple[int, int], list[tuple[object, ...]]] = {}
    for slot_r in session.execute(
        select(
            GalaxySlot.galaxy,
            GalaxySlot.system,
            GalaxySlot.coords,
            GalaxySlot.owner,
            GalaxySlot.status_color,
            GalaxySlot.is_inactive,
            GalaxySlot.is_spacia,
            GalaxySlot.has_debris,
            GalaxySlot.colonize_max_fields,
        ).where(tuple_(GalaxySlot.galaxy, GalaxySlot.system).in_(valid_sectors))
    ).all():
        slots_by_sec.setdefault((int(slot_r[0]), int(slot_r[1])), []).append(tuple(slot_r))

    spy_by_sec: dict[tuple[int, int], list[tuple[object, ...]]] = {}
    for spy_r in session.execute(
        select(
            SpyTarget.coords,
            SpyTarget.owner,
            SpyTarget.status_color,
            SpyTarget.is_inactive,
            SpyTarget.is_spacia,
            SpyTarget.has_debris,
            SpyTarget.uninhabited_fields,
            SpyTarget.colonize_max_fields,
        ).where(
            SpyTarget.merged_into == "",
            SpyTarget.is_own_planet == 0,
            func.substr(SpyTarget.coords, 1, 6).in_(sector_prefixes),
        )
    ).all():
        gsp = parse_coords_gsp(str(spy_r[0] or ""))
        if gsp is not None:
            spy_by_sec.setdefault((gsp[0], gsp[1]), []).append(tuple(spy_r))

    return scan_map, hist_map, slots_by_sec, spy_by_sec


def _build_sector_hot_row(
    sec_key: tuple[int, int],
    *,
    scan_info: tuple[float, int, int, int],
    hist_info: tuple[int, float, int],
    slot_rows: Sequence[tuple[object, ...]],
    spy_rows: Sequence[tuple[object, ...]],
    own_coords: set[str],
    ts_r: float,
) -> dict[str, object]:
    gal_i, sys_i = sec_key
    last_ts, sm_occ, sm_tgt, sm_free = scan_info
    scan_cnt = 1 if last_ts > 0.0 else 0
    h_cnt, h_ts, hist_deb = hist_info
    if h_cnt > 0:
        scan_cnt = max(scan_cnt, h_cnt)
        last_ts = max(last_ts, h_ts)

    occ_set: set[str] = set()
    debris_set: set[str] = set()
    inact_set: set[str] = set()
    spacia_set: set[str] = set()
    free_set: set[str] = set()
    prime_set: set[str] = set()
    owner_display: dict[str, str] = {}
    owner_coords: dict[str, set[str]] = {}
    coord_owner: dict[str, str] = {}
    sets = (occ_set, debris_set, inact_set, spacia_set, free_set, prime_set)
    owners = (owner_display, owner_coords, coord_owner)

    for sl in slot_rows:
        nc = normalize_coords(str(sl[2] or ""))
        gsp = parse_coords_gsp(nc)
        if gsp is not None:
            _accumulate_slot_or_spy(
                nc=nc,
                pos_i=gsp[2],
                owner_s=sanitize_text(sl[3] or "").strip(),
                scolor=sanitize_text(sl[4] or "free").strip().lower() or "free",
                is_inactive=bool(sl[5]),
                is_spacia=bool(sl[6]),
                has_debris=bool(sl[7]),
                uf=0,
                cmax=int(sl[8]) if isinstance(sl[8], int) else 0,
                from_spy=False,
                own_coords=own_coords,
                sets=sets,
                owners=owners,
            )
    for st in spy_rows:
        nc = normalize_coords(str(st[0] or ""))
        gsp = parse_coords_gsp(nc)
        if gsp is not None and (gsp[0], gsp[1]) == sec_key:
            _accumulate_slot_or_spy(
                nc=nc,
                pos_i=gsp[2],
                owner_s=sanitize_text(st[1] or "").strip(),
                scolor=sanitize_text(st[2] or "yellow").strip().lower() or "yellow",
                is_inactive=bool(st[3]),
                is_spacia=bool(st[4]),
                has_debris=bool(st[5]),
                uf=int(st[6]) if isinstance(st[6], int) else 0,
                cmax=int(st[7]) if isinstance(st[7], int) else 0,
                from_spy=True,
                own_coords=own_coords,
                sets=sets,
                owners=owners,
            )

    owners_list: list[str] = []
    owner_counts: dict[str, int] = {}
    for okey in sorted(owner_coords):
        pcnt = len(owner_coords[okey])
        if pcnt > 0:
            disp = owner_display.get(okey, okey)
            owners_list.append(disp)
            owner_counts[disp] = pcnt
    co_norm = {nc: owner_display.get(ov.lower(), ov) for nc, ov in sorted(coord_owner.items()) if nc not in spacia_set}
    inact_cnt, spacia_cnt = len(inact_set), len(spacia_set)
    return {
        "galaxy": gal_i,
        "system": sys_i,
        "last_scanned_ts": round(last_ts, 3),
        "scan_count": scan_cnt,
        "occupied_count": max(sm_occ, len(occ_set)),
        "target_count": max(sm_tgt, inact_cnt + spacia_cnt),
        "inactive_count": inact_cnt,
        "spacia_count": spacia_cnt,
        "debris_count": len(debris_set),
        "free_count": max(sm_free, len(free_set)),
        "prime_colony_free_count": len(prime_set),
        "hist_debris_max": hist_deb,
        "owners_json": dumps_json_unsorted(owners_list),
        "owner_planet_counts_json": dumps_json_unsorted(owner_counts),
        "coord_owners_json": dumps_json_unsorted(co_norm),
        "spacia_coords_json": dumps_json_unsorted(sorted(spacia_set)),
        "updated_ts": ts_r,
    }


def refresh_galaxy_sectors_hot(
    session: Session,
    sectors: Sequence[tuple[int, int]],
    *,
    ts: float | None = None,
) -> int:
    """Recompute and bulk-upsert `galaxy_sector_hot` across `sectors` in O(1) SQL statements."""
    valid_sectors = sorted({(int(g), int(s)) for g, s in sectors if 1 <= int(g) <= 14 and 1 <= int(s) <= 400})
    if not valid_sectors:
        return 0
    ts_r = round_ts(ts)
    own_coords = _load_own_coords(session)
    scan_map, hist_map, slots_by_sec, spy_by_sec = _fetch_sector_sources(session, valid_sectors)

    payload = [
        _build_sector_hot_row(
            sec_key,
            scan_info=scan_map.get(sec_key, (0.0, 0, 0, 0)),
            hist_info=hist_map.get(sec_key, (0, 0.0, 0)),
            slot_rows=slots_by_sec.get(sec_key, ()),
            spy_rows=spy_by_sec.get(sec_key, ()),
            own_coords=own_coords,
            ts_r=ts_r,
        )
        for sec_key in valid_sectors
    ]
    stmt = pg_insert(GalaxySectorHot).values(payload)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[GalaxySectorHot.galaxy, GalaxySectorHot.system],
            set_={k: getattr(stmt.excluded, k) for k in payload[0] if k not in ("galaxy", "system")},
        )
    )
    return len(payload)


__all__ = ["refresh_galaxy_sectors_hot"]
