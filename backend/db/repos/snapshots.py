"""SQLAlchemy ORM repository for empire snapshots, hot state, and rank history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.dto import (
    EmpirePlanetHotRecord,
    LatestStateHotRecord,
    PlanetSnapshotRecord,
    RankSnapshotRecord,
    StateSnapshotRecord,
)
from backend.db.models import (
    EconomyRatesCache,
    EmpirePlanetHot,
    LatestStateHot,
    PlanetSnapshot,
    RankSnapshot,
    StateSnapshot,
)
from backend.db.repos._helpers import (
    clamp_int64,
    clamp_limit,
    dumps_json,
    normalize_coords,
    parse_coords_gsp,
    round_ts,
    sanitize_text,
    to_float,
    to_int,
)
from backend.db.repos._snapshot_inputs import (
    LATEST_PLANET_SNAPSHOTS_SQL,
    PlanetSnapshotInput,
    RankSnapshotInput,
    StateSnapshotInput,
    empire_hot_to_dto,
    hot_state_to_dto,
    planet_snap_to_dto,
    rank_to_dto,
    snap_to_latest_hot_dto,
    state_to_dto,
)


class SnapshotsRepository:
    """Atomic empire snapshot, hot-state, and rank-sample persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_full_state_snapshot(
        self,
        snapshot: StateSnapshotInput,
        *,
        min_interval_seconds: float = 0.0,
    ) -> int:
        """Append to `state_snapshots` and upsert singleton `latest_state_hot(id=1)`."""
        now_ts = round_ts(snapshot.ts)
        src_clean = sanitize_text(snapshot.source or "advisor", 32)
        if min_interval_seconds > 0.0:
            last = self._session.execute(
                select(StateSnapshot.id, StateSnapshot.ts)
                .where(StateSnapshot.source == src_clean)
                .order_by(StateSnapshot.id.desc())
                .limit(1)
            ).first()
            if last is not None:
                last_ts = to_float(last[1], -1.0)
                if last_ts >= 0.0 and abs(now_ts - last_ts) < to_float(min_interval_seconds, 0.0):
                    return to_int(last[0], 0)

        state_json_str = dumps_json(snapshot.state_payload)
        plan_json_str = dumps_json(list(snapshot.plan_payload))
        nick = sanitize_text(snapshot.nickname, 120)
        uid = clamp_int64(int(snapshot.universe_id))
        usr = clamp_int64(int(snapshot.user_id))
        act_pid = clamp_int64(int(snapshot.active_planet_id))
        pcnt = max(0, clamp_int64(int(snapshot.planet_count)))
        plan_cnt = max(0, clamp_int64(len(snapshot.plan_payload)))

        row = StateSnapshot(
            ts=now_ts,
            source=src_clean,
            universe_id=uid,
            user_id=usr,
            nickname=nick,
            active_planet_id=act_pid,
            planet_count=pcnt,
            planned_action_count=plan_cnt,
            state_json=state_json_str,
            plan_json=plan_json_str,
        )
        self._session.add(row)
        self._session.flush()

        hot_stmt = pg_insert(LatestStateHot).values(
            id=1,
            source_snapshot_id=int(row.id),
            ts=now_ts,
            source=src_clean,
            universe_id=uid,
            user_id=usr,
            nickname=nick,
            active_planet_id=act_pid,
            planet_count=pcnt,
            planned_action_count=plan_cnt,
            state_json=state_json_str,
            plan_json=plan_json_str,
            updated_ts=now_ts,
        )
        self._session.execute(
            hot_stmt.on_conflict_do_update(
                index_elements=[LatestStateHot.id],
                set_={
                    "source_snapshot_id": hot_stmt.excluded.source_snapshot_id,
                    "ts": hot_stmt.excluded.ts,
                    "source": hot_stmt.excluded.source,
                    "universe_id": hot_stmt.excluded.universe_id,
                    "user_id": hot_stmt.excluded.user_id,
                    "nickname": hot_stmt.excluded.nickname,
                    "active_planet_id": hot_stmt.excluded.active_planet_id,
                    "planet_count": hot_stmt.excluded.planet_count,
                    "planned_action_count": hot_stmt.excluded.planned_action_count,
                    "state_json": hot_stmt.excluded.state_json,
                    "plan_json": hot_stmt.excluded.plan_json,
                    "updated_ts": hot_stmt.excluded.updated_ts,
                },
            )
        )
        return int(row.id)

    def record_rank_snapshot(
        self,
        sample: RankSnapshotInput,
        *,
        min_interval_seconds: float = 300.0,
    ) -> RankSnapshotRecord | None:
        """Append a deduplicated `rank_snapshots` row in <= 2 SQL statements."""
        now_ts = round_ts(sample.ts)
        sp_opt = clamp_int64(int(sample.server_points)) if sample.server_points is not None else None
        sr_opt = clamp_int64(int(sample.server_rank)) if sample.server_rank is not None else None
        last = self._session.execute(
            select(
                RankSnapshot.ts,
                RankSnapshot.server_points,
                RankSnapshot.server_rank,
                RankSnapshot.estimated_points,
            )
            .order_by(RankSnapshot.id.desc())
            .limit(1)
        ).first()
        if last is not None:
            last_ts = to_float(last[0], -1.0)
            last_est = to_float(last[3], float("-inf"))
            min_iv = to_float(min_interval_seconds, 300.0)
            elapsed = abs(now_ts - last_ts) if last_ts >= 0.0 else min_iv
            changed_server = (sp_opt is not None and sp_opt != last[1]) or (sr_opt is not None and sr_opt != last[2])
            cur_est = to_float(sample.estimated_points, 0.0)
            changed_est = last_ts < 0.0 or last_est == float("-inf") or int(round(cur_est)) != int(round(last_est))
            if not (changed_server or changed_est or elapsed >= min_iv):
                return None

        row = RankSnapshot(
            ts=now_ts,
            universe_id=clamp_int64(to_int(sample.universe_id, 0)),
            nickname=sanitize_text(sample.nickname),
            server_points=sp_opt,
            server_rank=sr_opt,
            estimated_points=to_float(sample.estimated_points, 0.0),
            pts_per_hr=to_float(sample.pts_per_hr, 0.0),
            spice_value=to_float(sample.spice_value, 0.0),
            planets=max(0, clamp_int64(to_int(sample.planets, 0))),
        )
        self._session.add(row)
        self._session.flush()
        return rank_to_dto(row)

    def record_planet_snapshots(
        self,
        planets: Sequence[PlanetSnapshotInput],
        *,
        researches: Mapping[str, object] | None = None,
        universe_id: int = 0,
        planet_min_interval_seconds: float = 120.0,
        ts: float | None = None,
    ) -> int:
        """Batch-persist due/changed `planet_snapshots` and `economy_rates_cache` in <= 3 SQL stmts."""
        if not planets:
            return 0
        now_ts = round_ts(ts)
        res_json = dumps_json(researches or {})
        pids = sorted({clamp_int64(int(p.planet_id)) for p in planets})

        latest_ids = (
            select(func.max(PlanetSnapshot.id))
            .where(PlanetSnapshot.planet_id.in_(pids))
            .group_by(PlanetSnapshot.planet_id)
        )
        prev_rows = self._session.execute(
            select(
                PlanetSnapshot.planet_id,
                PlanetSnapshot.ts,
                PlanetSnapshot.buildings_json,
                PlanetSnapshot.ships_json,
                PlanetSnapshot.economy_rates_json,
            ).where(PlanetSnapshot.id.in_(latest_ids))
        ).all()
        prev_by_pid = {to_int(r[0], 0): (to_float(r[1], -1.0), str(r[2]), str(r[3]), str(r[4])) for r in prev_rows}

        rate_payload: dict[int, dict[str, object]] = {}
        snap_payload: list[dict[str, object]] = []
        uid_sql = clamp_int64(to_int(universe_id, 0))
        min_iv_p = to_float(planet_min_interval_seconds, 120.0)
        for p in planets:
            pid = clamp_int64(to_int(p.planet_id, 0))
            bld_j, shp_j, def_j, rates_j = (
                dumps_json(p.buildings),
                dumps_json(p.ships),
                dumps_json(p.defense),
                dumps_json(p.economy_rates),
            )
            coords_s = sanitize_text(p.coords)
            if p.economy_rates:
                rate_payload[pid] = {"planet_id": pid, "coords": coords_s, "rates_json": rates_j, "updated_ts": now_ts}
            prev = prev_by_pid.get(pid)
            if (
                prev is None
                or prev[0] < 0.0
                or abs(now_ts - prev[0]) >= min_iv_p
                or prev[1] != bld_j
                or prev[2] != shp_j
                or prev[3] != rates_j
            ):
                snap_payload.append(
                    {
                        "ts": now_ts,
                        "universe_id": uid_sql,
                        "planet_id": pid,
                        "coords": coords_s,
                        "name": sanitize_text(p.name),
                        "planet_type": sanitize_text(p.planet_type or "normal"),
                        "temperature": clamp_int64(int(p.temperature)),
                        "fields_used": clamp_int64(int(p.fields_used)),
                        "fields_max": clamp_int64(int(p.fields_max)),
                        "pig_iron": clamp_int64(int(p.pig_iron)),
                        "metal": clamp_int64(int(p.metal)),
                        "kryptonite": clamp_int64(int(p.kryptonite)),
                        "spice": clamp_int64(int(p.spice)),
                        "energy_produced": clamp_int64(int(p.energy_produced)),
                        "energy_used": clamp_int64(int(p.energy_used)),
                        "buildings_json": bld_j,
                        "ships_json": shp_j,
                        "defense_json": def_j,
                        "economy_rates_json": rates_j,
                        "researches_json": res_json,
                    }
                )
        if rate_payload:
            r_stmt = pg_insert(EconomyRatesCache).values(list(rate_payload.values()))
            self._session.execute(
                r_stmt.on_conflict_do_update(
                    index_elements=[EconomyRatesCache.planet_id],
                    set_={
                        "coords": r_stmt.excluded.coords,
                        "rates_json": r_stmt.excluded.rates_json,
                        "updated_ts": r_stmt.excluded.updated_ts,
                    },
                )
            )
        if snap_payload:
            self._session.execute(pg_insert(PlanetSnapshot).values(snap_payload))
        return len(snap_payload)

    def sync_empire_planets_hot(
        self,
        planets: Sequence[PlanetSnapshotInput],
        *,
        universe_id: int = 0,
        ts: float | None = None,
    ) -> tuple[tuple[int, int], ...]:
        """Bulk-upsert active `empire_planets_hot` rows and deactivate removed ones in <= 3 SQL stmts."""
        ts_r = round_ts(ts)
        prev_rows = self._session.execute(
            select(EmpirePlanetHot.planet_id, EmpirePlanetHot.coords).where(EmpirePlanetHot.is_active == 1)
        ).all()
        prev_by_pid = {int(r[0]): str(r[1] or "") for r in prev_rows}

        touched: set[tuple[int, int]] = set()
        payload_by_pid: dict[int, dict[str, object]] = {}
        uid_sql = clamp_int64(int(universe_id))
        for p in planets:
            pid = clamp_int64(int(p.planet_id))
            if pid <= 0:
                continue
            norm_c = normalize_coords(p.coords)
            if prev_by_pid.get(pid) != norm_c:
                for cand in (prev_by_pid.get(pid, ""), norm_c):
                    gsp = parse_coords_gsp(cand)
                    if gsp is not None and 1 <= gsp[0] <= 14 and 1 <= gsp[1] <= 400:
                        touched.add((gsp[0], gsp[1]))
            payload_by_pid[pid] = {
                "planet_id": pid,
                "universe_id": uid_sql,
                "coords": norm_c,
                "name": sanitize_text(p.name),
                "planet_type": sanitize_text(p.planet_type or "normal"),
                "temperature": clamp_int64(int(p.temperature)),
                "fields_used": clamp_int64(int(p.fields_used)),
                "fields_max": clamp_int64(int(p.fields_max)),
                "pig_iron": clamp_int64(int(p.pig_iron)),
                "metal": clamp_int64(int(p.metal)),
                "kryptonite": clamp_int64(int(p.kryptonite)),
                "spice": clamp_int64(int(p.spice)),
                "energy_produced": clamp_int64(int(p.energy_produced)),
                "energy_used": clamp_int64(int(p.energy_used)),
                "is_active": 1,
                "updated_ts": ts_r,
            }
        if payload_by_pid:
            rows_list = list(payload_by_pid.values())
            stmt = pg_insert(EmpirePlanetHot).values(rows_list)
            self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[EmpirePlanetHot.planet_id],
                    set_={k: getattr(stmt.excluded, k) for k in rows_list[0] if k != "planet_id"},
                )
            )
            self._session.execute(
                update(EmpirePlanetHot)
                .where(EmpirePlanetHot.is_active == 1, ~EmpirePlanetHot.planet_id.in_(list(payload_by_pid)))
                .values(is_active=0, updated_ts=ts_r)
            )
        return tuple(sorted(touched))

    def get_latest_state_hot(self, *, include_payload: bool = True) -> LatestStateHotRecord | None:
        """Read singleton `latest_state_hot(id=1)` (falling back to latest `state_snapshots`)."""
        hot = self._session.execute(select(LatestStateHot).where(LatestStateHot.id == 1).limit(1)).scalar_one_or_none()
        if hot is not None:
            return hot_state_to_dto(hot, include_payload=include_payload)
        snap = self._session.execute(
            select(StateSnapshot).order_by(StateSnapshot.id.desc()).limit(1)
        ).scalar_one_or_none()
        if snap is None:
            return None
        return snap_to_latest_hot_dto(snap, include_payload=include_payload)

    def list_state_snapshots(
        self, *, limit: int = 20, include_payload: bool = False
    ) -> tuple[StateSnapshotRecord, ...]:
        """List recent `state_snapshots` newest first."""
        rows = (
            self._session.execute(
                select(StateSnapshot).order_by(StateSnapshot.id.desc()).limit(clamp_limit(limit, 20, max_limit=200))
            )
            .scalars()
            .all()
        )
        return tuple(state_to_dto(r, include_payload=include_payload) for r in rows)

    def list_empire_planets_hot(self, *, active_only: bool = True) -> tuple[EmpirePlanetHotRecord, ...]:
        """List materialized owned planets ordered by `planet_id`."""
        stmt = select(EmpirePlanetHot)
        if active_only:
            stmt = stmt.where(EmpirePlanetHot.is_active == 1)
        rows = self._session.execute(stmt.order_by(EmpirePlanetHot.planet_id.asc())).scalars().all()
        return tuple(empire_hot_to_dto(r) for r in rows)

    def list_latest_planet_snapshots(self) -> tuple[PlanetSnapshotRecord, ...]:
        """Return latest snapshot per planet in 1 recursive index skip-scan + LATERAL query."""
        rows: Sequence[PlanetSnapshot] = (
            self._session.execute(select(PlanetSnapshot).from_statement(text(LATEST_PLANET_SNAPSHOTS_SQL)))
            .scalars()
            .all()
        )
        return tuple(planet_snap_to_dto(r) for r in rows)

    def list_planet_history(self, planet_id: int, *, limit: int = 50) -> tuple[PlanetSnapshotRecord, ...]:
        """List per-planet snapshots newest first."""
        stmt = (
            select(PlanetSnapshot)
            .where(PlanetSnapshot.planet_id == clamp_int64(to_int(planet_id, 0)))
            .order_by(PlanetSnapshot.id.desc())
            .limit(clamp_limit(limit, 50))
        )
        return tuple(planet_snap_to_dto(r) for r in self._session.execute(stmt).scalars().all())

    def list_rank_history(self, *, limit: int = 240) -> tuple[RankSnapshotRecord, ...]:
        """Return the latest `limit` rank samples ordered chronologically in 1 SQL statement."""
        sub = select(RankSnapshot).order_by(RankSnapshot.id.desc()).limit(clamp_limit(limit, 240)).subquery()
        stmt = select(RankSnapshot).join(sub, RankSnapshot.id == sub.c.id).order_by(RankSnapshot.id.asc())
        return tuple(rank_to_dto(r) for r in self._session.execute(stmt).scalars().all())


__all__ = ["PlanetSnapshotInput", "RankSnapshotInput", "SnapshotsRepository", "StateSnapshotInput"]
