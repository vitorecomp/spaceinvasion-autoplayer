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
        repo_hist = Path(__file__).resolve().parents[1] / DEFAULT_ATTACK_HISTORY_PATH
        before_bytes = repo_hist.read_bytes() if repo_hist.exists() else None

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_tmp = GameDatabase(tmp_path / "spaceinvasion.db")
            try:
                self.assertEqual(db_tmp.attack_history_path, tmp_path / "attack_history.json")
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

        after_bytes = repo_hist.read_bytes() if repo_hist.exists() else None
        self.assertEqual(after_bytes, before_bytes)

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

            mirror = tmp_path / "attack_history.json"
            self.assertTrue(mirror.exists())
            self.assertEqual(list(tmp_path.glob("*.tmp")), [])
            raw = mirror.read_text(encoding="utf-8")
            self.assertNotIn("SID_TEST_ALPHA_999", raw)
            parsed = json.loads(raw)
            for k in ("version", "updated_at", "overall", "per_target", "history", "telemetry_summary"):
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


if __name__ == "__main__":
    unittest.main()
