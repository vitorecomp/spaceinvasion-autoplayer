"""Tests for Plan v4 of adaptive AI-Planner execution.

Four behaviours, all behind `ai_adaptive_execution`:

- the empire smelting arm: a furnace also runs while the empire's waiting steps need more Metal than the
  pooled stock holds, unless Pig iron is the scarcer resource or the colony cannot power it;
- the live fill rule: a colony whose builder is free while its next plan step waits starts a later plan
  step that does not hold the waiting step back and spends nothing the empire is short of;
- plan-order loans: a colony that cannot yet pay for its own next step lends what it holds back for it
  to a waiting step that comes earlier in the plan;
- the plan-sized logistics fleet leaves hulls away on raids out of its count, up to a ceiling.
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from spaceinvasion.core import game_data
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import EnergyBalance, GameState, PlanetState, QueueJob, Resources
from spaceinvasion.planner import ai_planner as ap
from spaceinvasion.planner.config_optimizer import full_cost
from spaceinvasion.planner.strategy import (
    AI_PLAN_ORDER_LOAN_TAG,
    EMPIRE_SMELT_MIN_SPARE_PIG,
    INFRA_TRANSPORT_FLEET_CAP_RATIO,
    PLAN_ORDER_LOAN_MIN_CARGO,
    PLAN_ORDER_LOAN_MIN_SHARE,
    PlannedAction,
    compute_infra_transport_targets,
    plan_ai_blast_furnace_throttles,
    plan_interplanet_transports,
)
from tests.unit.test_ai_planner import _make_planet
from tests.unit.test_strategy import _adaptive_cfg, _ai_step, _colony, _state

FURNACE_KWARGS = dict(
    planner_mode="ai_planner",
    allow_multi_planet=True,
    allow_transport=True,
    auto_throttle_blast_furnace=True,
    merchant_mode=False,
)


class TestEmpireSmeltingArm(unittest.TestCase):
    """A furnace runs for the empire's Metal shortage, not only for its own colony's next step."""

    def setUp(self) -> None:
        self.cfg = _adaptive_cfg(True, **FURNACE_KWARGS)
        self.cfg_off = _adaptive_cfg(False, **FURNACE_KWARGS)

    def _plan(self, state, ai, **kwargs):
        return plan_ai_blast_furnace_throttles(state, ai, [], self.cfg, now_ts=5000.0, **kwargs)

    def test_colony_with_a_busy_queue_smelts_for_the_empire(self) -> None:
        # Own step is 1,000 Metal short but its queue stays busy far longer than smelting needs.
        colony = _colony(901, "02:380:04", metal=10_000, b2_pct=0)
        ai = [_ai_step(901, "02:380:04", 11_000)]
        busy = {901: 1e9}
        # Without the empire arm the furnace waits for its own queue (Plan v2 behaviour).
        self.assertEqual(self._plan(_state((colony,)), ai, in_progress_rem_by_planet=busy), [])
        # With it, the empire's waiting steps lack Metal (11,000 needed, 10,000 pooled), so it smelts now.
        acts = self._plan(_state((colony,)), ai, in_progress_rem_by_planet=busy, enable_donor_smelting=True)
        self.assertEqual([(a.planet_id, a.target_level) for a in acts], [(901, 100)])
        self.assertIn("AI-Planner empire", acts[0].reason)
        self.assertIn("1,000 more Metal", acts[0].reason)

    def test_running_furnace_holds_until_pooled_metal_is_ten_percent_above_need(self) -> None:
        needy = _colony(901, "02:380:04", metal=0, b2=0)
        ai = [_ai_step(901, "02:380:04", 100_000)]

        def acts_for(metal: int, b2_pct: int):
            helper = _colony(902, "02:380:08", metal=metal, b2_pct=b2_pct)
            return self._plan(_state((needy, helper)), ai, enable_donor_smelting=True)

        # Pooled Metal covers the need by 5%: a running furnace keeps running, a stopped one stays off.
        self.assertEqual(acts_for(105_000, 100), [])
        self.assertEqual(acts_for(105_000, 0), [])
        # 11% above the need: the running furnace is released.
        off = acts_for(111_000, 100)
        self.assertEqual([(a.planet_id, a.target_level) for a in off], [(902, 0)])
        # Short of the need: a stopped furnace starts.
        on = acts_for(60_000, 0)
        self.assertEqual([(a.planet_id, a.target_level) for a in on], [(902, 100)])

    def test_pig_iron_is_not_burned_when_it_is_the_scarcer_resource(self) -> None:
        needy = _colony(901, "02:380:04", metal=0, b2=0)
        needy_step = _ai_step(901, "02:380:04", 80_000)

        # 1. The colony's own waiting step needs Pig iron: only what is above that cost may be smelted.
        own_pig_step = _ai_step(902, "02:380:08", 0, pig=55_000, name="Pig iron mine")
        tight = _colony(902, "02:380:08", metal=5_000, pig=60_000, b2_pct=0)
        self.assertEqual(
            self._plan(_state((needy, tight)), [needy_step, own_pig_step], enable_donor_smelting=True), []
        )
        roomy = replace(tight, resources=replace(tight.resources, pig_iron=55_000 + EMPIRE_SMELT_MIN_SPARE_PIG))
        acts = self._plan(_state((needy, roomy)), [needy_step, own_pig_step], enable_donor_smelting=True)
        self.assertEqual([(a.planet_id, a.target_level) for a in acts], [(902, 100)])

        # 2. The empire's waiting steps need more Pig iron than the pooled stock holds: no empire smelting.
        pig_hungry = _ai_step(901, "02:380:04", 80_000, pig=500_000)
        helper = _colony(902, "02:380:08", metal=5_000, pig=60_000, b2_pct=0)
        self.assertEqual(self._plan(_state((needy, helper)), [pig_hungry], enable_donor_smelting=True), [])
        # ... and a furnace that was running for the empire is released.
        running = replace(helper, economy_rates={**helper.economy_rates, "resource2": 100})
        released = self._plan(_state((needy, running)), [pig_hungry], enable_donor_smelting=True)
        self.assertEqual([(a.planet_id, a.target_level) for a in released], [(902, 0)])

        # 3. A stopped furnace needs a real Pig iron reserve to start; a running one uses what is there.
        low = _colony(902, "02:380:08", metal=5_000, pig=10_000, b2_pct=0)
        self.assertEqual(self._plan(_state((needy, low)), [needy_step], enable_donor_smelting=True), [])
        low_running = replace(low, economy_rates={**low.economy_rates, "resource2": 100})
        self.assertEqual(self._plan(_state((needy, low_running)), [needy_step], enable_donor_smelting=True), [])
        empty_running = replace(low_running, resources=replace(low_running.resources, pig_iron=0))
        stop = self._plan(_state((needy, empty_running)), [needy_step], enable_donor_smelting=True)
        self.assertEqual([(a.planet_id, a.target_level) for a in stop], [(902, 0)])

    def test_empire_arm_never_runs_a_furnace_the_colony_cannot_power(self) -> None:
        needy = _colony(901, "02:380:04", metal=0, b2=0)
        ai = [_ai_step(901, "02:380:04", 80_000)]
        helper = _colony(902, "02:380:08", metal=5_000, pig=60_000, b2_pct=0)
        furnace_energy = game_data.mine_energy_needed(2, 10)
        self.assertGreater(furnace_energy, 1)

        no_room = replace(helper, energy=EnergyBalance(produced=1500, used=1500 - furnace_energy + 1))
        self.assertEqual(self._plan(_state((needy, no_room)), ai, enable_donor_smelting=True), [])
        just_enough = replace(helper, energy=EnergyBalance(produced=1500, used=1500 - furnace_energy))
        acts = self._plan(_state((needy, just_enough)), ai, enable_donor_smelting=True)
        self.assertEqual([(a.planet_id, a.target_level) for a in acts], [(902, 100)])
        # A furnace running for the empire is released when the colony falls short of energy.
        deficit = replace(
            helper,
            energy=EnergyBalance(produced=1400, used=1500),
            economy_rates={**helper.economy_rates, "resource2": 100},
        )
        off = self._plan(_state((needy, deficit)), ai, enable_donor_smelting=True)
        self.assertEqual([(a.planet_id, a.target_level) for a in off], [(902, 0)])
        # The colony's own need still runs the furnace regardless of energy (unchanged Plan v2 rule).
        own = [_ai_step(902, "02:380:08", 80_000)]
        own_acts = self._plan(_state((needy, no_room)), own, enable_donor_smelting=True)
        self.assertEqual([(a.planet_id, a.target_level) for a in own_acts], [(902, 100)])
        self.assertIn("on this colony", own_acts[0].reason)

    def test_switch_off_is_unchanged(self) -> None:
        colony = _colony(901, "02:380:04", metal=10_000, b2_pct=0)
        helper = _colony(902, "02:380:08", metal=5_000, b2_pct=0)
        ai = [_ai_step(901, "02:380:04", 11_000)]
        st = _state((colony, helper))
        baseline = plan_ai_blast_furnace_throttles(st, ai, [], StrategyConfig(**FURNACE_KWARGS), now_ts=5000.0)
        with_new = plan_ai_blast_furnace_throttles(
            st, ai, [], self.cfg_off, now_ts=5000.0,
            in_progress_rem_by_planet={901: 1e9}, enable_donor_smelting=True,
        )
        self.assertEqual(baseline, with_new)


def _step(seq: int, pid: int, coords: str, key: str, level: int, kind: str = "BUILD") -> dict[str, object]:
    if key.startswith("b"):
        name = game_data.BUILDING_BY_ID[int(key[1:])]["name"]
    else:
        name = game_data.RESEARCH_BY_ID[key]["name"]
    return {
        "seq": seq,
        "kind": kind,
        "planet_id": pid if kind == "BUILD" else 0,
        "planet_coords": coords if kind == "BUILD" else "account",
        "planet_name": "X",
        "entity_key": key,
        "entity_name": name,
        "target_level": level,
    }


X_BUILDINGS = {"b1": 9, "b2": 9, "b3": 9, "b4": 4, "b5": 9, "b7": 9}
BIG_STORE = Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000)
DEV_L10 = full_cost(game_data.building_cost(7, 10))  # Metal 307,200 / Kryptonite 153,600 / Spice 102,400
KRY_MINE_L10 = full_cost(game_data.building_cost(3, 10))  # Pig iron 3,674 / Kryptonite 2,245


def _x(resources: Resources, *, pid: int = 101, coords: str = "02:380:12", buildings=None, **kwargs) -> PlanetState:
    return _make_planet(
        pid, coords, f"X{pid}", buildings=dict(buildings or X_BUILDINGS), resources=resources, storage=BIG_STORE, **kwargs
    )


def _game(planets, *, queues=(), researches=None, active: int = 101) -> GameState:
    return GameState(
        universe_id=31,
        user_id=1,
        nickname="Tester",
        timestamp=1_700_000_000.0,
        planets=tuple(planets),
        active_planet_id=active,
        researches=dict(researches or {"r1": 8, "r2": 4}),
        active_queues=tuple(queues),
        speedup_factor=5.0,
    )


def _adaptive(on: bool = True) -> StrategyConfig:
    return _adaptive_cfg(on, allow_multi_planet=True)


class TestLiveFillRule(unittest.TestCase):
    """A free builder starts a later plan step while the colony's next step waits, without holding it back."""

    PLAN = {
        "steps": [
            _step(1, 101, "02:380:12", "b7", 10),  # waits for Metal
            _step(2, 101, "02:380:12", "b4", 5),  # costs Metal: the empire is short of it
            _step(3, 101, "02:380:12", "b3", 10),  # Pig iron + Kryptonite only
            _step(4, 101, "02:380:12", "b1", 10),
        ]
    }
    WAITING_FOR_METAL = Resources(500_000, 100_000, 400_000, 200_000)

    def _reconcile(self, state, plan=None, cfg=None):
        actions, conflicts = ap.reconcile_live_ai_actions(state, plan or self.PLAN, cfg or _adaptive())
        self.assertEqual(conflicts, [])
        return actions

    def _kinds(self, actions):
        return [(a.kind, a.planet_id, a.target_id, a.target_level) for a in actions]

    def test_fill_starts_the_first_later_step_that_qualifies(self) -> None:
        state = _game([_x(self.WAITING_FOR_METAL)])
        actions = self._reconcile(state)
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10), ("BUILD", 101, 3, 10)])
        wait, fill = actions
        self.assertFalse(wait.executable_now)
        self.assertEqual(wait.missing.metal, DEV_L10["metal"] - 100_000)
        self.assertTrue(fill.executable_now)
        self.assertEqual(fill.priority, "P1")
        self.assertTrue(fill.reason.startswith(f"{ap.AI_FILL_REASON_PREFIX} #3: build Kryptonite mine L10"))
        self.assertIn("Development center L10 waits for Metal", fill.reason)
        self.assertEqual(fill.cost.kryptonite, KRY_MINE_L10["kryptonite"])
        self.assertTrue(fill.missing.is_zero())

    def test_switch_off_emits_no_fill(self) -> None:
        state = _game([_x(self.WAITING_FOR_METAL)])
        actions = self._reconcile(state, cfg=_adaptive(False))
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10)])
        self.assertEqual(ap.plan_live_fill_actions(state, {"steps": []}, _adaptive(), actions), [])

    def test_no_fill_when_the_builder_is_busy_or_only_one_field_is_free(self) -> None:
        busy = QueueJob(
            category="building",
            name="Fusion plant (Level 10)",
            building_id=5,
            target_level=10,
            origin_coords="02:380:12",
        )
        actions = self._reconcile(_game([_x(self.WAITING_FOR_METAL)], queues=(busy,)))
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10)])

        used = sum(X_BUILDINGS.values())
        one_free = _x(self.WAITING_FOR_METAL, fields_max=used + 1)
        self.assertEqual(self._kinds(self._reconcile(_game([one_free]))), [("WAIT_RESOURCES", 101, 7, 10)])
        two_free = _x(self.WAITING_FOR_METAL, fields_max=used + 2)
        self.assertEqual(len(self._reconcile(_game([two_free]))), 2)

    def test_fill_never_spends_what_the_empire_is_short_of(self) -> None:
        # Colony 101 waits for Spice and holds more Metal than its own step needs; colony 102 waits for Metal.
        plan = {
            "steps": [
                _step(1, 101, "02:380:12", "b7", 10),
                _step(2, 102, "02:380:13", "b7", 10),
                _step(3, 101, "02:380:12", "b4", 5),  # Spice mine: Metal + Kryptonite
            ]
        }
        holder = _x(Resources(500_000, 400_000, 400_000, DEV_L10["spice"] - 458))
        short = _x(Resources(500_000, 0, 400_000, 200_000), pid=102, coords="02:380:13")
        # The two next steps need 614,400 Metal and the empire holds 400,000: the Metal stays for them.
        actions = self._reconcile(_game([holder, short]), plan)
        self.assertEqual(
            self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10), ("WAIT_RESOURCES", 102, 7, 10)]
        )
        # Once the empire holds more Metal than every next step needs, the same step is a valid fill.
        funded = replace(short, resources=replace(short.resources, metal=1_000_000))
        actions = self._reconcile(_game([holder, funded]), plan)
        self.assertEqual(
            self._kinds(actions),
            [("WAIT_RESOURCES", 101, 7, 10), ("BUILD", 102, 7, 10), ("BUILD", 101, 4, 5)],
        )
        self.assertTrue(actions[2].reason.startswith(f"{ap.AI_FILL_REASON_PREFIX} #3: build Spice mine L5"))

    def test_fill_leaves_alone_what_the_colony_cannot_produce(self) -> None:
        # The empire holds plenty of Metal, so a loan can pay for the waiting step and a Metal fill is allowed ...
        only_metal_step = {"steps": [self.PLAN["steps"][0], self.PLAN["steps"][1]]}
        rich = _x(Resources(0, 1_000_000, 0, 0), pid=102, coords="02:380:13")
        actions = self._reconcile(_game([_x(self.WAITING_FOR_METAL), rich]), only_metal_step)
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10), ("BUILD", 101, 4, 5)])
        # ... unless the colony makes no Metal itself (furnace off): what it holds for the waiting step stays.
        furnace_off = {"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100}
        no_metal = _x(self.WAITING_FOR_METAL, economy_rates=furnace_off)
        actions = self._reconcile(_game([no_metal, rich]), only_metal_step)
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10)])

    def test_pooled_spare_is_shared_between_fills_in_plan_order(self) -> None:
        kry_each = DEV_L10["kryptonite"] + int(0.75 * KRY_MINE_L10["kryptonite"])
        res = Resources(500_000, 100_000, kry_each, 200_000)
        plan = {
            "steps": [
                _step(1, 101, "02:380:12", "b7", 10),
                _step(2, 102, "02:380:13", "b7", 10),
                _step(3, 102, "02:380:13", "b3", 10),
                _step(4, 101, "02:380:12", "b3", 10),
            ]
        }
        state = _game([_x(res), _x(res, pid=102, coords="02:380:13")])
        actions = self._reconcile(state, plan)
        # Spare Kryptonite covers one Kryptonite mine only: the earlier plan step (#3, colony 102) gets it.
        self.assertEqual(
            self._kinds(actions),
            [("WAIT_RESOURCES", 101, 7, 10), ("WAIT_RESOURCES", 102, 7, 10), ("BUILD", 102, 3, 10)],
        )

    def test_fill_must_not_hold_the_waiting_step_back(self) -> None:
        plan = {"steps": [self.PLAN["steps"][0], self.PLAN["steps"][2]]}
        donor = _x(Resources(1_000_000, 0, 1_000_000, 1_000_000), pid=102, coords="02:380:13")
        # The waiting step lacks 600 Kryptonite (about 16 minutes of production); the fill costs 2,245.
        short_kry = _x(Resources(500_000, 400_000, DEV_L10["kryptonite"] - 600, 200_000))
        self.assertEqual(
            self._kinds(self._reconcile(_game([short_kry, donor]), plan)), [("WAIT_RESOURCES", 101, 7, 10)]
        )
        # With Kryptonite to spare on the colony the same fill costs the waiting step nothing.
        self.assertEqual(len(self._reconcile(_game([_x(self.WAITING_FOR_METAL), donor]), plan)), 2)

    def test_step_the_empire_can_pay_for_soon_only_gets_short_fills(self) -> None:
        # Alone, the colony would wait about 125 hours for Metal. When pooled stock already pays for its step,
        # a loan may land within AI_FILL_POOL_LEAD_HOURS and the builder has to be free again by then.
        deep_mine = {**X_BUILDINGS, "b1": 19}
        colony = _x(self.WAITING_FOR_METAL, buildings=deep_mine)
        long_fill = {"steps": [self.PLAN["steps"][0], _step(2, 101, "02:380:12", "b1", 20)]}
        short_fill = {"steps": [self.PLAN["steps"][0], self.PLAN["steps"][2]]}
        metal_rich = _x(Resources(0, 1_000_000, 0, 0), pid=102, coords="02:380:13")
        metal_poor = _x(Resources(0, 0, 0, 0), pid=102, coords="02:380:13")

        state = _game([colony, metal_rich])
        longest_allowed_h = ap.AI_FILL_POOL_LEAD_HOURS / (1.0 - ap.AI_FILL_MIN_PATIENCE)
        self.assertGreater(self._fill_duration_hours(state, "b1", 20), longest_allowed_h)
        self.assertLess(self._fill_duration_hours(state, "b3", 10), longest_allowed_h)
        self.assertEqual(self._kinds(self._reconcile(state, long_fill)), [("WAIT_RESOURCES", 101, 7, 10)])
        self.assertEqual(
            self._kinds(self._reconcile(state, short_fill)), [("WAIT_RESOURCES", 101, 7, 10), ("BUILD", 101, 3, 10)]
        )
        # With no Metal to pool, the step waits for days either way and the long fill is welcome.
        actions = self._reconcile(_game([colony, metal_poor]), long_fill)
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10), ("BUILD", 101, 1, 20)])

    def _fill_duration_hours(self, state, key: str, level: int) -> float:
        from spaceinvasion.planner.config_optimizer import build_monotonic_base_times, building_time_divisor

        planet = state.planets[0]
        info = game_data.BUILDING_BY_ID[int(key[1:])]
        levels_map = info.get("levels") or {}
        base = build_monotonic_base_times(levels_map, max(int(info.get("max_table_level") or len(levels_map)), level))
        div = building_time_divisor(planet.building_level(7), planet.building_level(17), ap._safe_speedup(state, {}))
        return float(base.get(level, 0.0)) / 3600.0 / max(1e-9, div)

    def test_no_fill_when_cargo_in_flight_already_pays_for_the_waiting_step(self) -> None:
        plan = {"steps": [self.PLAN["steps"][0], self.PLAN["steps"][2]]}
        donor = _x(Resources(1_000_000, 0, 1_000_000, 1_000_000), pid=102, coords="02:380:13")
        flight = QueueJob(
            category="fleet",
            name="Transport → [02:380:12] X101",
            remaining_seconds=600.0,
            origin_coords="02:380:13",
            cargo=Resources(pig_iron=0, metal=DEV_L10["metal"], kryptonite=0, spice=0),
        )
        actions = self._reconcile(_game([_x(self.WAITING_FOR_METAL), donor], queues=(flight,)), plan)
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10)])

    def test_waiting_step_that_depends_on_a_delivery_still_gets_fills(self) -> None:
        plan = {"steps": [self.PLAN["steps"][0], self.PLAN["steps"][2]]}
        donor = _x(Resources(1_000_000, 0, 1_000_000, 1_000_000), pid=102, coords="02:380:13")
        furnace_off = {"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100}
        # No local Metal production: the step waits for a transport. A Pig iron + Kryptonite fill is fine,
        waiting = _x(self.WAITING_FOR_METAL, economy_rates=furnace_off)
        self.assertEqual(len(self._reconcile(_game([waiting, donor]), plan)), 2)
        # even when it dips into Kryptonite the colony earns back long before the Metal can arrive,
        thin_kry = _x(Resources(500_000, 100_000, DEV_L10["kryptonite"] + 1_000, 200_000), economy_rates=furnace_off)
        self.assertEqual(len(self._reconcile(_game([thin_kry, donor]), plan)), 2)
        # but not when the colony makes no Kryptonite either.
        mines_off = {"resource1": 100, "resource2": 0, "resource3": 0, "resource4": 100}
        no_kry = replace(thin_kry, economy_rates=mines_off)
        self.assertEqual(
            self._kinds(self._reconcile(_game([no_kry, donor]), plan)), [("WAIT_RESOURCES", 101, 7, 10)]
        )

    def test_research_waiting_on_the_colony_is_protected(self) -> None:
        lab = {**X_BUILDINGS, "b8": 3}
        # Colony 102 comes first in the plan and needs far more Metal than the empire holds, so the lab
        # colony's own steps are paid by the lab colony alone. It has Pig iron and Kryptonite to spare.
        hog = _x(Resources(1_000_000, 0, 1_000_000, 1_000_000), pid=102, coords="02:380:13", buildings={**X_BUILDINGS, "b7": 11})
        hog_step = _step(1, 102, "02:380:13", "b7", 12)
        # The lab colony's building step lacks 458 Spice (about two hours); Mining technology L9 lacks
        # Pig iron and Kryptonite (about 54 hours), and a Kryptonite mine would add an hour to that.
        res = Resources(200_000, 400_000, 160_000, DEV_L10["spice"] - 458)
        build_step = _step(2, 101, "02:380:12", "b7", 10)
        fill_step = _step(4, 101, "02:380:12", "b3", 10)
        build_only = {"steps": [hog_step, build_step, fill_step]}
        with_research = {"steps": [hog_step, build_step, _step(3, 0, "account", "r1", 9, kind="RESEARCH"), fill_step]}
        state = _game([_x(res, buildings=lab), hog])
        self.assertEqual(
            self._kinds(self._reconcile(state, build_only)),
            [("WAIT_RESOURCES", 102, 7, 12), ("WAIT_RESOURCES", 101, 7, 10), ("BUILD", 101, 3, 10)],
        )
        actions = self._reconcile(state, with_research)
        self.assertEqual(
            self._kinds(actions),
            [("WAIT_RESOURCES", 102, 7, 12), ("WAIT_RESOURCES", 101, 7, 10), ("RESEARCH", 101, 1, 9)],
        )
        self.assertFalse(actions[2].executable_now)

    def test_same_chain_and_non_build_steps_are_never_fills(self) -> None:
        plan = {
            "steps": [
                _step(1, 101, "02:380:12", "b7", 10),
                _step(2, 101, "02:380:12", "b7", 11),
                {**_step(3, 101, "02:380:12", "b3", 10), "kind": "DEMOLISH"},
                {**_step(4, 101, "02:380:12", "b1", 10), "kind": "DEMOLISH_BUILDING"},
                _step(5, 101, "02:380:12", "b3", 11),  # not the next level of its chain
            ]
        }
        actions = self._reconcile(_game([_x(self.WAITING_FOR_METAL)]), plan)
        self.assertEqual(self._kinds(actions), [("WAIT_RESOURCES", 101, 7, 10)])

    def test_patience_comes_from_the_plan_and_is_bounded(self) -> None:
        self.assertEqual(ap._plan_fill_patience({}), ap.AI_FILL_MIN_PATIENCE)
        self.assertEqual(ap._plan_fill_patience({"search": {"tuned_genes": {"patience": 0.05}}}), ap.AI_FILL_MIN_PATIENCE)
        self.assertAlmostEqual(ap._plan_fill_patience({"search": {"tuned_genes": {"patience": 0.6}}}), 0.6)
        self.assertEqual(ap._plan_fill_patience({"search": {"tuned_genes": {"patience": 1.0}}}), ap.AI_FILL_MAX_PATIENCE)
        self.assertAlmostEqual(ap._plan_fill_patience({"meta_parameters": {"patience": 0.4}}), 0.4)


def _wait(pid: int, coords: str, seq: int, *, cost: int, missing: int) -> PlannedAction:
    return PlannedAction(
        priority="P1",
        kind="WAIT_RESOURCES",
        planet_id=pid,
        planet_coords=coords,
        target_id=7,
        target_name="Development center",
        target_level=10,
        cost=Resources(0, cost, 0, 0),
        missing=Resources(0, missing, 0, 0),
        reason=f"AI-Planner #{seq}: build Development center L10",
        executable_now=False,
    )


def _need(metal: int, **extra) -> dict[str, object]:
    return {
        "pig_iron": 0,
        "metal": metal,
        "kryptonite": 0,
        "spice": 0,
        "label": "Development center L10",
        "need_by_sec": 0.0,
        "lead_sec": 600.0,
        "storage_cap_ratio": 0.85,
        **extra,
    }


class TestPlanOrderLoans(unittest.TestCase):
    """A colony saving for a later plan step lends its savings to a waiting step that comes earlier."""

    def setUp(self) -> None:
        self.cfg = _adaptive_cfg(True, allow_transport=True, allow_multi_planet=True)
        self.cfg_off = _adaptive_cfg(False, allow_transport=True, allow_multi_planet=True)
        self.borrower = _colony(901, "02:380:04", metal=50_000)
        self.lender = _colony(902, "02:380:08", metal=300_000, ships={"s2": 10})
        self.plan = [
            _wait(901, "02:380:04", 10, cost=150_000, missing=100_000),
            _wait(902, "02:380:08", 50, cost=400_000, missing=100_000),
        ]
        self.needs = {901: _need(100_000), 902: _need(100_000)}

    def _transports(self, planets, plan=None, needs=None, cfg=None):
        return plan_interplanet_transports(
            _state(tuple(planets)), cfg or self.cfg, plan or self.plan, priority_resource_need=needs or self.needs
        )

    def test_later_step_lends_its_savings_to_an_earlier_waiting_step(self) -> None:
        # Switch off: a colony that needs Metal itself gives none away.
        self.assertEqual(self._transports([self.borrower, self.lender], cfg=self.cfg_off), [])
        acts = self._transports([self.borrower, self.lender])
        self.assertEqual(len(acts), 1)
        act = acts[0]
        self.assertEqual((act.kind, act.priority, act.planet_id, act.target_id), ("TRANSPORT", "P1", 902, 901))
        # Exactly what the earlier step lacks is lent; the rest of the cargo is the lender's spare stock.
        self.assertEqual(act.cost.metal, 100_000)
        self.assertTrue(act.executable_now)
        self.assertIn(AI_PLAN_ORDER_LOAN_TAG, act.reason)
        self.assertIn("lends 100,000 held for its own step #50 to step #10", act.reason)

    def test_no_loan_towards_a_later_step(self) -> None:
        plan = [
            _wait(901, "02:380:04", 50, cost=150_000, missing=100_000),
            _wait(902, "02:380:08", 10, cost=400_000, missing=100_000),
        ]
        self.assertEqual(self._transports([self.borrower, self.lender], plan=plan), [])

    def test_spare_stock_is_used_before_a_loan(self) -> None:
        spare = _colony(903, "02:380:09", metal=200_000, ships={"s2": 10})
        acts = self._transports([self.borrower, self.lender, spare])
        # The colony with spare Metal pays the earlier step in full, then helps the later one; nothing is lent.
        self.assertEqual(
            [(a.planet_id, a.target_id, a.cost.metal) for a in acts], [(903, 901, 100_000), (903, 902, 95_000)]
        )
        self.assertFalse(any(AI_PLAN_ORDER_LOAN_TAG in a.reason for a in acts))
        # Spare stock that covers only part of the need loses to a loan that covers all of it.
        thin = replace(spare, resources=replace(spare.resources, metal=40_000))
        acts_thin = self._transports([self.borrower, self.lender, thin])
        self.assertEqual((acts_thin[0].planet_id, acts_thin[0].target_id, acts_thin[0].cost.metal), (902, 901, 100_000))
        self.assertIn(AI_PLAN_ORDER_LOAN_TAG, acts_thin[0].reason)
        self.assertFalse(any(AI_PLAN_ORDER_LOAN_TAG in a.reason for a in acts_thin[1:]))

    def test_colony_that_can_pay_for_its_own_step_does_not_lend(self) -> None:
        # Cargo already flying in covers the lender's own step: it is about to start.
        self.assertEqual(self._transports([self.borrower, self.lender], needs={901: _need(100_000)}), [])
        # The lender's own step is ready to start.
        ready = replace(self.plan[1], kind="BUILD", executable_now=True, missing=Resources())
        self.assertEqual(self._transports([self.borrower, self.lender], plan=[self.plan[0], ready]), [])

    def test_loan_keeps_the_lenders_other_reservations(self) -> None:
        fill = PlannedAction(
            priority="P1",
            kind="BUILD",
            planet_id=902,
            planet_coords="02:380:08",
            target_id=3,
            target_name="Kryptonite mine",
            target_level=10,
            cost=Resources(0, 240_000, 0, 0),
            missing=Resources(),
            reason="AI-Planner fill #60: build Kryptonite mine L10 while Development center L10 waits for Metal",
            executable_now=True,
        )
        acts = self._transports([self.borrower, self.lender], plan=[*self.plan, fill])
        # 300,000 Metal - 240,000 for the ready step - 5,000 cushion = 55,000 to lend.
        self.assertEqual([(a.planet_id, a.target_id, a.cost.metal) for a in acts], [(902, 901, 55_000)])

    def test_small_loans_are_not_flown(self) -> None:
        def lender_with(metal: int) -> PlanetState:
            return replace(self.lender, resources=replace(self.lender.resources, metal=metal))

        # 20,000 Metal leaves 18,000 to lend after the 10% cushion: under PLAN_ORDER_LOAN_MIN_CARGO.
        poor = lender_with(PLAN_ORDER_LOAN_MIN_CARGO)
        self.assertEqual(self._transports([self.borrower, poor]), [])
        # 24,300 to lend is above the cargo floor but under a quarter of the 100,000 the step lacks.
        self.assertLess(24_300, PLAN_ORDER_LOAN_MIN_SHARE * 100_000)
        self.assertEqual(self._transports([self.borrower, lender_with(27_000)]), [])
        acts = self._transports([self.borrower, lender_with(28_000)])
        self.assertEqual([(a.planet_id, a.target_id, a.cost.metal) for a in acts], [(902, 901, 25_200)])
        # A small loan that pays for the whole need is still flown.
        plan = [_wait(901, "02:380:04", 10, cost=60_000, missing=10_000), self.plan[1]]
        needs = {901: _need(10_000), 902: _need(100_000)}
        acts = self._transports([self.borrower, poor], plan=plan, needs=needs)
        self.assertEqual([(a.planet_id, a.target_id, a.cost.metal) for a in acts], [(902, 901, 10_000)])

    def test_loan_covers_only_the_borrowers_own_waiting_step(self) -> None:
        # The colony's need also contains 80,000 Metal for research; only the building step may borrow.
        needs = {901: _need(180_000), 902: _need(100_000)}
        acts = self._transports([self.borrower, self.lender], needs=needs)
        self.assertEqual([(a.planet_id, a.target_id, a.cost.metal) for a in acts], [(902, 901, 100_000)])
        # A need that is not due yet (pre-ship behind a running build) does not borrow.
        later = {901: _need(100_000, need_by_sec=900.0), 902: _need(100_000)}
        self.assertEqual(self._transports([self.borrower, self.lender], needs=later), [])

    def test_needs_are_served_in_plan_order(self) -> None:
        second = _colony(903, "02:380:09", metal=50_000)
        plan = [
            _wait(903, "02:380:09", 20, cost=150_000, missing=100_000),
            _wait(901, "02:380:04", 10, cost=150_000, missing=100_000),
            _wait(902, "02:380:08", 50, cost=400_000, missing=100_000),
        ]
        needs = {901: _need(100_000), 902: _need(100_000), 903: _need(100_000)}
        lender = replace(self.lender, resources=replace(self.lender.resources, metal=150_000))
        acts = self._transports([self.borrower, lender, second], plan=plan, needs=needs)
        # 150,000 - 5,000 cushion: step #10 is paid in full first, step #20 gets the rest.
        self.assertEqual(
            [(a.planet_id, a.target_id, a.cost.metal) for a in acts], [(902, 901, 100_000), (902, 903, 45_000)]
        )


class TestLogisticsTargetIgnoresRaidingHulls(unittest.TestCase):
    """Hulls away on a raid do not count toward the plan-sized logistics fleet; the whole fleet has a ceiling."""

    # 120,000 cargo target: ceil(270,000 / 2.5 / 0.9).
    DEMAND = {"cargo_per_hour_24h": 270_000.0, "round_trips_per_hour": 2.5, "realized_utilization": 0.9}

    def _targets(self, queues=(), parked_s2: int = 0, cfg=None):
        yard = _colony(801, "02:381:01", b2=0, buildings={"b9": 6}, ships={"s2": parked_s2}, metal=200_000)
        st = _state((yard,), queues=tuple(queues), researches={"r4": 6})
        return compute_infra_transport_targets(st, cfg or _adaptive_cfg(True), self.DEMAND)

    @staticmethod
    def _flight(name: str, detail: str) -> QueueJob:
        return QueueJob(category="fleet", name=name, remaining_seconds=600.0, origin_coords="02:381:01", detail=detail)

    def test_hulls_on_a_transport_flight_count_and_hulls_on_a_raid_do_not(self) -> None:
        self.assertEqual(INFRA_TRANSPORT_FLEET_CAP_RATIO, 2.0)
        hauling = self._flight("Transport → [02:390:01] Colony", "Outbound · 4 Large transport")
        self.assertEqual(self._targets(queues=(hauling,)), {})
        # The same four hulls on a raid, going out or coming back, leave the logistics fleet empty:
        # four more are ordered (the colony's target counts the four it owns in flight).
        for raid in (
            self._flight("Attack → [02:340:02] Spacia Planet", "Outbound · 4 Large transport, 6 Small fighter"),
            self._flight("Attack ↩ [02:340:02] Spacia Planet", "Returning · 4 Large transport, 6 Small fighter"),
        ):
            self.assertEqual(self._targets(queues=(raid,)), {801: {"large-transport": 8}})
        # Switch off: no plan-sized fleet at all.
        self.assertEqual(self._targets(queues=(hauling,), cfg=_adaptive_cfg(False)), {})

    def test_whole_fleet_stops_at_twice_the_target(self) -> None:
        # Six hulls raiding (180,000 cargo): two more reach the 240,000 ceiling, not the four the target lacks.
        raid6 = self._flight("Attack → [02:340:02] Spacia Planet", "Outbound · 6 Large transport")
        self.assertEqual(self._targets(queues=(raid6,)), {801: {"large-transport": 8}})
        # Eight hulls raiding: the fleet is at the ceiling, nothing is ordered however many are away.
        raid8 = self._flight("Attack → [02:340:02] Spacia Planet", "Outbound · 8 Large transport")
        self.assertEqual(self._targets(queues=(raid8,)), {})
        # Hulls at home count toward the target as before.
        self.assertEqual(self._targets(parked_s2=4), {})
        self.assertEqual(self._targets(parked_s2=2), {801: {"large-transport": 4}})


if __name__ == "__main__":
    unittest.main()
