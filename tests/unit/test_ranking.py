"""Tests for ranking evolution estimator, Attack Zones, and snapshot trend tracking."""

from __future__ import annotations

from decimal import Decimal
import math
from pathlib import Path
import tempfile
import unittest

from spaceinvasion.core import catalog as game_data
from spaceinvasion.combat.points import (
    attack_zone_for_points,
    build_combat_overview,
    build_ranking_evolution,
    estimate_account_points,
    estimate_hourly_economy,
    record_rank_snapshot,
)
from spaceinvasion.combat.simulator import simulate_combat_matchup
from spaceinvasion.planner.eta import (
    compute_resource_eta as _compute_resource_eta,
    attach_action_etas,
    build_buildings_eta,
    compute_action_eta,
    estimate_planet_hourly_economy,
)
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
)
from spaceinvasion.core.model import PlannedAction


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

    def test_format_duration_half_second_huge_ints_clamp_micro_and_malformed_ranking_fields(self) -> None:
        import time
        from spaceinvasion.combat._points_helpers import safe_big_mul_div as _safe_big_mul_div
        from spaceinvasion.combat.points import (
            build_combat_overview,
            build_ranking_evolution,
            estimate_account_points,
        )
        from spaceinvasion.planner.eta import (
            format_duration as _format_duration,
            build_buildings_eta,
        )

        self.assertEqual(_format_duration(0.0), "0s")
        self.assertEqual(_format_duration(0.25), "1s")
        self.assertEqual(_format_duration(0.5), "1s")
        self.assertEqual(_format_duration(1.0), "1s")

        # build_buildings_eta exact 0.5s parity with compute_action_eta on b6 L1 (b7=7, b17=5, speedup=1.0)
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
        st_half = GameState(31, 1, "u", 0.0, (p_half,), active_planet_id=1, speedup_factor=1.0)
        bld_eta = build_buildings_eta(st_half)
        b6_row = next(r for r in bld_eta["buildings"] if r["building_id"] == 6)
        act_half = compute_action_eta(st_half, {"kind": "BUILD", "planet_id": 1, "target_id": 6, "target_level": 1})
        self.assertEqual(b6_row["build_time_text"], "1s")
        self.assertEqual(act_half["duration_text"], "1s")

        # O(1) bit_length short-circuit on million-digit int
        huge_int = 10**1_000_000
        t0 = time.perf_counter()
        big_res = _safe_big_mul_div(huge_int, 1.0, 100.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(big_res, 1e200)
        self.assertLess(elapsed_ms, 50.0)

        p_bad = PlanetState(
            planet_id=1,
            name="Test",
            coords="01:001:01",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=200,
            resources=Resources(),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(100, 50),
            buildings={"b1": 5, "b7": "bad", "b17": "bad"},  # type: ignore[dict-item]
            ships={"s1": "bad", "s4": None},  # type: ignore[dict-item]
            defense={"d1": "oops"},  # type: ignore[dict-item]
        )
        st_bad = GameState(
            31,
            1,
            "u",
            1000.0,
            (p_bad,),
            active_planet_id=1,
            researches={"r1": "bad", "r2": "bad", "r8": None},  # type: ignore[dict-item]
            raid_stats={"per_target": "bad", "overall": None},  # type: ignore[arg-type]
        )
        pts = estimate_account_points(st_bad)
        self.assertIn("estimated_points", pts)
        eta_rows = build_buildings_eta(st_bad)
        self.assertIsInstance(eta_rows, dict)
        self.assertEqual(len(eta_rows["buildings"]), 21)
        for bad_lvl in (None, float("inf"), float("nan")):
            st_b17 = GameState(
                31,
                1,
                "u",
                1000.0,
                (
                    PlanetState(
                        1,
                        "T",
                        "01:001:01",
                        "normal",
                        20,
                        10,
                        200,
                        Resources(),
                        Resources(100_000, 100_000, 100_000, 100_000),
                        EnergyBalance(100, 50),
                        buildings={"b1": 5, "b17": bad_lvl},  # type: ignore[dict-item]
                    ),
                ),
                active_planet_id=1,
                researches={"r2": bad_lvl},  # type: ignore[dict-item]
            )
            self.assertEqual(len(build_buildings_eta(st_b17)["buildings"]), 21)
        combat_ov = build_combat_overview(st_bad)
        self.assertIsInstance(combat_ov, dict)
        self.assertEqual(attack_zone_for_points("bad")["zone"], 1)  # type: ignore[arg-type]
        self.assertEqual(attack_zone_for_points(float("nan"))["zone"], 1)
        self.assertEqual(attack_zone_for_points(float("inf"))["zone"], 1)
        evo = build_ranking_evolution(
            st_bad,
            [
                {"ts": "bad", "server_points": "bad", "server_rank": "bad", "estimated_points": "nan"},
                {"ts": 2000.0, "server_points": 250.0, "server_rank": float("inf"), "estimated_points": None},
            ],
        )
        self.assertIsInstance(evo, dict)
        sim_corrupt = simulate_combat_matchup(
            st_bad,
            attacker_ships={"s1": "bad", "s4": None},  # type: ignore[dict-item]
            defender_ships={"s1": "oops"},  # type: ignore[dict-item]
            defender_defense={"d1": "bad"},  # type: ignore[dict-item]
            target_resources={"pig_iron": "bad"},  # type: ignore[dict-item]
            safety_margin=float("nan"),
        )
        self.assertEqual(sim_corrupt["attacker_power"], 0.0)
        huge_eta = compute_action_eta(
            st_bad,
            {"kind": "BUILD", "planet_id": "  ", "planet_coords": "01:001:01", "target_id": 1, "missing": {"pig_iron": "1e99999999999999999999"}},
        )
        self.assertIsNotNone(huge_eta["wait_hours"])
        self.assertGreater(huge_eta["wait_hours"], 0.0)
        self.assertNotEqual(huge_eta["wait_eta"], "Ready")
        for inf_val in (float("inf"), "inf", "Infinity"):
            inf_eta = compute_action_eta(
                st_bad,
                {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 6, "missing": {"pig_iron": inf_val}},
            )
            self.assertIsNotNone(inf_eta["wait_hours"])
            self.assertGreater(inf_eta["wait_hours"], 0.0)
            self.assertNotEqual(inf_eta["wait_eta"], "Ready")
            self.assertEqual(inf_eta["eta_bottleneck"], "pig_iron")

    def test_huge_ints_unicode_digits_and_corrupt_safety_margin_and_snapshot(self) -> None:
        import json
        from spaceinvasion.db.storage import GameDatabase

        base_st = _sample_ranking_state()
        p0 = base_st.planets[0]
        huge_planet = PlanetState(
            p0.planet_id,
            p0.name,
            p0.coords,
            p0.planet_type,
            p0.temperature,
            p0.fields_used,
            p0.fields_max,
            Resources(1500, 7900, 7000, 2600),
            p0.storage,
            p0.energy,
            buildings={"b1": 10**400, "b5": 10},
            ships={"s4": 10**1_000_000},
            defense={"d1": 10**1_000_000},
        )
        huge_state = GameState(
            31,
            1,
            "u",
            1000.0,
            (huge_planet,),
            active_planet_id=huge_planet.planet_id,
            researches={"r1": 10**400, "r4": 10**400, "r5": 10**400, "r6": 10**400, "r8": 10**400},
            account_points=10**1_000_000,
        )

        # Finding 4: Unicode superscript digit "²" does not raise ValueError across BUILD/RESEARCH/SHIP/DEFENSE
        for kind in ("BUILD", "RESEARCH", "BUILD_SHIP", "BUILD_DEFENSE"):
            eta = compute_action_eta(
                base_st,
                {"kind": kind, "planet_id": p0.planet_id, "target_id": 0, "target_name": "²"},
            )
            self.assertIn("wait_eta", eta)

        # Finding 3 & Finding 5: huge ints and bad/NaN/Inf safety_margin produce finite JSON without OverflowError/ValueError
        evo = build_ranking_evolution(huge_state, [{"ts": 1000.0, "server_points": 500.0, "estimated_points": 500.0}])
        json.dumps(evo, allow_nan=False)
        for bad_margin in ("bad", float("nan"), float("inf"), -2.0):
            ov = build_combat_overview(huge_state, safety_margin=bad_margin)  # type: ignore[arg-type]
            self.assertEqual(ov["safety_margin"], 1.4)
            json.dumps(ov, allow_nan=False)
        sim = simulate_combat_matchup(
            huge_state,
            attacker_ships={"s4": 10**1_000_000},
            defender_ships={"s4": 10**400},
            defender_defense={"d1": 10**400},
            safety_margin="bad",  # type: ignore[arg-type]
        )
        json.dumps(sim, allow_nan=False)

        with tempfile.TemporaryDirectory() as tmp:
            snap_path = Path(tmp) / "rank.jsonl"
            snap_path.write_text(
                json.dumps([{"ts": "bad", "estimated_points": "nan"}]),
                encoding="utf-8",
            )
            samples = record_rank_snapshot(snap_path, huge_state)
            self.assertGreaterEqual(len(samples), 2)
            self.assertIn("estimated_points", samples[-1])
            json.dumps(samples, allow_nan=False)

    def test_buildings_eta_combat_overview_huge_levels_and_rank_history_nan_sanitized(self) -> None:
        import json
        from spaceinvasion.combat.points import load_rank_history
        from spaceinvasion.planner.eta import format_duration as _format_duration

        self.assertEqual(_format_duration(0.5), "1s")
        p_huge = PlanetState(
            planet_id=10**18 + 123456789,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=55,
            fields_max=10**1_000_000,
            resources=Resources(10**400, 10**400, 7000, 2600),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(10**5000, 600),
            buildings={"b1": 10**1_000_000, "b9": 10**1_000_000, "b10": 5},
            ships={"s4": 10**1_000_000},
            defense={"d1": 10**1_000_000},
        )
        st_huge = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p_huge,),
            active_planet_id=10**18 + 123456789,
            researches={"r1": 10**1_000_000, "r4": 5, "r5": 5, "r6": 5},
            account_points=10**1_000_000,
            account_rank=10**5000,
        )
        eta = build_buildings_eta(st_huge, p_huge)
        cov = build_combat_overview(st_huge, p_huge)
        self.assertEqual(eta["planet"]["planet_id"], 10**18 + 123456789)
        self.assertEqual(cov["planet_id"], 10**18 + 123456789)
        self.assertEqual(eta["planet"]["fields_max"], game_data.MAX_JSON_SAFE_INT)
        json.dumps(eta, allow_nan=False)
        json.dumps(cov, allow_nan=False)

        with tempfile.TemporaryDirectory() as tmp:
            hp = Path(tmp) / "rank_hist.json"
            hp.write_text(
                '[{"ts": 1000.0, "estimated_points": NaN, "server_rank": Infinity, "server_points": 1e5000}]',
                encoding="utf-8",
            )
            loaded = load_rank_history(hp)
            json.dumps(loaded, allow_nan=False)
            recorded = record_rank_snapshot(hp, st_huge)
            json.dumps(recorded, allow_nan=False)
            evo = build_ranking_evolution(
                st_huge,
                [{"ts": 1000.0, "estimated_points": float("nan"), "server_rank": float("inf"), "server_points": 10**5000}],
            )
            json.dumps(evo, allow_nan=False)

    def test_extreme_speedup_and_1e308_and_malformed_eta_targets_strict_json(self) -> None:
        import json

        for spd in (1e295, 1e306, 1e307, 1e308):
            st = _sample_ranking_state()
            p0 = PlanetState(
                planet_id=10**18 + 123456789,
                name="Big",
                coords="01:001:01",
                planet_type="normal",
                temperature=30,
                fields_used=20,
                fields_max=200,
                resources=Resources(10**1_000_000, 10**50, 10**50, 10**50),
                storage=Resources(10**400, 10**400, 10**400, 10**400),
                energy=EnergyBalance(produced=5000, used=1000),
                buildings={"b1": 10, "b2": 5, "b3": 8, "b4": 6, "b5": 10, "b17": 60},
            )
            st_ext = GameState(
                universe_id=31,
                user_id=st.user_id,
                nickname=st.nickname,
                timestamp=st.timestamp,
                planets=(p0,),
                active_planet_id=p0.planet_id,
                researches={"r1": 5, "r2": 4, "r3": 6},
                speedup_factor=spd,
                account_points=10**20,
                account_rank=1,
            )
            p_econ = estimate_planet_hourly_economy(st_ext, p0)
            json.dumps(p_econ, allow_nan=False)
            acct_econ = estimate_hourly_economy(st_ext)
            json.dumps(acct_econ, allow_nan=False)
            b_eta = build_buildings_eta(st_ext)
            json.dumps(b_eta, allow_nan=False)
            self.assertEqual(b_eta["planet"]["planet_id"], 10**18 + 123456789)
            evo = build_ranking_evolution(
                st_ext,
                [
                    {"ts": 100.0, "estimated_points": -1e308, "server_rank": 1, "server_points": 1},
                    {"ts": 3700.0, "estimated_points": 1e308, "server_rank": 1, "server_points": 2},
                    {"ts": -1e308, "estimated_points": -1e308, "server_rank": 1, "server_points": -1e308},
                    {"ts": 1e308, "estimated_points": 1e308, "server_rank": 1, "server_points": 1e308},
                ],
            )
            json.dumps(evo, allow_nan=False)

        # Preserve critical 1e307 blast-furnace wait_hours=None invariant AND test b2=0
        for b2_lvl in (2, 0):
            p_bf = PlanetState(
                planet_id=12476,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=31,
                fields_used=55,
                fields_max=230,
                resources=Resources(0, 0, 0, 0),
                storage=Resources(100000, 100000, 100000, 100000),
                energy=EnergyBalance(produced=900, used=600),
                buildings={"b1": 5, "b2": b2_lvl, "b3": 5, "b4": 3, "b5": 5},
            )
            st_bf = GameState(
                universe_id=31,
                user_id=1,
                nickname="u",
                timestamp=1000.0,
                planets=(p_bf,),
                active_planet_id=12476,
                researches={"r1": 1},
                speedup_factor=1e307,
            )
            eta = compute_action_eta(
                st_bf,
                PlannedAction(
                    priority="P2",
                    kind="BUILD",
                    planet_id=12476,
                    planet_coords="02:380:12",
                    target_id=1,
                    target_name="Pig iron mine",
                    target_level=6,
                    cost=Resources(100, 0, 0, 0),
                    missing=Resources(100, 0, 0, 0),
                    reason="test",
                ),
            )
            json.dumps(eta, allow_nan=False)
            if b2_lvl == 2:
                self.assertIsNone(eta["wait_hours"])
                self.assertEqual(eta["wait_eta"], "—")
                self.assertEqual(eta["eta_bottleneck"], "pig_iron")

        # Finding 3 part 2: corrupted coords / target_id / target_name / kind on compute_action_eta
        st_norm = _sample_ranking_state()
        for kind_val in ("BUILD", "RESEARCH", "BUILD_SHIP", "BUILD_DEFENSE", 10**5000):
            act_bad = PlannedAction(
                priority="P2",
                kind=kind_val,  # type: ignore[arg-type]
                planet_id=12476,
                planet_coords="9" * 5000,
                target_id=10**5000,  # type: ignore[arg-type]
                target_name=10**5000,  # type: ignore[arg-type]
                target_level=10**5000,
                reason="test",
            )
            out = compute_action_eta(st_norm, act_bad)
            json.dumps(out, allow_nan=False)

        bad_res_eta = _compute_resource_eta(
            missing={"pig_iron": 10**400, "metal": -5, "kryptonite": float("nan"), "spice": 100},  # type: ignore[dict-item]
            rates_hr={"pig_iron": float("nan"), "metal": 1e308, "kryptonite": -1e308, "spice": 0.0},
        )
        json.dumps(bad_res_eta, allow_nan=False)
        str_res_eta = _compute_resource_eta(
            missing={"pig_iron": "bad"},  # type: ignore[dict-item]
            rates_hr={"pig_iron": 10.0},
        )
        json.dumps(str_res_eta, allow_nan=False)

    def test_r8_build_buildings_eta_bounds_nonfinite_bad_and_huge_horizon_count(self) -> None:
        import json
        import time
        from spaceinvasion.planner.eta import build_buildings_eta

        st = _sample_ranking_state()
        for bad_h in (float("nan"), float("inf"), float("-inf"), "bad", None, True):
            res = build_buildings_eta(st, horizon_count=bad_h)  # type: ignore[arg-type]
            self.assertEqual(len(res["buildings"]), 21)
            self.assertEqual(len(res["buildings"][0]["horizons"]), 4)
            json.dumps(res, allow_nan=False)

        for zero_h in (-5, 0):
            res_z = build_buildings_eta(st, horizon_count=zero_h)
            self.assertEqual(res_z["buildings"][0]["horizons"], [])
            self.assertEqual(res_z["researches"][0]["horizons"], [])

        t0 = time.monotonic()
        for huge_h in (10_000, 10**6, 10**5000):
            res_big = build_buildings_eta(st, horizon_count=huge_h)
            self.assertEqual(len(res_big["buildings"][0]["horizons"]), 100)
            self.assertEqual(len(res_big["researches"][0]["horizons"]), 3)
            json.dumps(res_big, allow_nan=False)
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_r9_simulate_combat_matchup_scales_defender_researches(self) -> None:
        import json

        st = _sample_ranking_state()
        m0 = simulate_combat_matchup(
            st,
            attacker_ships={"s4": 16},
            defender_ships={"s4": 10},
            defender_researches={"r8": 0, "r9": 0, "r10": 0},
        )
        self.assertEqual(m0["defender_power"], 2950.0)
        self.assertAlmostEqual(m0["win_ratio"], 1.6, places=6)
        self.assertTrue(m0["safe_to_attack"])

        m15 = simulate_combat_matchup(
            st,
            attacker_ships={"s4": 16},
            defender_ships={"s4": 10},
            defender_researches={"r8": 15, "r9": 15, "r10": 15},
        )
        self.assertEqual(m15["defender_power"], 7375.0)
        self.assertAlmostEqual(m15["win_ratio"], 0.64, places=6)
        self.assertFalse(m15["safe_to_attack"])
        self.assertEqual(m15["defender_researches"], {"r8": 15, "r9": 15, "r10": 15})
        json.dumps(m15, allow_nan=False)

        # Rejects Arabic-Indic research digits and sanitizes huge values
        m_bad = simulate_combat_matchup(
            st,
            attacker_ships={"s4": 16},
            defender_ships={"s4": 10},
            defender_researches={"r8": "\u0661\u0665", "r9": -5, "r10": 10**400},  # type: ignore[dict-item]
        )
        self.assertLess(m_bad["defender_power"], 1e12)
        json.dumps(m_bad, allow_nan=False)

    def test_r10_huge_precision_nonascii_bytes_and_safe_key(self) -> None:
        import json
        from dataclasses import replace
        from spaceinvasion.combat.points import (
            build_combat_overview,
            estimate_account_points,
        )
        from spaceinvasion.planner.eta import attach_action_etas
        from spaceinvasion.core.model import PlannedAction

        st = _sample_ranking_state()
        pts = estimate_account_points(
            replace(
                st,
                researches={**st.researches, (10**5000, "r"): 2},  # type: ignore[dict-item]
                planets=(
                    replace(
                        st.planets[0],
                        planet_id=10**18 + 123456789,
                        buildings={**st.planets[0].buildings, "b\u0661": 5, b"b1": 5},  # type: ignore[dict-item]
                        ships={**st.planets[0].ships, (10**5000, "s"): 2},  # type: ignore[dict-item]
                        defense={**st.planets[0].defense, frozenset({10**5000}): 2},  # type: ignore[dict-item]
                    ),
                ),
                active_planet_id=10**18 + 123456789,
            )
        )
        self.assertIn("estimated_points", pts)
        json.dumps(pts, allow_nan=False)

        cov = build_combat_overview(st, replace(st.planets[0], ships={"s4": b"5", (10**5000, "s"): 2}))  # type: ignore[dict-item]
        json.dumps(cov, allow_nan=False)

        fast_st = replace(
            st,
            speedup_factor=1e307,
            planets=(replace(st.planets[0], planet_id=10**18 + 123456789),),
            active_planet_id=10**18 + 123456789,
        )
        act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=10**18 + 123456789,
            planet_coords="01:001:01",
            target_id=1,
            target_name="Pig iron mine",
            target_level=2,
            cost=Resources(100, 0, 0, 0),
            missing=Resources(100, 0, 0, 0),
            reason="Wait ETA probe",
            duration_seconds=1,
        )
        attached = attach_action_etas(fast_st, [act])
        self.assertEqual(attached[0].wait_eta, "\u2014")
        self.assertEqual(attached[0].duration_text, "1s")

    def test_r11_build_buildings_eta_huge_research_key_and_normalized_asteroid_type(self) -> None:
        import json
        from dataclasses import replace
        from spaceinvasion.planner.eta import build_buildings_eta
        from spaceinvasion.core.model import ResearchOffer

        st = _sample_ranking_state()
        ast_p = replace(st.planets[0], planet_type=" Asteroid ")
        st_ast = replace(
            st,
            speedup_factor=1e307,
            planets=(ast_p,),
            active_planet_id=ast_p.planet_id,
            research_offers=(
                ResearchOffer(1, 10**5000, "Huge", 0, 1, Resources(100, 0, 0, 0), True, "ok", "?r=1"),  # type: ignore[arg-type]
                ResearchOffer(2, b"r2", "Energy technology", 0, 1, Resources(0, 400, 800, 400), True, "ok", "?r=2"),  # type: ignore[arg-type]
            ),
        )
        eta = build_buildings_eta(st_ast)
        self.assertEqual(eta["planet"]["planet_type"], "asteroid")
        json.dumps(eta, allow_nan=False)

    def test_r12_ranking_resists_broken_fspath_raise_str_and_huge_reward_fields(self) -> None:
        import json
        from spaceinvasion.planner._eta_helpers import safe_eta_int as _safe_eta_int
        from spaceinvasion.core.model import (
            TutorialStatus,
            TutorialTask,
        )

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _RaiseStr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        self.assertEqual(_safe_eta_int(_RaiseFspath(), default=-3), -3)
        self.assertIsInstance(_safe_eta_int(_IntPath(), default=-3), int)

        ast_p = PlanetState(
            planet_id=1,
            name=_RaiseStrRepr(),  # type: ignore[arg-type]
            coords=_RaiseFspath(),  # type: ignore[arg-type]
            planet_type=_RaiseStr(),  # type: ignore[arg-type]
            temperature=10,
            fields_used=_IntPath(),  # type: ignore[arg-type]
            fields_max=50,
            resources=Resources(100, 100, 100, 100),
            storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(50, 10),
            buildings={"b7": _RaiseFspath()},  # type: ignore[dict-item]
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="u",
            timestamp=1000.0,
            planets=(ast_p,),
            active_planet_id=1,
            tutorial=TutorialStatus(
                step=1,
                total_steps=11,
                reward_text=10**5000,  # type: ignore[arg-type]
                tasks=(TutorialTask(_RaiseStrRepr(), _RaiseFspath(), 1, 0, 1),),  # type: ignore[arg-type]
            ),
        )
        eta = build_buildings_eta(st)
        cov = build_combat_overview(st)
        json.dumps(eta, allow_nan=False)
        json.dumps(cov, allow_nan=False)

    def test_r14_safe_eta_int_resists_base_exception_dag20(self) -> None:
        import time
        from spaceinvasion.combat._points_helpers import safe_nonneg_int as _safe_nonneg_int
        from spaceinvasion.combat._points_helpers import safe_optional_finite_int as _safe_optional_finite_int
        from spaceinvasion.planner._eta_helpers import safe_eta_int as _safe_eta_int

        dag: BaseException = RuntimeError("leaf")
        for _ in range(20):
            dag = RuntimeError(10**5000, dag, dag, dag, dag)

        t0 = time.perf_counter()
        res_eta = _safe_eta_int(dag, default=-7)
        res_nn = _safe_nonneg_int(dag, default=-8)
        res_opt = _safe_optional_finite_int(dag)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(res_eta, -7)
        self.assertEqual(res_nn, -8)
        self.assertIsNone(res_opt)
        self.assertLess(elapsed_ms, 50.0)


if __name__ == "__main__":
    unittest.main()
