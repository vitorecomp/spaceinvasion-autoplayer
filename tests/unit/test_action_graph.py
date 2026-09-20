"""Unit tests for the pure Action Graph DAG builder and config override engine."""

from __future__ import annotations

import copy
import json
import unittest

from spaceinvasion.action_graph import (
    build_action_graph,
    build_ai_planner_graph,
    build_fleet_planner_graph,
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
    QueueJob,
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

    def _assert_generic_strict_dag(self, graph: dict) -> None:
        dumped = json.dumps(graph, allow_nan=False)
        self.assertNotIn("NaN", dumped)
        self.assertNotIn("Infinity", dumped)

        nodes = graph["nodes"]
        edges = graph["edges"]
        by_id = {n["id"]: n for n in nodes}
        self.assertEqual(len(by_id), len(nodes))
        seen_pairs: set[tuple[str, str]] = set()
        for idx, n in enumerate(nodes):
            self.assertEqual(n["order_index"], idx)
            self.assertGreaterEqual(n["topological_layer"], 0)
        for edge in edges:
            src = edge["source"]
            dst = edge["target"]
            self.assertIn(src, by_id)
            self.assertIn(dst, by_id)
            self.assertNotIn((src, dst), seen_pairs)
            seen_pairs.add((src, dst))
            self.assertLess(
                by_id[src]["topological_layer"],
                by_id[dst]["topological_layer"],
                f"Layer violation on {edge}",
            )
            self.assertTrue(edge["label"].strip())
            self.assertIn(edge["primary_type"], edge["types"])

    def test_build_ai_planner_graph_windowing_filters_and_causal_edges(self) -> None:
        p1 = _mk_planet(
            101,
            "Homeworld",
            "01:042:07",
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            buildings={"b1": 5, "b2": 3, "b5": 6, "b7": 1, "b8": 0, "b9": 0},
        )
        p2 = _mk_planet(
            202,
            "ColonyTwo",
            "01:042:08",
            resources=Resources(500, 500, 500, 500),
            buildings={"b1": 2, "b5": 3},
        )
        st = GameState(1, 1, "cmdr", 1000.0, (p1, p2), active_planet_id=101, researches={"r1": 1, "r2": 0})

        # Empty / None plan_result
        empty_g = build_ai_planner_graph(st, None)
        self._assert_generic_strict_dag(empty_g)
        self.assertEqual(empty_g["planner_mode"], "ai_planner")
        self.assertEqual(empty_g["nodes"], [])
        self.assertEqual(empty_g["edges"], [])

        plan_result = {
            "steps": [
                # Step 1 already completed on p1 (b1 is already L5!)
                {
                    "seq": 1,
                    "start_sec": 0.0,
                    "duration_sec": 300.0,
                    "finish_sec": 300.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "01:042:07",
                    "planet_name": "Homeworld",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 5,
                    "cost": {"pig_iron": 1000, "metal": 400, "kryptonite": 0, "spice": 0},
                },
                # Step 2: b5 L7 (energy producer on p1)
                {
                    "seq": 2,
                    "start_sec": 300.0,
                    "duration_sec": 600.0,
                    "finish_sec": 900.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "01:042:07",
                    "planet_name": "Homeworld",
                    "entity_key": "b5",
                    "entity_name": "Fusion plant",
                    "target_level": 7,
                    "cost": {"pig_iron": 2000, "metal": 800, "kryptonite": 0, "spice": 0},
                },
                # Step 3: b1 L6 (pig iron mine on p1, consumes energy -> energy_support from b5)
                {
                    "seq": 3,
                    "start_sec": 900.0,
                    "duration_sec": 900.0,
                    "finish_sec": 1800.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "01:042:07",
                    "planet_name": "Homeworld",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                    "cost": {"pig_iron": 3000, "metal": 1200, "kryptonite": 0, "spice": 0},
                },
                # Step 4: b1 L7 (level_chain from b1 L6)
                {
                    "seq": 4,
                    "start_sec": 1800.0,
                    "duration_sec": 1200.0,
                    "finish_sec": 3000.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "01:042:07",
                    "planet_name": "Homeworld",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 7,
                    "cost": {"pig_iron": 4500, "metal": 1800, "kryptonite": 0, "spice": 0},
                },
                # Step 5: b2 L4 (Blast furnace -> resource_feed 2:1 Pig iron from b1)
                {
                    "seq": 5,
                    "start_sec": 3000.0,
                    "duration_sec": 1500.0,
                    "finish_sec": 4500.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "01:042:07",
                    "planet_name": "Homeworld",
                    "entity_key": "b2",
                    "entity_name": "Blast furnace",
                    "target_level": 4,
                    "cost": {"pig_iron": 4000, "metal": 2000, "kryptonite": 0, "spice": 0},
                },
                # Step 6: b7 L2 -> Step 7: b8 L1 (prerequisite b7 L2 -> b8 L1) -> Step 8: r2 L1 (prerequisite b8 L1 -> r2 L1)
                {
                    "seq": 6,
                    "start_sec": 4500.0,
                    "duration_sec": 1800.0,
                    "finish_sec": 6300.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "01:042:07",
                    "planet_name": "Homeworld",
                    "entity_key": "b7",
                    "entity_name": "Development center",
                    "target_level": 2,
                    "cost": {"pig_iron": 2500, "metal": 2500, "kryptonite": 1000, "spice": 0},
                },
                {
                    "seq": 7,
                    "start_sec": 6300.0,
                    "duration_sec": 2400.0,
                    "finish_sec": 8700.0,
                    "week": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "01:042:07",
                    "planet_name": "Homeworld",
                    "entity_key": "b8",
                    "entity_name": "Research lab",
                    "target_level": 1,
                    "cost": {"pig_iron": 2000, "metal": 4000, "kryptonite": 2000, "spice": 0},
                },
                {
                    "seq": 8,
                    "start_sec": 8700.0,
                    "duration_sec": 3600.0,
                    "finish_sec": 12300.0,
                    "week": 2,
                    "kind": "RESEARCH",
                    "planet_id": 0,
                    "planet_coords": "",
                    "planet_name": "Empire",
                    "entity_key": "r2",
                    "entity_name": "Energy technology",
                    "target_level": 1,
                    "cost": {"pig_iron": 0, "metal": 800, "kryptonite": 1600, "spice": 800},
                },
                # Step 9: ColonyTwo b1 L3 in week 2
                {
                    "seq": 9,
                    "start_sec": 610000.0,
                    "duration_sec": 1200.0,
                    "finish_sec": 611200.0,
                    "week": 2,
                    "kind": "BUILD",
                    "planet_id": 202,
                    "planet_coords": "01:042:08",
                    "planet_name": "ColonyTwo",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 3,
                    "cost": {"pig_iron": 900, "metal": 400, "kryptonite": 0, "spice": 0},
                },
            ]
        }

        # Default window skips completed Step 1
        g = build_ai_planner_graph(st, plan_result, window_size=40)
        self._assert_generic_strict_dag(g)
        self.assertEqual(g["planner_mode"], "ai_planner")
        self.assertEqual(g["summary"]["total_plan_steps"], 9)
        self.assertEqual(g["summary"]["completed_steps"], 1)
        self.assertEqual(g["summary"]["remaining_steps"], 8)
        self.assertEqual([n["step_seq"] for n in g["nodes"]], [2, 3, 4, 5, 6, 7, 8, 9])

        by_seq = {n["step_seq"]: n for n in g["nodes"]}
        self.assertEqual(by_seq[2]["status"], "ready")
        self.assertEqual(by_seq[9]["status"], "waiting_resources")
        self.assertIn("m", by_seq[2]["duration_text"])

        edge_map = {(e["source"], e["target"]): e for e in g["edges"]}
        # b5 L7 -> b1 L6 (energy_support + queue_order)
        self.assertIn("energy_support", edge_map[("ai_2", "ai_3")]["types"])
        self.assertIn("queue_order", edge_map[("ai_2", "ai_3")]["types"])
        # b1 L6 -> b1 L7 (level_chain)
        self.assertIn("level_chain", edge_map[("ai_3", "ai_4")]["types"])
        # b1 L7 -> b2 L4 (resource_feed 2:1 Pig iron)
        self.assertIn("resource_feed", edge_map[("ai_4", "ai_5")]["types"])
        # b7 L2 -> b8 L1 -> r2 L1 (prerequisite)
        self.assertIn("prerequisite", edge_map[("ai_6", "ai_7")]["types"])
        self.assertIn("prerequisite", edge_map[("ai_7", "ai_8")]["types"])

        # Window size slicing
        g_win = build_ai_planner_graph(st, plan_result, window_size=3)
        self._assert_generic_strict_dag(g_win)
        self.assertEqual(len(g_win["nodes"]), 3)
        self.assertEqual([n["step_seq"] for n in g_win["nodes"]], [2, 3, 4])

        # Week filter (week 2 only)
        g_w2 = build_ai_planner_graph(st, plan_result, week_filter=2)
        self._assert_generic_strict_dag(g_w2)
        self.assertEqual([n["step_seq"] for n in g_w2["nodes"]], [8, 9])
        self.assertEqual(g_w2["summary"]["week_filter"], 2)

        # Week 1 filter retains completed step 1 for historical inspection
        g_w1 = build_ai_planner_graph(st, plan_result, week_filter=1)
        self._assert_generic_strict_dag(g_w1)
        self.assertEqual(g_w1["nodes"][0]["step_seq"], 1)
        self.assertEqual(g_w1["nodes"][0]["status"], "completed")

        # Planet filter by coords & global
        g_p2 = build_ai_planner_graph(st, plan_result, planet_filter="01:042:08")
        self._assert_generic_strict_dag(g_p2)
        self.assertEqual([n["step_seq"] for n in g_p2["nodes"]], [8, 9])

        g_glob = build_ai_planner_graph(st, plan_result, planet_filter="global")
        self._assert_generic_strict_dag(g_glob)
        self.assertEqual([n["step_seq"] for n in g_glob["nodes"]], [8])

    def test_build_fleet_planner_graph_slot_budgets_inflight_and_all_fleet_kinds(self) -> None:
        p1 = _mk_planet(
            101,
            "HubWorld",
            "01:042:07",
            buildings={"b1": 10, "b9": 6, "b10": 1},  # max_slots = 1 + 1 = 2
            ships={"s1": 10, "s2": 5, "s4": 25, "s9": 15, "s10": 4, "s11": 3},
        )
        p2 = _mk_planet(
            202,
            "StagingColony",
            "01:042:08",
            buildings={"b1": 8, "b10": 0},  # max_slots = 1 + 0 = 1 (will be full!)
            ships={"s4": 10},
        )
        active_queues = (
            QueueJob(
                category="fleet",
                name="Attack [01:042:10]",
                remaining_text="12m 0s",
                remaining_seconds=720.0,
                detail="Outbound raid",
                origin_coords="01:042:07",
            ),
            QueueJob(
                category="fleet",
                name="Recycle [01:042:11]",
                remaining_text="5m 0s",
                remaining_seconds=300.0,
                detail="Debris harvest",
                origin_coords="01:042:08",
            ),
            # Shipyard build on fleet category must NOT count as in-flight fleet slot
            QueueJob(
                category="fleet",
                name="Small fighter (x5)",
                remaining_text="2m 0s",
                remaining_seconds=120.0,
                detail="Shipyard queue",
                origin_coords="01:042:07",
            ),
        )
        st = GameState(
            1,
            1,
            "cmdr",
            2000.0,
            (p1, p2),
            active_planet_id=101,
            active_queues=active_queues,
        )
        planned = [
            # Non-fleet BUILD/RESEARCH must be excluded from Tab 4 Fleet graph
            PlannedAction("P2", "BUILD", 101, "01:042:07", "Pig iron mine", "Excluded", target_id=1, target_level=11, executable_now=True),
            PlannedAction("P2", "RESEARCH", 101, "01:042:07", "Mining technology", "Excluded", target_id=1, target_level=4, executable_now=True),
            # Fleet & shipyard actions
            PlannedAction("P0", "ALERT_HOSTILE", 101, "01:042:07", "Incoming attack", "Hostile detected", executable_now=False),
            PlannedAction("P0", "ESCAPE_FLIGHT", 101, "01:042:07", "Escape flight", "Save fleet", executable_now=True),
            PlannedAction("P1", "BUILD_SHIP", 101, "01:042:07", "Spy probe", "Replace probes", target_id=10, target_level=2, executable_now=True),
            PlannedAction("P1", "BUILD_SHIP", 101, "01:042:07", "Recycler", "Add recycler", target_id=11, target_level=1, executable_now=True),
            PlannedAction("P1", "BUILD_SHIP", 101, "01:042:07", "Colony ship", "New colony", target_id=8, target_level=1, executable_now=False),
            PlannedAction("P1", "SCAN_GALAXY", 101, "01:042:07", "Galaxy [01:042]", "Scan local system", executable_now=True),
            PlannedAction("P1", "SPY", 101, "01:042:07", "01:042:12 (Inactive)", "Scout target", target_id=10, target_level=1, executable_now=True),
            PlannedAction("P1", "COLONIZE", 101, "01:042:07", "01:042:09", "Colonize slot", target_id=8, target_level=1, executable_now=False),
            PlannedAction("P2", "TRANSPORT", 101, "01:042:07", "Transport to [01:042:08]", "Feed upgrade", executable_now=True),
            PlannedAction("P2", "STATION", 101, "01:042:07", "Station at [01:042:08]", "Deploy raiders", executable_now=True),
            PlannedAction("P2", "ATTACK", 202, "01:042:08", "01:042:12 (Inactive)", "Raid target", executable_now=False),
            PlannedAction("P2", "RECYCLE", 101, "01:042:07", "01:042:12 Debris", "Harvest debris", executable_now=True),
            PlannedAction("P2", "ACTIVATE_TRANSMITTER", 0, "", "Interdimensional Transmitter", "Free spin", executable_now=True),
        ]
        telemetry = {"median_loot_spice_eq": 18500.0, "median_round_trip_seconds": 1800.0, "sample_count": 14}
        spare = {"spare_percent": {"pig_iron": 10.0, "metal": 10.0}, "can_spend": True}

        fg = build_fleet_planner_graph(
            st,
            planned,
            StrategyConfig(),
            attack_telemetry=telemetry,
            spare_budget=spare,
        )
        self._assert_generic_strict_dag(fg)
        self.assertEqual(fg["planner_mode"], "fleet_planner")
        self.assertEqual(fg["attack_telemetry"]["median_loot_spice_eq"], 18500.0)
        self.assertTrue(fg["spare_budget"]["can_spend"])

        # Slot budgets: HubWorld (b10=1 -> 1/2), StagingColony (b10=0 -> 1/1 full)
        sb_by_pid = {b["planet_id"]: b for b in fg["slot_budgets"]}
        self.assertEqual(sb_by_pid[101]["b10_level"], 1)
        self.assertEqual(sb_by_pid[101]["used_slots"], 1)
        self.assertEqual(sb_by_pid[101]["max_slots"], 2)
        self.assertEqual(sb_by_pid[101]["available_slots"], 1)
        self.assertEqual(sb_by_pid[101]["solar_cells"], 15)
        self.assertEqual(sb_by_pid[202]["b10_level"], 0)
        self.assertEqual(sb_by_pid[202]["used_slots"], 1)
        self.assertEqual(sb_by_pid[202]["max_slots"], 1)
        self.assertEqual(sb_by_pid[202]["available_slots"], 0)

        self.assertEqual(fg["summary"]["total_slots_used"], 2)
        self.assertEqual(fg["summary"]["total_slots_max"], 3)
        self.assertEqual(fg["summary"]["in_flight_count"], 2)

        kinds = {n["kind"] for n in fg["nodes"]}
        self.assertNotIn("BUILD", kinds)
        self.assertNotIn("RESEARCH", kinds)
        for expected_kind in (
            "IN_FLIGHT",
            "ALERT_HOSTILE",
            "ESCAPE_FLIGHT",
            "ATTACK",
            "RECYCLE",
            "SPY",
            "SCAN_GALAXY",
            "TRANSPORT",
            "STATION",
            "COLONIZE",
            "BUILD_SHIP",
            "ACTIVATE_TRANSMITTER",
        ):
            self.assertIn(expected_kind, kinds)

        # Full-slot edge on StagingColony: in-flight Recycle (inflight_1) -> planned ATTACK on p2
        p2_inflight = next(n for n in fg["nodes"] if n["in_flight"] and n["planet_id"] == 202)
        p2_attack = next(n for n in fg["nodes"] if n["kind"] == "ATTACK" and n["planet_id"] == 202)
        station_node = next(n for n in fg["nodes"] if n["kind"] == "STATION")
        edge_map = {(e["source"], e["target"]): e for e in fg["edges"]}
        self.assertIn("queue_order", edge_map[(p2_inflight["id"], p2_attack["id"])]["types"])
        # Cross-planet STATION -> ATTACK at destination planet 01:042:08
        self.assertIn("prerequisite", edge_map[(station_node["id"], p2_attack["id"])]["types"])

        # Empty/None state safety
        fg_none = build_fleet_planner_graph(None, [], StrategyConfig())
        self._assert_generic_strict_dag(fg_none)
        self.assertEqual(fg_none["nodes"], [])
        self.assertEqual(fg_none["slot_budgets"], [])

    def test_build_fleet_planner_graph_includes_fleet_research_and_prerequisite_edges(self) -> None:
        from spaceinvasion.planner.action_graph import build_fleet_planner_graph, merge_strategy_config_overrides

        p1 = PlanetState(
            planet_id=101,
            name="HubWorld",
            coords="01:042:05",
            planet_type="normal",
            temperature=25,
            fields_used=60,
            fields_max=240,
            resources=Resources(100_000, 100_000, 100_000, 50_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(1500, 800),
            buildings={"b8": 8, "b9": 8, "b10": 2},
            ships={"s4": 10},
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="tester",
            timestamp=2500.0,
            planets=(p1,),
            active_planet_id=101,
            researches={"r1": 8, "r2": 7, "r3": 7, "r6": 3},
        )
        actions = [
            # Pure economy research -> MUST remain excluded
            PlannedAction(
                priority="P2",
                kind="RESEARCH",
                planet_id=101,
                planet_coords="01:042:05",
                target_id=1,
                target_name="Mining technology",
                target_level=9,
                reason="Economy boost",
            ),
            # Fleet research prereq chain: r3 (Engine) -> r6 (Warp) -> BUILD_SHIP Battleship (s13)
            PlannedAction(
                priority="P2",
                kind="RESEARCH",
                planet_id=101,
                planet_coords="01:042:05",
                target_id=3,
                target_name="Engine research",
                target_level=8,
                reason="Fleet research (spare budget): Engine research L8/8 — unlocks Battleship",
                executable_now=True,
            ),
            PlannedAction(
                priority="P2",
                kind="RESEARCH",
                planet_id=101,
                planet_coords="01:042:05",
                target_id=6,
                target_name="Warp engine",
                target_level=4,
                reason="Fleet research (spare budget): Warp engine L4/4 — unlocks Battleship",
                executable_now=False,
            ),
            PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=101,
                planet_coords="01:042:05",
                target_id=13,
                target_name="Battleship",
                target_level=1,
                reason="Fleet build target (0/8 Battleship)",
                executable_now=False,
            ),
        ]
        fg = build_fleet_planner_graph(st, actions, StrategyConfig())
        self._assert_generic_strict_dag(fg)

        res_nodes = [n for n in fg["nodes"] if n["kind"] == "RESEARCH"]
        self.assertEqual([n["target_id"] for n in res_nodes], [3, 6])
        self.assertTrue(all(n["lane_id"] == "lane:global" for n in res_nodes))
        self.assertEqual(fg["summary"]["planned_research_actions"], 2)

        r3_node = next(n for n in res_nodes if n["target_id"] == 3)
        r6_node = next(n for n in res_nodes if n["target_id"] == 6)
        bs_node = next(n for n in fg["nodes"] if n["kind"] == "BUILD_SHIP" and n["target_id"] == 13)
        edge_map = {(e["source"], e["target"]): e for e in fg["edges"]}
        self.assertIn("prerequisite", edge_map[(r3_node["id"], r6_node["id"])]["types"])
        self.assertIn("prerequisite", edge_map[(r6_node["id"], bs_node["id"])]["types"])

        # Partial merge of fleet_research_targets + allow_fleet_research override
        base_cfg = StrategyConfig(allow_fleet_research=True, fleet_research_targets={"r8": 4, "r9": 5})
        merged_cfg, diff = merge_strategy_config_overrides(
            base_cfg,
            {"allow_fleet_research": False, "fleet_research_targets": {"r9": 0, "r11": 6}},
        )
        self.assertFalse(merged_cfg.allow_fleet_research)
        self.assertEqual(merged_cfg.fleet_research_targets, {"r8": 4, "r11": 6})
        self.assertIn("allow_fleet_research", diff)
        self.assertIn("fleet_research_targets", diff)


if __name__ == "__main__":
    unittest.main()
