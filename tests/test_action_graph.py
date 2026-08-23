"""Unit tests for the pure Action Graph DAG builder and config override engine."""

from __future__ import annotations

import copy
import json
import unittest

from spaceinvasion.action_graph import (
    build_action_graph,
    merge_strategy_config_overrides,
)
from spaceinvasion.config import StrategyConfig
from spaceinvasion.optimizer import (
    get_last_optimizer_summary,
    run_optimizer,
)
from spaceinvasion.state import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    UnitOffer,
)
from spaceinvasion.strategy import PlannedAction, plan_actions


def _mk_planet(
    planet_id: int = 101,
    name: str = "Homeworld",
    coords: str = "01:042:07",
    *,
    planet_type: str = "normal",
    temperature: int = 25,
    fields_used: int = 20,
    fields_max: int = 230,
    resources: Resources = Resources(20_000, 15_000, 10_000, 8_000),
    storage: Resources = Resources(100_000, 100_000, 100_000, 100_000),
    energy: EnergyBalance = EnergyBalance(300, 120),
    buildings: dict[str, int] | None = None,
    ships: dict[str, int] | None = None,
    building_offers: tuple[BuildingOffer, ...] = (),
    ship_offers: tuple[UnitOffer, ...] = (),
    economy_rates: dict[str, int] | None = None,
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
        buildings=dict(buildings or {"b1": 4, "b2": 3, "b3": 4, "b4": 3, "b5": 6, "b7": 2}),
        ships=dict(ships or {}),
        building_offers=building_offers,
        ship_offers=ship_offers,
        economy_rates=dict(economy_rates or {}),
    )


class TestActionGraphEngine(unittest.TestCase):
    def _assert_strict_dag(self, graph: dict) -> None:
        # Must serialize cleanly with allow_nan=False
        dumped = json.dumps(graph, allow_nan=False)
        self.assertNotIn("NaN", dumped)
        self.assertNotIn("Infinity", dumped)

        nodes = graph["nodes"]
        edges = graph["edges"]
        by_id = {n["id"]: n for n in nodes}
        self.assertEqual(len(by_id), len(nodes))
        seen_pairs: set[tuple[str, str]] = set()

        for idx, n in enumerate(nodes):
            self.assertEqual(n["id"], f"act_{idx}")
            self.assertEqual(n["order_index"], idx)
            self.assertGreaterEqual(n["topological_layer"], 0)

        for edge in edges:
            src = edge["source"]
            dst = edge["target"]
            self.assertIn(src, by_id)
            self.assertIn(dst, by_id)
            self.assertNotIn((src, dst), seen_pairs, f"Duplicate edge {(src, dst)}")
            seen_pairs.add((src, dst))
            u_node = by_id[src]
            v_node = by_id[dst]
            self.assertLess(
                u_node["topological_layer"],
                v_node["topological_layer"],
                f"Topological layer violation on {edge}: {u_node['topological_layer']} -> {v_node['topological_layer']}",
            )
            if not (u_node["kind"] == "ALERT_HOSTILE" and v_node["kind"] == "ESCAPE_FLIGHT"):
                self.assertLess(u_node["order_index"], v_node["order_index"])
            self.assertTrue(edge["label"].strip())
            self.assertIn(edge["primary_type"], edge["types"])

    def test_merge_strategy_config_overrides_immutability_and_partial_shipyard_targets(self) -> None:
        base = StrategyConfig(
            planner_mode="priority",
            playstyle="fleeter",
            ga_population_size=24,
            shipyard_targets={"small-fighter": 12, "spy-probe": 10, "recycler": 4},
        )
        snapshot_targets = copy.deepcopy(base.shipyard_targets)

        eff, changed = merge_strategy_config_overrides(
            base,
            {
                "ga_population_size": 48,
                "ga_horizon_steps": 9,
                "allow_raids": True,
                "shipyard_targets": {"small-fighter": 30},
            },
            planner_mode_override="ga_nn",
        )

        # Base config and its nested dict must remain 100% unmutated
        self.assertEqual(base.planner_mode, "priority")
        self.assertEqual(base.ga_population_size, 24)
        self.assertFalse(base.allow_raids)
        self.assertEqual(base.shipyard_targets, snapshot_targets)

        # Effective config reflects overrides + preserved sibling shipyard keys
        self.assertEqual(eff.planner_mode, "ga_nn")
        self.assertEqual(eff.ga_population_size, 48)
        self.assertEqual(eff.ga_horizon_steps, 9)
        self.assertTrue(eff.allow_raids)
        self.assertEqual(eff.shipyard_targets["small-fighter"], 30)
        self.assertEqual(eff.shipyard_targets["spy-probe"], 10)
        self.assertEqual(eff.shipyard_targets["recycler"], 4)
        self.assertEqual(
            changed,
            ["allow_raids", "ga_horizon_steps", "ga_population_size", "planner_mode", "shipyard_targets"],
        )

    def test_empty_state_and_planet_filter_produce_clean_acyclic_graph(self) -> None:
        cfg = StrategyConfig()
        g_none = build_action_graph(None, (), effective_config=cfg, active_config=cfg)
        self._assert_strict_dag(g_none)
        self.assertFalse(g_none["state_available"])
        self.assertEqual(g_none["nodes"], [])
        self.assertEqual(g_none["edges"], [])
        self.assertFalse(g_none["is_dirty"])

        empty_state = GameState(universe_id=1, user_id=1, nickname="cmdr", timestamp=100.0, planets=())
        g_empty = build_action_graph(empty_state, (), effective_config=cfg, active_config=cfg)
        self._assert_strict_dag(g_empty)
        self.assertTrue(g_empty["state_available"])
        self.assertEqual(g_empty["nodes"], [])
        self.assertEqual(g_empty["edges"], [])

        p1 = _mk_planet(101, "Alpha", "01:010:01")
        p2 = _mk_planet(202, "Beta", "01:010:02")
        st = GameState(1, 1, "cmdr", 100.0, (p1, p2), active_planet_id=101)
        acts = [
            PlannedAction("P2", "BUILD", 101, "01:010:01", "Pig iron mine", "ROI", target_id=1, target_level=5, executable_now=True),
            PlannedAction("P2", "BUILD", 202, "01:010:02", "Spice mine", "ROI", target_id=4, target_level=4, executable_now=True),
            PlannedAction("P2", "RESEARCH", 101, "01:010:01", "Energy technology", "Tech", target_id=2, target_level=1, executable_now=False),
        ]
        g_filtered = build_action_graph(st, acts, effective_config=cfg, active_config=cfg, planet_filter=202)
        self._assert_strict_dag(g_filtered)
        self.assertEqual(g_filtered["planet_filter"], 202)
        pids = [n["planet_id"] for n in g_filtered["nodes"] if n["kind"] != "RESEARCH"]
        self.assertEqual(pids, [202])
        self.assertTrue(any(n["kind"] == "RESEARCH" for n in g_filtered["nodes"]))

    def test_p0_hostile_alert_to_escape_flight_and_survival_gate(self) -> None:
        p1 = _mk_planet(
            101,
            "Homeworld",
            "01:042:07",
            ships={"s1": 8, "s4": 10},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 4, 5, Resources(1000, 400, 0, 0), True, "Available", "indexInternal.es?b=1"),
            ),
        )
        st = GameState(
            universe_id=1,
            user_id=1,
            nickname="cmdr",
            timestamp=1000.0,
            planets=(p1,),
            active_planet_id=101,
            fleet_alerts=(FleetAlert("ATTACK", True, "Hostile fleet arriving at [01:042:07]"),),
        )
        cfg = StrategyConfig(allow_fleetsave=True)
        actions = plan_actions(st, config=cfg)
        graph = build_action_graph(st, actions, effective_config=cfg, active_config=cfg)
        self._assert_strict_dag(graph)

        kinds = [n["kind"] for n in graph["nodes"]]
        self.assertIn("ALERT_HOSTILE", kinds)
        self.assertIn("ESCAPE_FLIGHT", kinds)
        alert_node = next(n for n in graph["nodes"] if n["kind"] == "ALERT_HOSTILE")
        esc_node = next(n for n in graph["nodes"] if n["kind"] == "ESCAPE_FLIGHT")
        self.assertEqual(alert_node["status"], "blocked_gate")
        self.assertEqual(esc_node["status"], "ready")

        has_alert_to_esc = any(
            e["source"] == alert_node["id"]
            and e["target"] == esc_node["id"]
            and "priority_gate" in e["types"]
            for e in graph["edges"]
        )
        self.assertTrue(has_alert_to_esc)
        non_p0 = [n for n in graph["nodes"] if n["priority"] != "P0"]
        if non_p0:
            self.assertTrue(
                any(
                    e["source"] == esc_node["id"] and "priority_gate" in e["types"]
                    for e in graph["edges"]
                )
            )

    def test_tech_prerequisite_chain_b7_b8_r2_b9_ships_and_recon(self) -> None:
        p1 = _mk_planet(
            101,
            "Homeworld",
            "01:042:07",
            buildings={"b1": 3, "b5": 4, "b7": 1, "b8": 0, "b9": 0},
        )
        st = GameState(1, 1, "cmdr", 500.0, (p1,), active_planet_id=101, researches={})
        chain = [
            PlannedAction("P2", "BUILD", 101, "01:042:07", "Development center", "Unlock lab", target_id=7, target_level=2, executable_now=True),
            PlannedAction("P2", "WAIT_RESOURCES", 101, "01:042:07", "Development center", "Unlock WF", target_id=7, target_level=3, executable_now=False),
            PlannedAction("P2", "WAIT_RESOURCES", 101, "01:042:07", "Research lab", "Unlock research", target_id=8, target_level=1, executable_now=False),
            PlannedAction("P2", "RESEARCH", 101, "01:042:07", "Energy technology", "Unlock WF & engines", target_id=2, target_level=1, executable_now=False),
            PlannedAction("P2", "WAIT_RESOURCES", 101, "01:042:07", "Weapons factory", "Unlock shipyard", target_id=9, target_level=1, executable_now=False),
            PlannedAction("P2", "BUILD_SHIP", 101, "01:042:07", "Small fighter", "Build fighters", target_id=4, target_level=5, executable_now=False),
            PlannedAction("P2", "BUILD_SHIP", 101, "01:042:07", "Spy probe", "Build probes", target_id=10, target_level=2, executable_now=False),
            PlannedAction("P2", "SCAN_GALAXY", 101, "01:042:07", "Galaxy [01:042]", "Scan system 01:042", executable_now=True),
            PlannedAction("P2", "SPY", 101, "01:042:07", "01:042:09 (Inactive)", "Scout target", target_id=10, target_level=1, executable_now=False),
            PlannedAction("P2", "ATTACK", 101, "01:042:07", "01:042:09 (Inactive)", "Raid target", executable_now=False),
        ]
        graph = build_action_graph(st, chain, effective_config=StrategyConfig())
        self._assert_strict_dag(graph)

        edge_map = {(e["source"], e["target"]): e for e in graph["edges"]}
        # b7 L2 -> b7 L3 (level_chain)
        self.assertIn("level_chain", edge_map[("act_0", "act_1")]["types"])
        # b7 L2 (or L3) -> b8 L1 (prerequisite)
        self.assertTrue(
            any(
                e["target"] == "act_2" and "prerequisite" in e["types"]
                for e in graph["edges"]
            )
        )
        # b8 L1 -> r2 L1 (prerequisite)
        self.assertIn("prerequisite", edge_map[("act_2", "act_3")]["types"])
        # b7 L3 -> b9 L1 AND r2 L1 -> b9 L1 (prerequisites)
        self.assertIn("prerequisite", edge_map[("act_1", "act_4")]["types"])
        self.assertIn("prerequisite", edge_map[("act_3", "act_4")]["types"])
        # b9 L1 -> Small fighter (act_5) & Spy probe (act_6)
        self.assertIn("prerequisite", edge_map[("act_4", "act_5")]["types"])
        self.assertIn("prerequisite", edge_map[("act_4", "act_6")]["types"])
        # Spy probe -> SPY, Small fighter -> ATTACK, SCAN_GALAXY -> SPY/ATTACK
        self.assertIn("prerequisite", edge_map[("act_6", "act_8")]["types"])
        self.assertIn("prerequisite", edge_map[("act_5", "act_9")]["types"])
        self.assertIn("prerequisite", edge_map[("act_7", "act_8")]["types"])
        self.assertIn("prerequisite", edge_map[("act_7", "act_9")]["types"])

    def test_negative_energy_p1_power_before_mine_and_proactive_power_support(self) -> None:
        p1 = _mk_planet(
            101,
            "DeficitWorld",
            "01:042:07",
            energy=EnergyBalance(produced=80, used=110),
            buildings={"b1": 5, "b5": 3},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 3, 4, Resources(800, 320, 0, 0), True, "Available", "indexInternal.es?b=5"),
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(900, 400, 0, 0), True, "Available", "indexInternal.es?b=1"),
            ),
        )
        st = GameState(1, 1, "cmdr", 600.0, (p1,), active_planet_id=101)
        actions = plan_actions(st, config=StrategyConfig())
        graph = build_action_graph(st, actions, effective_config=StrategyConfig())
        self._assert_strict_dag(graph)

        fus_node = next(n for n in graph["nodes"] if n["target_id"] == 5)
        mine_node = next(n for n in graph["nodes"] if n["target_id"] == 1)
        self.assertEqual(fus_node["category"], "energy")
        self.assertLess(fus_node["topological_layer"], mine_node["topological_layer"])
        self.assertTrue(
            any(
                e["source"] == fus_node["id"]
                and e["target"] == mine_node["id"]
                and "energy_support" in e["types"]
                for e in graph["edges"]
            )
        )

    def test_blast_furnace_2_to_1_throttle_0_and_100_and_mining_tech_exclusion(self) -> None:
        p1 = _mk_planet(
            101,
            "SmelterColony",
            "01:042:07",
            resources=Resources(200, 5_000, 2_000, 2_000),
            buildings={"b1": 4, "b2": 5, "b3": 4, "b4": 3, "b5": 7, "b8": 2},
            economy_rates={"b2": 100},
        )
        st = GameState(1, 1, "cmdr", 700.0, (p1,), active_planet_id=101, researches={"r2": 4})

        # Scenario A: Pause b2 -> 0% to conserve 2:1 Pig iron + r1 Mining tech must NOT feed Metal-only action
        acts_pause = [
            PlannedAction(
                "P1",
                "THROTTLE_BUILDING",
                101,
                "01:042:07",
                "Blast furnace",
                "Pause 2:1 Pig iron drain",
                target_id=2,
                target_level=0,
                executable_now=True,
            ),
            PlannedAction(
                "P2",
                "RESEARCH",
                101,
                "01:042:07",
                "Mining technology",
                "Boost mines",
                target_id=1,
                target_level=2,
                executable_now=True,
            ),
            PlannedAction(
                "P2",
                "WAIT_RESOURCES",
                101,
                "01:042:07",
                "Pig iron mine",
                "Need Pig iron",
                target_id=1,
                target_level=5,
                cost=Resources(4000, 1000, 0, 0),
                missing=Resources(3800, 0, 0, 0),
                eta_bottleneck="pig_iron",
            ),
            PlannedAction(
                "P2",
                "WAIT_RESOURCES",
                101,
                "01:042:07",
                "Research lab",
                "Need Metal only",
                target_id=8,
                target_level=3,
                cost=Resources(100, 9000, 0, 0),
                missing=Resources(0, 4000, 0, 0),
                eta_bottleneck="metal",
            ),
            PlannedAction(
                "P3",
                "WAIT_RESOURCES",
                101,
                "01:042:07",
                "Blast furnace",
                "Smelter upgrade",
                target_id=2,
                target_level=6,
                cost=Resources(5000, 2000, 0, 0),
                missing=Resources(4800, 0, 0, 0),
            ),
        ]
        g_pause = build_action_graph(st, acts_pause, effective_config=StrategyConfig())
        self._assert_strict_dag(g_pause)
        edge_pause = {(e["source"], e["target"]): e for e in g_pause["edges"]}
        # THROTTLE 0% -> Pig iron mine (act_2)
        self.assertIn("throttle_control", edge_pause[("act_0", "act_2")]["types"])
        # r1 Mining tech (act_1) -> Pig iron mine (act_2), but NEVER -> Metal-only Research lab (act_3)!
        self.assertIn("resource_feed", edge_pause[("act_1", "act_2")]["types"])
        self.assertNotIn(("act_1", "act_3"), edge_pause)
        # Pig iron mine (act_2) -> Blast furnace (act_4) 2:1 Pig iron feedstock
        self.assertIn("resource_feed", edge_pause[("act_2", "act_4")]["types"])
        self.assertIn("2:1 Pig iron", edge_pause[("act_2", "act_4")]["label"])

        # Scenario B: Resume b2 -> 100% unblocks Metal-waiting action
        acts_resume = [
            PlannedAction(
                "P1",
                "THROTTLE_BUILDING",
                101,
                "01:042:07",
                "Blast furnace",
                "Resume smelting",
                target_id=2,
                target_level=100,
                executable_now=True,
            ),
            PlannedAction(
                "P2",
                "WAIT_RESOURCES",
                101,
                "01:042:07",
                "Research lab",
                "Need Metal",
                target_id=8,
                target_level=3,
                cost=Resources(100, 9000, 0, 0),
                missing=Resources(0, 4000, 0, 0),
                eta_bottleneck="metal",
            ),
        ]
        g_res = build_action_graph(st, acts_resume, effective_config=StrategyConfig())
        self._assert_strict_dag(g_res)
        self.assertEqual(g_res["edges"][0]["primary_type"], "throttle_control")

    def test_warehouse_warn_ratio_overflow_and_cost_exceeds_storage_cap(self) -> None:
        p1 = _mk_planet(
            101,
            "StorageWorld",
            "01:042:07",
            resources=Resources(90_000, 10_000, 10_000, 10_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
        )
        st = GameState(1, 1, "cmdr", 800.0, (p1,), active_planet_id=101)
        acts = [
            PlannedAction(
                "P1",
                "BUILD",
                101,
                "01:042:07",
                "Pig iron warehouse",
                "Pig iron storage at 90% (5% cap risk)",
                target_id=11,
                target_level=1,
                executable_now=True,
            ),
            PlannedAction(
                "P2",
                "BUILD",
                101,
                "01:042:07",
                "Metal warehouse",
                "Expand metal cap for Microsystem accelerator",
                target_id=12,
                target_level=1,
                executable_now=True,
            ),
            PlannedAction(
                "P2",
                "BUILD",
                101,
                "01:042:07",
                "Pig iron mine",
                "ROI mine",
                target_id=1,
                target_level=6,
                executable_now=True,
            ),
            PlannedAction(
                "P2",
                "WAIT_RESOURCES",
                101,
                "01:042:07",
                "Microsystem accelerator",
                "Exceeds metal cap",
                target_id=17,
                target_level=1,
                cost=Resources(50_000, 150_000, 50_000, 20_000),
                missing=Resources(0, 140_000, 40_000, 10_000),
            ),
        ]
        graph = build_action_graph(st, acts, effective_config=StrategyConfig())
        self._assert_strict_dag(graph)
        edge_map = {(e["source"], e["target"]): e for e in graph["edges"]}
        self.assertIn("storage_cap", edge_map[("act_0", "act_2")]["types"])
        self.assertIn("storage_cap", edge_map[("act_1", "act_3")]["types"])

    def test_multi_planet_parallelism_vs_single_planet_queue_and_global_research_slot(self) -> None:
        p1 = _mk_planet(101, "ColonyOne", "01:010:01", buildings={"b1": 4, "b5": 6, "b8": 3})
        p2 = _mk_planet(202, "ColonyTwo", "01:010:02", buildings={"b1": 4, "b5": 6, "b8": 2})
        st = GameState(1, 1, "cmdr", 900.0, (p1, p2), active_planet_id=101)
        acts = [
            PlannedAction("P2", "BUILD", 101, "01:010:01", "Pig iron mine", "Colony 1 build #1", target_id=1, target_level=5, executable_now=True),
            PlannedAction("P2", "BUILD", 202, "01:010:02", "Pig iron mine", "Colony 2 build #1", target_id=1, target_level=5, executable_now=True),
            PlannedAction("P2", "BUILD", 101, "01:010:01", "Kryptonite mine", "Colony 1 build #2", target_id=3, target_level=5, executable_now=True),
            PlannedAction("P2", "RESEARCH", 101, "01:010:01", "Energy technology", "Global research #1", target_id=2, target_level=2, executable_now=True),
            PlannedAction("P2", "RESEARCH", 202, "01:010:02", "Engine research", "Global research #2", target_id=3, target_level=2, executable_now=False),
        ]
        graph = build_action_graph(st, acts, effective_config=StrategyConfig(allow_multi_planet=True))
        self._assert_strict_dag(graph)
        by_id = {n["id"]: n for n in graph["nodes"]}

        # Colony 1 build #1 and Colony 2 build #1 execute in parallel (both topological_layer == 0!)
        self.assertEqual(by_id["act_0"]["topological_layer"], 0)
        self.assertEqual(by_id["act_1"]["topological_layer"], 0)
        self.assertEqual(by_id["act_0"]["lane_id"], "lane:planet:101")
        self.assertEqual(by_id["act_1"]["lane_id"], "lane:planet:202")

        # Colony 1 build #2 queues behind Colony 1 build #1
        self.assertEqual(by_id["act_2"]["topological_layer"], 1)

        # Global research #1 and #2 share "lane:global" and single global research slot
        self.assertEqual(by_id["act_3"]["lane_id"], "lane:global")
        self.assertEqual(by_id["act_4"]["lane_id"], "lane:global")
        self.assertEqual(by_id["act_3"]["topological_layer"], 0)
        self.assertEqual(by_id["act_4"]["topological_layer"], 1)
        self.assertTrue(
            any(
                e["source"] == "act_3"
                and e["target"] == "act_4"
                and "queue_order" in e["types"]
                for e in graph["edges"]
            )
        )

    def test_priority_vs_ga_nn_step_outcomes_and_optimizer_summary_isolation(self) -> None:
        p1 = _mk_planet(
            101,
            "Homeworld",
            "01:042:07",
            resources=Resources(25_000, 18_000, 12_000, 8_000),
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 3, "b5": 6, "b7": 3, "b8": 2, "b9": 2},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 4, 5, Resources(800, 320, 0, 0), True, "Available", "indexInternal.es?b=1"),
                BuildingOffer(3, "Kryptonite mine", 4, 5, Resources(700, 500, 0, 0), True, "Available", "indexInternal.es?b=3"),
                BuildingOffer(4, "Spice mine", 3, 4, Resources(900, 600, 200, 0), True, "Available", "indexInternal.es?b=4"),
            ),
        )
        st = GameState(
            1,
            1,
            "cmdr",
            1000.0,
            (p1,),
            active_planet_id=101,
            researches={"r2": 2, "r3": 2, "r4": 2},
            research_offers=(
                ResearchOffer(2, "r2", "Energy technology", 2, 3, Resources(0, 1600, 3200, 1600), True, "Available", "indexInternal.es?r=2"),
            ),
            speedup_factor=4.0,
        )

        # Record a baseline global summary first
        base_ga_cfg = StrategyConfig(
            planner_mode="ga_nn",
            ga_population_size=6,
            ga_generations=2,
            ga_horizon_steps=4,
            ga_seed=11,
        )
        baseline_opt = run_optimizer(st, config=base_ga_cfg, record_global_summary=True)
        global_before = copy.deepcopy(get_last_optimizer_summary())
        self.assertIsNotNone(global_before)
        self.assertIn("step_outcomes", baseline_opt.to_dict()["best_simulation"])

        # Now run an isolated simulation with different GA seed/population/generations (record_global_summary=False)
        sim_ga_cfg = StrategyConfig(
            planner_mode="ga_nn",
            ga_population_size=10,
            ga_generations=3,
            ga_horizon_steps=5,
            ga_seed=77,
        )
        sim_opt = run_optimizer(st, config=sim_ga_cfg, record_global_summary=False)
        global_after = get_last_optimizer_summary()
        self.assertEqual(global_after, global_before)

        prio_cfg = StrategyConfig(planner_mode="priority")
        prio_actions = plan_actions(st, config=prio_cfg)
        g_prio = build_action_graph(st, prio_actions, effective_config=prio_cfg, active_config=prio_cfg)
        self._assert_strict_dag(g_prio)
        self.assertEqual(g_prio["mode"], "priority")
        self.assertIsNone(g_prio["ga_summary"])
        self.assertTrue(all(n["ga_step"] is None for n in g_prio["nodes"]))

        g_gann = build_action_graph(
            st,
            list(sim_opt.actions),
            effective_config=sim_ga_cfg,
            active_config=prio_cfg,
            simulated=True,
            optimizer_summary=sim_opt.to_dict(),
        )
        self._assert_strict_dag(g_gann)
        self.assertEqual(g_gann["mode"], "ga_nn")
        self.assertTrue(g_gann["simulated"])
        self.assertTrue(g_gann["is_dirty"])
        self.assertIsNotNone(g_gann["ga_summary"])
        self.assertTrue(any(n["ga_step"] is not None for n in g_gann["nodes"]))

    def test_recycle_and_tutorial_task_classification_slot_edges_and_ga_step_matching(self) -> None:
        p1 = _mk_planet(101, "Homeworld", "01:042:07", ships={"s11": 0})
        st = GameState(1, 1, "cmdr", 1000.0, (p1,), active_planet_id=101)
        acts = [
            PlannedAction(
                "P1",
                "BUILD_SHIP",
                101,
                "01:042:07",
                "Recycler",
                "Build 1 Recycler for debris harvest",
                target_id=11,
                target_level=1,
                executable_now=True,
            ),
            PlannedAction(
                "P1",
                "SCAN_GALAXY",
                101,
                "01:042:07",
                "Galaxy scan",
                "Locate debris field",
                executable_now=True,
            ),
            PlannedAction(
                "P1",
                "RECYCLE",
                101,
                "01:042:07",
                "Recycle debris [01:042:08]",
                "Harvest debris field",
                executable_now=False,
            ),
            PlannedAction(
                "P3",
                "TUTORIAL_TASK",
                101,
                "01:042:07",
                "Tutorial step 9: Recycle a debris field 0 / 1",
                "Await debris collection",
                executable_now=False,
            ),
        ]
        ga_cfg = StrategyConfig(planner_mode="ga_nn")
        opt_summary = {
            "planner_mode": "ga_nn",
            "best_simulation": {
                "fitness": 125.0,
                "step_outcomes": [
                    {
                        "index": 0,
                        "kind": "RECYCLE",
                        "planet_id": 101,
                        "target_id": 11,
                        "target_name": "Recycle debris [01:042:08]",
                        "wait_s": 60.0,
                        "duration_s": 180.0,
                        "start_t": 60.0,
                        "finish_t": 240.0,
                    }
                ],
            },
        }
        graph = build_action_graph(
            st,
            acts,
            effective_config=ga_cfg,
            active_config=ga_cfg,
            optimizer_summary=opt_summary,
        )
        self._assert_strict_dag(graph)
        by_id = {n["id"]: n for n in graph["nodes"]}
        self.assertEqual(by_id["act_2"]["category"], "recon")
        self.assertEqual(by_id["act_2"]["lane_id"], "lane:planet:101")
        self.assertIsNotNone(by_id["act_2"]["ga_step"])
        self.assertEqual(by_id["act_2"]["ga_step"]["finish_t"], 240.0)
        self.assertEqual(by_id["act_3"]["category"], "tutorial")

        edge_map = {(e["source"], e["target"]): e for e in graph["edges"]}
        self.assertIn("prerequisite", edge_map[("act_0", "act_2")]["types"])
        self.assertIn("prerequisite", edge_map[("act_1", "act_2")]["types"])
        self.assertIn("queue_order", edge_map[("act_1", "act_2")]["types"])

    def test_adversarial_storage_energy_target_ids_and_partial_shipyard_parity(self) -> None:
        p_adv = _mk_planet(
            101,
            "Homeworld",
            "01:042:07",
            storage=Resources(float("nan"), float("inf"), None, 10**300),  # type: ignore[arg-type]
            energy=None,  # type: ignore[arg-type]
            buildings={"b1": 4, "b5": 5, "b8": 1, "b9": 1, "b10": 1, "b11": 1},
        )
        st = GameState(1, 1, "cmdr", 1000.0, (p_adv,), active_planet_id=101)

        partial_cfg = StrategyConfig(shipyard_targets={"small-fighter": 17})
        g_parity = build_action_graph(st, [], effective_config=partial_cfg, active_config=partial_cfg)
        self._assert_strict_dag(g_parity)
        self.assertFalse(g_parity["is_dirty"])
        self.assertEqual(g_parity["changed_keys"], [])
        self.assertEqual(g_parity["active_config"], g_parity["effective_config"])
        self.assertEqual(g_parity["active_config"]["shipyard_targets"]["small-fighter"], 17)
        self.assertIn("spy-probe", g_parity["active_config"]["shipyard_targets"])

        huge_digits = "9" * 5000
        acts = [
            PlannedAction("P1", "BUILD", 101, "01:042:07", "Spice store", "Expand spice cap", target_id=14, target_level=2, executable_now=True),
            PlannedAction("P1", "BUILD", 101, "01:042:07", "Fusion plant", "Prefixed target_id", target_id="b5", target_level=6, executable_now=True),  # type: ignore[arg-type]
            PlannedAction("P2", "RESEARCH", 101, "01:042:07", "Energy technology", "Prefixed research id", target_id="r2", target_level=2, executable_now=True),  # type: ignore[arg-type]
            PlannedAction("P2", "BUILD_SHIP", 101, "01:042:07", "Spy probe", "Prefixed ship id", target_id="s10", target_level=2, executable_now=True),  # type: ignore[arg-type]
            PlannedAction("P2", "BUILD", 101, "01:042:07", "Pig iron mine", "Unicode superscript target_id", target_id="b\u00b2", target_level=5, cost=Resources(500, 500, 500, 10**305), executable_now=False),  # type: ignore[arg-type]
            PlannedAction("P2", "RESEARCH", 101, "01:042:07", "Engine research", "Unicode digit research", target_id="\u00b2", target_level=2, executable_now=False),  # type: ignore[arg-type]
            PlannedAction("P2", "BUILD_SHIP", 101, "01:042:07", "Recycler", "5000-digit ship id", target_id="s" + huge_digits, target_level=1, executable_now=False),  # type: ignore[arg-type]
            PlannedAction("P2", "BUILD", 101, "01:042:07", "Metal mine", "5000-digit build id", target_id=huge_digits, target_level=4, executable_now=False),  # type: ignore[arg-type]
        ]
        ga_cfg = StrategyConfig(planner_mode="ga_nn")
        opt_summary = {
            "planner_mode": "ga_nn",
            "best_simulation": {
                "fitness": 88.0,
                "step_outcomes": [
                    {"index": 0, "kind": "BUILD", "planet_id": 101, "target_id": "b5", "target_name": "Fusion plant", "target_level": 6, "finish_t": 30.0},
                    {"index": 1, "kind": "BUILD", "planet_id": 101, "target_id": "b\u00b2", "target_name": "Unknown", "finish_t": 45.0},
                    {"index": 2, "kind": "BUILD_SHIP", "planet_id": 101, "target_id": "s" + huge_digits, "target_name": "Unknown", "finish_t": 60.0},
                ],
            },
        }
        g_adv = build_action_graph(st, acts, effective_config=ga_cfg, active_config=ga_cfg, optimizer_summary=opt_summary)
        self._assert_strict_dag(g_adv)
        by_id = {n["id"]: n for n in g_adv["nodes"]}
        self.assertIsNotNone(by_id["act_1"]["ga_step"])
        self.assertEqual(by_id["act_1"]["ga_step"]["finish_t"], 30.0)
        edge_map = {(e["source"], e["target"]): e for e in g_adv["edges"]}
        self.assertIn(("act_0", "act_4"), edge_map)
        self.assertIn("storage_cap", edge_map[("act_0", "act_4")]["types"])

    def test_demolish_building_category_slot_edges_and_ga_step_matching(self) -> None:
        p1 = _mk_planet(
            101,
            "Homeworld",
            "01:042:07",
            fields_used=200,
            fields_max=200,
            energy=EnergyBalance(500, 120),
            buildings={"b1": 8, "b5": 10, "b6": 2},
        )
        st = GameState(1, 1, "cmdr", 1000.0, (p1,), active_planet_id=101)
        acts = [
            PlannedAction(
                "P1",
                "BUILD",
                101,
                "01:042:07",
                "Fusion plant",
                "Upgrade Fusion plant before tearing down Thermosolar plant L2",
                target_id=5,
                target_level=11,
                executable_now=True,
            ),
            PlannedAction(
                "P1",
                "DEMOLISH_BUILDING",
                101,
                "01:042:07",
                "Thermosolar plant",
                "Tear down Thermosolar plant (L2 -> L1) to reclaim planetary building fields",
                target_id=6,
                target_level=1,
                executable_now=False,
            ),
            PlannedAction(
                "P1",
                "DEMOLISH_BUILDING",
                101,
                "01:042:07",
                "Thermosolar plant",
                "Tear down Thermosolar plant (L1 -> L0) to reclaim planetary building fields",
                target_id=6,
                target_level=0,
                executable_now=False,
            ),
            PlannedAction(
                "P2",
                "BUILD",
                101,
                "01:042:07",
                "Pig iron mine",
                "Best mine ROI",
                target_id=1,
                target_level=9,
                executable_now=False,
            ),
        ]
        ga_cfg = StrategyConfig(planner_mode="ga_nn")
        opt_summary = {
            "planner_mode": "ga_nn",
            "best_simulation": {
                "fitness": 120.0,
                "step_outcomes": [
                    {"index": 0, "kind": "BUILD", "planet_id": 101, "target_id": 5, "target_level": 11, "finish_t": 40.0},
                    {"index": 1, "kind": "DEMOLISH_BUILDING", "planet_id": 101, "target_id": 6, "target_level": 1, "finish_t": 95.0},
                    {"index": 2, "kind": "DEMOLISH_BUILDING", "planet_id": 101, "target_id": "b6", "target_level": 0, "finish_t": 130.0},
                ],
            },
        }
        graph = build_action_graph(st, acts, effective_config=ga_cfg, active_config=ga_cfg, optimizer_summary=opt_summary)
        self._assert_strict_dag(graph)
        by_id = {n["id"]: n for n in graph["nodes"]}
        self.assertEqual(by_id["act_1"]["category"], "energy")
        self.assertEqual(by_id["act_1"]["lane_id"], "lane:planet:101")
        self.assertIsNotNone(by_id["act_1"]["ga_step"])
        self.assertEqual(by_id["act_1"]["ga_step"]["finish_t"], 95.0)
        self.assertIsNotNone(by_id["act_2"]["ga_step"])
        self.assertEqual(by_id["act_2"]["ga_step"]["finish_t"], 130.0)

        edge_map = {(e["source"], e["target"]): e for e in graph["edges"]}
        self.assertIn("energy_support", edge_map[("act_0", "act_1")]["types"])
        self.assertIn("queue_order", edge_map[("act_0", "act_1")]["types"])
        self.assertIn("level_chain", edge_map[("act_1", "act_2")]["types"])
        self.assertIn("prerequisite", edge_map[("act_1", "act_3")]["types"])


if __name__ == "__main__":
    unittest.main()
