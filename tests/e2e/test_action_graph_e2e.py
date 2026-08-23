"""End-to-End tests for the Operational Action Graph UI, mode switching, isolated simulation, and Apply."""

from __future__ import annotations

import json
import unittest

import requests

from tests.e2e.harness import (
    E2ETestHarness,
    FakeSpaceInvasionServer,
    run_ui_in_node,
)
from spaceinvasion.config import StrategyConfig, load_strategy_config


def _make_two_colony_server(*, hostile: bool = False) -> FakeSpaceInvasionServer:
    planets = {
        12476: {
            "planet_id": 12476,
            "name": "Homeworld",
            "coords": "02:380:12",
            "planet_type": "normal",
            "temperature": 28,
            "fields_max": 230,
            "resources": [18000, 12000, 9000, 6000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 320, "used": 140},
            "buildings": {"b1": 4, "b2": 3, "b3": 4, "b4": 3, "b5": 6, "b7": 3, "b8": 2, "b9": 2},
            "ships": {"s1": 6, "s4": 10, "s10": 4},
            "defense": {},
            "economy_rates": {},
            "building_queue": [],
        },
        12477: {
            "planet_id": 12477,
            "name": "OuterColony",
            "coords": "02:380:15",
            "planet_type": "normal",
            "temperature": -45,
            "fields_max": 220,
            "resources": [14000, 9000, 7000, 5000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 260, "used": 95},
            "buildings": {"b1": 3, "b2": 2, "b3": 3, "b4": 4, "b5": 5, "b7": 2},
            "ships": {},
            "defense": {},
            "economy_rates": {},
            "building_queue": [],
        },
    }
    hostile_alerts = (
        ["Hostile attack fleet approaching [02:380:12]"]
        if hostile
        else []
    )
    return FakeSpaceInvasionServer(
        planets=planets,
        researches={"r1": 1, "r2": 2, "r3": 2, "r4": 2},
        hostile_alerts=hostile_alerts,
        speedup_factor=4.0,
    )


class TestActionGraphEndToEnd(unittest.TestCase):
    def test_ag_e2e_01_render_action_graph_tab_and_switch_priority_and_ga_nn_modes(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(
            planner_mode="priority",
            ga_population_size=6,
            ga_generations=2,
            ga_horizon_steps=4,
            ga_seed=17,
        )
        with E2ETestHarness(server=srv, config=cfg) as h:
            dash = h.get_dashboard(refresh=1)
            self.assertIn("action_graph", dash)
            self.assertEqual(dash["action_graph"]["mode"], "priority")
            self.assertGreater(len(dash["action_graph"]["nodes"]), 0)

            ui_res = run_ui_in_node(
                h.base_url,
                r"""
                const initOk = await __waitUntil(() => state.dashboard !== null, 5000);
                if (!initOk) throw new Error("Dashboard failed to load");

                // Quick-jump from Dashboard to Action Graph
                const jumpBtn = document.querySelector('[data-goto-page="action-graph"]');
                if (!jumpBtn) throw new Error("Missing quick-jump button to action-graph");
                jumpBtn.click();

                const agView = document.getElementById("view-action-graph");
                const prioNodes = document.querySelectorAll(".ag-node-card").length;
                const prioEdges = document.querySelectorAll(".ag-edge-path").length;
                const svgMounted = Boolean(document.getElementById("ag-edges-svg"));
                const mainWinGenPrio = document.getElementById("ag-main-window").textContent.toLowerCase().includes("generation");

                // Switch to GA + Neural (ga_nn) mode via header radio button
                document.getElementById("ag-mode-ga_nn").click();
                const gaOk = await __waitUntil(() => state.agSimRunning === 0 && state.actionGraphData?.mode === "ga_nn", 6000);
                if (!gaOk) throw new Error("Failed switching Action Graph to ga_nn");
                const gaNodes = document.querySelectorAll(".ag-node-card").length;
                const gaEngineText = document.getElementById("ag-kpi-engine").textContent;
                const mainWinGenGa = document.getElementById("ag-main-window").textContent.toLowerCase().includes("generation");

                // Switch back to Heuristic (priority) mode
                document.getElementById("ag-mode-priority").click();
                const prioBackOk = await __waitUntil(() => state.agSimRunning === 0 && state.actionGraphData?.mode === "priority", 6000);
                if (!prioBackOk) throw new Error("Failed switching Action Graph back to priority");
                const prioEngineText = document.getElementById("ag-kpi-engine").textContent;

                return {
                  hidden: agView.hidden,
                  prioNodes,
                  prioEdges,
                  svgMounted,
                  gaNodes,
                  gaEngineText,
                  prioEngineText,
                  mainWinGenPrio,
                  mainWinGenGa,
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertFalse(sc["hidden"])
            self.assertGreater(sc["prioNodes"], 0)
            self.assertGreaterEqual(sc["prioEdges"], 0)
            self.assertTrue(sc["svgMounted"])
            self.assertGreater(sc["gaNodes"], 0)
            self.assertIn("GA+NN", sc["gaEngineText"])
            self.assertIn("PRIORITY", sc["prioEngineText"])
            self.assertFalse(sc["mainWinGenPrio"])
            self.assertFalse(sc["mainWinGenGa"])

    def test_ag_e2e_02_isolated_right_panel_parameter_simulation_zero_active_config_mutation(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(
            planner_mode="priority",
            playstyle="fleeter",
            allow_multi_planet=True,
            allow_raids=False,
            ga_population_size=12,
            ga_generations=3,
            ga_horizon_steps=5,
        )
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.get_dashboard(refresh=1)
            srv.page_view_log.clear()
            disk_before = h.config_path.read_text(encoding="utf-8")
            cfg_before = h.api.send_command({"command": "config"})["config"]

            ui_res = run_ui_in_node(
                h.base_url,
                r"""
                await __waitUntil(() => state.dashboard !== null, 5000);
                document.querySelector('[data-page="action-graph"]').click();

                // Edit right-side controls across both subtabs & simulate preview
                document.getElementById("ag-cfg-planner_mode").value = "ga_nn";
                document.getElementById("ag-cfg-playstyle").value = "miner";
                document.getElementById("ag-cfg-ga_population_size").value = "6";
                document.getElementById("ag-cfg-ga_generations").value = "2";
                document.getElementById("ag-cfg-ga_horizon_steps").value = "4";
                document.getElementById("ag-cfg-ga_seed").value = "77";
                document.getElementById("ag-cfg-storage_warn_ratio").value = "0.75";
                document.getElementById("ag-ptab-btn-scope").click();
                const multiChk = document.getElementById("ag-cfg-allow_multi_planet");
                multiChk.checked = false;
                document.getElementById("ag-cfg-allow_raids").checked = true;

                document.getElementById("btn-ag-simulate").click();
                const simOk = await __waitUntil(
                  () => state.agSimRunning === 0 && state.actionGraphData?.mode === "ga_nn" && state.actionGraphData?.simulated === true,
                  6000
                );
                if (!simOk) throw new Error("Simulation preview did not complete");

                // Verify background 5s /dashboard poll does NOT overwrite right-side simulation state
                await loadDashboard({ refresh: false, preserveConfig: true, force: true });

                const updateConfigCount = fetchLog.filter((f) => f.url.includes("/command") && f.body?.command === "update_config").length;
                return {
                  statusPill: document.getElementById("ag-sim-status-pill").textContent,
                  dirtyBadge: document.getElementById("ag-dirty-badge").textContent,
                  simMode: state.actionGraphData?.mode,
                  simPlaystyle: document.getElementById("ag-cfg-playstyle").value,
                  simMultiChecked: document.getElementById("ag-cfg-allow_multi_planet").checked,
                  changedKeys: state.actionGraphData?.changed_keys || [],
                  updateConfigCount,
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertEqual(sc["statusPill"], "SIMULATION PREVIEW · UNSAVED")
            self.assertEqual(sc["dirtyBadge"], "MODIFIED (PREVIEW)")
            self.assertEqual(sc["simMode"], "ga_nn")
            self.assertEqual(sc["simPlaystyle"], "miner")
            self.assertFalse(sc["simMultiChecked"])
            self.assertIn("planner_mode", sc["changedKeys"])
            self.assertIn("allow_multi_planet", sc["changedKeys"])
            self.assertEqual(sc["updateConfigCount"], 0)

            # Zero browser navigations & zero active StrategyConfig / disk config.yaml mutation!
            self.assertEqual(len(srv.page_view_log), 0)
            cfg_after = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg_after, cfg_before)
            self.assertEqual(h.get_dashboard(refresh=0)["planner_mode"], "priority")
            self.assertEqual(h.config_path.read_text(encoding="utf-8"), disk_before)

    def test_ag_e2e_03_explicit_apply_persists_across_bridge_restart(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(planner_mode="priority", playstyle="fleeter", allow_raids=False)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.get_dashboard(refresh=1)

            ui_res = run_ui_in_node(
                h.base_url,
                r"""
                await __waitUntil(() => state.dashboard !== null, 5000);
                document.querySelector('[data-page="action-graph"]').click();

                document.getElementById("ag-cfg-planner_mode").value = "ga_nn";
                document.getElementById("ag-cfg-playstyle").value = "hybrid";
                document.getElementById("ag-cfg-ga_population_size").value = "6";
                document.getElementById("ag-cfg-ga_generations").value = "2";
                document.getElementById("ag-cfg-ga_horizon_steps").value = "4";
                document.getElementById("ag-cfg-ga_seed").value = "88";
                document.getElementById("ag-cfg-combat_safety_margin").value = "1.8";
                document.getElementById("ag-ptab-btn-scope").click();
                document.getElementById("ag-cfg-allow_raids").checked = true;
                document.getElementById("ag-cfg-allow_fleetsave").checked = true;

                document.getElementById("btn-ag-apply").click();
                const appliedOk = await __waitUntil(
                  () => state.configSaving === 0 && !state.agSimDirty && state.dashboard?.config?.planner_mode === "ga_nn" && state.dashboard?.config?.ga_seed === 88,
                  6000
                );
                if (!appliedOk) throw new Error("Explicit Apply failed to persist StrategyConfig");

                return {
                  statusPill: document.getElementById("ag-sim-status-pill").textContent,
                  dirtyBadge: document.getElementById("ag-dirty-badge").textContent,
                  banner: document.getElementById("feedback-banner").textContent,
                  dashBadge: document.getElementById("dash-planner-mode-badge").textContent,
                  cfgViewMode: document.getElementById("cfg-planner_mode").value,
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertEqual(sc["statusPill"], "LIVE ACTIVE CONFIG")
            self.assertEqual(sc["dirtyBadge"], "ACTIVE SYNCED")
            self.assertIn("Applied parameters to active StrategyConfig", sc["banner"])
            self.assertEqual(sc["dashBadge"], "MODE: GA+NN")
            self.assertEqual(sc["cfgViewMode"], "ga_nn")

            # Restart bridge and prove config.yaml persisted cleanly on disk
            h.restart_bridge()
            reloaded_disk = load_strategy_config(h.config_path)
            self.assertEqual(reloaded_disk.planner_mode, "ga_nn")
            self.assertEqual(reloaded_disk.playstyle, "hybrid")
            self.assertEqual(reloaded_disk.ga_population_size, 6)
            self.assertEqual(reloaded_disk.ga_generations, 2)
            self.assertEqual(reloaded_disk.ga_horizon_steps, 4)
            self.assertEqual(reloaded_disk.ga_seed, 88)
            self.assertAlmostEqual(reloaded_disk.combat_safety_margin, 1.8, places=3)
            self.assertTrue(reloaded_disk.allow_raids)
            self.assertTrue(reloaded_disk.allow_fleetsave)

            post_restart_cfg = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(post_restart_cfg["planner_mode"], "ga_nn")
            self.assertEqual(post_restart_cfg["ga_seed"], 88)

    def test_ag_e2e_04_hostile_p0_multi_planet_invalid_input_and_english_only_verification(self) -> None:
        srv = _make_two_colony_server(hostile=True)
        cfg = StrategyConfig(planner_mode="priority", allow_fleetsave=True, allow_multi_planet=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            dash = h.get_dashboard(refresh=1)
            ag_p0 = dash["action_graph"]
            kinds = [n["kind"] for n in ag_p0["nodes"]]
            self.assertIn("ALERT_HOSTILE", kinds)
            self.assertIn("ESCAPE_FLIGHT", kinds)
            self.assertTrue(
                any(
                    e["primary_type"] == "priority_gate"
                    for e in ag_p0["edges"]
                )
            )

            # Invalid simulation input rejected with HTTP 400 + English feedback, active config untouched
            bad_resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "action_graph", "config": {"ga_population_size": 0}},
                timeout=5,
            )
            self.assertEqual(bad_resp.status_code, 400)
            self.assertIn("ga_population_size", bad_resp.json()["error"])

            ui_res = run_ui_in_node(
                h.base_url,
                r"""
                await __waitUntil(() => state.dashboard !== null, 5000);
                document.querySelector('[data-page="action-graph"]').click();

                // Trigger invalid ga_seed="invalid_seed" simulation from UI
                document.getElementById("ag-cfg-ga_seed").value = "invalid_seed";
                document.getElementById("btn-ag-simulate").click();
                const errOk = await __waitUntil(
                  () => document.getElementById("feedback-banner").textContent.includes("Simulation preview error"),
                  5000
                );
                if (!errOk) throw new Error("Expected simulation validation error banner");

                return {
                  errBanner: document.getElementById("feedback-banner").textContent,
                  viewText: document.getElementById("view-action-graph").textContent,
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertIn("Simulation preview error", sc["errBanner"])
            self.assertEqual(h.api.send_command({"command": "config"})["config"]["planner_mode"], "priority")

            # Verify JSON & UI strings contain zero NaN/Infinity/non-English words
            dumped = json.dumps(ag_p0, allow_nan=False)
            for forbidden in ("grafo", "parâmetros", "simular", "aplicar", "configuração"):
                self.assertNotIn(forbidden, dumped.lower())
                self.assertNotIn(forbidden, sc["viewText"].lower())


if __name__ == "__main__":
    unittest.main()
