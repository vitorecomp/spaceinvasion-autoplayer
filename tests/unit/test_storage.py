"""Unit tests for SQLite `GameDatabase` persistence layer."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from spaceinvasion.fleet import GalaxySlot, SpyReport
from spaceinvasion.state import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
    TutorialStatus,
    TutorialTask,
)
import spaceinvasion.storage as storage_mod
from spaceinvasion.storage import DEFAULT_ATTACK_HISTORY_PATH, GameDatabase
from spaceinvasion.strategy import PlannedAction


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
            legacy_json = tmp_path / "rank_history.json"
            legacy_json.write_text(
                json.dumps(
                    [
                        {
                            "ts": 500.0,
                            "server_points": 3000,
                            "server_rank": 290,
                            "estimated_points": 2950.0,
                            "pts_per_hr": 20.0,
                            "spice_value": 90000.0,
                            "planets": 1,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            db = GameDatabase(tmp_path / "spaceinvasion.db", legacy_rank_json=legacy_json)
            try:
                # Migrated legacy sample + new snapshot
                samples = db.record_state_snapshot(_sample_db_state(ts=1000.0, step=6, b1=12, b2_rate=0))
                self.assertEqual(len(samples), 2)
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
                p_hist = db.get_planet_history(12476)
                self.assertEqual(len(p_hist), 2)
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
                db.record_action(source="api", command="build", action=act_ok, executed=True, dry_run=False, message="sid=SECRET_SID_99887766", ts=2100.0)
                db.record_action(source="api", command="build", action=act_ok, executed=False, dry_run=False, message="Aborted sid=deadbeef1234", ts=2110.0)
                db.record_action(source="api", command="build", action=act_ok, executed=False, dry_run=True, message="Dry", ts=2120.0)

                db.record_raid(
                    "02:380:04",
                    attacked_ts=2200.0,
                    is_exempt=True,
                    outcome="Victory url?sid=SECRET_SID_99887766&next=sid=deadbeef1234",
                    loot={"pig_iron": 3000, "metal": 1000, "kryptonite": 500, "spice": 100},
                )

                summary = db.get_full_history_summary()
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
                leaked = [s for s in re.findall(r"sid=([A-Za-z0-9_-]{4,})", dumped, flags=re.I) if s.upper() != "REDACTED"]
                self.assertEqual(leaked, [])
            finally:
                db.close()

    def test_attack_history_path_isolation_memory_and_custom_override(self) -> None:
        repo_hist = Path(__file__).resolve().parents[2] / "data" / "attack_history.json"
        self.assertFalse(repo_hist.exists(), "Legacy data/attack_history.json should be retired into SQLite")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_tmp = GameDatabase(tmp_path / "spaceinvasion.db")
            try:
                self.assertIsNone(db_tmp.attack_history_path)
                self.assertFalse((tmp_path / "attack_history.json").exists())
            finally:
                db_tmp.close()

            custom_json = tmp_path / "custom" / "raids.json"
            db_custom = GameDatabase(tmp_path / "custom.db", attack_history_path=custom_json)
            try:
                db_custom.record_raid("02:380:04", attacked_ts=100.0, outcome="Victory", loot={"spice": 100})
                self.assertTrue(custom_json.exists())
            finally:
                db_custom.close()

            db_mem = GameDatabase(":memory:")
            try:
                self.assertIsNone(db_mem.attack_history_path)
                self.assertEqual(db_mem.summary()["db_path"], ":memory:")
                self.assertIsNone(db_mem.sync_attack_history_file())
            finally:
                db_mem.close()

            patched_db = tmp_path / "default_state" / "spaceinvasion.db"
            patched_hist = tmp_path / "default_data" / "attack_history.json"
            with (
                patch.object(storage_mod, "DEFAULT_DB_PATH", patched_db),
                patch.object(storage_mod, "DEFAULT_ATTACK_HISTORY_PATH", patched_hist),
            ):
                db_def = GameDatabase(patched_db)
                try:
                    self.assertEqual(db_def.attack_history_path, patched_hist)
                finally:
                    db_def.close()

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
            from spaceinvasion.optimizer import load_attack_history_stats

            parsed = load_attack_history_stats(db_path)
            raw = json.dumps(parsed, allow_nan=False)
            self.assertNotIn("SID_TEST_ALPHA_999", raw)
            for k in ("version", "overall", "per_target", "history", "telemetry_summary"):
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
        import sqlite3
        from unittest.mock import patch

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
                        self.assertTrue(db._is_memory)
                        self.assertIsNone(db.attack_history_path)
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

            # Corrupt cells & null JSON values in tables degrade safely
            db_c = GameDatabase(tmp_root / "corrupt.db", attack_history_path="")
            try:
                db_c._conn.execute(
                    "INSERT INTO economy_rates_cache(planet_id, coords, rates_json, updated_ts) VALUES(1, '01:001:01', 'null', 10.0)"
                )
                db_c._conn.execute(
                    "INSERT INTO spy_targets(coords, owner, fleet_json, defense_json, last_scanned_ts) VALUES('01:001:02', 'X', '{bad', 'null', 10.0)"
                )
                db_c._conn.execute(
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                    "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                    "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                    "economy_rates_json, researches_json) "
                    "VALUES(10.0, 31, 1, '01:001:01', 'P', 'normal', 20, 10, 200, 0, 0, 0, 0, 0, 0, "
                    "'[]', '{bad', 'null', 'null', '{bad')"
                )
                db_c._conn.execute(
                    "INSERT INTO tutorial_progress(step, total_steps, tasks_json, reward_text, can_complete, first_seen_ts, last_seen_ts) "
                    "VALUES(1, 5, '{bad', 'Reward', 0, 10.0, 10.0)"
                )
                db_c._conn.commit()
                self.assertEqual(db_c.get_all_economy_rates(), {})
                self.assertEqual(len(db_c.get_spy_targets()), 1)
                self.assertEqual(len(db_c.get_planet_history(planet_id=1)), 1)
                self.assertEqual(len(db_c.get_tutorial_history()), 1)
                self.assertIsInstance(db_c.get_full_history_summary(), dict)
            finally:
                db_c.close()

            # DDL error after connect closes _conn without leaking ResourceWarning
            real_conn = sqlite3.connect(":memory:")
            closed_flags: list[bool] = []

            class _ProxyConn:
                def __init__(self, conn: sqlite3.Connection) -> None:
                    object.__setattr__(self, "_c", conn)

                def __getattr__(self, name: str):
                    return getattr(object.__getattribute__(self, "_c"), name)

                def __setattr__(self, name: str, value) -> None:
                    setattr(object.__getattribute__(self, "_c"), name, value)

                def executescript(self, _sql: str):
                    raise sqlite3.OperationalError("boom")

                def close(self) -> None:
                    closed_flags.append(True)
                    object.__getattribute__(self, "_c").close()

            with patch("spaceinvasion.storage.sqlite3.connect", return_value=_ProxyConn(real_conn)):
                with self.assertRaises(sqlite3.OperationalError):
                    GameDatabase(tmp_root / "broken_ddl.db", attack_history_path="")
            self.assertEqual(closed_flags, [True])

    def test_corrupt_numeric_cells_in_readers_and_spy_report_coord_normalization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_stor_adv_") as tmp:
            db = GameDatabase(Path(tmp) / "adv.db", attack_history_path="")
            try:
                # 1. Corrupt non-numeric cells in rank_snapshots, galaxy_scans, action_history, planet_snapshots
                db._conn.execute(
                    "INSERT INTO rank_snapshots(ts, universe_id, nickname, server_points, server_rank, "
                    "estimated_points, pts_per_hr, spice_value, planets) "
                    "VALUES('bad_ts', 31, 'u', 'bad_pts', 'bad_rank', 'bad_est', 'bad_pph', 'bad_sp', 'bad_pl')"
                )
                db._conn.execute(
                    "INSERT INTO galaxy_scans(galaxy, system, last_scanned_ts) VALUES('bad_g', 10, 100.0)"
                )
                db._conn.execute(
                    "INSERT INTO galaxy_scans(galaxy, system, last_scanned_ts) VALUES(2, 380, 'bad_ts')"
                )
                db._conn.execute(
                    "INSERT INTO action_history(ts, source, command, kind, planet_id, target_id, executed, dry_run) "
                    "VALUES('bad_ts', 'api', 'throttle', 'THROTTLE_BUILDING', 'bad_pid', 2, 1, 0)"
                )
                db._conn.execute(
                    "INSERT INTO action_history(ts, source, command, kind, planet_id, target_id, executed, dry_run) "
                    "VALUES('bad_ts', 'api', 'throttle', 'THROTTLE_BUILDING', 101, 2, 1, 0)"
                )
                db._conn.execute(
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                    "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                    "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                    "economy_rates_json, researches_json) "
                    "VALUES('bad_ts', 31, 1, '01:001:01', 'P', 'normal', 20, 10, 200, 0, 0, 0, 0, 0, 0, "
                    "'{}', '{}', '{}', '{}', '{}')"
                )
                db._conn.commit()

                rk = db.get_rank_samples()
                self.assertEqual(len(rk), 1)
                self.assertEqual(rk[0]["ts"], 0.0)
                self.assertIsNone(rk[0]["server_points"])
                self.assertIsNone(rk[0]["server_rank"])

                scans = db.get_galaxy_scans()
                self.assertNotIn("bad_g", str(scans))
                self.assertEqual(scans.get("02:380"), 0.0)

                throttles = db.get_last_throttle_timestamps()
                self.assertEqual(throttles, {101: 0.0})

                full_sum = db.get_full_history_summary()
                self.assertEqual(full_sum["planet_snapshots"]["first_ts"], 0.0)
                self.assertEqual(full_sum["planet_snapshots"]["latest_ts"], 0.0)

                # 2. record_spy_report normalizes coords and merges legacy unnormalized row
                db._conn.execute(
                    "INSERT INTO spy_targets(coords, owner, status_color, is_inactive, last_scanned_ts) "
                    "VALUES('2:380:4', 'LegacyYellow', 'yellow', 1, 500.0)"
                )
                db._conn.commit()
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
                raw_rows = db._conn.execute("SELECT coords FROM spy_targets").fetchall()
                self.assertEqual([r[0] for r in raw_rows], ["02:380:04"])
            finally:
                db.close()

    def test_spy_target_direct_lookup_galaxy_coalescing_and_corrupt_snapshot_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "gap01_04.db")
            try:
                # Pre-existing legacy unnormalized row with detailed spy intel
                db._conn.execute(
                    "INSERT INTO spy_targets("
                    "coords, owner, status_color, is_inactive, pig_iron, metal, kryptonite, spice, last_spied_ts, last_scanned_ts"
                    ") VALUES('2:380:4', 'LegacyOwner', 'white', 0, 9999, 111, 0, 0, 800.0, 500.0)"
                )
                db._conn.commit()

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
                raw_rows = db._conn.execute("SELECT coords FROM spy_targets").fetchall()
                self.assertEqual([r[0] for r in raw_rows], ["02:380:04"])
                merged = db.get_spy_target_by_coords("2:380:4")
                self.assertIsNotNone(merged)
                self.assertEqual(merged["status_color"], "yellow")
                self.assertTrue(merged["is_inactive"])
                self.assertEqual(merged["owner"], "NewOwner")
                self.assertEqual(merged["resources"]["pig_iron"], 9999)
                self.assertEqual(merged["last_spied_ts"], 800.0)

                # Caveat: pre-existing corrupt non-numeric snapshot timestamps/points do not crash record_state_snapshot
                db._conn.execute(
                    "INSERT INTO rank_snapshots(ts, universe_id, estimated_points, pts_per_hr, spice_value, planets) "
                    "VALUES('bad_ts', 31, 'bad_pts', 0.0, 0.0, 1)"
                )
                db._conn.execute(
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                    "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                    "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                    "economy_rates_json, researches_json) "
                    "VALUES('bad_ts', 31, 12476, '02:380:12', 'Colony', 'normal', 31, 55, 230, 0, 0, 0, 0, 0, 0, "
                    "'{}', '{}', '{}', '{}', '{}')"
                )
                db._conn.commit()
                db.record_state_snapshot(_sample_db_state(ts=2000.0))
            finally:
                db.close()

    def test_blank_attack_history_path_disables_mirror_on_record_raid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for idx, blank in enumerate(("", "   ", Path(""))):
                db = GameDatabase(Path(tmp) / f"blank_{idx}.db", attack_history_path=blank)
                try:
                    self.assertIsNone(db.attack_history_path)
                    db.record_raid(
                        "02:380:04",
                        attacked_ts=1_700_000_000.0,
                        is_exempt=True,
                        outcome="Victory",
                        loot={"pig_iron": 10**5000, "spice": 100},
                    )
                    self.assertIsNone(db.sync_attack_history_file())
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
            db = GameDatabase(Path(tmp) / "r7_queries.db", attack_history_path=None)
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
        from spaceinvasion.storage import _coord_query_candidates

        ar = "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"
        norm, cands, like_pat = _coord_query_candidates(ar)
        self.assertEqual(norm, ar)
        self.assertEqual(cands, [ar])
        self.assertIsNone(like_pat)

        secret = "DIRECT_DB_SECRET_SID_42"
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r9_redact.db", attack_history_path="")
            try:
                db._conn.execute(
                    "INSERT INTO action_history(ts, source, command, priority, kind, planet_coords, target_name, executed, dry_run, message) "
                    "VALUES(1000.0, 'api', 'build', 'P2', 'BUILD', '01:001:01', ?, 1, 0, ?)",
                    (f"Mine ?sid={secret}", f"Done ?sid={secret}"),
                )
                db._conn.execute(
                    "INSERT INTO spy_targets(coords, owner, planet_name, status_color, last_scanned_ts) VALUES('02:380:04', ?, ?, 'yellow', 1000.0)",
                    (f"Owner ?sid={secret}", f"Planet ?sid={secret}"),
                )
                db._conn.execute(
                    "INSERT INTO tutorial_progress(step, total_steps, tasks_json, reward_text, can_complete, first_seen_ts, last_seen_ts) "
                    "VALUES(1, 5, ?, ?, 0, 1000.0, 1000.0)",
                    (json.dumps([{"description": f"Step ?sid={secret}"}], allow_nan=False), f"Reward ?sid={secret}"),
                )
                db._conn.commit()

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
        from spaceinvasion.storage import _sanitize_obj

        s1 = "BLOB_SECRET_11"
        s2 = "JSON_SID_22"
        pw = "PW_SECRET_33"
        san_obj = _sanitize_obj(
            {
                (10**5000, "x"): frozenset({b"?sid=" + s1.encode("utf-8")}),
                "sid": s2,
                "password": b"PW_SECRET_33",
                "path": Path(f"https://x/?sid={s1}"),
            }
        )
        san_dumped = json.dumps(san_obj, allow_nan=False)
        for sec in (s1, s2, pw):
            self.assertNotIn(sec, san_dumped)

        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "r10.db", attack_history_path="")
            try:
                blob_url = f"https://x/?sid={s1}".encode("utf-8")
                fleet_json = json.dumps({"sid": s2, "password": pw, "s4": 3}, allow_nan=False)
                huge_num_s = "1" + ("0" * 5000)
                db._conn.execute(
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, energy_produced, energy_used, buildings_json, ships_json, defense_json, economy_rates_json, researches_json) "
                    "VALUES('nan', 31, 42, ?, ?, 'normal', 20, 10, 200, ?, 1, 1, 1, 50, 10, ?, ?, '{}', ?, '{}')",
                    (
                        b"01:001:01",
                        blob_url,
                        huge_num_s,
                        fleet_json,
                        '{"b1": 100}',
                        json.dumps({"sid": s2, "resource1": 100}, allow_nan=False),
                    ),
                )
                db._conn.execute(
                    "INSERT INTO spy_targets(coords, owner, planet_name, status_color, points, pig_iron, fleet_json, defense_json, last_scanned_ts) "
                    "VALUES(?, ?, ?, 'yellow', ?, 'nan', ?, ?, 1000.0)",
                    (b"02:380:04", blob_url, blob_url, huge_num_s, fleet_json, fleet_json),
                )
                db._conn.execute(
                    "INSERT INTO rank_snapshots(ts, server_points, server_rank, estimated_points, pts_per_hr, spice_value, planets) "
                    "VALUES(1000.0, ?, 10, 'nan', 1.0, 2.0, ?)",
                    (huge_num_s, huge_num_s),
                )
                db._conn.execute(
                    "INSERT INTO economy_rates_cache(planet_id, coords, rates_json, updated_ts) VALUES(?, '01:001:01', ?, 1000.0)",
                    (42, json.dumps({f"?sid={s1}": 80, "resource1": 90}, allow_nan=False)),
                )
                db._conn.execute(
                    "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) VALUES(?, 1000.0, 0, ?, ?)",
                    (b"02:380:04", blob_url, fleet_json),
                )
                db._conn.commit()

                ph = db.get_planet_history(42)
                rs = db.get_rank_samples()
                er = db.get_all_economy_rates()
                st_list = db.get_spy_targets()
                st_one = db.get_spy_target_by_coords(b"2:380:4")  # type: ignore[arg-type]
                bash = db.load_bash_tracker(now=1000.0)
                full_sum = db.get_full_history_summary()
                tot_sum = db.summary()

                self.assertEqual(len(ph), 1)
                self.assertIsNotNone(st_one)
                self.assertEqual(bash.attacks_last_24h("02:380:04", timestamp=1000.0), 1)
                for label, payload in (
                    ("planet_history", ph),
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
        from spaceinvasion.storage import _is_win_outcome, _json_dumps, _sanitize_obj

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
            db = GameDatabase(Path(tmp) / "r11.db", attack_history_path="")
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
                row = db._conn.execute(
                    "SELECT planet_type FROM planet_snapshots WHERE planet_id = 77 ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(row["planet_type"], "asteroid")
                full_sum = db.get_full_history_summary()
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
        from spaceinvasion.storage import _json_dumps

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
            db = GameDatabase(Path(tmp) / "r12.db", attack_history_path="")
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
        from spaceinvasion.storage import _json_dumps, _raw_text, _sanitize_text

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
            db = GameDatabase(Path(tmp) / "r13.db", attack_history_path="")
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
        from spaceinvasion import storage as st_mod

        vectors = (
            '%22password%22%3A%22ST_UE_PW%22',
            '%22Bearer%20ST_UE_BR%22',
            '\\"password\\":\\"ST_ESC_PW\\"',
            '\\\\"password\\\\":\\\\"ST_DESC_PW\\\\"',
            'Bearer [MY ST SECRET]',
            'Bearer <MY ST ANG>',
            'Bearer "UNCLOSED_ST_Q',
            'Bearer ST_SCHEMELESS_BR',
            '?s\u0130d=ST_TR_I1&?sid=ST_REG_SID',
        )
        for v in vectors:
            red = st_mod._sanitize_text(v)
            for marker in ("ST_UE_PW", "ST_UE_BR", "ST_ESC_PW", "ST_DESC_PW", "MY ST SECRET", "MY ST ANG", "UNCLOSED_ST_Q", "ST_SCHEMELESS_BR", "ST_TR_I1", "ST_REG_SID"):
                self.assertNotIn(marker, red, f"Leaked {marker} in {red!r}")

        redos_s = 'password=' + ('\\' * 5000) + '"'
        t0 = time.perf_counter()
        _ = st_mod._sanitize_text(redos_s)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 250.0)

        cyc: list[object] = []
        cyc.append(cyc)
        self.assertEqual(st_mod._sanitize_obj(cyc), ["[Cycle]"])
        self.assertIsInstance(st_mod._raw_text(cyc), str)

        dag_exc: BaseException = RuntimeError("Bearer ST_DAG_SEC")
        for _ in range(20):
            dag_exc = RuntimeError(dag_exc, dag_exc, dag_exc, dag_exc)
        t1 = time.perf_counter()
        s_dag = st_mod._sanitize_text(st_mod._raw_text(dag_exc))
        _ = st_mod._stringify_keys({dag_exc: "ok"})
        self.assertNotIn("ST_DAG_SEC", s_dag)
        self.assertLess((time.perf_counter() - t1) * 1000.0, 50.0)

    def test_colony_attempts_append_only_logging_filtering_and_summary(self) -> None:
        now = 1_750_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "col_attempts.db", attack_history_path="")
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
        from spaceinvasion.combat.fleet import GalaxySlot, SpyReport
        from spaceinvasion.core.state import Resources

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
                db._conn.execute(
                    """
                    UPDATE spy_targets
                    SET owner = 'Spacia', planet_name = 'Colony', status_color = 'yellow',
                        is_inactive = 1, is_spacia = 1,
                        pig_iron = 120668, metal = 135782, kryptonite = 614098, spice = 524983,
                        last_scanned_ts = 1200.0, last_spied_ts = 1200.0,
                        blocked_until_ts = 9999.0,
                        block_reason = 'Target cannot be attacked on internalSendFleet (ATTACK mission unavailable)'
                    WHERE coords = '02:379:02'
                    """
                )
                db._conn.execute(
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
                db._conn.commit()
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


if __name__ == "__main__":
    unittest.main()
