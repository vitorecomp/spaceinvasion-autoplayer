"""End-to-End tests for the 4-Tab Action Graph UI, mode switching, isolated simulation, and Apply."""

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
            "buildings": {
                "b1": 4,
                "b2": 3,
                "b3": 4,
                "b4": 3,
                "b5": 6,
                "b7": 3,
                "b8": 2,
                "b9": 2,
                "b10": 4,
            },
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
            "buildings": {"b1": 3, "b2": 2, "b3": 3, "b4": 4, "b5": 5, "b7": 2, "b10": 2},
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
    def test_ag_e2e_01_render_action_graph_tab_and_switch_priority_and_ai_planner_modes(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(planner_mode="priority")
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
                const subTabs = Array.from(document.querySelectorAll('#ag-workspace-tablist [data-ag-tab]')).map((el) => el.getAttribute("data-ag-tab"));
                const prioNodes = document.querySelectorAll(".ag-node-card").length;
                const prioEdges = document.querySelectorAll(".ag-edge-path").length;
                const svgMounted = Boolean(document.getElementById("ag-edges-svg"));
                const mainWinGenPrio = document.getElementById("ag-main-window").textContent.toLowerCase().includes("generation");

                // Switch to AI-Planner (ai_planner) mode preview via Tab 1 header radio button
                document.getElementById("ag-mode-ai_planner").click();
                const aiOk = await __waitUntil(() => state.agSimRunning === 0 && state.actionGraphData?.mode === "ai_planner", 6000);
                if (!aiOk) throw new Error("Failed switching Action Graph to ai_planner");
                const aiNodes = document.querySelectorAll(".ag-node-card").length;
                const aiEngineText = document.getElementById("ag-kpi-engine").textContent;
                const mainWinGenAi = document.getElementById("ag-main-window").textContent.toLowerCase().includes("generation");

                // Switch back to Heuristic (priority) mode
                document.getElementById("ag-mode-priority").click();
                const prioBackOk = await __waitUntil(() => state.agSimRunning === 0 && state.actionGraphData?.mode === "priority", 6000);
                if (!prioBackOk) throw new Error("Failed switching Action Graph back to priority");
                const prioEngineText = document.getElementById("ag-kpi-engine").textContent;

                return {
                  hidden: agView.hidden,
                  subTabs,
                  prioNodes,
                  prioEdges,
                  svgMounted,
                  aiNodes,
                  aiEngineText,
                  prioEngineText,
                  mainWinGenPrio,
                  mainWinGenAi,
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertFalse(sc["hidden"])
            self.assertEqual(
                sc["subTabs"],
                ["heuristics", "config-optimizer", "ai-planner", "fleet-planner"],
            )
            self.assertGreater(sc["prioNodes"], 0)
            self.assertGreaterEqual(sc["prioEdges"], 0)
            self.assertTrue(sc["svgMounted"])
            self.assertGreater(sc["aiNodes"], 0)
            self.assertIn("AI-PLANNER", sc["aiEngineText"])
            self.assertIn("HEURISTIC", sc["prioEngineText"])
            self.assertFalse(sc["mainWinGenPrio"])
            self.assertFalse(sc["mainWinGenAi"])

    def test_ag_e2e_02_isolated_right_panel_parameter_simulation_zero_active_config_mutation(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(
            planner_mode="priority",
            playstyle="fleeter",
            allow_multi_planet=True,
            allow_raids=False,
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
                document.getElementById("ag-cfg-planner_mode").value = "ai_planner";
                document.getElementById("ag-cfg-playstyle").value = "miner";
                document.getElementById("ag-cfg-storage_warn_ratio").value = "0.75";
                document.getElementById("ag-cfg-combat_safety_margin").value = "1.8";
                document.getElementById("ag-ptab-btn-scope").click();
                const multiChk = document.getElementById("ag-cfg-allow_multi_planet");
                multiChk.checked = false;
                document.getElementById("ag-cfg-allow_raids").checked = true;

                document.getElementById("btn-ag-simulate").click();
                const simOk = await __waitUntil(
                  () => state.agSimRunning === 0 && state.actionGraphData?.mode === "ai_planner" && state.actionGraphData?.simulated === true,
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
            self.assertEqual(sc["simMode"], "ai_planner")
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

                document.getElementById("ag-cfg-planner_mode").value = "ai_planner";
                document.getElementById("ag-cfg-playstyle").value = "hybrid";
                document.getElementById("ag-cfg-combat_safety_margin").value = "1.8";
                document.getElementById("ag-cfg-min_colony_fields").value = "315";
                document.getElementById("ag-ptab-btn-scope").click();
                document.getElementById("ag-cfg-allow_raids").checked = true;
                document.getElementById("ag-cfg-allow_fleetsave").checked = true;

                document.getElementById("btn-ag-apply").click();
                const appliedOk = await __waitUntil(
                  () => state.configSaving === 0 && !state.agSimDirty && state.dashboard?.config?.planner_mode === "ai_planner" && state.dashboard?.config?.min_colony_fields === 315,
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
            self.assertEqual(sc["dashBadge"], "MODE: AI-PLANNER")
            self.assertEqual(sc["cfgViewMode"], "ai_planner")

            # Restart bridge and prove config.yaml persisted cleanly on disk
            h.restart_bridge()
            reloaded_disk = load_strategy_config(h.config_path)
            self.assertEqual(reloaded_disk.planner_mode, "ai_planner")
            self.assertEqual(reloaded_disk.playstyle, "hybrid")
            self.assertEqual(reloaded_disk.min_colony_fields, 315)
            self.assertAlmostEqual(reloaded_disk.combat_safety_margin, 1.8, places=3)
            self.assertTrue(reloaded_disk.allow_raids)
            self.assertTrue(reloaded_disk.allow_fleetsave)

            post_restart_cfg = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(post_restart_cfg["planner_mode"], "ai_planner")
            self.assertEqual(post_restart_cfg["min_colony_fields"], 315)

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

                // Trigger out-of-range simulation input from Tab 1 UI
                document.getElementById("ag-cfg-combat_safety_margin").value = "0.2";
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

    def test_ag_e2e_05_4tab_workspace_config_optimizer_ai_planner_gate_and_fleet_sync(self) -> None:
        srv = _make_two_colony_server(hostile=False)
        cfg = StrategyConfig(planner_mode="priority", raid_min_loot_spice_eq=1500)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.get_dashboard(refresh=1)

            ui_res = run_ui_in_node(
                h.base_url,
                r"""
                await __waitUntil(() => state.dashboard !== null, 5000);
                document.querySelector('[data-page="action-graph"]').click();

                // 1) Tab 2: Config Optimization (run with small GA params for fast E2E execution)
                document.getElementById("ag-tab-btn-config-optimizer").click();
                document.getElementById("co-ptab-buildings").click();
                const coPop = document.getElementById("co-param-ga_population_size");
                coPop.value = "6";
                coPop.dispatchEvent(new Event("change", { bubbles: true }));
                const coGen = document.getElementById("co-param-ga_generations");
                coGen.value = "2";
                coGen.dispatchEvent(new Event("change", { bubbles: true }));
                document.getElementById("btn-co-run").click();
                const coDone = await __waitUntil(
                  () => !document.getElementById("co-kpi-ships").textContent.startsWith("—"),
                  20000
                );
                if (!coDone) throw new Error("Config Optimizer run did not populate co-kpi-ships");
                const coShips = document.getElementById("co-kpi-ships").textContent;
                const coPlanetRows = document.querySelectorAll("#co-planets-tbody tr").length;

                // 2) Tab 3: AI-Planner (Colony Gate locked at 2/11 + real windowed DAG + Path table)
                document.getElementById("ag-tab-btn-ai-planner").click();
                const aiWeeks = document.getElementById("ai-param-reward_weeks");
                aiWeeks.value = "2";
                aiWeeks.dispatchEvent(new Event("change", { bubbles: true }));
                document.getElementById("ai-ptab-weights").click();
                const aiPop = document.getElementById("ai-param-ga_population_size");
                aiPop.value = "4";
                aiPop.dispatchEvent(new Event("change", { bubbles: true }));
                const aiGen = document.getElementById("ai-param-ga_generations");
                aiGen.value = "1";
                aiGen.dispatchEvent(new Event("change", { bubbles: true }));
                document.getElementById("btn-ai-run").click();
                const aiDone = await __waitUntil(
                  () => document.getElementById("ai-kpi-reward").textContent !== "—",
                  20000
                );
                if (!aiDone) throw new Error("AI-Planner run did not populate ai-kpi-reward");
                const aiReward = document.getElementById("ai-kpi-reward").textContent;
                const gateText = document.getElementById("ag-ai-colony-gate").textContent;
                const activateDisabled = document.getElementById("btn-ai-activate").disabled;
                const aiDagNodes = document.querySelectorAll("#ag-ai-canvas-scroll .ag-node-card").length;

                // 3) Tab 4: Fleet / Attack Planner (slot table + bidirectional Config sync)
                document.getElementById("ag-tab-btn-fleet-planner").click();
                const fltLoaded = await __waitUntil(
                  () => document.querySelectorAll("#flt-slots-table tbody tr").length === 2,
                  8000
                );
                if (!fltLoaded) throw new Error("Fleet Planner slots table did not load 2 colonies");
                const fltSlotsText = document.getElementById("flt-kpi-slots").textContent;
                document.getElementById("flt-cfg-raid_min_loot_spice_eq").value = "3300";
                document.getElementById("flt-cfg-raid_min_loot_spice_eq").dispatchEvent("change");
                document.querySelector('[data-flt-ptab="shipyard"]').click();
                document.getElementById("flt-shipyard-starcruiser").value = "19";
                document.getElementById("flt-shipyard-starcruiser").dispatchEvent("change");
                document.getElementById("btn-flt-apply").click();
                const fltApplied = await __waitUntil(
                  () => state.configSaving === 0 && Number(state.dashboard?.config?.raid_min_loot_spice_eq) === 3300,
                  8000
                );
                if (!fltApplied) throw new Error("Fleet Planner Apply failed to update Config");

                return {
                  coShips,
                  coPlanetRows,
                  aiReward,
                  gateText,
                  activateDisabled,
                  aiDagNodes,
                  fltSlotsText,
                  syncedCfgLoot: document.getElementById("cfg-raid_min_loot_spice_eq")?.value,
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertNotIn("NaN", sc["coShips"])
            self.assertGreaterEqual(sc["coPlanetRows"], 2)
            self.assertNotEqual(sc["aiReward"], "—")
            self.assertIn("2 / 11 colonies founded", sc["gateText"])
            self.assertTrue(sc["activateDisabled"])
            self.assertGreater(sc["aiDagNodes"], 0)
            self.assertIn("/ 8", sc["fltSlotsText"])
            self.assertEqual(sc["syncedCfgLoot"], "3300")

            # Restart bridge and confirm Tab 4 Apply persisted to disk config.yaml
            h.restart_bridge()
            reloaded = load_strategy_config(h.config_path)
            self.assertEqual(int(reloaded.raid_min_loot_spice_eq), 3300)
            self.assertEqual(int(reloaded.shipyard_targets.get("starcruiser", 0)), 19)


if __name__ == "__main__":
    unittest.main()
