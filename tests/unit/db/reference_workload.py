"""Deterministic Wave A reference storage workload for `GameDatabase` vs ORM parity."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import time
from typing import TypedDict

import psycopg
from sqlalchemy.orm import Session

from spaceinvasion.db.models import BattleReport, RecycleReport
from spaceinvasion.db.repos import (
    ColoniesRepository,
    ColonyCandidateHotInput,
    ManualQueueRepository,
    NotificationsRepository,
    PlannerRunsRepository,
    SettingsRepository,
    TelemetryRepository,
)
from spaceinvasion.db.session import DatabaseEngine, db_uow
from spaceinvasion.db.storage import GameDatabase

WAVE_A_TABLES: tuple[str, ...] = tuple(
    (
        "ui_notifications manual_queue manual_queue_events meta strategy_config_store config_history "
        "config_optimizer_runs config_optimizer_active ai_planner_runs ai_planner_active "
        "field_conflict_prompts field_conflict_events action_history economy_rates_cache "
        "hourly_production_history loot_recycle_hourly tutorial_progress universe_research_max "
        "universe_research_max_history colony_attempts colony_candidates_hot"
    ).split()
)

_T0: float = 1700001000.0
_CFG_OPT_RESULT: dict[str, object] = {
    "summary": {"ships_per_month": 42.5, "limiting_factor": "spice"},
    "meta_parameters": {"target_ship_key": "s16"},
}
_CFG_OPT_PARAMS: dict[str, object] = {"target_ship_key": "s16", "planet_specializations": {"101": "spice"}}
_CFG_FLOORS: dict[str, dict[str, int]] = {"101": {"b1": 18}}
_AI_PLAN_RESULT: dict[str, object] = {
    "summary": {"discounted_reward_spice_eq": 9999.5, "total_net_spice_eq": 12000.0},
    "steps": [{"a": 1}, {"a": 2}],
}
_AI_PLAN_PARAMS: dict[str, object] = {"reward_weeks": 26, "weekly_discount": 0.9}
_CONFLICT_PAYLOAD: dict[str, object] = {
    "conflict_key": "101:b1:20",
    "planet_id": 101,
    "planet_coords": "2:380:12",
    "blocked_building_key": "1",
    "blocked_target_level": 20,
    "candidate_building_key": "b14",
    "candidate_level": 8,
    "created_ts": _T0,
    "reason": "fields_full",
}


class _NotifKwargs(TypedDict):
    title: str
    message: str
    severity: str
    action_kind: str
    planet_coords: str
    target_name: str
    dedupe_key: str
    details: dict[str, object] | None
    ts: float


class _QueueKwargs(TypedDict):
    kind: str
    priority: str
    position: str
    planet_id: int
    planet_coords: str
    target_id: int | None
    target_name: str
    target_coords: str
    target_level: int | None
    note: str
    ts: float


_ATTEMPT_ROWS: tuple[tuple[str, str, int, str, float, int | None, int | None, int | None], ...] = (
    ("dispatched", "A", 92, "Sent", _T0, None, None, None),
    ("kept", "S", 98, "Kept", _T0 + 120.0, 202, 335, 32),
)


def _mk_notif(
    title: str,
    message: str,
    severity: str,
    dedupe_key: str,
    ts: float,
    action_kind: str = "",
    planet_coords: str = "",
    target_name: str = "",
    details: dict[str, object] | None = None,
) -> _NotifKwargs:
    return {
        "title": title,
        "message": message,
        "severity": severity,
        "action_kind": action_kind,
        "planet_coords": planet_coords,
        "target_name": target_name,
        "dedupe_key": dedupe_key,
        "details": details,
        "ts": ts,
    }


def _mk_queue(
    kind: str,
    priority: str,
    position: str,
    note: str,
    ts: float,
    target_id: int | None = None,
    target_name: str = "",
    target_coords: str = "",
    target_level: int | None = None,
) -> _QueueKwargs:
    return {
        "kind": kind,
        "priority": priority,
        "position": position,
        "planet_id": 101,
        "planet_coords": "2:380:12",
        "target_id": target_id,
        "target_name": target_name,
        "target_coords": target_coords,
        "target_level": target_level,
        "note": note,
        "ts": ts,
    }


_NOTIF_SPECS: tuple[_NotifKwargs, ...] = (
    _mk_notif(
        "Mine Blocked", "Need storage", "warning", "mine_1", _T0, "build", "02:380:12", "Pig iron mine", {"n": 5}
    ),
    _mk_notif("Mine Blocked", "Retry", "error", "mine_1", _T0 + 10.0, "build", "02:380:12", "Pig iron mine", {"n": 6}),
    _mk_notif("Spy Ready", "Probes ready", "info", "spy_1", _T0 + 15.0),
)
_QUEUE_SPECS: tuple[_QueueKwargs, ...] = (
    _mk_queue("build", "P1", "top", "first", _T0, 1, "Pig iron mine", "", 16),
    _mk_queue("build", "P1", "bottom", "second", _T0 + 1.0, 2, "Blast furnace", "", 12),
    _mk_queue("spy", "P2", "top", "scout", _T0 + 2.0, None, "", "2:380:4", None),
)
_PROD_SAMPLES: tuple[tuple[float, float, float, float, float, float, float], ...] = (
    (1000.4, 500.2, 300.1, 200.6, 2001.3, 1500.5, _T0),
    (9999.0, 9999.0, 9999.0, 9999.0, 9999.0, 9999.0, _T0 + 5.0),
)
_CFG_SNAPSHOTS: tuple[tuple[dict[str, object], float], ...] = (
    ({"planner_mode": "ai_planner", "raid_enabled": True}, 0.0),
    ({"planner_mode": "ai_planner", "raid_enabled": True}, 5.0),
    ({"planner_mode": "priority", "raid_enabled": False}, 10.0),
)


@contextmanager
def _frozen_time(ts: float) -> Iterator[None]:
    orig = time.time
    time.time = lambda: ts
    try:
        yield
    finally:
        time.time = orig


def _seed_shared_fixtures(dsn: str) -> None:
    """Insert identical combat reports and hot colony candidates into both DBs."""
    battles = (
        (_T0, "br-ref-1", "02:380:04", 12000, 8000, 4000, 1500, 6500.0),
        (_T0 + 600.0, "br-ref-2", "02:380:05", 3000, 2000, 1000, 500, 1750.0),
    )
    with DatabaseEngine.from_dsn(dsn, pool_size=1, max_overflow=0) as db:
        with db_uow(db) as session:
            for bts, uid, coords, pig, met, kry, spi, speq in battles:
                session.add(
                    BattleReport(
                        ts=bts,
                        msg_uid=uid,
                        target_coords=coords,
                        pig_iron=pig,
                        metal=met,
                        kryptonite=kry,
                        spice=spi,
                        loot_spice_eq=speq,
                    )
                )
            session.add(
                RecycleReport(
                    ts=_T0 + 300.0,
                    msg_uid="rr-ref-1",
                    coords="02:380:04",
                    harvested_pig_iron=5000,
                    harvested_metal=3000,
                    harvested_spice_eq=1800.0,
                )
            )
            ColoniesRepository(session).upsert_candidates_hot(
                [
                    ColonyCandidateHotInput("2:380:8", 335, None, None, 32, _T0, _T0, 2),
                    ColonyCandidateHotInput("2:380:9", 280, None, None, 28, _T0, _T0, 1),
                    ColonyCandidateHotInput("2:381:10", last_scanned_ts=_T0),
                ],
                ts=_T0,
            )


def _legacy_workload_steps(gdb: GameDatabase) -> None:
    n_ids: list[int] = []
    for ns in _NOTIF_SPECS:
        rec = gdb.create_or_bump_notification(**ns)
        assert rec is not None
        n_ids.append(int(rec["id"]))
    gdb.acknowledge_notification(f"NTF-{n_ids[0]:04d}", status="read", ts=_T0 + 20.0)
    gdb.acknowledge_all_notifications(status="dismissed", ts=_T0 + 25.0)

    q_ids = [int(gdb.enqueue_manual_command(**qs)["id"]) for qs in _QUEUE_SPECS]
    gdb.update_manual_queue_item(q_ids[1], move="top", note="bumped", ts=_T0 + 5.0)
    gdb.complete_manual_queue_item(q_ids[0], message="Built L16", ts=_T0 + 8.0)
    gdb.cancel_manual_queue_item(q_ids[2], reason="Not needed", ts=_T0 + 9.0)

    gdb.save_meta_json("custom_flag", {"enabled": True, "count": 3}, ts=_T0)
    for cfg, dt in _CFG_SNAPSHOTS:
        gdb.save_strategy_config_snapshot(cfg, ts=_T0 + dt)

    cfg_rid = gdb.record_config_optimizer_run(
        _CFG_OPT_RESULT, params=_CFG_OPT_PARAMS, reserved_floors=_CFG_FLOORS, trigger_source="test", elapsed_ms=125.5
    )
    gdb.record_ai_planner_run(
        _AI_PLAN_RESULT, config_run_id=cfg_rid, params=_AI_PLAN_PARAMS, trigger_source="test", elapsed_ms=210.0
    )
    gdb.upsert_field_conflict_prompt(_CONFLICT_PAYLOAD)
    gdb.resolve_field_conflict_prompt("101:b1:20", "demolish", detail={"approved": True})
    gdb.save_economy_rates(101, "02:380:12", {"b1": 100, "b2": 80}, ts=_T0)
    act = {
        "priority": "P1",
        "kind": "BUILD",
        "planet_id": 101,
        "planet_coords": "02:380:12",
        "target_id": 1,
        "target_name": "Pig iron mine",
        "target_level": 16,
        "reason": "roi",
    }
    gdb.record_action(
        source="commander", command="build", action=act, executed=True, dry_run=False, message="Ok", ts=_T0
    )
    for pig, met, kry, spi, tot, speq, pts in _PROD_SAMPLES:
        gdb.record_hourly_production(pig, met, kry, spi, tot, speq, 5, 2, ts=pts)
    with gdb._lock:
        gdb._refresh_loot_recycle_hourly_locked(ts=_T0)
        gdb._conn.commit()
    for ev, grade, score, msg, ats, pid, fields, temp in _ATTEMPT_ROWS:
        gdb.record_colony_attempt(
            target_coords="2:380:8",
            event_type=ev,
            origin_coords="2:380:12",
            planet_id=pid,
            fields_rolled=fields,
            min_fields_target=300,
            temperature=temp,
            grade=grade,
            score=score,
            status_message=msg,
            details={"wave": 1},
            ts=ats,
        )
    gdb.discard_colony_candidate("2:380:9", reason="too_small", ts=_T0 + 30.0)


def _orm_workload_steps(session: Session) -> None:
    notifs = NotificationsRepository(session)
    n_recs = [notifs.create_or_bump(**ns) for ns in _NOTIF_SPECS]
    notifs.acknowledge(n_recs[0].notification_code, status="read", ts=_T0 + 20.0)
    notifs.acknowledge_all(status="dismissed", ts=_T0 + 25.0)

    queue = ManualQueueRepository(session)
    q_ids = [queue.enqueue(**qs).id for qs in _QUEUE_SPECS]
    queue.update_item(q_ids[1], move="top", note="bumped", ts=_T0 + 5.0)
    queue.complete_item(q_ids[0], message="Built L16", ts=_T0 + 8.0)
    queue.cancel_item(q_ids[2], reason="Not needed", ts=_T0 + 9.0)

    settings = SettingsRepository(session)
    settings.upsert_meta_json("custom_flag", {"enabled": True, "count": 3}, ts=_T0)
    for cfg, dt in _CFG_SNAPSHOTS:
        settings.save_strategy_config(cfg, ts=_T0 + dt)

    planner = PlannerRunsRepository(session)
    cfg_rid = planner.record_config_optimizer_run(
        _CFG_OPT_RESULT,
        params=_CFG_OPT_PARAMS,
        reserved_floors=_CFG_FLOORS,
        trigger_source="test",
        elapsed_ms=125.5,
        ts=_T0,
    )
    planner.record_ai_planner_run(
        _AI_PLAN_RESULT, config_run_id=cfg_rid, params=_AI_PLAN_PARAMS, trigger_source="test", elapsed_ms=210.0, ts=_T0
    )
    planner.upsert_field_conflict_prompt(_CONFLICT_PAYLOAD, ts=_T0)
    planner.resolve_field_conflict_prompt("101:b1:20", "demolish", detail={"approved": True}, ts=_T0)

    tel = TelemetryRepository(session)
    tel.upsert_economy_rates(101, "02:380:12", {"b1": 100, "b2": 80}, ts=_T0)
    tel.record_action(
        source="commander",
        command="build",
        priority="P1",
        kind="BUILD",
        planet_id=101,
        planet_coords="02:380:12",
        target_id=1,
        target_name="Pig iron mine",
        target_level=16,
        executed=True,
        dry_run=False,
        reason="roi",
        message="Ok",
        ts=_T0,
    )
    for pig, met, kry, spi, tot, speq, pts in _PROD_SAMPLES:
        tel.record_hourly_production(
            pig_iron_per_hour=pig,
            metal_per_hour=met,
            kryptonite_per_hour=kry,
            spice_per_hour=spi,
            total_per_hour=tot,
            speq_per_hour=speq,
            planet_count=5,
            active_builders=2,
            ts=pts,
        )
    tel.refresh_loot_recycle_hourly(ts=_T0)

    col = ColoniesRepository(session)
    for ev, grade, score, msg, ats, pid, fields, temp in _ATTEMPT_ROWS:
        col.record_attempt(
            target_coords="2:380:8",
            event_type=ev,
            origin_coords="2:380:12",
            planet_id=pid,
            fields_rolled=fields,
            min_fields_target=300,
            temperature=temp,
            grade=grade,
            score=score,
            status_message=msg,
            details={"wave": 1},
            ts=ats,
        )
    col.discard_candidate("2:380:9", reason="too_small", ts=_T0 + 30.0)


def run_legacy_workload(dsn: str) -> None:
    """Execute the deterministic Wave A reference workload through `GameDatabase`."""
    _seed_shared_fixtures(dsn)
    with _frozen_time(_T0):
        gdb = GameDatabase(backend="postgres", postgres_url=dsn)
        try:
            _legacy_workload_steps(gdb)
        finally:
            gdb.close()


def run_orm_workload(dsn: str) -> None:
    """Execute the exact same Wave A reference workload through the new ORM repositories."""
    _seed_shared_fixtures(dsn)
    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with db_uow(db) as session:
            _orm_workload_steps(session)


def snapshot_tables(dsn: str, tables: Sequence[str] = WAVE_A_TABLES) -> dict[str, list[tuple[object, ...]]]:
    """Read sorted row snapshots across `tables` from PostgreSQL for exact parity diffs."""
    out: dict[str, list[tuple[object, ...]]] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in tables:
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            out[table] = sorted(
                (tuple(r) for r in rows),
                key=lambda row: tuple("" if v is None else str(v) for v in row),
            )
    return out


__all__ = ["WAVE_A_TABLES", "run_legacy_workload", "run_orm_workload", "snapshot_tables"]
