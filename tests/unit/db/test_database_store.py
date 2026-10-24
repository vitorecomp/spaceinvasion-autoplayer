"""Parity, integrity, and statement-count tests for `DatabaseStore` and dashboard queries."""

from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import DeclarativeBase

from spaceinvasion.db.migrations import BASELINE_REVISION, NO_BACKUP
from spaceinvasion.db.models import Base, SchemaMigration
from spaceinvasion.db.queries import (
    GAME_REQUIRED_TABLES,
    HISTORY_TABLES,
    HOT_TABLES,
    query_config_optimizer_goals,
    query_dashboard_time_series,
    query_db_summary,
)
from spaceinvasion.db.repos import BattleReportInput, RecycleReportInput
from spaceinvasion.db.session import DatabaseEngine, count_statements, db_uow
from spaceinvasion.db.store import DatabaseStore
from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway
from tests.pg_harness import DisposablePostgresServer
from tests.unit.db.reference_workload_b import run_orm_workload_b
from tests.unit.db.test_repos_wave_a import _audit_module_standards

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]


def _assert_no_orm_leak(obj: object) -> None:
    assert not isinstance(obj, DeclarativeBase), f"ORM object leaked across DB boundary: {type(obj)}"
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _assert_no_orm_leak(item)
    elif isinstance(obj, dict):
        for val in obj.values():
            _assert_no_orm_leak(val)


def test_database_store_ensure_ready_and_verify_fresh_and_existing_v14(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`DatabaseStore.ensure_ready` upgrades fresh DBs and stamps existing V14 DBs cleanly."""
    fresh_dsn = pg_harness.create_database(prefix="store_fresh", from_template=False)
    backup_calls: list[str] = []

    def _record_backup() -> str:
        backup_calls.append("backup")
        return "/tmp/synthetic_backup.dump"

    with DatabaseStore.from_dsn(fresh_dsn, pool_size=2, max_overflow=0) as store:
        mig_res = store.ensure_ready(backup_fn=_record_backup, verify_schema=True)
        assert mig_res.current_revision == BASELINE_REVISION
        assert len(backup_calls) == 1

        verify = store.verify()
        assert verify.ok is True
        assert verify.backend == "postgres"
        assert verify.alembic_revision == BASELINE_REVISION
        assert verify.target_revision == BASELINE_REVISION
        assert verify.up_to_date is True
        assert verify.missing_tables == ()
        assert verify.schema_diffs == ()
        assert verify.ledger_ok is True
        assert verify.ledger_events_count >= 1
        assert len(verify.table_tiers.hot_tables) == len(HOT_TABLES) == 19
        assert len(verify.table_tiers.history_tables) == len(HISTORY_TABLES) == 21
        assert len(GAME_REQUIRED_TABLES) == 40
        _assert_no_orm_leak(verify)

        # Idempotent second call performs zero extra upgrades or backups.
        second_res = store.ensure_ready(backup_fn=_record_backup, verify_schema=True)
        assert second_res.current_revision == BASELINE_REVISION
        assert len(backup_calls) == 1

    # Existing V14 database: `ensure_ready` stamps `0001` with zero table DDL and preserves all rows.
    v14_dsn = pg_harness.create_database(prefix="store_v14", from_template=False)
    with DatabaseEngine.from_dsn(v14_dsn, pool_size=1, max_overflow=0) as raw_eng:
        Base.metadata.create_all(raw_eng.engine)
        with db_uow(raw_eng) as sess:
            sess.add(
                SchemaMigration(
                    version=14,
                    version_tag="V014",
                    name="legacy_v14",
                    description="Simulated pre-Alembic V14 schema",
                    checksum="legacy",
                    applied_ts=1700000100.0,
                    duration_ms=1.0,
                    app_schema_version="14",
                )
            )
    run_orm_workload_b(v14_dsn)
    with DatabaseStore.from_dsn(v14_dsn, pool_size=2, max_overflow=0) as v14_store:
        stamp_res = v14_store.ensure_ready(backup_fn=NO_BACKUP, allow_stamp_existing=True, verify_schema=True)
        assert stamp_res.current_revision == BASELINE_REVISION
        v14_verify = v14_store.verify()
        assert v14_verify.ok is True
        assert v14_verify.schema_diffs == ()
        assert v14_verify.table_tiers.hot_total_rows > 0
        assert v14_verify.table_tiers.history_total_rows > 0


def test_query_db_summary_matches_runtime_gateway_summary_with_bounded_sql(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`query_db_summary` matches `RuntimeDatabaseGateway.summary()` counts/tiers in <=8 SQL statements."""
    dsn = pg_harness.create_database(prefix="store_summary", from_template=True)
    run_orm_workload_b(dsn)

    with DatabaseStore.from_dsn(dsn, pool_size=2, max_overflow=0) as store:
        store.ensure_ready(backup_fn=NO_BACKUP, verify_schema=True)
        with store.repositories() as repos:
            repos.telemetry.record_action(
                source="commander",
                command="build",
                priority="P1",
                kind="BUILD",
                planet_id=101,
                planet_coords="02:380:12",
                target_id=1,
                target_name="Pig iron mine",
                target_level=19,
                executed=True,
                dry_run=False,
                message="Started",
                ts=1700010500.0,
            )

    gw = RuntimeDatabaseGateway.from_dsn(dsn, pool_size=2, max_overflow=0, ensure_ready=False)
    try:
        gw_sum = gw.summary()
    finally:
        gw.close()

    with DatabaseStore.from_dsn(dsn, pool_size=2, max_overflow=0) as store:
        with count_statements(store.db_engine, max_statements=8) as c_sum:
            with store.session() as session:
                orm_sum = query_db_summary(session, recent_actions_limit=12, spy_targets_limit=25)
        assert c_sum.count <= 8
        assert is_dataclass(orm_sum)
        _assert_no_orm_leak(orm_sum)

    assert orm_sum.backend == "postgres"
    assert orm_sum.size_bytes > 0
    assert orm_sum.alembic_revision == BASELINE_REVISION
    gw_counts = gw_sum["counts"]
    assert isinstance(gw_counts, dict)
    assert "meta" not in gw_counts and len(gw_counts) == 39
    for tbl, gw_cnt in gw_counts.items():
        assert orm_sum.counts[tbl] == gw_cnt, f"Count mismatch on `{tbl}`"
    gw_tiers = gw_sum["table_tiers"]
    assert isinstance(gw_tiers, dict)
    assert orm_sum.table_tiers.hot_counts == gw_tiers["hot_counts"]
    assert orm_sum.table_tiers.history_counts == gw_tiers["history_counts"]
    assert orm_sum.table_tiers.hot_total_rows == gw_tiers["hot_total_rows"]
    assert orm_sum.table_tiers.history_total_rows == gw_tiers["history_total_rows"]
    assert orm_sum.message_categories == gw_sum["message_categories"]
    assert list(orm_sum.persisted_meta_keys) == gw_sum["persisted_meta_keys"]
    gw_tut = gw_sum["tutorial_history"]
    gw_act = gw_sum["recent_actions"]
    gw_spy = gw_sum["spy_targets"]
    assert isinstance(gw_tut, list) and isinstance(gw_act, list) and isinstance(gw_spy, list)
    assert len(orm_sum.tutorial_history) == len(gw_tut)
    assert len(orm_sum.recent_actions) == len(gw_act) == 1
    assert len(orm_sum.spy_targets) == len(gw_spy)
    assert orm_sum.latest_snapshot_ts == 1700010000.0
    assert orm_sum.latest_scan_ts == 1700010007.0
    assert orm_sum.latest_raid_ts == 1700010200.0


def test_query_dashboard_time_series_full_and_incremental_deltas(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`query_dashboard_time_series` matches legacy series and supports incremental `since_minute_bucket`."""
    dsn = pg_harness.create_database(prefix="store_series", from_template=True)
    base_ts = 1700040000.0

    with DatabaseStore.from_dsn(dsn, pool_size=2, max_overflow=0) as store:
        with store.repositories() as repos:
            for idx in range(6):
                repos.telemetry.record_hourly_production(
                    pig_iron_per_hour=1000.0 + idx * 100.0,
                    metal_per_hour=600.0 + idx * 50.0,
                    kryptonite_per_hour=300.0,
                    spice_per_hour=150.0,
                    total_per_hour=2050.0 + idx * 150.0,
                    speq_per_hour=650.0 + idx * 40.0,
                    planet_count=3,
                    active_builders=2,
                    ts=base_ts + idx * 60.0,
                )
            repos.raids.upsert_battle_reports(
                [
                    BattleReportInput(
                        msg_uid="ts-bat-1", target_coords="02:380:04", pig_iron=6000, metal=2500, ts=base_ts
                    )
                ],
                ts=base_ts,
            )
            repos.messages.upsert_recycle_reports(
                [
                    RecycleReportInput(
                        msg_uid="ts-rec-1",
                        coords="02:380:04",
                        harvested_pig_iron=1800,
                        harvested_metal=1200,
                        ts=base_ts,
                    )
                ],
                ts=base_ts,
            )
            repos.telemetry.refresh_loot_recycle_hourly(ts=base_ts)

        with count_statements(store.db_engine, max_statements=2) as c_full:
            with store.session() as session:
                full = query_dashboard_time_series(session, production_limit=120, loot_hours=6, now_ts=base_ts)
        assert c_full.count == 2
        assert full.incremental is False
        assert len(full.hourly_production) == 6
        assert full.latest_minute_bucket == full.hourly_production[-1].minute_bucket
        assert full.loot_recycle_summary.total_stolen == 8500.0
        assert full.loot_recycle_summary.total_recycled == 3000.0
        _assert_no_orm_leak(full)

        cutoff_bucket = full.hourly_production[2].minute_bucket
        with count_statements(store.db_engine, max_statements=2) as c_inc:
            with store.session() as session:
                delta = query_dashboard_time_series(
                    session,
                    production_limit=120,
                    since_minute_bucket=cutoff_bucket,
                    loot_hours=6,
                    now_ts=base_ts,
                )
        assert c_inc.count == 2
        assert delta.incremental is True
        assert [row.minute_bucket for row in delta.hourly_production] == [
            row.minute_bucket for row in full.hourly_production[3:]
        ]
        assert delta.latest_minute_bucket == full.latest_minute_bucket

    gw = RuntimeDatabaseGateway.from_dsn(dsn, pool_size=2, max_overflow=0, ensure_ready=False)
    try:
        gw_prod = gw.get_hourly_production_history(limit=120)
        gw_loot = gw.get_loot_recycle_hourly(hours=6, now_ts=base_ts)
    finally:
        gw.close()
    assert [r.minute_bucket for r in full.hourly_production] == [
        int(r["minute_bucket"]) for r in gw_prod if isinstance(r["minute_bucket"], int)
    ]
    assert [r.total_per_hour for r in full.hourly_production] == [
        float(r["total_per_hour"]) for r in gw_prod if isinstance(r["total_per_hour"], (int, float))
    ]
    gw_loot_sum = gw_loot["summary"]
    assert isinstance(gw_loot_sum, dict)
    assert full.loot_recycle_summary.grand_total == float(gw_loot_sum["grand_total"])


def test_query_config_optimizer_goals_and_store_uow_atomicity(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`query_config_optimizer_goals` extracts `b1..b21` in 1 SQL query and `DatabaseStore` rolls back atomically."""
    dsn = pg_harness.create_database(prefix="store_goals", from_template=True)
    ts = 1700045000.0

    with DatabaseStore.from_dsn(dsn, pool_size=2, max_overflow=0) as store:
        with store.session() as session:
            assert query_config_optimizer_goals(session) is None

        # Verify atomic rollback via `store.repositories()`.
        with pytest.raises(RuntimeError, match="abort_uow"):
            with store.repositories() as repos:
                repos.settings.upsert_meta("should_rollback", "1", ts=ts)
                raise RuntimeError("abort_uow")
        with store.session() as session:
            assert store.settings_repo(session).get_meta("should_rollback") is None

        opt_result: dict[str, object] = {
            "summary": {"ships_per_month": 55.0, "limiting_factor": "spice"},
            "planets": [
                {
                    "planet_id": 101,
                    "coords": "2:380:12",
                    "detected_specialization": "spice",
                    "buildings": [{"key": "b1", "target_level": 24}, {"key": "b4", "target_level": 22}],
                },
                {
                    "planet_id": 102,
                    "coords": "03:120:07",
                    "locked_specialization": "metal",
                    "buildings": [{"key": "b2", "target_level": 20}],
                },
                {
                    "planet_id": 999,
                    "coords": "04:001:01",
                    "is_placeholder": True,
                    "buildings": [{"key": "b1", "target_level": 40}],
                },
            ],
        }
        with store.repositories() as repos:
            run_id = repos.planner_runs.record_config_optimizer_run(opt_result, trigger_source="test", ts=ts)

        with count_statements(store.db_engine, max_statements=1) as c_goals:
            with store.session() as session:
                goals = query_config_optimizer_goals(session)
        assert c_goals.count == 1
        assert goals is not None
        assert goals.run_id == run_id
        assert set(goals.by_planet_id) == {"101", "102"}
        assert set(goals.by_coords) == {"02:380:12", "03:120:07"}
        p101 = goals.by_planet_id["101"]
        assert p101.specialization == "spice"
        assert p101.goals["b1"] == 24 and p101.goals["b4"] == 22 and p101.goals["b21"] == 0
        assert len(p101.goals) == 21
        assert goals.by_coords["03:120:07"].specialization == "metal"
        _assert_no_orm_leak(goals)


def test_store_and_dashboard_modules_obey_section_4_standards() -> None:
    """`store.py`, `queries/dashboard.py`, and `dto/dashboard.py` pass Section 4 limits and layering."""
    for rel in (
        "spaceinvasion/db/store.py",
        "spaceinvasion/db/queries/dashboard.py",
        "spaceinvasion/db/dto/dashboard.py",
    ):
        _audit_module_standards(_REPO_ROOT / rel)
