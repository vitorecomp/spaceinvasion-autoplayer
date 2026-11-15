"""Phase 3c Alembic revision (0002..0004) round-trip, constraint, and EXPLAIN ANALYZE tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

from alembic import command
import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from spaceinvasion.db.migrations import (
    BASELINE_REVISION,
    NO_BACKUP,
    build_alembic_config,
    get_current_revision,
    stamp_baseline_database,
    upgrade_database,
    verify_models_match_migrations,
)
from spaceinvasion.db.models import (
    ManualQueueEvent,
    ManualQueueItem,
    SchemaMigrationEvent,
    SpyTarget,
    UiNotification,
)
from spaceinvasion.db.queries import (
    query_active_debris_targets,
    query_bash_window,
    query_colony_candidates,
    query_config_optimizer_goals,
    query_dashboard_time_series,
    query_db_summary,
    query_galaxy_coverage,
    query_galaxy_sector_intel,
    query_raid_stats,
    query_spy_targets,
)
from spaceinvasion.db.repos import SnapshotsRepository
from spaceinvasion.db.session import DatabaseEngine, count_statements, db_uow
from spaceinvasion.db.store import DatabaseStore
from tests.pg_harness import DisposablePostgresServer
from tests.unit.db.reference_workload import run_orm_workload
from tests.unit.db.reference_workload_b import run_orm_workload_b
from tests.unit.db.test_live_backup_stamp import LIVE_BACKUP_ENV_VAR, _restore_dump_into_disposable_db
from tests.unit.db.test_repos_wave_a import _audit_module_standards

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_HEAD_REVISION: str = "0004"
_LARGE_TABLES: frozenset[str] = frozenset(
    {
        "galaxy_slot_history",
        "galaxy_slots",
        "spy_targets",
        "colony_candidates_hot",
        "galaxy_scan_history",
        "planet_snapshots",
    }
)


def _collect_plan_scans(node: dict[str, object], out: list[tuple[str, str, str]]) -> None:
    node_type = str(node.get("Node Type") or "")
    if "Scan" in node_type:
        out.append((node_type, str(node.get("Relation Name") or ""), str(node.get("Index Name") or "")))
    children = node.get("Plans")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _collect_plan_scans(child, out)


def _run_explain_json(
    conn: psycopg.Connection[tuple[object, ...]],
    sql: str,
) -> tuple[float, list[tuple[str, str, str]]]:
    row = conn.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}").fetchone()
    assert row is not None and isinstance(row[0], list) and row[0]
    root = row[0][0]
    assert isinstance(root, dict)
    scans: list[tuple[str, str, str]] = []
    plan_node = root.get("Plan")
    if isinstance(plan_node, dict):
        _collect_plan_scans(plan_node, scans)
    return float(root["Execution Time"]), scans


def _seed_baseline_rows_raw(dsn: str) -> int:
    with psycopg.connect(dsn, autocommit=False) as conn:
        qid_row = conn.execute(
            "INSERT INTO manual_queue(created_ts, updated_ts, status, priority, kind, planet_id, target_name) "
            "VALUES (1700050000.0, 1700050000.0, 'pending', 'P1', 'BUILD', 101, 'Pig iron mine') RETURNING id"
        ).fetchone()
        assert qid_row is not None
        qid = int(qid_row[0])
        conn.execute(
            "INSERT INTO manual_queue_events(ts, queue_id, event_type, details_json) "
            "VALUES (1700050001.0, %s, 'enqueued', %s)",
            (qid, '{"origin":"phase3c","level":18}'),
        )
        conn.execute(
            "INSERT INTO spy_targets(coords, owner, planet_name, status_color, is_inactive, is_spacia, "
            "has_debris, fleet_json, defense_json, last_scanned_ts, last_spied_ts) "
            "VALUES ('02:380:04', 'TargetA', 'P1', 'yellow', 1, 0, 1, '{\"s1\":12}', '{\"d1\":3}', 1700050002.0, 1700050002.0)"
        )
        conn.execute(
            "INSERT INTO ui_notifications(created_ts, updated_ts, severity, title, message, status, details_json) "
            "VALUES (1700050003.0, 1700050003.0, 'warning', 'Notice', 'Check', 'unread', '{\"k\":1}')"
        )
        conn.commit()
    return qid


def _assert_0003_constraints_enforce(db: DatabaseEngine) -> None:
    with pytest.raises(IntegrityError):
        with db_uow(db) as s:
            s.add(ManualQueueEvent(ts=1700050010.0, queue_id=999_999, event_type="orphan"))
            s.flush()
    with pytest.raises(IntegrityError):
        with db_uow(db) as s:
            s.add(ManualQueueItem(created_ts=1700050011.0, updated_ts=1700050011.0, status="bad", kind="BUILD"))
            s.flush()
    with pytest.raises(IntegrityError):
        with db_uow(db) as s:
            s.add(
                UiNotification(
                    created_ts=1700050012.0, updated_ts=1700050012.0, severity="bogus", title="Bad", message="Bad"
                )
            )
            s.flush()


def test_phase3c_upgrade_downgrade_roundtrip_preserves_data_and_zero_drift(
    pg_harness: DisposablePostgresServer,
) -> None:
    """`0001 -> 0002 -> 0003 -> 0004 -> downgrade('0001') -> upgrade('0004')` is lossless with zero drift."""
    dsn = pg_harness.create_database(prefix="p3c_rt", from_template=False)

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        upgrade_database(db, BASELINE_REVISION, backup_fn=NO_BACKUP)
        qid = _seed_baseline_rows_raw(dsn)

        assert upgrade_database(db, "0002", backup_fn=NO_BACKUP).current_revision == "0002"
        with db_uow(db) as s:
            col_type, s1_val = s.execute(
                text(
                    "SELECT pg_typeof(fleet_json)::text, fleet_json->>'s1' FROM spy_targets WHERE coords = '02:380:04'"
                )
            ).one()
            assert col_type == "jsonb" and s1_val == "12"

        assert upgrade_database(db, "0003", backup_fn=NO_BACKUP).current_revision == "0003"
        _assert_0003_constraints_enforce(db)

        assert upgrade_database(db, _HEAD_REVISION, backup_fn=NO_BACKUP).current_revision == _HEAD_REVISION
        assert verify_models_match_migrations(db) == []
        with db_uow(db) as s:
            brin_cnt: object = s.execute(
                text("SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public' AND indexdef ILIKE '%USING brin%'")
            ).scalar_one()
            assert isinstance(brin_cnt, int) and brin_cnt == 7
            ev_names = list(
                s.execute(select(SchemaMigrationEvent.name).order_by(SchemaMigrationEvent.id.asc())).scalars().all()
            )
            assert ev_names == [
                "0001_baseline_schema",
                "0002_jsonb_payload_columns",
                "0003_foreign_keys_and_checks",
                "0004_composite_partial_brin_indexes",
            ]

        with db.engine.begin() as conn:
            command.downgrade(build_alembic_config(connection=conn), BASELINE_REVISION)
        assert get_current_revision(db) == BASELINE_REVISION
        with db_uow(db) as s:
            col_type_dn, fleet_txt = s.execute(
                text("SELECT pg_typeof(fleet_json)::text, fleet_json FROM spy_targets WHERE coords = '02:380:04'")
            ).one()
            assert col_type_dn == "text" and json.loads(str(fleet_txt)) == {"s1": 12}
            ev_det: object = s.execute(
                text("SELECT details_json FROM manual_queue_events WHERE queue_id = :qid"),
                {"qid": qid},
            ).scalar_one()
            assert json.loads(str(ev_det)) == {"origin": "phase3c", "level": 18}

        assert upgrade_database(db, _HEAD_REVISION, backup_fn=NO_BACKUP).current_revision == _HEAD_REVISION
        assert verify_models_match_migrations(db) == []
        with db_uow(db) as s:
            target = s.execute(select(SpyTarget).where(SpyTarget.coords == "02:380:04")).scalar_one()
            assert json.loads(target.fleet_json) == {"s1": 12}


def test_phase3c_orm_workloads_and_queries_pass_at_revision_0004(
    pg_harness: DisposablePostgresServer,
) -> None:
    """All Wave A + Wave B repos and Phase 3c optimized queries pass with bounded SQL on `0004`."""
    dsn = pg_harness.create_database(prefix="p3c_0004_work", from_template=False)
    with DatabaseStore.from_dsn(dsn, pool_size=2, max_overflow=0) as store:
        assert store.ensure_ready(backup_fn=NO_BACKUP, verify_schema=True).current_revision == _HEAD_REVISION
        assert store.verify().ok is True

    run_orm_workload(dsn)
    run_orm_workload_b(dsn)

    with DatabaseStore.from_dsn(dsn, pool_size=2, max_overflow=0) as store:
        with count_statements(store.db_engine, max_statements=1) as c_spy:
            with store.session() as s:
                assert len(query_spy_targets(s, limit=25)) >= 1
        assert c_spy.count == 1
        with count_statements(store.db_engine, max_statements=1) as c_col:
            with store.session() as s:
                assert len(query_colony_candidates(s, origin_coords="02:380:12", limit=50)) >= 1
        assert c_col.count == 1
        with count_statements(store.db_engine, max_statements=1) as c_snap:
            with store.session() as s:
                assert len(SnapshotsRepository(s).list_latest_planet_snapshots()) >= 1
        assert c_snap.count == 1
        with count_statements(store.db_engine, max_statements=4) as c_gal:
            with store.session() as s:
                assert len(query_galaxy_sector_intel(s).sectors) >= 1
        assert c_gal.count == 4


def test_phase3c_modules_obey_section4_standards() -> None:
    """All new and edited Phase 3c modules obey Section 4 LOC/typing/layering standards."""
    paths = [
        _REPO_ROOT / "spaceinvasion" / "db" / "store.py",
        *sorted((_REPO_ROOT / "spaceinvasion" / "db" / "models").glob("*.py")),
        *sorted((_REPO_ROOT / "spaceinvasion" / "db" / "migrations" / "versions").glob("*.py")),
        *sorted((_REPO_ROOT / "spaceinvasion" / "db" / "queries").glob("*.py")),
        _REPO_ROOT / "spaceinvasion" / "db" / "repos" / "snapshots.py",
        _REPO_ROOT / "spaceinvasion" / "db" / "repos" / "_galaxy_sync.py",
        _REPO_ROOT / "spaceinvasion" / "db" / "repos" / "_message_ingest.py",
    ]
    for path in paths:
        _audit_module_standards(path)


def _live_explain_queries() -> tuple[tuple[str, str], ...]:
    not_empty = (
        "NOT (status_color = 'free' AND owner = '' AND is_spacia = 0 AND has_debris = 0 "
        "AND last_spied_ts <= 0.0 AND uninhabited_fields IS NULL AND colonize_max_fields IS NULL)"
    )
    spy_branches = [
        f"(SELECT coords, {a + b + c} AS pscore, {sf} AS hspy, last_scanned_ts, points "
        f"FROM spy_targets WHERE merged_into = '' AND is_own_planet = 0 "
        f"AND is_inactive = {a} AND is_spacia = {b} AND has_debris = {c} AND {sc} AND {not_empty} "
        f"ORDER BY last_scanned_ts DESC, points DESC NULLS LAST, coords ASC LIMIT 25)"
        for a in (1, 0)
        for b in (1, 0)
        for c in (1, 0)
        for sc, sf in (("last_spied_ts > 0.0", 1), ("last_spied_ts <= 0.0", 0))
    ]
    return (
        (
            "spy_targets_default_top25",
            "WITH u AS ("
            + " UNION ALL ".join(spy_branches)
            + ") SELECT s.coords FROM u JOIN spy_targets s ON s.coords = u.coords "
            "ORDER BY u.pscore DESC, u.hspy DESC, u.last_scanned_ts DESC, u.points DESC NULLS LAST, u.coords ASC LIMIT 25",
        ),
        (
            "spy_targets_debris",
            "SELECT * FROM spy_targets WHERE merged_into = '' AND is_own_planet = 0 AND has_debris = 1 "
            "ORDER BY last_scanned_ts DESC, points DESC NULLS LAST, coords ASC LIMIT 100",
        ),
        (
            "spy_targets_blocked",
            "SELECT coords, blocked_until_ts, block_reason, bash_hits, last_blocked_ts "
            "FROM spy_targets WHERE blocked_until_ts > 1700000000.0 ORDER BY blocked_until_ts DESC, coords ASC",
        ),
        (
            "spy_targets_spied",
            "SELECT * FROM spy_targets WHERE merged_into = '' AND is_own_planet = 0 AND last_spied_ts > 0.0 "
            "ORDER BY last_scanned_ts DESC, coords ASC LIMIT 50",
        ),
        (
            "planet_snapshots_latest",
            "WITH RECURSIVE pids AS ("
            "  (SELECT planet_id FROM planet_snapshots ORDER BY planet_id ASC LIMIT 1)"
            "  UNION ALL "
            "  SELECT (SELECT ps.planet_id FROM planet_snapshots ps"
            "          WHERE ps.planet_id > pids.planet_id ORDER BY ps.planet_id ASC LIMIT 1)"
            "  FROM pids WHERE pids.planet_id IS NOT NULL"
            ") SELECT latest.id, latest.ts, latest.planet_id FROM pids "
            "CROSS JOIN LATERAL ("
            "  SELECT ps.* FROM planet_snapshots ps WHERE ps.planet_id = pids.planet_id"
            "  ORDER BY ps.ts DESC, ps.id DESC LIMIT 1"
            ") latest WHERE pids.planet_id IS NOT NULL ORDER BY latest.planet_id ASC",
        ),
        (
            "colony_candidates_top200",
            "WITH tiered AS ("
            "  (SELECT coords, 0 AS tier, (-max_fields_est)::double precision AS ps FROM colony_candidates_hot "
            "   WHERE is_active = 1 AND is_discarded = 0 AND is_probed = 1 AND max_fields_est >= 300 "
            "   ORDER BY (-max_fields_est)::double precision ASC, coords ASC LIMIT 200) UNION ALL "
            "  (SELECT coords, 1 AS tier, 1000.0::double precision AS ps FROM colony_candidates_hot "
            "   WHERE is_active = 1 AND is_discarded = 0 AND is_probed = 0 AND position >= 7 AND position <= 12 "
            "   ORDER BY coords ASC LIMIT 200) UNION ALL "
            "  (SELECT coords, 2 AS tier, -30.0::double precision AS ps FROM colony_candidates_hot "
            "   WHERE is_active = 1 AND is_discarded = 0 AND is_probed = 0 AND (position < 7 OR position > 12) "
            "   ORDER BY coords ASC LIMIT 200) UNION ALL "
            "  (SELECT coords, 3 AS tier, (-max_fields_est)::double precision AS ps FROM colony_candidates_hot "
            "   WHERE is_active = 1 AND is_discarded = 0 AND is_probed = 1 AND max_fields_est < 300 "
            "   ORDER BY (-max_fields_est)::double precision ASC, coords ASC LIMIT 200)"
            ") SELECT c.coords, t.tier FROM tiered t JOIN colony_candidates_hot c ON c.coords = t.coords "
            "ORDER BY t.tier ASC, t.ps ASC, t.coords ASC LIMIT 200",
        ),
        (
            "galaxy_slot_history_recent",
            "SELECT id, ts, coords, status_color FROM galaxy_slot_history "
            "WHERE ts >= (SELECT MAX(ts) - 3600 FROM galaxy_slot_history) ORDER BY ts DESC LIMIT 100",
        ),
        (
            "galaxy_scan_history_recent",
            "SELECT id, ts, galaxy, system FROM galaxy_scan_history "
            "WHERE ts >= (SELECT MAX(ts) - 3600 FROM galaxy_scan_history) ORDER BY ts DESC LIMIT 100",
        ),
        (
            "action_history_throttle_cooldown",
            "SELECT planet_id, MAX(ts) AS last_ts FROM action_history "
            "WHERE kind = 'THROTTLE_BUILDING' AND executed = 1 AND dry_run = 0 AND planet_id IS NOT NULL "
            "GROUP BY planet_id",
        ),
        (
            "count_spy_targets_active",
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE is_inactive = 1), COUNT(*) FILTER (WHERE is_spacia = 1), "
            "COUNT(*) FILTER (WHERE has_debris = 1) FROM spy_targets WHERE merged_into = '' AND is_own_planet = 0",
        ),
        (
            "galaxy_coverage_g1_g14",
            "SELECT galaxy, COUNT(*), SUM(occupied_count), SUM(target_count) "
            "FROM galaxy_sector_hot WHERE galaxy BETWEEN 1 AND 14 AND system BETWEEN 1 AND 400 "
            "GROUP BY galaxy ORDER BY galaxy",
        ),
        (
            "ui_notifications_unread",
            "SELECT id, updated_ts, title FROM ui_notifications WHERE status = 'unread' "
            "ORDER BY updated_ts DESC, id DESC LIMIT 50",
        ),
    )


@pytest.mark.live_backup
def test_phase3c_live_backup_roundtrip_and_explain_plans_under_50ms(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Opt-in: 386k-row restore round-trips `0001 -> 0004 -> 0001 -> 0004` and keeps all hot plans <50ms."""
    raw_path = os.environ.get(LIVE_BACKUP_ENV_VAR, "").strip()
    if not raw_path:
        pytest.skip(f"Set {LIVE_BACKUP_ENV_VAR} to a custom-format PostgreSQL dump path to run this test")
    dump_path = Path(raw_path)
    if not dump_path.is_file():
        pytest.skip(f"{LIVE_BACKUP_ENV_VAR} does not point to an existing file")

    dsn = pg_harness.create_database(prefix="p3c_live", from_template=False)
    db_name = dsn.split("///", 1)[1].split("?", 1)[0]
    _restore_dump_into_disposable_db(pg_harness, dump_path, db_name)

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        stamp_baseline_database(db, verify_schema=False)
        assert upgrade_database(db, _HEAD_REVISION, backup_fn=NO_BACKUP).current_revision == _HEAD_REVISION
        assert verify_models_match_migrations(db) == []

        with db.engine.begin() as conn:
            command.downgrade(build_alembic_config(connection=conn), BASELINE_REVISION)
        assert get_current_revision(db) == BASELINE_REVISION

        assert upgrade_database(db, _HEAD_REVISION, backup_fn=NO_BACKUP).current_revision == _HEAD_REVISION
        assert verify_models_match_migrations(db) == []

        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("VACUUM ANALYZE")

        with db_uow(db) as s:
            assert len(query_spy_targets(s, limit=25)) == 25
            assert len(query_active_debris_targets(s, limit=100)) >= 0
            assert len(query_colony_candidates(s, origin_coords="02:380:12", limit=200)) == 200
            assert len(query_galaxy_coverage(s)) == 14
            assert len(query_galaxy_sector_intel(s).sectors) > 0
            _ = query_bash_window(s)
            _ = query_raid_stats(s)
            _ = query_dashboard_time_series(s)
            _ = query_config_optimizer_goals(s)
            assert query_db_summary(s).alembic_revision == _HEAD_REVISION
            assert len(SnapshotsRepository(s).list_latest_planet_snapshots()) > 0

        with psycopg.connect(dsn, autocommit=True) as conn:
            for qname, sql in _live_explain_queries():
                exec_ms, scans = _run_explain_json(conn, sql)
                assert exec_ms < 50.0, f"{qname} exceeded 50ms budget: {exec_ms:.2f}ms"
                bad_seq = [rel for nt, rel, _ in scans if nt == "Seq Scan" and rel in _LARGE_TABLES]
                assert bad_seq == [], f"{qname} had unexplained Seq Scan on large table(s): {bad_seq}"
