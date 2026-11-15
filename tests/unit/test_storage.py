"""Unit tests for SQLite `GameDatabase` persistence layer."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from spaceinvasion.parsing.models import (
    GalaxySlot,
    SpyReport,
)
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
    TutorialStatus,
    TutorialTask,
)
from tests.pg_harness import open_test_db as GameDatabase, run_pg_exec, run_pg_query
from spaceinvasion.core.model import PlannedAction


def _sample_db_state(*, ts: float = 1000.0, step: int = 6, b1: int = 12, b2_rate: int = 0) -> GameState:
    planet = PlanetState(
        planet_id=12476,
        name="Colony",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=55,
        fields_max=230,
        resources=Resources(1500, 2900, 6300, 840),
        storage=Resources(100000, 100000, 100000, 100000),
        energy=EnergyBalance(850, 814),
        buildings={"b1": b1, "b2": 11, "b3": 11, "b4": 8, "b5": 13},
        ships={"s1": 1},
        economy_rates={"resource1": 100, "resource2": b2_rate, "resource3": 100, "resource4": 100},
    )
    tut = TutorialStatus(
        step=step,
        total_steps=11,
        tasks=(
            TutorialTask("Build a Small transport 1 / 2", "Small transport", 1, 1, 2, "ship"),
            TutorialTask("Have a successful fight 0 / 1", "Have a successful fight", None, 0, 1, "milestone"),
        ),
        reward_text="10000 Pig iron, 10000 Metal, 10000 Kryptonite, 2000 Spice",
        can_complete=False,
    )
    return GameState(
        universe_id=31,
        user_id=103603,
        nickname="ventkx86",
        timestamp=ts,
        planets=(planet,),
        active_planet_id=12476,
        researches={"r1": 0, "r2": 2},
        tutorial=tut,
        account_points=4210,
        account_rank=218,
    )


class TestGameDatabase(unittest.TestCase):
    def test_persists_snapshots_tutorial_economy_actions_and_intel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = GameDatabase(tmp_path / "spaceinvasion.db")
            try:
                samples = db.record_state_snapshot(_sample_db_state(ts=1000.0, step=6, b1=12, b2_rate=0))
                self.assertEqual(len(samples), 1)
                self.assertEqual(db.get_all_economy_rates()[12476]["resource2"], 0)

                # Step 7 observation marks step 6 completed
                db.record_state_snapshot(_sample_db_state(ts=2000.0, step=7, b1=13, b2_rate=100))
                tut_hist = db.get_tutorial_history()
                self.assertEqual(len(tut_hist), 2)
                self.assertIsNotNone(tut_hist[0]["completed_ts"])
                self.assertIsNone(tut_hist[1]["completed_ts"])

                # Action log redacts sid tokens
                act = PlannedAction(
                    priority="P1",
                    kind="THROTTLE_BUILDING",
                    planet_id=12476,
                    planet_coords="02:380:12",
                    target_id=2,
                    target_name="Blast furnace 0%",
                    target_level=0,
                    reason="Save Pig iron",
                    executable_now=True,
                )
                db.record_action(
                    source="api",
                    command="throttle_building",
                    action=act,
                    executed=True,
                    dry_run=False,
                    message="Done sid=deadbeef1234abcd",
                    ts=2100.0,
                )
                recent = db.get_recent_actions()
                self.assertEqual(len(recent), 1)
                self.assertIn("sid=REDACTED", recent[0]["message"])

                # Spy report + GalaxySlot persistence + Raid BashTracker
                rep = SpyReport(
                    target_coords="02:380:05",
                    owner="YellowPlayer",
                    resources=Resources(5000, 4000, 3000, 1000),
                    fleet={},
                    defense={},
                    is_spacia=False,
                )
                db.record_spy_report(rep, points=25, is_inactive=True, ts=2200.0)
                saved = db.record_galaxy_slots(
                    [
                        GalaxySlot("02:380:01", 1, "", "", "free"),
                        GalaxySlot("02:380:05", 5, "ReusedPlanet", "YellowPlayer", "yellow"),
                        GalaxySlot("02:380:09", 9, "Spacia", "Spacia", "purple", is_spacia=True),
                    ],
                    ts=2210.0,
                )
                self.assertEqual(saved, 2)
                targets = db.get_spy_targets()
                self.assertEqual(len(targets), 2)
                p5 = next(t for t in targets if t["coords"] == "02:380:05")
                # Preserved spy resources rather than clobbering with zeros
                self.assertEqual(p5["resources"]["pig_iron"], 5000)
                self.assertEqual(p5["planet_name"], "ReusedPlanet")
                self.assertEqual(p5["status_color"], "yellow")
                p9 = next(t for t in targets if t["coords"] == "02:380:09")
                self.assertTrue(p9["is_spacia"])

                db.record_raid("02:380:05", attacked_ts=2250.0, is_exempt=False, outcome="WIN")
                tracker = db.load_bash_tracker(now=2300.0)
                self.assertEqual(tracker.attacks_last_24h("02:380:05", timestamp=2300.0), 1)

                summary = db.summary()
                self.assertGreaterEqual(summary["counts"]["planet_snapshots"], 2)
                self.assertEqual(summary["counts"]["spy_targets"], 2)
                self.assertEqual(summary["counts"]["raid_history"], 1)
                latest_st = db.load_latest_game_state()
                self.assertIsNotNone(latest_st)
                assert latest_st is not None
                self.assertEqual(latest_st.planets[0].planet_id, 12476)
            finally:
                db.close()

    def test_get_raid_stats_empty_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "spaceinvasion.db")
            try:
                stats = db.get_raid_stats()
                ov = stats["overall"]
                self.assertEqual(ov["attempts"], 0)
                self.assertEqual(ov["wins"], 0)
                self.assertEqual(ov["losses"], 0)
                self.assertEqual(ov["success_rate"], 0.0)
                self.assertEqual(ov["total_loot"], {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0})
                self.assertEqual(ov["avg_loot"], {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0})
                self.assertEqual(ov["total_loot_spice_eq"], 0.0)
                self.assertEqual(ov["avg_loot_spice_eq"], 0.0)
                self.assertEqual(ov["recency_weighted_loot_spice_eq"], 0.0)
                self.assertIsNone(ov["last_attack"])
                self.assertEqual(stats["per_target"], {})
                self.assertEqual(stats["history"], [])
                tsum = stats["telemetry_summary"]
                self.assertEqual(tsum["rank_snapshots_count"], 0)
                self.assertEqual(tsum["planet_snapshots_count"], 0)
                self.assertEqual(tsum["action_history_count"], 0)
                self.assertEqual(tsum["spy_targets_count"], 0)
            finally:
                db.close()

    def test_get_raid_stats_aggregates_spice_eq_recency_and_window_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "spaceinvasion.db")
            try:
                # Target A (02:380:04): growing loot over time -> recency_weighted > avg
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1000.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 1000, "metal": 0, "kryptonite": 0, "spice": 0},
                )  # 1000/6 = 166.667 spice-eq
                db.record_raid(
                    "02:380:04",
                    attacked_ts=2000.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 2000, "metal": 1000, "kryptonite": 500, "spice": 500},
                )  # 2000/6 + 1000/2.5 + 500/3 + 500 = 1400.0 spice-eq
                db.record_raid(
                    "02:380:04",
                    attacked_ts=3000.0,
                    is_exempt=True,
                    outcome="WIN",
                    loot={"pig_iron": 10000, "metal": 4000, "kryptonite": 2000, "spice": 1000},
                )  # 10000/6 + 4000/2.5 + 2000/3 + 1000 = 4933.333 spice-eq

                # Target B (02:380:05): shrinking loot + losses -> recency_weighted < avg
                db.record_raid(
                    "02:380:05",
                    attacked_ts=1000.0,
                    is_exempt=False,
                    outcome="Victory",
                    loot={"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 10000},
                )
                db.record_raid("02:380:05", attacked_ts=2000.0, is_exempt=False, outcome="loss", loot={})
                db.record_raid("02:380:05", attacked_ts=3000.0, is_exempt=False, outcome="Aborted", loot={})
                db.record_raid("02:380:05", attacked_ts=4000.0, is_exempt=False, outcome="raid_sent", loot={})

                stats = db.get_raid_stats()
                ov = stats["overall"]
                self.assertEqual(ov["attempts"], 7)
                self.assertEqual(ov["wins"], 4)
                self.assertEqual(ov["losses"], 3)
                self.assertAlmostEqual(ov["success_rate"], round(4 / 7, 6))
                self.assertEqual(ov["last_attack"]["target_coords"], "02:380:05")
                self.assertEqual(ov["last_attack"]["attacked_ts"], 4000.0)

                pt4 = stats["per_target"]["02:380:04"]
                self.assertEqual(pt4["attempts"], 3)
                self.assertEqual(pt4["wins"], 3)
                self.assertAlmostEqual(pt4["total_loot_spice_eq"], 6500.0, places=2)
                self.assertGreater(pt4["recency_weighted_loot_spice_eq"], pt4["avg_loot_spice_eq"])

                pt5 = stats["per_target"]["02:380:05"]
                self.assertEqual(pt5["attempts"], 4)
                self.assertEqual(pt5["wins"], 1)
                self.assertEqual(pt5["losses"], 3)
                self.assertAlmostEqual(pt5["success_rate"], 0.25)
                self.assertLess(pt5["recency_weighted_loot_spice_eq"], pt5["avg_loot_spice_eq"])

                ts_list = [h["attacked_ts"] for h in stats["history"]]
                self.assertEqual(ts_list, sorted(ts_list))

                windowed = db.get_raid_stats(window_seconds=1500.0, now=4000.0)
                self.assertEqual(windowed["overall"]["attempts"], 3)
                self.assertTrue(all(h["attacked_ts"] >= 2500.0 for h in windowed["history"]))
            finally:
                db.close()

    def test_get_full_history_summary_all_five_tables_and_sid_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "spaceinvasion.db")
            try:
                db.record_state_snapshot(_sample_db_state(ts=1000.0, step=6, b1=12))
                db.record_state_snapshot(_sample_db_state(ts=2000.0, step=7, b1=14))
                db.record_spy_report(
                    SpyReport("02:380:04", "Sleepy", Resources(5000, 2000, 1000, 500), is_spacia=False),
                    is_inactive=True,
                    ts=2050.0,
                )
                db.record_galaxy_scan(2, 380, (), ts=2050.0)

                act_ok = PlannedAction(
                    "P2",
                    "BUILD",
                    12476,
                    "02:380:12",
                    target_id=1,
                    target_name="Mine",
                    target_level=13,
                    reason="Upgrade mine",
                    executable_now=True,
                )
                db.record_action(
                    source="api",
                    command="build",
                    action=act_ok,
                    executed=True,
                    dry_run=False,
                    message="sid=SECRET_SID_99887766",
                    ts=2100.0,
                )
                db.record_action(
                    source="api",
                    command="build",
                    action=act_ok,
                    executed=False,
                    dry_run=False,
                    message="Aborted sid=deadbeef1234",
                    ts=2110.0,
                )
                db.record_action(
                    source="api", command="build", action=act_ok, executed=False, dry_run=True, message="Dry", ts=2120.0
                )

                db.record_raid(
                    "02:380:04",
                    attacked_ts=2200.0,
                    is_exempt=True,
                    outcome="Victory url?sid=SECRET_SID_99887766&next=sid=deadbeef1234",
                    loot={"pig_iron": 3000, "metal": 1000, "kryptonite": 500, "spice": 100},
                )

                summary = db.get_raid_stats()["telemetry_summary"]
                for key in (
                    "raid_history",
                    "spy_targets",
                    "planet_snapshots",
                    "rank_snapshots",
                    "action_history",
                    "rank_snapshots_count",
                    "planet_snapshots_count",
                    "action_history_count",
                    "spy_targets_count",
                    "galaxy_scans_count",
                    "points_delta",
                    "points_trend",
                    "pts_per_hr",
                    "economy_trend",
                    "action_count",
                    "action_successes",
                    "action_success_rate",
                ):
                    self.assertIn(key, summary)

                self.assertEqual(summary["action_history"]["total"], 3)
                self.assertEqual(summary["action_history"]["executed"], 1)
                self.assertEqual(summary["action_history"]["failed"], 1)
                self.assertEqual(summary["action_history"]["dry_run"], 1)
                self.assertEqual(summary["planet_snapshots"]["trend"]["building_levels_delta"], 2)
                self.assertEqual(summary["galaxy_scans_count"], 1)
                self.assertEqual(summary["spy_targets"]["spied_count"], 1)

                dumped = json.dumps({"summary": summary, "stats": db.get_raid_stats()})
                self.assertNotIn("SECRET_SID_99887766", dumped)
                self.assertNotIn("deadbeef1234", dumped)
                self.assertNotIn("ventkx86", dumped)
                leaked = [
                    s for s in re.findall(r"sid=([A-Za-z0-9_-]{4,})", dumped, flags=re.I) if s.upper() != "REDACTED"
                ]
                self.assertEqual(leaked, [])
            finally:
                db.close()

    def test_attack_history_json_retired(self) -> None:
        repo_hist = Path(__file__).resolve().parents[2] / "data" / "attack_history.json"
        self.assertFalse(repo_hist.exists(), "Legacy data/attack_history.json should be retired into DB")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_mem = GameDatabase(":memory:")
            try:
                mem_sum = db_mem.summary()
                self.assertEqual(mem_sum["backend"], "postgres")
                self.assertTrue(str(mem_sum["db_path"]).startswith("postgresql://"))
            finally:
                db_mem.close()
            db_tmp = GameDatabase(tmp_path / "spaceinvasion.db")
            try:
                db_tmp.record_raid("02:380:04", attacked_ts=100.0, outcome="Victory", loot={"spice": 100})
                self.assertFalse((tmp_path / "attack_history.json").exists())
            finally:
                db_tmp.close()

        self.assertFalse(repo_hist.exists())

    def test_record_raid_auto_sync_atomic_json_and_reopen_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "spaceinvasion.db"
            db1 = GameDatabase(db_path)
            try:
                db1.record_state_snapshot(_sample_db_state(ts=5000.0))
                db1.record_raid(
                    "02:380:04",
                    attacked_ts=5100.0,
                    is_exempt=True,
                    outcome="Victory sid=SID_TEST_ALPHA_999",
                    loot={"pig_iron": 6000, "metal": 2000, "kryptonite": 1000, "spice": 500},
                )
            finally:
                db1.close()

            self.assertFalse((tmp_path / "attack_history.json").exists())
            self.assertEqual(list(tmp_path.glob("*.tmp")), [])
            stats_db = GameDatabase(db_path)
            try:
                parsed = stats_db.get_raid_stats()
            finally:
                stats_db.close()
            raw = json.dumps(parsed, allow_nan=False)
            self.assertNotIn("SID_TEST_ALPHA_999", raw)
            for k in ("overall", "per_target", "history", "telemetry_summary"):
                self.assertIn(k, parsed)
            self.assertEqual(parsed["overall"]["attempts"], 1)

            db2 = GameDatabase(db_path)
            try:
                reopened = db2.get_raid_stats()
                self.assertEqual(reopened["overall"]["attempts"], 1)
                self.assertEqual(reopened["overall"]["wins"], 1)
                self.assertEqual(reopened["per_target"]["02:380:04"]["last_loot"]["spice"], 500)
                self.assertGreaterEqual(reopened["telemetry_summary"]["planet_snapshots_count"], 1)
            finally:
                db2.close()

    def test_memory_uris_no_disk_ddl_close_on_error_and_corrupt_cells(self) -> None:
        import os
        from sqlite3 import OperationalError as _SqliteOperationalError

        with tempfile.TemporaryDirectory(prefix="si_mem_") as tmp:
            tmp_root = Path(tmp)
            cwd_dir = tmp_root / "cwd_isolated"
            cwd_dir.mkdir()
            old_cwd = Path.cwd()
            try:
                os.chdir(cwd_dir)
                for mem_uri in (":memory:", "file::memory:?cache=shared"):
                    db = GameDatabase(mem_uri)
                    try:
                        self.assertEqual(db.backend_name, "postgres")
                        db.record_raid(
                            "2:380:4",
                            attacked_ts=float("inf"),
                            is_exempt=False,
                            outcome="Victory",
                            loot={"pig_iron": 1e400, "metal": float("inf"), "kryptonite": float("nan"), "spice": 150},
                        )
                        stats = db.get_raid_stats()
                        json.dumps(stats, allow_nan=False)
                        self.assertIn("02:380:04", stats["per_target"])
                        self.assertEqual(stats["per_target"]["02:380:04"]["last_loot"]["spice"], 150)
                        tracker = db.load_bash_tracker()
                        self.assertEqual(
                            tracker.attacks_last_24h("02:380:04", timestamp=stats["history"][0]["attacked_ts"] + 60.0),
                            1,
                        )
                    finally:
                        db.close()
                self.assertEqual(list(cwd_dir.iterdir()), [])
            finally:
                os.chdir(old_cwd)

            # Corrupt/non-object JSONB cells degrade safely; malformed raw JSON is rejected by PG JSONB
            import psycopg

            db_c = GameDatabase(tmp_root / "corrupt.db")
            try:
                with self.assertRaises(psycopg.errors.InvalidTextRepresentation):
                    run_pg_exec(
                        db_c,
                        "INSERT INTO spy_targets(coords, owner, fleet_json, defense_json, last_scanned_ts) "
                        "VALUES('01:001:99', 'Bad', '{bad', 'null', 10.0)",
                    )
                run_pg_exec(
                    db_c,
                    "INSERT INTO economy_rates_cache(planet_id, coords, rates_json, updated_ts) VALUES(1, '01:001:01', 'null', 10.0)",
                )
                run_pg_exec(
                    db_c,
                    "INSERT INTO spy_targets(coords, owner, fleet_json, defense_json, last_scanned_ts) VALUES('01:001:02', 'X', '[]', 'null', 10.0)",
                )
                run_pg_exec(
                    db_c,
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                    "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                    "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                    "economy_rates_json, researches_json) "
                    "VALUES(10.0, 31, 1, '01:001:01', 'P', 'normal', 20, 10, 200, 0, 0, 0, 0, 0, 0, "
                    "'[]', '123', 'null', 'null', '[]')",
                )
                run_pg_exec(
                    db_c,
                    "INSERT INTO tutorial_progress(step, total_steps, tasks_json, reward_text, can_complete, first_seen_ts, last_seen_ts) "
                    "VALUES(1, 5, '{}', 'Reward', 0, 10.0, 10.0)",
                )
                self.assertEqual(db_c.get_all_economy_rates(), {})
                self.assertEqual(len(db_c.get_spy_targets()), 1)
                self.assertEqual(len(db_c.get_tutorial_history()), 1)
                self.assertIsInstance(db_c.get_raid_stats()["telemetry_summary"], dict)
            finally:
                db_c.close()

            # Migration error after connect disposes SQLAlchemy engine cleanly without leaking connections
            from spaceinvasion.db.store import DatabaseStore
            from tests.pg_harness import get_shared_pg_harness

            err_dsn = get_shared_pg_harness().create_database(prefix="broken_ddl", from_template=False)
            with DatabaseStore.from_dsn(err_dsn, pool_size=1, max_overflow=0) as err_store:
                with patch(
                    "spaceinvasion.db.store.upgrade_database",
                    side_effect=RuntimeError("boom"),
                ):
                    with self.assertRaises(RuntimeError):
                        err_store.ensure_ready(backup_fn=None)

    def test_corrupt_numeric_cells_in_readers_and_spy_report_coord_normalization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_stor_adv_") as tmp:
            db = GameDatabase(Path(tmp) / "adv.db")
            try:
                # 1. Corrupt non-finite / out-of-range numeric cells in rank_snapshots, galaxy_scans, action_history, planet_snapshots
                run_pg_exec(
                    db,
                    "INSERT INTO rank_snapshots(ts, universe_id, nickname, server_points, server_rank, "
                    "estimated_points, pts_per_hr, spice_value, planets) "
                    "VALUES(?, 31, 'u', NULL, NULL, ?, ?, ?, -1)",
                    (float("nan"), float("nan"), float("inf"), float("-inf")),
                )
                import psycopg

                with self.assertRaises(psycopg.errors.CheckViolation):
                    run_pg_exec(db, "INSERT INTO galaxy_scans(galaxy, system, last_scanned_ts) VALUES(0, 10, 100.0)")
                run_pg_exec(
                    db,
                    "INSERT INTO galaxy_scans(galaxy, system, last_scanned_ts) VALUES(2, 380, ?)",
                    (float("nan"),),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO action_history(ts, source, command, kind, planet_id, target_id, executed, dry_run) "
                    "VALUES(?, 'api', 'throttle', 'THROTTLE_BUILDING', 0, 2, 1, 0)",
                    (float("nan"),),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO action_history(ts, source, command, kind, planet_id, target_id, executed, dry_run) "
                    "VALUES(?, 'api', 'throttle', 'THROTTLE_BUILDING', 101, 2, 1, 0)",
                    (float("nan"),),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                    "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                    "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                    "economy_rates_json, researches_json) "
                    "VALUES(?, 31, 1, '01:001:01', 'P', 'normal', 20, 10, 200, 0, 0, 0, 0, 0, 0, "
                    "'{}', '{}', '{}', '{}', '{}')",
                    (float("nan"),),
                )

                rk = db.get_rank_samples()
                self.assertEqual(len(rk), 1)
                self.assertEqual(rk[0]["ts"], 0.0)
                self.assertIsNone(rk[0]["server_points"])
                self.assertIsNone(rk[0]["server_rank"])

                scans = db.get_galaxy_scans()
                self.assertNotIn("00:010", scans)
                self.assertEqual(scans.get("02:380"), 0.0)

                throttles = db.get_last_throttle_timestamps()
                self.assertEqual(throttles, {101: 0.0})

                full_sum = db.get_raid_stats()["telemetry_summary"]
                self.assertEqual(full_sum["planet_snapshots"]["first_ts"], 0.0)
                self.assertEqual(full_sum["planet_snapshots"]["latest_ts"], 0.0)

                # 2. record_spy_report normalizes coords and merges legacy unnormalized row
                run_pg_exec(
                    db,
                    "INSERT INTO spy_targets(coords, owner, status_color, is_inactive, last_scanned_ts) "
                    "VALUES('2:380:4', 'LegacyYellow', 'yellow', 1, 500.0)",
                )
                db.record_spy_report(
                    SpyReport(
                        target_coords="2:380:4",
                        owner="",
                        resources=Resources(12000, 6000, 3000, 1000),
                    ),
                    ts=1000.0,
                )
                targets = db.get_spy_targets()
                matching = [t for t in targets if t["coords"] == "02:380:04"]
                self.assertEqual(len(matching), 1)
                self.assertEqual(matching[0]["owner"], "LegacyYellow")
                self.assertEqual(matching[0]["resources"]["pig_iron"], 12000)
                raw_rows = run_pg_query(db, "SELECT coords FROM spy_targets")
                self.assertEqual([r["coords"] for r in raw_rows], ["02:380:04"])
            finally:
                db.close()

    def test_spy_target_direct_lookup_galaxy_coalescing_and_corrupt_snapshot_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "gap01_04.db")
            try:
                # Pre-existing legacy unnormalized row with detailed spy intel
                run_pg_exec(
                    db,
                    "INSERT INTO spy_targets("
                    "coords, owner, status_color, is_inactive, pig_iron, metal, kryptonite, spice, last_spied_ts, last_scanned_ts"
                    ") VALUES('2:380:4', 'LegacyOwner', 'white', 0, 9999, 111, 0, 0, 800.0, 500.0)",
                )

                direct_unnorm = db.get_spy_target_by_coords("2:380:4")
                self.assertIsNotNone(direct_unnorm)
                self.assertEqual(direct_unnorm["coords"], "02:380:04")
                self.assertEqual(direct_unnorm["resources"]["pig_iron"], 9999)
                self.assertIsNone(db.get_spy_target_by_coords("99:999:99"))

                # GAP-04: record_galaxy_slots with ALREADY normalized slot.coords coalesces legacy '2:380:4'
                db.record_galaxy_slots(
                    [GalaxySlot("02:380:04", 4, "IdlePlanet", "NewOwner", "yellow")],
                    ts=1000.0,
                )
                raw_rows = run_pg_query(db, "SELECT coords FROM spy_targets")
                self.assertEqual([r["coords"] for r in raw_rows], ["02:380:04"])
                merged = db.get_spy_target_by_coords("2:380:4")
                self.assertIsNotNone(merged)
                self.assertEqual(merged["status_color"], "yellow")
                self.assertTrue(merged["is_inactive"])
                self.assertEqual(merged["owner"], "NewOwner")
                self.assertEqual(merged["resources"]["pig_iron"], 9999)
                self.assertEqual(merged["last_spied_ts"], 800.0)

                # Caveat: pre-existing non-finite snapshot timestamps/points do not crash record_state_snapshot
                run_pg_exec(
                    db,
                    "INSERT INTO rank_snapshots(ts, universe_id, estimated_points, pts_per_hr, spice_value, planets) "
                    "VALUES(?, 31, ?, 0.0, 0.0, 1)",
                    (float("nan"), float("nan")),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                    "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                    "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                    "economy_rates_json, researches_json) "
                    "VALUES(?, 31, 12476, '02:380:12', 'Colony', 'normal', 31, 55, 230, 0, 0, 0, 0, 0, 0, "
                    "'{}', '{}', '{}', '{}', '{}')",
                    (float("nan"),),
                )
                db.record_state_snapshot(_sample_db_state(ts=2000.0))
            finally:
                db.close()

    def test_extreme_ints_on_record_raid_and_planet_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for idx in range(1):
                db = GameDatabase(Path(tmp) / f"extreme_{idx}.db")
                try:
                    db.record_raid(
                        "02:380:04",
                        attacked_ts=1_700_000_000.0,
                        is_exempt=True,
                        outcome="Victory",
                        loot={"pig_iron": 10**5000, "spice": 100},
                    )
                    stats_json = json.dumps(db.get_raid_stats(), allow_nan=False)
                    stats = json.loads(stats_json)
                    self.assertIn("per_target", stats)
                    huge_planet = PlanetState(
                        planet_id=10**5000,
                        name="Huge",
                        coords="02:380:12",
                        planet_type="normal",
                        temperature=31,
                        fields_used=10**5000,
                        fields_max=10**5000,
                        resources=Resources(10**5000, 100, 100, 100),
                        storage=Resources(100000, 100000, 100000, 100000),
                        energy=EnergyBalance(10**5000, 100),
                        buildings={"b1": 10},
                    )
                    huge_state = GameState(
                        universe_id=10**5000,
                        user_id=10**5000,
                        nickname="u",
                        timestamp=1_700_000_000.0,
                        planets=(huge_planet,),
                        active_planet_id=10**5000,
                        account_points=10**5000,
                        account_rank=10**5000,
                    )
                    db.record_state_snapshot(huge_state)
                finally:
                    db.close()

    def test_r7_storage_queries_guard_huge_limits_and_nonfinite_float_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r7_queries.db")
            try:
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_000.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 1000},
                )
                db.record_galaxy_scan(2, 380, ts=1_700_000_000.0)

                self.assertEqual(db.get_rank_samples(limit=10**5000), [])
                self.assertEqual(db.get_spy_targets(limit=10**5000), [])
                self.assertEqual(db.get_last_throttle_timestamps(building_id=10**5000), {})

                for bad_f in (10**400, float("nan"), float("inf")):
                    scans = db.get_galaxy_scans(max_age_seconds=bad_f, now=bad_f)  # type: ignore[arg-type]
                    self.assertIn("02:380", scans)
                    stats = db.get_raid_stats(window_seconds=bad_f, now=bad_f)  # type: ignore[arg-type]
                    self.assertEqual(stats["overall"]["attempts"], 1)
            finally:
                db.close()

    def test_r9_coord_candidates_arabic_indic_and_direct_summary_sid_redaction(self) -> None:
        from spaceinvasion.core.coords import coord_query_variants as _coord_query_candidates

        ar = "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"
        norm, cands, like_pat = _coord_query_candidates(ar)
        self.assertEqual(norm, ar)
        self.assertEqual(cands, [ar])
        self.assertIsNone(like_pat)

        secret = "DIRECT_DB_SECRET_SID_42"
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r9_redact.db")
            try:
                run_pg_exec(
                    db,
                    "INSERT INTO action_history(ts, source, command, priority, kind, planet_coords, target_name, executed, dry_run, message) "
                    "VALUES(1000.0, 'api', 'build', 'P2', 'BUILD', '01:001:01', ?, 1, 0, ?)",
                    (f"Mine ?sid={secret}", f"Done ?sid={secret}"),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO spy_targets(coords, owner, planet_name, status_color, last_scanned_ts) VALUES('02:380:04', ?, ?, 'yellow', 1000.0)",
                    (f"Owner ?sid={secret}", f"Planet ?sid={secret}"),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO tutorial_progress(step, total_steps, tasks_json, reward_text, can_complete, first_seen_ts, last_seen_ts) "
                    "VALUES(1, 5, ?, ?, 0, 1000.0, 1000.0)",
                    (json.dumps([{"description": f"Step ?sid={secret}"}], allow_nan=False), f"Reward ?sid={secret}"),
                )

                recent = db.get_recent_actions()
                self.assertEqual(len(recent), 1)
                self.assertNotIn(secret, json.dumps(recent, allow_nan=False))
                self.assertIn("REDACTED", recent[0]["target_name"])
                self.assertIn("REDACTED", recent[0]["message"])

                summary = db.summary()
                dumped = json.dumps(summary, allow_nan=False)
                self.assertNotIn(secret, dumped)
                self.assertIn("REDACTED", dumped)
            finally:
                db.close()

    def test_r10_blob_sid_redaction_literal_sid_json_and_planet_history(self) -> None:
        from spaceinvasion.core.coercion import sanitize_json_tree
        from spaceinvasion.core.redaction import redact_sensitive

        s1 = "BLOB_SECRET_11"
        s2 = "JSON_SID_22"
        pw = "PW_SECRET_33"
        san_obj = sanitize_json_tree(
            redact_sensitive(
                {
                    (10**5000, "x"): frozenset({b"?sid=" + s1.encode("utf-8")}),
                    "sid": s2,
                    "password": b"PW_SECRET_33",
                    "path": Path(f"https://x/?sid={s1}"),
                }
            )
        )
        san_dumped = json.dumps(san_obj, allow_nan=False)
        for sec in (s1, s2, pw):
            self.assertNotIn(sec, san_dumped)

        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r10.db")
            try:
                sid_url = f"https://x/?sid={s1}"
                fleet_json = json.dumps({"sid": s2, "password": pw, "s4": 3}, allow_nan=False)
                huge_num = 10**12
                run_pg_exec(
                    db,
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, energy_produced, energy_used, buildings_json, ships_json, defense_json, economy_rates_json, researches_json) "
                    "VALUES(?, 31, 42, ?, ?, 'normal', 20, 10, 200, ?, 1, 1, 1, 50, 10, ?, ?, '{}', ?, '{}')",
                    (
                        float("nan"),
                        "01:001:01",
                        sid_url,
                        huge_num,
                        fleet_json,
                        '{"b1": 100}',
                        json.dumps({"sid": s2, "resource1": 100}, allow_nan=False),
                    ),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO spy_targets(coords, owner, planet_name, status_color, points, pig_iron, fleet_json, defense_json, last_scanned_ts) "
                    "VALUES(?, ?, ?, 'yellow', ?, 0, ?, ?, 1000.0)",
                    ("02:380:04", sid_url, sid_url, huge_num, fleet_json, fleet_json),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO rank_snapshots(ts, server_points, server_rank, estimated_points, pts_per_hr, spice_value, planets) "
                    "VALUES(1000.0, ?, 10, ?, 1.0, 2.0, ?)",
                    (huge_num, float("nan"), 12),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO economy_rates_cache(planet_id, coords, rates_json, updated_ts) VALUES(?, '01:001:01', ?, 1000.0)",
                    (42, json.dumps({f"?sid={s1}": 80, "resource1": 90}, allow_nan=False)),
                )
                run_pg_exec(
                    db,
                    "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) VALUES(?, 1000.0, 0, ?, ?)",
                    ("02:380:04", sid_url, fleet_json),
                )

                rs = db.get_rank_samples()
                er = db.get_all_economy_rates()
                st_list = db.get_spy_targets()
                st_one = db.get_spy_target_by_coords("2:380:4")
                bash = db.load_bash_tracker(now=1000.0)
                full_sum = db.get_raid_stats()["telemetry_summary"]
                tot_sum = db.summary()

                self.assertIsNotNone(st_one)
                self.assertEqual(bash.attacks_last_24h("02:380:04", timestamp=1000.0), 1)
                for label, payload in (
                    ("rank_samples", rs),
                    ("economy_rates", er),
                    ("spy_targets", st_list),
                    ("spy_target_one", st_one),
                    ("full_summary", full_sum),
                    ("summary", tot_sum),
                ):
                    dumped = json.dumps(payload, allow_nan=False)
                    for sec in (s1, s2, pw):
                        self.assertNotIn(sec, dumped, f"{label} leaked {sec}")
            finally:
                db.close()

    def test_r11_normalized_planet_type_plannedaction_class_mapping_path_and_complex(self) -> None:
        from types import MappingProxyType
        from spaceinvasion.db.repos._helpers import is_win_outcome as _is_win_outcome
        from spaceinvasion.db.repos._helpers import json_dumps as _json_dumps
        from spaceinvasion.db.repos._helpers import sanitize_obj as _sanitize_obj

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"/tmp/?sid=ST_BPATH_SEC"

        dumped_path = _json_dumps(Path("/tmp/x?sid=ST_PATH_SEC"))
        self.assertNotIn("ST_PATH_SEC", dumped_path)
        dumped_bpath = _json_dumps(_BytesPath())
        self.assertNotIn("ST_BPATH_SEC", dumped_bpath)
        dumped_cplx = _json_dumps(complex(1, 2))
        self.assertIsInstance(json.loads(dumped_cplx), str)
        dumped_err = _json_dumps({"err": RuntimeError("bad ?sid=ST_ERR_SEC"), "obj": object()})
        self.assertNotIn("ST_ERR_SEC", dumped_err)
        self.assertIsInstance(_sanitize_obj(complex(3, 4)), str)
        self.assertTrue(_is_win_outcome(b"victory", 0.0))  # type: ignore[arg-type]
        self.assertFalse(_is_win_outcome(10**5000, 0.0))  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r11.db")
            try:
                st = _sample_db_state(ts=2000.0)
                ast_p = PlanetState(
                    planet_id=77,
                    name="Ast",
                    coords="01:001:05",
                    planet_type=" Asteroid ",
                    temperature=10,
                    fields_used=5,
                    fields_max=20,
                    resources=Resources(1000, 1000, 1000, 1000),
                    storage=Resources(50_000, 50_000, 50_000, 50_000),
                    energy=EnergyBalance(50, 10),
                )
                st_ast = GameState(
                    universe_id=st.universe_id,
                    user_id=st.user_id,
                    nickname=st.nickname,
                    timestamp=2000.0,
                    planets=(ast_p,),
                    active_planet_id=77,
                )
                db.record_state_snapshot(st_ast)
                row = run_pg_query(
                    db,
                    "SELECT planet_type FROM planet_snapshots WHERE planet_id = 77 ORDER BY id DESC LIMIT 1",
                )[0]
                self.assertEqual(row["planet_type"], "asteroid")
                full_sum = db.get_raid_stats()["telemetry_summary"]
                self.assertEqual(
                    full_sum["planet_snapshots"]["latest_by_planet"]["77"]["planet_type"],
                    "asteroid",
                )

                # PlannedAction class object (is_dataclass(cls) is True) + MappingProxyType + Path/huge int
                db.record_action(
                    source="test",
                    command="plan",
                    action=PlannedAction,
                    executed=False,
                    dry_run=True,
                    message="class check",
                )
                db.record_action(
                    source=b"test_src",  # type: ignore[arg-type]
                    command=Path("cmd?sid=ACT_CMD_SEC"),  # type: ignore[arg-type]
                    action=MappingProxyType(
                        {
                            "priority": 10**5000,
                            "kind": b"BUILD",
                            "planet_id": 77,
                            "planet_coords": "01:001:05",
                            "target_id": 7,
                            "target_name": Path("Dev?sid=ACT_NAME_SEC"),
                            "reason": RuntimeError("?sid=ACT_REASON_SEC"),
                        }
                    ),
                    executed=False,
                    dry_run=True,
                    message=RuntimeError("?sid=ACT_MSG_SEC"),  # type: ignore[arg-type]
                )
                recent = db.get_recent_actions(limit=5)
                recent_dump = json.dumps(recent, allow_nan=False)
                for sec in ("ACT_CMD_SEC", "ACT_NAME_SEC", "ACT_REASON_SEC", "ACT_MSG_SEC"):
                    self.assertNotIn(sec, recent_dump)
            finally:
                db.close()

    def test_r12_storage_redacts_authorization_json_pw_exceptions_and_broken_pathlike(self) -> None:
        from types import MappingProxyType
        from spaceinvasion.db.repos._helpers import json_dumps as _json_dumps

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom ?password=ST_FSPATH_PW")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        deep_key: object = Path("?password=ST_DP5_PW")
        for _ in range(5):
            deep_key = (deep_key,)
        dumped = _json_dumps(
            {
                "auth_header": "Authorization: Bearer ST_BEARER_SEC",
                "json_blob": '{"password": "ST_JSON_PW_SEC", "api_key": "ST_JSON_KEY_SEC"}',
                "authorization": "ST_DICT_AUTH_SEC",
                "long_s": "\u017fid=ST_LONGS_SEC",
                "exc": RuntimeError(10**5000, "?password=ST_EXC_PW"),
                "bad_fspath": _RaiseFspath(),
                "int_path": _IntPath(),
                "bad_repr": _RaiseStrRepr(),
                deep_key: "ok",
                "mixed_keys": MappingProxyType({1: "a", "b": 2, _RaiseStrRepr(): "c"}),
            }
        )
        json.loads(dumped, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        for sec in (
            "ST_FSPATH_PW",
            "ST_DP5_PW",
            "ST_BEARER_SEC",
            "ST_JSON_PW_SEC",
            "ST_JSON_KEY_SEC",
            "ST_DICT_AUTH_SEC",
            "ST_LONGS_SEC",
            "ST_EXC_PW",
        ):
            self.assertNotIn(sec, dumped)
        self.assertIn("authorization: bearer redacted", dumped.lower())

        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r12.db")
            try:
                db.record_action(
                    source=_RaiseFspath(),  # type: ignore[arg-type]
                    command=_RaiseStrRepr(),  # type: ignore[arg-type]
                    action={"kind": "BUILD", "reason": "Authorization: Bearer ST_DB_SEC", "target_name": _IntPath()},
                    executed=False,
                    dry_run=True,
                    message=RuntimeError(10**5000, "?password=ST_DB_PW"),  # type: ignore[arg-type]
                )
                recent = json.dumps(db.get_recent_actions(limit=5), allow_nan=False)
                self.assertNotIn("ST_DB_SEC", recent)
                self.assertNotIn("ST_DB_PW", recent)
            finally:
                db.close()

    def test_r13_storage_redacts_compound_secrets_unicode_fastpath_and_quoted_bearer(self) -> None:
        from spaceinvasion.core.redaction import redact_sensitive as _raw_text
        from spaceinvasion.db.repos._helpers import json_dumps as _json_dumps
        from spaceinvasion.db.repos._helpers import sanitize_text as _sanitize_text

        cyc = RuntimeError("cyc")
        cyc.args = (10**5000, cyc)
        self.assertIsInstance(_raw_text(cyc), str)

        self.assertEqual(_sanitize_text('Authorization: Bearer "SECRET"'), 'Authorization: Bearer "REDACTED"')
        self.assertEqual(_sanitize_text("Authorization: Bearer [SECRET]"), "Authorization: Bearer [REDACTED]")
        self.assertEqual(_sanitize_text("Bearer <SECRET>"), "Bearer <REDACTED>")
        self.assertEqual(_sanitize_text('Authorization: Bearer ""'), 'Authorization: Bearer ""')
        self.assertEqual(
            _sanitize_text("?s\u0130d=ST_I1&?s\u0131d=ST_I2&?cook\u0130e=ST_CK&?ap\u0131_key=ST_AK"),
            "?s\u0130d=REDACTED&?s\u0131d=REDACTED&?cook\u0130e=REDACTED&?ap\u0131_key=REDACTED",
        )
        benign = "has_sid=false&has_password=true&no_secret=false&cookie_path=/tmp/c&bsid=123"
        self.assertEqual(_sanitize_text(benign), benign)

        dumped = _json_dumps(
            {
                "client_secret": "ST_CSEC",
                "user_password": "ST_UPW",
                "bot_api_key": "ST_BKEY",
                "session_cookie": "ST_SCK",
                "session_sid": "ST_SSID",
                "has_sid": "false",
                "has_password": "true",
                "cookie_path": "/tmp/c.json",
                "kv": "client_secret=KV_CSEC&session_sid=KV_SSID&has_sid=false",
            }
        )
        for sec in ("ST_CSEC", "ST_UPW", "ST_BKEY", "ST_SCK", "ST_SSID", "KV_CSEC", "KV_SSID"):
            self.assertNotIn(sec, dumped)
        parsed = json.loads(dumped)
        self.assertEqual(parsed["has_sid"], "false")
        self.assertEqual(parsed["has_password"], "true")
        self.assertEqual(parsed["cookie_path"], "/tmp/c.json")
        self.assertIn("has_sid=false", parsed["kv"])

        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r13.db")
            try:
                db.record_action(
                    source="test",
                    command="plan",
                    action={
                        "kind": "BUILD",
                        "reason": 'Authorization: Bearer "DB_Q_BEAR" ?s\u0130d=DB_TURK_SID',
                        "target_name": "Mine",
                    },
                    executed=False,
                    dry_run=True,
                    message="client_secret=DB_ACT_CSEC",
                )
                recent = json.dumps(db.get_recent_actions(limit=5), allow_nan=False)
                for sec in ("DB_Q_BEAR", "DB_TURK_SID", "DB_ACT_CSEC"):
                    self.assertNotIn(sec, recent)
            finally:
                db.close()

    def test_r14_storage_sid_delim_url_json_cycle_and_dag20_safety(self) -> None:
        import time
        from spaceinvasion.core.catalog.json_sanitize import safe_key, sanitize_json_tree
        from spaceinvasion.core.redaction import redact_sensitive
        from spaceinvasion.db.repos._helpers import sanitize_text

        vectors = (
            "%22password%22%3A%22ST_UE_PW%22",
            "%22Bearer%20ST_UE_BR%22",
            '\\"password\\":\\"ST_ESC_PW\\"',
            '\\\\"password\\\\":\\\\"ST_DESC_PW\\\\"',
            "Bearer [MY ST SECRET]",
            "Bearer <MY ST ANG>",
            'Bearer "UNCLOSED_ST_Q',
            "Bearer ST_SCHEMELESS_BR",
            "?s\u0130d=ST_TR_I1&?sid=ST_REG_SID",
        )
        for v in vectors:
            red = sanitize_text(v)
            for marker in (
                "ST_UE_PW",
                "ST_UE_BR",
                "ST_ESC_PW",
                "ST_DESC_PW",
                "MY ST SECRET",
                "MY ST ANG",
                "UNCLOSED_ST_Q",
                "ST_SCHEMELESS_BR",
                "ST_TR_I1",
                "ST_REG_SID",
            ):
                self.assertNotIn(marker, red, f"Leaked {marker} in {red!r}")

        redos_s = "password=" + ("\\" * 5000) + '"'
        t0 = time.perf_counter()
        _ = sanitize_text(redos_s)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 250.0)

        cyc: list[object] = []
        cyc.append(cyc)
        self.assertEqual(sanitize_json_tree(cyc), ["[Cycle]"])
        self.assertIsInstance(sanitize_text(cyc), str)

        dag_exc: BaseException = RuntimeError("Bearer ST_DAG_SEC")
        for _ in range(20):
            dag_exc = RuntimeError(dag_exc, dag_exc, dag_exc, dag_exc)
        t1 = time.perf_counter()
        s_dag = sanitize_text(redact_sensitive(dag_exc))
        _ = {safe_key(k): v for k, v in {dag_exc: "ok"}.items()}
        self.assertNotIn("ST_DAG_SEC", s_dag)
        self.assertLess((time.perf_counter() - t1) * 1000.0, 50.0)

    def test_colony_attempts_append_only_logging_filtering_and_summary(self) -> None:
        now = 1_750_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "col_attempts.db")
            try:
                # 1st attempt on 02:150:08: dispatched -> deleted_undersized (215 fields)
                db.record_colony_attempt(
                    target_coords="2:150:8",
                    event_type="dispatched",
                    origin_coords="2:150:5",
                    expected_min_fields=180,
                    expected_max_fields=340,
                    temperature=32,
                    grade="S",
                    score=94,
                    status_message="Dispatched Colony ship to 02:150:08",
                    ts=now - 600.0,
                )
                db.record_colony_attempt(
                    target_coords="02:150:08",
                    event_type="deleted_undersized",
                    origin_coords="02:150:05",
                    planet_id=5001,
                    fields_rolled=215,
                    min_fields_target=300,
                    temperature=32,
                    status_message="Deleted undersized colony (215 < 300 fields) ?sid=SECRET_COL_TOK",
                    details={"planet_name": "Colony"},
                    ts=now - 300.0,
                )
                # 2nd attempt on 02:150:08: dispatched -> kept (318 fields)
                db.record_colony_attempt(
                    target_coords="02:150:08",
                    event_type="dispatched",
                    origin_coords="02:150:05",
                    status_message="Re-dispatched Colony ship to 02:150:08",
                    ts=now - 200.0,
                )
                db.record_colony_attempt(
                    target_coords="02:150:08",
                    event_type="kept",
                    origin_coords="02:150:05",
                    planet_id=5002,
                    fields_rolled=318,
                    min_fields_target=300,
                    temperature=32,
                    grade="S",
                    score=96,
                    status_message="Kept settled colony (318 >= 300 fields)",
                    ts=now - 50.0,
                )
                # Attempt on another coord 02:151:09: failed
                db.record_colony_attempt(
                    target_coords="02:151:09",
                    event_type="failed",
                    origin_coords="02:150:05",
                    status_message="Slot occupied on arrival",
                    ts=now - 20.0,
                )

                all_rows = db.get_colony_attempts(limit=50)
                self.assertEqual(len(all_rows), 5)
                self.assertEqual(all_rows[0]["target_coords"], "02:151:09")
                self.assertEqual(all_rows[1]["event_type"], "kept")
                self.assertEqual(all_rows[1]["fields_rolled"], 318)
                self.assertNotIn("SECRET_COL_TOK", json.dumps(all_rows, allow_nan=False))

                filtered = db.get_colony_attempts(target_coords="2:150:8")
                self.assertEqual(len(filtered), 4)

                summary = db.get_colony_attempts_summary()
                self.assertEqual(summary["total_events"], 5)
                self.assertEqual(summary["unique_coords_tried"], 2)
                self.assertEqual(summary["dispatched_count"], 2)
                self.assertEqual(summary["deleted_undersized_count"], 1)
                self.assertEqual(summary["kept_count"], 1)
                self.assertEqual(summary["failed_count"], 1)

                c150 = summary["by_coords"]["02:150:08"]
                self.assertEqual(c150["attempts_count"], 2)
                self.assertEqual(c150["events_count"], 4)
                self.assertEqual(c150["deleted_count"], 1)
                self.assertEqual(c150["kept_count"], 1)
                self.assertEqual(c150["last_event_type"], "kept")
                self.assertEqual(c150["last_fields_rolled"], 318)
            finally:
                db.close()

    def test_record_spy_report_rejects_fleet_return_and_startup_repairs_poisoned_free_slots(self) -> None:
        from spaceinvasion.parsing.models import (
            GalaxySlot,
            SpyReport,
        )
        from spaceinvasion.core.model import Resources

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "spaceinvasion.db"
            db = GameDatabase(db_path)
            try:
                # 1. Seed a genuine free slot in galaxy_slots (e.g. 02:379:02)
                db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:379:02",
                            position=2,
                            name="",
                            owner="",
                            status_color="free",
                            is_spacia=False,
                            has_debris=False,
                            colonize_min_fields=100,
                            colonize_max_fields=220,
                            temperature=-33,
                        )
                    ],
                    ts=1000.0,
                )

                # 2. record_spy_report must reject a poisoned fleet-return SpyReport
                poison_raw = (
                    "2026-11-17 13:04:40 System Colony [02:380:12] fleet return "
                    "Message from System: Colony [02:380:12] fleet return One of your fleets (1 Recycler) "
                    "is returning from duty from Spacia Planet [02:379:02] to its home planet Colony [02:380:12]. "
                    "The assignment was to: Recycle. 0 Pig iron, 0 Metal, 0 Kryptonite, 0 Spice were unloaded."
                )
                db.record_spy_report(
                    SpyReport(
                        target_coords="02:379:02",
                        owner="Spacia",
                        planet_name="Colony",
                        resources=Resources(120_668, 135_782, 614_098, 524_983),
                        is_spacia=True,
                        raw_text=poison_raw,
                    ),
                    ts=1100.0,
                )
                t_clean = db.get_spy_target_by_coords("02:379:02")
                self.assertIsNotNone(t_clean)
                assert t_clean is not None
                self.assertEqual(t_clean["status_color"], "free")
                self.assertFalse(t_clean["is_spacia"])
                self.assertEqual(t_clean["resources"]["pig_iron"], 0)
                self.assertFalse(t_clean["has_spy_intel"])

                # 3. Simulate legacy poisoned DB state directly in SQLite (as occurred before the parser fix)
                run_pg_exec(
                    db,
                    """
                    UPDATE spy_targets
                    SET owner = 'Spacia', planet_name = 'Colony', status_color = 'yellow',
                        is_inactive = 1, is_spacia = 1,
                        pig_iron = 120668, metal = 135782, kryptonite = 614098, spice = 524983,
                        last_scanned_ts = 1200.0, last_spied_ts = 1200.0,
                        blocked_until_ts = 9999.0,
                        block_reason = 'Target cannot be attacked on internalSendFleet (ATTACK mission unavailable)'
                    WHERE coords = '02:379:02'
                    """,
                )
                run_pg_exec(
                    db,
                    """
                    INSERT INTO spy_reports(
                        ts, target_coords, owner, planet_name, is_spacia,
                        pig_iron, metal, kryptonite, spice, loot_spice_eq,
                        buildings_json, fleet_json, defense_json, researches_json,
                        dug_in, uninhabited_fields, server_msg_id, msg_date_text, raw_text
                    ) VALUES (1200.0, '02:379:02', 'Spacia', 'Colony', 1,
                              120668, 135782, 614098, 524983, 804106.5,
                              '{}', '{}', '{}', '{}', 0, NULL, '7881', '2026-11-17 13:04:40', ?)
                    """,
                    (poison_raw,),
                )
            finally:
                db.close()

            # 4. Reopening GameDatabase triggers _repair_spy_targets_locked -> non-destructively restores free slot!
            db2 = GameDatabase(db_path)
            try:
                repaired = db2.get_spy_target_by_coords("02:379:02")
                self.assertIsNotNone(repaired)
                assert repaired is not None
                self.assertEqual(repaired["status_color"], "free")
                self.assertEqual(repaired["owner"], "")
                self.assertEqual(repaired["planet_name"], "")
                self.assertFalse(repaired["is_spacia"])
                self.assertFalse(repaired["has_spy_intel"])
                self.assertEqual(repaired["resources"], {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0})
                self.assertEqual(repaired["block_reason"], "")
                self.assertEqual(repaired["blocked_until_ts"], 0.0)
                # Append-only spy_reports row remains in DB but is excluded from get_spy_reports API
                self.assertEqual(db2.get_spy_reports(coords="02:379:02"), [])
            finally:
                db2.close()

    def test_v013_config_optimizer_ai_planner_reserved_floors_and_field_conflicts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_v13_store_") as td:
            db = GameDatabase(Path(td) / "v13_store.db")
            try:
                # 1. Reserved building floor can be updated before any optimizer run without faking active result
                self.assertIsNone(db.get_active_config_optimizer())
                self.assertEqual(db.get_reserved_building_floors(), {})

                floors1 = db.update_reserved_building_floor(
                    "12476",
                    "6",
                    8,
                    reason="Keep Thermosolar plant L8",
                )
                self.assertEqual(floors1, {"12476": {"b6": 8}})
                self.assertEqual(db.get_reserved_building_floors(), {"12476": {"b6": 8}})
                self.assertIsNone(db.get_active_config_optimizer())

                # 2. Record Config Optimizer run (preserves existing reserved floors, sanitizes NaN & sid)
                cfg_res1 = {
                    "schema_version": 1,
                    "meta_parameters": {"target_ship_key": "s16", "bad_nan": float("nan")},
                    "summary": {
                        "ships_per_month": 42.5,
                        "limiting_factor": "kryptonite",
                        "note": "sid=SECRET_OPT_TOKEN_1234",
                    },
                    "planets": [{"planet_id": 12476, "coords": "02:380:12"}],
                }
                rid1 = db.record_config_optimizer_run(
                    cfg_res1,
                    params={"target_ship_key": "s16", "ga_population_size": 100},
                    trigger_source="manual",
                    elapsed_ms=125.4,
                    set_active=True,
                )
                self.assertGreater(rid1, 0)

                active_cfg = db.get_active_config_optimizer()
                self.assertIsNotNone(active_cfg)
                assert active_cfg is not None
                self.assertEqual(active_cfg["run_id"], rid1)
                self.assertEqual(active_cfg["reserved_floors"], {"12476": {"b6": 8}})
                self.assertEqual(active_cfg["result"]["summary"]["ships_per_month"], 42.5)
                dumped_cfg = json.dumps(active_cfg, allow_nan=False)
                self.assertNotIn("SECRET_OPT_TOKEN_1234", dumped_cfg)

                # Second run with set_active=False appends history without replacing active
                rid2 = db.record_config_optimizer_run(
                    {
                        "summary": {"ships_per_month": 55.0, "limiting_factor": "spice"},
                        "meta_parameters": {"target_ship_key": "s15"},
                    },
                    reserved_floors={"12476": {"b6": 8, "b11": 12}},
                    trigger_source="preview",
                    elapsed_ms=80.0,
                    set_active=False,
                )
                self.assertGreater(rid2, rid1)
                self.assertEqual(db.get_active_config_optimizer()["run_id"], rid1)  # type: ignore[index]

                cfg_runs = db.list_config_optimizer_runs(limit=10)
                self.assertEqual([r["id"] for r in cfg_runs], [rid2, rid1])
                self.assertEqual(cfg_runs[0]["target_ship_key"], "s15")
                self.assertEqual(cfg_runs[0]["ships_per_month"], 55.0)
                self.assertEqual(cfg_runs[0]["limiting_factor"], "spice")
                self.assertEqual(cfg_runs[0]["reserved_floors"], {"12476": {"b6": 8, "b11": 12}})

                # Updating & clearing reserved floors preserves active result/run_id
                floors2 = db.update_reserved_building_floor("02:380:15", "b14", 9)
                self.assertEqual(floors2, {"12476": {"b6": 8}, "02:380:15": {"b14": 9}})
                floors3 = db.update_reserved_building_floor("12476", "b6", 0)
                self.assertEqual(floors3, {"02:380:15": {"b14": 9}})
                self.assertEqual(db.get_active_config_optimizer()["run_id"], rid1)  # type: ignore[index]

                # Per-planet specialization locks persist in params_json without disturbing run_id / floors / result
                self.assertEqual(db.get_config_optimizer_specializations(), {})
                specs1 = db.update_config_optimizer_specializations(
                    {"12476": "kryptonite", "02:380:15": "spice+shipyard"},
                    replace=False,
                )
                self.assertEqual(specs1, {"12476": "kryptonite", "02:380:15": "spice_shipyard"})
                self.assertEqual(db.get_config_optimizer_specializations(), specs1)
                self.assertEqual(db.get_active_config_optimizer()["run_id"], rid1)  # type: ignore[index]
                self.assertEqual(db.get_reserved_building_floors(), {"02:380:15": {"b14": 9}})
                specs2 = db.update_config_optimizer_specializations({"12476": "auto"}, replace=False)
                self.assertEqual(specs2, {"02:380:15": "spice_shipyard"})
                specs3 = db.update_config_optimizer_specializations({"12476": "metal"}, replace=True)
                self.assertEqual(specs3, {"12476": "metal"})
                self.assertEqual(db.get_active_config_optimizer()["result"]["summary"]["ships_per_month"], 42.5)  # type: ignore[index]

                # 3. Record AI-Planner runs (active + history + config_run_id linkage + finite JSON)
                self.assertIsNone(db.get_active_ai_planner())
                ai_res1 = {
                    "meta_parameters": {"reward_weeks": 26, "weekly_discount": 0.9},
                    "summary": {
                        "discounted_reward_spice_eq": 123456.75,
                        "total_net_spice_eq": 456789.5,
                        "inf_guard": float("inf"),
                    },
                    "steps": [
                        {"seq": 1, "kind": "BUILD", "planet_id": 12476, "entity_key": "b1", "target_level": 13},
                        {"seq": 2, "kind": "RESEARCH", "planet_id": 0, "entity_key": "r1", "target_level": 5},
                    ],
                }
                ai_id1 = db.record_ai_planner_run(
                    ai_res1,
                    config_run_id=rid1,
                    trigger_source="auto_replan",
                    elapsed_ms=340.2,
                    set_active=True,
                )
                self.assertGreater(ai_id1, 0)

                ai_id2 = db.record_ai_planner_run(
                    {
                        "meta_parameters": {"reward_weeks": 12, "weekly_discount": 0.85},
                        "summary": {"discounted_reward_spice_eq": 50000.0, "total_net_spice_eq": 90000.0},
                        "steps": [
                            {"seq": 1, "kind": "BUILD", "planet_id": 12476, "entity_key": "b2", "target_level": 12}
                        ],
                    },
                    config_run_id=rid2,
                    trigger_source="preview",
                    elapsed_ms=95.0,
                    set_active=False,
                )
                self.assertGreater(ai_id2, ai_id1)

                active_ai = db.get_active_ai_planner()
                self.assertIsNotNone(active_ai)
                assert active_ai is not None
                self.assertEqual(active_ai["run_id"], ai_id1)
                self.assertEqual(active_ai["config_run_id"], rid1)
                self.assertEqual(active_ai["summary"]["discounted_reward_spice_eq"], 123456.75)
                self.assertEqual(len(active_ai["result"]["steps"]), 2)
                json.dumps(active_ai, allow_nan=False)

                ai_runs = db.list_ai_planner_runs(limit=10)
                self.assertEqual([r["id"] for r in ai_runs], [ai_id2, ai_id1])
                self.assertEqual(ai_runs[0]["reward_weeks"], 12)
                self.assertEqual(ai_runs[0]["actions_count"], 1)
                self.assertEqual(ai_runs[1]["config_run_id"], rid1)
                self.assertEqual(ai_runs[1]["actions_count"], 2)

                # 4. Field conflict prompts (upsert, candidates fallback, resolve, append-only events, idempotency)
                fc1 = db.upsert_field_conflict_prompt(
                    {
                        "conflict_key": "12476:b1:25",
                        "planet_id": 12476,
                        "planet_coords": "2:380:12",
                        "planet_name": "Homeworld",
                        "blocked_building_key": "b1",
                        "blocked_building_name": "Pig iron mine",
                        "blocked_target_level": 25,
                        "fields_used": 230,
                        "fields_max": 230,
                        "candidates": [
                            {"building_key": "b6", "name": "Thermosolar plant", "level": 6},
                            {"building_key": "b11", "name": "Pig iron warehouse", "level": 10},
                        ],
                    }
                )
                self.assertEqual(fc1["conflict_key"], "12476:b1:25")
                self.assertEqual(fc1["planet_coords"], "02:380:12")
                self.assertEqual(fc1["candidate_building_key"], "b6")
                self.assertEqual(fc1["candidate_level"], 6)
                self.assertEqual(fc1["status"], "pending")
                self.assertIsNone(fc1["resolved_ts"])
                self.assertEqual(len(db.list_field_conflict_prompts(status="pending")), 1)
                self.assertEqual(len(db.list_field_conflict_prompts(status="resolved")), 0)

                # Resolve with keep_reserve -> persists decision & resolved_ts & appends event
                resolved = db.resolve_field_conflict_prompt(
                    "12476:b1:25",
                    "keep_reserve",
                    detail={"reserved_level": 6, "triggered_replan": True},
                )
                self.assertIsNotNone(resolved)
                assert resolved is not None
                self.assertEqual(resolved["status"], "resolved")
                self.assertEqual(resolved["decision"], "keep_reserve")
                self.assertIsNotNone(resolved["resolved_ts"])
                self.assertTrue(resolved["detail"]["triggered_replan"])
                self.assertEqual(len(db.list_field_conflict_prompts(status="pending")), 0)
                self.assertEqual(len(db.list_field_conflict_prompts(status="resolved")), 1)
                self.assertEqual(len(db.list_field_conflict_prompts(status=None)), 1)

                # Subsequent upsert on an already-resolved conflict_key must NOT reopen it to pending
                fc1_again = db.upsert_field_conflict_prompt(
                    {
                        "conflict_key": "12476:b1:25",
                        "planet_id": 12476,
                        "planet_coords": "02:380:12",
                        "blocked_building_key": "b1",
                        "blocked_target_level": 25,
                        "candidate_building_key": "b6",
                        "candidate_level": 6,
                    }
                )
                self.assertEqual(fc1_again["status"], "resolved")
                self.assertEqual(fc1_again["decision"], "keep_reserve")

                # Missing conflict_key returns None
                self.assertIsNone(db.resolve_field_conflict_prompt("nonexistent:key", "demolish"))

                summ = db.summary()
                self.assertEqual(summ["counts"]["config_optimizer_active"], 1)
                self.assertEqual(summ["counts"]["config_optimizer_runs"], 2)
                self.assertEqual(summ["counts"]["ai_planner_active"], 1)
                self.assertEqual(summ["counts"]["ai_planner_runs"], 2)
                self.assertEqual(summ["counts"]["field_conflict_prompts"], 1)
                self.assertGreaterEqual(summ["counts"]["field_conflict_events"], 2)
            finally:
                db.close()

    def test_debris_amounts_proximity_ranking_and_non_destructive_recycle_filter(self) -> None:
        from spaceinvasion.parsing.models import (
            GalaxySlot,
            InboxMessage,
        )

        with tempfile.TemporaryDirectory(prefix="si_deb_store_") as tmp:
            db = GameDatabase(Path(tmp) / "spaceinvasion.db")
            try:
                db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="06:180:13",
                            position=13,
                            name="Spacia Planet",
                            owner="Spacia",
                            status_color="yellow",
                            has_debris=True,
                            is_spacia=True,
                            debris_pig_iron=10_000,
                            debris_metal=5_000,
                        ),
                        GalaxySlot(
                            coords="02:379:01",
                            position=1,
                            name="",
                            owner="",
                            status_color="free",
                            has_debris=True,
                            is_spacia=False,
                            debris_pig_iron=10_720,
                            debris_metal=10_574,
                            debris_kryptonite=15_560,
                            debris_spice=11_584,
                        ),
                    ],
                    ts=1000.0,
                )
                active_deb = db.get_active_debris_targets(origin_coords=("02:380:12",), limit=10)
                self.assertEqual([t["coords"] for t in active_deb], ["02:379:01", "06:180:13"])
                self.assertEqual(active_deb[0]["debris_total"], 48_438)
                self.assertEqual(active_deb[0]["debris_kryptonite"], 15_560)
                self.assertEqual(active_deb[0]["debris_spice"], 11_584)

                # Clearing debris prunes galaxy_debris_amounts and removes target from active debris
                db.set_target_debris("02:379:01", False)
                active_after = db.get_active_debris_targets(origin_coords=("02:380:12",), limit=10)
                self.assertEqual([t["coords"] for t in active_after], ["06:180:13"])

                # Legacy misclassified STATION notice in recycle_reports is filtered non-destructively
                station_legacy = InboxMessage(
                    msg_uid="id:legacy_st_1",
                    server_msg_id="legacy_st_1",
                    folder_type="inbox",
                    category="recycle_report",
                    msg_date_text="2026-11-17 13:50:00",
                    sender="System",
                    subject="Colony [02:380:06] station",
                    coords="02:380:06",
                    body_text="Colony [02:380:06] station One of your fleets (6 Recycler) was re-stationed from Colony [02:379:06] to Colony [02:380:06]. It delivers 0 Pig iron, 0 Metal, 0 Kryptonite, 0 Spice.",
                    parsed_data={
                        "recycler_count": 6,
                        "harvested": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                    },
                )
                genuine_rec = InboxMessage(
                    msg_uid="id:real_rec_1",
                    server_msg_id="real_rec_1",
                    folder_type="inbox",
                    category="recycle_report",
                    msg_date_text="2026-11-17 13:56:00",
                    sender="System",
                    subject="Space rubble [02:379:13] recycling report",
                    coords="02:379:13",
                    body_text="Space rubble [02:379:13] recycling report Your fleet (10 Recycler) processed 10000 Pig iron, 5000 Metal pieces of rubble there.",
                    parsed_data={
                        "recycler_count": 10,
                        "harvested": {"pig_iron": 10000, "metal": 5000, "kryptonite": 0, "spice": 0},
                    },
                )
                db.record_game_messages([station_legacy, genuine_rec], ts=1050.0)
                rec_rows = db.get_recycle_reports(limit=20)
                self.assertEqual([r["msg_uid"] for r in rec_rows], ["id:real_rec_1"])
                # Append-only guarantee: raw row remains in PostgreSQL table untouched
                raw_cnt = int(run_pg_query(db, "SELECT COUNT(*) AS cnt FROM recycle_reports")[0]["cnt"])
                self.assertEqual(raw_cnt, 2)
            finally:
                db.close()

    def test_custom_planet_names_transport_dispatches_v014_telemetry_and_max_galaxies(self) -> None:
        from spaceinvasion.parsing.models import (
            GalaxySlot,
            InboxMessage,
        )
        from spaceinvasion.core.model import PlannedAction

        base_hr = 1_760_000_000 // 3600 * 3600
        with tempfile.TemporaryDirectory(prefix="si_store_v14_") as tmp:
            db = GameDatabase(Path(tmp) / "store_v14.db")
            try:
                # 1. Custom planet names: sanitization, 48-char cap, sid redaction, clear & batch
                from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway

                self.assertEqual(
                    RuntimeDatabaseGateway.sanitize_custom_planet_name("  Capital\x07  Prime  "),
                    "Capital Prime",
                )
                self.assertEqual(len(RuntimeDatabaseGateway.sanitize_custom_planet_name("A" * 80)), 48)
                self.assertNotIn(
                    "supersecret",
                    RuntimeDatabaseGateway.sanitize_custom_planet_name("Home sid=supersecret123"),
                )

                names1 = db.set_custom_planet_name(101, "Forge World")
                self.assertEqual(names1, {"101": "Forge World"})
                self.assertEqual(db.get_custom_planet_names(), {"101": "Forge World"})
                names2 = db.set_custom_planet_names({102: "Spice Hub", 101: ""})
                self.assertEqual(names2, {"102": "Spice Hub"})
                self.assertNotIn("101", db.get_custom_planet_names())

                # 2. Recent transport dispatches ring + dedupe + action_history fallback
                db.record_transport_dispatch(
                    origin_coords="2:380:12",
                    dest_coords="2:380:08",
                    cargo=Resources(25000, 15000, 5000, 1000),
                    ships="5 Large transport",
                    source="dispatch",
                    ts=float(base_hr + 100),
                )
                # Duplicate within 5s window is collapsed
                db.record_transport_dispatch(
                    origin_coords="02:380:12",
                    dest_coords="02:380:08",
                    cargo={"pig_iron": 25000, "metal": 15000, "kryptonite": 5000, "spice": 1000},
                    ships="5 Large transport",
                    source="dispatch",
                    ts=float(base_hr + 102),
                )
                dispatches = db.get_recent_transport_dispatches(max_age_seconds=86400.0, now_ts=float(base_hr + 300))
                self.assertEqual(len(dispatches), 1)
                self.assertEqual(dispatches[0]["origin_coords"], "02:380:12")
                self.assertEqual(dispatches[0]["dest_coords"], "02:380:08")
                self.assertEqual(dispatches[0]["cargo"]["pig_iron"], 25000)

                # action_history fallback for legacy dispatches
                tr_act = PlannedAction(
                    priority="P1",
                    kind="TRANSPORT",
                    planet_id=101,
                    planet_coords="02:380:12",
                    target_name="Colony [02:380:09]",
                    cost=Resources(10000, 8000, 2000, 0),
                    reason="Deliver metal",
                    executable_now=True,
                )
                db.record_action(
                    source="api",
                    command="transport",
                    action=tr_act,
                    executed=True,
                    dry_run=False,
                    message="Dispatched 2 Large transport to [02:380:09] (10,000P/8,000M/2,000K)",
                    ts=float(base_hr + 120),
                )
                fb_rows = db.get_recent_transport_actions_fallback(max_age_seconds=86400.0, now_ts=float(base_hr + 300))
                self.assertEqual(len(fb_rows), 1)
                self.assertEqual(fb_rows[0]["dest_coords"], "02:380:09")
                self.assertEqual(fb_rows[0]["cargo"]["metal"], 8000)

                # 3. Galaxy debris amounts map & clearing
                db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:381:05",
                            position=5,
                            name="Farm",
                            owner="Idle",
                            status_color="yellow",
                            has_debris=True,
                            debris_pig_iron=8000,
                            debris_metal=4000,
                            debris_kryptonite=2000,
                            debris_spice=1000,
                        )
                    ],
                    ts=float(base_hr + 150),
                )
                deb_map = db.get_galaxy_debris_amounts()
                self.assertEqual(deb_map["02:381:05"]["pig_iron"], 8000)
                db.set_target_debris("02:381:05", False)
                self.assertNotIn("02:381:05", db.get_galaxy_debris_amounts())

                # 4. V014 auto-refresh of loot_recycle_hourly on message ingest (24h zero-filled)
                db.record_game_messages(
                    [
                        InboxMessage(
                            msg_uid="id:st_atk_h1",
                            server_msg_id="st_atk_h1",
                            folder_type="attack",
                            category="battle_report",
                            msg_date_text="2026-11-18 11:05:00",
                            sender="System",
                            subject="Battle report [02:380:11]",
                            coords="02:380:11",
                            body_text="You won the battle and stole 5000 Pig iron, 2500 Metal.",
                            parsed_data={
                                "outcome": "win",
                                "loot": {"pig_iron": 5000, "metal": 2500, "kryptonite": 0, "spice": 0},
                            },
                        ),
                        InboxMessage(
                            msg_uid="id:st_rec_h2",
                            server_msg_id="st_rec_h2",
                            folder_type="inbox",
                            category="recycle_report",
                            msg_date_text="2026-11-18 12:15:00",
                            sender="System",
                            subject="Space rubble [02:380:12] recycling report",
                            coords="02:380:12",
                            body_text="Your fleet (8 Recycler) processed 3000 Kryptonite, 1000 Spice pieces of rubble there.",
                            parsed_data={
                                "recycler_count": 8,
                                "harvested": {"pig_iron": 0, "metal": 0, "kryptonite": 3000, "spice": 1000},
                            },
                        ),
                    ],
                    ts=float(base_hr + 3600 + 300),
                )
                lr = db.get_loot_recycle_hourly(hours=24, now_ts=float(base_hr + 3600 + 600))
                self.assertEqual(len(lr["history"]), 24)
                self.assertEqual(lr["summary"]["total_stolen"], 7500.0)
                self.assertEqual(lr["summary"]["total_recycled"], 4000.0)
                self.assertEqual(lr["summary"]["grand_total"], 11500.0)
                self.assertEqual(lr["summary"]["attack_count"], 1)
                self.assertEqual(lr["summary"]["recycle_count"], 1)

                # 5. Dynamic max_galaxies filtering in get_galaxy_sector_intel (#103)
                db.record_galaxy_scan(2, 100, [], ts=float(base_hr + 100))
                db.record_galaxy_scan(9, 10, [], ts=float(base_hr + 105))
                intel_8 = db.get_galaxy_sector_intel(max_galaxies=8, now_ts=float(base_hr + 200))
                self.assertEqual(intel_8["universe_total_systems"], 3200)
                self.assertEqual(intel_8["universe_explored_systems"], 1)
                self.assertIn("02:100", intel_8["sectors"])
                self.assertNotIn("09:010", intel_8["sectors"])
                intel_14 = db.get_galaxy_sector_intel(max_galaxies=14, now_ts=float(base_hr + 200))
                self.assertEqual(intel_14["universe_total_systems"], 5600)
                self.assertEqual(intel_14["universe_explored_systems"], 2)
            finally:
                db.close()

    def test_hot_read_caches_telemetry_toggle_blocked_expiry_and_cte_snapshot_parity(self) -> None:
        import time
        from spaceinvasion.parsing.models import GalaxySlot

        db = GameDatabase(":memory:")
        try:
            now = time.time()
            # Seed multiple planets across multiple snapshots to verify ROW_NUMBER() CTE first/latest parity
            for step_i, offset_s in enumerate((300, 200, 100)):
                p1 = PlanetState(
                    planet_id=101,
                    name="Alpha",
                    coords="02:380:01",
                    planet_type="normal",
                    temperature=20,
                    fields_used=40,
                    fields_max=200,
                    resources=Resources(1000 * (step_i + 1), 500, 200, 100),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(500, 300),
                    buildings={"b1": 5 + step_i, "b2": 3},
                )
                p2 = PlanetState(
                    planet_id=102,
                    name="Beta",
                    coords="02:380:02",
                    planet_type="normal",
                    temperature=25,
                    fields_used=35,
                    fields_max=210,
                    resources=Resources(2000 * (step_i + 1), 600, 300, 150),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(500, 300),
                    buildings={"b1": 4 + step_i, "b2": 2},
                )
                st = GameState(
                    universe_id=31,
                    user_id=1,
                    nickname="Tester",
                    timestamp=now - offset_s,
                    active_planet_id=101,
                    planets=(p1, p2),
                )
                db.record_state_snapshot(st)

            # Record galaxy slots + debris + bash block
            db.record_galaxy_slots(
                [
                    GalaxySlot(
                        position=9,
                        coords="02:380:09",
                        owner="InactiveA",
                        name="RichA",
                        status_color="yellow",
                        has_debris=True,
                        is_spacia=False,
                    ),
                    GalaxySlot(
                        position=10,
                        coords="02:380:10",
                        owner="InactiveB",
                        name="RichB",
                        status_color="yellow",
                        has_debris=False,
                        is_spacia=False,
                    ),
                ],
                ts=now - 50,
            )
            db.record_target_block(
                "02:380:09",
                blocked_until_ts=now + 120.0,
                reason="bash test",
                is_bash=True,
                ts=now - 10,
            )
            db.record_raid(
                target_coords="02:380:09",
                outcome="win",
                loot={"pig_iron": 4000, "metal": 2000, "kryptonite": 500, "spice": 100},
                attacked_ts=now - 40,
            )

            # 1. include_telemetry_summary=True vs False + warm cache hit isolation
            rs_full_1 = db.get_raid_stats()
            rs_full_2 = db.get_raid_stats()
            self.assertEqual(rs_full_1["overall"]["attempts"], 1)
            self.assertIn("02:380:09", rs_full_1["blocked_targets"])
            self.assertEqual(rs_full_1["telemetry_summary"]["planet_snapshots_count"], 6)
            self.assertEqual(
                rs_full_1["telemetry_summary"]["planet_snapshots"]["trend"]["building_levels_delta"],
                4,
            )
            self.assertEqual(rs_full_1["telemetry_summary"]["spy_targets_count"], 2)
            # Mutating returned dict must not poison cache
            rs_full_1["overall"]["attempts"] = 999
            self.assertEqual(rs_full_2["overall"]["attempts"], 1)
            self.assertEqual(db.get_raid_stats()["overall"]["attempts"], 1)

            rs_light = db.get_raid_stats(include_telemetry_summary=False)
            self.assertEqual(rs_light["telemetry_summary"], {})
            self.assertEqual(rs_light["overall"]["attempts"], 1)
            self.assertIn("02:380:09", rs_light["per_target"])
            self.assertIn("02:380:09", rs_light["blocked_targets"])

            # 2. Cached spy_targets & active_debris_targets invalidate on DB write
            sp1 = db.get_spy_targets(limit=50)
            sp2 = db.get_spy_targets(limit=50)
            self.assertEqual(len(sp1), 2)
            self.assertEqual(sp1, sp2)
            deb1 = db.get_active_debris_targets(origin_coords=["02:380:01"], limit=20)
            deb2 = db.get_active_debris_targets(origin_coords=["02:380:01"], limit=20)
            self.assertEqual(len(deb1), 1)
            self.assertEqual(deb1[0]["coords"], "02:380:09")
            self.assertEqual(deb1, deb2)

            # Insert new debris slot -> version bump invalidates caches immediately
            db.record_galaxy_slots(
                [
                    GalaxySlot(
                        position=11,
                        coords="02:380:11",
                        owner="InactiveC",
                        name="RichC",
                        status_color="yellow",
                        has_debris=True,
                        is_spacia=False,
                    )
                ],
                ts=now - 5,
            )
            self.assertEqual(len(db.get_spy_targets(limit=50)), 3)
            self.assertEqual(len(db.get_active_debris_targets(origin_coords=["02:380:01"], limit=20)), 2)

            # 3. Blocked target expires dynamically on warm cache hit as wall clock advances
            db.record_target_block(
                "02:380:10",
                blocked_until_ts=now + 2.0,
                reason="short block",
                is_bash=True,
                ts=now - 1.5,
            )
            rs_before = db.get_raid_stats(now=now, include_telemetry_summary=False)
            self.assertIn("02:380:10", rs_before["blocked_targets"])
            rs_after = db.get_raid_stats(now=now + 10.0, include_telemetry_summary=False)
            self.assertNotIn("02:380:10", rs_after["blocked_targets"])
            self.assertIn("02:380:09", rs_after["blocked_targets"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
