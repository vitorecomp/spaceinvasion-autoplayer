"""Tests for database schema versioning, forward migrations, backups, checksums, CLI, and API."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import re
from sqlite3 import DatabaseError, OperationalError
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

import requests

from spaceinvasion.runtime.api_server import (
    GameApiBridge,
    create_api_server,
)
from spaceinvasion.db.migrations import (
    GAME_DB_MIGRATIONS,
    GAME_REQUIRED_TABLES,
    HISTORY_TABLES,
    HOT_TABLES,
    LATEST_SCHEMA_VERSION,
    SCHEMA_VERSION,
    TABLE_TIER_BY_NAME,
    Migration,
    MigrationRunner,
    main as migrations_cli_main,
)
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
)
from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.core.model import PlannedAction
from tests.pg_harness import get_shared_pg_harness


class TestDatabaseMigrationsAndVersioning(unittest.TestCase):
    def test_staged_forward_migrations_dry_run_user_version_and_append_only_invariants(self) -> None:
        for src_path in (Path("spaceinvasion/db/migrations.py"), Path("spaceinvasion/db/storage.py")):
            txt = src_path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bDELETE\s+FROM\b", txt, re.IGNORECASE), str(src_path))
            self.assertIsNone(re.search(r"\bDROP\s+TABLE\b", txt, re.IGNORECASE), str(src_path))

        with tempfile.TemporaryDirectory(prefix="si_mig_staged_") as tmp:
            db_path = Path(tmp) / "staged.db"

            # 1) Create DB pinned at V002 -> only V001..V002 applied; V003..V005 pending
            db_v2 = GameDatabase(db_path, target_schema_version=2)
            try:
                ver2 = db_v2.get_schema_version()
                self.assertEqual(ver2["version"], 2)
                self.assertEqual(ver2["version_tag"], "V002")
                self.assertEqual(ver2["user_version"], 2)
                self.assertEqual(ver2["meta_schema_version"], "2")
                self.assertFalse(ver2["up_to_date"])
                self.assertEqual(ver2["applied_count"], 2)
                self.assertEqual(ver2["pending_count"], LATEST_SCHEMA_VERSION - 2)

                # summary() works safely even when V003..V005 tables do not exist yet
                sum2 = db_v2.summary()
                self.assertEqual(sum2["schema"]["version"], 2)
                self.assertEqual(sum2["counts"]["manual_queue"], 0)

                # Dry-run preview does NOT mutate DB
                dry = db_v2.run_migrations(dry_run=True)
                self.assertTrue(dry["dry_run"])
                self.assertEqual([m["version"] for m in dry["would_apply"]], list(range(3, LATEST_SCHEMA_VERSION + 1)))
                self.assertEqual(db_v2.get_schema_version()["version"], 2)

                # Advance to V004 with online backup enabled
                mig4 = db_v2.run_migrations(target_version=4, backup=True)
                self.assertFalse(mig4["dry_run"])
                self.assertEqual(mig4["version"], 4)
                self.assertIsNotNone(mig4["backup_path"])
                self.assertTrue(Path(mig4["backup_path"]).exists())

                # Advance to latest V005
                mig5 = db_v2.run_migrations()
                self.assertEqual(mig5["version"], LATEST_SCHEMA_VERSION)
                self.assertTrue(mig5["up_to_date"])
                self.assertEqual(mig5["checksum_drift"], [])

                ver_chk = db_v2.verify_schema(full_integrity=True)
                self.assertTrue(ver_chk["ok"], ver_chk)
                self.assertEqual(ver_chk["missing_tables"], [])
                self.assertEqual(ver_chk["missing_columns"], [])
            finally:
                db_v2.close()

    def test_v002_database_upgrades_nondestructively_and_preserves_all_rows(self) -> None:
        from spaceinvasion.parsing.models import SpyReport

        dsn = get_shared_pg_harness().create_database(prefix="mig_v2", from_template=False)
        db = GameDatabase(dsn, target_schema_version=2)
        try:
            self.assertEqual(db.get_schema_version()["version"], 2)
            db._conn.execute(
                "INSERT INTO rank_snapshots(ts, nickname, server_points, server_rank, estimated_points, pts_per_hr, spice_value, planets) "
                "VALUES (1700000100.0, 'ventkx86', 4500, 120, 4500.0, 80.0, 90000.0, 3)"
            )
            db._conn.commit()
            db.record_spy_report(
                SpyReport(
                    target_coords="02:380:08",
                    owner="OldFarm",
                    resources=Resources(12000, 8000, 5000, 2000),
                    uninhabited_fields=325,
                ),
                ts=1700000205.0,
            )

            with tempfile.TemporaryDirectory(prefix="si_mig_bkp_") as bdir:
                mig = db.run_migrations(target_version=LATEST_SCHEMA_VERSION, backup=True, backup_dir=Path(bdir))
                self.assertEqual(mig["version"], LATEST_SCHEMA_VERSION)
                self.assertTrue(mig["up_to_date"])
                self.assertTrue(Path(mig["backup_path"]).exists())

            sv = db.get_schema_version()
            self.assertEqual(sv["version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(sv["meta_schema_version"], SCHEMA_VERSION)
            self.assertTrue(sv["up_to_date"])

            # Zero row loss across upgraded tables + new columns populated with safe defaults
            self.assertEqual(len(db.get_rank_samples()), 1)
            targets = db.get_spy_targets()
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["coords"], "02:380:08")
            self.assertEqual(targets[0]["resources"]["pig_iron"], 12000)

            sreps = db.get_spy_reports(coords="02:380:08")
            self.assertEqual(len(sreps), 1)
            self.assertEqual(sreps[0]["uninhabited_fields"], 325)
            self.assertEqual(db.get_recycle_reports(), [])
        finally:
            db.close()

    def test_savepoint_rollback_on_failed_migration_and_checksum_drift_and_future_version_guard(self) -> None:
        from spaceinvasion.db.postgres import PostgresPoolBackend

        # 1) Savepoint rollback: V001 succeeds, V002 fails mid-script -> V002 partial table rolled back completely
        bad_runner = MigrationRunner(
            (
                Migration(
                    version=1,
                    name="v001_ok",
                    description="Base meta + sample table",
                    sql="CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL); CREATE TABLE sample_a(id INTEGER PRIMARY KEY);",
                ),
                Migration(
                    version=2,
                    name="v002_boom",
                    description="Partial table then invalid SQL",
                    sql="CREATE TABLE partial_should_rollback(id INTEGER PRIMARY KEY); CREATE TABLE INVALID SYNTAX HERE;",
                ),
            ),
            schema_family="custom_test",
            required_tables=("meta", "sample_a"),
        )
        sp_dsn = get_shared_pg_harness().create_database(prefix="mig_sp", from_template=False)
        pg_sp = PostgresPoolBackend(sp_dsn, min_size=1, max_size=2)
        try:
            with pg_sp.session_lock:
                conn = pg_sp.conn_proxy
                res1 = bad_runner.migrate(conn, is_memory=True, target_version=1)
                self.assertEqual(res1["version"], 1)
                with self.assertRaises((OperationalError, DatabaseError)):
                    bad_runner.migrate(conn, is_memory=True, target_version=2)
                tables = {
                    str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                self.assertIn("sample_a", tables)
                self.assertNotIn("partial_should_rollback", tables)
                self.assertEqual(bad_runner.inspect(conn)["version"], 1)
        finally:
            pg_sp.close()

        # 2) Checksum drift detection + future-version guard (never clobbers newer DB downwards)
        drift_dsn = get_shared_pg_harness().create_database(prefix="mig_drift", from_template=True)
        db = GameDatabase(drift_dsn)
        try:
            db._conn.execute("UPDATE schema_migrations SET checksum = 'tampered_sha256' WHERE version = 2")
            db._conn.commit()
        finally:
            db.close()

        db2 = GameDatabase(drift_dsn)
        try:
            chk = db2.verify_schema()
            self.assertFalse(chk["ok"])
            self.assertEqual(len(chk["checksum_drift"]), 1)
            self.assertEqual(chk["checksum_drift"][0]["version"], 2)
            self.assertEqual(chk["checksum_drift"][0]["reason"], "checksum_mismatch")
        finally:
            db2.close()

        # Future DB (e.g. V099 from a newer release) is never downgraded
        fut_dsn = get_shared_pg_harness().create_database(prefix="mig_fut", from_template=True)
        db_seed_fut = GameDatabase(fut_dsn)
        try:
            db_seed_fut._conn.execute("PRAGMA user_version = 99;")
            db_seed_fut._conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
            db_seed_fut._conn.commit()
        finally:
            db_seed_fut.close()
        db_fut = GameDatabase(fut_dsn)
        try:
            sv_fut = db_fut.get_schema_version()
            self.assertEqual(sv_fut["version"], 99)
            self.assertTrue(sv_fut["newer_than_code"])
        finally:
            db_fut.close()

    def test_pg_cli_status_migrate_verify_and_http_api_commands(self) -> None:
        cli_dsn = get_shared_pg_harness().create_database(prefix="mig_cli", from_template=False)
        seed_db = GameDatabase(cli_dsn, target_schema_version=2)
        seed_db.close()

        # CLI status --json detects pending migrations (V002)
        buf_st = io.StringIO()
        with contextlib.redirect_stdout(buf_st):
            rc = migrations_cli_main(["--db-url", cli_dsn, "--json", "status"])
        self.assertEqual(rc, 0)
        st_payload = json.loads(buf_st.getvalue())
        self.assertEqual(st_payload["version"], 2)
        self.assertFalse(st_payload["up_to_date"])

        with tempfile.TemporaryDirectory(prefix="si_mig_reg_") as tmp:
            root = Path(tmp)
            # CLI migrate --backup upgrades to latest!
            buf_mig = io.StringIO()
            with contextlib.redirect_stdout(buf_mig):
                rc_m = migrations_cli_main(["--root", str(root), "--db-url", cli_dsn, "--json", "migrate", "--backup"])
            self.assertEqual(rc_m, 0)
            mig_payload = json.loads(buf_mig.getvalue())
            self.assertTrue(mig_payload["up_to_date"])
            self.assertEqual(mig_payload["version"], LATEST_SCHEMA_VERSION)
            self.assertTrue(Path(mig_payload["backup_path"]).exists())

            # CLI verify --full-integrity
            buf_ver = io.StringIO()
            with contextlib.redirect_stdout(buf_ver):
                rc_v = migrations_cli_main(["--db-url", cli_dsn, "--json", "verify", "--full-integrity"])
            self.assertEqual(rc_v, 0)
            ver_payload = json.loads(buf_ver.getvalue())
            self.assertTrue(ver_payload["ok"])
            self.assertTrue(ver_payload["integrity_ok"])

            # HTTP API command: db_summary + direct DB snapshots verification
            cfg_path = root / "config.json"
            cfg_path.write_text('{"planner_mode": "priority"}', encoding="utf-8")
            p_home = PlanetState(
                planet_id=12476,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=20,
                fields_used=40,
                fields_max=250,
                resources=Resources(10000, 8000, 6000, 4000),
                storage=Resources(100000, 100000, 100000, 100000),
                energy=EnergyBalance(500, 300),
                buildings={"b1": 5, "b5": 6},
            )
            st = GameState(
                universe_id=31,
                user_id=103603,
                nickname="ventkx86",
                timestamp=time.time(),
                planets=(p_home,),
                active_planet_id=12476,
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=root / "no.env",
                config_path=cfg_path,
                db_path=cli_dsn,
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            assert bridge._db is not None
            bridge._db.record_full_state_snapshot(
                st,
                plan=[
                    PlannedAction(
                        priority="P2",
                        kind="UPGRADE_BUILDING",
                        planet_id=12476,
                        planet_coords="02:380:12",
                        target_id=1,
                        target_name="Pig iron mine",
                        target_level=9,
                        reason="ROI sid=secret1234567890",
                    )
                ],
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                r_sum = requests.post(
                    f"{base_url}/command",
                    json={"command": "db_summary"},
                    timeout=5,
                ).json()
                self.assertTrue(r_sum["ok"])
                self.assertEqual(r_sum["db"]["backend"], "postgres")

                snaps = bridge._db.get_state_snapshots(limit=20, include_payload=True)
                self.assertEqual(len(snaps), 1)
                self.assertNotIn("secret1234567890", json.dumps(snaps, allow_nan=False))
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_v009_universe_research_max_monotonic_ledger_and_append_only_history(self) -> None:
        from spaceinvasion.parsing.models import SpyReport

        self.assertEqual(LATEST_SCHEMA_VERSION, 14)
        with tempfile.TemporaryDirectory(prefix="si_mig_v9_") as tmp:
            db_path = Path(tmp) / "v9.db"
            db_v8 = GameDatabase(db_path, target_schema_version=8)
            try:
                self.assertEqual(db_v8.get_schema_version()["version"], 8)
                mig9 = db_v8.run_migrations(target_version=9, backup=True)
                self.assertEqual(mig9["version"], 9)
                mig_head = db_v8.run_migrations(target_version=LATEST_SCHEMA_VERSION, backup=False)
                self.assertTrue(mig_head["up_to_date"])
                chk = db_v8.verify_schema(full_integrity=True)
                self.assertTrue(chk["ok"], chk)

                seeded = db_v8.get_universe_research_max(own_researches={"r1": 6, "r2": 8}, own_nick="ventkx86")
                by_k = {r["key"]: r for r in seeded}
                self.assertEqual(by_k["r1"]["own_level"], 6)
                self.assertEqual(by_k["r1"]["universe_max_level"], 6)
                self.assertEqual(by_k["r1"]["holder"], "ventkx86")

                # Spy report with higher research raises spied_max_level and universe_max_level
                db_v8.record_spy_report(
                    SpyReport(
                        target_coords="02:381:05",
                        owner="TechLeader",
                        resources=Resources(1000, 1000, 1000, 1000),
                        researches={"r1": 14, "r2": 5},
                    ),
                    ts=1700001000.0,
                )
                refreshed = db_v8.refresh_universe_research_max(
                    own_researches={"r1": 6, "r2": 8},
                    own_nick="ventkx86",
                    server_records={"r3": {"level": 19, "holder": "TopPilot", "coords": "01:010:02"}},
                    source="manual_sync",
                    ts=1700001010.0,
                )
                by_k2 = {r["key"]: r for r in refreshed}
                self.assertEqual(by_k2["r1"]["spied_max_level"], 14)
                self.assertEqual(by_k2["r1"]["universe_max_level"], 14)
                self.assertEqual(by_k2["r1"]["gap_to_max"], 8)
                self.assertEqual(by_k2["r1"]["holder"], "TechLeader")
                # r2 own_level (8) > spied (5) -> universe_max_level stays 8
                self.assertEqual(by_k2["r2"]["universe_max_level"], 8)
                self.assertEqual(by_k2["r3"]["server_record_level"], 19)
                self.assertEqual(by_k2["r3"]["universe_max_level"], 19)
                self.assertEqual(by_k2["r3"]["holder"], "TopPilot")

                # Subsequent lower server record never decreases persisted universe_max_level
                refreshed_low = db_v8.refresh_universe_research_max(
                    own_researches={"r1": 6, "r2": 8},
                    own_nick="ventkx86",
                    server_records={"r3": {"level": 10, "holder": "LowerPilot"}},
                    source="manual_sync",
                    ts=1700001020.0,
                )
                by_k3 = {r["key"]: r for r in refreshed_low}
                self.assertEqual(by_k3["r3"]["universe_max_level"], 19)
                self.assertGreaterEqual(db_v8.summary()["counts"]["universe_research_max_history"], 2)
            finally:
                db_v8.close()

    def test_v011_colony_attempts_staged_guard_and_upgrade(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_mig_v11_") as tmp:
            db_path = Path(tmp) / "v11.db"
            db = GameDatabase(db_path, target_schema_version=10)
            try:
                self.assertEqual(db.get_schema_version()["version"], 10)
                # Guarded gracefully when pinned at V010 before V011 table exists
                self.assertIsNone(
                    db.record_colony_attempt(
                        target_coords="02:150:08",
                        event_type="dispatched",
                        origin_coords="02:150:05",
                    )
                )
                self.assertEqual(db.get_colony_attempts(), [])
                self.assertEqual(db.get_colony_attempts_summary()["total_events"], 0)

                mig11 = db.run_migrations(target_version=11, backup=True)
                self.assertEqual(mig11["version"], 11)
                self.assertFalse(mig11["up_to_date"])
                mig12 = db.run_migrations(target_version=LATEST_SCHEMA_VERSION, backup=False)
                self.assertEqual(mig12["version"], LATEST_SCHEMA_VERSION)
                self.assertTrue(mig12["up_to_date"])
                chk = db.verify_schema(full_integrity=True)
                self.assertTrue(chk["ok"], chk)

                ins = db.record_colony_attempt(
                    target_coords="2:150:8",
                    event_type="deleted_undersized",
                    origin_coords="2:150:5",
                    planet_id=9901,
                    fields_rolled=214,
                    min_fields_target=300,
                    expected_min_fields=180,
                    expected_max_fields=340,
                    temperature=35,
                    grade="S",
                    score=92,
                    status_message="Deleted undersized colony (214 < 300 fields)",
                    details={"action": "ABANDON_COLONY"},
                    ts=1_750_000_100.0,
                )
                self.assertIsNotNone(ins)
                assert ins is not None
                self.assertEqual(ins["target_coords"], "02:150:08")
                self.assertEqual(ins["fields_rolled"], 214)
                self.assertEqual(db.summary()["counts"]["colony_attempts"], 1)
            finally:
                db.close()

    def test_v012_hot_and_history_table_division_backfill_and_incremental_sync(self) -> None:
        from spaceinvasion.parsing.models import (
            GalaxySlot,
            SpyReport,
        )

        # Canonical hot vs history partition must be disjoint and cover all required tables
        hot_set = set(HOT_TABLES)
        hist_set = set(HISTORY_TABLES)
        req_set = set(GAME_REQUIRED_TABLES)
        self.assertEqual(len(hot_set), 19)
        self.assertEqual(len(hist_set), 21)
        self.assertEqual(len(req_set), 40)
        self.assertEqual(hot_set & hist_set, set())
        self.assertEqual(hot_set | hist_set, req_set)
        self.assertEqual(len(TABLE_TIER_BY_NAME), 40)
        for tbl in hot_set:
            self.assertEqual(TABLE_TIER_BY_NAME[tbl], "hot")
        for tbl in hist_set:
            self.assertEqual(TABLE_TIER_BY_NAME[tbl], "history")

        with tempfile.TemporaryDirectory(prefix="si_mig_v12_") as tmp:
            db_path = Path(tmp) / "v12.db"
            # Seed a V011 database with planets, galaxy scans, slots, spy report, raid, and discarded coord
            db = GameDatabase(db_path, target_schema_version=11)
            try:
                self.assertEqual(db.get_schema_version()["version"], 11)
                state = GameState(
                    timestamp=1_760_000_000.0,
                    universe_id=31,
                    user_id=103603,
                    nickname="tester",
                    active_planet_id=101,
                    planets=(
                        PlanetState(
                            planet_id=101,
                            name="Home",
                            coords="02:150:05",
                            planet_type="normal",
                            temperature=30,
                            resources=Resources(1000, 2000, 3000, 4000),
                            storage=Resources(50000, 50000, 50000, 50000),
                            energy=EnergyBalance(produced=200, used=150),
                            fields_used=40,
                            fields_max=320,
                        ),
                    ),
                )
                db.record_state_snapshot(state)
                db.record_full_state_snapshot(state, source="test_v11")
                db.record_galaxy_scan(
                    2,
                    150,
                    slots=[
                        GalaxySlot(
                            coords="02:150:05",
                            position=5,
                            name="Home",
                            owner="tester",
                            status_color="white",
                        ),
                        GalaxySlot(
                            coords="02:150:08",
                            position=8,
                            name="",
                            owner="",
                            status_color="free",
                            colonize_min_fields=240,
                            colonize_max_fields=340,
                            temperature=30,
                        ),
                        GalaxySlot(
                            coords="02:150:09",
                            position=9,
                            name="",
                            owner="",
                            status_color="free",
                            colonize_min_fields=220,
                            colonize_max_fields=330,
                            temperature=25,
                        ),
                        GalaxySlot(
                            coords="02:150:10",
                            position=10,
                            name="FarmA",
                            owner="IdleGuy",
                            status_color="yellow",
                            has_debris=True,
                        ),
                    ],
                    ts=1_760_000_010.0,
                )
                db.record_spy_report(
                    SpyReport(
                        target_coords="02:150:08",
                        owner="",
                        uninhabited_fields=335,
                    ),
                    ts=1_760_000_020.0,
                )
                db.record_raid("02:150:10", attacked_ts=1_760_000_030.0, outcome="win")
                db.discard_colony_candidate("02:150:09", ts=1_760_000_040.0)

                v11_intel = db.get_galaxy_sector_intel(now_ts=1_760_000_100.0)
                v11_cands = db.get_colony_candidates(
                    origin_coords="02:150:05",
                    min_fields_target=300,
                    exclude_coords={"02:150:05"},
                    limit=50,
                )

                # Upgrade V011 -> V012 with backup & backfill, then advance to LATEST_SCHEMA_VERSION (V013)
                mig12 = db.run_migrations(target_version=12, backup=True)
                self.assertEqual(mig12["version"], 12)
                self.assertFalse(mig12["up_to_date"])
                mig_head = db.run_migrations(target_version=LATEST_SCHEMA_VERSION, backup=False)
                self.assertEqual(mig_head["version"], LATEST_SCHEMA_VERSION)
                self.assertTrue(mig_head["up_to_date"])
                chk = db.verify_schema(full_integrity=True)
                self.assertTrue(chk["ok"], chk)
                self.assertEqual(chk["table_tiers"]["hot_count"], 19)
                self.assertEqual(chk["table_tiers"]["history_count"], 21)

                summ = db.summary()
                self.assertEqual(summ["counts"]["latest_state_hot"], 1)
                self.assertEqual(summ["counts"]["empire_planets_hot"], 1)
                self.assertEqual(summ["counts"]["galaxy_sector_hot"], 1)
                self.assertEqual(summ["counts"]["colony_candidates_hot"], 2)
                self.assertEqual(set(summ["table_tiers"]["hot"]), hot_set)
                self.assertEqual(set(summ["table_tiers"]["history"]), hist_set)

                # Hot latest state reader matches full state snapshot
                latest_hot = db.get_latest_state_hot(include_payload=True)
                self.assertIsNotNone(latest_hot)
                assert latest_hot is not None
                self.assertEqual(latest_hot["active_planet_id"], 101)
                self.assertEqual(latest_hot["source"], "test_v11")

                # Exact parity between V011 full-scan reads and V012 hot-table reads
                v12_intel = db.get_galaxy_sector_intel(now_ts=1_760_000_100.0)
                v12_cands = db.get_colony_candidates(
                    origin_coords="02:150:05",
                    min_fields_target=300,
                    exclude_coords={"02:150:05"},
                    limit=50,
                )
                self.assertEqual(v12_intel, v11_intel)
                self.assertEqual(v12_cands, v11_cands)
                self.assertEqual([c["coords"] for c in v12_cands], ["02:150:08"])

                # Incremental hot-table maintenance stays in sync on writes & restore
                db.restore_colony_candidate("02:150:09", ts=1_760_000_050.0)
                v12_after_restore = db.get_colony_candidates(
                    origin_coords="02:150:05",
                    min_fields_target=300,
                    exclude_coords={"02:150:05"},
                    limit=50,
                )
                self.assertEqual(
                    {c["coords"] for c in v12_after_restore},
                    {"02:150:08", "02:150:09"},
                )

                rebuilt = db.rebuild_hot_tables(ts=1_760_000_100.0)
                self.assertEqual(rebuilt["galaxy_sector_hot"], 1)
                self.assertEqual(rebuilt["colony_candidates_hot"], 2)
                self.assertEqual(
                    db.get_colony_candidates(
                        origin_coords="02:150:05",
                        min_fields_target=300,
                        exclude_coords={"02:150:05"},
                        limit=50,
                    ),
                    v12_after_restore,
                )
            finally:
                db.close()

    def test_v013_staged_upgrade_from_v012_checksum_tiers_and_cli_verify(self) -> None:
        v13_hot = {"config_optimizer_active", "ai_planner_active", "field_conflict_prompts"}
        v13_hist = {"config_optimizer_runs", "ai_planner_runs", "field_conflict_events"}
        self.assertTrue(v13_hot.issubset(set(HOT_TABLES)))
        self.assertTrue(v13_hist.issubset(set(HISTORY_TABLES)))
        self.assertTrue((v13_hot | v13_hist).issubset(set(GAME_REQUIRED_TABLES)))

        dsn_v13 = get_shared_pg_harness().create_database(prefix="mig_v13", from_template=False)
        # Seed a DB pinned at V012
        db_v12 = GameDatabase(dsn_v13, target_schema_version=12)
        try:
            sv12 = db_v12.get_migration_status(include_events=False)
            self.assertEqual(sv12["version"], 12)
            self.assertEqual(sv12["version_tag"], "V012")
            self.assertFalse(sv12["up_to_date"])
            self.assertEqual(sv12["pending_count"], LATEST_SCHEMA_VERSION - 12)
            self.assertEqual(sv12["pending_migrations"][0]["version"], 13)

            # All V013 GameDatabase methods degrade safely when DB is pinned at V012
            self.assertEqual(db_v12.record_config_optimizer_run({"summary": {}}), 0)
            self.assertIsNone(db_v12.get_active_config_optimizer())
            self.assertEqual(db_v12.list_config_optimizer_runs(), [])
            self.assertEqual(db_v12.get_reserved_building_floors(), {})
            self.assertEqual(db_v12.update_reserved_building_floor("101", "b6", 4), {})
            self.assertEqual(db_v12.record_ai_planner_run({"summary": {}}), 0)
            self.assertIsNone(db_v12.get_active_ai_planner())
            self.assertEqual(db_v12.list_ai_planner_runs(), [])
            self.assertEqual(db_v12.list_field_conflict_prompts(), [])
            self.assertIsNone(db_v12.resolve_field_conflict_prompt("101:b1:15", "demolish"))
        finally:
            db_v12.close()

        with tempfile.TemporaryDirectory(prefix="si_mig_v13_") as tmp:
            # Upgrade V012 -> LATEST via CLI entrypoint with online backup + full integrity verify
            buf_mig = io.StringIO()
            with contextlib.redirect_stdout(buf_mig):
                rc_m = migrations_cli_main(["--root", tmp, "--db-url", dsn_v13, "--json", "migrate", "--backup"])
            self.assertEqual(rc_m, 0)
            mig_res = json.loads(buf_mig.getvalue())
            self.assertEqual(mig_res["from_version"], 12)
            self.assertEqual(mig_res["version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(mig_res["version_tag"], f"V{LATEST_SCHEMA_VERSION:03d}")
            self.assertTrue(mig_res["up_to_date"])
            self.assertIsNotNone(mig_res["backup_path"])
            self.assertTrue(Path(mig_res["backup_path"]).exists())

            buf_ver = io.StringIO()
            with contextlib.redirect_stdout(buf_ver):
                rc_v = migrations_cli_main(["--db-url", dsn_v13, "--json", "verify", "--full-integrity"])
            self.assertEqual(rc_v, 0)
            ver_res = json.loads(buf_ver.getvalue())
            self.assertTrue(ver_res["ok"], ver_res)
            self.assertTrue(ver_res["integrity_ok"])
            self.assertEqual(ver_res["missing_tables"], [])
            self.assertEqual(ver_res["checksum_drift"], [])
            self.assertEqual(ver_res["table_tiers"]["hot_count"], 19)
            self.assertEqual(ver_res["table_tiers"]["history_count"], 21)

            # Verify V013 checksum & table usability on reopened GameDatabase
            db_v13 = GameDatabase(dsn_v13)
            try:
                v13_spec = next(m for m in GAME_DB_MIGRATIONS if m.version == 13)
                applied_v13 = next(
                    m
                    for m in db_v13.get_migration_status(include_events=False)["applied_migrations"]
                    if m["version"] == 13
                )
                self.assertEqual(applied_v13["checksum"], v13_spec.checksum)
                summ = db_v13.summary()
                for tbl in v13_hot | v13_hist:
                    self.assertIn(tbl, summ["counts"])
                    self.assertEqual(summ["counts"][tbl], 0)
            finally:
                db_v13.close()

    def test_v014_hourly_production_and_loot_recycle_hourly_upgrade_and_backfill(self) -> None:
        from spaceinvasion.parsing.models import InboxMessage

        self.assertIn("loot_recycle_hourly", set(HOT_TABLES))
        self.assertIn("hourly_production_history", set(HISTORY_TABLES))
        self.assertEqual(TABLE_TIER_BY_NAME["loot_recycle_hourly"], "hot")
        self.assertEqual(TABLE_TIER_BY_NAME["hourly_production_history"], "history")

        base_hr = 1_760_000_000 // 3600 * 3600
        dsn_v14 = get_shared_pg_harness().create_database(prefix="mig_v14", from_template=False)
        with tempfile.TemporaryDirectory(prefix="si_mig_v14_") as tmp:
            db_v13 = GameDatabase(dsn_v14, target_schema_version=13)
            try:
                sv13 = db_v13.get_migration_status(include_events=False)
                self.assertEqual(sv13["version"], 13)
                self.assertEqual(sv13["pending_count"], 1)
                self.assertEqual(sv13["pending_migrations"][0]["version"], 14)

                # V014 methods degrade safely when pinned at V013
                self.assertIsNone(db_v13.record_hourly_production(pig_iron_per_hour=100.0, ts=float(base_hr + 60)))
                self.assertEqual(db_v13.get_hourly_production_history(), [])
                v13_lr = db_v13.get_loot_recycle_hourly(hours=6, now_ts=float(base_hr + 120))
                self.assertEqual(len(v13_lr["history"]), 6)
                self.assertEqual(v13_lr["summary"]["grand_total"], 0.0)

                # Seed pre-existing battle & recycle reports (plus a non-recycle station notice)
                db_v13.record_game_messages(
                    [
                        InboxMessage(
                            msg_uid="id:v14_atk_1",
                            server_msg_id="v14_atk_1",
                            folder_type="attack",
                            category="battle_report",
                            msg_date_text="2026-11-18 10:15:00",
                            sender="System",
                            subject="Battle report [02:380:11]",
                            coords="02:380:11",
                            body_text="You won the battle and stole 4000 Pig iron, 3000 Metal, 2000 Kryptonite, 1000 Spice.",
                            parsed_data={
                                "outcome": "win",
                                "loot": {"pig_iron": 4000, "metal": 3000, "kryptonite": 2000, "spice": 1000},
                            },
                        ),
                        InboxMessage(
                            msg_uid="id:v14_rec_1",
                            server_msg_id="v14_rec_1",
                            folder_type="inbox",
                            category="recycle_report",
                            msg_date_text="2026-11-18 10:25:00",
                            sender="System",
                            subject="Space rubble [02:380:11] recycling report",
                            coords="02:380:11",
                            body_text="Your fleet (10 Recycler) processed 1500 Pig iron, 500 Metal pieces of rubble there.",
                            parsed_data={
                                "recycler_count": 10,
                                "harvested": {"pig_iron": 1500, "metal": 500, "kryptonite": 0, "spice": 0},
                            },
                        ),
                        InboxMessage(
                            msg_uid="id:v14_station_ignore",
                            server_msg_id="v14_station_ignore",
                            folder_type="inbox",
                            category="recycle_report",
                            msg_date_text="2026-11-18 10:30:00",
                            sender="System",
                            subject="Colony [02:380:06] station",
                            coords="02:380:06",
                            body_text="One of your fleets (6 Recycler) was re-stationed from Colony [02:379:06] to Colony [02:380:06]. It delivers 99999 Pig iron.",
                            parsed_data={
                                "recycler_count": 6,
                                "harvested": {"pig_iron": 99999, "metal": 0, "kryptonite": 0, "spice": 0},
                            },
                        ),
                    ],
                    ts=float(base_hr + 900),
                )
            finally:
                db_v13.close()

            buf_mig = io.StringIO()
            with contextlib.redirect_stdout(buf_mig):
                rc_m = migrations_cli_main(["--root", tmp, "--db-url", dsn_v14, "--json", "migrate", "--backup"])
            self.assertEqual(rc_m, 0)
            mig_res = json.loads(buf_mig.getvalue())
            self.assertEqual(mig_res["from_version"], 13)
            self.assertEqual(mig_res["version"], 14)
            self.assertEqual(mig_res["version_tag"], "V014")
            self.assertTrue(mig_res["up_to_date"])
            self.assertTrue(Path(mig_res["backup_path"]).exists())

            buf_ver = io.StringIO()
            with contextlib.redirect_stdout(buf_ver):
                rc_v = migrations_cli_main(["--db-url", dsn_v14, "--json", "verify", "--full-integrity"])
            self.assertEqual(rc_v, 0)
            ver_res = json.loads(buf_ver.getvalue())
            self.assertTrue(ver_res["ok"], ver_res)
            self.assertEqual(ver_res["table_tiers"]["hot_count"], 19)
            self.assertEqual(ver_res["table_tiers"]["history_count"], 21)

            db_v14 = GameDatabase(dsn_v14)
            try:
                v14_spec = next(m for m in GAME_DB_MIGRATIONS if m.version == 14)
                applied_v14 = next(
                    m
                    for m in db_v14.get_migration_status(include_events=False)["applied_migrations"]
                    if m["version"] == 14
                )
                self.assertEqual(applied_v14["checksum"], v14_spec.checksum)

                # Backfill materialized battle + genuine recycle (excluding station notice!)
                lr = db_v14.get_loot_recycle_hourly(hours=24, now_ts=float(base_hr + 1800))
                self.assertEqual(len(lr["history"]), 24)
                self.assertEqual(lr["summary"]["total_stolen"], 10000.0)
                self.assertEqual(lr["summary"]["total_recycled"], 2000.0)
                self.assertEqual(lr["summary"]["grand_total"], 12000.0)
                self.assertEqual(lr["summary"]["attack_count"], 1)
                self.assertEqual(lr["summary"]["recycle_count"], 1)

                # Hourly production 1 row per minute (INSERT OR IGNORE)
                p1 = db_v14.record_hourly_production(
                    pig_iron_per_hour=5000.0,
                    metal_per_hour=3000.0,
                    kryptonite_per_hour=2000.0,
                    spice_per_hour=1000.0,
                    total_per_hour=11000.0,
                    speq_per_hour=3200.0,
                    planet_count=3,
                    active_builders=2,
                    ts=float(base_hr + 125),
                )
                p1_dup = db_v14.record_hourly_production(
                    pig_iron_per_hour=9999.0,
                    ts=float(base_hr + 155),
                )
                p2 = db_v14.record_hourly_production(
                    pig_iron_per_hour=5500.0,
                    metal_per_hour=3100.0,
                    kryptonite_per_hour=2100.0,
                    spice_per_hour=1100.0,
                    total_per_hour=11800.0,
                    speq_per_hour=3450.0,
                    planet_count=3,
                    active_builders=3,
                    ts=float(base_hr + 185),
                )
                self.assertTrue(p1["inserted"])
                self.assertFalse(p1_dup["inserted"])
                self.assertTrue(p2["inserted"])
                hist = db_v14.get_hourly_production_history(limit=10)
                self.assertEqual(len(hist), 2)
                self.assertEqual(hist[0]["pig_iron_per_hour"], 5000.0)
                self.assertEqual(hist[1]["active_builders"], 3)
                self.assertLess(hist[0]["minute_bucket"], hist[1]["minute_bucket"])
            finally:
                db_v14.close()


if __name__ == "__main__":
    unittest.main()
