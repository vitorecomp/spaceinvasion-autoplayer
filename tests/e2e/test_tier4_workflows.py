"""Tier 4 Real-World End-to-End Player Application Workflows (8 workflows: T4-01..T4-08)."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

import requests

import commander
from tests.e2e.fixtures import (
    evaluate_sequence_score,
    make_game_state,
    make_planet,
    make_ship_offer,
)
from tests.e2e.harness import E2ETestHarness, FakeClock, FakeSpaceInvasionServer, run_ui_in_node
from spaceinvasion.config import StrategyConfig, load_strategy_config
from spaceinvasion.fleet import BashTracker, SpyReport
from spaceinvasion.state import EnergyBalance, Resources
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import (
    BLAST_FURNACE,
    DEVELOPMENT_CENTER,
    FUSION_PLANT,
    PIG_IRON_MINE,
    PlannedAction,
    plan_actions,
)


def _import_eta_helpers(test: unittest.TestCase):
    try:
        from spaceinvasion.ranking import attach_action_etas, compute_action_eta
    except ImportError as exc:
        test.fail(f"R3 ETA helpers missing from spaceinvasion.ranking: {exc}")
    return compute_action_eta, attach_action_etas


def _verify_eta_fields(test: unittest.TestCase, obj: dict | PlannedAction) -> None:
    keys = (
        "wait_hours",
        "wait_seconds",
        "wait_eta",
        "duration_seconds",
        "duration_text",
        "total_hours",
        "total_seconds",
        "total_eta",
        "eta_bottleneck",
    )
    for k in keys:
        if isinstance(obj, dict):
            test.assertIn(k, obj, f"ETA field {k!r} missing from action dict")
        else:
            test.assertTrue(hasattr(obj, k), f"ETA field {k!r} missing from PlannedAction")


class TestTier4Workflows(unittest.TestCase):
    # T4-01: Fresh Login/Session Recovery -> Tutorial Progression -> Claim -> Research Unlock
    def test_t4_01_fresh_session_tutorial_progression_claim_and_research_unlock(self) -> None:
        _import_eta_helpers(self)
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "StarterWorld",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [5000, 3000, 3000, 1500],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 80},
                "buildings": {"b1": 2, "b3": 1, "b5": 5},
                "ships": {},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            }
        }
        tutorial = {
            "step": 1,
            "total_steps": 11,
            "displayed": True,
            "can_complete": False,
            "reward_text": "5000 Pig iron",
            "tasks": [{"description": "Increase your Development center level 0 / 1", "done": False}],
        }
        srv = FakeSpaceInvasionServer(planets=planets, tutorial=tutorial, researches={})
        cfg = StrategyConfig()
        with E2ETestHarness(server=srv, config=cfg) as h:
            # 1) Session check attaches cleanly
            sess = h.api.send_command({"command": "ensure_session"})
            self.assertTrue(sess["ok"])
            self.assertGreaterEqual(len(h.ensure_session_calls), 1)

            # 2) Dashboard shows Tutorial 1/11 and plan defers non-urgent Fusion plant (bucket=4) behind mines/tutorial
            dash1 = h.get_dashboard(refresh=1)
            self.assertEqual(dash1["state"]["tutorial"]["step"], 1)
            plan1 = dash1["plan"]
            self.assertGreater(len(plan1), 0)
            _verify_eta_fields(self, plan1[0])
            idx_dev = next((i for i, a in enumerate(plan1) if a.get("target_id") == DEVELOPMENT_CENTER), None)
            idx_fus = next((i for i, a in enumerate(plan1) if a.get("target_id") == FUSION_PLANT), None)
            if idx_dev is not None and idx_fus is not None:
                self.assertLess(idx_dev, idx_fus, f"Fusion rushed ahead of Development center in tutorial: {plan1}")

            # 3) Live BUILD click on Development center -> F_planets refreshed afterward
            srv.page_view_log.clear()
            b_res = h.api.send_command({"command": "build", "building_id": DEVELOPMENT_CENTER, "dry_run": False})
            self.assertTrue(b_res["executed"])
            f_planet_views = [v for v in srv.page_view_log if v["frame"] == "F_planets"]
            self.assertGreaterEqual(len(f_planet_views), 1)

            # 4) Tutorial marks ready -> CLAIM_TUTORIAL clicks tutorialComplete -> advances Step 2
            srv.tutorial["can_complete"] = True
            plan_claim = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            self.assertEqual(plan_claim[0]["kind"], "CLAIM_TUTORIAL")
            _verify_eta_fields(self, plan_claim[0])

            claim_res = h.api.send_command({"command": "claim_tutorial", "dry_run": False})
            self.assertTrue(claim_res["executed"])
            dash2 = h.get_dashboard(refresh=1)
            self.assertGreaterEqual(dash2["state"]["tutorial"]["step"], 2)

            # 5) UI renders updated Tutorial strip & Dashboard Wait/Total ETAs cleanly
            ui_res = run_ui_in_node(
                h.base_url,
                """
                const ok = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!ok) throw new Error('dash-plan-tbody failed to render non-placeholder rows');
                return {
                  tutStrip: __snapshotElement('tutorial-strip'),
                  tutTitle: __snapshotElement('tutorial-title'),
                  dashRows: __serializeTable('dash-plan-tbody')
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertIsNotNone(sc["tutStrip"])
            self.assertFalse(sc["tutStrip"]["hidden"])
            self.assertIn("2/11", sc["tutTitle"]["textContent"])
            self.assertGreater(len(sc["dashRows"]), 0)

    # T4-02: Multi-Planet Mid-Game Optimization (priority -> ai_planner via Operator Control UI)
    def test_t4_02_multi_planet_midgame_priority_to_ai_planner_ui_workflow(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        cfg_prio = StrategyConfig(
            planner_mode="priority",
            allow_multi_planet=True,
            allow_shipyard=True,
            hot_planet_solar_temp_threshold=80,
        )
        cfg_ai = StrategyConfig(
            planner_mode="ai_planner",
            allow_multi_planet=True,
            allow_shipyard=True,
            hot_planet_solar_temp_threshold=80,
        )

        # Direct 4-body strategic empire verification (Homeworld, Cold Spice, Hot Tight s9, Asteroid)
        p_home = make_planet(
            planet_id=101,
            name="Homeworld",
            coords="02:380:12",
            temperature=31,
            fields_max=230,
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 7, "b7": 2},
            resources=Resources(9000, 6000, 4000, 2000),
            auto_offers=True,
        )
        p_cold = make_planet(
            planet_id=102,
            name="ColdSpice",
            coords="02:380:15",
            temperature=-120,
            fields_max=240,
            buildings={"b1": 4, "b3": 4, "b4": 5, "b5": 6, "b7": 2},
            resources=Resources(8000, 5000, 3000, 2000),
            auto_offers=True,
        )
        p_hot = make_planet(
            planet_id=103,
            name="HotTight",
            coords="02:380:01",
            temperature=120,
            fields_max=230,
            fields_used=229,
            buildings={"b1": 4, "b3": 4, "b4": 2, "b5": 4, "b9": 1},
            energy=EnergyBalance(produced=100, used=125),
            resources=Resources(9000, 9000, 5000, 2000),
            ship_offers=(make_ship_offer(9, owned=2, can_build=True),),
            auto_offers=True,
        )
        p_astro = make_planet(
            planet_id=104,
            name="AsteroidBase",
            coords="02:380:12",
            planet_type="asteroid",
            temperature=10,
            fields_max=10,
            fields_used=2,
            buildings={"b7": 4, "b9": 2},
            energy=EnergyBalance(produced=0, used=0),
            resources=Resources(5000, 5000, 3000, 2000),
            auto_offers=True,
        )
        empire = make_game_state(planets=(p_home, p_cold, p_hot, p_astro), active_planet_id=101, speedup_factor=5.0)
        prio_actions = plan_actions(empire, config=cfg_prio)
        ai_actions = plan_actions(empire, config=cfg_ai)

        # Asteroid respects astro_ok (no mines b1..b6) & hot tight colony emits 0-field s9
        self.assertEqual([a for a in ai_actions if a.planet_id == 104 and a.kind == "BUILD" and a.target_id in (1, 2, 3, 4, 5, 6)], [])
        self.assertGreater(len([a for a in ai_actions if a.planet_id == 103 and a.kind == "BUILD_SHIP" and a.target_id == 9]), 0)
        for act in ai_actions:
            _verify_eta_fields(self, act)
            exp = compute_action_eta(empire, act)
            self.assertAlmostEqual(float(act.wait_seconds), float(exp["wait_seconds"]), delta=1.0)
            self.assertAlmostEqual(float(act.duration_seconds), float(exp["duration_seconds"]), delta=1.0)

        ev = evaluate_sequence_score(empire, [a for a in ai_actions if a.executable_now][:4], config=cfg_ai, horizon_hours=6.0)
        self.assertTrue(ev.legal, f"Illegal planner sequence on 4-body empire: {ev.violations}")

        # Operator UI roundtrip priority -> ai_planner -> priority via Node vm against real HTTP server
        srv = FakeSpaceInvasionServer(speedup_factor=5.0)
        with E2ETestHarness(server=srv, config=cfg_prio) as h:
            h.api.get_state(include_buildings=True)
            baseline_targets = [(a["kind"], a["target_id"]) for a in h.api.send_command({"command": "plan"})["actions"][:5]]
            ui_res = run_ui_in_node(
                h.base_url,
                """
                const initOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!initOk) throw new Error('dash-plan-tbody failed to populate initial rows');
                document.querySelector('[data-page="config"]').click();
                const sel = document.getElementById('cfg-planner_mode');
                if (!sel) throw new Error('Missing #cfg-planner_mode');
                sel.value = 'ai_planner';
                sel.dispatchEvent({ type: 'change', target: sel });
                const dashBefore = fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length;
                document.getElementById('btn-save-config').click();
                const postOk = await __waitUntil(() => fetchLog.some((f) => f.url.includes('/command') && f.body && f.body.command === 'update_config'), 4000);
                if (!postOk) throw new Error('update_config POST missing');
                await __waitUntil(() => fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length > dashBefore, 4000);
                const refOk = await __waitUntil(() => __serializeTable('dash-plan-tbody').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!refOk) throw new Error('dash-plan-tbody failed to populate after update_config');
                document.querySelector('[data-page="dashboard"]').click();
                const dashRows = __serializeTable('dash-plan-tbody');
                document.querySelector('[data-page="operations"]').click();
                const opsRows = __serializeTable('plan-tbody');
                return { dashRows, opsRows };
                """,
            )
            self.assertGreater(len(ui_res["scenario"]["dashRows"]), 0)
            self.assertGreater(len(ui_res["scenario"]["opsRows"]), 0)
            self.assertEqual(load_strategy_config(h.config_path).planner_mode, "ai_planner")

            # Toggle back to priority leaves baseline outputs untouched
            h.api.send_command({"command": "update_config", "config": {"planner_mode": "priority"}})
            restored_targets = [(a["kind"], a["target_id"]) for a in h.api.send_command({"command": "plan"})["actions"][:5]]
            self.assertEqual(restored_targets, baseline_targets)

    # T4-03: Blast Furnace Starvation, Pause (0%), Pig Iron Recovery & Hysteresis Resume (100%)
    def test_t4_03_blast_furnace_starvation_pause_recovery_and_hysteresis_resume(self) -> None:
        _import_eta_helpers(self)
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [0, 0, 50000, 50000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 400, "used": 180},
                "buildings": {"b1": 5, "b2": 12, "b3": 4, "b4": 2, "b5": 9, "b7": 3},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, planets=planets)
        cfg = StrategyConfig(auto_throttle_blast_furnace=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            # Phase 1: b2=100% drains Pig net negative -> Pig builds blocked & UI renders .is-blocked + PIG
            ui1 = run_ui_in_node(
                h.base_url,
                """
                const ok = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!ok) throw new Error('dash-plan-tbody failed to populate non-placeholder rows');
                return {
                  dashRows: __serializeTable('dash-plan-tbody'),
                  opsRows: __serializeTable('plan-tbody'),
                  bfPill: __snapshotElement('hud-bf-pill')
                };
                """,
            )
            blocked_pills = [
                p
                for row in ui1["scenario"]["dashRows"]
                for cell in row
                for p in cell.get("pills", [])
                if "is-blocked" in p.get("className", "") and "PIG" in p.get("text", "").upper()
            ]
            self.assertGreater(len(blocked_pills), 0, f"Expected .is-blocked PIG pill on dashboard: {ui1['scenario']['dashRows']}")

            # Phase 2: Execute throttle_building(2, 0) -> persists in SQLite & updates UI bf pill + Pig ETA unblocks
            th0 = h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 0, "dry_run": False})
            self.assertTrue(th0["executed"])
            plan2 = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            b1_act = next((a for a in plan2 if a["kind"] in ("BUILD", "WAIT_RESOURCES") and a.get("target_id") == PIG_IRON_MINE), None)
            self.assertIsNotNone(b1_act)
            assert b1_act is not None
            self.assertNotEqual(b1_act["wait_eta"], "—")

            ui2 = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => ((document.getElementById('hud-bf-pill') || {}).textContent || '').includes('0% PAUSED'), 4000);
                return { bfPill: __snapshotElement('hud-bf-pill') };
                """,
            )
            self.assertIn("0% PAUSED", ui2["scenario"]["bfPill"]["textContent"])

            # Phase 3: Advance slightly (Pig reserves partial, P1 b1 still missing Pig) -> hysteresis prevents premature 100% flip
            clock.advance(400.0)
            srv.planets[12476]["resources"] = [200, 0, 50000, 50000]
            h.api.get_state(include_buildings=True)
            plan3 = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            self.assertFalse(
                any(a["kind"] == "THROTTLE_BUILDING" and a.get("target_level") == 100 for a in plan3),
                f"Hysteresis failed; resumed b2=100% while Pig still bottleneck: {plan3}",
            )

            # Phase 4: Reserves recover & Metal becomes P1 bottleneck (zero metal, plenty pig) -> resumes b2=100%
            srv.planets[12476]["buildings"] = {"b1": 12, "b2": 6, "b3": 4, "b4": 4, "b5": 11, "b7": 2}
            srv.planets[12476]["resources"] = [80000, 0, 50000, 50000]
            h.api.send_command({"command": "navigate", "action": "internalHome", "return_home": False})
            plan4 = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            resume = next((a for a in plan4 if a["kind"] == "THROTTLE_BUILDING" and a.get("target_level") == 100), None)
            self.assertIsNotNone(resume, f"Expected THROTTLE_BUILDING 100% once reserves healthy and Metal needed: {plan4}")
            th100 = h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 100, "dry_run": False})
            self.assertTrue(th100["executed"])
            plan5 = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            met_act = next((a for a in plan5 if a["cost"]["metal"] > 0), None)
            self.assertIsNotNone(met_act, f"Expected a Metal-costing action in plan5: {plan5}")
            assert met_act is not None
            self.assertEqual(met_act["eta_bottleneck"], "metal")
            self.assertNotEqual(met_act["wait_eta"], "—")
            self.assertIsNotNone(met_act["wait_hours"])
            self.assertNotEqual(met_act["total_eta"], "—")

    # T4-04: Full Universe Exploration Sweep, Espionage, Combat Sim, Raid Dispatch & Bash Enforcement
    def test_t4_04_full_exploration_spy_sim_raid_and_bash_workflow(self) -> None:
        _import_eta_helpers(self)
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [12000, 8000, 6000, 3000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 5, "b5": 7, "b9": 2},
                "ships": {"s10": 5},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            },
            12477: {
                "planet_id": 12477,
                "name": "RaiderHub",
                "coords": "02:380:14",
                "planet_type": "normal",
                "temperature": 25,
                "fields_max": 240,
                "resources": [5000, 5000, 5000, 2000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100},
                "buildings": {"b1": 4, "b5": 6, "b9": 2},
                "ships": {"s4": 25, "s1": 5, "s14": 2},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            },
        }
        gal = {
            (2, 380): [
                {"position": 4, "name": "IdlePrime", "owner": "SleepyOne", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 6, "name": "ActiveRival", "owner": "PlayerX", "status_color": "light_red", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 7, "name": "VacBlue", "owner": "VacP", "status_color": "blue", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 8, "name": "AllyGreen", "owner": "Mate", "status_color": "green", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 9, "name": "Spacia World", "owner": "Spacia", "status_color": "yellow", "has_debris": True, "is_spacia": True, "spy_href": None},
            ],
            (2, 381): [
                {"position": 5, "name": "OuterIdle", "owner": "Idle381", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
            ],
            (2, 382): [
                {"position": 7, "name": "RimIdle", "owner": "Idle382", "status_color": "yellow", "has_debris": True, "is_spacia": False, "spy_href": None},
            ],
        }
        spy_reports = {
            "02:380:04": {
                "owner": "SleepyOne",
                "resources": {"pig_iron": 30000, "metal": 20000, "kryptonite": 10000, "spice": 4000},
                "fleet": {},
                "defense": {"d1": 1},
                "dug_in": False,
                "is_spacia": False,
            }
        }
        srv = FakeSpaceInvasionServer(
            planets=planets,
            active_planet_id=12476,
            galaxy_systems=gal,
            spy_reports=spy_reports,
            sid_mode="empty",
            p_galaxy_mode="in_frame",
            fleet_form_dialect="standard",
        )
        cfg = StrategyConfig(allow_galaxy_scan=False, allow_raids=False, allow_pvp_in_az=False, allow_multi_planet=True)
        with E2ETestHarness(server=srv, config=cfg, executor_max_limiter_wait=None) as h:
            initial_views = len(srv.page_view_log)
            h.api.get_state(include_buildings=True)
            # 1) Enable scanning, raids, AZ PvP live via UI Config view
            run_ui_in_node(
                h.base_url,
                """
                document.querySelector('[data-page="config"]').click();
                for (const id of ['cfg-allow_galaxy_scan', 'cfg-allow_raids', 'cfg-allow_pvp_in_az']) {
                  const el = document.getElementById(id);
                  if (!el) throw new Error('Missing ' + id);
                  el.checked = true;
                  el.dispatchEvent({ type: 'change', target: el });
                }
                document.getElementById('btn-save-config').click();
                await __waitUntil(() => fetchLog.some((f) => f.url.includes('/command') && f.body && f.body.command === 'update_config'), 4000);
                await __sleep(100);
                """,
            )

            # 2) Sweep systems [02:380], [02:381], [02:382] via p_galaxy() fallback
            for sys_n in (380, 381, 382):
                self.assertTrue(h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": sys_n, "dry_run": False})["executed"])

            # 3) Spy recon & Combat simulation (>= 1.4x with Stealth bomber first-strike)
            self.assertTrue(h.api.send_command({"command": "spy", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False})["executed"])
            sync_res = h.api.send_command({"command": "sync_spy_reports"})
            self.assertGreaterEqual(sync_res["synced_reports"], 1)

            sim = h.api.send_command(
                {
                    "command": "simulate_combat",
                    "attacker_ships": {"s4": 25, "s1": 5, "s14": 2},
                    "defender_defense": {"d1": 1},
                    "target_resources": {"pig_iron": 30000, "metal": 20000, "kryptonite": 10000, "spice": 4000},
                    "target_coords": "02:380:04",
                }
            )["simulation"]
            self.assertTrue(sim["safe_to_attack"])
            self.assertGreater(sim["stealth_first_strike_power"], 0.0)

            # 4) Live attack switches origin planet via newPlanet=12477, verifies raid_history + Bash 3/24h cap
            atk1 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False})
            self.assertTrue(atk1["executed"])
            self.assertEqual(srv.launched_fleets[-1]["origin_planet_id"], 12477)

            for _ in range(3):
                r_az = h.api.send_command({"command": "attack", "target_coords": "02:380:06", "planet_id": 12477, "dry_run": False})
                self.assertTrue(r_az["executed"])
            # 4th attack on active light_red MUST be blocked; 4th on yellow/Spacia exempt!
            blocked_az = h.api.send_command({"command": "attack", "target_coords": "02:380:06", "planet_id": 12477, "dry_run": False})
            self.assertFalse(blocked_az["executed"])
            self.assertIn("bash", blocked_az["message"].lower())
            for _ in range(3):
                self.assertTrue(h.api.send_command({"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False})["executed"])

            auto_ts = [v["ts"] for v in srv.page_view_log[initial_views:]]
            self.assertGreater(len(auto_ts), 24)
            for ts in auto_ts:
                win_count = sum(1 for x in auto_ts if ts - 59.5 < x <= ts)
                self.assertLessEqual(
                    win_count,
                    24,
                    f"T4-04 rolling 60s window exceeded 24 automated views: {win_count}",
                )

    # T4-05: Per-Minute Page-View Throttle Storm & Cooldown Recovery Under Concurrent Commander + UI
    def test_t4_05_page_view_throttle_storm_and_recovery_concurrent_commander_and_ui(self) -> None:
        _import_eta_helpers(self)
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(
            clock=clock,
            rate_limit_threshold=22,
            server_cooldown_s=45.0,
            rate_limit_on_frame="F_Hauptframe",
        )
        cfg = StrategyConfig(allow_galaxy_scan=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            srv.page_view_log.clear()
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            scan_actions = [v["action"] for v in srv.page_view_log]
            self.assertNotIn("internalBuildings", scan_actions)
            self.assertNotIn("internalHome", scan_actions)

            # Force server throttle hit -> Commander tick 1 backs off full >=45s; UI keeps Chrome ONLINE with warn banner
            clock.advance(10.0)
            srv.force_rate_limit_remaining = 1
            rc = commander.main(
                [
                    "--api-url",
                    h.base_url,
                    "--loop",
                    "--interval",
                    "20",
                    "--max-ticks",
                    "2",
                    "--config",
                    str(h.config_path),
                    "--log-dir",
                    str(h.log_dir),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(any(s >= 45.0 for s in clock.sleep_calls), f"sleep_calls={clock.sleep_calls}")
            self.assertEqual(len(h.ensure_session_calls), 0)

            # While active cooldown forced via bridge rate_limit, UI shows ONLINE + .is-warn banner
            h.bridge.record_rate_limit(20.0)
            health = h.api.health()
            self.assertTrue(health["in_game"])
            self.assertTrue(health["rate_limited"])
            ui_res = run_ui_in_node(
                h.base_url,
                """
                const initOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!initOk) throw new Error('dash-plan-tbody failed to populate initial rows');
                document.querySelector('[data-page="combat"]').click();
                const galBtn = document.getElementById('btn-scan-galaxy');
                if (!galBtn) throw new Error('Missing #btn-scan-galaxy');
                galBtn.click();
                const postOk = await __waitUntil(() => fetchLog.some((f) => f.url === '/command' && f.body && f.body.command === 'scan_galaxy'), 4000);
                if (!postOk) throw new Error('scan_galaxy POST missing from fetchLog');
                const bannerOk = await __waitUntil(() => {
                  const b = __snapshotElement('feedback-banner');
                  return b && !b.hidden && !String(b.textContent || '').includes('Scanning home Galaxy');
                }, 4000);
                if (!bannerOk) throw new Error('feedback-banner did not update after scan_galaxy response');
                return {
                  chromeText: document.getElementById('chrome-status-text').textContent.trim(),
                  banner: __snapshotElement('feedback-banner')
                };
                """,
            )
            self.assertEqual(ui_res["errors"], [])
            self.assertIn("ONLINE", ui_res["scenario"]["chromeText"].upper())
            self.assertFalse(ui_res["scenario"]["banner"]["hidden"])
            self.assertIn("is-warn", (ui_res["scenario"]["banner"] or {}).get("className", ""))

            # Advance clock past cooldown & verify recovery
            clock.advance(65.0)
            srv.clear_throttle()
            self.assertTrue(h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 381, "dry_run": False})["executed"])
            plan = h.api.send_command({"command": "plan"})["actions"]
            _verify_eta_fields(self, plan[0])
            self.assertEqual(len(h.ensure_session_calls), 0)

    # T4-06: Hostile Incoming Attack Survival (P0 Escape Flight) Mid-Optimizer Session
    def test_t4_06_hostile_incoming_attack_escape_flight_mid_session(self) -> None:
        _import_eta_helpers(self)
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [40000, 30000, 20000, 20000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 5, "b5": 7, "b9": 2},
                "ships": {"s1": 4, "s9": 10},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            },
            12477: {
                "planet_id": 12477,
                "name": "Sanctuary",
                "coords": "02:380:15",
                "planet_type": "normal",
                "temperature": -50,
                "fields_max": 240,
                "resources": [1000, 1000, 1000, 500],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 150, "used": 80},
                "buildings": {"b1": 2, "b5": 4},
                "ships": {},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            },
        }
        srv = FakeSpaceInvasionServer(
            planets=planets,
            active_planet_id=12476,
            hostile_alerts=["Hostile attack fleet inbound on 02:380:12 in 00:04:00"],
        )
        cfg = StrategyConfig(allow_fleetsave=False, fleet_save_fuel_reserve_spice=1500)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            plan = h.api.send_command({"command": "plan"})["actions"]
            self.assertEqual(plan[0]["kind"], "ALERT_HOSTILE")
            esc = next(a for a in plan if a["kind"] == "ESCAPE_FLIGHT")
            _verify_eta_fields(self, esc)
            self.assertEqual(esc["wait_eta"], "Ready")
            self.assertEqual(esc["duration_text"], "Instant")
            self.assertFalse(esc["executable_now"])
            self.assertEqual(esc["cost"]["spice"], 20000 - 1500)

            ui_res = run_ui_in_node(
                h.base_url,
                """
                const ok = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!ok) throw new Error('dash-plan-tbody failed to populate non-placeholder rows');
                return {
                  beacon: __snapshotElement('hud-alert-beacon'),
                  alertText: __snapshotElement('val-alerts')
                };
                """,
            )
            self.assertIn("is-alert", ui_res["scenario"]["beacon"]["className"])
            self.assertIn("HOSTILE ALERT (1)", ui_res["scenario"]["alertText"]["textContent"])

            # allow_fleetsave=True unlocks executable_now on P0 ESCAPE_FLIGHT
            h.api.send_command({"command": "update_config", "config": {"allow_fleetsave": True}})
            plan_enabled = h.api.send_command({"command": "plan"})["actions"]
            esc_enabled = next(a for a in plan_enabled if a["kind"] == "ESCAPE_FLIGHT")
            self.assertTrue(esc_enabled["executable_now"])

    # T4-07: API Server Restart & SQLite WAL / config.yaml / UI Hot-Reload Continuity
    def test_t4_07_api_restart_sqlite_wal_config_and_ui_hot_reload_continuity(self) -> None:
        _import_eta_helpers(self)
        srv = FakeSpaceInvasionServer(
            planets={
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [12000, 8000, 6000, 3000],
                    "storage": [100000, 100000, 100000, 100000],
                    "energy": {"produced": 250, "used": 140},
                    "buildings": {"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 7, "b7": 2, "b9": 2},
                    "ships": {"s4": 20, "s1": 5, "s10": 5},
                    "defense": {},
                    "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                    "building_queue": [],
                }
            }
        )
        cfg = StrategyConfig(planner_mode="priority", allow_galaxy_scan=True, allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command(
                {
                    "command": "update_config",
                    "config": {"planner_mode": "ai_planner", "playstyle": "miner", "max_actions_per_hour": 25},
                }
            )
            self.assertTrue(h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 0, "dry_run": False})["executed"])
            self.assertTrue(h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})["executed"])
            self.assertTrue(h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})["executed"])
            rnk1 = h.api.send_command({"command": "ranking_evolution"})
            self.assertTrue(rnk1["ok"])
            plan1 = h.api.send_command({"command": "plan"})["actions"]
            _verify_eta_fields(self, plan1[0])

            # Check UI detects server_boot_id change across server reload & reloads cleanly
            ui_reload = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => reloadCount >= 1, 6000);
                return { reloads: reloadCount };
                """,
                fetch_patch_js="""
                async (url, _opts, fullUrl, nativeFetch) => {
                  if (String(url).includes('/ui-version')) {
                    globalThis.__t4BootCalls = (globalThis.__t4BootCalls || 0) + 1;
                    const resp = await nativeFetch(fullUrl);
                    const data = await resp.json();
                    data.server_boot_id = globalThis.__t4BootCalls === 1 ? 'boot-old' : 'boot-new';
                    return { ok: true, status: 200, json: async () => data };
                  }
                  return undefined;
                }
                """,
            )
            self.assertGreaterEqual(ui_reload["reloadCount"], 1)

            # Shut down Server #1 and restart Bridge/Server #2 against identical tempdir DB & config.yaml
            h.restart_bridge()
            dash2 = h.get_dashboard(refresh=1)
            self.assertEqual(dash2["config"]["planner_mode"], "ai_planner")
            self.assertEqual(dash2["config"]["playstyle"], "miner")
            self.assertEqual(dash2["config"]["max_actions_per_hour"], 25)
            self.assertEqual(dash2["state"]["planets"][0]["economy_rates"].get("resource2"), 0)

            db2 = h.api.send_command({"command": "db_summary"})["db"]
            self.assertTrue(any(t["coords"] == "02:380:04" for t in db2["spy_targets"]))
            assert h.db_path is not None
            with contextlib.closing(sqlite3.connect(h.db_path)) as conn:
                raids2 = conn.execute("SELECT COUNT(*) FROM raid_history").fetchone()[0]
                snaps2 = conn.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0]
            self.assertGreaterEqual(raids2, 1)
            self.assertGreaterEqual(snaps2, 1)

            ui2 = run_ui_in_node(
                h.base_url,
                """
                const ok = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!ok) throw new Error('dash-plan-tbody failed to populate after bridge restart');
                return {
                  plannerVal: (document.getElementById('cfg-planner_mode') || {}).value ?? null,
                  bfPill: __snapshotElement('hud-bf-pill'),
                  dashRows: __serializeTable('dash-plan-tbody')
                };
                """,
            )
            self.assertEqual(ui2["scenario"].get("plannerVal"), "ai_planner")
            self.assertIn("0% PAUSED", ui2["scenario"]["bfPill"]["textContent"])
            self.assertGreater(len(ui2["scenario"]["dashRows"]), 0)


if __name__ == "__main__":
    unittest.main()
