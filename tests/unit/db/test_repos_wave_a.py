"""Exact parity, O(1) statement-count, DTO-boundary, and Section 4 tests for Wave A ORM repos."""

from __future__ import annotations

import ast
from dataclasses import is_dataclass
from pathlib import Path

from sqlalchemy.orm import DeclarativeBase

from spaceinvasion.db.models import BattleReport, RecycleReport
from spaceinvasion.db.queries import query_colony_attempts_summary, query_colony_candidates
from spaceinvasion.db.repos import (
    ColoniesRepository,
    ColonyCandidateHotInput,
    ManualQueueRepository,
    NotificationsRepository,
    PlannerRunsRepository,
    SettingsRepository,
    TelemetryRepository,
    UniverseResearchMaxInput,
)
from spaceinvasion.db.session import DatabaseEngine, count_statements, db_uow
from tests.pg_harness import DisposablePostgresServer
from tests.unit.db.reference_workload import (
    WAVE_A_TABLES,
    run_gateway_workload,
    run_orm_workload,
    snapshot_tables,
)

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_WAVE_A_DIRS: tuple[Path, ...] = (
    _REPO_ROOT / "spaceinvasion" / "db" / "dto",
    _REPO_ROOT / "spaceinvasion" / "db" / "repos",
    _REPO_ROOT / "spaceinvasion" / "db" / "queries",
)
_FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {
        "bin",
        "spaceinvasion.combat",
        "spaceinvasion.db.backend",
        "spaceinvasion.db.postgres",
        "spaceinvasion.db.storage",
        "spaceinvasion.parsing",
        "spaceinvasion.planner",
        "spaceinvasion.runtime",
        "spaceinvasion.session",
    }
)


def _assert_no_orm_leak(obj: object) -> None:
    assert not isinstance(obj, DeclarativeBase), f"ORM object leaked across DB boundary: {type(obj)}"
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _assert_no_orm_leak(item)
    elif isinstance(obj, dict):
        for val in obj.values():
            _assert_no_orm_leak(val)


def test_wave_a_orm_repositories_match_runtime_gateway_rows_exactly(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Wave A ORM repos reproduce 100% identical PostgreSQL table rows to `RuntimeDatabaseGateway`."""
    gateway_dsn = pg_harness.create_database(prefix="wave_a_gateway", from_template=True)
    orm_dsn = pg_harness.create_database(prefix="wave_a_orm", from_template=True)

    run_gateway_workload(gateway_dsn)
    run_orm_workload(orm_dsn)

    expected = snapshot_tables(gateway_dsn, WAVE_A_TABLES)
    actual = snapshot_tables(orm_dsn, WAVE_A_TABLES)
    for table in WAVE_A_TABLES:
        assert actual[table] == expected[table], f"Row parity mismatch on table `{table}`"


def test_wave_a_notifications_queue_settings_planner_statement_counts(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Notifications, queue, settings, and planner repos execute in bounded O(1) SQL and return DTOs."""
    dsn = pg_harness.create_database(prefix="wave_a_cnt_1", from_template=True)
    ts = 1700002000.0

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with db_uow(db) as session:
            notifs = NotificationsRepository(session)
            for idx in range(15):
                rec = notifs.create_or_bump(
                    title=f"Notice {idx}", message="Body", dedupe_key=f"bulk_n_{idx}", ts=ts + idx
                )
                assert is_dataclass(rec)
                _assert_no_orm_leak(rec)

        with count_statements(db, max_statements=1) as c_unread:
            with db_uow(db) as session:
                assert NotificationsRepository(session).unread_count() == 15
        assert c_unread.count == 1

        with count_statements(db, max_statements=1) as c_ack:
            with db_uow(db) as session:
                assert NotificationsRepository(session).acknowledge_all(ts=ts + 50.0) == 15
        assert c_ack.count == 1

        q_ids: list[int] = []
        with db_uow(db) as session:
            queue = ManualQueueRepository(session)
            for idx in range(12):
                item = queue.enqueue(
                    kind="build",
                    priority="P1",
                    position="bottom",
                    planet_id=101,
                    target_id=idx + 1,
                    target_name=f"Building {idx}",
                    ts=ts + idx,
                )
                q_ids.append(item.id)
                _assert_no_orm_leak(item)

        with count_statements(db, max_statements=5) as c_reord:
            with db_uow(db) as session:
                moved = ManualQueueRepository(session).update_item(q_ids[-1], move="top", ts=ts + 60.0)
                assert moved is not None and moved.sort_rank == 10
                _assert_no_orm_leak(moved)
        assert c_reord.count <= 5

        with count_statements(db, max_statements=2) as c_clear:
            with db_uow(db) as session:
                assert ManualQueueRepository(session).clear_pending(reason="bulk_clear", ts=ts + 70.0) == 12
        assert c_clear.count == 2

        with count_statements(db, max_statements=1) as c_meta:
            with db_uow(db) as session:
                entries = {f"bulk_k_{i}": f"val_{i}" for i in range(20)}
                assert SettingsRepository(session).bulk_upsert_meta(entries, ts=ts) == 20
        assert c_meta.count == 1

        with db_uow(db) as session:
            planner = PlannerRunsRepository(session)
            assert planner.get_active_config_optimizer() is None
            assert planner.get_active_ai_planner() is None


def test_wave_a_telemetry_and_colonies_statement_counts_and_dtos(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Telemetry and colony bulk upserts / set-based queries run in O(1) SQL and return DTOs."""
    dsn = pg_harness.create_database(prefix="wave_a_cnt_2", from_template=True)
    ts = 1700002000.0

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with count_statements(db, max_statements=1) as c_rates:
            with db_uow(db) as session:
                batch = [(200 + i, f"02:380:{i + 1:02d}", {"b1": 100, "b2": 90}) for i in range(10)]
                assert TelemetryRepository(session).bulk_upsert_economy_rates(batch, ts=ts) == 10
        assert c_rates.count == 1

        with count_statements(db, max_statements=2) as c_res:
            with db_uow(db) as session:
                techs = [
                    UniverseResearchMaxInput(
                        research_key=f"r{i + 1}",
                        research_id=i + 1,
                        slug=f"tech_{i + 1}",
                        name=f"Tech {i + 1}",
                        own_level=i,
                        spied_max_level=i + 2,
                        universe_max_level=i + 2,
                    )
                    for i in range(16)
                ]
                res_dtos = TelemetryRepository(session).bulk_upsert_universe_research_max(techs, ts=ts)
                assert len(res_dtos) == 16
                _assert_no_orm_leak(res_dtos)
        assert c_res.count == 2

        with db_uow(db) as session:
            for i in range(10):
                session.add(
                    BattleReport(ts=ts + i * 60.0, msg_uid=f"b_{i}", pig_iron=1000, metal=500, kryptonite=200, spice=50)
                )
                session.add(
                    RecycleReport(ts=ts + i * 60.0, msg_uid=f"r_{i}", harvested_pig_iron=300, harvested_metal=150)
                )

        with count_statements(db, max_statements=3) as c_loot:
            with db_uow(db) as session:
                assert TelemetryRepository(session).refresh_loot_recycle_hourly(ts=ts) == 1
        assert c_loot.count == 3

        with count_statements(db, max_statements=1) as c_loot_read:
            with db_uow(db) as session:
                hist, summary = TelemetryRepository(session).get_loot_recycle_hourly(hours=6, now_ts=ts)
                assert len(hist) == 6
                assert summary.total_stolen == 17500.0
                assert summary.total_recycled == 4500.0
                _assert_no_orm_leak((hist, summary))
        assert c_loot_read.count == 1

        with count_statements(db, max_statements=2) as c_tut:
            with db_uow(db) as session:
                tut = TelemetryRepository(session).record_tutorial_progress(
                    step=3,
                    total_steps=11,
                    tasks=[{"description": "Build mine", "completed": True}],
                    reward_text="500 Pig iron",
                    can_complete=True,
                    ts=ts,
                )
                assert tut.step == 3 and tut.can_complete is True
                _assert_no_orm_leak(tut)
        assert c_tut.count == 2


def test_wave_a_colonies_statement_counts_and_dtos(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Colony bulk hot-candidate upsert and set-based queries run in 1 SQL statement each."""
    dsn = pg_harness.create_database(prefix="wave_a_cnt_3", from_template=True)
    ts = 1700002000.0

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with count_statements(db, max_statements=1) as c_col_up:
            with db_uow(db) as session:
                cands = [
                    ColonyCandidateHotInput(
                        coords=f"2:380:{(i % 16) + 1}" if i < 16 else f"2:381:{(i % 16) + 1}",
                        uninhabited_fields=340 if i == 7 else (270 if i == 3 else None),
                        last_scanned_ts=ts,
                    )
                    for i in range(25)
                ]
                assert ColoniesRepository(session).upsert_candidates_hot(cands, ts=ts) == 25
        assert c_col_up.count == 1

        with count_statements(db, max_statements=1) as c_col_q:
            with db_uow(db) as session:
                ranked = query_colony_candidates(session, origin_coords="02:380:12", min_fields_target=300, limit=10)
                assert len(ranked) == 10
                assert ranked[0].coords == "02:380:08" and ranked[0].tier == 0 and ranked[0].meets_target is True
                _assert_no_orm_leak(ranked)
        assert c_col_q.count == 1

        with db_uow(db) as session:
            col = ColoniesRepository(session)
            for i in range(6):
                col.record_attempt(target_coords=f"2:385:{i + 1}", event_type="dispatched", ts=ts + i)
                col.record_attempt(
                    target_coords=f"2:385:{i + 1}",
                    event_type="kept" if i % 2 == 0 else "deleted_undersized",
                    fields_rolled=320 if i % 2 == 0 else 210,
                    ts=ts + 100 + i,
                )

        with count_statements(db, max_statements=1) as c_att_sum:
            with db_uow(db) as session:
                att_sum = query_colony_attempts_summary(session)
                assert att_sum.total_events == 12 and att_sum.kept_count == 3 and att_sum.deleted_undersized_count == 3
                _assert_no_orm_leak(att_sum)
        assert c_att_sum.count == 1


def _audit_module_standards(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    assert len(lines) <= 400, f"{path.name} exceeds 400 LOC ({len(lines)})"
    tree = ast.parse(source, filename=str(path))
    assert ast.get_docstring(tree), f"{path.name} missing module docstring"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for bad in _FORBIDDEN_IMPORTS:
                    assert not alias.name.startswith(bad), f"{path.name} imports forbidden {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            for bad in _FORBIDDEN_IMPORTS:
                assert not node.module.startswith(bad), f"{path.name} imports forbidden {node.module}"
            if node.module == "os":
                for alias in node.names:
                    assert alias.name not in {"environ", "getenv"}, f"{path.name} reads os.{alias.name}"
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                assert node.attr not in {"environ", "getenv"}, f"{path.name} reads os.{node.attr}"
        elif isinstance(node, ast.Name) and node.id == "Any":
            raise AssertionError(f"{path.name} must not use `Any`")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            raise AssertionError(f"{path.name} must not call `print()`")
        elif isinstance(node, ast.ExceptHandler):
            exc = node.type
            exc_name = exc.id if isinstance(exc, ast.Name) else ""
            if exc is None or exc_name in {"Exception", "BaseException"}:
                raise AssertionError(f"{path.name} must not use broad except")
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            limit = 400 if isinstance(node, ast.ClassDef) else 100
            assert span <= limit, f"{path.name}:{node.name} exceeds {limit} LOC ({span})"


def test_wave_a_modules_obey_section_4_standards() -> None:
    """All DTO, repository, and query modules satisfy Section 4 limits and layering."""
    targets: list[Path] = []
    for directory in _WAVE_A_DIRS:
        targets.extend(sorted(directory.rglob("*.py")))
    assert len(targets) >= 12
    for path in targets:
        _audit_module_standards(path)
