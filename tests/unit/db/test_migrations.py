"""Tests for Alembic database migrations, schema tiers, CLI, HTTP summary, and domain ledgers."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

import psycopg
import requests

from backend.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    PlannedAction,
    Resources,
)
from backend.db.migrations import (
    BASELINE_REVISION,
    NO_BACKUP,
    get_current_revision,
    upgrade_database,
    verify_models_match_migrations,
)
from backend.db.migrations.cli import main as migrations_cli_main
from backend.db.queries.dashboard import (
    GAME_REQUIRED_TABLES,
    HISTORY_TABLES,
    HOT_TABLES,
    TABLE_TIER_BY_NAME,
)
from backend.db.store import DatabaseStore
from backend.parsing.models import GalaxySlot, InboxMessage, SpyReport
from backend.runtime.api.http_server import create_api_server
from backend.runtime.bridge import GameApiBridge
from tests.pg_harness import get_shared_pg_harness, open_test_db as GameDatabase


class TestDatabaseMigrationsAndVersioning(unittest.TestCase):
    def test_append_only_ddl_invariants_and_alembic_fresh_upgrade_and_drift_detection(self) -> None:
        for src_path in sorted(Path("backend/db").rglob("*.py")):
            txt = src_path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bDELETE\s+FROM\b", txt, re.IGNORECASE), str(src_path))
            self.assertIsNone(re.search(r"\bDROP\s+TABLE\b", txt, re.IGNORECASE), str(src_path))

        dsn = get_shared_pg_harness().create_database(prefix="mig_alembic", from_template=False)
        with DatabaseStore.from_dsn(dsn, pool_size=1, max_overflow=0) as store:
            self.assertIsNone(get_current_revision(store.db_engine))
            res = upgrade_database(store.db_engine, "head", backup_fn=NO_BACKUP)
            self.assertEqual(res.current_revision, "0004")
            self.assertEqual(get_current_revision(store.db_engine), "0004")
            self.assertEqual(verify_models_match_migrations(store.db_engine), [])

            # Injecting an unmodeled table must be caught by verify_models_match_migrations
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("CREATE TABLE unexpected_drift_probe (id BIGINT PRIMARY KEY)")
            diffs = verify_models_match_migrations(store.db_engine)
            self.assertTrue(any("unexpected_drift_probe" in str(d) for d in diffs), diffs)

    def test_pg_cli_status_migrate_verify_and_http_api_commands(self) -> None:
        cli_dsn = get_shared_pg_harness().create_database(prefix="mig_cli", from_template=False)

        # Fresh empty DB -> CLI status --json reports pending revisions 0001..0004
        expected_revs = [BASELINE_REVISION, "0002", "0003", "0004"]
        buf_st = io.StringIO()
        with contextlib.redirect_stdout(buf_st):
            rc = migrations_cli_main(["--db-url", cli_dsn, "--json", "status"])
        self.assertEqual(rc, 0)
        st_payload = json.loads(buf_st.getvalue())
        self.assertIsNone(st_payload["current_revision"])
        self.assertFalse(st_payload["up_to_date"])
        self.assertEqual(st_payload["pending_revisions"], expected_revs)

        # Dry-run does not mutate the database
        buf_dry = io.StringIO()
        with contextlib.redirect_stdout(buf_dry):
            rc_d = migrations_cli_main(["--db-url", cli_dsn, "--json", "migrate", "--dry-run"])
        self.assertEqual(rc_d, 0)
        dry_payload = json.loads(buf_dry.getvalue())
        self.assertTrue(dry_payload["dry_run"])
        self.assertEqual(dry_payload["pending_revisions"], expected_revs)

        with tempfile.TemporaryDirectory(prefix="si_mig_reg_") as tmp:
            root = Path(tmp)
            bkp_file = root / "pre_migrate.dump"

            def _fake_backup(_dsn: str, _label: str, _dir: Path | None) -> Path:
                bkp_file.write_bytes(b"PGDMP-TEST")
                return bkp_file

            # CLI migrate --backup upgrades cleanly to head "0004"
            buf_mig = io.StringIO()
            with contextlib.redirect_stdout(buf_mig):
                rc_m = migrations_cli_main(
                    ["--root", str(root), "--db-url", cli_dsn, "--json", "migrate", "--backup"],
                    backup_hook=_fake_backup,
                )
            self.assertEqual(rc_m, 0)
            mig_payload = json.loads(buf_mig.getvalue())
            self.assertTrue(mig_payload["ok"])
            self.assertEqual(mig_payload["current_revision"], "0004")
            self.assertTrue(bkp_file.exists())

            # CLI verify --full-integrity
            buf_ver = io.StringIO()
            with contextlib.redirect_stdout(buf_ver):
                rc_v = migrations_cli_main(["--db-url", cli_dsn, "--json", "verify", "--full-integrity"])
            self.assertEqual(rc_v, 0)
            ver_payload = json.loads(buf_ver.getvalue())
            self.assertTrue(ver_payload["ok"], ver_payload)
            self.assertTrue(ver_payload["up_to_date"])
            self.assertEqual(ver_payload["missing_tables"], [])
            self.assertEqual(ver_payload["schema_diffs"], [])
            self.assertEqual(len(ver_payload["table_tiers"]["hot_tables"]), 19)
            self.assertEqual(len(ver_payload["table_tiers"]["history_tables"]), 21)

            # HTTP API command: db_summary + direct DB snapshot redaction verification
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

    def test_universe_research_max_monotonic_ledger_and_append_only_history(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_mig_v9_") as tmp:
            db = GameDatabase(Path(tmp) / "v9.db")
            try:
                seeded = db.get_universe_research_max(own_researches={"r1": 6, "r2": 8}, own_nick="ventkx86")
                by_k = {r["key"]: r for r in seeded}
                self.assertEqual(by_k["r1"]["own_level"], 6)
                self.assertEqual(by_k["r1"]["universe_max_level"], 6)
                self.assertEqual(by_k["r1"]["holder"], "ventkx86")

                db.record_spy_report(
                    SpyReport(
                        target_coords="02:381:05",
                        owner="TechLeader",
                        resources=Resources(1000, 1000, 1000, 1000),
                        researches={"r1": 14, "r2": 5},
                    ),
                    ts=1700001000.0,
                )
                refreshed = db.refresh_universe_research_max(
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
                self.assertEqual(by_k2["r2"]["universe_max_level"], 8)
                self.assertEqual(by_k2["r3"]["server_record_level"], 19)
                self.assertEqual(by_k2["r3"]["universe_max_level"], 19)
                self.assertEqual(by_k2["r3"]["holder"], "TopPilot")

                refreshed_low = db.refresh_universe_research_max(
                    own_researches={"r1": 6, "r2": 8},
                    own_nick="ventkx86",
                    server_records={"r3": {"level": 10, "holder": "LowerPilot"}},
                    source="manual_sync",
                    ts=1700001020.0,
                )
                by_k3 = {r["key"]: r for r in refreshed_low}
                self.assertEqual(by_k3["r3"]["universe_max_level"], 19)
                self.assertGreaterEqual(db.summary()["counts"]["universe_research_max_history"], 2)
            finally:
                db.close()

    def test_colony_attempts_and_hot_history_table_division_and_incremental_sync(self) -> None:
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

        with tempfile.TemporaryDirectory(prefix="si_mig_hot_") as tmp:
            db = GameDatabase(Path(tmp) / "hot.db")
            try:
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
                db.record_full_state_snapshot(state, source="test_hot")
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

                summ = db.summary()
                self.assertEqual(summ["counts"]["latest_state_hot"], 1)
                self.assertEqual(summ["counts"]["empire_planets_hot"], 1)
                self.assertEqual(summ["counts"]["galaxy_sector_hot"], 1)
                self.assertEqual(summ["counts"]["colony_candidates_hot"], 2)
                self.assertEqual(set(summ["table_tiers"]["hot"]), hot_set)
                self.assertEqual(set(summ["table_tiers"]["history"]), hist_set)

                latest_hot = db.get_latest_state_hot(include_payload=True)
                self.assertIsNotNone(latest_hot)
                assert latest_hot is not None
                self.assertEqual(latest_hot["active_planet_id"], 101)
                self.assertEqual(latest_hot["source"], "test_hot")

                cands = db.get_colony_candidates(
                    origin_coords="02:150:05",
                    min_fields_target=300,
                    exclude_coords={"02:150:05"},
                    limit=50,
                )
                self.assertEqual([c["coords"] for c in cands], ["02:150:08"])

                db.restore_colony_candidate("02:150:09", ts=1_760_000_050.0)
                cands_after = db.get_colony_candidates(
                    origin_coords="02:150:05",
                    min_fields_target=300,
                    exclude_coords={"02:150:05"},
                    limit=50,
                )
                self.assertEqual({c["coords"] for c in cands_after}, {"02:150:08", "02:150:09"})

                rebuilt = db.rebuild_hot_tables(ts=1_760_000_100.0)
                self.assertEqual(rebuilt["galaxy_sector_hot"], 1)
                self.assertEqual(rebuilt["colony_candidates_hot"], 2)
            finally:
                db.close()

    def test_hourly_production_and_loot_recycle_hourly_rollups(self) -> None:
        self.assertIn("loot_recycle_hourly", set(HOT_TABLES))
        self.assertIn("hourly_production_history", set(HISTORY_TABLES))
        self.assertEqual(TABLE_TIER_BY_NAME["loot_recycle_hourly"], "hot")
        self.assertEqual(TABLE_TIER_BY_NAME["hourly_production_history"], "history")

        base_hr = 1_760_000_000 // 3600 * 3600
        with tempfile.TemporaryDirectory(prefix="si_mig_v14_") as tmp:
            db = GameDatabase(Path(tmp) / "v14.db")
            try:
                db.record_game_messages(
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

                lr = db.get_loot_recycle_hourly(hours=24, now_ts=float(base_hr + 1800))
                self.assertEqual(len(lr["history"]), 24)
                self.assertEqual(lr["summary"]["total_stolen"], 10000.0)
                self.assertEqual(lr["summary"]["total_recycled"], 2000.0)
                self.assertEqual(lr["summary"]["grand_total"], 12000.0)
                self.assertEqual(lr["summary"]["attack_count"], 1)
                self.assertEqual(lr["summary"]["recycle_count"], 1)

                p1 = db.record_hourly_production(
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
                p1_dup = db.record_hourly_production(
                    pig_iron_per_hour=9999.0,
                    ts=float(base_hr + 155),
                )
                p2 = db.record_hourly_production(
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
                hist = db.get_hourly_production_history(limit=10)
                self.assertEqual(len(hist), 2)
                self.assertEqual(hist[0]["pig_iron_per_hour"], 5000.0)
                self.assertEqual(hist[1]["active_builders"], 3)
                self.assertLess(hist[0]["minute_bucket"], hist[1]["minute_bucket"])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
