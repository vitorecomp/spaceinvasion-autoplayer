"""Tests for ranking evolution estimator, Attack Zones, and snapshot trend tracking."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from spaceinvasion.ranking import (
    attack_zone_for_points,
    build_ranking_evolution,
    estimate_account_points,
    estimate_hourly_economy,
    record_rank_snapshot,
)
from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources


def _sample_ranking_state(*, net_deficit: bool = False) -> GameState:
    energy = EnergyBalance(produced=400, used=800) if net_deficit else EnergyBalance(produced=900, used=600)
    planet = PlanetState(
        planet_id=12476,
        name="Colony",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=55,
        fields_max=230,
        resources=Resources(1500, 7900, 7000, 2600),
        storage=Resources(100000, 100000, 100000, 100000),
        energy=energy,
        buildings={"b1": 11, "b2": 8, "b3": 11, "b4": 7, "b5": 13, "b7": 3},
        ships={"s1": 2},
    )
    return GameState(
        universe_id=31,
        user_id=103603,
        nickname="ventkx86",
        timestamp=1_700_000_000.0,
        planets=(planet,),
        active_planet_id=12476,
        researches={"r1": 2, "r2": 2},
        speedup_factor=20.0,
        account_points=46,
        account_rank=393,
    )


class TestRankingEvolution(unittest.TestCase):
    def test_attack_zone_classification_boundaries(self) -> None:
        self.assertEqual(attack_zone_for_points(0)["zone"], 1)
        self.assertEqual(attack_zone_for_points(999)["zone"], 1)
        self.assertEqual(attack_zone_for_points(1000)["zone"], 2)
        self.assertEqual(attack_zone_for_points(10_000)["zone"], 3)
        az20 = attack_zone_for_points(5_000_000_000)
        self.assertEqual(az20["zone"], 20)
        self.assertIsNone(az20["next_zone"])

    def test_points_and_hourly_economy_respect_energy_throttling(self) -> None:
        healthy = _sample_ranking_state(net_deficit=False)
        throttled = _sample_ranking_state(net_deficit=True)

        pts = estimate_account_points(healthy)
        self.assertGreater(pts["buildings_points"], 50.0)
        self.assertGreater(pts["research_points"], 0.0)
        self.assertGreater(pts["fleet_defense_points"], 0.0)
        self.assertGreater(pts["spice_value"], 0.0)

        econ_ok = estimate_hourly_economy(healthy)
        econ_low = estimate_hourly_economy(throttled)
        self.assertGreater(econ_ok["pts_per_hr_steady"], 20.0)
        self.assertAlmostEqual(
            econ_low["pts_per_hr_steady"], econ_ok["pts_per_hr_steady"] * 0.5, delta=0.5
        )

    def test_evolution_horizons_milestones_and_history_tracking(self) -> None:
        st = _sample_ranking_state()
        with tempfile.TemporaryDirectory() as tmp:
            hist_path = Path(tmp) / "state" / "rank_history.json"
            s1 = record_rank_snapshot(hist_path, st)
            self.assertEqual(len(s1), 1)
            # Duplicate unchanged snapshot within 5m is ignored
            s_dup = record_rank_snapshot(hist_path, st)
            self.assertEqual(len(s_dup), 1)

            st_later = GameState(
                universe_id=st.universe_id,
                user_id=st.user_id,
                nickname=st.nickname,
                timestamp=st.timestamp + 3600.0,
                planets=st.planets,
                active_planet_id=st.active_planet_id,
                researches=st.researches,
                speedup_factor=st.speedup_factor,
                account_points=80,
                account_rank=360,
            )
            samples = record_rank_snapshot(hist_path, st_later)
            self.assertEqual(len(samples), 2)

            evo = build_ranking_evolution(st_later, samples)
            self.assertEqual(len(evo["horizons"]), 5)
            h24 = next(h for h in evo["horizons"] if h["key"] == "24h")
            self.assertGreater(h24["compound_points"], h24["steady_points"])
            self.assertEqual(evo["observed_trend"]["sample_count"], 2)
            self.assertEqual(evo["observed_trend"]["delta_points"], 34.0)
            self.assertEqual(evo["observed_trend"]["rank_climbed"], 33)
            self.assertGreater(len(evo["milestones"]), 0)
            self.assertEqual(evo["milestones"][0]["label"], "AZ 2 Entry")


if __name__ == "__main__":
    unittest.main()
