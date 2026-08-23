"""Unit tests for SQLite `GameDatabase` persistence layer."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from spaceinvasion.fleet import SpyReport
from spaceinvasion.state import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
    TutorialStatus,
    TutorialTask,
)
from spaceinvasion.storage import GameDatabase
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

                # Spy report + Raid BashTracker persistence
                rep = SpyReport(
                    target_coords="02:380:05",
                    owner="YellowPlayer",
                    resources=Resources(5000, 4000, 3000, 1000),
                    fleet={},
                    defense={},
                    is_spacia=False,
                )
                db.record_spy_report(rep, points=25, is_inactive=True, ts=2200.0)
                db.record_raid("02:380:05", attacked_ts=2250.0, is_exempt=False, outcome="WIN")
                tracker = db.load_bash_tracker(now=2300.0)
                self.assertEqual(tracker.attacks_last_24h("02:380:05", timestamp=2300.0), 1)

                summary = db.summary()
                self.assertGreaterEqual(summary["counts"]["planet_snapshots"], 2)
                self.assertEqual(summary["counts"]["spy_targets"], 1)
                self.assertEqual(summary["counts"]["raid_history"], 1)
                p_hist = db.get_planet_history(12476)
                self.assertEqual(len(p_hist), 2)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
