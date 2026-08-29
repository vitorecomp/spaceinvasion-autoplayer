"""Tests for database schema versioning, forward migrations, backups, checksums, CLI, and API."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

from spaceinvasion.api_server import GameApiBridge, create_api_server
from spaceinvasion.migrations import (
    ATTACK_HISTORY_SCHEMA_VERSION,
    DEFAULT_GAME_MIGRATION_RUNNER,
    GAME_DB_MIGRATIONS,
    LATEST_SCHEMA_VERSION,
    SCHEMA_VERSION,
    DatabaseMigrationRegistry,
    Migration,
    MigrationRunner,
    main as migrations_cli_main,
    migrate_attack_history_json,
)
from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.storage import GameDatabase


class TestDatabaseMigrationsAndVersioning(unittest.TestCase):
    def test_staged_forward_migrations_dry_run_user_version_and_append_only_invariants(self) -> None:
        for src_path in (Path("spaceinvasion/db/migrations.py"), Path("spaceinvasion/db/storage.py")):
            txt = src_path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bDELETE\s+FROM\b", txt, re.IGNORECASE), str(src_path))
            self.assertIsNone(re.search(r"\bDROP\s+TABLE\b", txt, re.IGNORECASE), str(src_path))

        with tempfile.TemporaryDirectory(prefix="si_mig_staged_") as tmp:
            db_path = Path(tmp) / "staged.db"

            # 1) Create DB pinned at V002 -> only V001..V002 applied; V003..V005 pending
            db_v2 = GameDatabase(db_path, attack_history_path="", target_schema_version=2)
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

    def test_legacy_v1_database_upgrades_nondestructively_and_preserves_all_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_mig_legacy_") as tmp:
            db_path = Path(tmp) / "legacy_v1.db"
            with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
                # Minimal pre-V002 schema with older narrow spy_targets, spy_reports, and recycle_reports
                conn.executescript(
                    """
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL);
                    INSERT INTO meta VALUES ('schema_version', '1', 1700000000.0);

                    CREATE TABLE rank_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        universe_id INTEGER NOT NULL DEFAULT 0,
                        nickname TEXT NOT NULL DEFAULT '',
                        server_points INTEGER,
                        server_rank INTEGER,
                        estimated_points REAL NOT NULL,
                        pts_per_hr REAL NOT NULL,
                        spice_value REAL NOT NULL,
                        planets INTEGER NOT NULL
                    );
                    INSERT INTO rank_snapshots(ts, nickname, server_points, server_rank, estimated_points, pts_per_hr, spice_value, planets)
                    VALUES (1700000100.0, 'ventkx86', 4500, 120, 4500.0, 80.0, 90000.0, 3);

                    CREATE TABLE spy_targets (
                        coords TEXT PRIMARY KEY,
                        owner TEXT NOT NULL DEFAULT '',
                        is_inactive INTEGER NOT NULL DEFAULT 0,
                        is_spacia INTEGER NOT NULL DEFAULT 0,
                        points INTEGER,
                        pig_iron INTEGER NOT NULL DEFAULT 0,
                        metal INTEGER NOT NULL DEFAULT 0,
                        kryptonite INTEGER NOT NULL DEFAULT 0,
                        spice INTEGER NOT NULL DEFAULT 0,
                        fleet_json TEXT NOT NULL DEFAULT '{}',
                        defense_json TEXT NOT NULL DEFAULT '{}',
                        dug_in INTEGER NOT NULL DEFAULT 0,
                        last_scanned_ts REAL NOT NULL
                    );
                    INSERT INTO spy_targets(coords, owner, is_inactive, pig_iron, metal, kryptonite, spice, last_scanned_ts)
                    VALUES ('02:380:08', 'OldFarm', 1, 12000, 8000, 5000, 2000, 1700000200.0);

                    CREATE TABLE spy_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        target_coords TEXT NOT NULL,
                        owner TEXT NOT NULL DEFAULT '',
                        is_spacia INTEGER NOT NULL DEFAULT 0,
                        pig_iron INTEGER NOT NULL DEFAULT 0,
                        metal INTEGER NOT NULL DEFAULT 0,
                        kryptonite INTEGER NOT NULL DEFAULT 0,
                        spice INTEGER NOT NULL DEFAULT 0,
                        loot_spice_eq REAL NOT NULL DEFAULT 0.0,
                        fleet_json TEXT NOT NULL DEFAULT '{}',
                        defense_json TEXT NOT NULL DEFAULT '{}',
                        dug_in INTEGER,
                        uninhabited_fields INTEGER
                    );
                    INSERT INTO spy_reports(ts, target_coords, owner, pig_iron, uninhabited_fields)
                    VALUES (1700000205.0, '02:380:08', 'OldFarm', 12000, 325);

                    CREATE TABLE recycle_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        msg_uid TEXT NOT NULL UNIQUE,
                        server_msg_id TEXT NOT NULL DEFAULT '',
                        msg_date_text TEXT NOT NULL DEFAULT '',
                        coords TEXT NOT NULL DEFAULT '',
                        recycler_count INTEGER NOT NULL DEFAULT 0,
                        cargo_capacity INTEGER NOT NULL DEFAULT 0,
                        debris_avail_pig INTEGER NOT NULL DEFAULT 0,
                        debris_avail_metal INTEGER NOT NULL DEFAULT 0,
                        harvested_pig_iron INTEGER NOT NULL DEFAULT 0,
                        harvested_metal INTEGER NOT NULL DEFAULT 0,
                        harvested_spice_eq REAL NOT NULL DEFAULT 0.0,
                        raw_text TEXT NOT NULL DEFAULT ''
                    );
                    INSERT INTO recycle_reports(ts, msg_uid, coords, harvested_pig_iron, harvested_metal, harvested_spice_eq)
                    VALUES (1700000210.0, 'rec:legacy:1', '02:380:08', 6000, 2500, 2000.0);
                    """
                )
                conn.commit()

            db = GameDatabase(db_path, attack_history_path="", backup_on_upgrade=True)
            try:
                sv = db.get_schema_version()
                self.assertEqual(sv["version"], LATEST_SCHEMA_VERSION)
                self.assertEqual(sv["meta_schema_version"], SCHEMA_VERSION)
                self.assertEqual(sv["user_version"], LATEST_SCHEMA_VERSION)
                self.assertTrue(sv["up_to_date"])

                # Pre-upgrade backup was created in backups/ and contains the original v1 state
                backups = list((Path(tmp) / "backups").glob("*.db"))
                self.assertEqual(len(backups), 1)
                with contextlib.closing(sqlite3.connect(str(backups[0]))) as bconn:
                    b_ver = bconn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                    self.assertEqual(b_ver, "1")

                # Zero row loss across upgraded tables + new columns populated with safe defaults
                self.assertEqual(len(db.get_rank_samples()), 1)
                targets = db.get_spy_targets()
                self.assertEqual(len(targets), 1)
                self.assertEqual(targets[0]["coords"], "02:380:08")
                self.assertEqual(targets[0]["resources"]["pig_iron"], 12000)

                sreps = db.get_spy_reports(coords="02:380:08")
                self.assertEqual(len(sreps), 1)
                self.assertEqual(sreps[0]["uninhabited_fields"], 325)

                rreps = db.get_recycle_reports()
                self.assertEqual(len(rreps), 1)
                self.assertEqual(rreps[0]["harvested"]["pig_iron"], 6000)
                self.assertEqual(rreps[0]["harvested"]["kryptonite"], 0)
            finally:
                db.close()

    def test_savepoint_rollback_on_failed_migration_and_checksum_drift_and_future_version_guard(self) -> None:
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
        with contextlib.closing(sqlite3.connect(":memory:")) as conn:
            res1 = bad_runner.migrate(conn, is_memory=True, target_version=1)
            self.assertEqual(res1["version"], 1)
            with self.assertRaises(sqlite3.OperationalError):
                bad_runner.migrate(conn, is_memory=True, target_version=2)
            tables = {
                str(r[0])
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            self.assertIn("sample_a", tables)
            self.assertNotIn("partial_should_rollback", tables)
            self.assertEqual(bad_runner.inspect(conn)["version"], 1)

        # 2) Checksum drift detection + future-version guard (never clobbers newer DB downwards)
        with tempfile.TemporaryDirectory(prefix="si_mig_drift_") as tmp:
            db_path = Path(tmp) / "drift.db"
            db = GameDatabase(db_path, attack_history_path="")
            db.close()

            with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
                conn.execute("UPDATE schema_migrations SET checksum = 'tampered_sha256' WHERE version = 2")
                conn.commit()

            db2 = GameDatabase(db_path, attack_history_path="")
            try:
                chk = db2.verify_schema()
                self.assertFalse(chk["ok"])
                self.assertEqual(len(chk["checksum_drift"]), 1)
                self.assertEqual(chk["checksum_drift"][0]["version"], 2)
                self.assertEqual(chk["checksum_drift"][0]["reason"], "checksum_mismatch")
            finally:
                db2.close()

            # Future DB (e.g. V099 from a newer release) is never downgraded
            future_path = Path(tmp) / "future.db"
            shutil.copy2(db_path, future_path)
            with contextlib.closing(sqlite3.connect(str(future_path))) as conn:
                conn.execute("PRAGMA user_version = 99;")
                conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
                conn.commit()
            db_fut = GameDatabase(future_path, attack_history_path="")
            try:
                sv_fut = db_fut.get_schema_version()
                self.assertEqual(sv_fut["version"], 99)
                self.assertTrue(sv_fut["newer_than_code"])
            finally:
                db_fut.close()

    def test_multi_database_registry_cli_json_sidecar_and_http_api_commands(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_mig_reg_") as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            (root / "data").mkdir()

            # Legacy v1 attack_history.json with 2 raids + 1 bash block to absorb & retire
            atk_json = root / "data" / "attack_history.json"
            atk_json.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "history": [
                            {
                                "target_coords": "02:380:04",
                                "attacked_ts": 1700000100.0,
                                "is_exempt": True,
                                "outcome": "Victory",
                                "loot": {"pig_iron": 6000, "metal": 2500, "kryptonite": 3000, "spice": 1000},
                            },
                            {
                                "target_coords": "02:380:05",
                                "attacked_ts": 1700000200.0,
                                "is_exempt": False,
                                "outcome": "Defeat",
                                "loot": {},
                            },
                        ],
                        "bash_blocks": [
                            {
                                "target_coords": "02:380:05",
                                "blocked_ts": 1700000205.0,
                                "blocked_until_ts": 1700086605.0,
                                "cooldown_seconds": 86400.0,
                                "is_bash": True,
                                "reason": "3/24h bash limit",
                            }
                        ],
                    },
                    allow_nan=False,
                ),
                encoding="utf-8",
            )

            # Legacy rank_history.json + snapshot.json in state/ to absorb & retire
            rk_json = root / "state" / "rank_history.json"
            rk_json.write_text(
                json.dumps(
                    [
                        {
                            "ts": 1699999500.0,
                            "server_points": 3200,
                            "server_rank": 240,
                            "estimated_points": 3180.0,
                            "pts_per_hr": 55.0,
                            "spice_value": 64000.0,
                            "planets": 2,
                        }
                    ],
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            snap_json = root / "state" / "snapshot.json"
            cfg_yaml_legacy = root / "config.yaml"
            cfg_yaml_legacy.write_text(
                "planner_mode: ga_nn\nallow_raids: true\nmin_colony_fields: 320\n",
                encoding="utf-8",
            )
            snap_json.write_text(
                json.dumps(
                    {
                        "state": {
                            "universe_id": 31,
                            "user_id": 103603,
                            "nickname": "ventkx86",
                            "timestamp": 1699999600.0,
                            "active_planet_id": 12476,
                            "researches": {"r1": 4},
                            "planets": [
                                {
                                    "planet_id": 12476,
                                    "name": "Colony",
                                    "coords": "02:380:12",
                                    "planet_type": "normal",
                                    "temperature": 22,
                                    "fields_used": 35,
                                    "fields_max": 250,
                                    "resources": {"pig_iron": 9000, "metal": 6000, "kryptonite": 4000, "spice": 2000},
                                    "energy": {"produced": 400, "used": 250},
                                    "buildings": {"b1": 8, "b5": 9},
                                    "ships": {"s10": 5},
                                    "defense": {},
                                    "economy_rates": {"resource1": 100},
                                }
                            ],
                        },
                        "plan": [
                            {
                                "priority": "P2",
                                "kind": "UPGRADE_BUILDING",
                                "planet_id": 12476,
                                "planet_coords": "02:380:12",
                                "target_name": "Pig iron mine",
                                "target_level": 9,
                                "reason": "ROI sid=secret1234567890",
                            }
                        ],
                    },
                    allow_nan=False,
                ),
                encoding="utf-8",
            )

            # Secondary SQLite DB in state/archive.db pinned at V002
            sec_db = GameDatabase(root / "state" / "archive.db", attack_history_path="", target_schema_version=2)
            sec_db.close()

            # CLI status --json detects missing primary, pending archive.db (V002/V007), and unretired JSON files
            buf_st = io.StringIO()
            with contextlib.redirect_stdout(buf_st):
                rc = migrations_cli_main(["--root", str(root), "--json", "status"])
            self.assertEqual(rc, 0)
            st_payload = json.loads(buf_st.getvalue())
            self.assertFalse(st_payload["ok"])
            self.assertEqual(st_payload["databases"]["archive"]["version"], 2)
            self.assertFalse(st_payload["json_stores"]["attack_history_retired"]["retired_to_sqlite"])
            self.assertFalse(st_payload["json_stores"]["rank_history_retired"]["retired_to_sqlite"])
            self.assertFalse(st_payload["json_stores"]["snapshot_retired"]["retired_to_sqlite"])
            self.assertFalse(st_payload["json_stores"]["config_yaml_retired"]["retired_to_sqlite"])

            # CLI migrate --all --create --backup upgrades primary + archive.db to V008, absorbs & removes all legacy files!
            buf_mig = io.StringIO()
            with contextlib.redirect_stdout(buf_mig):
                rc_m = migrations_cli_main(["--root", str(root), "--json", "migrate", "--all", "--create", "--backup"])
            self.assertEqual(rc_m, 0)
            mig_payload = json.loads(buf_mig.getvalue())
            self.assertTrue(mig_payload["ok"])
            self.assertEqual(mig_payload["databases"]["primary"]["version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(mig_payload["databases"]["archive"]["version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(mig_payload["legacy_attack_json"]["imported_raids"], 2)
            self.assertEqual(mig_payload["legacy_attack_json"]["imported_bash_blocks"], 1)
            self.assertTrue(mig_payload["legacy_attack_json"]["removed_json"])
            self.assertEqual(mig_payload["legacy_rank_json"]["imported_samples"], 1)
            self.assertTrue(mig_payload["legacy_rank_json"]["removed_json"])
            self.assertEqual(mig_payload["legacy_snapshot_json"]["imported_snapshots"], 1)
            self.assertEqual(mig_payload["legacy_snapshot_json"]["imported_planets"], 1)
            self.assertTrue(mig_payload["legacy_snapshot_json"]["removed_json"])
            self.assertTrue(mig_payload["legacy_config_yaml"]["imported_config"])
            self.assertTrue(mig_payload["legacy_config_yaml"]["removed_yaml"])
            self.assertFalse(atk_json.exists())
            self.assertFalse(rk_json.exists())
            self.assertFalse(snap_json.exists())
            self.assertFalse(cfg_yaml_legacy.exists())

            from spaceinvasion.core.config import load_strategy_config

            db_cfg = load_strategy_config(root / "state" / "spaceinvasion.db")
            self.assertEqual(db_cfg.planner_mode, "ga_nn")
            self.assertEqual(db_cfg.min_colony_fields, 320)

            from spaceinvasion.optimizer import load_attack_history_stats
            from spaceinvasion.ranking import load_rank_history

            db_stats = load_attack_history_stats(root / "state" / "spaceinvasion.db")
            self.assertEqual(db_stats["overall"]["attempts"], 2)
            self.assertEqual(db_stats["overall"]["wins"], 1)
            self.assertEqual(len(db_stats["bash_blocks"]), 1)
            rk_loaded = load_rank_history(root / "state" / "spaceinvasion.db")
            self.assertEqual(len(rk_loaded), 1)
            self.assertEqual(rk_loaded[0]["server_points"], 3200)

            # CLI verify --full-integrity & backup
            buf_ver = io.StringIO()
            with contextlib.redirect_stdout(buf_ver):
                rc_v = migrations_cli_main(["--root", str(root), "--json", "verify", "--full-integrity"])
            self.assertEqual(rc_v, 0)
            self.assertTrue(json.loads(buf_ver.getvalue())["ok"])

            # HTTP API commands: db_migrations, db_migrate, db_backup, db_verify
            cfg_path = root / "config.yaml"
            cfg_path.write_text("planner_mode: priority\n", encoding="utf-8")
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
                db_path=root / "state" / "spaceinvasion.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                r_migs = requests.post(
                    f"{base_url}/command",
                    json={"command": "db_migrations", "include_all_stores": True},
                    timeout=5,
                ).json()
                self.assertTrue(r_migs["ok"])
                self.assertEqual(r_migs["migrations"]["version"], LATEST_SCHEMA_VERSION)
                self.assertIn("archive", r_migs["stores"]["databases"])

                r_dry = requests.post(
                    f"{base_url}/command",
                    json={"command": "db_migrate", "dry_run": True},
                    timeout=5,
                ).json()
                self.assertTrue(r_dry["ok"])
                self.assertTrue(r_dry["migration"]["dry_run"])

                r_bkp = requests.post(
                    f"{base_url}/command",
                    json={"command": "db_backup", "label": "api_test"},
                    timeout=5,
                ).json()
                self.assertTrue(r_bkp["ok"])
                self.assertTrue(Path(r_bkp["backup"]["backup_path"]).exists())

                r_ver = requests.post(
                    f"{base_url}/command",
                    json={"command": "db_verify", "full_integrity": True},
                    timeout=5,
                ).json()
                self.assertTrue(r_ver["ok"])
                self.assertTrue(r_ver["verification"]["sqlite_integrity_ok"])

                r_snaps = requests.post(
                    f"{base_url}/command",
                    json={"command": "state_snapshots", "include_payload": True},
                    timeout=5,
                ).json()
                self.assertTrue(r_snaps["ok"])
                self.assertEqual(r_snaps["count"], 1)
                self.assertNotIn("secret1234567890", json.dumps(r_snaps, allow_nan=False))
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    def test_v009_universe_research_max_monotonic_ledger_and_append_only_history(self) -> None:
        from spaceinvasion.combat.fleet import SpyReport

        self.assertEqual(LATEST_SCHEMA_VERSION, 11)
        with tempfile.TemporaryDirectory(prefix="si_mig_v9_") as tmp:
            db_path = Path(tmp) / "v9.db"
            db_v8 = GameDatabase(db_path, attack_history_path="", target_schema_version=8)
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
            db = GameDatabase(db_path, attack_history_path="", target_schema_version=10)
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
                self.assertTrue(mig11["up_to_date"])
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


if __name__ == "__main__":
    unittest.main()
