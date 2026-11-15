"""Deterministic Wave A reference storage workload for `RuntimeDatabaseGateway` vs ORM parity."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import json
import time

import psycopg
from sqlalchemy.orm import Session

from backend.db.models import BattleReport, RecycleReport
from backend.db.repos import (
    ColoniesRepository,
    ColonyCandidateHotInput,
    ManualQueueRepository,
    NotificationsRepository,
    PlannerRunsRepository,
    SettingsRepository,
    TelemetryRepository,
)
from backend.db.session import DatabaseEngine, db_uow
from backend.runtime.bridge._db_gateway import RuntimeDatabaseGateway
from tests.unit.db._workload_a_fixtures import (
    AI_PLAN_PARAMS,
    AI_PLAN_RESULT,
    ATTEMPT_SPECS,
    CFG_FLOORS,
    CFG_OPT_PARAMS,
    CFG_OPT_RESULT,
    CFG_SNAPSHOTS,
    CONFLICT_PAYLOAD,
    NOTIF_SPECS,
    PROD_SAMPLES,
    QUEUE_SPECS,
    T0,
)

WAVE_A_TABLES: tuple[str, ...] = tuple(
    (
        "ui_notifications manual_queue manual_queue_events meta strategy_config_store config_history "
        "config_optimizer_runs config_optimizer_active ai_planner_runs ai_planner_active "
        "field_conflict_prompts field_conflict_events action_history economy_rates_cache "
        "hourly_production_history loot_recycle_hourly tutorial_progress universe_research_max "
        "universe_research_max_history colony_attempts colony_candidates_hot"
    ).split()
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
        (T0, "br-ref-1", "02:380:04", 12000, 8000, 4000, 1500, 6500.0),
        (T0 + 600.0, "br-ref-2", "02:380:05", 3000, 2000, 1000, 500, 1750.0),
    )
    with DatabaseEngine.from_dsn(dsn, pool_size=1, max_overflow=0) as db, db_uow(db) as session:
        session.add_all(
            [
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
                for bts, uid, coords, pig, met, kry, spi, speq in battles
            ]
        )
        session.add(
            RecycleReport(
                ts=T0 + 300.0,
                msg_uid="rr-ref-1",
                coords="02:380:04",
                harvested_pig_iron=5000,
                harvested_metal=3000,
                harvested_spice_eq=1800.0,
            )
        )
        ColoniesRepository(session).upsert_candidates_hot(
            [
                ColonyCandidateHotInput("2:380:8", 335, None, None, 32, T0, T0, 2),
                ColonyCandidateHotInput("2:380:9", 280, None, None, 28, T0, T0, 1),
                ColonyCandidateHotInput("2:381:10", last_scanned_ts=T0),
            ],
            ts=T0,
        )


def _gateway_workload_steps(gw: RuntimeDatabaseGateway) -> None:
    n_ids: list[int] = []
    for ns in NOTIF_SPECS:
        rec = gw.create_or_bump_notification(**ns)
        assert rec is not None
        nid_val = rec["id"]
        assert isinstance(nid_val, int)
        n_ids.append(nid_val)
    gw.acknowledge_notification(f"NTF-{n_ids[0]:04d}", status="read", ts=T0 + 20.0)
    gw.acknowledge_all_notifications(status="dismissed", ts=T0 + 25.0)

    q_ids: list[int] = []
    for qs in QUEUE_SPECS:
        qid_val = gw.enqueue_manual_command(**qs)["id"]
        assert isinstance(qid_val, int)
        q_ids.append(qid_val)
    gw.update_manual_queue_item(q_ids[1], move="top", note="bumped", ts=T0 + 5.0)
    gw.complete_manual_queue_item(q_ids[0], message="Built L16", ts=T0 + 8.0)
    gw.cancel_manual_queue_item(q_ids[2], reason="Not needed", ts=T0 + 9.0)

    gw.save_meta_json("custom_flag", {"enabled": True, "count": 3}, ts=T0)
    for cfg, dt in CFG_SNAPSHOTS:
        gw.save_strategy_config_snapshot(cfg, ts=T0 + dt)

    cfg_rid = gw.record_config_optimizer_run(
        CFG_OPT_RESULT, params=CFG_OPT_PARAMS, reserved_floors=CFG_FLOORS, trigger_source="test", elapsed_ms=125.5
    )
    gw.record_ai_planner_run(
        AI_PLAN_RESULT, config_run_id=cfg_rid, params=AI_PLAN_PARAMS, trigger_source="test", elapsed_ms=210.0
    )
    gw.upsert_field_conflict_prompt(CONFLICT_PAYLOAD)
    gw.resolve_field_conflict_prompt("101:b1:20", "demolish", detail={"approved": True})
    gw.save_economy_rates(101, "02:380:12", {"b1": 100, "b2": 80}, ts=T0)
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
    gw.record_action(source="commander", command="build", action=act, executed=True, dry_run=False, message="Ok", ts=T0)
    for ps in PROD_SAMPLES:
        gw.record_hourly_production(**ps)
    gw.get_loot_recycle_hourly(hours=24, now_ts=T0)
    for att in ATTEMPT_SPECS:
        gw.record_colony_attempt(**att)
    gw.discard_colony_candidate("2:380:9", reason="too_small", ts=T0 + 30.0)


def _orm_workload_steps(session: Session) -> None:
    notifs = NotificationsRepository(session)
    n_recs = [notifs.create_or_bump(**ns) for ns in NOTIF_SPECS]
    notifs.acknowledge(n_recs[0].notification_code, status="read", ts=T0 + 20.0)
    notifs.acknowledge_all(status="dismissed", ts=T0 + 25.0)

    queue = ManualQueueRepository(session)
    q_ids = [queue.enqueue(**qs).id for qs in QUEUE_SPECS]
    queue.update_item(q_ids[1], move="top", note="bumped", ts=T0 + 5.0)
    queue.complete_item(q_ids[0], message="Built L16", ts=T0 + 8.0)
    queue.cancel_item(q_ids[2], reason="Not needed", ts=T0 + 9.0)

    settings = SettingsRepository(session)
    settings.upsert_meta_json("custom_flag", {"enabled": True, "count": 3}, ts=T0)
    for cfg, dt in CFG_SNAPSHOTS:
        settings.save_strategy_config(cfg, ts=T0 + dt)

    planner = PlannerRunsRepository(session)
    cfg_rid = planner.record_config_optimizer_run(
        CFG_OPT_RESULT,
        params=CFG_OPT_PARAMS,
        reserved_floors=CFG_FLOORS,
        trigger_source="test",
        elapsed_ms=125.5,
        ts=T0,
    )
    planner.record_ai_planner_run(
        AI_PLAN_RESULT, config_run_id=cfg_rid, params=AI_PLAN_PARAMS, trigger_source="test", elapsed_ms=210.0, ts=T0
    )
    planner.upsert_field_conflict_prompt(CONFLICT_PAYLOAD, ts=T0)
    planner.resolve_field_conflict_prompt("101:b1:20", "demolish", detail={"approved": True}, ts=T0)

    tel = TelemetryRepository(session)
    tel.upsert_economy_rates(101, "02:380:12", {"b1": 100, "b2": 80}, ts=T0)
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
        ts=T0,
    )
    for ps in PROD_SAMPLES:
        tel.record_hourly_production(**ps)
    tel.refresh_loot_recycle_hourly(ts=T0)

    col = ColoniesRepository(session)
    for att in ATTEMPT_SPECS:
        col.record_attempt(**att)
    col.discard_candidate("2:380:9", reason="too_small", ts=T0 + 30.0)


def run_gateway_workload(dsn: str) -> None:
    """Execute the deterministic Wave A reference workload through `RuntimeDatabaseGateway`."""
    _seed_shared_fixtures(dsn)
    with _frozen_time(T0):
        gw = RuntimeDatabaseGateway.from_dsn(dsn, pool_size=2, max_overflow=0, ensure_ready=False)
        try:
            _gateway_workload_steps(gw)
        finally:
            gw.close()


def run_orm_workload(dsn: str) -> None:
    """Execute the exact same Wave A reference workload through the new ORM repositories."""
    _seed_shared_fixtures(dsn)
    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with db_uow(db) as session:
            _orm_workload_steps(session)


def _normalize_snapshot_cell(col_name: str, val: object) -> object:
    if not col_name.endswith("_json") or val is None:
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if isinstance(val, str):
        try:
            parsed: object = json.loads(val)
        except ValueError:
            return val
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return val


def snapshot_tables(dsn: str, tables: Sequence[str] = WAVE_A_TABLES) -> dict[str, list[tuple[object, ...]]]:
    """Read sorted row snapshots across `tables` from PostgreSQL for exact parity diffs."""
    out: dict[str, list[tuple[object, ...]]] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in tables:
            cur = conn.execute(f'SELECT * FROM "{table}"')
            col_names = [desc.name for desc in (cur.description or ())]
            rows = cur.fetchall()
            out[table] = sorted(
                (tuple(_normalize_snapshot_cell(c, v) for c, v in zip(col_names, r, strict=True)) for r in rows),
                key=lambda row: tuple("" if v is None else str(v) for v in row),
            )
    return out


__all__ = ["WAVE_A_TABLES", "run_gateway_workload", "run_orm_workload", "snapshot_tables"]
