"""SQLAlchemy ORM repository for galaxy scans, slots, history, and hot sector sync."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.dto import (
    GalaxyScanHistoryRecord,
    GalaxyScanRecord,
    GalaxySlotHistoryRecord,
    GalaxySlotRecord,
)
from backend.db.models import GalaxyScan, GalaxyScanHistory, GalaxySlot, GalaxySlotHistory
from backend.db.repos._galaxy_sync import (
    GalaxySlotInput,
    batch_upsert_slots_and_history,
    sync_slots_to_spy_targets_and_debris,
)
from backend.db.repos._helpers import (
    clamp_int64,
    clamp_limit,
    normalize_coords,
    round_ts,
    sanitize_text,
    to_float,
    to_int,
)
from backend.db.repos._hot_refresh import refresh_colony_candidates_hot, refresh_galaxy_sectors_hot


def _slot_to_dto(row: GalaxySlot) -> GalaxySlotRecord:
    return GalaxySlotRecord(
        coords=normalize_coords(row.coords),
        galaxy=to_int(row.galaxy, 0),
        system=to_int(row.system, 0),
        position=to_int(row.position, 0),
        planet_name=row.planet_name,
        owner=row.owner,
        status_color=row.status_color,
        is_inactive=bool(row.is_inactive),
        is_spacia=bool(row.is_spacia),
        has_debris=bool(row.has_debris),
        last_scanned_ts=to_float(row.last_scanned_ts, 0.0),
        colonize_min_fields=to_int(row.colonize_min_fields, 0) if row.colonize_min_fields is not None else None,
        colonize_max_fields=to_int(row.colonize_max_fields, 0) if row.colonize_max_fields is not None else None,
        temperature=to_int(row.temperature, 0) if row.temperature is not None else None,
    )


def _slot_hist_to_dto(row: GalaxySlotHistory) -> GalaxySlotHistoryRecord:
    return GalaxySlotHistoryRecord(
        id=to_int(row.id, 0),
        ts=to_float(row.ts, 0.0),
        coords=normalize_coords(row.coords),
        galaxy=to_int(row.galaxy, 0),
        system=to_int(row.system, 0),
        position=to_int(row.position, 0),
        planet_name=row.planet_name,
        owner=row.owner,
        status_color=row.status_color,
        is_inactive=bool(row.is_inactive),
        is_spacia=bool(row.is_spacia),
        has_debris=bool(row.has_debris),
        has_asteroid=bool(row.has_asteroid),
        colonize_min_fields=to_int(row.colonize_min_fields, 0) if row.colonize_min_fields is not None else None,
        colonize_max_fields=to_int(row.colonize_max_fields, 0) if row.colonize_max_fields is not None else None,
        temperature=to_int(row.temperature, 0) if row.temperature is not None else None,
    )


def _scan_to_dto(row: GalaxyScan) -> GalaxyScanRecord:
    g, s = to_int(row.galaxy, 0), to_int(row.system, 0)
    return GalaxyScanRecord(
        galaxy=g,
        system=s,
        sector_key=f"{g:02d}:{s:03d}",
        last_scanned_ts=to_float(row.last_scanned_ts, 0.0),
        occupied_count=to_int(row.occupied_count, 0),
        target_count=to_int(row.target_count, 0),
        free_count=to_int(row.free_count, 0),
    )


def _scan_hist_to_dto(row: GalaxyScanHistory) -> GalaxyScanHistoryRecord:
    return GalaxyScanHistoryRecord(
        id=to_int(row.id, 0),
        ts=to_float(row.ts, 0.0),
        galaxy=to_int(row.galaxy, 0),
        system=to_int(row.system, 0),
        occupied_count=to_int(row.occupied_count, 0),
        target_count=to_int(row.target_count, 0),
        free_count=to_int(row.free_count, 0),
        debris_count=to_int(row.debris_count, 0),
        asteroid_count=to_int(row.asteroid_count, 0),
    )


class GalaxyRepository:
    """Batched galaxy scan, slot catalog, and sector hot-table persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_galaxy_slots(
        self,
        slots: Sequence[GalaxySlotInput],
        *,
        append_slot_history: bool = True,
        only_changed_slot_history: bool = False,
        refresh_hot: bool = True,
        ts: float | None = None,
    ) -> int:
        """Batch-upsert galaxy slots, sync `spy_targets` & debris meta, and refresh hot tables in O(1) SQL."""
        now_ts = round_ts(ts)
        coords_list, sectors = batch_upsert_slots_and_history(
            self._session,
            slots,
            now_ts,
            append_slot_history=append_slot_history,
            only_changed_slot_history=only_changed_slot_history,
        )
        saved = sync_slots_to_spy_targets_and_debris(self._session, slots, now_ts)
        if refresh_hot:
            if coords_list:
                refresh_colony_candidates_hot(self._session, coords_list, ts=now_ts)
            if sectors:
                refresh_galaxy_sectors_hot(self._session, sorted(sectors), ts=now_ts)
        return saved

    def record_system_scan(
        self,
        galaxy: int,
        system: int,
        slots: Sequence[GalaxySlotInput] = (),
        *,
        sync_spy_targets: bool = False,
        append_slot_history: bool = True,
        only_changed_slot_history: bool = False,
        refresh_hot: bool = True,
        ts: float | None = None,
    ) -> GalaxyScanRecord:
        """Batch-record a full galaxy system scan in bounded O(1) SQL (never per-slot loops)."""
        now_ts = round_ts(ts)
        gal_i = clamp_int64(int(galaxy))
        sys_i = clamp_int64(int(system))
        occupied = sum(1 for s in slots if (s.status_color or "free") != "free")
        targets = sum(1 for s in slots if s.status_color == "yellow" or s.is_spacia)
        free = sum(1 for s in slots if (s.status_color or "free") == "free")
        debris = sum(1 for s in slots if s.has_debris)
        asteroids = sum(1 for s in slots if s.has_asteroid)

        coords_list, _ = batch_upsert_slots_and_history(
            self._session,
            slots,
            now_ts,
            append_slot_history=append_slot_history,
            only_changed_slot_history=only_changed_slot_history,
        )
        if sync_spy_targets and slots:
            sync_slots_to_spy_targets_and_debris(self._session, slots, now_ts)

        scan_stmt = pg_insert(GalaxyScan).values(
            galaxy=gal_i,
            system=sys_i,
            last_scanned_ts=now_ts,
            occupied_count=occupied,
            target_count=targets,
            free_count=free,
        )
        self._session.execute(
            scan_stmt.on_conflict_do_update(
                index_elements=[GalaxyScan.galaxy, GalaxyScan.system],
                set_={
                    "last_scanned_ts": scan_stmt.excluded.last_scanned_ts,
                    "occupied_count": scan_stmt.excluded.occupied_count,
                    "target_count": scan_stmt.excluded.target_count,
                    "free_count": scan_stmt.excluded.free_count,
                },
            )
        )
        self._session.add(
            GalaxyScanHistory(
                ts=now_ts,
                galaxy=gal_i,
                system=sys_i,
                occupied_count=occupied,
                target_count=targets,
                free_count=free,
                debris_count=debris,
                asteroid_count=asteroids,
            )
        )
        self._session.flush()
        if refresh_hot:
            if coords_list:
                refresh_colony_candidates_hot(self._session, coords_list, ts=now_ts)
            if 1 <= gal_i <= 14 and 1 <= sys_i <= 400:
                refresh_galaxy_sectors_hot(self._session, ((gal_i, sys_i),), ts=now_ts)
        return GalaxyScanRecord(gal_i, sys_i, f"{gal_i:02d}:{sys_i:03d}", now_ts, occupied, targets, free)

    def list_galaxy_slots(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        status_color: str | None = None,
        limit: int = 500,
    ) -> tuple[GalaxySlotRecord, ...]:
        """List current scanned slots ordered by `(galaxy, system, position)`."""
        stmt = select(GalaxySlot)
        if galaxy is not None:
            stmt = stmt.where(GalaxySlot.galaxy == clamp_int64(to_int(galaxy, 0)))
        if system is not None:
            stmt = stmt.where(GalaxySlot.system == clamp_int64(to_int(system, 0)))
        if status_color is not None:
            stmt = stmt.where(GalaxySlot.status_color == sanitize_text(status_color).strip())
        rows = (
            self._session.execute(
                stmt.order_by(GalaxySlot.galaxy.asc(), GalaxySlot.system.asc(), GalaxySlot.position.asc()).limit(
                    clamp_limit(limit, 500)
                )
            )
            .scalars()
            .all()
        )
        return tuple(_slot_to_dto(r) for r in rows)

    def list_galaxy_scans(
        self,
        *,
        max_age_seconds: float | None = None,
        now_ts: float | None = None,
    ) -> tuple[GalaxyScanRecord, ...]:
        """List scanned systems ordered most recently scanned first."""
        stmt = select(GalaxyScan)
        age_f = to_float(max_age_seconds, -1.0) if max_age_seconds is not None else -1.0
        if age_f >= 0.0:
            cutoff = round_ts(now_ts) - age_f
            stmt = stmt.where(GalaxyScan.last_scanned_ts >= cutoff)
        rows = (
            self._session.execute(
                stmt.order_by(GalaxyScan.last_scanned_ts.desc(), GalaxyScan.galaxy.asc(), GalaxyScan.system.asc())
            )
            .scalars()
            .all()
        )
        return tuple(_scan_to_dto(r) for r in rows)

    def list_galaxy_slot_history(
        self,
        *,
        coords: str | None = None,
        galaxy: int | None = None,
        system: int | None = None,
        limit: int = 500,
    ) -> tuple[GalaxySlotHistoryRecord, ...]:
        """List append-only galaxy slot observations newest first."""
        stmt = select(GalaxySlotHistory)
        if coords is not None:
            stmt = stmt.where(GalaxySlotHistory.coords == normalize_coords(coords))
        if galaxy is not None:
            stmt = stmt.where(GalaxySlotHistory.galaxy == clamp_int64(to_int(galaxy, 0)))
        if system is not None:
            stmt = stmt.where(GalaxySlotHistory.system == clamp_int64(to_int(system, 0)))
        rows = (
            self._session.execute(
                stmt.order_by(GalaxySlotHistory.ts.desc(), GalaxySlotHistory.id.desc()).limit(clamp_limit(limit, 500))
            )
            .scalars()
            .all()
        )
        return tuple(_slot_hist_to_dto(r) for r in rows)

    def list_galaxy_scan_history(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        limit: int = 200,
    ) -> tuple[GalaxyScanHistoryRecord, ...]:
        """List append-only galaxy scan observations newest first."""
        stmt = select(GalaxyScanHistory)
        if galaxy is not None:
            stmt = stmt.where(GalaxyScanHistory.galaxy == clamp_int64(to_int(galaxy, 0)))
        if system is not None:
            stmt = stmt.where(GalaxyScanHistory.system == clamp_int64(to_int(system, 0)))
        rows = (
            self._session.execute(
                stmt.order_by(GalaxyScanHistory.ts.desc(), GalaxyScanHistory.id.desc()).limit(clamp_limit(limit, 200))
            )
            .scalars()
            .all()
        )
        return tuple(_scan_hist_to_dto(r) for r in rows)


__all__ = ["GalaxyRepository", "GalaxySlotInput"]
