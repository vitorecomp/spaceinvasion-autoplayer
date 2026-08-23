"""Unit tests for `spaceinvasion.optimizer` (simulator, neural network, GA, and PlannedAction contract)."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from spaceinvasion import game_data
from spaceinvasion.config import StrategyConfig
from spaceinvasion.optimizer import (
    STATE_FEATURE_DIM,
    TORCH_AVAILABLE,
    NeuralValueNetwork,
    OptimizerRunSummary,
    PurePythonMLP,
    SimAction,
    _materialize_planned_actions,
    _safe_base_time_s,
    create_value_network,
    encode_empire_state,
    get_last_optimizer_summary,
    load_attack_history_stats,
    optimize_plan_actions,
    run_ga_nn_optimizer,
    run_optimizer,
    simulate_action_sequence,
)
from spaceinvasion.state import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    TutorialStatus,
    TutorialTask,
    UnitOffer,
)
from spaceinvasion.strategy import plan_actions


def _make_planet(
    planet_id: int = 101,
    *,
    name: str = "Home",
    coords: str = "01:010:05",
    planet_type: str = "normal",
    temperature: int = 20,
    fields_used: int = 25,
    fields_max: int = 230,
    resources: Resources = Resources(5000, 3000, 3000, 1000),
    storage: Resources = Resources(100000, 100000, 100000, 100000),
    energy: EnergyBalance = EnergyBalance(300, 120),
    buildings: dict[str, int] | None = None,
    ships: dict[str, int] | None = None,
    defense: dict[str, int] | None = None,
    economy_rates: dict[str, int] | None = None,
    building_offers: tuple[BuildingOffer, ...] = (),
    ship_offers: tuple[UnitOffer, ...] = (),
) -> PlanetState:
    return PlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type=planet_type,
        temperature=temperature,
        fields_used=fields_used,
        fields_max=fields_max,
        resources=resources,
        storage=storage,
        energy=energy,
        buildings=dict(buildings or {}),
        ships=dict(ships or {}),
        defense=dict(defense or {}),
        economy_rates=dict(economy_rates or {}),
        building_offers=building_offers,
        ship_offers=ship_offers,
    )


class TestEmpireSimulator(unittest.TestCase):
    def test_simulator_never_mutates_input_gamestate(self) -> None:
        b_dict = {"b1": 4, "b2": 2, "b3": 3, "b5": 5}
        s_dict = {"s1": 3, "s4": 2}
        d_dict = {"d1": 1}
        e_dict = {"resource1": 100, "resource2": 100}
        r_dict = {"r1": 1, "r2": 1}
        rt_list = (
            {
                "target_coords": "01:010:08",
                "owner": "IdlePlayer",
                "status_color": "yellow",
                "pig_iron": 4000,
                "metal": 2000,
                "kryptonite": 1000,
                "spice": 500,
                "fleet": {},
                "defense": {},
                "is_spacia": False,
            },
        )
        p = _make_planet(
            resources=Resources(5000, 3000, 3000, 1000),
            buildings=b_dict,
            ships=s_dict,
            defense=d_dict,
            economy_rates=e_dict,
        )
        state = GameState(
            universe_id=31,
            user_id=7,
            nickname="tester",
            timestamp=1_000_000.0,
            planets=(p,),
            researches=r_dict,
            specs={"fleeter": 1},
            raid_targets=rt_list,
        )
        before_b = copy.deepcopy(p.buildings)
        before_s = copy.deepcopy(p.ships)
        before_r = copy.deepcopy(state.researches)
        before_rt = copy.deepcopy(state.raid_targets)

        res = simulate_action_sequence(
            state,
            [
                SimAction("BUILD", p.planet_id, target_id=1),
                SimAction("THROTTLE_BUILDING", p.planet_id, target_id=2, quantity=0),
                SimAction("ATTACK", p.planet_id, target_coords="01:010:08"),
            ],
            horizon_hours=2.0,
        )
        self.assertGreater(res.valid_steps, 0)
        self.assertEqual(p.buildings, before_b)
        self.assertEqual(p.ships, before_s)
        self.assertEqual(state.researches, before_r)
        self.assertEqual(state.raid_targets, before_rt)

    def test_blast_furnace_2_to_1_no_r1_boost_and_pig_iron_dry_clamping(self) -> None:
        # b1 L1 (27/h base), b2 L5 (105 Metal/h, 210 Pig iron/h used), b4 L3 (temp curve)
        p_cold = _make_planet(
            temperature=-120,
            resources=Resources(100, 0, 0, 0),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 1, "b2": 5, "b3": 2, "b4": 3, "b5": 8},
        )
        p_hot = _make_planet(
            temperature=120,
            resources=Resources(100, 0, 0, 0),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 1, "b2": 5, "b3": 2, "b4": 3, "b5": 8},
        )
        st_r0 = GameState(31, 1, "u", 0.0, (p_cold,), researches={"r1": 0})
        st_r10 = GameState(31, 1, "u", 0.0, (p_cold,), researches={"r1": 10})
        st_hot = GameState(31, 1, "u", 0.0, (p_hot,), researches={"r1": 0})

        # Over 3h: Pig iron starts at 100 and drains rapidly -> clamps >= 0; Metal capped to 0.5*pig_gross
        out_r0 = simulate_action_sequence(st_r0, (), horizon_hours=3.0)
        out_r10 = simulate_action_sequence(st_r10, (), horizon_hours=3.0)
        out_hot = simulate_action_sequence(st_hot, (), horizon_hours=3.0)

        p0 = out_r0.final_state.planets[0]
        p10 = out_r10.final_state.planets[0]
        ph = out_hot.final_state.planets[0]
        self.assertGreaterEqual(p0.resources.pig_iron, 0)
        # r1=+20% boosts b1, b3, b4, NOT b2 when unconstrained; verify with Pig iron abundant
        p_rich = _make_planet(
            temperature=0,
            resources=Resources(50000, 0, 0, 0),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b2": 3, "b3": 3, "b4": 3, "b5": 8},
        )
        rich_r0 = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p_rich,), researches={"r1": 0}), (), horizon_hours=1.0)
        rich_r10 = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p_rich,), researches={"r1": 10}), (), horizon_hours=1.0)
        # Metal produced must be IDENTICAL (no r1 boost on b2!), while Kryptonite & Spice jump +20%
        self.assertEqual(
            rich_r0.final_state.planets[0].resources.metal,
            rich_r10.final_state.planets[0].resources.metal,
        )
        self.assertGreater(
            rich_r10.final_state.planets[0].resources.kryptonite,
            rich_r0.final_state.planets[0].resources.kryptonite,
        )
        self.assertGreater(p0.resources.spice, ph.resources.spice)  # cold planet produces more Spice

        # Throttling b2 to 0% stops Pig iron drain completely
        out_throttled = simulate_action_sequence(
            st_r0,
            [SimAction("THROTTLE_BUILDING", p_cold.planet_id, target_id=2, quantity=0)],
            horizon_hours=2.0,
        )
        self.assertEqual(out_throttled.step_outcomes[0].valid, True)
        self.assertGreater(out_throttled.final_metrics.hourly_pig_iron_net, 0.0)
        self.assertGreater(out_throttled.final_state.planets[0].resources.pig_iron, 100)

    def test_energy_deficit_throttles_mines_proportionally_and_power_restores(self) -> None:
        # Energy 100 / 200 => 50% production throttle; researching r2 L1 gives +1% energy -> 101 / 200 (50.5%)
        p_def = _make_planet(
            resources=Resources(5000, 5000, 5000, 5000),
            energy=EnergyBalance(100, 200),
            buildings={"b1": 5, "b5": 5, "b8": 1},
        )
        res_def = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p_def,)), (), horizon_hours=1.0)
        res_r2 = simulate_action_sequence(
            GameState(31, 1, "u", 0.0, (p_def,), researches={"r2": 0}),
            [SimAction("RESEARCH", p_def.planet_id, target_id=2)],
        )
        self.assertAlmostEqual(res_def.final_metrics.hourly_pig_iron_net, 241.0 * 0.50, delta=0.5)
        self.assertEqual(res_r2.final_metrics.total_energy_net, 101 - 200)
        self.assertAlmostEqual(res_r2.final_metrics.hourly_pig_iron_net, 241.0 * 0.505, delta=0.5)
        self.assertGreater(res_def.energy_deficit_hours, 0.99)

        # Building Fusion plant restores power (net >= 0 -> 100% production)
        p_restore = _make_planet(
            resources=Resources(5000, 0, 2000, 0),
            energy=EnergyBalance(100, 120),
            buildings={"b1": 5, "b5": 5},
        )
        res_restored = simulate_action_sequence(
            GameState(31, 1, "u", 0.0, (p_restore,)),
            [SimAction("BUILD", p_restore.planet_id, target_id=5)],
        )
        self.assertGreaterEqual(res_restored.final_metrics.total_energy_net, 0)
        self.assertAlmostEqual(res_restored.final_metrics.hourly_pig_iron_net, 241.0, delta=1.0)

        # Thermosolar Spice dry collapse (`REQ-ECO-022`): b6 L5 burns 20 Spice/h; with 0 Spice & b4=0 -> 0 energy
        p_thermo_dry = _make_planet(
            resources=Resources(0, 0, 0, 0),
            energy=EnergyBalance(80, 40),
            buildings={"b1": 5, "b6": 5},
        )
        res_dry = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p_thermo_dry,)), (), horizon_hours=1.0)
        self.assertEqual(res_dry.final_metrics.total_energy_net, -40)
        self.assertAlmostEqual(res_dry.final_metrics.hourly_pig_iron_net, 0.0, delta=0.5)

    def test_warehouse_capacity_scaling_and_5_percent_overflow_cap(self) -> None:
        # Start below 100,000 cap with b1 L5 (241/h) -> hits 100,000 in ~0.415h, then clamps to 5% (~12.05/h)
        p_cap = _make_planet(
            resources=Resources(99_900, 0, 0, 0),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(200, 50),
            buildings={"b1": 5, "b5": 6},
        )
        res_capped = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p_cap,)), (), horizon_hours=2.0)
        self.assertAlmostEqual(res_capped.final_metrics.hourly_pig_iron_net, 241.0 * 0.05, delta=0.5)
        self.assertGreater(res_capped.overflow_penalty, 1.5)
        # Upgrading b11 doubles storage to 200,000 and restores 100% production
        p_upg = _make_planet(
            resources=Resources(100_000, 2000, 0, 0),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(200, 50),
            buildings={"b1": 5, "b5": 6},
        )
        res_upg = simulate_action_sequence(
            GameState(31, 1, "u", 0.0, (p_upg,)),
            [SimAction("BUILD", p_upg.planet_id, target_id=11)],
        )
        self.assertTrue(res_upg.step_outcomes[0].valid)
        self.assertEqual(res_upg.final_state.planets[0].storage.pig_iron, 200_000)
        self.assertAlmostEqual(res_upg.final_metrics.hourly_pig_iron_net, 241.0, delta=1.0)

    def test_asteroid_raid_and_b19_rules(self) -> None:
        astro = _make_planet(
            planet_id=500,
            name="Asteroid",
            coords="01:010:05",
            planet_type="asteroid",
            fields_used=10,
            fields_max=50,
            resources=Resources(50000, 100000, 80000, 50000),
            buildings={"b7": 8},
        )
        normal = _make_planet(
            planet_id=101,
            resources=Resources(50000, 100000, 80000, 50000),
            buildings={"b7": 8},
        )
        raid_col = _make_planet(
            planet_id=600,
            planet_type="raid",
            resources=Resources(50000, 100000, 80000, 50000),
            buildings={"b7": 3},
        )
        st = GameState(31, 1, "u", 0.0, (astro, normal, raid_col), active_planet_id=500)
        res = simulate_action_sequence(
            st,
            [
                SimAction("BUILD", 500, target_id=1),   # Forbidden on Asteroid
                SimAction("BUILD", 500, target_id=8),   # Forbidden on Asteroid without Synth flag
                SimAction("BUILD", 500, target_id=19),  # Allowed on Asteroid (b7>=8 met)
                SimAction("BUILD", 101, target_id=19),  # b19 forbidden on normal planet
                SimAction("BUILD", 600, target_id=1),   # Forbidden on raid colony
                SimAction("THROTTLE_BUILDING", 500, target_id=2, quantity=0),
            ],
        )
        self.assertFalse(res.step_outcomes[0].valid)
        self.assertFalse(res.step_outcomes[1].valid)
        self.assertTrue(res.step_outcomes[2].valid)
        self.assertFalse(res.step_outcomes[3].valid)
        self.assertFalse(res.step_outcomes[4].valid)
        self.assertFalse(res.step_outcomes[5].valid)

        # Asteroid b8 allowed when allow_asteroid_research_lab=True
        res_synth = simulate_action_sequence(
            st,
            [SimAction("BUILD", 500, target_id=8)],
            allow_asteroid_research_lab=True,
        )
        self.assertTrue(res_synth.step_outcomes[0].valid)

        # Asteroid 1-slot queue rule: 2nd concurrent build waits for 1st build to finish
        res_q = simulate_action_sequence(
            st,
            [
                SimAction("BUILD", 500, target_id=10),
                SimAction("BUILD", 500, target_id=19),
            ],
        )
        self.assertTrue(all(o.valid for o in res_q.step_outcomes))
        self.assertGreaterEqual(res_q.step_outcomes[1].start_t, res_q.step_outcomes[0].finish_t - 1e-6)

    def test_prerequisites_field_limits_and_zero_field_solar_cells(self) -> None:
        p = _make_planet(
            temperature=120,
            fields_used=229,
            fields_max=230,  # Only 1 free field left
            resources=Resources(50000, 50000, 50000, 20000),
            energy=EnergyBalance(50, 50),
            buildings={"b7": 3, "b8": 1, "b9": 1},
        )
        st = GameState(31, 1, "u", 0.0, (p,), researches={"r2": 1})
        res = simulate_action_sequence(
            st,
            [
                SimAction("BUILD", p.planet_id, target_id=1),  # Uses last field (230/230)
                SimAction("BUILD", p.planet_id, target_id=3),  # Rejected: fields full!
                SimAction("BUILD_SHIP", p.planet_id, target_id=9, quantity=2),  # 0 fields, +120 energy @ 120C
                SimAction("RESEARCH", p.planet_id, target_id=1),  # Rejected: r1 needs b8 L2 & r2 L4
            ],
        )
        self.assertTrue(res.step_outcomes[0].valid)
        self.assertFalse(res.step_outcomes[1].valid)
        self.assertTrue(res.step_outcomes[2].valid)
        self.assertFalse(res.step_outcomes[3].valid)
        self.assertEqual(res.final_state.planets[0].fields_used, 230)
        self.assertGreater(res.final_state.planets[0].energy.produced, 50)

    def test_research_lock_safe_base_time_and_unknown_event_ship_ignored(self) -> None:
        # Ensure _safe_base_time_s fixes 1e14 truncation artifact monotonically
        self.assertGreater(_safe_base_time_s("buildings", 17, 27), _safe_base_time_s("buildings", 17, 26))
        self.assertGreater(_safe_base_time_s("buildings", 1, 69), _safe_base_time_s("buildings", 1, 68))
        self.assertGreater(_safe_base_time_s("research", "r1", 37), _safe_base_time_s("research", "r1", 36))

        p = _make_planet(
            resources=Resources(50000, 50000, 50000, 50000),
            buildings={"b7": 4, "b8": 2, "b9": 2},
        )
        st = GameState(31, 1, "u", 0.0, (p,), researches={"r2": 1})
        res = simulate_action_sequence(
            st,
            [
                SimAction("RESEARCH", p.planet_id, target_id=2),
                SimAction("RESEARCH", p.planet_id, target_id=3),  # Must wait for r2 in global 1-slot queue
                SimAction("BUILD_SHIP", p.planet_id, target_id=104, quantity=1),  # Unknown event ship rejected
                SimAction("BUILD_DEFENSE", p.planet_id, target_id="d1", quantity=2),  # Mortar unlocked at b9 L1
                SimAction("BUILD_DEFENSE", p.planet_id, target_id="d2", quantity=1),  # Laser battery prereqs unmet
            ],
        )
        self.assertTrue(res.step_outcomes[0].valid)
        self.assertTrue(res.step_outcomes[1].valid)
        self.assertGreaterEqual(res.step_outcomes[1].start_t, res.step_outcomes[0].finish_t - 1e-6)
        self.assertFalse(res.step_outcomes[2].valid)
        self.assertTrue(res.step_outcomes[3].valid)
        self.assertFalse(res.step_outcomes[4].valid)
        self.assertGreater(res.final_metrics.fleet_defense_points, res.initial_metrics.fleet_defense_points)

        # When prereqs met, d2 resolves cleanly via fleet.DEFENSE_SLUG_BY_KEY (`laser-cannon-battery`)
        p_hi = _make_planet(
            resources=Resources(50000, 50000, 50000, 50000),
            buildings={"b9": 8},
        )
        res_d2 = simulate_action_sequence(
            GameState(31, 1, "u", 0.0, (p_hi,), researches={"r2": 8, "r11": 10}),
            [SimAction("BUILD_DEFENSE", p_hi.planet_id, target_id="d2", quantity=2)],
        )
        self.assertTrue(res_d2.step_outcomes[0].valid)
        self.assertEqual(res_d2.final_state.planets[0].defense.get("d2"), 2)

        # Microsystem accelerator (b17) cuts building & shipyard durations by 2^b17, NOT research
        p_no_m = _make_planet(resources=Resources(50000, 50000, 50000, 50000), buildings={"b8": 2, "b9": 2, "b17": 0})
        p_m2 = _make_planet(resources=Resources(50000, 50000, 50000, 50000), buildings={"b8": 2, "b9": 2, "b17": 2})
        seq_t = [
            SimAction("BUILD", p_no_m.planet_id, target_id=1),
            SimAction("BUILD_SHIP", p_no_m.planet_id, target_id=1, quantity=2),
            SimAction("RESEARCH", p_no_m.planet_id, target_id=2),
        ]
        out_no_m = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p_no_m,)), seq_t)
        out_m2 = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p_m2,)), seq_t)
        self.assertAlmostEqual(out_m2.step_outcomes[0].duration_s * 4.0, out_no_m.step_outcomes[0].duration_s, delta=2.0)
        self.assertAlmostEqual(out_m2.step_outcomes[1].duration_s * 4.0, out_no_m.step_outcomes[1].duration_s, delta=2.0)
        self.assertAlmostEqual(out_m2.step_outcomes[2].duration_s, out_no_m.step_outcomes[2].duration_s, delta=1e-6)

    def test_blocked_resource_guard_rejects_non_positive_rate(self) -> None:
        p = _make_planet(
            resources=Resources(0, 0, 0, 0),
            energy=EnergyBalance(0, 0),
            buildings={},  # 0/h income!
        )
        res = simulate_action_sequence(
            GameState(31, 1, "u", 0.0, (p,)),
            [SimAction("BUILD", p.planet_id, target_id=1)],
        )
        self.assertEqual(res.valid_steps, 0)
        self.assertEqual(res.rejected_steps, 1)
        self.assertIn("Blocked", res.step_outcomes[0].rejection_reason)

    def test_raid_scoring_bash_limit_and_pvp_guard(self) -> None:
        p = _make_planet(ships={"s6": 20, "s2": 10})
        targets = (
            {
                "target_coords": "01:010:09",
                "owner": "PvPTarget",
                "status_color": "light_red",
                "pig_iron": 10000,
                "metal": 10000,
                "kryptonite": 10000,
                "spice": 10000,
                "fleet": {},
                "defense": {},
                "is_spacia": False,
            },
            {
                "target_coords": "01:010:11",
                "owner": "YellowFarm",
                "status_color": "yellow",
                "pig_iron": 160000,
                "metal": 0,
                "kryptonite": 0,
                "spice": 0,
                "fleet": {},
                "defense": {},
                "is_spacia": False,
            },
        )
        st = GameState(31, 1, "u", 1_000_000.0, (p,), raid_targets=targets)
        # PvP disallowed by default
        res_no_pvp = simulate_action_sequence(
            st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:09")],
            config=StrategyConfig(allow_pvp_in_az=False),
        )
        self.assertFalse(res_no_pvp.step_outcomes[0].valid)

        # Bash limit 3/24h on active light_red; 4th attack blocked, while yellow exempt (>3 OK)
        res_bash = simulate_action_sequence(
            st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:09")] * 4
            + [SimAction("ATTACK", p.planet_id, target_coords="01:010:11")] * 4,
            config=StrategyConfig(allow_pvp_in_az=True),
        )
        self.assertEqual([o.valid for o in res_bash.step_outcomes[:4]], [True, True, True, False])
        self.assertEqual([o.valid for o in res_bash.step_outcomes[4:]], [True, True, True, True])
        self.assertGreater(res_bash.final_metrics.simulated_raid_loot_spice_eq, 0.0)
        self.assertGreaterEqual(res_bash.elapsed_seconds, 5400.0)


class TestNeuralValueNetwork(unittest.TestCase):
    def test_state_encoder_fixed_dim_finite_and_sensitive(self) -> None:
        p1 = _make_planet(resources=Resources(500, 200, 100, 50), buildings={"b1": 2})
        p2 = _make_planet(
            resources=Resources(50000, 20000, 10000, 5000),
            buildings={"b1": 12, "b3": 10, "b5": 14},
            ships={"s6": 25},
        )
        v1 = encode_empire_state(GameState(31, 1, "u", 0.0, (p1,)))
        v2 = encode_empire_state(GameState(31, 1, "u", 0.0, (p2,)))
        self.assertEqual(len(v1), STATE_FEATURE_DIM)
        self.assertEqual(len(v2), STATE_FEATURE_DIM)
        self.assertTrue(all(math.isfinite(x) for x in v1))
        self.assertTrue(all(math.isfinite(x) for x in v2))
        self.assertNotEqual(v1, v2)

    def test_pure_python_mlp_exact_analytical_gradients(self) -> None:
        net = PurePythonMLP(seed=7)
        self.assertEqual(net.parameter_count(), STATE_FEATURE_DIM * 32 + 32 + 32 * 16 + 16 + 16 * 1 + 1)
        x = [0.05 * ((i % 7) - 3) for i in range(STATE_FEATURE_DIM)]
        y = 0.65
        eps = 1e-6
        lr = 0.01
        wd = 1e-3

        # Verify analytical gradients across 100% of weights & biases including L2 weight decay
        for layer_idx in range(len(net.weights)):
            for i in range(len(net.biases[layer_idx])):
                b_orig = net.biases[layer_idx][i]
                net.biases[layer_idx][i] = b_orig + eps
                loss_p = 0.5 * (net.predict(x) - y) ** 2
                net.biases[layer_idx][i] = b_orig - eps
                loss_m = 0.5 * (net.predict(x) - y) ** 2
                net.biases[layer_idx][i] = b_orig
                fd_grad = (loss_p - loss_m) / (2.0 * eps)

                snap = net.to_dict()
                net.train_step(x, y, lr=lr, weight_decay=wd)
                analytic_grad = (b_orig - net.biases[layer_idx][i]) / lr
                net = PurePythonMLP.from_dict(snap)
                self.assertAlmostEqual(analytic_grad, fd_grad, places=6)

                for j in range(len(net.weights[layer_idx][i])):
                    w_orig = net.weights[layer_idx][i][j]
                    net.weights[layer_idx][i][j] = w_orig + eps
                    loss_p = 0.5 * (net.predict(x) - y) ** 2
                    net.weights[layer_idx][i][j] = w_orig - eps
                    loss_m = 0.5 * (net.predict(x) - y) ** 2
                    net.weights[layer_idx][i][j] = w_orig
                    fd_grad_w = (loss_p - loss_m) / (2.0 * eps) + wd * w_orig

                    snap = net.to_dict()
                    net.train_step(x, y, lr=lr, weight_decay=wd)
                    analytic_grad_w = (w_orig - net.weights[layer_idx][i][j]) / lr
                    net = PurePythonMLP.from_dict(snap)
                    self.assertAlmostEqual(analytic_grad_w, fd_grad_w, places=6)

    def test_mlp_training_reduces_loss_updates_weights_and_roundtrips_dict(self) -> None:
        net = create_value_network("python", seed=11)
        samples = [
            ([0.1 * ((i + k) % 4) for i in range(STATE_FEATURE_DIM)], math.tanh(0.15 * k - 0.5))
            for k in range(12)
        ]
        w_before = net.weight_snapshot()
        stats = net.train_on_rollouts(samples, epochs=15, lr=0.03)
        w_after = net.weight_snapshot()
        self.assertLess(stats.final_loss, stats.initial_loss)
        self.assertGreater(stats.weight_delta_norm, 0.0)
        self.assertNotEqual(w_before, w_after)

        reloaded = NeuralValueNetwork.from_dict(net.to_dict(), backend="python")
        self.assertAlmostEqual(net.predict(samples[0][0]), reloaded.predict(samples[0][0]), places=10)

    def test_seeded_nn_reproducibility_and_distinct_seeds(self) -> None:
        n1 = NeuralValueNetwork(backend="python", seed=123)
        n2 = NeuralValueNetwork(backend="python", seed=123)
        n3 = NeuralValueNetwork(backend="python", seed=456)
        x = [0.25] * STATE_FEATURE_DIM
        self.assertEqual(n1.weight_snapshot(), n2.weight_snapshot())
        self.assertAlmostEqual(n1.predict(x), n2.predict(x), places=12)
        self.assertNotEqual(n1.weight_snapshot(), n3.weight_snapshot())

    def test_backend_selection_python_and_torch_guard(self) -> None:
        net_auto = NeuralValueNetwork(backend="auto", seed=1)
        if not TORCH_AVAILABLE:
            self.assertEqual(net_auto.backend, "python")
            with self.assertRaises(RuntimeError):
                NeuralValueNetwork(backend="torch", seed=1)
        else:
            self.assertEqual(net_auto.backend, "torch")
        with self.assertRaises(ValueError):
            NeuralValueNetwork(backend="invalid_backend")

    def test_r7_neural_value_network_from_dict_to_dict_and_seed_hardening(self) -> None:
        import json
        from spaceinvasion.optimizer import _deterministic_seed

        bad_payloads: list[object] = [
            None,
            [],
            {},
            {"layer_sizes": None},
            {"layer_sizes": 123},
            {"layer_sizes": "bad"},
            {"layer_sizes": [4]},
            {"layer_sizes": ["9" * 5000, 1]},
            {"layer_sizes": [4, 10**8]},
            {"layer_sizes": [4, 2, 1], "seed": None},
            {"layer_sizes": [4, 2, 1], "seed": "9" * 5000},
            {"layer_sizes": [4, 2, 1], "seed": 10**5000},
            {"layer_sizes": [4, 2, 1], "seed": float("nan")},
            {"layer_sizes": [4, 2, 1], "weights": {1: 2}, "biases": {1: 2}},
            {"layer_sizes": [4, 2, 1], "weights": 123, "biases": ["bad"]},
        ]
        for payload in bad_payloads:
            net = NeuralValueNetwork.from_dict(payload, backend="python")  # type: ignore[arg-type]
            self.assertLessEqual(len(net.impl.layer_sizes), 16)
            self.assertTrue(all(1 <= d <= 4096 for d in net.impl.layer_sizes))
            pred = net.predict([0.5] * net.impl.layer_sizes[0])
            self.assertTrue(math.isfinite(pred))
            json.dumps(net.to_dict(), allow_nan=False)

        mlp_neg = PurePythonMLP(layer_sizes=(4, -1, 1), seed=10**5000)
        self.assertEqual(mlp_neg.layer_sizes, (4, 1, 1))
        self.assertTrue(math.isfinite(mlp_neg.predict([1.0, 2.0, 3.0, 4.0])))
        json.dumps(mlp_neg.to_dict(), allow_nan=False)
        self.assertTrue(math.isfinite(PurePythonMLP.from_dict(None).predict([0.1] * STATE_FEATURE_DIM)))  # type: ignore[arg-type]
        self.assertTrue(
            math.isfinite(
                PurePythonMLP.from_dict({"layer_sizes": [4, 1], "weights": {1: 2}, "biases": {1: 2}}).predict(
                    [0.1, 0.2, 0.3, 0.4]
                )
            )
        )
        with self.assertRaises(ValueError):
            PurePythonMLP(layer_sizes=(4,))
        if not TORCH_AVAILABLE:
            with self.assertRaises(RuntimeError):
                NeuralValueNetwork.from_dict({}, backend="torch")
        with self.assertRaises(ValueError):
            NeuralValueNetwork.from_dict({}, backend="bad_backend")

        p = _make_planet(resources=Resources(5000, 3000, 2000, 1000), buildings={"b1": 2})
        gs = GameState(universe_id=31, user_id=1, nickname="u", timestamp=1000.0, planets=(p,), active_planet_id=1)
        self.assertIsInstance(_deterministic_seed(gs, StrategyConfig(), float("nan")), int)  # type: ignore[arg-type]
        self.assertIsInstance(_deterministic_seed(gs, StrategyConfig(), "9" * 5000), int)  # type: ignore[arg-type]
        summ = run_optimizer(gs, population_size=4, generations=1, horizon_steps=1, seed=float("nan"), attack_history_path="")  # type: ignore[arg-type]
        self.assertTrue(math.isfinite(summ.final_best_fitness))
        json.dumps(summ.to_dict(), allow_nan=False)


class TestGeneticOptimizer(unittest.TestCase):
    def _rich_empire_state(self) -> GameState:
        p = _make_planet(
            resources=Resources(4500, 2500, 3500, 1200),
            energy=EnergyBalance(140, 90),
            buildings={"b1": 5, "b2": 3, "b3": 4, "b4": 2, "b5": 6, "b7": 2, "b8": 1, "b9": 1},
            ships={"s1": 2, "s4": 4},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(600, 0, 200, 0), True, "Build", "h1"),
                BuildingOffer(3, "Kryptonite mine", 4, 5, Resources(500, 0, 300, 0), True, "Build", "h3"),
                BuildingOffer(5, "Fusion plant", 6, 7, Resources(900, 0, 400, 0), True, "Build", "h5"),
            ),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 4, Resources(1500, 0, 500, 0), True, "ship[4]"),
                UnitOffer("ship", 6, "Starcruiser", 0, Resources(0, 4000, 1500, 500), True, "ship[6]"),
            ),
        )
        return GameState(
            universe_id=31,
            user_id=1,
            nickname="tester",
            timestamp=1_000_000.0,
            planets=(p,),
            researches={"r1": 0, "r2": 1},
            research_offers=(
                ResearchOffer(2, "r2", "Energy technology", 1, 2, Resources(0, 800, 1600, 800), True, "Research", "hr2"),
            ),
            raid_targets=(
                {
                    "target_coords": "01:010:12",
                    "owner": "RichInactive",
                    "status_color": "yellow",
                    "pig_iron": 6000,
                    "metal": 3000,
                    "kryptonite": 2000,
                    "spice": 1500,
                    "fleet": {},
                    "defense": {},
                    "is_spacia": False,
                },
            ),
        )

    def test_ga_deterministic_seeding_reproducibility(self) -> None:
        st = self._rich_empire_state()
        cfg = StrategyConfig(planner_mode="ga_nn", ga_seed=99, allow_research=True, allow_shipyard=True)
        s1 = run_optimizer(st, config=cfg)
        s2 = run_optimizer(st, config=cfg)
        self.assertEqual(s1.seed, 99)
        self.assertEqual(s1.best_chromosome, s2.best_chromosome)
        self.assertEqual(s1.final_best_fitness, s2.final_best_fitness)
        self.assertEqual(s1.nn_final_loss, s2.nn_final_loss)
        self.assertEqual(s1.actions, s2.actions)

    def test_ga_improves_composite_fitness_and_trains_nn_over_gen_zero(self) -> None:
        st = self._rich_empire_state()
        cfg = StrategyConfig(
            planner_mode="ga_nn",
            playstyle="miner",
            ga_population_size=24,
            ga_generations=12,
            ga_horizon_steps=6,
            ga_seed=42,
            allow_research=True,
            allow_shipyard=True,
        )
        summary = run_optimizer(st, config=cfg)
        self.assertLess(summary.elapsed_ms, 2000.0)
        self.assertGreater(summary.final_best_fitness, summary.initial_best_fitness)
        self.assertGreater(summary.final_mean_fitness, summary.initial_mean_fitness)
        # Monotonic elitism across all generations
        for prev_g, cur_g in zip(summary.generation_history, summary.generation_history[1:]):
            self.assertGreaterEqual(cur_g.best_fitness, prev_g.best_fitness)
        self.assertGreater(summary.nn_weight_delta_norm, 0.0)
        self.assertLessEqual(summary.nn_final_loss, summary.nn_initial_loss)

    def test_playstyle_changes_fitness_weighting_and_plan_preference(self) -> None:
        st = self._rich_empire_state()
        miner_sum = run_optimizer(
            st,
            config=StrategyConfig(
                playstyle="miner",
                planner_mode="ga_nn",
                ga_seed=77,
                allow_research=True,
                allow_shipyard=True,
                allow_raids=True,
            ),
        )
        fleeter_sum = run_optimizer(
            st,
            config=StrategyConfig(
                playstyle="fleeter",
                planner_mode="ga_nn",
                ga_seed=77,
                allow_research=True,
                allow_shipyard=True,
                allow_raids=True,
            ),
        )
        # Check same chromosome scores differently under miner vs fleeter weights
        seq = [SimAction("ATTACK", 101, target_coords="01:010:12"), SimAction("BUILD_SHIP", 101, target_id=4)]
        miner_eval = simulate_action_sequence(st, seq, config=StrategyConfig(playstyle="miner"))
        fleeter_eval = simulate_action_sequence(st, seq, config=StrategyConfig(playstyle="fleeter"))
        self.assertGreater(fleeter_eval.composite_growth_score, miner_eval.composite_growth_score)
        self.assertNotEqual(miner_sum.best_chromosome, fleeter_sum.best_chromosome)

    def test_all_generated_actions_respect_prereqs_fields_queues_and_asteroid_rules(self) -> None:
        astro = _make_planet(
            planet_id=500,
            name="Astro",
            coords="01:010:06",
            planet_type="asteroid",
            fields_used=5,
            fields_max=50,
            resources=Resources(20000, 20000, 20000, 10000),
            buildings={"b7": 4},
        )
        st = GameState(31, 1, "u", 0.0, (astro,), active_planet_id=500)
        summary = run_optimizer(st, config=StrategyConfig(planner_mode="ga_nn", ga_seed=15))
        self.assertEqual(summary.best_simulation.rejected_steps, 0)
        for out in summary.best_simulation.step_outcomes:
            if out.action.kind == "BUILD":
                self.assertIn(out.action.target_id, {7, 9, 10, 17, 19})


class TestOptimizerPlannedActionContract(unittest.TestCase):
    def test_p0_hostile_alert_and_escape_flight_outrank_optimizer(self) -> None:
        p1 = _make_planet(
            planet_id=101,
            coords="01:010:05",
            resources=Resources(10000, 5000, 4000, 2000),
            ships={"s1": 5, "s4": 3},
            building_offers=(BuildingOffer(1, "Pig iron mine", 5, 6, Resources(600, 0, 200, 0), True, "Build", "h1"),),
        )
        p2 = _make_planet(planet_id=102, coords="01:010:07")
        st = GameState(
            31,
            1,
            "u",
            0.0,
            (p1, p2),
            active_planet_id=101,
            fleet_alerts=(FleetAlert("ATTACK", True, "Enemy fleet incoming"),),
        )
        acts_hold = optimize_plan_actions(st, config=StrategyConfig(allow_fleetsave=False, ga_seed=5))
        self.assertEqual(acts_hold[0].priority, "P0")
        self.assertEqual(acts_hold[0].kind, "ALERT_HOSTILE")
        self.assertFalse(acts_hold[0].executable_now)
        esc_hold = next(a for a in acts_hold if a.kind == "ESCAPE_FLIGHT")
        self.assertEqual(esc_hold.priority, "P0")
        self.assertFalse(esc_hold.executable_now)

        acts_go = optimize_plan_actions(st, config=StrategyConfig(allow_fleetsave=True, ga_seed=5))
        esc_go = next(a for a in acts_go if a.kind == "ESCAPE_FLIGHT")
        self.assertTrue(esc_go.executable_now)

    def test_claim_tutorial_emitted_when_can_complete(self) -> None:
        p = _make_planet()
        tut = TutorialStatus(
            step=1,
            total_steps=11,
            tasks=(TutorialTask("Increase your Fusion plant level 3 / 3", "Fusion plant", 5, 3, 3),),
            reward_text="5000 Pig iron",
            can_complete=True,
        )
        st = GameState(31, 1, "u", 0.0, (p,), tutorial=tut)
        acts = optimize_plan_actions(st, config=StrategyConfig(ga_seed=1))
        claim = next((a for a in acts if a.kind == "CLAIM_TUTORIAL"), None)
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertTrue(claim.executable_now)
        self.assertEqual(claim.priority, "P2")

    def test_exact_action_href_propagation_and_executable_now_invariants(self) -> None:
        p1 = _make_planet(
            planet_id=101,
            coords="01:010:05",
            resources=Resources(5000, 3000, 3000, 1000),
            buildings={"b1": 4, "b2": 2, "b3": 3, "b5": 5, "b8": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 4, 5, Resources(400, 0, 120, 0), True, "Build level 5", "href_b1"),
                BuildingOffer(3, "Kryptonite mine", 3, 4, Resources(350, 0, 180, 0), True, "Build level 4", "href_b3"),
                BuildingOffer(5, "Fusion plant", 5, 6, Resources(900, 0, 400, 0), True, "Build level 6", "href_b5"),
            ),
        )
        p2 = _make_planet(
            planet_id=202,
            name="Colony2",
            coords="01:010:08",
            resources=Resources(5000, 3000, 3000, 1000),
            buildings={"b1": 1, "b5": 2},
        )
        st = GameState(
            31,
            1,
            "u",
            0.0,
            (p1, p2),
            active_planet_id=101,
            researches={"r2": 1},
            research_offers=(
                ResearchOffer(2, "r2", "Energy technology", 1, 2, Resources(0, 800, 1600, 800), True, "Research", "href_r2"),
            ),
        )

        # Single-planet scope excludes p2
        single_acts = optimize_plan_actions(
            st, config=StrategyConfig(allow_multi_planet=False, allow_research=True, ga_seed=12)
        )
        self.assertTrue(all(a.planet_id == 101 for a in single_acts))
        for a in single_acts:
            if a.kind == "BUILD" and a.planet_id == 101:
                self.assertTrue(a.executable_now)
                self.assertTrue(a.action_href and a.action_href.startswith("href_"))
                self.assertNotIn("->", a.target_name)
            elif a.kind == "RESEARCH" and a.executable_now:
                self.assertEqual(a.action_href, "href_r2")
                self.assertNotIn("->", a.target_name)
            elif a.kind == "WAIT_RESOURCES":
                self.assertFalse(a.executable_now)
                self.assertIsNone(a.action_href)

        # Full Asteroid queue (1 slot) must block executable_now BUILD on active asteroid
        astro = _make_planet(
            planet_id=500,
            planet_type="asteroid",
            buildings={"b7": 1},
            building_offers=(BuildingOffer(10, "Fleet base", 0, 1, Resources(0, 2000, 500, 800), True, "Build", "href_b10"),),
        )
        st_astro_full = GameState(
            31,
            1,
            "u",
            0.0,
            (astro,),
            active_planet_id=500,
            active_queues=(QueueJob("building", "Development center", building_id=7, target_level=2),),
        )
        astro_acts = optimize_plan_actions(st_astro_full, config=StrategyConfig(ga_seed=3))
        self.assertFalse(any(a.kind == "BUILD" and a.executable_now for a in astro_acts))

    def test_allow_flags_gate_executable_now(self) -> None:
        p = _make_planet(
            resources=Resources(5000, 5000, 5000, 2000),
            buildings={"b1": 4, "b3": 4, "b5": 6, "b8": 1, "b9": 1},
            ships={"s1": 4, "s4": 5, "s10": 2},
            building_offers=(BuildingOffer(1, "Pig iron mine", 4, 5, Resources(400, 0, 120, 0), True, "Build", "h1"),),
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 5, Resources(1500, 0, 500, 0), True, "ship[4]"),),
        )
        st = GameState(
            31,
            1,
            "u",
            0.0,
            (p,),
            researches={"r2": 1},
            research_offers=(
                ResearchOffer(2, "r2", "Energy technology", 1, 2, Resources(0, 800, 1600, 800), True, "Research", "hr2"),
            ),
            raid_targets=(
                {
                    "target_coords": "01:010:15",
                    "owner": "YellowTarget",
                    "status_color": "yellow",
                    "pig_iron": 12000,
                    "metal": 5000,
                    "kryptonite": 3000,
                    "spice": 2000,
                    "fleet": {},
                    "defense": {},
                    "is_spacia": False,
                },
            ),
        )
        gated_cfg = StrategyConfig(
            allow_research=False,
            allow_shipyard=False,
            allow_raids=False,
            allow_galaxy_scan=False,
            allow_fleetsave=False,
            ga_seed=9,
        )
        acts = optimize_plan_actions(st, config=gated_cfg)
        for kind in ("RESEARCH", "BUILD_SHIP", "ATTACK", "SCAN_GALAXY", "ESCAPE_FLIGHT"):
            matching = [a for a in acts if a.kind == kind]
            self.assertTrue(all(not a.executable_now for a in matching), f"Expected {kind} gated non-executable")
        self.assertTrue(any(a.kind == "ATTACK" and not a.executable_now for a in acts))
        self.assertTrue(any(a.kind == "RESEARCH" and not a.executable_now for a in acts))
        self.assertTrue(any(a.kind == "BUILD_SHIP" and not a.executable_now for a in acts))

    def test_optimize_plan_actions_leaves_priority_plan_actions_unchanged(self) -> None:
        p = _make_planet(
            resources=Resources(1200, 0, 800, 0),
            energy=EnergyBalance(0, 0),
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 0, 1, Resources(78, 0, 25, 0), True, "Build", "h1"),
                BuildingOffer(5, "Fusion plant", 0, 1, Resources(80, 0, 35, 0), True, "Build", "h5"),
            ),
        )
        st = GameState(31, 1, "u", 0.0, (p,))
        before = plan_actions(st, config=StrategyConfig(planner_mode="priority"))
        _ = optimize_plan_actions(st, config=StrategyConfig(planner_mode="ga_nn", ga_seed=10))
        after = plan_actions(st, config=StrategyConfig(planner_mode="priority"))
        self.assertEqual(before, after)


class TestAttackHistoryAndTelemetryIntegration(unittest.TestCase):
    def test_load_attack_history_stats_safe_read_only_and_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            self.assertEqual(load_attack_history_stats(missing), {})

            bad = Path(tmp) / "bad.json"
            bad.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(load_attack_history_stats(bad), {})

            valid_payload = {
                "overall": {
                    "attempts": 2,
                    "wins": 2,
                    "losses": 0,
                    "success_rate": 1.0,
                    "total_loot_spice_eq": 12000.0,
                    "avg_loot_spice_eq": 6000.0,
                    "recency_weighted_loot_spice_eq": 6500.0,
                },
                "per_target": {
                    "01:010:12": {
                        "attempts": 2,
                        "wins": 2,
                        "losses": 0,
                        "success_rate": 1.0,
                        "avg_loot_spice_eq": 6000.0,
                        "recency_weighted_loot_spice_eq": 6500.0,
                        "last_loot_spice_eq": 7000.0,
                    }
                },
                "telemetry_summary": {"points_delta": 500.0, "action_success_rate": 0.95},
            }
            good = Path(tmp) / "attack_history.json"
            raw_txt = json.dumps(valid_payload)
            good.write_text(raw_txt, encoding="utf-8")
            loaded = load_attack_history_stats(good)
            self.assertEqual(loaded["overall"]["attempts"], 2)
            self.assertEqual(loaded["per_target"]["01:010:12"]["last_loot_spice_eq"], 7000.0)
            self.assertEqual(good.read_text(encoding="utf-8"), raw_txt)

    def test_precedence_and_explicit_suppression(self) -> None:
        p = _make_planet(ships={"s6": 20, "s2": 10})
        targets = (
            {
                "target_coords": "01:010:12",
                "owner": "Farmer",
                "status_color": "yellow",
                "pig_iron": 6000,
                "metal": 0,
                "kryptonite": 0,
                "spice": 0,
                "fleet": {},
                "defense": {},
                "is_spacia": False,
                "attack_attempts": 4,
                "attack_wins": 4,
                "attack_losses": 0,
                "attack_success_rate": 1.0,
                "avg_attack_loot_spice_eq": 4500.0,
                "recency_weighted_loot_spice_eq": 5000.0,
                "last_attack_loot_spice_eq": 5500.0,
                "last_attack_outcome": "win",
            },
        )
        st_plain = GameState(31, 1, "u", 1_000_000.0, (p,))
        st = GameState(31, 1, "u", 1_000_000.0, (p,), raid_targets=targets)
        vec_plain = encode_empire_state(st_plain, attack_history_path="")
        vec_from_rt = encode_empire_state(st, attack_history_path="")
        self.assertEqual(len(vec_from_rt), STATE_FEATURE_DIM)
        self.assertNotEqual(vec_from_rt, vec_plain)

        vec_explicit_override = encode_empire_state(
            st,
            raid_stats={"per_target": {"01:010:12": {"attempts": 3, "wins": 0, "losses": 3, "success_rate": 0.0, "last_loot_spice_eq": 0.0}}},
            attack_history_path="",
        )
        self.assertNotEqual(vec_explicit_override, vec_from_rt)

        with tempfile.TemporaryDirectory() as tmp:
            disk = Path(tmp) / "attack_history.json"
            disk.write_text(
                json.dumps(
                    {
                        "overall": {
                            "attempts": 5,
                            "wins": 5,
                            "losses": 0,
                            "success_rate": 1.0,
                            "total_loot_spice_eq": 50000.0,
                            "avg_loot_spice_eq": 10000.0,
                            "recency_weighted_loot_spice_eq": 11000.0,
                        },
                        "telemetry_summary": {"points_delta": 1200.0, "action_success_rate": 0.9},
                    }
                ),
                encoding="utf-8",
            )
            vec_disk = encode_empire_state(st_plain, attack_history_path=disk)
            vec_empty_path = encode_empire_state(st_plain, attack_history_path="")
            self.assertNotEqual(vec_disk, vec_empty_path)
            with mock.patch("spaceinvasion.optimizer.DEFAULT_ATTACK_HISTORY_PATH", disk):
                vec_default_disk = encode_empire_state(st_plain)
                vec_suppressed_by_empty_stats = encode_empire_state(st_plain, raid_stats={})
                vec_suppressed_by_empty_path = encode_empire_state(st_plain, attack_history_path="")
                self.assertEqual(vec_default_disk, vec_disk)
                self.assertEqual(vec_suppressed_by_empty_stats, vec_empty_path)
                self.assertEqual(vec_suppressed_by_empty_path, vec_empty_path)
                self.assertNotEqual(vec_default_disk, vec_suppressed_by_empty_stats)

    def test_raw_raid_history_aggregation_bash_seeding_and_fitness(self) -> None:
        p = _make_planet(
            resources=Resources(200, 200, 200, 200),
            buildings={"b1": 1, "b3": 1, "b5": 2},
            ships={"s6": 30, "s2": 30},
        )
        now_ts = 1_000_000.0
        # Identical moderate spy reports on two yellow targets, plus one light_red already hit 3x in 24h
        targets = (
            {
                "target_coords": "01:010:09",
                "owner": "ProtectedAz",
                "status_color": "light_red",
                "pig_iron": 60000,
                "metal": 30000,
                "kryptonite": 20000,
                "spice": 20000,
                "fleet": {},
                "defense": {},
                "is_spacia": False,
            },
            {
                "target_coords": "01:010:12",
                "owner": "GoodFarm",
                "status_color": "yellow",
                "pig_iron": 12000,
                "metal": 0,
                "kryptonite": 0,
                "spice": 0,
                "fleet": {},
                "defense": {},
                "is_spacia": False,
            },
            {
                "target_coords": "01:010:13",
                "owner": "BadFarm",
                "status_color": "yellow",
                "pig_iron": 12000,
                "metal": 0,
                "kryptonite": 0,
                "spice": 0,
                "fleet": {},
                "defense": {},
                "is_spacia": False,
            },
        )
        raw_history = (
            # 3 non-exempt hits on 01:010:09 -> BashTracker must block a 4th hit!
            {"target_coords": "01:010:09", "attacked_ts": now_ts - 3600, "is_exempt": False, "outcome": "win", "loot": {"spice": 1000}},
            {"target_coords": "01:010:09", "attacked_ts": now_ts - 2400, "is_exempt": False, "outcome": "win", "loot": {"spice": 1000}},
            {"target_coords": "01:010:09", "attacked_ts": now_ts - 1200, "is_exempt": False, "outcome": "win", "loot": {"spice": 1000}},
            # Rich win streak on 01:010:12
            {"target_coords": "01:010:12", "attacked_ts": now_ts - 7200, "is_exempt": True, "outcome": "win", "loot": {"pig_iron": 60000, "spice": 12000}},
            {"target_coords": "01:010:12", "attacked_ts": now_ts - 1800, "is_exempt": True, "outcome": "win", "loot": {"pig_iron": 90000, "spice": 18000}},
            # Repeated losses & dry hauls on 01:010:13
            {"target_coords": "01:010:13", "attacked_ts": now_ts - 5400, "is_exempt": True, "outcome": "loss", "loot": {}},
            {"target_coords": "01:010:13", "attacked_ts": now_ts - 900, "is_exempt": True, "outcome": "loss", "loot": {}},
        )
        st = GameState(31, 1, "u", now_ts, (p,), raid_targets=targets)

        res_bash = simulate_action_sequence(
            st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:09")],
            config=StrategyConfig(allow_pvp_in_az=True),
            raid_history=raw_history,
            attack_history_path="",
        )
        self.assertFalse(res_bash.step_outcomes[0].valid)
        self.assertIn("Bash limit", res_bash.step_outcomes[0].rejection_reason)

        res_good = simulate_action_sequence(
            st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:12")],
            raid_history=raw_history,
            attack_history_path="",
        )
        res_bad = simulate_action_sequence(
            st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:13")],
            raid_history=raw_history,
            attack_history_path="",
        )
        self.assertGreater(
            res_good.final_metrics.simulated_raid_loot_spice_eq,
            res_bad.final_metrics.simulated_raid_loot_spice_eq,
        )

        # Empty 0-resource target yields 0 loot (no phantom loot farming), while low non-zero resources + rich win history lift expectation
        empty_st = GameState(
            31,
            1,
            "u",
            now_ts,
            (p,),
            raid_targets=(
                {
                    "target_coords": "01:010:12",
                    "owner": "GoodFarm",
                    "status_color": "yellow",
                    "pig_iron": 0,
                    "metal": 0,
                    "kryptonite": 0,
                    "spice": 0,
                    "fleet": {},
                    "defense": {},
                    "is_spacia": False,
                },
            ),
        )
        res_empty = simulate_action_sequence(
            empty_st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:12")],
            raid_history=raw_history,
            attack_history_path="",
        )
        self.assertFalse(res_empty.step_outcomes[0].valid)
        self.assertEqual(res_empty.final_metrics.simulated_raid_loot_spice_eq, 0.0)

        low_st = GameState(
            31,
            1,
            "u",
            now_ts,
            (p,),
            raid_targets=(
                {
                    "target_coords": "01:010:12",
                    "owner": "GoodFarm",
                    "status_color": "yellow",
                    "pig_iron": 0,
                    "metal": 0,
                    "kryptonite": 0,
                    "spice": 2000,
                    "fleet": {},
                    "defense": {},
                    "is_spacia": False,
                },
            ),
        )
        res_low = simulate_action_sequence(
            low_st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:12")],
            raid_history=raw_history,
            attack_history_path="",
        )
        self.assertTrue(res_low.step_outcomes[0].valid)
        self.assertGreater(res_low.final_metrics.simulated_raid_loot_spice_eq, 1500.0)

        # Optimizer prefers 01:010:12 over equally-rich 01:010:13 and reports historical stats in reason
        rich_pair = tuple(
            {
                **t,
                "pig_iron": 60000,
                "metal": 30000,
                "kryptonite": 20000,
                "spice": 12000,
            }
            for t in targets[1:]
        )
        st_two = GameState(31, 1, "u", now_ts, (p,), raid_targets=rich_pair)
        summary = run_optimizer(
            st_two,
            config=StrategyConfig(allow_raids=True, allow_research=False, allow_shipyard=False, playstyle="fleeter"),
            population_size=8,
            generations=3,
            horizon_steps=2,
            seed=21,
            raid_history=raw_history,
            attack_history_path="",
        )
        atk_acts = [a for a in summary.actions if a.kind == "ATTACK"]
        self.assertTrue(atk_acts)
        self.assertIn("01:010:12", atk_acts[0].target_name)
        self.assertIn("hist ", atk_acts[0].reason)

    def test_zero_second_instant_repeat_raid_exploit_blocked(self) -> None:
        p = _make_planet(
            resources=Resources(0, 0, 0, 0),
            buildings={},
            ships={"s6": 20, "s2": 20},
        )
        target = {
            "target_coords": "01:010:12",
            "owner": "YellowFarm",
            "status_color": "yellow",
            "pig_iron": 120000,
            "metal": 60000,
            "kryptonite": 30000,
            "spice": 20000,
            "fleet": {},
            "defense": {},
            "is_spacia": False,
        }
        st = GameState(31, 1, "u", 1_000_000.0, (p,), raid_targets=(target,))
        res = simulate_action_sequence(
            st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:12")] * 2,
            config=StrategyConfig(allow_raids=True),
            attack_history_path="",
        )
        self.assertTrue(res.step_outcomes[0].valid)
        self.assertTrue(res.step_outcomes[1].valid)
        self.assertGreaterEqual(res.step_outcomes[0].duration_s, 1800.0)
        self.assertGreaterEqual(res.step_outcomes[1].start_t, res.step_outcomes[0].finish_t - 1e-6)
        self.assertTrue(res.step_outcomes[0].executable_at_t0)
        self.assertFalse(res.step_outcomes[1].executable_at_t0)

    def test_run_summary_json_telemetry_alias_and_last_summary(self) -> None:
        self.assertIs(run_ga_nn_optimizer, run_optimizer)
        p = _make_planet(
            resources=Resources(1500, 1500, 1500, 1500),
            buildings={"b1": 2, "b3": 2, "b5": 3},
        )
        st = GameState(31, 1, "u", 1_000_000.0, (p,))
        stats = {
            "overall": {
                "attempts": 3,
                "wins": 2,
                "losses": 1,
                "success_rate": 2.0 / 3.0,
                "total_loot_spice_eq": 9000.0,
                "avg_loot_spice_eq": 3000.0,
                "recency_weighted_loot_spice_eq": 3200.0,
            },
            "per_target": {},
            "telemetry_summary": {"points_delta": 250.0, "action_success_rate": 0.9},
        }
        summary = run_ga_nn_optimizer(
            st,
            config=StrategyConfig(planner_mode="ga_nn"),
            population_size=6,
            generations=3,
            horizon_steps=4,
            seed=17,
            raid_stats=stats,
            attack_history_path="",
        )
        self.assertIsInstance(summary, OptimizerRunSummary)
        self.assertEqual(summary.generations, 3)
        self.assertEqual(summary.population_size, 6)
        self.assertGreater(summary.nn_evaluations, 0)
        self.assertEqual(summary.baseline_fitness, summary.initial_best_fitness)
        self.assertEqual(summary.best_fitness, summary.final_best_fitness)
        self.assertTrue(math.isfinite(summary.improvement_pct))
        self.assertEqual(len(summary.generation_history), 3)
        self.assertEqual(summary.attack_history_stats.get("overall", {}).get("attempts"), 3)

        d = summary.to_dict()
        dumped = json.dumps(d)
        self.assertIn("nn_evaluations", dumped)
        last = get_last_optimizer_summary()
        self.assertEqual(last, d)


class TestIteration2SimulatorAndPlannerRegressions(unittest.TestCase):
    def test_solve_planet_reach_tiny_switch_h_crossing(self) -> None:
        # b6 L5 burns 20 Spice/h; spice starts at 1e-10 (switch_h = 5e-12 <= 1e-9), zero post-switch Pig Iron production
        p = _make_planet(
            resources=Resources(pig_iron=0, metal=0, kryptonite=0, spice=0),
            energy=EnergyBalance(80, 40),
            buildings={"b1": 5, "b6": 5},
        )
        from spaceinvasion.optimizer import _SimState, _advance_until_affordable

        sim = _SimState.from_game_state(GameState(31, 1, "u", 0.0, (p,)), attack_history_path="")
        sim.planet_by_id[p.planet_id].res_f["spice"] = 1e-10  # Crosses switch_h in 5e-12h (<= 1e-9)
        ok, waited, _ = _advance_until_affordable(sim, p.planet_id, Resources(pig_iron=200), max_clock_s=10.0)
        self.assertFalse(ok)
        self.assertEqual(waited, 0.0)

    def test_b5_plus_b6_dry_spice_no_double_collapse(self) -> None:
        # Both b5 L5 and b6 L5 built with 0 Spice: only b6 collapses, b5 keeps powering mine!
        p = _make_planet(
            resources=Resources(0, 0, 0, 0),
            energy=EnergyBalance(100, 40),
            buildings={"b1": 5, "b5": 5, "b6": 5},
        )
        res = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p,)), (), horizon_hours=1.0, attack_history_path="")
        self.assertGreater(res.final_metrics.total_energy_net, 0)
        self.assertGreater(res.final_metrics.hourly_pig_iron_net, 100.0)

    def test_thermo_oil_and_thermosolar_fixed_point_convergence(self) -> None:
        # b4=12 + b6=6 with 0 Spice and b5=0: no bootstrap fusion power, must converge cleanly to 0 production
        p = _make_planet(
            resources=Resources(0, 0, 0, 0),
            energy=EnergyBalance(0, 120),
            buildings={"b1": 5, "b4": 12, "b6": 6},
        )
        res = simulate_action_sequence(GameState(31, 1, "u", 0.0, (p,)), (), horizon_hours=1.0, attack_history_path="")
        self.assertLessEqual(res.final_metrics.total_energy_net, 0)
        self.assertAlmostEqual(res.final_metrics.hourly_pig_iron_net, 0.0, delta=0.1)

    def test_throttle_rejects_b5_and_no_cumulative_rounding_drift(self) -> None:
        p = _make_planet(
            resources=Resources(5000, 5000, 5000, 5000),
            buildings={"b1": 5, "b2": 4, "b5": 5},
        )
        st = GameState(31, 1, "u", 0.0, (p,))
        res_b5 = simulate_action_sequence(
            st,
            [
                SimAction("THROTTLE_BUILDING", p.planet_id, target_id=5, quantity=50),
                SimAction("THROTTLE_BUILDING", p.planet_id, target_id="b5", quantity=50),
            ],
            attack_history_path="",
        )
        self.assertFalse(res_b5.step_outcomes[0].valid)
        self.assertFalse(res_b5.step_outcomes[1].valid)

        res_b2 = simulate_action_sequence(
            st,
            [
                SimAction("THROTTLE_BUILDING", p.planet_id, target_id="b2", quantity=50),
                SimAction("THROTTLE_BUILDING", p.planet_id, target_id="b2", quantity=0),
                SimAction("THROTTLE_BUILDING", p.planet_id, target_id="b2", quantity=50),
            ],
            attack_history_path="",
        )
        self.assertTrue(res_b2.step_outcomes[0].valid)
        self.assertTrue(res_b2.step_outcomes[1].valid)
        self.assertTrue(res_b2.step_outcomes[2].valid)
        res_single = simulate_action_sequence(
            st,
            [SimAction("THROTTLE_BUILDING", p.planet_id, target_id="b2", quantity=50)],
            attack_history_path="",
        )
        self.assertEqual(res_b2.final_metrics.total_energy_net, res_single.final_metrics.total_energy_net)

    def test_research_waits_for_queued_b8_l1(self) -> None:
        p = _make_planet(
            resources=Resources(20000, 20000, 20000, 20000),
            buildings={"b8": 0},
        )
        q = QueueJob(category="building", building_id=8, name="Research lab", target_level=1, remaining_text="00:05:00")
        st = GameState(31, 1, "u", 0.0, (p,), active_queues=(q,))
        res = simulate_action_sequence(
            st,
            [SimAction("RESEARCH", p.planet_id, target_id=2)],
            attack_history_path="",
        )
        self.assertTrue(res.step_outcomes[0].valid)
        self.assertGreaterEqual(res.step_outcomes[0].start_t, 299.0)

    def test_queue_job_without_building_id_and_active_research_materialization(self) -> None:
        p = _make_planet(
            resources=Resources(50000, 50000, 50000, 50000),
            buildings={"b1": 3, "b5": 1, "b8": 2},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 4, 5, Resources(400, 0, 100, 0), True, "Build", "h1"),
            ),
        )
        queues = (
            QueueJob(category="research", name="Energy technology (L2) 00:02:00", target_level=2, remaining_text="00:02:00"),
            QueueJob(category="building", building_id=None, name="Pig iron mine (L4) 00:05:00", target_level=4, remaining_text="00:05:00"),
            QueueJob(category="building", building_id=5, name="Fusion plant", target_level=2, remaining_text="00:00:00"),
        )
        st = GameState(
            31,
            1,
            "u",
            0.0,
            (p,),
            researches={"r2": 1},
            research_offers=(
                ResearchOffer(2, "r2", "Energy technology", 2, 3, Resources(0, 100, 200, 100), True, "Research", "hr2"),
            ),
            active_queues=queues,
        )
        sim_res = simulate_action_sequence(
            st,
            [
                SimAction("BUILD", p.planet_id, target_id="b1"),
                SimAction("RESEARCH", p.planet_id, target_id=2),
            ],
            attack_history_path="",
        )
        self.assertTrue(all(o.valid for o in sim_res.step_outcomes))
        self.assertEqual(sim_res.final_state.planets[0].building_level(1), 5)
        self.assertEqual(sim_res.final_state.researches.get("r2"), 3)

        acts = _materialize_planned_actions(
            st,
            [
                SimAction("BUILD", p.planet_id, target_id="b1"),
                SimAction("RESEARCH", p.planet_id, target_id=2),
            ],
            StrategyConfig(),
            0.9,
            attack_history_stats={},
        )
        b_acts = [a for a in acts if a.target_id == 1]
        r_acts = [a for a in acts if a.kind == "RESEARCH"]
        self.assertTrue(b_acts and b_acts[0].target_level == 5)
        self.assertTrue(r_acts and r_acts[0].target_level == 3)

    def test_full_fields_blocks_build_and_uses_s9_on_deficit(self) -> None:
        full_act = _make_planet(
            planet_id=101,
            fields_used=230,
            fields_max=230,
            resources=Resources(50000, 50000, 50000, 50000),
            energy=EnergyBalance(50, 120),
            buildings={"b5": 3, "b9": 1},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 3, 4, Resources(100, 0, 50, 0), True, "Build", "h5"),
            ),
            ship_offers=(
                UnitOffer("ship", 9, "Solar cells", 0, Resources(0, 1200, 1500, 400), True, None, "Build"),
            ),
        )
        full_remote = _make_planet(
            planet_id=102,
            name="Colony",
            coords="01:010:06",
            fields_used=200,
            fields_max=200,
            resources=Resources(50000, 50000, 50000, 50000),
            energy=EnergyBalance(20, 80),
            buildings={"b5": 2, "b9": 0},
        )
        st = GameState(31, 1, "u", 0.0, (full_act, full_remote), active_planet_id=101)
        acts = _materialize_planned_actions(
            st,
            [
                SimAction("BUILD", 101, target_id=5),
                SimAction("BUILD", 102, target_id=5),
            ],
            StrategyConfig(allow_multi_planet=True, allow_shipyard=True),
            0.9,
            attack_history_stats={},
        )
        self.assertFalse(any(a.kind == "BUILD" and a.executable_now for a in acts))
        s9_acts = [a for a in acts if a.kind == "BUILD_SHIP" and a.target_id == 9]
        self.assertTrue(s9_acts and s9_acts[0].executable_now)

    def test_remote_resource_accounting_and_p1_stale_offer_target_level(self) -> None:
        act_p = _make_planet(
            planet_id=101,
            resources=Resources(5000, 5000, 5000, 5000),
            energy=EnergyBalance(10, 100),  # Negative energy triggers P1 check
            buildings={"b5": 3},
            # Stale offer still points at L3 even though b5 L3 is already built!
            building_offers=(
                BuildingOffer(5, "Fusion plant", 2, 3, Resources(100, 0, 50, 0), True, "Build", "stale"),
            ),
        )
        remote_p = _make_planet(
            planet_id=102,
            name="Colony",
            coords="01:010:06",
            resources=Resources(200, 0, 100, 0),  # Enough for at most ONE cheap b1 L1/L2, not two
            energy=EnergyBalance(200, 50),
            buildings={"b1": 1, "b3": 0},
        )
        st = GameState(31, 1, "u", 0.0, (act_p, remote_p), active_planet_id=101)
        acts = _materialize_planned_actions(
            st,
            [
                SimAction("BUILD", 102, target_id=1),
                SimAction("BUILD", 102, target_id=3),
            ],
            StrategyConfig(allow_multi_planet=True),
            0.9,
            attack_history_stats={},
        )
        p1_f5 = [a for a in acts if a.planet_id == 101 and a.target_id == 5]
        self.assertTrue(p1_f5 and not p1_f5[0].executable_now)
        remote_exec_builds = [a for a in acts if a.planet_id == 102 and a.kind == "BUILD" and a.executable_now]
        self.assertLessEqual(len(remote_exec_builds), 1)

    def test_raid_min_loot_enforced_ga_min_bounds_and_nn_nan_safety(self) -> None:
        p = _make_planet(
            resources=Resources(1000, 1000, 1000, 1000),
            buildings={"b1": 2},
            ships={"s6": 5},
        )
        tiny_target = {
            "target_coords": "01:010:19",
            "owner": "TinyFarm",
            "status_color": "yellow",
            "pig_iron": 600,
            "metal": 0,
            "kryptonite": 0,
            "spice": 0,
            "fleet": {},
            "defense": {},
            "is_spacia": False,
        }
        st = GameState(31, 1, "u", 1_000_000.0, (p,), raid_targets=(tiny_target,))
        res_tiny = simulate_action_sequence(
            st,
            [SimAction("ATTACK", p.planet_id, target_coords="01:010:19")],
            config=StrategyConfig(raid_min_loot_spice_eq=1500.0),
            attack_history_path="",
        )
        self.assertFalse(res_tiny.step_outcomes[0].valid)

        p3_acts = _materialize_planned_actions(
            st,
            [],
            StrategyConfig(allow_raids=False, raid_min_loot_spice_eq=1500.0),
            0.9,
            attack_history_stats={},
        )
        self.assertFalse(any(a.kind == "ATTACK" for a in p3_acts))

        min_summary = run_optimizer(
            st,
            population_size=4,
            generations=1,
            horizon_steps=1,
            seed=9,
            attack_history_path="",
        )
        self.assertEqual(min_summary.population_size, 4)
        self.assertEqual(min_summary.generations, 1)
        self.assertEqual(len(min_summary.generation_history), 1)

        net = PurePythonMLP(seed=3)
        x = [0.2] * STATE_FEATURE_DIM
        stats = net.train_on_rollouts([(x, float("nan")), (x, float("inf"))], epochs=2)
        self.assertTrue(math.isfinite(stats.initial_loss))
        self.assertTrue(math.isfinite(stats.final_loss))

    def test_p1_full_fields_blocks_build_b5_and_warehouse_on_active_and_remote(self) -> None:
        full_act = _make_planet(
            planet_id=101,
            fields_used=230,
            fields_max=230,
            resources=Resources(95_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(40, 120),
            buildings={"b1": 10, "b5": 5, "b9": 0, "b11": 5},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 5, 6, Resources(200, 0, 100, 0), True, "Build", "h5"),
                BuildingOffer(11, "Pig iron warehouse", 5, 6, Resources(500, 200, 0, 0), True, "Build", "h11"),
            ),
        )
        full_remote = _make_planet(
            planet_id=102,
            name="ColonyFull",
            coords="01:010:06",
            fields_used=200,
            fields_max=200,
            resources=Resources(95_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(30, 100),
            buildings={"b1": 8, "b5": 4, "b9": 0, "b11": 4},
        )
        st = GameState(31, 1, "u", 0.0, (full_act, full_remote), active_planet_id=101)
        acts = optimize_plan_actions(
            st,
            config=StrategyConfig(allow_multi_planet=True, allow_shipyard=True, ga_population_size=6, ga_generations=2, ga_horizon_steps=2, ga_seed=11),
            attack_history_path="",
        )
        self.assertFalse(any(a.kind == "BUILD" and a.target_id in (5, 11, 12, 13, 14) for a in acts))
        self.assertFalse(any(a.kind == "BUILD" and a.executable_now for a in acts))

    def test_optimize_plan_actions_ignores_unknown_kwargs(self) -> None:
        p = _make_planet(
            resources=Resources(5000, 3000, 3000, 1000),
            buildings={"b1": 2, "b3": 2, "b5": 3},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 2, 3, Resources(200, 0, 50, 0), True, "Build", "h1"),
            ),
        )
        st = GameState(31, 1, "u", 0.0, (p,))
        acts = optimize_plan_actions(
            st,
            scanned_systems={(1, 10)},
            throttle_cooldowns={101: 9999.0},
            now_ts=12345.0,
            unknown_extra=123,
            population_size=4,
            generations=1,
            horizon_steps=1,
            seed=5,
            attack_history_path="",
        )
        self.assertIsInstance(acts, list)


class TestIteration3Wave2Regressions(unittest.TestCase):
    def test_raid_expectation_monotonic_no_phantom_loot_and_depletion(self) -> None:
        from spaceinvasion.optimizer import _compute_effective_raid_expectation

        rich_stats = {
            "overall": {
                "attempts": 5,
                "wins": 5,
                "losses": 0,
                "success_rate": 1.0,
                "avg_loot_spice_eq": 15000.0,
                "recency_weighted_loot_spice_eq": 18000.0,
                "last_attack": {"loot_spice_eq": 20000.0, "outcome": "win"},
            },
            "per_target": {
                "01:010:09": {
                    "attempts": 5,
                    "wins": 5,
                    "losses": 0,
                    "success_rate": 1.0,
                    "avg_loot_spice_eq": 15000.0,
                    "recency_weighted_loot_spice_eq": 18000.0,
                    "total_loot_spice_eq": 75000.0,
                    "last_loot_spice_eq": 20000.0,
                    "last_outcome": "win",
                }
            },
        }
        e0 = _compute_effective_raid_expectation(0.0, 50000, "01:010:09", rich_stats)[0]
        e1 = _compute_effective_raid_expectation(1.0, 50000, "01:010:09", rich_stats)[0]
        e100 = _compute_effective_raid_expectation(100.0, 50000, "01:010:09", rich_stats)[0]
        e_dep = _compute_effective_raid_expectation(1000.0, 50000, "01:010:09", rich_stats, target_depleted=True)[0]
        self.assertEqual(e0, 0.0)
        self.assertEqual(e_dep, 0.0)
        self.assertLessEqual(e0, e1)
        self.assertLess(e1, 10.0)  # No hallucinated 8,817 SpEq on 1-Spice target
        self.assertLess(e1, e100)

        p = _make_planet(
            planet_id=1,
            coords="01:010:01",
            fields_used=230,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 5000),
            buildings={"b1": 5, "b2": 3, "b3": 3, "b4": 3, "b5": 5},
            ships={"s1": 50, "s4": 30},
        )
        hist = [
            {
                "target_coords": "01:010:09",
                "attacked_ts": 1_699_900_000.0 + i * 3600.0,
                "is_exempt": True,
                "outcome": "win",
                "loot": {"spice": 25000},
            }
            for i in range(5)
        ]

        # Sequential depletion on 2,000 Spice stops yielding loot once exhausted
        gs_drain = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p,),
            raid_targets=({"coords": "01:010:09", "owner": "Small", "status_color": "yellow", "resources": {"spice": 2000}},),
        )
        res_drain = simulate_action_sequence(
            gs_drain,
            [SimAction("ATTACK", 1, target_coords="01:010:09")] * 15,
            config=StrategyConfig(allow_raids=True, raid_min_loot_spice_eq=0.0),
            raid_history=hist,
            attack_history_path="",
        )
        self.assertLessEqual(res_drain.final_metrics.simulated_raid_loot_spice_eq, 2000.0)
        self.assertEqual(res_drain.step_outcomes[-1].raid_loot_spice_eq, 0.0)

        # GA prefers real 4k-each target over empty 0-resource target with rich history
        gs_pref = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p,),
            raid_targets=(
                {"coords": "01:010:09", "owner": "Empty", "status_color": "yellow", "resources": {}},
                {
                    "coords": "01:010:12",
                    "owner": "Rich",
                    "status_color": "yellow",
                    "resources": {"pig_iron": 4000, "metal": 4000, "kryptonite": 4000, "spice": 4000},
                },
            ),
        )
        summ = run_optimizer(
            gs_pref,
            config=StrategyConfig(
                allow_raids=True,
                allow_research=False,
                allow_shipyard=False,
                planner_mode="ga_nn",
                ga_population_size=12,
                ga_generations=6,
                ga_horizon_steps=4,
                ga_seed=42,
            ),
            raid_history=hist,
            attack_history_path="",
        )
        atks = [s.target_coords for s in summ.best_chromosome if s.kind == "ATTACK"]
        self.assertTrue(atks)
        self.assertTrue(all(c == "01:010:12" for c in atks))

    def test_bash_tracker_clock_alignment_sorting_skew_and_multi_planet_cap(self) -> None:
        p1 = _make_planet(planet_id=1, coords="01:010:01", ships={"s1": 50, "s4": 30})
        p2 = _make_planet(planet_id=2, name="Colony", coords="01:010:02", ships={"s1": 50, "s4": 30})
        lr = {
            "coords": "01:010:09",
            "owner": "Active",
            "status_color": "light_red",
            "resources": {"pig_iron": 50000, "metal": 50000, "kryptonite": 50000, "spice": 50000},
        }

        # 1. Synthetic history (1.7e9) with state.timestamp=0.0 blocks both sim and P3 advisory
        h_syn = [
            {"target_coords": "01:010:09", "attacked_ts": 1_700_000_000.0 + i * 100.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}}
            for i in range(3)
        ]
        st_syn0 = GameState(31, 1, "u", 0.0, (p1,), raid_targets=(lr,))
        self.assertFalse(
            simulate_action_sequence(
                st_syn0,
                [SimAction("ATTACK", 1, target_coords="01:010:09")],
                config=StrategyConfig(allow_raids=True, allow_pvp_in_az=True),
                raid_history=h_syn,
                attack_history_path="",
            ).step_outcomes[0].valid
        )
        p3_syn = optimize_plan_actions(
            st_syn0,
            config=StrategyConfig(allow_raids=False, allow_pvp_in_az=True, ga_population_size=6, ga_generations=2, ga_seed=1),
            raid_history=h_syn,
            attack_history_path="",
        )
        self.assertFalse(any(a.kind == "ATTACK" for a in p3_syn))

        # 2. Recent wall-clock raids 30h ago with state.timestamp=0.0 are expired in BOTH sim and P3
        wall_now = time.time()
        h_30h = [
            {"target_coords": "01:010:09", "attacked_ts": wall_now - 30 * 3600 + i * 100.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}}
            for i in range(3)
        ]
        self.assertTrue(
            simulate_action_sequence(
                st_syn0,
                [SimAction("ATTACK", 1, target_coords="01:010:09")],
                config=StrategyConfig(allow_raids=True, allow_pvp_in_az=True),
                raid_history=h_30h,
                attack_history_path="",
            ).step_outcomes[0].valid
        )
        p3_30h = optimize_plan_actions(
            st_syn0,
            config=StrategyConfig(allow_raids=False, allow_pvp_in_az=True, ga_population_size=6, ga_generations=2, ga_seed=1),
            raid_history=h_30h,
            attack_history_path="",
        )
        self.assertTrue(any(a.kind == "ATTACK" for a in p3_30h))

        # 3. Unsorted history (2 recent + 1 expired 25h ago) does not corrupt BashTracker._prune deque
        h_unsorted = [
            {"target_coords": "01:010:09", "attacked_ts": wall_now - 100.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}},
            {"target_coords": "01:010:09", "attacked_ts": wall_now - 90000.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}},
            {"target_coords": "01:010:09", "attacked_ts": wall_now - 200.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}},
        ]
        p3_unsorted = optimize_plan_actions(
            st_syn0,
            config=StrategyConfig(allow_raids=False, allow_pvp_in_az=True, ga_population_size=6, ga_generations=2, ga_seed=1),
            raid_history=h_unsorted,
            attack_history_path="",
        )
        self.assertTrue(any(a.kind == "ATTACK" for a in p3_unsorted))

        # 4. Negative/zero attacked_ts ignored; +27.7h future skew does not wipe valid 24h entries
        h_neg = [
            {"target_coords": "01:010:09", "attacked_ts": -1000.0 * (i + 1), "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}}
            for i in range(3)
        ]
        st_now = GameState(31, 1, "u", 1_700_000_000.0, (p1,), raid_targets=(lr,))
        self.assertTrue(
            simulate_action_sequence(
                st_now,
                [SimAction("ATTACK", 1, target_coords="01:010:09")],
                config=StrategyConfig(allow_raids=True, allow_pvp_in_az=True),
                raid_history=h_neg,
                attack_history_path="",
            ).step_outcomes[0].valid
        )
        h_fut = [
            {"target_coords": "01:010:09", "attacked_ts": 1_699_996_400.0 + i * 100.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}}
            for i in range(3)
        ] + [{"target_coords": "01:010:09", "attacked_ts": 1_700_100_000.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}}]
        self.assertFalse(
            simulate_action_sequence(
                st_now,
                [SimAction("ATTACK", 1, target_coords="01:010:09")],
                config=StrategyConfig(allow_raids=True, allow_pvp_in_az=True),
                raid_history=h_fut,
                attack_history_path="",
            ).step_outcomes[0].valid
        )

        # 5. Multi-planet ATTACK materialization caps executable_now at 3/24h
        from spaceinvasion.optimizer import _resolve_complete_attack_history

        h_two = [
            {"target_coords": "01:010:09", "attacked_ts": 1_699_999_000.0 + i * 100.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 5000}}
            for i in range(2)
        ]
        st_mp = GameState(31, 1, "u", 1_700_000_000.0, (p1, p2), active_planet_id=1, raid_targets=(lr,))
        stats_mp = _resolve_complete_attack_history(st_mp, raid_history=h_two, attack_history_path="")
        acts_mp = _materialize_planned_actions(
            st_mp,
            [SimAction("ATTACK", 1, target_coords="01:010:09"), SimAction("ATTACK", 2, target_coords="01:010:09")],
            StrategyConfig(allow_raids=True, allow_multi_planet=True, allow_pvp_in_az=True),
            0.85,
            attack_history_stats=stats_mp,
        )
        exec_atks = [a for a in acts_mp if a.kind == "ATTACK" and a.executable_now]
        self.assertEqual(len(exec_atks), 1)

    def test_unseen_attempts_zero_not_penalized(self) -> None:
        from spaceinvasion.optimizer import _compute_effective_raid_expectation, _normalize_target_stat_entry

        self.assertIsNone(_normalize_target_stat_entry({"attempts": 0, "wins": 0, "losses": 0, "success_rate": 0.0}))
        exp_val, eff_p, hist = _compute_effective_raid_expectation(
            10000.0,
            50000,
            "01:010:09",
            {"overall": {"attempts": 0}, "per_target": {"01:010:09": {"attempts": 0, "wins": 0, "losses": 0, "success_rate": 0.0}}},
        )
        self.assertEqual(exp_val, 10000.0)
        self.assertEqual(eff_p, 1.0)
        self.assertIsNone(hist)

    def test_corrupt_values_non_finite_speedup_overflow_underflow_and_partial_overlay(self) -> None:
        from spaceinvasion.optimizer import _aggregate_raw_raid_history, _merge_stats_layers

        p = _make_planet(ships={"s6": 5})
        for bad_stats in (
            {"overall": {"attempts": "bad_int", "success_rate": "nan"}},
            {"overall": {"attempts": float("inf"), "avg_loot_spice_eq": float("nan")}},
            {"per_target": {"01:010:12": {"attempts": float("inf"), "last_loot": {"spice": "inf"}}}},
        ):
            vec = encode_empire_state(GameState(31, 1, "u", 1000.0, (p,)), raid_stats=bad_stats, attack_history_path="")
            self.assertEqual(len(vec), STATE_FEATURE_DIM)
            self.assertTrue(all(math.isfinite(x) for x in vec))

        with tempfile.TemporaryDirectory() as tmp:
            list_p = Path(tmp) / "list.json"
            list_p.write_text('[{"target_coords":"01:010:09","loot":{"spice":NaN},"loot_spice_eq":Infinity}]', encoding="utf-8")
            loaded = load_attack_history_stats(list_p)
            json.dumps(loaded, allow_nan=False)

        for spd in (float("nan"), float("inf")):
            st_spd = GameState(31, 1, "u", 1000.0, (p,), speedup_factor=spd)
            sim_res = simulate_action_sequence(st_spd, (), horizon_hours=1.0, attack_history_path="")
            self.assertTrue(math.isfinite(sim_res.composite_growth_score))
            summ = run_optimizer(st_spd, population_size=4, generations=1, horizon_steps=1, seed=1, attack_history_path="")
            self.assertTrue(math.isfinite(summ.best_fitness))

        net = PurePythonMLP(seed=2)
        t_big = net.train_on_rollouts([([0.2] * STATE_FEATURE_DIM, 1e200)], epochs=2)
        self.assertTrue(math.isfinite(t_big.initial_loss))
        self.assertTrue(math.isfinite(t_big.final_loss))

        # Ancient >5.8y raid uses order_weights fallback instead of 0.0 underflow
        ancient = _aggregate_raw_raid_history(
            [{"target_coords": "01:010:09", "attacked_ts": 1000.0, "is_exempt": True, "outcome": "win", "loot": {"spice": 5000}}],
            now_ts=1_700_000_000.0,
        )
        self.assertEqual(ancient["overall"]["recency_weighted_loot_spice_eq"], 5000.0)

        # Partial per_target overlay without overall re-synthesizes overall and strips keys
        merged = _merge_stats_layers(
            {
                "overall": {"attempts": 8, "wins": 1, "losses": 7, "success_rate": 0.125},
                "per_target": {"01:010:12": {"attempts": 8, "wins": 1, "losses": 7, "success_rate": 0.125}},
            },
            {
                "per_target": {
                    " 01:010:12 ": {
                        "attempts": 8,
                        "wins": 8,
                        "losses": 0,
                        "success_rate": 1.0,
                        "total_loot_spice_eq": 80000.0,
                        "avg_loot_spice_eq": 10000.0,
                    }
                }
            },
            now_ts=1000.0,
        )
        self.assertEqual(list(merged["per_target"].keys()), ["01:010:12"])
        self.assertEqual(merged["overall"]["wins"], 8)
        self.assertEqual(merged["overall"]["success_rate"], 1.0)

    def test_m3_raids_plus_shipyard_emits_executable_attack_and_state_raid_stats_fallback(self) -> None:
        # Mirrors test_r1_a3_t4: allow_raids=True + allow_shipyard=True must emit executable ATTACK & beat no-raids
        p = _make_planet(
            resources=Resources(10_000, 10_000, 8_000, 4_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b2": 4, "b3": 5, "b4": 3, "b5": 8, "b7": 3, "b9": 4},
            ships={"s4": 15, "s1": 5},
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 15, Resources(1500, 0, 500, 0), True, "ship[4]"),
                UnitOffer("ship", 1, "Small transport", 5, Resources(0, 3000, 3000, 0), True, "ship[1]"),
            ),
        )
        raid_target = {
            "coords": "01:010:04",
            "owner": "SleepyOne",
            "planet_name": "IdleLoot",
            "status_color": "yellow",
            "is_inactive": True,
            "is_spacia": False,
            "resources": {"pig_iron": 50_000, "metal": 50_000, "kryptonite": 20_000, "spice": 10_000},
            "fleet": {},
            "defense": {},
        }
        st = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p,),
            researches={"r2": 4, "r3": 4, "r4": 4},
            raid_targets=(raid_target,),
        )
        cfg_raids = StrategyConfig(
            planner_mode="ga_nn",
            playstyle="fleeter",
            allow_raids=True,
            allow_shipyard=True,
            ga_seed=42,
            ga_generations=10,
            ga_population_size=24,
        )
        cfg_no_raids = StrategyConfig(
            planner_mode="ga_nn",
            playstyle="fleeter",
            allow_raids=False,
            allow_shipyard=True,
            ga_seed=42,
            ga_generations=10,
            ga_population_size=24,
        )
        s_raids = run_optimizer(st, config=cfg_raids, attack_history_path="")
        s_no_raids = run_optimizer(st, config=cfg_no_raids, attack_history_path="")
        self.assertTrue(any(a.kind == "ATTACK" and a.executable_now for a in s_raids.actions))
        self.assertFalse(any(a.kind == "ATTACK" and a.executable_now for a in s_no_raids.actions))
        self.assertGreater(s_raids.final_best_fitness, s_no_raids.final_best_fitness)

        # state.raid_stats populates Layer 2 without suppressing Layer 4 when empty
        with tempfile.TemporaryDirectory() as tmp:
            disk = Path(tmp) / "attack_history.json"
            disk.write_text(
                json.dumps(
                    {
                        "overall": {"attempts": 7, "wins": 7, "losses": 0, "success_rate": 1.0, "avg_loot_spice_eq": 9000.0},
                        "per_target": {"01:010:04": {"attempts": 7, "wins": 7, "losses": 0, "success_rate": 1.0, "last_loot_spice_eq": 9500.0}},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("spaceinvasion.optimizer.DEFAULT_ATTACK_HISTORY_PATH", disk):
                st_empty_stats = GameState(31, 1, "u", 1_700_000_000.0, (p,), raid_stats={})
                summ_l4 = run_optimizer(st_empty_stats, config=cfg_no_raids, population_size=6, generations=1)
                self.assertEqual(summ_l4.attack_history_stats["overall"]["attempts"], 7)

                summ_supp = run_optimizer(st_empty_stats, config=cfg_no_raids, population_size=6, generations=1, raid_stats={})
                self.assertEqual(summ_supp.attack_history_stats["overall"]["attempts"], 0)

                st_l2 = GameState(
                    31,
                    1,
                    "u",
                    1_700_000_000.0,
                    (p,),
                    raid_stats={"overall": {"attempts": 3, "wins": 2, "losses": 1, "success_rate": 0.6667}},
                )
                summ_l2 = run_optimizer(st_l2, config=cfg_no_raids, population_size=6, generations=1)
                self.assertEqual(summ_l2.attack_history_stats["overall"]["attempts"], 3)

    def test_m3_raids_plus_shipyard_aligned_across_miner_10x_speedup_and_target_ordering(self) -> None:
        from spaceinvasion.optimizer import _make_baseline_raid_and_economy, _SimState

        res_prereqs = {"r2": 4, "r3": 4, "r4": 4}
        p = _make_planet(
            temperature=31,
            energy=EnergyBalance(244, 134),
            buildings={"b1": 5, "b2": 4, "b3": 5, "b4": 3, "b5": 8, "b7": 3, "b9": 4},
            ships={"s4": 15, "s1": 5},
            resources=Resources(10_000, 10_000, 8_000, 4_000),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 15, Resources(0, 4000, 2000, 100), True, "ship[4]"),
                UnitOffer("ship", 1, "Small transport", 5, Resources(0, 3000, 3000, 0), True, "ship[1]"),
            ),
        )
        raid_target = {
            "coords": "01:010:04",
            "owner": "SleepyOne",
            "planet_name": "IdleLoot",
            "status_color": "yellow",
            "is_inactive": True,
            "is_spacia": False,
            "resources": {"pig_iron": 50_000, "metal": 50_000, "kryptonite": 20_000, "spice": 10_000},
            "fleet": {},
            "defense": {},
        }
        # Challenger 2 counterexample seeds on miner (pop=8 seed=77, pop=12 seed=19, pop=24 seed=42)
        st_1x = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p,),
            researches=res_prereqs,
            raid_targets=(raid_target,),
            speedup_factor=1.0,
        )
        for pop, seed in ((8, 77), (12, 19), (24, 42)):
            cr = StrategyConfig(
                planner_mode="ga_nn",
                playstyle="miner",
                allow_raids=True,
                allow_shipyard=True,
                ga_seed=seed,
                ga_generations=10,
                ga_population_size=pop,
            )
            cn = StrategyConfig(
                planner_mode="ga_nn",
                playstyle="miner",
                allow_raids=False,
                allow_shipyard=True,
                ga_seed=seed,
                ga_generations=10,
                ga_population_size=pop,
            )
            sr = run_optimizer(st_1x, config=cr, attack_history_path="")
            sn = run_optimizer(st_1x, config=cn, attack_history_path="")
            self.assertTrue(any(a.kind == "ATTACK" and a.executable_now for a in sr.actions))
            self.assertGreater(sr.final_best_fitness, sn.final_best_fitness)

        # 10x, 20x, and 50x speedups across all 3 playstyles emit executable ATTACK & beat allow_raids=False
        for spd in (10.0, 20.0, 50.0):
            st_spd = GameState(
                31,
                1,
                "u",
                1_700_000_000.0,
                (p,),
                researches=res_prereqs,
                raid_targets=(raid_target,),
                speedup_factor=spd,
            )
            for ps in ("fleeter", "hybrid", "miner"):
                cr = StrategyConfig(
                    planner_mode="ga_nn",
                    playstyle=ps,
                    allow_raids=True,
                    allow_shipyard=True,
                    ga_seed=42,
                    ga_generations=10,
                    ga_population_size=24,
                )
                cn = StrategyConfig(
                    planner_mode="ga_nn",
                    playstyle=ps,
                    allow_raids=False,
                    allow_shipyard=True,
                    ga_seed=42,
                    ga_generations=10,
                    ga_population_size=24,
                )
                sr = run_optimizer(st_spd, config=cr, attack_history_path="")
                sn = run_optimizer(st_spd, config=cn, attack_history_path="")
                self.assertTrue(any(a.kind == "ATTACK" and a.executable_now for a in sr.actions), (spd, ps))
                self.assertGreater(sr.final_best_fitness, sn.final_best_fitness, (spd, ps))

        # Baseline raid+economy orders richer targets ahead of poorer ones even when listed second
        poor_t = {
            "coords": "01:010:03",
            "owner": "PoorIdle",
            "status_color": "yellow",
            "is_inactive": True,
            "resources": {"pig_iron": 2_000, "metal": 1_000, "kryptonite": 0, "spice": 0},
            "fleet": {},
            "defense": {},
        }
        st_order = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p,),
            researches=res_prereqs,
            raid_targets=(poor_t, raid_target),
        )
        base_seq = _make_baseline_raid_and_economy(
            _SimState.from_game_state(st_order, attack_history_path=""),
            StrategyConfig(allow_raids=True),
            3,
        )
        self.assertEqual(base_seq[0].kind, "ATTACK")
        self.assertEqual(base_seq[0].target_coords, "01:010:04")

        # Explicit is_inactive=False with omitted status_color stays active (light_red), blocked when allow_pvp_in_az=False
        ambig_active = {
            "coords": "01:010:09",
            "owner": "ActiveNoColor",
            "is_inactive": False,
            "is_spacia": False,
            "resources": {"pig_iron": 50_000, "metal": 50_000, "kryptonite": 20_000, "spice": 10_000},
            "fleet": {},
            "defense": {},
        }
        st_active = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p,),
            researches=res_prereqs,
            raid_targets=(ambig_active,),
        )
        acts_no_pvp = optimize_plan_actions(
            st_active,
            config=StrategyConfig(allow_raids=True, allow_pvp_in_az=False, ga_seed=42, ga_generations=4, ga_population_size=8),
            attack_history_path="",
        )
        self.assertFalse(any(a.kind == "ATTACK" and a.executable_now for a in acts_no_pvp))

    def test_tier5_zero_loot_nan_knobs_now_ts_cooldowns_and_dual_planet_attack_materialization(self) -> None:
        from spaceinvasion.optimizer import (
            SimAction,
            _resolve_reference_now_ts,
            run_optimizer,
            simulate_action_sequence,
        )

        p1 = PlanetState(
            planet_id=1,
            name="P1",
            coords="01:010:01",
            planet_type="normal",
            temperature=20,
            fields_used=20,
            fields_max=220,
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 8, "b9": 2},
            ships={"s1": 10, "s4": 25},
            resources=Resources(10_000, 8_000, 5_000, 2_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=400, used=120),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
        )
        p2 = PlanetState(
            planet_id=2,
            name="P2",
            coords="01:010:02",
            planet_type="normal",
            temperature=20,
            fields_used=20,
            fields_max=220,
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 8, "b9": 2},
            ships={"s1": 10, "s4": 25},
            resources=Resources(10_000, 8_000, 5_000, 2_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=400, used=120),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
        )
        # 1. Sub-unit / 0-loot target rejected even when raid_min_loot_spice_eq=0.0
        zero_target = {
            "coords": "01:010:05",
            "owner": "Empty",
            "is_inactive": True,
            "status_color": "yellow",
            "resources": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
            "fleet": {},
            "defense": {},
        }
        st_zero = GameState(31, 1, "u", 1_700_000_000.0, (p1,), raid_targets=(zero_target,))
        sim_zero = simulate_action_sequence(
            st_zero,
            [SimAction("ATTACK", 1, target_coords="01:010:05")],
            config=StrategyConfig(allow_raids=True, raid_min_loot_spice_eq=0.0),
            attack_history_path="",
        )
        self.assertEqual(sim_zero.valid_steps, 0)
        self.assertEqual(sim_zero.final_metrics.simulated_raid_loot_spice_eq, 0.0)

        # 2. Dual-planet single target: consecutive ATTACK steps do not over-emit phantom attacks beyond available loot
        one_target = {
            "coords": "01:010:06",
            "owner": "SingleLoot",
            "is_inactive": True,
            "status_color": "yellow",
            "resources": {"pig_iron": 6_000, "metal": 4_000, "kryptonite": 2_000, "spice": 1_000},
            "fleet": {},
            "defense": {},
        }
        st_2p = GameState(31, 1, "u", 1_700_000_000.0, (p1, p2), active_planet_id=1, raid_targets=(one_target,))
        acts_2p = optimize_plan_actions(
            st_2p,
            config=StrategyConfig(
                planner_mode="ga_nn",
                allow_raids=True,
                allow_multi_planet=True,
                raid_min_loot_spice_eq=2500.0,
                ga_seed=42,
                ga_generations=4,
                ga_population_size=8,
            ),
            attack_history_path="",
            now_ts=1_700_000_100.0,
            scanned_systems={"01:010": 1_700_000_050.0},
            throttle_cooldowns={1: 1_700_000_080.0},
        )
        atk_06 = [a for a in acts_2p if a.kind == "ATTACK" and "01:010:06" in a.target_name and a.executable_now]
        self.assertLessEqual(len(atk_06), 1)
        self.assertFalse(any(a.kind == "SCAN_GALAXY" for a in acts_2p))

        # 3. NaN / inf optimizer knobs stay finite + _resolve_reference_now_ts 365d cutoff
        nan_sum = run_optimizer(
            st_zero,
            config=StrategyConfig(planner_mode="ga_nn", ga_seed=7, ga_generations=2, ga_population_size=6),
            storage_warn_ratio=float("nan"),
            mutation_rate=float("inf"),
            nn_weight=float("nan"),
            attack_history_path="",
        )
        self.assertTrue(all(x == x for x in (nan_sum.baseline_fitness, nan_sum.best_fitness)))
        # If timestamp == 0.0 and history timestamp is within 365 days -> uses wall_now, not recent history
        import time

        recent_ts = time.time() - 10.0 * 86400.0
        ref_recent = _resolve_reference_now_ts(GameState(31, 1, "u", 0.0, (p1,)), {"history": [{"attacked_ts": recent_ts}]})
        self.assertGreater(ref_recent, recent_ts)
        # More than 365 days old falls back to history ts
        ref_old = _resolve_reference_now_ts(GameState(31, 1, "u", 0.0, (p1,)), {"history": [{"attacked_ts": 1_000_000.0}]})
        self.assertEqual(ref_old, 1_000_000.0)

        # 4. 8d/300d active raids with GameState(timestamp=0.0) expire under 365d cutoff; synthetic 1.7e9 rewinds & blocks
        lr_target = {
            "coords": "01:010:09",
            "owner": "ActiveEnemy",
            "is_inactive": False,
            "status_color": "light_red",
            "resources": {"pig_iron": 30_000, "metal": 20_000, "kryptonite": 10_000, "spice": 5_000},
            "fleet": {},
            "defense": {},
        }
        st_bash_ts0 = GameState(31, 1, "u", 0.0, (p1,), raid_targets=(lr_target,))
        wall_now = time.time()
        for age_days in (8.0, 300.0):
            old_hist = [
                {"target_coords": "01:010:09", "attacked_ts": wall_now - age_days * 86400.0 + i * 60.0, "is_exempt": False, "outcome": "win", "loot": {"spice": 1000}}
                for i in range(3)
            ]
            sim_old = simulate_action_sequence(
                st_bash_ts0,
                [SimAction("ATTACK", 1, target_coords="01:010:09")],
                config=StrategyConfig(allow_raids=True, allow_pvp_in_az=True),
                raid_history=old_hist,
                attack_history_path="",
            )
            self.assertTrue(sim_old.step_outcomes[0].valid, f"age_days={age_days} wrongly blocked")

        # 5. Tiny 2 Pig iron target with 0.1 win rate (rounded plunder == 0) must not emit phantom loot
        tiny_target = {
            "coords": "01:010:07",
            "owner": "TinyTwo",
            "is_inactive": True,
            "status_color": "yellow",
            "resources": {"pig_iron": 2, "metal": 0, "kryptonite": 0, "spice": 0},
            "fleet": {},
            "defense": {},
        }
        st_tiny = GameState(31, 1, "u", 1_700_000_000.0, (p1,), raid_targets=(tiny_target,))
        sim_tiny = simulate_action_sequence(
            st_tiny,
            [SimAction("ATTACK", 1, target_coords="01:010:07")],
            config=StrategyConfig(allow_raids=True, raid_min_loot_spice_eq=0.0),
            raid_stats={"per_target": {"01:010:07": {"attempts": 10, "wins": 1, "losses": 9, "success_rate": 0.1}}},
            attack_history_path="",
        )
        self.assertFalse(sim_tiny.step_outcomes[0].valid)
        self.assertEqual(sim_tiny.final_metrics.simulated_raid_loot_spice_eq, 0.0)

        # 6. storage_warn_ratio=NaN preserves P1 warehouse guard & _SimPlanet guards inf/1e307/bad strings
        p_full_wh = PlanetState(
            planet_id=1,
            name="NearFull",
            coords="01:010:01",
            planet_type="normal",
            temperature=20,
            fields_used=20,
            fields_max=220,
            buildings={"b1": 4, "b11": 1},
            resources=Resources(95_000, 1_000, 1_000, 1_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=400, used=100),
        )
        st_wh = GameState(31, 1, "u", 1_700_000_000.0, (p_full_wh,), active_planet_id=1)
        nan_wh = run_optimizer(
            st_wh,
            config=StrategyConfig(planner_mode="ga_nn", ga_seed=7, ga_generations=2, ga_population_size=6),
            storage_warn_ratio=float("nan"),
            attack_history_path="",
        )
        self.assertTrue(any(a.priority == "P1" and a.target_id == 11 for a in nan_wh.actions))

        p_corrupt = PlanetState(
            planet_id=1,
            name="BadNums",
            coords="01:010:01",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=200,
            buildings={"b1": "bad"},  # type: ignore[dict-item]
            ships={"s4": "oops"},  # type: ignore[dict-item]
            resources=Resources(float("inf"), 0, 0, 0),  # type: ignore[arg-type]
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=10**400, used=10),
        )
        st_corrupt = GameState(31, 1, "u", 0.0, (p_corrupt,), speedup_factor=1e307)
        corrupt_sum = run_optimizer(
            st_corrupt,
            config=StrategyConfig(planner_mode="ga_nn", ga_seed=3, ga_generations=2, ga_population_size=6),
            attack_history_path="",
        )
        self.assertTrue(math.isfinite(corrupt_sum.best_fitness))

        # 7. ColdSpice (-110C) vs WarmCore (+80C) at pop=24, seed=42 picks ColdSpice b4 first
        p_warm = _make_planet(planet_id=101, name="WarmCore", coords="02:380:02", temperature=80, buildings={"b1": 6, "b2": 5, "b3": 5, "b4": 2, "b5": 10}, resources=Resources(12_000, 12_000, 12_000, 5_000))
        p_cold = _make_planet(planet_id=102, name="ColdSpice", coords="02:380:15", temperature=-110, buildings={"b1": 6, "b2": 5, "b3": 5, "b4": 2, "b5": 10}, resources=Resources(12_000, 12_000, 12_000, 5_000))
        st_cs = GameState(31, 1, "u", 0.0, (p_warm, p_cold), active_planet_id=101, researches={"r1": 2})
        acts_cs = optimize_plan_actions(
            st_cs,
            config=StrategyConfig(planner_mode="ga_nn", allow_multi_planet=True, ga_population_size=24, ga_generations=12, ga_horizon_steps=6, ga_seed=42),
            attack_history_path="",
        )
        b4_cs = [a for a in acts_cs if a.kind in ("BUILD", "WAIT_RESOURCES") and a.target_id == 4]
        self.assertTrue(b4_cs and b4_cs[0].planet_id == 102)

        # 8. _cum_research_res_sum clamped fast (<50ms at r1=200_000), _safe_int(10**400), direct _materialize_planned_actions tiny-loot rejection & BUILD_SHIP aggregation
        from spaceinvasion.optimizer import _cum_research_res_sum, _safe_int

        t_r0 = time.perf_counter()
        _cum_research_res_sum("r1", 200_000)
        elapsed_r_ms = (time.perf_counter() - t_r0) * 1000.0
        self.assertLess(elapsed_r_ms, 50.0)
        self.assertEqual(_safe_int(10**400, -1), 10**400)

        t_opt0 = time.perf_counter()
        sum_r200k = run_optimizer(
            GameState(31, 1, "u", 0.0, (p1,), researches={"r1": 200_000}),
            config=StrategyConfig(planner_mode="ga_nn", ga_population_size=4, ga_generations=1, ga_horizon_steps=2, ga_seed=1),
            attack_history_path="",
        )
        self.assertLess((time.perf_counter() - t_opt0) * 1000.0, 2000.0)
        self.assertTrue(math.isfinite(sum_r200k.best_fitness))

        direct_tiny = _materialize_planned_actions(
            st_tiny,
            (SimAction("ATTACK", 1, target_coords="01:010:07"),),
            StrategyConfig(allow_raids=True, raid_min_loot_spice_eq=0.0),
            0.85,
            attack_history_stats={"per_target": {"01:010:07": {"attempts": 10, "wins": 1, "losses": 9, "success_rate": 0.1}}},
        )
        self.assertFalse(any(a.kind == "ATTACK" and a.executable_now for a in direct_tiny))

        p_ships = PlanetState(
            planet_id=1,
            name="Yard",
            coords="01:010:01",
            planet_type="normal",
            temperature=20,
            fields_used=20,
            fields_max=220,
            buildings={"b1": 4, "b9": 2},
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=400, used=100),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 0, Resources(1500, 0, 500, 0), True, "ship[4]"),
            ),
        )
        st_ships = GameState(31, 1, "u", 0.0, (p_ships,), active_planet_id=1)
        agg_true = _materialize_planned_actions(
            st_ships,
            (
                SimAction("BUILD_SHIP", 1, target_id=4, quantity=2),
                SimAction("BUILD_SHIP", 1, target_id=4, quantity=5),
            ),
            StrategyConfig(allow_shipyard=True),
            0.85,
            attack_history_stats={},
        )
        bs_true = [a for a in agg_true if a.kind == "BUILD_SHIP"]
        self.assertEqual(len(bs_true), 1)
        self.assertEqual(bs_true[0].target_level, 7)
        self.assertEqual(bs_true[0].cost, Resources(10_500, 0, 3_500, 0))
        self.assertTrue(bs_true[0].executable_now)

        agg_false = _materialize_planned_actions(
            st_ships,
            (
                SimAction("BUILD_SHIP", 1, target_id=4, quantity=2),
                SimAction("BUILD_SHIP", 1, target_id=4, quantity=5),
            ),
            StrategyConfig(allow_shipyard=False),
            0.85,
            attack_history_stats={},
        )
        bs_false = [a for a in agg_false if a.kind == "BUILD_SHIP"]
        self.assertEqual(len(bs_false), 1)
        self.assertEqual(bs_false[0].target_level, 7)
        self.assertFalse(bs_false[0].executable_now)

    def test_optimizer_normalizes_own_coords_handles_bad_commander_and_clamps_huge_ints(self) -> None:
        from spaceinvasion.optimizer import (
            _aggregate_raw_raid_history,
            _compute_effective_raid_expectation,
            _merge_stats_layers,
        )

        # Finding 1: Unnormalized own planet coords ("1:10:1") match normalized DB/target ("01:010:01")
        own_p = _make_planet(101, coords="1:10:1", buildings={"b1": 4, "b2": 2, "b3": 2, "b4": 2, "b5": 5, "b9": 2}, ships={"s1": 5, "s4": 10})
        other_t = {"coords": "2:380:4", "is_inactive": True, "status_color": "yellow", "resources": {"pig_iron": 25_000, "metal": 15_000, "kryptonite": 10_000, "spice": 5_000}, "last_spied_ts": 1000.0}
        own_t = {"coords": "01:010:01", "is_inactive": True, "status_color": "yellow", "resources": {"pig_iron": 999_999, "metal": 999_999, "kryptonite": 999_999, "spice": 999_999}, "last_spied_ts": 1000.0}
        st_own = GameState(
            31,
            1,
            "u",
            1000.0,
            (own_p,),
            active_planet_id=101,
            specs={"construction_commander": "bad"},
            raid_targets=(own_t, other_t),
        )
        cfg_raid = StrategyConfig(planner_mode="ga_nn", allow_raids=True, ga_population_size=4, ga_generations=1, raid_min_loot_spice_eq=1.0)
        planned = optimize_plan_actions(st_own, config=cfg_raid)
        atk_targets = [a.target_name for a in planned if a.kind == "ATTACK"]
        self.assertFalse(any("01:010:01" in t or "1:10:1" in t for t in atk_targets))
        mat_own = _materialize_planned_actions(
            st_own,
            (SimAction("ATTACK", 101, target_coords="01:010:01"), SimAction("ATTACK", 101, target_coords="1:10:1")),
            cfg_raid,
            0.85,
        )
        self.assertEqual([a for a in mat_own if a.kind == "ATTACK"], [])
        sim_own = simulate_action_sequence(st_own, (SimAction("ATTACK", 101, target_coords="01:010:01"),), config=cfg_raid)
        self.assertEqual(sim_own.valid_steps, 0)
        self.assertEqual(sim_own.rejected_steps, 1)

        agg = _aggregate_raw_raid_history(
            [
                {"target_coords": "2:380:4", "ts": 900.0, "won": True, "loot": {"pig_iron": 6000}},
                {"target_coords": "02:380:04", "ts": 950.0, "won": True, "loot": {"pig_iron": 6000}},
            ],
            now_ts=1000.0,
        )
        self.assertEqual(list(agg["per_target"].keys()), ["02:380:04"])
        self.assertEqual(agg["per_target"]["02:380:04"]["attempts"], 2)
        merged_stats = _merge_stats_layers(
            agg,
            {"per_target": {"2:380:4": {"attempts": 1, "wins": 1, "loot_spice_total": 500.0}}},
            now_ts=1000.0,
        )
        self.assertEqual(list(merged_stats["per_target"].keys()), ["02:380:04"])
        self.assertEqual(merged_stats["per_target"]["02:380:04"]["attempts"], 1)
        exp_loot, exp_win, exp_hist = _compute_effective_raid_expectation(500.0, 10_000, "2:380:4", merged_stats)
        self.assertGreater(exp_loot, 0.0)
        self.assertGreater(exp_win, 0.0)
        self.assertIsNotNone(exp_hist)
        self.assertEqual(exp_hist["attempts"], 1)

        # Finding 2 & Finding 3: bad specs + huge int levels/ships/points do not raise ValueError/OverflowError
        huge_p = _make_planet(
            102,
            coords="02:380:05",
            buildings={"b1": 10**400, "b5": 10},
            ships={"s1": 10**1_000_000, "s9": 10**400},
            defense={"d1": 10**1_000_000},
        )
        st_huge = GameState(
            31,
            1,
            "u",
            1000.0,
            (huge_p,),
            active_planet_id=102,
            researches={"r1": 10**400, "r2": 10**400, "r8": 10**400},
            specs={"construction_commander": "bad"},
            account_points=10**1_000_000,
        )
        vec = encode_empire_state(st_huge)
        self.assertEqual(len(vec), STATE_FEATURE_DIM)
        self.assertTrue(all(math.isfinite(x) for x in vec))
        summary = run_optimizer(st_huge, config=StrategyConfig(planner_mode="ga_nn", ga_population_size=4, ga_generations=1))
        self.assertTrue(math.isfinite(summary.best_fitness))

    def test_huge_offer_loot_and_research_target_levels_no_overflow(self) -> None:
        p_huge = PlanetState(
            planet_id=1,
            name="P1",
            coords="01:001:01",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=200,
            resources=Resources(10**400, 10**400, 10**400, 10**400),
            storage=Resources(10**400, 10**400, 10**400, 10**400),
            energy=EnergyBalance(1000, 100),
            buildings={"b1": 5, "b8": 5, "b9": 5, "b10": 10},
            ships={"s1": 10**400, "s4": 10**400},
            defense={"d1": 10},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(10**400, 10**400, 0, 0), False, "Need"),
            ),
            ship_offers=(
                UnitOffer("ship", 4, "Falcon", 10, Resources(100, 100, 0, 0), True),
            ),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
        )
        raid_t = {
            "coords": "01:001:09",
            "owner": "IdleY",
            "status_color": "yellow",
            "is_inactive": True,
            "resources": {"pig_iron": 10**400, "metal": 10**308, "kryptonite": 0, "spice": 0},
            "fleet": {},
            "defense": {},
        }
        st = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p_huge,),
            active_planet_id=1,
            researches={"r1": 1},
            active_queues=(QueueJob("research", "Mining technology", target_level=10**5000),),
            research_offers=(
                ResearchOffer(
                    1,
                    "r1",
                    "Mining technology",
                    1,
                    10**5000,
                    Resources(100, 0, 0, 0),
                    True,
                    "In research",
                    active=True,
                ),
            ),
            raid_targets=(raid_t,),
        )
        sim_res = simulate_action_sequence(
            st,
            [SimAction("BUILD", 1, target_id=1), SimAction("RAID", 1, target_coords="01:001:09")],
        )
        self.assertTrue(math.isfinite(sim_res.fitness))

        cfg = StrategyConfig(planner_mode="ga_nn", ga_population_size=4, ga_generations=1, ga_horizon_steps=2, allow_raids=True)
        with tempfile.TemporaryDirectory() as tmp:
            ah = Path(tmp) / "ah.json"
            ah.write_text("[]", encoding="utf-8")
            acts = optimize_plan_actions(st, config=cfg, attack_history_path=ah)
        opt_summary = get_last_optimizer_summary()
        self.assertIsNotNone(opt_summary)
        assert opt_summary is not None
        self.assertTrue(math.isfinite(opt_summary["best_fitness"]))
        for act in acts:
            if act.target_level is not None:
                self.assertLessEqual(act.target_level, 1_000_000)
        json.dumps(opt_summary, allow_nan=False)

        # Preserve BUILD_SHIP coalescing target_level=7 invariant
        coalesced = _materialize_planned_actions(
            st,
            (
                SimAction("BUILD_SHIP", 1, target_id=4, quantity=2),
                SimAction("BUILD_SHIP", 1, target_id=4, quantity=5),
            ),
            StrategyConfig(allow_shipyard=True),
            0.85,
            attack_history_stats={},
        )
        ship_acts = [a for a in coalesced if a.kind == "BUILD_SHIP" and a.target_id == 4]
        self.assertEqual(len(ship_acts), 1)
        self.assertEqual(ship_acts[0].target_level, 7)

    def test_adversarial_sim_actions_huge_ids_invalid_quantities_and_timers(self) -> None:
        from spaceinvasion.optimizer import _parse_timer_seconds, _shipyard_duration_s

        self.assertTrue(math.isfinite(_parse_timer_seconds("9" * 5000 + ":00:00")))
        p = _make_planet(buildings={"b1": 5, "b5": 5, "b7": 10})
        dur = _shipyard_duration_s(100.0, 10**5000, wf_level=10, micro_level=2, speedup=1e308)
        self.assertTrue(math.isfinite(dur))

        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="u",
            timestamp=1000.0,
            planets=(p,),
            active_planet_id=p.planet_id,
            researches={"r1": 1},
            speedup_factor=1.0,
        )
        cfg = StrategyConfig(allow_shipyard=True, allow_research=True)

        # Invalid/extreme target_id, target_coords, kind, unhashable/huge planet_id
        huge_str = "9" * 5000
        for bad_act in (
            SimAction(kind="BUILD", planet_id=p.planet_id, target_id=10**5000),  # type: ignore[arg-type]
            SimAction(kind="RESEARCH", planet_id=p.planet_id, target_id=10**5000),  # type: ignore[arg-type]
            SimAction(kind="RESEARCH", planet_id=p.planet_id, target_id=huge_str),  # type: ignore[arg-type]
            SimAction(kind="RESEARCH", planet_id=p.planet_id, target_id="²"),  # type: ignore[arg-type]
            SimAction(kind="BUILD_SHIP", planet_id=p.planet_id, target_id=10**5000, quantity=1),  # type: ignore[arg-type]
            SimAction(kind="BUILD_SHIP", planet_id=p.planet_id, target_id=huge_str, quantity=1),  # type: ignore[arg-type]
            SimAction(kind="BUILD_SHIP", planet_id=p.planet_id, target_id="²", quantity=1),  # type: ignore[arg-type]
            SimAction(kind="BUILD_DEFENSE", planet_id=p.planet_id, target_id="²", quantity=1),  # type: ignore[arg-type]
            SimAction(kind="BUILD_DEFENSE", planet_id=p.planet_id, target_id=huge_str, quantity=1),  # type: ignore[arg-type]
            SimAction(kind="THROTTLE_BUILDING", planet_id=[1], target_id=10**5000, quantity=10**5000),  # type: ignore[arg-type]
            SimAction(kind=10**5000, planet_id=10**5000, target_coords=10**5000, target_id=1),  # type: ignore[arg-type]
        ):
            res = simulate_action_sequence(st, [bad_act], config=cfg)
            json.dumps(res.step_outcomes[0].rejection_reason, allow_nan=False)

        # Even with 10**400 resources + Weapons factory b9=1, quantity=10**5000 must not crash _shipyard_duration_s
        p_rich = PlanetState(
            planet_id=p.planet_id,
            name=p.name,
            coords=p.coords,
            planet_type=p.planet_type,
            temperature=p.temperature,
            fields_used=p.fields_used,
            fields_max=p.fields_max,
            resources=Resources(10**400, 10**400, 10**400, 10**400),
            storage=Resources(10**400, 10**400, 10**400, 10**400),
            energy=EnergyBalance(100, 50),
            buildings={"b1": 5, "b9": 1},
        )
        st_rich = GameState(
            universe_id=31,
            user_id=1,
            nickname="u",
            timestamp=1000.0,
            planets=(p_rich,),
            active_planet_id=p.planet_id,
            researches={"r1": 1},
            speedup_factor=1.0,
        )
        rich_ship_res = simulate_action_sequence(
            st_rich,
            [SimAction(kind="BUILD_SHIP", planet_id=p.planet_id, target_id=1, quantity=10**5000)],
            config=cfg,
        )
        self.assertFalse(rich_ship_res.step_outcomes[0].valid)

        # Invalid quantities must be rejected (valid=False) without exception
        for bad_qty in ("bad", float("inf"), float("nan"), -5, 0, True, 10**5000):
            res = simulate_action_sequence(
                st,
                [SimAction(kind="BUILD_SHIP", planet_id=p.planet_id, target_id=1, quantity=bad_qty)],  # type: ignore[arg-type]
                config=cfg,
            )
            lbl = f"int({bad_qty.bit_length()}b)" if isinstance(bad_qty, int) else repr(bad_qty)
            self.assertFalse(res.step_outcomes[0].valid, f"Expected quantity={lbl} to be invalid")
            if bad_qty == 10**5000:
                self.assertEqual(res.valid_steps, 0)
                self.assertEqual(res.rejected_steps, 1)

        summary = OptimizerRunSummary(
            actions=(),
            best_chromosome=(),
            best_simulation=simulate_action_sequence(st, [], config=cfg),
            generation_history=(),
            initial_best_fitness=float("inf"),
            final_best_fitness=float("nan"),
            initial_mean_fitness=float("nan"),
            final_mean_fitness=float("inf"),
            nn_initial_loss=float("nan"),
            nn_final_loss=float("nan"),
            nn_weight_delta_norm=float("inf"),
            nn_backend="pure_python",
            seed=1,
            elapsed_ms=float("inf"),
        )
        json.dumps(summary.to_dict(), allow_nan=False)

    def test_adv_huge_bkey_nonfinite_timestamp_and_mlp_from_dict_sanitization(self) -> None:
        from spaceinvasion.optimizer import _SimState, _compute_sim_metrics
        from spaceinvasion.state import game_state_from_dict

        # GAP-01: valid JSON key "b" + "9"*5000 must not trigger 4300-digit int() ValueError
        huge_bkey = "b" + ("9" * 5000)
        gs_b5k = game_state_from_dict(
            {
                "timestamp": 1000.0,
                "active_planet_id": 1,
                "planets": [
                    {
                        "planet_id": 1,
                        "name": "H",
                        "coords": "01:001:01",
                        "buildings": {"b1": 2, huge_bkey: 5},
                        "resources": {"pig_iron": 5000, "metal": 3000, "kryptonite": 2000, "spice": 500},
                        "storage": {"pig_iron": 100_000, "metal": 100_000, "kryptonite": 100_000, "spice": 100_000},
                        "energy": {"produced": 200, "used": 50},
                    }
                ],
            }
        )
        cfg = StrategyConfig(planner_mode="ga_nn", ga_generations=1, ga_population_size=4, ga_horizon_steps=2, ga_seed=1)
        sim = _SimState.from_game_state(gs_b5k)
        metrics = _compute_sim_metrics(sim, cfg, 0.85)
        self.assertTrue(math.isfinite(metrics.composite_score))
        feats = encode_empire_state(gs_b5k)
        self.assertTrue(all(math.isfinite(f) for f in feats))
        res_b5k = run_optimizer(gs_b5k, generations=1, population_size=4, horizon_steps=2, seed=1)
        self.assertTrue(math.isfinite(res_b5k.final_best_fitness))
        acts_b5k = optimize_plan_actions(gs_b5k, config=cfg, attack_history_path="")
        self.assertIsInstance(acts_b5k, list)
        planned_b5k = plan_actions(gs_b5k, config=cfg)
        self.assertIsInstance(planned_b5k, list)

        # GAP-03: non-finite & 10**5000 GameState.timestamp sanitized cleanly in _SimState.to_game_state
        for bad_ts in (float("nan"), float("inf"), float("-inf"), 10**5000):
            gs_ts = GameState(
                universe_id=31,
                user_id=1,
                nickname="u",
                timestamp=bad_ts,  # type: ignore[arg-type]
                planets=gs_b5k.planets,
                active_planet_id=1,
            )
            res_ts = run_optimizer(gs_ts, generations=1, population_size=4, horizon_steps=2, seed=1)
            self.assertTrue(math.isfinite(res_ts.best_simulation.final_state.timestamp))
            self.assertTrue(math.isfinite(_SimState.from_game_state(gs_ts).to_game_state().timestamp))

        # GAP-04: PurePythonMLP.from_dict sanitizes NaN / Inf / 10**5000 weights and biases
        net_d = create_value_network("python", seed=42).to_dict()
        net_d["weights"][0][0][0] = float("nan")
        net_d["weights"][0][0][1] = float("inf")
        net_d["weights"][0][0][2] = 10**5000
        net_d["biases"][0][0] = float("-inf")
        net = PurePythonMLP.from_dict(net_d)
        pred = net.predict([1.0] * STATE_FEATURE_DIM)
        self.assertTrue(math.isfinite(pred))
        t_stats = net.train_on_rollouts([([1.0] * STATE_FEATURE_DIM, float("nan"))], lr=float("nan"), weight_decay=float("inf"))
        self.assertTrue(math.isfinite(t_stats.initial_loss))
        self.assertTrue(math.isfinite(t_stats.final_loss))
        self.assertTrue(math.isfinite(t_stats.weight_delta_norm))
        json.dumps(net.to_dict(), allow_nan=False)

    def test_r8_mlp_multi_output_train_and_simulate_horizon_hours_bounds(self) -> None:
        from dataclasses import asdict
        import json

        # GAP-R8-02: layer_sizes[-1] > 1 must train cleanly without IndexError
        net_big = NeuralValueNetwork.from_dict({"layer_sizes": [4, 10**8]}, backend="python")
        self.assertEqual(net_big.impl.layer_sizes, (4, 4096))
        loss_big = net_big.train_step([1.0, 2.0, 3.0, 4.0], 0.5)
        self.assertTrue(math.isfinite(loss_big))
        stats_big = net_big.train_on_rollouts([([1.0, 2.0, 3.0, 4.0], 0.5)], epochs=2)
        self.assertTrue(math.isfinite(stats_big.final_loss))
        json.dumps(net_big.to_dict(), allow_nan=False)

        for mlp in (PurePythonMLP((4, 2)), PurePythonMLP([4] * 5000)):  # type: ignore[arg-type]
            l_step = mlp.train_step([1.0, 2.0, 3.0, 4.0], 0.5)
            self.assertTrue(math.isfinite(l_step))
            r_stats = mlp.train_on_rollouts([([1.0, 2.0, 3.0, 4.0], 0.5)], epochs=2)
            self.assertTrue(math.isfinite(r_stats.final_loss))

        # GAP-R8-03: simulate_action_sequence horizon_hours bounds (no OverflowError, ValueError, inf, or resource wipe)
        pl = _make_planet(
            resources=Resources(100, 100, 100, 100),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(200, 50),
            buildings={"b1": 5, "b3": 3, "b5": 5},
        )
        st = GameState(31, 1, "u", 1000.0, (pl,), active_planet_id=pl.planet_id)
        for bad_h in (10**5000, "bad", float("inf"), float("nan"), "9" * 5000, 1e12):
            res = simulate_action_sequence(st, [], horizon_hours=bad_h, attack_history_path="")  # type: ignore[arg-type]
            lbl = f"int({bad_h.bit_length()}b)" if isinstance(bad_h, int) else repr(bad_h)
            self.assertTrue(math.isfinite(res.elapsed_seconds), f"horizon={lbl}")
            self.assertGreaterEqual(res.elapsed_seconds, 0.0)
            self.assertLessEqual(res.elapsed_seconds, 87600.0 * 3600.0 + 1.0)
            self.assertGreater(res.final_state.planets[0].resources.pig_iron, 100)
            json.dumps(
                {
                    "elapsed_seconds": res.elapsed_seconds,
                    "fitness": res.fitness,
                    "points_delta": res.points_delta,
                    "pig_iron": res.final_state.planets[0].resources.pig_iron,
                },
                allow_nan=False,
            )

        res_neg = simulate_action_sequence(st, [], horizon_hours=-5.0, attack_history_path="")
        self.assertEqual(res_neg.elapsed_seconds, 0.0)
        self.assertGreaterEqual(res_neg.final_state.planets[0].resources.pig_iron, 100)

    def test_r9_coords_distance_rejects_arabic_indic_in_parity_with_fleet(self) -> None:
        from spaceinvasion.fleet import _parse_coords_distance
        from spaceinvasion.optimizer import _coords_distance

        ar = "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"
        self.assertEqual(_coords_distance(ar, "02:380:05"), 100.0)
        self.assertEqual(_coords_distance(ar, "02:380:05"), _parse_coords_distance(ar, "02:380:05"))
        self.assertEqual(_coords_distance("02:380:04", "02:380:05"), _parse_coords_distance("02:380:04", "02:380:05"))

    def test_r10_raid_colony_and_asteroid_parity_plus_safe_float_any(self) -> None:
        from dataclasses import replace
        from spaceinvasion.optimizer import (
            _SimPlanet,
            _planet_energy_balance,
            _safe_action_str,
            _safe_float_any,
        )
        from spaceinvasion.state import BuildingOffer, ResearchOffer, UnitOffer

        self.assertEqual(_safe_float_any(b"3.25"), 3.25)
        self.assertEqual(_safe_float_any("\u0663\u0665"), 0.0)
        self.assertEqual(_safe_float_any(frozenset({1})), 0.0)
        self.assertIn("~1e5000", _safe_action_str(10**5000))

        raid_pl = PlanetState(
            planet_id=91,
            name="RaidCol",
            coords="01:001:02",
            planet_type=" Raid ",
            temperature=25,
            fields_used=5,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(50, 100),
            buildings={"b1": 5, "b8": 2, "b9": 2},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(200, 100, 50, 0), True, "?b=1"),
                BuildingOffer(5, "Fusion plant", 0, 1, Resources(200, 100, 50, 0), True, "?b=5"),
            ),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 0, Resources(200, 100, 50, 0), True, "ship[4]"),
            ),
        )
        self.assertEqual(
            _planet_energy_balance(
                _SimPlanet.from_planet_state(
                    replace(raid_pl, buildings={"b1": 5, "b5": b"2", (10**5000, "x"): 1}),  # type: ignore[dict-item]
                    {},
                ),
                {},
                1.0,
            )[0],
            _planet_energy_balance(
                _SimPlanet.from_planet_state(replace(raid_pl, buildings={"b1": 5, "b5": 2}), {}),
                {},
                1.0,
            )[0],
        )
        raid_st = GameState(
            universe_id=31,
            user_id=1,
            nickname="u",
            timestamp=1000.0,
            planets=(raid_pl,),
            active_planet_id=91,
            research_offers=(
                ResearchOffer(1, "r1", "Mining technology", 0, 1, Resources(100, 100, 100, 0), True, "ready", "?r=1"),
            ),
        )
        raid_acts = optimize_plan_actions(
            raid_st,
            config=StrategyConfig(
                planner_mode="ga_nn",
                allow_research=True,
                allow_shipyard=True,
                ga_population_size=8,
                ga_generations=3,
                ga_horizon_steps=3,
                ga_seed=7,
            ),
            attack_history_path="",
        )
        self.assertEqual(
            [
                a
                for a in raid_acts
                if a.planet_id == 91
                and a.kind in ("BUILD", "WAIT_RESOURCES", "BUILD_SHIP", "BUILD_DEFENSE", "THROTTLE_BUILDING", "RESEARCH")
            ],
            [],
        )

        ast_pl = PlanetState(
            planet_id=92,
            name="Ast",
            coords="01:001:03",
            planet_type=" Asteroid ",
            temperature=10,
            fields_used=11,
            fields_max=20,
            resources=Resources(200_000, 200_000, 200_000, 200_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(50, 10),
            buildings={"b7": 10, "b19": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 0, 1, Resources(100, 50, 0, 0), True, "Build", "?b=1"),
                BuildingOffer(19, "Galaxy scanner", 1, 2, Resources(20_000, 80_000, 40_000, 16_000), True, "Build", "?b=19"),
            ),
        )
        ast_acts = optimize_plan_actions(
            GameState(31, 1, "u", 1000.0, (ast_pl,), active_planet_id=92),
            config=StrategyConfig(planner_mode="ga_nn", ga_population_size=8, ga_generations=3, ga_horizon_steps=3, ga_seed=11),
            attack_history_path="",
        )
        allowed_ast_bids = {bid for bid, info in game_data.BUILDING_BY_ID.items() if info.get("asteroid_ok")}
        self.assertEqual(
            [a for a in ast_acts if a.planet_id == 92 and a.kind == "BUILD" and a.target_id not in allowed_ast_bids],
            [],
        )
        self.assertTrue(
            any(a.planet_id == 92 and a.kind == "BUILD" and a.executable_now and a.target_id == 19 for a in ast_acts)
        )

    def test_r11_asteroid_energy_storage_queue_raid_sim_and_frozenset_nan_guards(self) -> None:
        import json
        from types import MappingProxyType
        from spaceinvasion.optimizer import (
            SimAction,
            _dict_to_resources,
            _json_safe,
            _parse_sim_quantity,
            _safe_float_any,
            _safe_res_float,
            simulate_action_sequence,
        )

        ast_q = PlanetState(
            planet_id=192,
            name="AstBlocked",
            coords="01:001:04",
            planet_type=" Asteroid ",
            temperature=10,
            fields_used=8,
            fields_max=20,
            resources=Resources(190_000, 190_000, 190_000, 190_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(10, 100),
            buildings={"b7": 8},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 0, 1, Resources(100, 0, 50, 0), True, "?b=5"),
                BuildingOffer(11, "Pig iron warehouse", 0, 1, Resources(2000, 800, 0, 0), True, "?b=11"),
                BuildingOffer(7, "Development center", 8, 9, Resources(1000, 1000, 1000, 1000), True, "?b=7"),
            ),
        )
        st_ast = GameState(
            31,
            1,
            "u",
            1000.0,
            (ast_q,),
            active_planet_id=192,
            active_queues=(QueueJob("building", "Development center", building_id=7, target_level=9),),
        )
        ast_acts = optimize_plan_actions(
            st_ast,
            config=StrategyConfig(planner_mode="ga_nn", ga_population_size=8, ga_generations=3, ga_horizon_steps=3, ga_seed=17),
            attack_history_path="",
        )
        self.assertEqual(
            [
                a
                for a in ast_acts
                if a.planet_id == 192
                and (
                    a.kind == "THROTTLE_BUILDING"
                    or (a.kind == "BUILD" and (a.target_id in (5, 11, 12, 13, 14) or a.executable_now))
                )
            ],
            [],
        )
        sim_thr = simulate_action_sequence(
            st_ast,
            [SimAction("THROTTLE_BUILDING", 192, target_id=2, quantity=0)],
            attack_history_path="",
        )
        self.assertEqual(sim_thr.valid_steps, 0)

        raid_q = PlanetState(
            planet_id=193,
            name="RaidSim",
            coords="01:001:05",
            planet_type=" Raid ",
            temperature=20,
            fields_used=4,
            fields_max=20,
            resources=Resources(200_000, 200_000, 200_000, 200_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(100, 10),
            buildings={"b7": 3, "b9": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 0, 1, Resources(78, 0, 25, 0), True, "?b=1"),
                BuildingOffer(7, "Development center", 3, 4, Resources(4800, 2400, 1600, 0), True, "?b=7"),
                BuildingOffer(9, "Weapons factory", 1, 2, Resources(600, 400, 0, 700), True, "?b=9"),
            ),
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(200, 100, 50, 0), True, "ship[4]"),),
            defense_offers=(UnitOffer("defense", 1, "Rocket launcher", 0, Resources(200, 100, 0, 0), True, "def[1]"),),
        )
        st_raid = GameState(
            31,
            1,
            "u",
            1000.0,
            (raid_q,),
            active_planet_id=193,
            researches={"r2": 1},
            research_offers=(ResearchOffer(1, "r1", "Mining technology", 0, 1, Resources(1000, 0, 500, 0), True, "ready", "?r=1"),),
        )
        for bad_step in (
            SimAction("RESEARCH", 193, target_id="r1"),
            SimAction("BUILD_SHIP", 193, target_id=4, quantity=1),
            SimAction("BUILD_DEFENSE", 193, target_id=1, quantity=1),
            SimAction("THROTTLE_BUILDING", 193, target_id=1, quantity=0),
            SimAction("BUILD", 193, target_id=1),
        ):
            self.assertEqual(
                simulate_action_sequence(
                    st_raid,
                    [bad_step],
                    config=StrategyConfig(allow_research=True, allow_shipyard=True),
                    attack_history_path="",
                ).valid_steps,
                0,
                f"Expected rejection for {bad_step}",
            )
        for good_bid in (7, 9):
            self.assertEqual(
                simulate_action_sequence(
                    st_raid,
                    [SimAction("BUILD", 193, target_id=good_bid)],
                    attack_history_path="",
                ).valid_steps,
                1,
                f"Expected BUILD b{good_bid} allowed on Raid colony",
            )

        js = _json_safe(frozenset({10**5000}))
        json.dumps(js, allow_nan=False)
        self.assertIsNone(_parse_sim_quantity(frozenset({5})))
        self.assertEqual(_dict_to_resources(MappingProxyType({"pig_iron": 123, "metal": 45})), Resources(123, 45, 0, 0))
        self.assertEqual(_safe_res_float(MappingProxyType({"a": 1})), 0.0)
        self.assertEqual(_safe_res_float(frozenset({1.5})), 0.0)
        self.assertEqual(_safe_float_any(float("nan"), 1500.0), 1500.0)

    def test_r12_optimizer_huge_and_broken_tutorial_reward_text_and_safe_action_str(self) -> None:
        from spaceinvasion import optimizer as opt
        from spaceinvasion.config import StrategyConfig
        from spaceinvasion.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            TutorialStatus,
            TutorialTask,
        )

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"bytes_ok"

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        for obj in (_RaiseFspath(), _IntPath(), _BytesPath(), _RaiseStrRepr()):
            self.assertIsInstance(opt._safe_action_str(obj), str)

        planet = PlanetState(
            planet_id=1,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=5,
            fields_max=230,
            resources=Resources(2000, 1000, 1000, 500),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(50, 10),
            buildings={"b1": 0},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 0, 1, Resources(60, 15, 0, 0), True, "Build", "?b=1"),
            ),
        )
        tut = TutorialStatus(
            step=1,
            total_steps=11,
            tasks=(TutorialTask(_RaiseFspath(), _RaiseStrRepr(), 1, 0, 1),),  # type: ignore[arg-type]
            reward_text=10**5000,  # type: ignore[arg-type]
            can_complete=True,
        )
        st = GameState(31, 1, "u", 1000.0, (planet,), active_planet_id=1, tutorial=tut)
        cfg = StrategyConfig(
            planner_mode="ga_nn",
            ga_population_size=4,
            ga_generations=2,
            ga_horizon_steps=3,
            ga_seed=1,
        )
        acts = opt.optimize_plan_actions(st, config=cfg)
        self.assertTrue(acts)
        self.assertEqual(acts[0].kind, "CLAIM_TUTORIAL")
        self.assertIsInstance(acts[0].reason, str)

    def test_r13_materialize_blocks_unbuilt_b8_b9_and_unmet_prereqs(self) -> None:
        p_nob8_b9 = PlanetState(
            planet_id=801,
            name="QueuedOnly",
            coords="02:380:12",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=200,
            resources=Resources(100_000, 100_000, 100_000, 100_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b5": 5, "b7": 3, "b8": 0, "b9": 0},
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),
                UnitOffer("ship", 9, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),
            ),
        )
        r2_offer = ResearchOffer(2, "r2", "Energy technology", 0, 1, Resources(0, 400, 800, 400), True, "Research", "?r=2")
        r1_offer = ResearchOffer(1, "r1", "Mining technology", 0, 1, Resources(200, 100, 100, 0), True, "Research", "?r=1")
        st_q = GameState(
            31,
            1,
            "u",
            1000.0,
            (p_nob8_b9,),
            active_planet_id=801,
            researches={},
            research_offers=(r2_offer, r1_offer),
            active_queues=(
                QueueJob("building", "Research lab", 8, 1, "00:00:00"),
                QueueJob("building", "Weapons factory", 9, 1, "00:00:00"),
            ),
        )
        mat_blocked = _materialize_planned_actions(
            st_q,
            (
                SimAction("RESEARCH", 801, target_id="r2"),
                SimAction("BUILD_SHIP", 801, target_id=4, quantity=1),
            ),
            StrategyConfig(allow_research=True, allow_shipyard=True),
            0.85,
            attack_history_stats={},
        )
        self.assertFalse(any(a.kind in ("RESEARCH", "BUILD_SHIP") and a.executable_now for a in mat_blocked))

        sim_q = simulate_action_sequence(
            st_q,
            (
                SimAction("RESEARCH", 801, target_id="r2"),
                SimAction("BUILD_SHIP", 801, target_id=9, quantity=1),
            ),
            config=StrategyConfig(allow_research=True, allow_shipyard=True),
            attack_history_path="",
        )
        self.assertTrue(all(not o.executable_at_t0 for o in sim_q.step_outcomes))

        # Forged b9=1 offer for Battleship (requires b9>=7) blocked, while b9=2 s4 (researches={}) preserved
        p_b9_1 = PlanetState(
            planet_id=802,
            name="Yard1",
            coords="02:380:13",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=200,
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b8": 1, "b9": 1},
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),
                UnitOffer("ship", 13, "Battleship", 0, Resources(50_000, 20_000, 10_000, 5000), True, "ship[13]"),
            ),
        )
        st_b9_1 = GameState(
            31,
            1,
            "u",
            1000.0,
            (p_b9_1,),
            active_planet_id=802,
            researches={},
            research_offers=(r1_offer,),
        )
        mat_mix = _materialize_planned_actions(
            st_b9_1,
            (
                SimAction("RESEARCH", 802, target_id="r1"),
                SimAction("BUILD_SHIP", 802, target_id=13, quantity=1),
                SimAction("BUILD_SHIP", 802, target_id=4, quantity=1),
            ),
            StrategyConfig(allow_research=True, allow_shipyard=True),
            0.85,
            attack_history_stats={},
        )
        by_kind_tid = {(a.kind, a.target_id): a.executable_now for a in mat_mix}
        self.assertFalse(by_kind_tid[("RESEARCH", 1)])
        self.assertFalse(by_kind_tid[("BUILD_SHIP", 13)])
        self.assertTrue(by_kind_tid[("BUILD_SHIP", 4)])

    def test_r14_optimizer_json_safe_cycles_prereq_offers_tutorial_ships_and_aliases(self) -> None:
        import time
        from spaceinvasion import optimizer as op_mod
        from spaceinvasion.config import StrategyConfig
        from spaceinvasion.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            TutorialStatus,
            TutorialTask,
        )

        # 1. _json_safe cycles + dag20 < 50ms
        cyc_l: list[object] = []
        cyc_l.append(cyc_l)
        cyc_d: dict[str, object] = {}
        cyc_d["self"] = cyc_d
        self.assertEqual(op_mod._json_safe(cyc_l), ["[Cycle]"])
        self.assertEqual(op_mod._json_safe(cyc_d), {"self": "[Cycle]"})

        dag: object = [1, {"k": 2}]
        for _ in range(20):
            dag = [dag, dag, dag]
        t0 = time.perf_counter()
        _ = op_mod._json_safe(dag)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 50.0)

        # 2. _legal_candidates resolves shipyard_targets aliases & incomplete tutorial category=='ship' tasks
        yard_p = PlanetState(
            planet_id=810,
            name="Yard",
            coords="02:380:14",
            planet_type="normal",
            temperature=25,
            fields_used=15,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 2, "b9": 3},
        )
        tut = TutorialStatus(
            5,
            11,
            (TutorialTask("Build 1 Small transporter", "Small transporter", 1, 0, 1, "ship", "small_transporter"),),
        )
        gs = GameState(
            31,
            1,
            "u",
            1000.0,
            (yard_p,),
            active_planet_id=810,
            researches={f"r{i}": 6 for i in range(1, 10)},
            tutorial=tut,
        )
        cfg = StrategyConfig(allow_shipyard=True, shipyard_targets={"s4": 1, "Small fighter": 3, "1": 99})
        sim = op_mod._SimState.from_game_state(gs)
        cands = op_mod._legal_candidates(sim, cfg)
        ship_tids = {c.target_id for c in cands if c.kind == "BUILD_SHIP"}
        self.assertIn(4, ship_tids)
        self.assertIn(1, ship_tids)

        # 3. _materialize_planned_actions blocks forged BuildingOffer with missing prerequisite (b17 needs b7>=12 & r2>=10, currently b7=2)
        forged_b17 = BuildingOffer(17, "Microsystem accelerator", 0, 1, Resources(500, 500, 500, 0), True, "Build", "?b=17")
        gs_forged = GameState(
            31,
            1,
            "u",
            1000.0,
            (PlanetState(**{**yard_p.__dict__, "building_offers": (forged_b17,)}),),
            active_planet_id=810,
        )
        mat = op_mod._materialize_planned_actions(
            gs_forged,
            (op_mod.SimAction("BUILD", 810, target_id=17),),
            StrategyConfig(),
            0.85,
            attack_history_stats={},
        )
        self.assertEqual(len(mat), 1)
        self.assertFalse(mat[0].executable_now)


if __name__ == "__main__":
    unittest.main()
