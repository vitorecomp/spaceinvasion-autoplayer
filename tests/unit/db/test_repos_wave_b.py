"""Exact parity, O(1) statement-count, NULL-sort, and Section 4 tests for Wave B ORM repos."""

from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

from sqlalchemy.orm import DeclarativeBase

from backend.db.models import Base
from backend.db.queries import (
    query_active_debris_targets,
    query_bash_window,
    query_galaxy_coverage,
    query_galaxy_sector_intel,
    query_raid_stats,
    query_spy_targets,
)
from backend.db.repos import (
    BattleReportInput,
    GalaxyRepository,
    GalaxySlotInput,
    GameMessageInput,
    IntelRepository,
    MessagesRepository,
    PlanetSnapshotInput,
    RaidsRepository,
    RankSnapshotInput,
    RecycleReportInput,
    SnapshotsRepository,
    SpyTargetUpsertInput,
    StateSnapshotInput,
)
from backend.db.session import DatabaseEngine, count_statements, db_uow
from tests.pg_harness import DisposablePostgresServer
from tests.unit.db.reference_workload import WAVE_A_TABLES, snapshot_tables
from tests.unit.db.reference_workload_b import WAVE_B_TABLES, run_gateway_workload_b, run_orm_workload_b
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


def test_wave_b_orm_repositories_match_runtime_gateway_rows_exactly(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Wave B ORM repos reproduce 100% identical PostgreSQL rows to `RuntimeDatabaseGateway` across all 22 tables."""
    gateway_dsn = pg_harness.create_database(prefix="wave_b_gateway", from_template=True)
    orm_dsn = pg_harness.create_database(prefix="wave_b_orm", from_template=True)

    run_gateway_workload_b(gateway_dsn)
    run_orm_workload_b(orm_dsn)

    expected = snapshot_tables(gateway_dsn, WAVE_B_TABLES)
    actual = snapshot_tables(orm_dsn, WAVE_B_TABLES)
    for table in WAVE_B_TABLES:
        assert actual[table] == expected[table], f"Row parity mismatch on table `{table}`"


def test_wave_b_raids_and_messages_statement_counts_and_bash_window(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Raids/messages bulk upserts and 3/24h Bash tracker execute in O(1) SQL and return DTOs."""
    dsn = pg_harness.create_database(prefix="wave_b_cnt_1", from_template=True)
    ts = 1700020000.0

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with count_statements(db, max_statements=1) as c_bat:
            with db_uow(db) as session:
                bat_batch = [
                    BattleReportInput(
                        msg_uid=f"bat-bulk-{i}",
                        target_coords=f"02:380:{(i % 10) + 1:02d}",
                        pig_iron=1000 * (i + 1),
                        metal=500 * (i + 1),
                        ts=ts + i,
                    )
                    for i in range(15)
                ]
                assert RaidsRepository(session).upsert_battle_reports(bat_batch, ts=ts) == 15
        assert c_bat.count == 1

        with count_statements(db, max_statements=1) as c_rec:
            with db_uow(db) as session:
                rec_batch = [
                    RecycleReportInput(
                        msg_uid=f"rec-bulk-{i}",
                        coords=f"02:380:{(i % 10) + 1:02d}",
                        recycler_count=2,
                        harvested_pig_iron=600,
                        harvested_metal=300,
                        ts=ts + i,
                    )
                    for i in range(15)
                ]
                assert MessagesRepository(session).upsert_recycle_reports(rec_batch, ts=ts) == 15
        assert c_rec.count == 1

        with count_statements(db, max_statements=8) as c_msg:
            with db_uow(db) as session:
                inbox_batch = [
                    GameMessageInput(
                        msg_uid=f"inb-{i}",
                        category="battle_report" if i == 0 else ("recycle_report" if i == 1 else "player"),
                        coords="02:380:04",
                        subject=f"Msg {i}",
                        body_text=f"Body {i}",
                        parsed_data={"loot": {"pig_iron": 600}}
                        if i == 0
                        else ({"harvested": {"metal": 250}} if i == 1 else {}),
                    )
                    for i in range(12)
                ]
                persisted, newly = MessagesRepository(session).ingest_messages(inbox_batch, ts=ts + 20.0)
                assert len(persisted) == 12 and len(newly) == 12
        assert c_msg.count <= 8

        with count_statements(db, max_statements=1) as c_del:
            with db_uow(db) as session:
                assert MessagesRepository(session).mark_deleted_in_game(["inb-2", "inb-3"], ts=ts + 30.0) == 2
        assert c_del.count == 1

        with count_statements(db, max_statements=1) as c_sum:
            with db_uow(db) as session:
                h_sum = MessagesRepository(session).summarize_recycle_harvests()
                assert h_sum.report_count == 16 and h_sum.total_pig_iron == 9000
                _assert_no_orm_leak(h_sum)
        assert c_sum.count == 1

        with db_uow(db) as session:
            raids = RaidsRepository(session)
            for idx in range(3):
                r_dto = raids.record_raid(
                    "02:380:04", attacked_ts=ts + 100.0 + idx * 60.0, outcome="win", loot={"spice": 100}
                )
                assert is_dataclass(r_dto)
                _assert_no_orm_leak(r_dto)
            # Exempt Spacia raids must NOT count toward the 3/24h Bash cap.
            for idx in range(4):
                raids.record_raid("02:380:06", attacked_ts=ts + 100.0 + idx * 60.0, is_exempt=True, outcome="win")

        with count_statements(db, max_statements=1) as c_bash:
            with db_uow(db) as session:
                entries = query_bash_window(session, now_ts=ts + 500.0)
                assert len(entries) == 1
                assert entries[0].target_coords == "02:380:04"
                assert entries[0].hits_in_window == 3
                assert entries[0].is_bash_blocked is True
                assert entries[0].remaining_attacks == 0
                _assert_no_orm_leak(entries)
        assert c_bash.count == 1

        with count_statements(db, max_statements=3) as c_stats:
            with db_uow(db) as session:
                stats = query_raid_stats(session, now_ts=ts + 500.0)
                assert stats.overall.attempts == 7 and stats.overall.wins == 7
                _assert_no_orm_leak(stats)
        assert c_stats.count <= 3


def test_wave_b_snapshots_and_intel_statement_counts_and_nulls_last(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Snapshot batching and intel queries run in O(1) SQL with explicit `NULLS LAST` ordering."""
    dsn = pg_harness.create_database(prefix="wave_b_cnt_2", from_template=True)
    ts = 1700025000.0
    planets = [
        PlanetSnapshotInput(
            planet_id=200 + i,
            coords=f"02:380:{i + 1:02d}",
            name=f"Colony-{i}",
            buildings={"b1": 10 + i},
            economy_rates={"b1": 100},
        )
        for i in range(10)
    ]

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with count_statements(db, max_statements=2) as c_full:
            with db_uow(db) as session:
                sid = SnapshotsRepository(session).record_full_state_snapshot(
                    StateSnapshotInput(state_payload={"ok": True}, plan_payload=[{"a": 1}], planet_count=10, ts=ts)
                )
                assert sid > 0
        assert c_full.count <= 2

        with count_statements(db, max_statements=3) as c_psnap:
            with db_uow(db) as session:
                assert SnapshotsRepository(session).record_planet_snapshots(planets, universe_id=1, ts=ts) == 10
        assert c_psnap.count <= 3

        with count_statements(db, max_statements=3) as c_ehot:
            with db_uow(db) as session:
                touched = SnapshotsRepository(session).sync_empire_planets_hot(planets, universe_id=1, ts=ts)
                assert touched == ((2, 380),)
        assert c_ehot.count <= 3

        with db_uow(db) as session:
            for idx in range(5):
                SnapshotsRepository(session).record_rank_snapshot(
                    RankSnapshotInput(estimated_points=1000.0 + idx * 100.0, planets=10, ts=ts + idx * 400.0)
                )

        with count_statements(db, max_statements=1) as c_rank:
            with db_uow(db) as session:
                hist = SnapshotsRepository(session).list_rank_history(limit=3)
                assert [r.estimated_points for r in hist] == [1200.0, 1300.0, 1400.0]
                _assert_no_orm_leak(hist)
        assert c_rank.count == 1

        with count_statements(db, max_statements=1) as c_spy_up:
            with db_uow(db) as session:
                targets = [
                    SpyTargetUpsertInput(
                        coords=f"02:385:{i + 1:02d}",
                        owner=f"P{i}",
                        status_color="yellow",
                        is_inactive=True,
                        has_debris=(i in (0, 5, 9)),
                        points=None if i == 0 else (1000 * i),
                        last_scanned_ts=ts,
                    )
                    for i in range(12)
                ]
                assert IntelRepository(session).bulk_upsert_targets(targets, ts=ts) == 12
        assert c_spy_up.count == 1

        with count_statements(db, max_statements=1) as c_spy_q:
            with db_uow(db) as session:
                # Among tied inactive non-debris rows at same `last_scanned_ts`, non-null `points` sort first (NULLS LAST).
                ranked = query_spy_targets(session, only_inactive_or_spacia=True, limit=20)
                non_deb = [r for r in ranked if not r.has_debris]
                assert non_deb[0].points == 11000
                _assert_no_orm_leak(ranked)
        assert c_spy_q.count == 1

        with count_statements(db, max_statements=1) as c_deb_q:
            with db_uow(db) as session:
                debris = query_active_debris_targets(session, origin_coords=("02:385:06",), limit=10)
                assert [d.coords for d in debris] == ["02:385:06", "02:385:10", "02:385:01"]
                _assert_no_orm_leak(debris)
        assert c_deb_q.count == 1


def test_wave_b_galaxy_batch_scan_changed_history_and_g1_g14_coverage(
    pg_harness: DisposablePostgresServer,
) -> None:
    """15-slot galaxy scan executes in bounded O(1) SQL and G1-G14 coverage runs in 1 SQL query."""
    dsn = pg_harness.create_database(prefix="wave_b_cnt_3", from_template=True)
    ts = 1700030000.0
    slots_15 = [
        GalaxySlotInput(
            coords=f"02:380:{pos:02d}",
            position=pos,
            planet_name=f"P-{pos}" if pos % 3 != 0 else "",
            owner=f"Owner-{pos}" if pos % 3 != 0 else "",
            status_color="free" if pos % 3 == 0 else ("yellow" if pos % 2 == 0 else "white"),
            is_spacia=(pos == 15),
            has_debris=(pos == 4),
            colonize_max_fields=320 if pos == 9 else None,
        )
        for pos in range(1, 16)
    ]

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        # Pure 15-slot scan without hot refresh: 4 SQL statements total (never 15 per-slot round trips!).
        with count_statements(db, max_statements=4) as c_scan_raw:
            with db_uow(db) as session:
                scan_dto = GalaxyRepository(session).record_system_scan(2, 380, slots_15, refresh_hot=False, ts=ts)
                assert scan_dto.sector_key == "02:380" and scan_dto.free_count == 5
                _assert_no_orm_leak(scan_dto)
        assert c_scan_raw.count == 4

        # Re-scan with `only_changed_slot_history=True` where only slot 4 changes -> appends exactly 1 history row.
        slots_updated = list(slots_15)
        slots_updated[3] = GalaxySlotInput(
            coords="02:380:04", position=4, planet_name="P-4", owner="Owner-4", status_color="yellow", has_debris=False
        )
        with db_uow(db) as session:
            GalaxyRepository(session).record_system_scan(
                2,
                380,
                slots_updated,
                only_changed_slot_history=True,
                sync_spy_targets=True,
                refresh_hot=True,
                ts=ts + 60.0,
            )
            hist_rows = GalaxyRepository(session).list_galaxy_slot_history(galaxy=2, system=380, limit=50)
            assert len(hist_rows) == 16

        # Full 15-slot scan + spy_targets sync + colony & sector hot refresh stays strictly O(1) (<=20 SQL stmts).
        slots_g7 = [
            GalaxySlotInput(
                coords=f"07:050:{pos:02d}",
                position=pos,
                status_color="free" if pos > 5 else "yellow",
                owner="" if pos > 5 else f"G7_{pos}",
            )
            for pos in range(1, 16)
        ]
        with count_statements(db, max_statements=20) as c_scan_full:
            with db_uow(db) as session:
                GalaxyRepository(session).record_system_scan(
                    7, 50, slots_g7, sync_spy_targets=True, refresh_hot=True, ts=ts + 120.0
                )
        assert c_scan_full.count <= 20

        with count_statements(db, max_statements=1) as c_cov:
            with db_uow(db) as session:
                cov = query_galaxy_coverage(session, max_galaxies=14)
                assert len(cov) == 14
                assert cov[1].galaxy == 2 and cov[1].scanned_systems == 1 and cov[1].coverage_pct == 0.25
                assert cov[6].galaxy == 7 and cov[6].scanned_systems == 1 and cov[6].inactive_count == 5
                _assert_no_orm_leak(cov)
        assert c_cov.count == 1

        with count_statements(db, max_statements=4) as c_intel:
            with db_uow(db) as session:
                intel = query_galaxy_sector_intel(session, max_galaxies=14)
                assert intel.active_galaxies == (2, 7)
                assert intel.universe_scanned_systems == 2
                assert len(intel.sectors) == 2
                _assert_no_orm_leak(intel)
        assert c_intel.count <= 4


def test_wave_b_galaxy_full_sync_is_strictly_constant_for_1_vs_15_slots(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Full-sync `record_system_scan` executes the exact same SQL count for 1 slot vs 15 slots (O(1))."""
    dsn = pg_harness.create_database(prefix="wave_b_o1_scan", from_template=True)
    ts = 1700031000.0

    def _occ(gal: int, sys_n: int, n: int) -> list[GalaxySlotInput]:
        return [
            GalaxySlotInput(f"{gal:02d}:{sys_n:03d}:{p:02d}", p, f"P{p}", f"O{p}", "yellow") for p in range(1, n + 1)
        ]

    def _mix(gal: int, sys_n: int, n_occ: int, n_probed: int, n_free: int) -> list[GalaxySlotInput]:
        occ = [
            GalaxySlotInput(
                f"{gal:02d}:{sys_n:03d}:{p:02d}",
                p,
                f"M{p}",
                f"MO{p}",
                "yellow",
                has_debris=(p == 1),
                debris_pig_iron=500 if p == 1 else 0,
            )
            for p in range(1, n_occ + 1)
        ]
        prb = [
            GalaxySlotInput(f"{gal:02d}:{sys_n:03d}:{p:02d}", p, status_color="free", colonize_max_fields=330)
            for p in range(n_occ + 1, n_occ + n_probed + 1)
        ]
        fre = [
            GalaxySlotInput(f"{gal:02d}:{sys_n:03d}:{p:02d}", p, status_color="free")
            for p in range(n_occ + n_probed + 1, n_occ + n_probed + n_free + 1)
        ]
        return occ + prb + fre

    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with db_uow(db) as session:
            SnapshotsRepository(session).sync_empire_planets_hot(
                [PlanetSnapshotInput(planet_id=901, coords="01:001:01", name="Home")], universe_id=1, ts=ts
            )

        def _count_scan(gal: int, sys_n: int, slots: list[GalaxySlotInput]) -> int:
            with count_statements(db) as cnt:
                with db_uow(db) as session:
                    GalaxyRepository(session).record_system_scan(
                        gal, sys_n, slots, sync_spy_targets=True, refresh_hot=True, ts=ts
                    )
            return cnt.count

        assert _count_scan(4, 100, _occ(4, 100, 1)) == _count_scan(4, 101, _occ(4, 101, 15))
        assert _count_scan(5, 200, _mix(5, 200, 1, 1, 1)) == _count_scan(5, 201, _mix(5, 201, 5, 5, 5))


def test_all_40_tables_covered_and_wave_b_standards() -> None:
    """All 40 ORM tables are covered across Wave A + Wave B + schema ledger, and all modules obey Section 4."""
    covered = set(WAVE_A_TABLES) | set(WAVE_B_TABLES) | {"schema_migrations", "schema_migration_events"}
    assert covered == set(Base.metadata.tables.keys())
    assert len(covered) == 40

    for subdir in ("dto", "repos", "queries"):
        for path in sorted((_REPO_ROOT / "backend" / "db" / subdir).rglob("*.py")):
            _audit_module_standards(path)
