"""SQLAlchemy ORM repository for `spy_targets` and `spy_reports`."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.dto import SpyReportDTO, SpyTargetRecord
from backend.db.models import GalaxySlot, SpyReportRecord, SpyTarget
from backend.db.repos._helpers import (
    affected_rows,
    clamp_int64,
    clamp_limit,
    coord_query_variants,
    dumps_json,
    normalize_coords,
    parse_coords_gsp,
    round_ts,
    sanitize_text,
    spice_equivalent_from_counts,
)
from backend.db.repos._hot_refresh import refresh_colony_candidates_hot, refresh_galaxy_sectors_hot
from backend.db.repos._intel_helpers import (
    SpyReportInput,
    SpyTargetUpsertInput,
    spy_report_to_dto,
    spy_target_to_dto,
    upsert_spy_target_from_report,
)
from backend.db.repos.settings import SettingsRepository


class IntelRepository:
    """Append-only `spy_reports` and non-destructive `spy_targets` persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_spy_report(
        self,
        report: SpyReportInput,
        *,
        points: int | None = None,
        is_inactive: bool = False,
        status_color: str | None = None,
        refresh_hot: bool = True,
        ts: float | None = None,
    ) -> SpyReportDTO | None:
        """Upsert `spy_targets` non-destructively and append an immutable `spy_reports` row."""
        now_ts = round_ts(ts)
        norm_c = normalize_coords(report.target_coords)
        if not norm_c:
            return None

        explicit_status = status_color is not None or bool(is_inactive) or bool(report.is_spacia)
        insert_inactive = bool(is_inactive or report.is_spacia or status_color == "yellow")
        insert_color = status_color or ("yellow" if insert_inactive else "light_red")
        pig_i, met_i = clamp_int64(int(report.pig_iron)), clamp_int64(int(report.metal))
        kry_i, spi_i = clamp_int64(int(report.kryptonite)), clamp_int64(int(report.spice))
        loot_sp = round(spice_equivalent_from_counts(pig_i, met_i, kry_i, spi_i), 3)
        bld_j, flt_j = dumps_json(report.buildings), dumps_json(report.fleet)
        def_j, res_j = dumps_json(report.defense), dumps_json(report.researches)
        owner_s, pname_s = sanitize_text(report.owner), sanitize_text(report.planet_name)
        pts_opt = clamp_int64(int(points)) if points is not None else None
        uf_opt = clamp_int64(int(report.uninhabited_fields)) if report.uninhabited_fields is not None else None

        upsert_spy_target_from_report(
            self._session,
            norm_c=norm_c,
            owner_s=owner_s,
            pname_s=pname_s,
            insert_color=insert_color,
            insert_inactive=insert_inactive,
            explicit_status=explicit_status,
            is_spacia=bool(report.is_spacia),
            pts_opt=pts_opt,
            resources=(pig_i, met_i, kry_i, spi_i),
            flt_j=flt_j,
            def_j=def_j,
            dug_in=report.dug_in,
            uf_opt=uf_opt,
            now_ts=now_ts,
        )
        rep = SpyReportRecord(
            ts=now_ts,
            target_coords=norm_c,
            owner=owner_s,
            planet_name=pname_s,
            is_spacia=1 if report.is_spacia else 0,
            pig_iron=pig_i,
            metal=met_i,
            kryptonite=kry_i,
            spice=spi_i,
            loot_spice_eq=loot_sp,
            buildings_json=bld_j,
            fleet_json=flt_j,
            defense_json=def_j,
            researches_json=res_j,
            dug_in=(1 if report.dug_in else 0) if report.dug_in is not None else None,
            uninhabited_fields=uf_opt,
            server_msg_id=sanitize_text(report.server_msg_id),
            msg_date_text=sanitize_text(report.msg_date_text),
            raw_text=sanitize_text(report.raw_text, 4000),
        )
        self._session.add(rep)
        self._session.flush()
        if refresh_hot:
            refresh_colony_candidates_hot(self._session, (norm_c,), ts=now_ts)
            gsp = parse_coords_gsp(norm_c)
            if gsp is not None and 1 <= gsp[0] <= 14 and 1 <= gsp[1] <= 400:
                refresh_galaxy_sectors_hot(self._session, ((gsp[0], gsp[1]),), ts=now_ts)
        return spy_report_to_dto(rep)

    def bulk_upsert_targets(
        self,
        targets: Sequence[SpyTargetUpsertInput],
        *,
        ts: float | None = None,
    ) -> int:
        """Bulk-upsert `spy_targets` in 1 PostgreSQL statement while preserving protection flags."""
        if not targets:
            return 0
        now_ts = round_ts(ts)
        by_coords: dict[str, dict[str, object]] = {}
        for t in targets:
            nc = normalize_coords(t.coords)
            if not nc:
                continue
            by_coords[nc] = {
                "coords": nc,
                "owner": sanitize_text(t.owner),
                "planet_name": sanitize_text(t.planet_name),
                "status_color": sanitize_text(t.status_color or "yellow"),
                "is_inactive": 1 if (t.is_inactive or t.is_spacia or t.status_color == "yellow") else 0,
                "is_spacia": 1 if t.is_spacia else 0,
                "has_debris": 1 if t.has_debris else 0,
                "points": clamp_int64(int(t.points)) if t.points is not None else None,
                "pig_iron": clamp_int64(int(t.pig_iron)),
                "metal": clamp_int64(int(t.metal)),
                "kryptonite": clamp_int64(int(t.kryptonite)),
                "spice": clamp_int64(int(t.spice)),
                "fleet_json": dumps_json(t.fleet),
                "defense_json": dumps_json(t.defense),
                "dug_in": 1 if t.dug_in else 0,
                "last_scanned_ts": round_ts(t.last_scanned_ts) if t.last_scanned_ts > 0 else now_ts,
                "last_spied_ts": round_ts(t.last_spied_ts) if t.last_spied_ts > 0 else 0.0,
                "uninhabited_fields": clamp_int64(int(t.uninhabited_fields))
                if t.uninhabited_fields is not None
                else None,
                "colonize_min_fields": clamp_int64(int(t.colonize_min_fields))
                if t.colonize_min_fields is not None
                else None,
                "colonize_max_fields": clamp_int64(int(t.colonize_max_fields))
                if t.colonize_max_fields is not None
                else None,
                "temperature": clamp_int64(int(t.temperature)) if t.temperature is not None else None,
                "merged_into": "",
            }
        if not by_coords:
            return 0
        payload = list(by_coords.values())
        stmt = pg_insert(SpyTarget).values(payload)
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[SpyTarget.coords],
                set_={
                    "merged_into": "",
                    "owner": case((stmt.excluded.owner != "", stmt.excluded.owner), else_=SpyTarget.owner),
                    "planet_name": case(
                        (stmt.excluded.planet_name != "", stmt.excluded.planet_name), else_=SpyTarget.planet_name
                    ),
                    "status_color": stmt.excluded.status_color,
                    "is_inactive": stmt.excluded.is_inactive,
                    "is_spacia": case((stmt.excluded.is_spacia == 1, 1), else_=SpyTarget.is_spacia),
                    "has_debris": stmt.excluded.has_debris,
                    "points": func.coalesce(stmt.excluded.points, SpyTarget.points),
                    "last_scanned_ts": func.greatest(SpyTarget.last_scanned_ts, stmt.excluded.last_scanned_ts),
                    "last_spied_ts": func.greatest(SpyTarget.last_spied_ts, stmt.excluded.last_spied_ts),
                    "uninhabited_fields": func.coalesce(stmt.excluded.uninhabited_fields, SpyTarget.uninhabited_fields),
                    "colonize_min_fields": func.coalesce(
                        stmt.excluded.colonize_min_fields, SpyTarget.colonize_min_fields
                    ),
                    "colonize_max_fields": func.coalesce(
                        stmt.excluded.colonize_max_fields, SpyTarget.colonize_max_fields
                    ),
                    "temperature": func.coalesce(stmt.excluded.temperature, SpyTarget.temperature),
                },
            )
        )
        return len(payload)

    def mark_own_planets(self, coords_list: Sequence[str]) -> int:
        """Mark owned empire coordinates non-destructively in 1 SQL `UPDATE`."""
        norm = sorted({nc for c in coords_list if (nc := normalize_coords(c))})
        if not norm:
            return 0
        res = self._session.execute(update(SpyTarget).where(SpyTarget.coords.in_(norm)).values(is_own_planet=1))
        return affected_rows(res)

    def set_target_debris(
        self,
        target_coords: str,
        *,
        has_debris: bool,
        refresh_hot: bool = True,
        ts: float | None = None,
    ) -> None:
        """Toggle debris flags on `spy_targets` and `galaxy_slots`, syncing meta debris amounts."""
        norm_c = normalize_coords(target_coords)
        if not norm_c:
            return
        flag = 1 if has_debris else 0
        now_ts = round_ts(ts)
        self._session.execute(update(SpyTarget).where(SpyTarget.coords == norm_c).values(has_debris=flag))
        self._session.execute(update(GalaxySlot).where(GalaxySlot.coords == norm_c).values(has_debris=flag))
        if not has_debris:
            settings = SettingsRepository(self._session)
            raw_deb = settings.get_meta_json("galaxy_debris_amounts")
            if isinstance(raw_deb, dict) and norm_c in raw_deb:
                updated = {str(k): v for k, v in raw_deb.items() if str(k) != norm_c}
                settings.upsert_meta_json("galaxy_debris_amounts", updated, ts=now_ts)
        if refresh_hot:
            gsp = parse_coords_gsp(norm_c)
            if gsp is not None and 1 <= gsp[0] <= 14 and 1 <= gsp[1] <= 400:
                refresh_galaxy_sectors_hot(self._session, ((gsp[0], gsp[1]),), ts=now_ts)

    def get_target_by_coords(self, coords: str) -> SpyTargetRecord | None:
        """Fetch a single `spy_targets` row by normalized coordinates in 1 SQL query."""
        norm_c = normalize_coords(coords)
        if not norm_c:
            return None
        row = self._session.execute(select(SpyTarget).where(SpyTarget.coords == norm_c).limit(1)).scalar_one_or_none()
        return spy_target_to_dto(row) if row is not None else None

    def get_target_by_coords_non_own(self, coords: str) -> SpyTargetRecord | None:
        """Fetch an active, non-own, unmerged `spy_targets` row matching `coords`."""
        variants = coord_query_variants(coords)
        if not variants:
            return None
        row = self._session.execute(
            select(SpyTarget)
            .where(SpyTarget.coords.in_(variants), SpyTarget.merged_into == "", SpyTarget.is_own_planet == 0)
            .order_by(case((SpyTarget.last_spied_ts > 0.0, 1), else_=0).desc(), SpyTarget.last_scanned_ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        return spy_target_to_dto(row) if row is not None else None

    def list_spy_reports(
        self,
        *,
        target_coords: str | None = None,
        limit: int = 200,
    ) -> tuple[SpyReportDTO, ...]:
        """List append-only espionage reports newest first in 1 SQL query."""
        stmt = select(SpyReportRecord)
        if target_coords is not None:
            stmt = stmt.where(SpyReportRecord.target_coords == normalize_coords(target_coords))
        rows = (
            self._session.execute(
                stmt.order_by(SpyReportRecord.ts.desc(), SpyReportRecord.id.desc()).limit(clamp_limit(limit, 200))
            )
            .scalars()
            .all()
        )
        return tuple(spy_report_to_dto(r) for r in rows)


__all__ = ["IntelRepository", "SpyReportInput", "SpyTargetUpsertInput", "spy_target_to_dto"]
