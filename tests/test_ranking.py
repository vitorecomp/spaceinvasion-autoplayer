"""Tests for ranking evolution estimator, Attack Zones, and snapshot trend tracking."""

from __future__ import annotations

from decimal import Decimal
import math
from pathlib import Path
import tempfile
import unittest

from spaceinvasion import game_data
from spaceinvasion.ranking import (
    _compute_resource_eta,
    attach_action_etas,
    attack_zone_for_points,
    build_buildings_eta,
    build_combat_overview,
    build_ranking_evolution,
    compute_action_eta,
    estimate_account_points,
    estimate_hourly_economy,
    estimate_planet_hourly_economy,
    record_rank_snapshot,
    simulate_combat_matchup,
)
from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.strategy import PlannedAction


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


class TestActionEtaEngine(unittest.TestCase):
    def test_affordable_build_matches_buildings_eta_and_precision(self) -> None:
        planet = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=30,
            fields_max=230,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 200),
            buildings={"b1": 3, "b7": 3, "b17": 1},
        )
        state = GameState(31, 1, "u", 0.0, (planet,), active_planet_id=101, speedup_factor=2.0)
        act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=101,
            planet_coords="02:380:12",
            target_id=1,
            target_name="Pig iron mine",
            target_level=4,
            cost=Resources(**game_data.building_cost(1, 4)),
            missing=Resources(),
            reason="Upgrade",
            executable_now=True,
        )
        eta = compute_action_eta(state, act)
        b_row = next(r for r in build_buildings_eta(state)["buildings"] if r["building_id"] == 1)
        base_s = float(game_data.building_level_stats(1, 4)["base_time_s"])
        expected_dur = base_s / ((3 + 1) * (2 ** 1) * 2.0)

        self.assertEqual(eta["wait_hours"], 0.0)
        self.assertEqual(eta["wait_seconds"], 0.0)
        self.assertEqual(eta["wait_eta"], "Ready")
        self.assertIsNone(eta["eta_bottleneck"])
        self.assertEqual(eta["duration_text"], b_row["build_time_text"])
        self.assertAlmostEqual(eta["duration_seconds"], expected_dur, delta=0.05)
        self.assertAlmostEqual(eta["total_hours"], expected_dur / 3600.0, places=5)
        self.assertAlmostEqual(eta["total_seconds"], expected_dur, delta=0.05)
        self.assertNotIn(eta["total_eta"], ("—", "Ready"))

    def test_dict_cost_derivation_wait_resources_and_days_formatting(self) -> None:
        planet = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=0,
            fields_used=20,
            fields_max=230,
            resources=Resources(100, 0, 0, 0),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(500, 200),
            buildings={"b3": 10, "b5": 15, "b7": 2},
        )
        state = GameState(31, 1, "u", 0.0, (planet,), active_planet_id=101, speedup_factor=1.0)
        kry_rate = float(estimate_planet_hourly_economy(state, planet)["kryptonite_per_hr"])
        miss_kry = round(200.0 * kry_rate)

        wait_dict = {
            "kind": "WAIT_RESOURCES",
            "planet_id": None,
            "planet_coords": "02:380:12",
            "target_id": 3,
            "target_level": 11,
            "cost": {"pig_iron": 50, "kryptonite": miss_kry},
        }
        eta = compute_action_eta(state, wait_dict)
        self.assertAlmostEqual(eta["wait_hours"], 200.0, delta=0.5)
        self.assertEqual(eta["eta_bottleneck"], "kryptonite")
        self.assertTrue(eta["wait_eta"].endswith("d"))
        self.assertTrue(eta["total_eta"].endswith("d"))
        self.assertGreater(eta["duration_seconds"], 0.0)
        self.assertAlmostEqual(eta["total_seconds"], eta["wait_seconds"] + eta["duration_seconds"], delta=0.1)

    def test_research_ignores_b17_and_extrapolates_l42(self) -> None:
        planet = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=0,
            fields_used=20,
            fields_max=230,
            resources=Resources(10_000, 10_000, 10_000, 10_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 200),
            buildings={"b8": 4, "b17": 5},
        )
        state = GameState(31, 1, "u", 0.0, (planet,), researches={"r1": 41}, speedup_factor=1.0)
        act_42 = PlannedAction(
            "P2",
            "RESEARCH",
            101,
            "02:380:12",
            "Mining technology",
            "l42",
            target_id=1,
            target_level=42,
        )
        eta = compute_action_eta(state, act_42)
        l40_s = float(game_data.RESEARCH_BY_ID["r1"]["levels"][40]["base_time_s"])
        expected_s = (l40_s * 4.0) / ((4 + 1) * 1.0)
        self.assertAlmostEqual(eta["duration_seconds"], expected_s, delta=0.1)

    def test_shipyard_defense_and_advisory_durations(self) -> None:
        p_zero_wf = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=0,
            fields_used=10,
            fields_max=230,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(100, 10),
            buildings={"b9": 0, "b17": 1},
        )
        p_wf5 = PlanetState(
            planet_id=102,
            name="Yard",
            coords="02:380:13",
            planet_type="normal",
            temperature=0,
            fields_used=20,
            fields_max=230,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(100, 10),
            buildings={"b3": 4, "b5": 6, "b9": 5, "b17": 1},
        )
        state = GameState(31, 1, "u", 0.0, (p_zero_wf, p_wf5), active_planet_id=101, speedup_factor=2.0)
        ship_base = float(game_data.SHIPS["small-transport"]["base_build_time_s"])
        def_base = float(game_data.DEFENSES["mortar"]["base_build_time_s"])

        e_wf0 = compute_action_eta(state, {"kind": "BUILD_SHIP", "planet_id": 101, "target_id": 1, "target_level": None})
        self.assertAlmostEqual(e_wf0["duration_seconds"], ship_base / (1 * 2 * 2.0), delta=0.05)

        e_wf5 = compute_action_eta(state, {"kind": "BUILD_SHIP", "planet_id": 102, "target_id": 1, "target_level": 4})
        self.assertAlmostEqual(e_wf5["duration_seconds"], (4 * ship_base) / (5 * 2 * 2.0), delta=0.05)

        e_zero_qty = compute_action_eta(state, {"kind": "BUILD_SHIP", "planet_id": 102, "target_id": 1, "target_level": 0})
        self.assertEqual(e_zero_qty["duration_seconds"], 0.0)
        self.assertEqual(e_zero_qty["duration_text"], "Instant")

        e_def = compute_action_eta(state, {"kind": "BUILD_DEFENSE", "planet_id": 102, "target_id": 1, "target_level": 3})
        self.assertAlmostEqual(e_def["duration_seconds"], (3 * def_base) / (5 * 2 * 2.0), delta=0.05)

        e_adv = compute_action_eta(
            state,
            PlannedAction("P3", "SHIP", 102, "02:380:13", "Small transport", "advisory", missing=Resources(0, 0, 500, 0)),
        )
        self.assertGreater(e_adv["wait_hours"], 0.0)
        self.assertEqual(e_adv["duration_seconds"], 0.0)
        self.assertEqual(e_adv["duration_text"], "Instant")
        self.assertAlmostEqual(e_adv["total_seconds"], e_adv["wait_seconds"], delta=0.05)

    def test_instant_and_travel_actions_ignore_cargo_cost(self) -> None:
        state = _sample_ranking_state()
        for kind in (
            "ALERT_HOSTILE",
            "ESCAPE_FLIGHT",
            "CLAIM_TUTORIAL",
            "THROTTLE_BUILDING",
            "SCAN_GALAXY",
            "ATTACK",
            "SPY",
            "TUTORIAL_TASK",
            "NONE",
            "COLONIZE",
            "RECYCLE",
            "TRANSPORT",
            "STATION",
        ):
            act = PlannedAction(
                "P0" if kind in ("ALERT_HOSTILE", "ESCAPE_FLIGHT") else "P2",
                kind,
                12476,
                "02:380:12",
                kind,
                "instant/travel",
                cost=Resources(999_999, 999_999, 999_999, 999_999),
                missing=Resources(1000, 1000, 1000, 1000),
            )
            eta = compute_action_eta(state, act)
            self.assertEqual(eta["wait_hours"], 0.0)
            self.assertEqual(eta["wait_seconds"], 0.0)
            self.assertEqual(eta["wait_eta"], "Ready")
            self.assertEqual(eta["duration_seconds"], 0.0)
            self.assertEqual(eta["duration_text"], "Instant")
            self.assertEqual(eta["total_hours"], 0.0)
            self.assertEqual(eta["total_seconds"], 0.0)
            self.assertEqual(eta["total_eta"], "Ready")
            self.assertIsNone(eta["eta_bottleneck"])

    def test_blocked_overdrain_blackout_isolation_and_subsecond_edges(self) -> None:
        p_drain = PlanetState(
            planet_id=10,
            name="Overdrain",
            coords="01:001:01",
            planet_type="normal",
            temperature=0,
            fields_used=10,
            fields_max=200,
            resources=Resources(),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 500),
            buildings={"b1": 2, "b2": 6, "b7": 2},
            economy_rates={"resource1": None, "resource2": 100},  # type: ignore[dict-item]
        )
        p_blackout = PlanetState(
            planet_id=20,
            name="Blackout",
            coords="01:001:02",
            planet_type="normal",
            temperature=0,
            fields_used=10,
            fields_max=200,
            resources=Resources(),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(0, 100),
            buildings={"b1": 5, "b7": 1},
        )
        state = GameState(
            31,
            1,
            "u",
            0.0,
            (p_drain, p_blackout),
            active_planet_id=10,
            speedup_factor=float("nan"),
        )
        # Estimate handles None rate & NaN speedup safely
        econ_drain = estimate_planet_hourly_economy(state, p_drain)
        self.assertEqual(econ_drain["speedup_factor"], 1.0)
        self.assertLess(econ_drain["pig_iron_per_hr"], 0.0)

        # Blocked over-drain still produces finite duration
        e_drain = compute_action_eta(
            state,
            PlannedAction("P2", "BUILD", 10, p_drain.coords, "Pig iron mine", "drain", target_id=1, target_level=3, missing=Resources(100, 0, 0, 0)),
        )
        self.assertIsNone(e_drain["wait_hours"])
        self.assertIsNone(e_drain["wait_seconds"])
        self.assertEqual(e_drain["wait_eta"], "—")
        self.assertIsNone(e_drain["total_hours"])
        self.assertEqual(e_drain["total_eta"], "—")
        self.assertEqual(e_drain["eta_bottleneck"], "pig_iron")
        self.assertGreater(e_drain["duration_seconds"], 0.0)

        # Energy blackout blocks wait while computing duration
        e_black = compute_action_eta(
            state,
            PlannedAction("P2", "BUILD", 20, p_blackout.coords, "Pig iron mine", "black", target_id=1, target_level=6, missing=Resources(50, 0, 0, 0)),
        )
        self.assertIsNone(e_black["wait_hours"])
        self.assertEqual(e_black["eta_bottleneck"], "pig_iron")
        self.assertGreater(e_black["duration_seconds"], 0.0)

        # Explicit missing planet_id does NOT leak p_drain/p_blackout even when planet_coords matches
        e_missing_pid = compute_action_eta(
            state,
            PlannedAction("P2", "BUILD", 999, p_drain.coords, "Pig iron mine", "bad pid", target_id=1, target_level=2, missing=Resources(0, 0, 50, 0)),
        )
        self.assertIsNone(e_missing_pid["wait_hours"])
        self.assertEqual(e_missing_pid["wait_eta"], "—")
        self.assertEqual(e_missing_pid["eta_bottleneck"], "kryptonite")

        # Bool planet_id cannot alias planet_id=1
        p_one = PlanetState(
            planet_id=1,
            name="One",
            coords="01:001:03",
            planet_type="normal",
            temperature=0,
            fields_used=5,
            fields_max=200,
            resources=Resources(),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b3": 5},
        )
        st_bool = GameState(31, 1, "u", 0.0, (p_one,), active_planet_id=1, speedup_factor=1.0)
        e_bool = compute_action_eta(st_bool, {"kind": "BUILD", "planet_id": True, "target_id": 3, "target_level": 6, "missing": {"kryptonite": 50}})
        self.assertIsNone(e_bool["wait_hours"])

        # Unknown target_id and sub-second high-speedup formatting
        e_unk = compute_action_eta(st_bool, {"kind": "BUILD", "planet_id": 1, "target_id": 999, "target_level": 1})
        self.assertEqual(e_unk["duration_seconds"], 0.0)
        self.assertEqual(e_unk["duration_text"], "Instant")

        st_fast = GameState(31, 1, "u", 0.0, (p_one,), active_planet_id=1, speedup_factor=100_000.0)
        e_tiny = compute_action_eta(st_fast, {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 1})
        self.assertGreater(e_tiny["duration_seconds"], 0.0)
        self.assertEqual(e_tiny["duration_text"], "1s")

    def test_attach_action_etas_dataclasses_dicts_and_empty(self) -> None:
        state = _sample_ranking_state()
        self.assertEqual(attach_action_etas(state, []), [])

        pa = PlannedAction(
            "P2",
            "BUILD",
            12476,
            "02:380:12",
            "Pig iron mine",
            "upgrade",
            target_id=1,
            target_level=12,
            missing=Resources(500, 0, 0, 0),
        )
        d_act = {"kind": "CLAIM_TUTORIAL", "planet_id": 12476}
        out = attach_action_etas(state, [pa, d_act])
        self.assertEqual(len(out), 2)
        self.assertEqual(pa.wait_eta, "Ready")  # original frozen PlannedAction untouched
        self.assertNotIn("wait_eta", d_act)  # original dict untouched
        self.assertGreater(out[0].wait_hours, 0.0)
        self.assertEqual(out[0].eta_bottleneck, "pig_iron")
        self.assertGreater(out[0].duration_seconds, 0.0)
        self.assertEqual(out[1]["wait_eta"], "Ready")
        self.assertEqual(out[1]["duration_text"], "Instant")

    def test_defensive_hardening_non_mapping_inf_unicode_huge_ints_b17_and_empty_tlvl(self) -> None:
        # 1. estimate_planet_hourly_economy handles non-Mapping economy_rates, inf/nan/float-string rates, and bad/inf r1
        p_inf = PlanetState(
            planet_id=1,
            name="H",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=30,
            fields_max=230,
            resources=Resources(),
            storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b2": 2, "b9": 4, "b17": 65},
            economy_rates={"resource1": float("inf"), "resource2": "50.5", "resource3": float("nan")},  # type: ignore[dict-item]
        )
        p_list = PlanetState(
            planet_id=2,
            name="L",
            coords="02:380:13",
            planet_type="normal",
            temperature=31,
            fields_used=10,
            fields_max=230,
            resources=Resources(),
            storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5},
            economy_rates=["bad"],  # type: ignore[arg-type]
        )
        for bad_r1 in ("bad", float("inf"), float("nan")):
            st_bad = GameState(31, 1, "u", 0.0, (p_inf, p_list), active_planet_id=1, researches={"r1": bad_r1})  # type: ignore[dict-item]
            econ_inf = estimate_planet_hourly_economy(st_bad, p_inf)
            self.assertEqual(econ_inf["rates_pct"]["resource1"], 100)
            self.assertEqual(econ_inf["rates_pct"]["resource2"], 50)
            self.assertEqual(econ_inf["rates_pct"]["resource3"], 100)
            econ_list = estimate_planet_hourly_economy(st_bad, p_list)
            self.assertGreater(econ_list["pig_iron_per_hr"], 0.0)

        # 2. Unicode superscript digits never raise ValueError and resolve cleanly to 0.0s / "Instant"
        st_clean = GameState(31, 1, "u", 0.0, (p_inf,), active_planet_id=1, speedup_factor=1.0)
        for kind, tid in (("BUILD", "b²"), ("RESEARCH", "r²"), ("BUILD_SHIP", "s²"), ("BUILD_DEFENSE", "d²")):
            e_uni = compute_action_eta(st_clean, {"kind": kind, "planet_id": 1, "target_id": tid, "target_level": 2})
            self.assertEqual(e_uni["duration_seconds"], 0.0)
            self.assertEqual(e_uni["duration_text"], "Instant")

        # 3. Huge ints (10**400) never raise OverflowError and return finite ETA values
        e_huge_wait = compute_action_eta(
            st_clean,
            {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 6, "missing": {"pig_iron": 10**400}},
        )
        self.assertTrue(math.isfinite(float(e_huge_wait["wait_hours"])))
        self.assertTrue(e_huge_wait["wait_eta"].endswith("d"))

        e_huge_ship = compute_action_eta(
            st_clean,
            {"kind": "BUILD_SHIP", "planet_id": 1, "target_id": 1, "target_level": 10**400},
        )
        self.assertTrue(math.isfinite(float(e_huge_ship["duration_seconds"])))
        self.assertIn("d ", e_huge_ship["duration_text"])

        e_huge_res = compute_action_eta(
            st_clean,
            {"kind": "RESEARCH", "planet_id": 1, "target_id": 1, "target_level": 10**400},
        )
        self.assertTrue(math.isfinite(float(e_huge_res["duration_seconds"])))

        # 4. b17 >= 60 preserves strictly positive duration_seconds matching duration_text="1s"
        e_b65 = compute_action_eta(st_clean, {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 1})
        self.assertGreater(e_b65["duration_seconds"], 0.0)
        self.assertEqual(e_b65["duration_text"], "1s")

        # 5. Empty-string target_level="" on BUILD_SHIP/BUILD_DEFENSE falls back to quantity
        e_empty_tlvl = compute_action_eta(
            st_clean,
            {"kind": "BUILD_SHIP", "planet_id": 1, "target_id": 1, "target_level": "", "quantity": 3},
        )
        e_qty3 = compute_action_eta(
            st_clean,
            {"kind": "BUILD_SHIP", "planet_id": 1, "target_id": 1, "quantity": 3},
        )
        self.assertEqual(e_empty_tlvl["duration_seconds"], e_qty3["duration_seconds"])
        self.assertGreater(e_empty_tlvl["duration_seconds"], 0.0)

        e_def_empty_tlvl = compute_action_eta(
            st_clean,
            {"kind": "BUILD_DEFENSE", "planet_id": 1, "target_id": 1, "target_level": "", "quantity": 4},
        )
        e_def_qty4 = compute_action_eta(
            st_clean,
            {"kind": "BUILD_DEFENSE", "planet_id": 1, "target_id": 1, "quantity": 4},
        )
        self.assertEqual(e_def_empty_tlvl["duration_seconds"], e_def_qty4["duration_seconds"])
        self.assertGreater(e_def_empty_tlvl["duration_seconds"], 0.0)

    def test_exact_half_second_nonfinite_planet_id_and_huge_decimal_missing(self) -> None:
        p_half = PlanetState(
            planet_id=1,
            name="HalfSec",
            coords="01:001:01",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=220,
            resources=Resources(10_000, 10_000, 10_000, 10_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b2": 2, "b7": 7, "b17": 5},
        )
        st = GameState(31, 1, "u", 0.0, (p_half,), active_planet_id=1, speedup_factor=1.0)

        # 1. Exact 0.5s duration (b6 L1 at b7=7, b17=5, speedup=1.0) must format as "1s", never "0s"
        e_half = compute_action_eta(
            st,
            {"kind": "BUILD", "planet_id": 1, "target_id": 6, "target_level": 1},
        )
        self.assertEqual(e_half["duration_seconds"], 0.5)
        self.assertEqual(e_half["duration_text"], "1s")

        # 2. Non-finite and fractional planet_ids safely resolve to missing planet (no OverflowError / truncation)
        for bad_pid in (
            float("inf"),
            float("-inf"),
            float("nan"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
            Decimal("NaN"),
            1.5,
            Decimal("1.5"),
            Decimal("1e100000000"),
            "9" * 5000,
        ):
            e_bad_pid = compute_action_eta(
                st,
                {
                    "kind": "BUILD",
                    "planet_id": bad_pid,
                    "target_id": 1,
                    "target_level": 6,
                    "missing": {"pig_iron": 500},
                },
            )
            self.assertIsNone(e_bad_pid["wait_hours"], msg=f"pid={bad_pid!r}")
            self.assertEqual(e_bad_pid["wait_eta"], "—", msg=f"pid={bad_pid!r}")
            self.assertEqual(e_bad_pid["eta_bottleneck"], "pig_iron", msg=f"pid={bad_pid!r}")

        # Integral string/float/Decimal planet_ids continue resolving planet_id=1
        for good_pid in ("1", " 1 ", 1.0, Decimal("1.0")):
            e_good_pid = compute_action_eta(
                st,
                {
                    "kind": "BUILD",
                    "planet_id": good_pid,
                    "target_id": 1,
                    "target_level": 6,
                    "missing": {"pig_iron": 500},
                },
            )
            self.assertIsNotNone(e_good_pid["wait_hours"], msg=f"pid={good_pid!r}")
            self.assertGreater(e_good_pid["wait_hours"], 0.0, msg=f"pid={good_pid!r}")

        # 3. Huge numeric strings / Decimal("1e400") in missing never collapse to "Ready"
        e_int_ref = compute_action_eta(
            st,
            {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 6, "missing": {"pig_iron": 10**400}},
        )
        for huge_val in ("1" + "0" * 400, Decimal("1e400")):
            e_huge = compute_action_eta(
                st,
                {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 6, "missing": {"pig_iron": huge_val}},
            )
            self.assertIsNotNone(e_huge["wait_hours"])
            self.assertGreater(e_huge["wait_hours"], 0.0)
            self.assertEqual(e_huge["wait_hours"], e_int_ref["wait_hours"])
            self.assertTrue(e_huge["wait_eta"].endswith("d"))

        # 4. NaN rate in _compute_resource_eta and extreme speedup inf-inf NaN safely block
        self.assertEqual(_compute_resource_eta({"pig_iron": 100}, {"pig_iron": float("nan")}), (None, "pig_iron"))
        st_extreme = GameState(31, 1, "u", 0.0, (p_half,), active_planet_id=1, speedup_factor=1e307)
        e_nan_rate = compute_action_eta(
            st_extreme,
            {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 6, "missing": {"pig_iron": 1000}},
        )
        self.assertIsNone(e_nan_rate["wait_hours"])
        self.assertEqual(e_nan_rate["wait_eta"], "—")
        self.assertEqual(e_nan_rate["eta_bottleneck"], "pig_iron")


if __name__ == "__main__":
    unittest.main()
