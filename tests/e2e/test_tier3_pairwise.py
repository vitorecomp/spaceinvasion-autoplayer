"""Tier 3 E2E Pairwise Feature Combination Tests across R1, R2, and R3 (13 tests: PW-01..PW-13)."""

from __future__ import annotations

from pathlib import Path
import unittest

import requests

import commander
from tests.e2e.fixtures import (
    evaluate_sequence_score,
    make_building_offer,
    make_ga_nn_config,
    make_game_state,
    make_midgame_state,
    make_planet,
    make_research_offer,
    make_ship_offer,
)
from tests.e2e.harness import E2ETestHarness, FakeClock, FakeSpaceInvasionServer, run_ui_in_node
from spaceinvasion import game_data, ranking
from spaceinvasion.config import StrategyConfig, load_strategy_config
from spaceinvasion.fleet import BashTracker, SpyReport, simulate_raid_outcome
from spaceinvasion.state import EnergyBalance, QueueJob, Resources, TutorialStatus, TutorialTask
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import (
    BLAST_FURNACE,
    DEVELOPMENT_CENTER,
    KRYPTONITE_MINE,
    PIG_IRON_MINE,
    SPICE_MINE,
    WEAPONS_FACTORY,
    PlannedAction,
    plan_actions,
)


def _import_optimizer(test: unittest.TestCase):
    try:
        from spaceinvasion.optimizer import optimize_plan_actions
    except ImportError as exc:
        test.fail(f"R1 GA+NN optimizer not importable: {exc}")
    return optimize_plan_actions


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


class TestTier3Pairwise(unittest.TestCase):
    # PW-01: R1 (ga_nn optimizer) x R3 (ETA engine & API)
    def test_pw_01_ga_nn_optimizer_with_eta_engine_and_api(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        _import_optimizer(self)
        cfg = make_ga_nn_config(
            self,
            allow_research=True,
            allow_shipyard=True,
            ga_population_size=16,
            ga_generations=8,
            ga_horizon_steps=6,
            ga_seed=42,
        )
        p = make_planet(
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 7, "b7": 2, "b8": 1, "b9": 2},
            resources=Resources(9000, 6000, 5000, 2000),
            researches={"r1": 1, "r2": 1},
            ship_offers=(make_ship_offer(4, owned=2, can_build=True),),
            auto_offers=True,
        )
        state = make_game_state(
            planets=(p,),
            researches={"r1": 1, "r2": 1},
            research_offers=(make_research_offer("r2", 1, can_research=True),),
            speedup_factor=10.0,
        )

        actions = plan_actions(state, config=cfg)
        self.assertGreater(len(actions), 0)
        for act in actions:
            _verify_eta_fields(self, act)
            expected = compute_action_eta(state, act)
            self.assertAlmostEqual(float(act.wait_seconds), float(expected["wait_seconds"]), delta=1.0)
            self.assertAlmostEqual(float(act.duration_seconds), float(expected["duration_seconds"]), delta=1.0)
            self.assertEqual(act.eta_bottleneck, expected["eta_bottleneck"])

        exec_seq = [a for a in actions if a.executable_now][:4]
        ev = evaluate_sequence_score(state, exec_seq, config=cfg, horizon_hours=6.0)
        self.assertTrue(ev.legal, f"Illegal GA+NN plan sequence: {ev.violations}")
        self.assertGreater(ev.total_score, 0.0)

        # Verify HTTP API `/command plan` override also returns 9 ETA fields in ga_nn mode
        srv = FakeSpaceInvasionServer(speedup_factor=10.0)
        with E2ETestHarness(server=srv, config=StrategyConfig(planner_mode="priority") if hasattr(StrategyConfig(), "planner_mode") else StrategyConfig()) as h:
            res = h.api.send_command({"command": "plan", "planner_mode": "ga_nn"})
            self.assertTrue(res["ok"])
            self.assertGreater(len(res["actions"]), 0)
            for row in res["actions"]:
                _verify_eta_fields(self, row)

    # PW-02: R1 (ga_nn) x R2 (scan_galaxy + attack + BashTracker) x R3 (ETA)
    def test_pw_02_ga_nn_with_scan_attack_bash_and_eta(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        _import_optimizer(self)
        cfg = make_ga_nn_config(
            self,
            allow_galaxy_scan=True,
            allow_raids=True,
            allow_pvp_in_az=True,
        )
        gal = {
            (2, 380): [
                {"position": 4, "name": "YellowLoot", "owner": "IdleY", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 6, "name": "BashedAZ", "owner": "RivalB", "status_color": "light_red", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 7, "name": "BunkerY", "owner": "FortY", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
            ]
        }
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [8000, 6000, 5000, 2000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 4, "b5": 6, "b9": 2},
                "ships": {"s4": 20, "s1": 5, "s10": 5},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            }
        }
        spy_reports = {
            "02:380:04": {"owner": "IdleY", "resources": {"pig_iron": 25000, "metal": 20000, "kryptonite": 10000, "spice": 5000}, "fleet": {}, "defense": {"d1": 1}},
            "02:380:06": {"owner": "RivalB", "resources": {"pig_iron": 60000, "metal": 40000, "kryptonite": 20000, "spice": 10000}, "fleet": {}, "defense": {}},
            "02:380:07": {"owner": "FortY", "resources": {"pig_iron": 90000, "metal": 50000, "kryptonite": 20000, "spice": 10000}, "fleet": {}, "defense": {"d1": 500}},
        }
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, planets=planets, galaxy_systems=gal, spy_reports=spy_reports)
        with E2ETestHarness(server=srv, clock=clock, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            for coords in ("02:380:04", "02:380:06", "02:380:07"):
                h.api.send_command({"command": "spy", "target_coords": coords, "probes": 1, "dry_run": False})
            h.api.send_command({"command": "sync_spy_reports"})

            now = clock.time()
            db = GameDatabase(h.db_path)
            try:
                # Record 3 active raids on 02:380:06 (bashed!)
                for i in range(3):
                    db.record_raid(
                        "02:380:06",
                        attacked_ts=now - 50 + i,
                        is_exempt=False,
                        outcome="Victory",
                        loot={"pig_iron": 15000, "metal": 10000},
                    )
                bash = db.load_bash_tracker()
            finally:
                db.close()

            plan_res = h.api.send_command({"command": "plan", "refresh": True})
            atks = [a for a in plan_res["actions"] if a["kind"] == "ATTACK"]
            atk04 = next((a for a in atks if "02:380:04" in a["target_name"]), None)
            self.assertIsNotNone(atk04, f"Expected valid yellow 02:380:04 ATTACK in ga_nn plan: {atks}")
            assert atk04 is not None
            _verify_eta_fields(self, atk04)
            self.assertTrue(atk04["executable_now"])
            self.assertEqual(atk04["wait_eta"], "Ready")
            self.assertEqual(atk04["duration_text"], "Instant")
            self.assertIn(atk04["total_eta"], ("Ready", "Instant"))

            # Strong bunker 02:380:07 must NOT be executable
            atk07 = next((a for a in atks if "02:380:07" in a["target_name"]), None)
            if atk07 is not None:
                self.assertFalse(atk07["executable_now"])

            # Bashed 02:380:06 (3 active raids in <24h) must NOT be emitted as executable ATTACK
            atk06 = next((a for a in atks if "02:380:06" in a["target_name"]), None)
            if atk06 is not None:
                self.assertFalse(
                    atk06["executable_now"],
                    f"Bashed 02:380:06 emitted as executable ATTACK: {atk06}",
                )

            # Direct BashTracker verification & live enforcement
            self.assertFalse(bash.can_attack("02:380:06", is_inactive=False, is_spacia=False, timestamp=now))
            self.assertTrue(bash.can_attack("02:380:04", is_inactive=True, is_spacia=False, timestamp=now))

            # Execute live attack on 02:380:04 -> succeeds cleanly; bashed 06 -> rejected
            live04 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(live04["executed"])
            blocked06 = h.api.send_command({"command": "attack", "target_coords": "02:380:06", "dry_run": False})
            self.assertFalse(blocked06["executed"])

    # PW-03: R1 (priority vs ga_nn) x R2 (b2 hysteresis) x R3 (blocked Metal ETA)
    def test_pw_03_priority_vs_ga_nn_b2_hysteresis_and_blocked_metal_eta(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        _import_optimizer(self)
        p = make_planet(
            buildings={"b1": 12, "b2": 11, "b3": 8, "b4": 4, "b5": 11, "b7": 3},
            resources=Resources(50, 0, 1000, 1000),
            economy_rates={"resource2": 0},
            researches={"r1": 1},
            auto_offers=True,
        )
        state = make_game_state(planets=(p,), researches={"r1": 1}, speedup_factor=1.0)
        cfg_prio = make_ga_nn_config(self, planner_mode="priority", auto_throttle_blast_furnace=True)
        cfg_gann = make_ga_nn_config(self, planner_mode="ga_nn", auto_throttle_blast_furnace=True)

        # Priority emits Metal-blocked actions with wait_eta='—', eta_bottleneck='metal' while keeping Pig-only finite
        prio_actions = plan_actions(state, config=cfg_prio)
        self.assertEqual([a for a in prio_actions if a.kind == "THROTTLE_BUILDING" and a.target_level == 100], [])
        met_acts_prio = [a for a in prio_actions if a.cost.metal > 0 and a.missing.metal > 0]
        self.assertGreater(len(met_acts_prio), 0)
        for ma in met_acts_prio:
            _verify_eta_fields(self, ma)
            self.assertEqual(ma.wait_eta, "—", f"[priority] Expected blocked Metal wait_eta='—', got {ma}")
            self.assertEqual(ma.eta_bottleneck, "metal")
            self.assertEqual(ma.total_eta, "—")
        for pa in [a for a in prio_actions if a.cost.metal == 0 and a.missing.pig_iron > 0]:
            self.assertNotEqual(pa.wait_eta, "—")

        # GA+NN preserves 0% hysteresis and never attaches finite Metal ETA
        gann_actions = plan_actions(state, config=cfg_gann)
        self.assertGreater(len(gann_actions), 0)
        self.assertEqual([a for a in gann_actions if a.kind == "THROTTLE_BUILDING" and a.target_level == 100], [])
        for ma in [a for a in gann_actions if a.cost.metal > 0 and a.missing.metal > 0]:
            _verify_eta_fields(self, ma)
            self.assertEqual(ma.wait_eta, "—", f"[ga_nn] Emitted finite Metal wait_eta while b2=0%: {ma}")
            self.assertEqual(ma.eta_bottleneck, "metal")
            self.assertEqual(ma.total_eta, "—")
        for pa in [a for a in gann_actions if a.cost.metal == 0 and a.missing.pig_iron > 0]:
            self.assertNotEqual(pa.wait_eta, "—")

        # Live harness verifies throttle_building(2, 0) surfaces blocked Metal ETA on /dashboard
        planets = {
            12476: {
                "planet_id": 12476, "name": "Home", "coords": "02:380:12", "planet_type": "normal", "temperature": 31, "fields_max": 230,
                "resources": [50, 0, 1000, 1000], "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 12, "b2": 11, "b3": 8, "b4": 4, "b5": 11, "b7": 3},
                "ships": {}, "defense": {}, "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100}, "building_queue": [],
            }
        }
        with E2ETestHarness(server=FakeSpaceInvasionServer(planets=planets), config=cfg_prio) as h:
            h.api.get_state(include_buildings=True)
            thr = h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 0, "dry_run": False})
            self.assertTrue(thr["executed"])
            dash = h.get_dashboard(refresh=1)
            dash_met = [a for a in dash["plan"] if a["cost"]["metal"] > 0 and a["missing"]["metal"] > 0]
            self.assertGreater(len(dash_met), 0)
            self.assertEqual(dash_met[0]["wait_eta"], "—")
            self.assertEqual(dash_met[0]["eta_bottleneck"], "metal")

    # PW-04: R1 (merchant_mode + planner_mode) x R3 (Config UI -> API -> Queue UI)
    def test_pw_04_config_ui_merchant_mode_and_ga_nn_updates_queue_ui(self) -> None:
        _import_eta_helpers(self)
        _import_optimizer(self)
        srv = FakeSpaceInvasionServer()
        cfg = make_ga_nn_config(self, planner_mode="priority", merchant_mode=False)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            res = run_ui_in_node(
                h.base_url,
                """
                const initOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 5000);
                if (!initOk) throw new Error('dash-plan-tbody failed to populate initial rows');
                const pm = document.getElementById('cfg-planner_mode');
                const mm = document.getElementById('cfg-merchant_mode');
                if (!pm || !mm) throw new Error('Missing #cfg-planner_mode or #cfg-merchant_mode');
                pm.value = 'ai_planner';
                pm.dispatchEvent({ type: 'change', target: pm });
                mm.checked = true;
                mm.dispatchEvent({ type: 'change', target: mm });
                const dashBefore = fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length;
                document.getElementById('btn-save-config').click();
                const saveOk = await __waitUntil(() => fetchLog.some((f) => f.url.includes('/command') && f.body && f.body.command === 'update_config'), 4000);
                if (!saveOk) throw new Error('update_config POST not dispatched');
                await __waitUntil(() => fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length > dashBefore, 4000);
                const rowsOk = await __waitUntil(() => __serializeTable('dash-plan-tbody').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!rowsOk) throw new Error('dash-plan-tbody empty after save refresh');
                return {
                  dashRows: __serializeTable('dash-plan-tbody'),
                  opsRows: __serializeTable('plan-tbody'),
                  dashHeaders: document.querySelectorAll('#view-dashboard table thead th').length,
                  opsHeaders: document.querySelectorAll('#view-operations table thead th').length
                };
                """,
            )
            disk_cfg = load_strategy_config(h.config_path)
            self.assertEqual(disk_cfg.planner_mode, "ai_planner")
            self.assertTrue(disk_cfg.merchant_mode)

            plan_after = h.api.send_command({"command": "plan"})["actions"]
            self.assertFalse(any(a.get("target_id") == BLAST_FURNACE and a.get("kind") == "BUILD" for a in plan_after))
            sc = res["scenario"]
            self.assertGreater(len(sc["dashRows"]), 0)
            self.assertGreater(len(sc["opsRows"]), 0)
            self.assertGreaterEqual(sc["dashHeaders"], 6)
            self.assertGreaterEqual(sc["opsHeaders"], 7)

    # PW-05: R1 (Multi-planet + Asteroid + hot s9) x R3 (per-planet ETA & #planet-select)
    def test_pw_05_multi_planet_asteroid_hot_s9_and_per_planet_etas(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        _import_optimizer(self)
        cfg = make_ga_nn_config(self, allow_multi_planet=True, allow_shipyard=True, hot_planet_solar_temp_threshold=80)
        cold_p = make_planet(
            planet_id=101,
            name="ColdSpice",
            coords="02:380:15",
            planet_type="normal",
            temperature=-120,
            fields_max=230,
            buildings={"b1": 4, "b3": 4, "b4": 5, "b5": 6, "b7": 4},
            resources=Resources(9000, 6000, 5000, 2000),
            auto_offers=True,
        )
        hot_p = make_planet(
            planet_id=102,
            name="HotTight",
            coords="02:380:01",
            planet_type="normal",
            temperature=120,
            fields_max=230,
            fields_used=229,
            buildings={"b1": 4, "b3": 4, "b4": 2, "b5": 4, "b9": 1},
            energy=EnergyBalance(produced=100, used=125),
            resources=Resources(9000, 9000, 5000, 2000),
            ship_offers=(make_ship_offer(9, owned=2, can_build=True),),
            auto_offers=True,
        )
        astro_p = make_planet(
            planet_id=103,
            name="AstroBase",
            coords="02:380:12",
            planet_type="asteroid",
            temperature=10,
            fields_max=10,
            fields_used=2,
            buildings={"b7": 1, "b9": 3},
            energy=EnergyBalance(produced=0, used=0),
            resources=Resources(5000, 5000, 3000, 2000),
            auto_offers=True,
        )
        state = make_game_state(planets=(cold_p, hot_p, astro_p), active_planet_id=101, speedup_factor=5.0)
        actions = plan_actions(state, config=cfg)

        # Asteroid must never schedule non-asteroid mines b1..b6
        astro_mines = [a for a in actions if a.planet_id == 103 and a.kind == "BUILD" and a.target_id in (1, 2, 3, 4, 5, 6)]
        self.assertEqual(astro_mines, [], f"Asteroid scheduled forbidden mine: {astro_mines}")

        # Hot tight colony prefers 0-field Solar cells s9 when mines starve energy
        hot_s9 = [a for a in actions if a.planet_id == 102 and a.kind == "BUILD_SHIP" and a.target_id == 9]
        self.assertGreater(len(hot_s9), 0, f"Expected 0-field s9 Solar cell build on hot tight colony; got {[a for a in actions if a.planet_id == 102]}")

        # Every action's ETA matches compute_action_eta evaluated against its own planet_id economy
        for act in actions:
            _verify_eta_fields(self, act)
            expected = compute_action_eta(state, act)
            self.assertAlmostEqual(float(act.wait_seconds), float(expected["wait_seconds"]), delta=1.0)
            self.assertAlmostEqual(float(act.duration_seconds), float(expected["duration_seconds"]), delta=1.0)

        # UI #planet-select works cleanly on multi-planet live harness
        planets_dict = {
            101: {
                "planet_id": 101,
                "name": "ColdSpice",
                "coords": "02:380:15",
                "planet_type": "normal",
                "temperature": -120,
                "fields_max": 230,
                "resources": [9000, 6000, 5000, 2000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 4, "b3": 4, "b4": 5, "b5": 6, "b7": 4},
                "ships": {},
                "defense": {},
                "building_queue": [],
            },
            102: {
                "planet_id": 102,
                "name": "HotTight",
                "coords": "02:380:01",
                "planet_type": "normal",
                "temperature": 120,
                "fields_max": 230,
                "fields_used": 229,
                "resources": [9000, 9000, 5000, 2000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 100, "used": 125},
                "buildings": {"b1": 4, "b3": 4, "b4": 2, "b5": 4, "b9": 1},
                "ships": {},
                "defense": {},
                "building_queue": [],
            },
        }
        srv = FakeSpaceInvasionServer(planets=planets_dict, active_planet_id=101, speedup_factor=5.0)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            ui_res = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => document.querySelectorAll('#planet-select option').length >= 2, 4000);
                const sel = document.getElementById('planet-select');
                sel.value = '102';
                sel.dispatchEvent({ type: 'change', target: sel });
                const switchedOk = await __waitUntil(() => __serializeTable('dash-plan-tbody').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 3000);
                if (!switchedOk) throw new Error('dash-plan-tbody empty after planet switch');
                return {
                  selected: sel.value,
                  rows: __serializeTable('dash-plan-tbody').length
                };
                """,
            )
            self.assertEqual(ui_res["scenario"]["selected"], "102")
            self.assertGreater(ui_res["scenario"]["rows"], 0)

    # PW-06: R2 (RateLimitError cooldown) x R3 (UI status + #feedback-banner + cached plan)
    def test_pw_06_rate_limit_cooldown_ui_feedback_and_cached_plan(self) -> None:
        _import_eta_helpers(self)
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, server_cooldown_s=45.0, rate_limit_on_frame="F_Hauptframe")
        cfg = StrategyConfig(allow_galaxy_scan=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            dash_ok = h.get_dashboard(refresh=0)
            self.assertGreater(len(dash_ok["plan"]), 0)
            _verify_eta_fields(self, dash_ok["plan"][0])

            srv.force_rate_limit_remaining = 1
            r = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            self.assertIn(r.status_code, (429, 503))
            body = r.json()
            self.assertFalse(body["ok"])
            self.assertTrue(body.get("rate_limited"))
            self.assertGreater(float(body.get("retry_after", 0)), 0.0)

            health = h.api.health()
            self.assertTrue(health["in_game"])
            self.assertTrue(health.get("rate_limited"))

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
                if (!postOk) throw new Error('scan_galaxy POST not recorded in fetchLog');
                const bannerOk = await __waitUntil(() => {
                  const b = __snapshotElement('feedback-banner');
                  return b && !b.hidden && !String(b.textContent || '').includes('Scanning home Galaxy');
                }, 4000);
                if (!bannerOk) throw new Error('feedback-banner did not update after scan_galaxy response');
                return {
                  chromeText: document.getElementById('chrome-status-text') ? document.getElementById('chrome-status-text').textContent.trim() : '',
                  banner: __snapshotElement('feedback-banner'),
                  dashRows: __serializeTable('dash-plan-tbody')
                };
                """,
            )
            self.assertEqual(ui_res["errors"], [])
            sc = ui_res["scenario"]
            self.assertIn("ONLINE", sc["chromeText"].upper())
            self.assertGreater(len(sc["dashRows"]), 0)
            self.assertIsNotNone(sc["banner"])
            self.assertFalse(sc["banner"]["hidden"])
            self.assertIn("is-warn", sc["banner"]["className"], f"Expected .is-warn cooldown banner: {sc['banner']}")
            dash_rl = h.get_dashboard(refresh=0)
            self.assertTrue(dash_rl["health"]["rate_limited"])
            self.assertTrue(dash_rl["health"]["in_game"])

    # PW-07: R2 (PageViewLimiter + Commander backoff) x R1 (ga_nn) x R3 (ETA logs & SQLite)
    def test_pw_07_commander_ga_nn_pacer_backoff_update_config_logs_sqlite(self) -> None:
        _import_eta_helpers(self)
        _import_optimizer(self)
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(
            clock=clock,
            rate_limit_threshold=25,
            server_cooldown_s=45.0,
            rate_limit_on_frame="F_Hauptframe",
        )
        cfg = make_ga_nn_config(self, allow_galaxy_scan=True, allow_raids=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg) as h:
            # 1) Normal ga_nn commander tick
            res = commander.run_commander_tick(h.api, live=True, max_actions=1, config=cfg)
            self.assertIsNotNone(res)

            # 2) Rate-limit hit triggers full >=45s backoff and recovery in loop
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

            # 3) update_config syncs live + dry executors
            uc = h.api.send_command({"command": "update_config", "config": {"max_actions_per_hour": 10}})
            self.assertEqual(uc["config"]["max_actions_per_hour"], 10)
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 9)

            # 4) Logs & SQLite action_history preserve ETA info & redact sid
            jsonl = (h.log_dir / "commander.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("abc123def456", jsonl)
            self.assertIn("wait_eta", jsonl)
            db_actions = h.api.send_command({"command": "db_summary"})["db"].get("recent_actions", [])
            self.assertGreater(len(db_actions), 0)

    # PW-08: R2 (consecutive open_galaxy + multi-system sweep) x R3 (UI Targets + plan ETA)
    def test_pw_08_consecutive_galaxy_sweep_sid_fallback_and_ui_targets(self) -> None:
        _import_eta_helpers(self)
        srv = FakeSpaceInvasionServer(sid_mode="empty", p_galaxy_mode="in_frame", async_nav_polls=2)
        cfg = StrategyConfig(allow_galaxy_scan=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            srv.page_view_log.clear()
            for sys_n in (380, 381, 382):
                res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": sys_n, "dry_run": False})
                self.assertTrue(res["executed"])

            # scan_galaxy must not invoke redundant snapshot rebuilds
            actions_hit = [v["action"] for v in srv.page_view_log]
            for forbidden in ("internalBuildings", "internalResearch", "internalWeaponFactory", "internalHome"):
                self.assertNotIn(forbidden, actions_hit, f"Redundant view {forbidden} during consecutive scans: {actions_hit}")

            coords = {t["coords"] for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertIn("02:380:04", coords)
            self.assertIn("02:381:05", coords)
            self.assertIn("02:382:07", coords)

            plan = h.api.send_command({"command": "plan"})["actions"]
            _verify_eta_fields(self, plan[0])

            ui_res = run_ui_in_node(
                h.base_url,
                """
                document.querySelector('[data-page="combat"]').click();
                await __waitUntil(() => document.querySelectorAll('#cmb-targets-tbody tr').length >= 3, 4000);
                return { rows: __serializeTable('cmb-targets-tbody') };
                """,
            )
            flat = " ".join(" ".join(c["text"] for c in r) for r in ui_res["scenario"]["rows"])
            self.assertIn("02:380:04", flat)
            self.assertIn("02:381:05", flat)
            self.assertIn("02:382:07", flat)

    # PW-09: R2 (Spy recon -> Combat Sim -> Attack) x R3 (Queue ETA)
    def test_pw_09_spy_to_combat_sim_to_attack_favourites_and_eta(self) -> None:
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
                "name": "StagingColony",
                "coords": "02:380:14",
                "planet_type": "normal",
                "temperature": 22,
                "fields_max": 240,
                "resources": [5000, 5000, 5000, 2000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100},
                "buildings": {"b1": 4, "b5": 6, "b9": 2},
                "ships": {"s4": 20, "s1": 5},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            },
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
            spy_reports=spy_reports,
            fleet_form_dialect="standard",
            async_nav_polls=2,
        )
        cfg = StrategyConfig(allow_galaxy_scan=True, allow_raids=True, allow_multi_planet=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            spy_res = h.api.send_command({"command": "spy", "target_coords": "02:380:04", "planet_id": 12476, "dry_run": False})
            self.assertTrue(spy_res["executed"])
            sync_res = h.api.send_command({"command": "sync_spy_reports"})
            self.assertGreaterEqual(sync_res["synced_reports"], 1)

            sim = h.api.send_command(
                {
                    "command": "simulate_combat",
                    "attacker_ships": {"s4": 20, "s1": 5},
                    "defender_defense": {"d1": 1},
                    "target_resources": {"pig_iron": 30000, "metal": 20000, "kryptonite": 10000, "spice": 4000},
                    "target_coords": "02:380:04",
                }
            )["simulation"]
            self.assertTrue(sim["safe_to_attack"])

            plan_res = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            atk = next((a for a in plan_res if a["kind"] == "ATTACK" and "02:380:04" in a["target_name"]), None)
            self.assertIsNotNone(atk)
            assert atk is not None
            _verify_eta_fields(self, atk)
            self.assertEqual(atk["wait_eta"], "Ready")
            self.assertEqual(atk["duration_text"], "Instant")

            att_res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False})
            self.assertTrue(att_res["executed"])
            self.assertEqual(srv.launched_fleets[-1]["origin_planet_id"], 12477)

    # PW-10: R1 (P0 Hostile Override under ga_nn) x R2 (Escape Flight) x R3 (Ready/Instant ETA)
    def test_pw_10_p0_hostile_escape_overrides_ga_nn_with_ready_instant_eta(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        _import_optimizer(self)
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [40000, 30000, 15000, 20000],
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
                "name": "SafeColony",
                "coords": "02:380:15",
                "planet_type": "normal",
                "temperature": -80,
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
            hostile_alerts=["Hostile Attack detected arriving at 02:380:12 in 00:05:12"],
        )
        cfg = make_ga_nn_config(self, allow_fleetsave=False, fleet_save_fuel_reserve_spice=1500)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            plan_off = h.api.send_command({"command": "plan"})["actions"]
            self.assertEqual(plan_off[0]["kind"], "ALERT_HOSTILE")
            self.assertFalse(plan_off[0]["executable_now"])
            esc = next(a for a in plan_off if a["kind"] == "ESCAPE_FLIGHT")
            _verify_eta_fields(self, esc)
            self.assertFalse(esc["executable_now"])
            self.assertEqual(esc["wait_eta"], "Ready")
            self.assertEqual(esc["duration_text"], "Instant")
            # Cargo prioritizes Spice -> Metal -> Kryptonite -> Pig iron and respects fuel reserve
            self.assertEqual(esc["cost"]["spice"], 20000 - 1500)

            h.api.send_command({"command": "update_config", "config": {"allow_fleetsave": True}})
            plan_on = h.api.send_command({"command": "plan"})["actions"]
            esc_on = next(a for a in plan_on if a["kind"] == "ESCAPE_FLIGHT")
            self.assertTrue(esc_on["executable_now"])

    # PW-11: R1 (Shipyard targets + speedup) x R3 (BUILD_SHIP duration + UI [data-filter="ready"])
    def test_pw_11_shipyard_build_ship_duration_divisor_and_ui_ready_filter(self) -> None:
        compute_action_eta, attach_action_etas = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 5, "b3": 4, "b4": 2, "b5": 7, "b7": 4, "b9": 4, "b17": 1},
            resources=Resources(50000, 0, 50000, 50000),
            researches={"r1": 2, "r2": 2},
            ship_offers=(make_ship_offer(4, owned=0, can_build=True),),
            building_offers=(make_building_offer(7, 4, cost=Resources(0, 999999, 50000, 50000), can_build=False),),
        )
        state = make_game_state(planets=(p,), researches={"r1": 2, "r2": 2}, speedup_factor=5.0)
        ship_act = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=p.planet_id,
            planet_coords=p.coords,
            target_id=4,
            target_name="Small fighter",
            target_level=10,
            cost=Resources(10 * 1600, 0, 10 * 1600, 0),
            missing=Resources(),
            reason="Shipyard batch",
            executable_now=True,
        )
        eta = compute_action_eta(state, ship_act)
        # Duration divisor = b9 * 2^b17 * speedup = 4 * 2 * 5.0 = 40.0
        # Wait/duration exact base formula for s4: (10 * 2133.333333) / 40.0 = 533.33s
        expected_dur = (10 * 2133.333333) / (4 * (2 ** 1) * 5.0)
        self.assertAlmostEqual(float(eta["duration_seconds"]), expected_dur, delta=5.0)
        self.assertEqual(eta["wait_eta"], "Ready")

        # UI Operations ready filter check against live bridge
        srv = FakeSpaceInvasionServer(speedup_factor=5.0)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            ui_res = run_ui_in_node(
                h.base_url,
                """
                document.querySelector('[data-page="operations"]').click();
                await __waitUntil(() => document.querySelectorAll('#plan-tbody tr').length >= 3, 4000);
                const allRows = document.querySelectorAll('#plan-tbody tr').length;
                const readyBtn = document.querySelector('[data-filter="ready"]');
                if (!readyBtn) throw new Error('Missing [data-filter="ready"]');
                readyBtn.click();
                await __sleep(80);
                const visibleReady = Array.from(document.querySelectorAll('#plan-tbody tr')).filter((tr) => !tr.hidden);
                return {
                  allRows,
                  visibleRows: visibleReady.length,
                  visibleCells: visibleReady.map((tr) => Array.from(tr.children).map((td) => td.textContent.trim()))
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertGreaterEqual(sc["allRows"], 3)
            self.assertGreater(sc["visibleRows"], 0)
            self.assertLessEqual(sc["visibleRows"], sc["allRows"])

    # PW-12: R1 (Negative Pig net drain) x R2 (THROTTLE_BUILDING 0%) x R3 (Blocked -> Unblocked ETA flip)
    def test_pw_12_negative_pig_drain_throttle_flips_eta_bottleneck(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
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
        srv = FakeSpaceInvasionServer(planets=planets)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            before_plan = h.api.send_command({"command": "plan"})["actions"]
            b1_before = next((a for a in before_plan if a["kind"] in ("BUILD", "WAIT_RESOURCES") and a.get("target_id") == PIG_IRON_MINE), None)
            self.assertIsNotNone(b1_before)
            assert b1_before is not None
            _verify_eta_fields(self, b1_before)
            self.assertEqual(b1_before["wait_eta"], "—", f"Expected b1 blocked by Pig net drain < 0: {b1_before}")
            self.assertEqual(b1_before["eta_bottleneck"], "pig_iron")
            self.assertEqual(b1_before["total_eta"], "—")

            th_res = h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 0, "dry_run": False})
            self.assertTrue(th_res["executed"])

            after_plan = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            b1_after = next((a for a in after_plan if a["kind"] in ("BUILD", "WAIT_RESOURCES") and a.get("target_id") == PIG_IRON_MINE), None)
            self.assertIsNotNone(b1_after)
            assert b1_after is not None
            _verify_eta_fields(self, b1_after)
            self.assertNotEqual(b1_after["wait_eta"], "—", f"Expected b1 unblocked after b2=0%: {b1_after}")
            self.assertNotEqual(b1_after["total_eta"], "—")

            met_after = next((a for a in after_plan if a["cost"]["metal"] > 0 and a["missing"]["metal"] > 0), None)
            self.assertIsNotNone(met_after)
            assert met_after is not None
            self.assertEqual(met_after["eta_bottleneck"], "metal")
            self.assertEqual(met_after["wait_eta"], "—")

            # Dashboard also reflects persisted 0% rate + updated ETAs
            dash = h.get_dashboard(refresh=0)
            self.assertEqual(dash["state"]["planets"][0]["economy_rates"].get("resource2"), 0)

    # PW-13: R2 (Multi-raid DB + temp attack_history.json sync) x R1 (ga_nn full-history shift) x R3 (API/ETA)
    def test_pw_13_multi_raid_db_sync_feeds_ga_nn_and_eta_api_payloads(self) -> None:
        _import_eta_helpers(self)
        _import_optimizer(self)
        gal = {
            (2, 380): [
                {"position": 4, "name": "FarmGood", "owner": "IdleA", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 5, "name": "TrapBad", "owner": "IdleB", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
            ]
        }
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [10000, 8000, 6000, 3000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 140},
                "buildings": {"b1": 5, "b5": 7, "b9": 2},
                "ships": {"s4": 25, "s1": 8, "s10": 5},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            }
        }
        same_loot_raw = {"pig_iron": 20000, "metal": 20000, "kryptonite": 10000, "spice": 5000}
        spy_reports = {
            "02:380:04": {"owner": "IdleA", "resources": same_loot_raw, "fleet": {}, "defense": {}},
            "02:380:05": {"owner": "IdleB", "resources": same_loot_raw, "fleet": {}, "defense": {}},
        }
        srv = FakeSpaceInvasionServer(planets=planets, galaxy_systems=gal, spy_reports=spy_reports)
        cfg = make_ga_nn_config(self, allow_galaxy_scan=True, allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            for coords in ("02:380:04", "02:380:05"):
                h.api.send_command({"command": "spy", "target_coords": coords, "probes": 1, "dry_run": False})
            h.api.send_command({"command": "sync_spy_reports"})

            db = GameDatabase(h.db_path)
            try:
                self.assertTrue(hasattr(db, "get_raid_stats"), "GameDatabase missing get_raid_stats()")
                for i in range(6):
                    db.record_raid("02:380:04", attacked_ts=1_700_000_000.0 + i * 60, is_exempt=True, outcome="Victory", loot={"pig_iron": 15000, "metal": 10000})
                    db.record_raid("02:380:05", attacked_ts=1_700_000_000.0 + i * 60, is_exempt=True, outcome="Defeat / bounced", loot={})
                mirror = getattr(db, "attack_history_path", None) or (h.db_path.parent / "attack_history.json")
            finally:
                db.close()

            # Live attack updates SQLite DB cleanly without writing a JSON mirror
            res_atk = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(res_atk["executed"])
            self.assertTrue(Path(h.db_path).exists())
            self.assertFalse(Path(mirror).exists())

            st_obj = h.api.get_state(include_buildings=True)
            self.assertGreater(len(st_obj.planets), 0)
            st_json = requests.get(f"{h.base_url}/state?include_buildings=1", timeout=5.0).json()
            self.assertTrue(
                "raid_stats" in st_json or any("attack_success_rate" in r for r in st_json.get("raid_targets", [])),
                "Expected full raid_stats / per-target attack_success_rate exposed via HTTP /state",
            )

            plan = h.api.send_command({"command": "plan", "planner_mode": "ga_nn", "refresh": True})["actions"]
            atks = [a for a in plan if a["kind"] == "ATTACK" and a["executable_now"]]
            self.assertGreater(len(atks), 0)
            self.assertIn("02:380:04", atks[0]["target_name"])
            for row in plan:
                _verify_eta_fields(self, row)


if __name__ == "__main__":
    unittest.main()
