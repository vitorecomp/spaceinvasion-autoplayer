"""Tests for ranking evolution estimator, Attack Zones, and snapshot trend tracking."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from spaceinvasion.ranking import (
    attack_zone_for_points,
    build_buildings_eta,
    build_combat_overview,
    build_ranking_evolution,
    estimate_account_points,
    estimate_hourly_economy,
    estimate_planet_hourly_economy,
    record_rank_snapshot,
    simulate_combat_matchup,
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

    def test_buildings_eta_and_blast_furnace_pause_effect(self) -> None:
        st_full = _sample_ranking_state()
        p_full = estimate_planet_hourly_economy(st_full, st_full.planets[0])
        self.assertGreater(p_full["metal_per_hr"], 0)
        self.assertGreater(p_full["blast_furnace_pig_used_per_hr"], 0)

        paused_planet = PlanetState(
            planet_id=st_full.planets[0].planet_id,
            name=st_full.planets[0].name,
            coords=st_full.planets[0].coords,
            planet_type=st_full.planets[0].planet_type,
            temperature=st_full.planets[0].temperature,
            fields_used=st_full.planets[0].fields_used,
            fields_max=st_full.planets[0].fields_max,
            resources=Resources(100, 5000, 5000, 2600),
            storage=st_full.planets[0].storage,
            energy=st_full.planets[0].energy,
            buildings=st_full.planets[0].buildings,
            economy_rates={"resource2": 0},
        )
        st_paused = GameState(
            universe_id=st_full.universe_id,
            user_id=st_full.user_id,
            nickname=st_full.nickname,
            timestamp=st_full.timestamp,
            planets=(paused_planet,),
            active_planet_id=paused_planet.planet_id,
            speedup_factor=st_full.speedup_factor,
        )
        p_paused = estimate_planet_hourly_economy(st_paused, paused_planet)
        self.assertEqual(p_paused["metal_per_hr"], 0.0)
        self.assertEqual(p_paused["blast_furnace_pig_used_per_hr"], 0.0)
        self.assertGreater(p_paused["pig_iron_per_hr"], p_full["pig_iron_per_hr"])

        eta = build_buildings_eta(st_paused)
        self.assertEqual(len(eta["buildings"]), 21)
        self.assertEqual(len(eta["researches"]), 15)
        b1 = next(b for b in eta["buildings"] if b["building_id"] == 1)
        self.assertEqual(b1["current_level"], 11)
        self.assertEqual(b1["next_level"], 12)
        self.assertEqual(len(b1["horizons"]), 4)
        self.assertIsNotNone(b1["reach_hours"])
        self.assertNotEqual(b1["reach_eta"], "—")

    def test_combat_overview_and_simulation(self) -> None:
        st = _sample_ranking_state()
        cmb = build_combat_overview(st, safety_margin=1.4)
        self.assertIn("attack_zone", cmb)
        self.assertGreater(len(cmb["ships"]), 10)
        self.assertGreater(len(cmb["defenses"]), 5)

        win = simulate_combat_matchup(
            st,
            attacker_ships={"s4": 25, "s1": 5},
            defender_defense={"d1": 2},
            target_resources={"pig_iron": 10000, "metal": 10000},
            safety_margin=1.4,
        )
        self.assertTrue(win["safe_to_attack"])
        self.assertGreater(win["win_ratio"], 1.4)
        self.assertGreater(win["total_plunder"], 0)

        lose = simulate_combat_matchup(
            st,
            attacker_ships={"s4": 1},
            defender_ships={"s10": 5},
            defender_defense={"d1": 20},
            safety_margin=1.4,
        )
        self.assertFalse(lose["safe_to_attack"])


if __name__ == "__main__":
    unittest.main()
