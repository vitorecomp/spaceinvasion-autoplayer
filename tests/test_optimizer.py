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


if __name__ == "__main__":
    unittest.main()
