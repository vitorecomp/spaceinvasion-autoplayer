"""Tests for the adaptive-execution follow-up (Plan v3): realized raid income, hourly ship budgets,
batched plan-sized transport orders, and the fleet-sizing utilization floor.

Every behaviour here is gated by `ai_adaptive_execution`; the switch-off assertions pin the old path.
"""

from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.db.config_store import save_strategy_config
from spaceinvasion.core.model import (
    GameState,
    QueueJob,
    Resources,
    UnitOffer,
)
from spaceinvasion.planner import ai as ap
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.planner.heuristics import (
    AI_INFRA_CARGO_TAG,
    INFRA_TRANSPORT_MAX_BATCH,
    INFRA_TRANSPORT_MIN_UTILIZATION,
    compute_infra_transport_targets,
    compute_spare_ship_budget,
    filter_shipyard_actions_by_spare_budget,
    plan_fleet_and_attack_actions,
)
from spaceinvasion.planner.heuristics._common import _ship_offer_map
from spaceinvasion.planner.heuristics._shipyard_targets import _plan_planet_shipyard_targets
from spaceinvasion.runtime.bridge import GameApiBridge
from tests.unit.test_ai_planner import _make_planet, _sample_target_config, _target_planet_entry
from tests.unit.test_strategy import _adaptive_cfg, _colony, _state

S2_COST = Resources(0, 8_000, 6_000, 0)
S4_COST = Resources(0, 4_000, 2_000, 100)
S2_OFFER = UnitOffer("ship", 2, "Large transport", 0, S2_COST, True, "ship_2")
S4_OFFER = UnitOffer("ship", 4, "Small fighter", 0, S4_COST, True, "ship_4")

RAIDS = [
    {
        "target_coords": "02:380:15",
        "attacked_ts": 1_700_000_000.0 + 1000.0 * i,
        "outcome": "win",
        "loot_json": json.dumps({"pig_iron": 12_000, "metal": 5_000, "kryptonite": 6_000, "spice": 2_000}),
        "round_trip_sec": 1800.0,
    }
    for i in range(3)
]
REALIZED = {"pig_iron": 800.0, "metal": 1_600.0, "kryptonite": 2_400.0, "spice": 400.0}


def _yard(pid: int, coords: str, *, metal: int, kryptonite: int = 500_000, ships=None, offers=(S2_OFFER,)):
    base = _colony(pid, coords, b2=0, buildings={"b9": 6}, ships=ships or {}, metal=metal)
    return replace(
        base,
        resources=replace(base.resources, kryptonite=kryptonite),
        ship_offers=tuple(offers),
    )


def _s2_action(qty: int, *, tagged: bool = True, cost: Resources = S2_COST) -> PlannedAction:
    return PlannedAction(
        priority="P2",
        kind="BUILD_SHIP",
        planet_id=801,
        planet_coords="02:381:01",
        target_id=2,
        target_name="Large transport",
        target_level=qty,
        cost=cost,
        executable_now=True,
        reason="Fleet build target (0/40 Large transport)" + (f" {AI_INFRA_CARGO_TAG}" if tagged else ""),
    )


class TestRealizedRaidIncome(unittest.TestCase):
    """Raid income in telemetry and in the planner comes from loot actually received when asked."""

    def _state(self) -> GameState:
        p1 = _make_planet(101, "02:380:12", "Homeworld", buildings={"b1": 8, "b10": 4})
        p2 = _make_planet(102, "02:380:13", "Colony", buildings={"b1": 6, "b10": 2})
        return GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1, p2),
            active_planet_id=101,
            speedup_factor=5.0,
        )

    def test_default_telemetry_is_unchanged_and_realized_replaces_the_model(self) -> None:
        state = self._state()
        modelled = ap.compute_attack_telemetry(RAIDS, None, state=state)
        self.assertNotIn("income_basis", modelled)
        self.assertNotIn("modelled_hourly_loot", modelled)
        # 8 slots x 2 attacks per slot-hour x 5,000 Metal.
        self.assertEqual(modelled["current_fleet_slots"], 8)
        self.assertAlmostEqual(modelled["hourly_loot"]["metal"], 80_000.0, places=2)
        self.assertFalse(ap.is_realized_income_basis(modelled))

        real = ap.compute_attack_telemetry(
            RAIDS, None, state=state, realized_hourly_loot=REALIZED, realized_missions_per_hour=3.5
        )
        self.assertEqual(real["income_basis"], ap.REALIZED_INCOME_BASIS)
        self.assertTrue(ap.is_realized_income_basis(real))
        for rk, val in REALIZED.items():
            self.assertAlmostEqual(real["hourly_loot"][rk], val, places=2)
            self.assertAlmostEqual(real["income_per_slot_per_hour"][rk], val / 8.0, places=2)
        self.assertEqual(real["modelled_hourly_loot"], modelled["hourly_loot"])
        self.assertAlmostEqual(real["estimated_attacks_per_hour"], 3.5, places=4)
        self.assertAlmostEqual(real["attacks_per_slot_per_hour"], 3.5 / 8.0, places=4)
        # What the raids themselves looked like is still reported.
        self.assertEqual(real["median_loot"], modelled["median_loot"])
        self.assertEqual(real["median_round_trip_seconds"], modelled["median_round_trip_seconds"])
        # A realized figure of zero is a valid measurement, not "missing".
        zero = ap.compute_attack_telemetry(RAIDS, None, state=state, realized_hourly_loot={})
        self.assertEqual(zero["hourly_loot"]["metal"], 0.0)
        self.assertEqual(zero["income_basis"], ap.REALIZED_INCOME_BASIS)
        # The API bridge names the basis the same way.
        self.assertEqual(GameApiBridge.REALIZED_INCOME_BASIS, ap.REALIZED_INCOME_BASIS)

    def test_planner_uses_realized_income_per_slot(self) -> None:
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={
                "b1": 6,
                "b2": 3,
                "b3": 5,
                "b4": 3,
                "b5": 8,
                "b7": 4,
                "b8": 3,
                "b10": 1,
                "b11": 8,
                "b12": 8,
                "b13": 8,
                "b14": 8,
            },
            resources=Resources(0, 0, 0, 0),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1,),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    {
                        "b1": 22,
                        "b2": 16,
                        "b3": 20,
                        "b4": 16,
                        "b5": 24,
                        "b7": 8,
                        "b8": 4,
                        "b10": 4,
                        "b11": 8,
                        "b12": 8,
                        "b13": 8,
                        "b14": 8,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
            ],
            planned_research={"r1": 6, "r2": 6},
        )
        params = {"reward_weeks": 3, "ga_generations": 0, "pad_generic_colonies": False}
        modelled_tel = ap.compute_attack_telemetry(RAIDS, None, state=state)
        realized_tel = ap.compute_attack_telemetry(
            RAIDS, None, state=state, realized_hourly_loot=REALIZED, realized_missions_per_hour=1.0
        )
        slots = modelled_tel["current_fleet_slots"]
        self.assertEqual(slots, 2)

        plan_m = ap.run_ai_planner(state, target, attack_telemetry=modelled_tel, params=params)
        plan_r = ap.run_ai_planner(state, target, attack_telemetry=realized_tel, params=params)

        # Modelled path: unchanged labels and no basis key in the summary.
        self.assertEqual(plan_m["attack_telemetry"]["pace_set_by"], "Fleet base (b10) slots x median round-trip")
        self.assertNotIn("attack_income_basis", plan_m["summary"])
        self.assertAlmostEqual(plan_m["attack_telemetry"]["hourly_loot"]["metal"], slots * 2.0 * 5_000.0, places=2)

        # Realized path: the simulator starts from exactly the realized hourly loot.
        tel_r = plan_r["attack_telemetry"]
        self.assertEqual(tel_r["income_basis"], ap.REALIZED_INCOME_BASIS)
        self.assertIn("realized loot and recycling", tel_r["pace_set_by"])
        self.assertEqual(plan_r["summary"]["attack_income_basis"], ap.REALIZED_INCOME_BASIS)
        for rk, val in REALIZED.items():
            self.assertAlmostEqual(tel_r["hourly_loot"][rk], val, places=1)
            self.assertAlmostEqual(tel_r["income_per_slot_per_hour"][rk], val / slots, places=2)
        self.assertAlmostEqual(tel_r["estimated_attacks_per_hour"], 1.0, places=4)
        self.assertAlmostEqual(plan_r["summary"]["attacks_per_slot_per_hour"], 0.5, places=4)
        # Twenty times less loot means the plan can afford less.
        self.assertLess(plan_r["summary"]["discounted_reward"], plan_m["summary"]["discounted_reward"])
        self.assertLess(
            sum(plan_r["summary"]["loot_counted"].values()), sum(plan_m["summary"]["loot_counted"].values())
        )

        # `attacks_per_day` still overrides either basis, and the stored telemetry says so.
        plan_fixed = ap.run_ai_planner(
            state, target, attack_telemetry=realized_tel, params={**params, "attacks_per_day": 24.0}
        )
        self.assertEqual(plan_fixed["attack_telemetry"]["pace_set_by"], "attacks_per_day")
        self.assertEqual(plan_fixed["attack_telemetry"]["income_basis"], "attacks_per_day")
        self.assertNotIn("attack_income_basis", plan_fixed["summary"])
        self.assertAlmostEqual(plan_fixed["attack_telemetry"]["hourly_loot"]["metal"], 5_000.0, places=2)
        # Without a realized figure on offer the stored telemetry gains no basis key at all.
        plan_fixed_m = ap.run_ai_planner(
            state, target, attack_telemetry=modelled_tel, params={**params, "attacks_per_day": 24.0}
        )
        self.assertNotIn("income_basis", plan_fixed_m["attack_telemetry"])


class TestHourlyShipBudgets(unittest.TestCase):
    """`spare %` is an hourly cap under adaptive execution, and batches are costed per unit x quantity."""

    def _state(self):
        return _state((_yard(801, "02:381:01", metal=400_000),), researches={"r4": 6})

    def test_spare_spend_ledger_only_applies_with_the_switch_on(self) -> None:
        st = self._state()
        tel = {"hourly_loot": {"pig_iron": 0.0, "metal": 100_000.0, "kryptonite": 100_000.0, "spice": 0.0}}
        spent = {"metal": 4_000.0, "kryptonite": 50_000.0}

        base = compute_spare_ship_budget(st, 10.0, attack_telemetry=tel)
        rate_metal = base["hourly_spare_allowance"]["metal"]
        rate_kry = base["hourly_spare_allowance"]["kryptonite"]
        self.assertGreater(rate_metal, 4_000.0)
        self.assertLess(rate_kry, 50_000.0)
        self.assertAlmostEqual(base["available_budget"]["metal"], rate_metal, places=2)  # one hour, per call
        self.assertNotIn("spare_spend_in_window", base)
        self.assertNotIn("spare_window_hours", base)
        self.assertNotIn("income_basis", base)

        # Switch off: a ledger passed by mistake changes nothing.
        off = compute_spare_ship_budget(
            st, 10.0, attack_telemetry=tel, spare_spend_in_window=spent, spare_window_hours=4.0
        )
        self.assertEqual(off["available_budget"], base["available_budget"])
        self.assertNotIn("spare_spend_in_window", off)

        # Switch on, one-hour window: what was already spent this hour is gone.
        on = compute_spare_ship_budget(
            st, 10.0, attack_telemetry=tel, adaptive_execution=True, spare_spend_in_window=spent
        )
        self.assertAlmostEqual(on["available_budget"]["metal"], rate_metal - 4_000.0, places=2)
        # Spending past the allowance leaves nothing, never a negative budget.
        self.assertEqual(on["available_budget"]["kryptonite"], 0.0)
        self.assertEqual(on["spare_spend_in_window"]["metal"], 4_000.0)
        self.assertEqual(on["spare_window_hours"], 1.0)
        self.assertEqual(on["available_budget"]["pig_iron"], base["available_budget"]["pig_iron"])
        # The 25% infra allowance has its own ledger and is untouched by spare spending.
        self.assertEqual(on["infra_transport_budget"], base["infra_transport_budget"])
        self.assertEqual(on["hourly_spare_allowance"], base["hourly_spare_allowance"])

        # A four-hour window lets the hourly rate save up, so a hull dearer than one hour's
        # allowance stays affordable, while the long-run rate is unchanged.
        on4 = compute_spare_ship_budget(
            st,
            10.0,
            attack_telemetry=tel,
            adaptive_execution=True,
            spare_spend_in_window=spent,
            spare_window_hours=4.0,
        )
        self.assertAlmostEqual(on4["available_budget"]["metal"], 4.0 * rate_metal - 4_000.0, places=2)
        self.assertAlmostEqual(on4["available_budget"]["kryptonite"], max(0.0, 4.0 * rate_kry - 50_000.0), places=2)
        self.assertEqual(on4["spare_window_hours"], 4.0)
        self.assertEqual(on4["infra_transport_budget"], base["infra_transport_budget"])

        via_cfg = compute_spare_ship_budget(
            st, 10.0, attack_telemetry=tel, config=_adaptive_cfg(True), spare_spend_in_window=spent
        )
        self.assertEqual(via_cfg["available_budget"], on["available_budget"])

        # The raid-income basis of the telemetry is echoed for the operator.
        echoed = compute_spare_ship_budget(st, 10.0, attack_telemetry={**tel, "income_basis": "realized_24h"})
        self.assertEqual(echoed["income_basis"], "realized_24h")

    def test_filter_shrinks_an_infra_batch_to_the_allowance(self) -> None:
        st = self._state()
        budget = {
            "available_budget": {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
            "free_after_reserve": {"pig_iron": 0.0, "metal": 500_000.0, "kryptonite": 500_000.0, "spice": 0.0},
            "infra_transport_budget": {"metal": 30_000.0, "kryptonite": 100_000.0},
            "urgent_replacements": {"s10": False, "s1": False, "s2": False},
        }
        # 30,000 Metal pays for 3 of the 10 hulls asked for; the second batch finds 6,000 left: none.
        out = filter_shipyard_actions_by_spare_budget(st, [_s2_action(10), _s2_action(10)], budget)
        self.assertTrue(out[0].executable_now)
        self.assertEqual(out[0].target_level, 3)
        self.assertEqual(out[0].cost, S2_COST)  # still the cost of one unit
        self.assertFalse(out[1].executable_now)
        self.assertIn("25%/h infra transport cap", out[1].reason)

        # Kryptonite can be the binding resource too.
        tight_k = {**budget, "infra_transport_budget": {"metal": 500_000.0, "kryptonite": 13_000.0}}
        self.assertEqual(filter_shipyard_actions_by_spare_budget(st, [_s2_action(10)], tight_k)[0].target_level, 2)

        # Free stock above the reserved AI step bounds the batch as well.
        tight_free = {
            **budget,
            "infra_transport_budget": {"metal": 500_000.0, "kryptonite": 500_000.0},
            "free_after_reserve": {"pig_iron": 0.0, "metal": 41_000.0, "kryptonite": 500_000.0, "spice": 0.0},
        }
        self.assertEqual(filter_shipyard_actions_by_spare_budget(st, [_s2_action(10)], tight_free)[0].target_level, 5)

        # A batch that fits is left alone.
        roomy = {**budget, "infra_transport_budget": {"metal": 500_000.0, "kryptonite": 500_000.0}}
        fits = filter_shipyard_actions_by_spare_budget(st, [_s2_action(4)], roomy)[0]
        self.assertTrue(fits.executable_now)
        self.assertEqual(fits.target_level, 4)

    def test_filter_costs_every_ship_order_per_unit_times_quantity(self) -> None:
        st = self._state()
        budget = {
            "available_budget": {"pig_iron": 0.0, "metal": 20_000.0, "kryptonite": 20_000.0, "spice": 0.0},
            "free_after_reserve": {"pig_iron": 0.0, "metal": 500_000.0, "kryptonite": 500_000.0, "spice": 0.0},
            "urgent_replacements": {"s10": False, "s1": False, "s2": False},
        }
        # Three Large transports from the spare budget cost 24,000 Metal, more than the 20,000 left.
        three = filter_shipyard_actions_by_spare_budget(st, [_s2_action(3, tagged=False)], budget)[0]
        self.assertFalse(three.executable_now)
        self.assertIn("spare % ship budget", three.reason)
        two = filter_shipyard_actions_by_spare_budget(st, [_s2_action(2, tagged=False)], budget)[0]
        self.assertTrue(two.executable_now)
        # A zero-cost action still falls back to the catalog cost times quantity.
        cat3 = filter_shipyard_actions_by_spare_budget(st, [_s2_action(3, tagged=False, cost=Resources())], budget)[0]
        self.assertFalse(cat3.executable_now)


class TestBatchedInfraTransportOrders(unittest.TestCase):
    """Plan-sized transports are ordered in one batch, first among P2 actions, sized without inflation."""

    DEMAND = {"cargo_per_hour_24h": 858_000.0, "round_trips_per_hour": 2.8, "realized_utilization": 0.5}
    BASE = dict(allow_shipyard=True, allow_multi_planet=True, shipyard_targets={"large-transport": 0})

    def _ship_acts(self, planet, st, cfg, demand=None):
        return _plan_planet_shipyard_targets(
            st, planet, _ship_offer_map(planet), cfg, transport_demand=demand or self.DEMAND
        )

    def test_one_order_for_the_affordable_batch(self) -> None:
        cfg = _adaptive_cfg(True, **self.BASE)

        # Stock for 3 hulls (25,000 Metal): the order asks for 3, not 1.
        poor = _yard(801, "02:381:01", metal=25_000)
        acts = self._ship_acts(poor, _state((poor,), researches={"r4": 6}), cfg)
        self.assertEqual(len(acts), 1)
        self.assertIn(AI_INFRA_CARGO_TAG, acts[0].reason)
        self.assertTrue(acts[0].executable_now)
        self.assertEqual(acts[0].target_level, 3)
        self.assertEqual(acts[0].cost, S2_COST)

        # Plenty of stock: capped at the batch size.
        rich = _yard(801, "02:381:01", metal=400_000)
        acts_rich = self._ship_acts(rich, _state((rich,), researches={"r4": 6}), cfg)
        self.assertEqual(acts_rich[0].target_level, INFRA_TRANSPORT_MAX_BATCH)

        # Fewer hulls missing than the batch size: order only what is missing.
        small = {"cargo_per_hour_24h": 100_000.0, "round_trips_per_hour": 2.5, "realized_utilization": 0.5}
        need = math.ceil(math.ceil(100_000.0 / 2.5 / 0.5) / 30_000)
        self.assertEqual(need, 3)
        acts_small = self._ship_acts(rich, _state((rich,), researches={"r4": 6}), cfg, demand=small)
        self.assertEqual(acts_small[0].target_level, 3)

        # Not affordable: listed as waiting with quantity 1, never executable.
        broke = _yard(801, "02:381:01", metal=1_000)
        acts_broke = self._ship_acts(broke, _state((broke,), researches={"r4": 6}), cfg)
        self.assertFalse(acts_broke[0].executable_now)
        self.assertEqual(acts_broke[0].target_level, 1)

        # Hulls configured by the operator (not plan-sized) keep one unit per order.
        fighters = _yard(801, "02:381:01", metal=400_000, offers=(S4_OFFER,))
        cfg_f = _adaptive_cfg(
            True, allow_shipyard=True, allow_multi_planet=True, shipyard_targets={"small-fighter": 12}
        )
        acts_f = _plan_planet_shipyard_targets(
            _state((fighters,), researches={"r4": 6}), fighters, _ship_offer_map(fighters), cfg_f
        )
        self.assertEqual([a.target_level for a in acts_f], [1])
        self.assertNotIn(AI_INFRA_CARGO_TAG, acts_f[0].reason)

    def test_utilization_floor_and_recent_orders_shape_the_target(self) -> None:
        self.assertEqual(INFRA_TRANSPORT_MIN_UTILIZATION, 0.5)
        planets = tuple(_yard(801 + i, f"02:381:{i + 1:02d}", metal=200_000) for i in range(2))
        st = _state(planets, researches={"r4": 6})
        cfg = _adaptive_cfg(True, **self.BASE)

        at_half = compute_infra_transport_targets(st, cfg, self.DEMAND)
        total_half = sum(t["large-transport"] for t in at_half.values())
        self.assertEqual(total_half, math.ceil(math.ceil(858_000.0 / 2.8 / 0.5) / 30_000))

        # A fleet measured as mostly idle (0.2) no longer inflates the target 2.5 times.
        idle = compute_infra_transport_targets(st, cfg, {**self.DEMAND, "realized_utilization": 0.2})
        self.assertEqual(idle, at_half)
        # A fleet measured as busy still shrinks it.
        busy = compute_infra_transport_targets(st, cfg, {**self.DEMAND, "realized_utilization": 0.9})
        self.assertLess(sum(t["large-transport"] for t in busy.values()), total_half)

        # Hulls ordered in the last hour count before they leave the Weapons factory.
        pending = compute_infra_transport_targets(st, cfg, {**self.DEMAND, "infra_cargo_ordered_last_hour": 300_000})
        self.assertEqual(sum(t["large-transport"] for t in pending.values()), total_half - 10)
        covered = compute_infra_transport_targets(st, cfg, {**self.DEMAND, "infra_cargo_ordered_last_hour": 10_000_000})
        self.assertEqual(covered, {})

    def test_infra_orders_rank_first_among_p2_and_switch_off_is_unchanged(self) -> None:
        yard = _yard(801, "02:381:01", metal=400_000, offers=(S4_OFFER, S2_OFFER))
        st = _state((yard,), researches={"r4": 6})
        base = dict(
            allow_shipyard=True, allow_multi_planet=True, shipyard_targets={"small-fighter": 12, "large-transport": 0}
        )
        demand = {"cargo_per_hour_24h": 300_000.0, "round_trips_per_hour": 2.5, "realized_utilization": 0.5}

        on = plan_fleet_and_attack_actions(st, config=_adaptive_cfg(True, **base), transport_demand=demand)
        ships = [a for a in on if a.kind == "BUILD_SHIP" and a.executable_now]
        self.assertEqual([a.target_name for a in ships], ["Large transport", "Small fighter"])
        self.assertIn(AI_INFRA_CARGO_TAG, ships[0].reason)
        self.assertGreater(ships[0].target_level, 1)
        self.assertEqual(ships[1].target_level, 1)
        first_p2_ready = next(a for a in on if a.priority == "P2" and a.executable_now)
        self.assertIs(first_p2_ready, ships[0])

        off = plan_fleet_and_attack_actions(st, config=_adaptive_cfg(False, **base), transport_demand=demand)
        baseline = plan_fleet_and_attack_actions(st, config=StrategyConfig(**base))
        self.assertFalse(any(AI_INFRA_CARGO_TAG in a.reason for a in off))
        sig = lambda acts: [(a.kind, a.planet_id, a.target_id, a.target_level, a.reason) for a in acts]  # noqa: E731
        self.assertEqual(sig(off), sig(baseline))


class TestBridgeLedgersAndRealizedLoot(unittest.TestCase):
    """API bridge: ship-spend ledgers, realized loot, and the one-off re-tune on a changed income basis."""

    def _bridge(self, tmp: str, *, adaptive: bool) -> GameApiBridge:
        tp = Path(tmp)
        cfg_path = tp / "config.json"
        if not cfg_path.exists():
            save_strategy_config(
                StrategyConfig(planner_mode="ai_planner", max_planets=2, ai_adaptive_execution=adaptive),
                cfg_path,
            )
        return GameApiBridge(
            client_factory=lambda _p: MagicMock(),
            config_path=cfg_path,
            db_path=tp / "state.db",
            log_dir=None,
        )

    def _unit_action(self, sid: int, name: str, cost: Resources, qty: int) -> PlannedAction:
        # Shape of the action `_find_ship_action` builds: per-unit cost, quantity in `target_level`.
        return PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=801,
            planet_coords="02:380:01",
            target_id=sid,
            target_name=name,
            target_level=qty,
            cost=cost,
            reason="API build_ship command",
            executable_now=True,
        )

    def test_ledgers_debit_whole_orders_expire_and_survive_a_restart(self) -> None:
        now_ts = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._bridge(tmp, adaptive=True)
            try:
                # Infra ledger: 3 Large transports at 8,000 / 6,000 each, 90,000 cargo on order.
                bridge._record_infra_transport_spend_locked(
                    self._unit_action(2, "Large transport", S2_COST, 3), now_ts=now_ts, quantity=3
                )
                spent, _carry = bridge._get_infra_transport_spend_last_hour_locked(now_ts=now_ts + 60.0)
                self.assertEqual(spent, {"metal": 24_000.0, "kryptonite": 18_000.0})
                self.assertEqual(bridge._get_infra_cargo_ordered_last_hour_locked(now_ts=now_ts + 60.0), 90_000)

                # Spare ledger: 2 Small fighters, then a hull priced from the catalog (zero cost given).
                bridge._record_spare_ship_spend_locked(
                    self._unit_action(4, "Small fighter", S4_COST, 2), now_ts=now_ts, quantity=2
                )
                bridge._record_spare_ship_spend_locked(
                    self._unit_action(6, "Starcruiser", Resources(), 1), now_ts=now_ts
                )
                spare = bridge._get_spare_ship_spend_in_window_locked(now_ts=now_ts + 60.0)
                self.assertEqual(spare["metal"], 2 * 4_000 + 25_000)
                self.assertEqual(spare["kryptonite"], 2 * 2_000 + 10_000)
                self.assertEqual(spare["spice"], 2 * 100 + 1_500)
                self.assertEqual(spare["pig_iron"], 0.0)

                bridge._persist_runtime_state()
            finally:
                bridge.close()

            bridge2 = self._bridge(tmp, adaptive=True)
            try:
                self.assertEqual(
                    bridge2._get_spare_ship_spend_in_window_locked(now_ts=now_ts + 120.0)["metal"], 33_000.0
                )
                self.assertEqual(bridge2._get_infra_cargo_ordered_last_hour_locked(now_ts=now_ts + 120.0), 90_000)
                # After an hour the infra ledger forgets the order; the spare ledger keeps it for
                # the whole saving-up window.
                self.assertEqual(bridge2._get_infra_cargo_ordered_last_hour_locked(now_ts=now_ts + 3_700.0), 0)
                self.assertEqual(
                    bridge2._get_spare_ship_spend_in_window_locked(now_ts=now_ts + 3_700.0)["metal"], 33_000.0
                )
                window_s = GameApiBridge.SPARE_SHIP_BUDGET_WINDOW_HOURS * 3600.0
                self.assertEqual(GameApiBridge.SPARE_SHIP_BUDGET_WINDOW_HOURS, 4.0)
                self.assertEqual(
                    bridge2._get_spare_ship_spend_in_window_locked(now_ts=now_ts + window_s + 100.0),
                    {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
                )
            finally:
                bridge2.close()

    def test_realized_loot_overlay_and_live_telemetry(self) -> None:
        summary = {
            "window_hours": 24,
            "attack_count": 4,
            "recycle_count": 80,
            "stolen_pig_iron": 24_000.0,
            "stolen_metal": 24_000.0,
            "stolen_kryptonite": 24_000.0,
            "stolen_spice": 24_000.0,
            "recycled_pig_iron": 720_000.0,
            "recycled_metal": 1_176_000.0,
            "recycled_kryptonite": 1_896_000.0,
            "recycled_spice": 696_000.0,
        }
        modelled_tel = {
            "median_loot": {"spice_eq": 2_907.0},
            "hourly_loot": {"pig_iron": 999_190.0, "metal": 989_394.0, "kryptonite": 999_190.0, "spice": 999_190.0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._bridge(tmp, adaptive=True)
            try:
                with patch.object(
                    bridge, "_get_loot_recycle_telemetry_locked", return_value={"history": [], "summary": summary}
                ):
                    realized = bridge._get_realized_loot_hourly_locked()
                    self.assertEqual(
                        realized["hourly_loot"],
                        {"pig_iron": 31_000.0, "metal": 50_000.0, "kryptonite": 80_000.0, "spice": 30_000.0},
                    )
                    self.assertAlmostEqual(realized["missions_per_hour"], 3.5, places=4)

                    over = bridge._overlay_realized_loot_locked(modelled_tel)
                    self.assertEqual(over["income_basis"], GameApiBridge.REALIZED_INCOME_BASIS)
                    self.assertEqual(over["hourly_loot"]["metal"], 50_000.0)
                    self.assertGreater(over["hourly_loot"]["spice_eq"], 0.0)
                    self.assertEqual(over["modelled_hourly_loot"], modelled_tel["hourly_loot"])
                    self.assertEqual(over["median_loot"], modelled_tel["median_loot"])
                    self.assertNotIn("income_basis", modelled_tel)  # the input is not mutated
                    # Overlaying twice keeps the first modelled figure.
                    again = bridge._overlay_realized_loot_locked(over)
                    self.assertEqual(again["modelled_hourly_loot"], modelled_tel["hourly_loot"])

                    # Live telemetry carries the realized basis only with the switch on.
                    live_on = bridge._compute_live_attack_telemetry_locked(None)
                    self.assertEqual(live_on["income_basis"], GameApiBridge.REALIZED_INCOME_BASIS)
                    self.assertEqual(live_on["hourly_loot"]["metal"], 50_000.0)
                    bridge.strategy_config = StrategyConfig(
                        planner_mode="ai_planner", max_planets=2, ai_adaptive_execution=False
                    )
                    live_off = bridge._compute_live_attack_telemetry_locked(None)
                    self.assertNotIn("income_basis", live_off)

                # A loot summary without per-resource totals (query failed) keeps the model in use.
                with patch.object(
                    bridge,
                    "_get_loot_recycle_telemetry_locked",
                    return_value={"history": [], "summary": {"total_stolen": 0.0, "window_hours": 24}},
                ):
                    self.assertIsNone(bridge._get_realized_loot_hourly_locked())
                    self.assertIs(bridge._overlay_realized_loot_locked(modelled_tel), modelled_tel)

                # No database: no realized figure.
                bridge._db.close()
                bridge._db = None
                self.assertIsNone(bridge._get_realized_loot_hourly_locked())
            finally:
                bridge.close()

    def test_drift_retunes_once_when_the_income_basis_changes(self) -> None:
        now_ts = time.time()
        p1 = _make_planet(801, "02:380:01", "Prime", buildings={"b1": 12, "b10": 4})
        p2 = _make_planet(802, "02:380:02", "Secundus", buildings={"b1": 10, "b10": 3})
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="Cmdr",
            timestamp=now_ts,
            planets=(p1, p2),
            active_planet_id=801,
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine",
                    origin_coords="02:380:01",
                    building_id=1,
                    target_level=13,
                    remaining_seconds=900.0,
                    finish_ts=now_ts + 900.0,
                ),
            ),
        )
        plan_res = {
            "schema_version": 1,
            "generated_at": "2026-11-19T00:00:00+00:00",
            "config_generated_at": "2026-11-19T00:00:00+00:00",
            "summary": {"colonies_founded": 2, "colonies_target": 2},
            "attack_telemetry": {"median_loot": {"spice_eq": 10_000.0}},  # modelled basis: no key
            "search": {"best_strategy": "tuned", "tuned_genes": {"patience": 0.42}},
            "steps": [],
        }
        live_realized = {"median_loot": {"spice_eq": 10_000.0}, "income_basis": GameApiBridge.REALIZED_INCOME_BASIS}
        calls: list[dict[str, object]] = []

        def _fake_start(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {"ok": True, "job": {"status": "running"}}

        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._bridge(tmp, adaptive=True)
            try:
                bridge._last_state = st
                bridge._last_snapshot_at = time.monotonic()
                bridge._active_config_opt_mem = {
                    "run_id": 10,
                    "updated_ts": now_ts - 18_000.0,
                    "result": {"generated_at": "2026-11-19T00:00:00+00:00"},
                }
                bridge._db.close()
                bridge._db = None
                bridge._active_ai_plan_mem = {
                    "run_id": 20,
                    "config_run_id": 10,
                    "updated_ts": now_ts - 3_600.0,  # 1 h old: past the cooldown, younger than the 4 h rule
                    "result": dict(plan_res),
                }
                with (
                    patch.object(bridge, "_start_ai_planner_job", side_effect=_fake_start),
                    patch.object(bridge, "_compute_live_attack_telemetry_locked", return_value=live_realized),
                ):
                    bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[-1]["trigger_source"], "auto_drift:income_basis_changed")
                    self.assertEqual(calls[-1]["params"], {"warm_start_genes": {"patience": 0.42}})

                    # Once the active plan was computed on the realized basis, nothing fires.
                    bridge._last_auto_ai_replan_ts = now_ts - 2_000.0
                    bridge._active_ai_plan_mem["result"] = {**plan_res, "attack_telemetry": dict(live_realized)}
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 1)

                    # A plan pinned to `attacks_per_day` is the operator's choice: never re-tuned for it.
                    bridge._active_ai_plan_mem["result"] = {
                        **plan_res,
                        "attack_telemetry": {"median_loot": {"spice_eq": 10_000.0}, "income_basis": "attacks_per_day"},
                    }
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 1)

                # A realized-basis plan does not depend on the median loot per raid, so a 30% shift
                # in that median no longer re-tunes it...
                shifted = {"median_loot": {"spice_eq": 13_000.0}, "income_basis": GameApiBridge.REALIZED_INCOME_BASIS}
                bridge._active_ai_plan_mem["result"] = {**plan_res, "attack_telemetry": dict(live_realized)}
                with (
                    patch.object(bridge, "_start_ai_planner_job", side_effect=_fake_start),
                    patch.object(bridge, "_compute_live_attack_telemetry_locked", return_value=shifted),
                ):
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                    self.assertEqual(len(calls), 1)
                    # ...while the 4-hour age rule still does.
                    bridge._active_ai_plan_mem["updated_ts"] = now_ts - 18_000.0
                    bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[-1]["trigger_source"], "auto_drift:plan_age_4h")
                bridge._active_ai_plan_mem["updated_ts"] = now_ts - 3_600.0
                bridge._last_auto_ai_replan_ts = now_ts - 2_000.0

                # Live telemetry still on the modelled basis (no loot ledger): nothing fires either.
                bridge._active_ai_plan_mem["result"] = dict(plan_res)
                with (
                    patch.object(bridge, "_start_ai_planner_job", side_effect=_fake_start),
                    patch.object(
                        bridge,
                        "_compute_live_attack_telemetry_locked",
                        return_value={"median_loot": {"spice_eq": 10_000.0}},
                    ),
                ):
                    self.assertIsNone(bridge._maybe_trigger_ai_drift_replan(now_ts=now_ts))
                self.assertEqual(len(calls), 2)
            finally:
                bridge.close()


if __name__ == "__main__":
    unittest.main()
