"""Core SpaceInvasion Classic combat, Escape flight, and Bash-rule invariants.

Tagged ``@pytest.mark.invariants``. These tests protect fundamental game-rule
contracts through public surfaces and must never be deleted during refactoring.
"""

from __future__ import annotations

import unittest

import pytest

from spaceinvasion.combat.cargo import plan_escape_flight
from spaceinvasion.combat.flight import BashTracker
from spaceinvasion.combat.simulator import simulate_raid_outcome
from spaceinvasion.parsing.fleet_page import parse_send_fleet_mission_block
from spaceinvasion.parsing.models import SpyReport
from spaceinvasion.combat.points import build_combat_overview
from spaceinvasion.combat.simulator import simulate_combat_matchup
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    Resources,
)
from spaceinvasion.planner.heuristics import plan_actions

pytestmark = pytest.mark.invariants


def _make_planet(
    *,
    planet_id: int = 101,
    coords: str = "02:380:12",
    ships: dict[str, int] | None = None,
    resources: Resources | None = None,
    buildings: dict[str, int] | None = None,
) -> PlanetState:
    return PlanetState(
        planet_id=planet_id,
        name="Homeworld",
        coords=coords,
        planet_type="normal",
        temperature=25,
        fields_used=20,
        fields_max=230,
        resources=resources or Resources(50_000, 40_000, 30_000, 10_000),
        storage=Resources(200_000, 200_000, 200_000, 200_000),
        energy=EnergyBalance(produced=400, used=200),
        buildings=buildings or {"b1": 5, "b5": 7, "b9": 4, "b10": 1},
        ships=dict(ships or {}),
    )


@pytest.mark.invariants
class TestCombatInvariants(unittest.TestCase):
    """SpaceInvasion Classic combat invariants: 1:1 zero-rapidfire, Escape flights, Bash rules."""

    def test_zero_rapidfire_1_to_1_targeting_and_1_4x_safety_threshold(self) -> None:
        report_small = SpyReport(
            target_coords="02:380:04",
            owner="Target",
            resources=Resources(20_000, 10_000, 5_000, 2_000),
            fleet={"s4": 10},
            defense={"d1": 10},
        )
        report_double = SpyReport(
            target_coords="02:380:04",
            owner="Target",
            resources=Resources(20_000, 10_000, 5_000, 2_000),
            fleet={"s4": 20},
            defense={"d1": 20},
        )

        # 1:1 targeting: doubling fleet or defender scales power strictly linearly (no rapidfire bonus).
        est_a = simulate_raid_outcome({"s6": 5}, report_small)
        est_b = simulate_raid_outcome({"s6": 10}, report_small)
        self.assertAlmostEqual(est_b.attacker_power, 2.0 * est_a.attacker_power, places=6)

        est_2d = simulate_raid_outcome({"s6": 5}, report_double)
        self.assertAlmostEqual(est_2d.defender_power, 2.0 * est_a.defender_power, places=6)

        # Mixed ship classes combine additively with zero cross-class rapidfire multiplier.
        est_s4 = simulate_raid_outcome({"s4": 10}, report_small)
        est_s6 = simulate_raid_outcome({"s6": 4}, report_small)
        est_mix = simulate_raid_outcome({"s4": 10, "s6": 4}, report_small)
        self.assertAlmostEqual(
            est_mix.attacker_power,
            est_s4.attacker_power + est_s6.attacker_power,
            places=6,
        )

        # Default safety margin strictly requires >= 1.4x power ratio.
        mirror_report = SpyReport(
            target_coords="02:380:04",
            owner="Mirror",
            resources=Resources(10_000, 10_000, 10_000, 1_000),
            fleet={"s4": 10},
        )
        est_unsafe = simulate_raid_outcome({"s4": 13}, mirror_report)
        self.assertLess(est_unsafe.power_ratio, 1.4)
        self.assertFalse(est_unsafe.safe_to_attack)

        est_safe = simulate_raid_outcome({"s4": 14}, mirror_report)
        self.assertGreaterEqual(est_safe.power_ratio, 1.4)
        self.assertTrue(est_safe.safe_to_attack)

        # Public ranking matchup helper preserves the same 1:1 zero-rapidfire & >=1.4x contract.
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Commander",
            timestamp=1_700_000_000.0,
            planets=(_make_planet(ships={"s4": 20}),),
            active_planet_id=101,
        )
        matchup = simulate_combat_matchup(
            state,
            attacker_ships={"s4": 14},
            defender_ships={"s4": 10},
            target_resources={"pig_iron": 10_000, "metal": 10_000, "kryptonite": 0, "spice": 0},
        )
        self.assertTrue(matchup["safe_to_attack"])
        self.assertAlmostEqual(matchup["win_ratio"], 1.4, places=6)

        overview = build_combat_overview(state)
        self.assertIn("1:1 zero-rapidfire", overview["combat_model"])

    def test_escape_flight_packing_priority_and_hostile_p0_dispatch_semantics(self) -> None:
        # Stationary Solar cells (s9) must be excluded from mobile Escape flights by default,
        # and cargo packs in value-density order: Spice (minus fuel reserve) -> Metal -> Kryptonite -> Pig iron.
        planet = _make_planet(
            planet_id=101,
            coords="02:380:12",
            ships={"s1": 1, "s9": 25},
            resources=Resources(pig_iron=8_000, metal=4_000, kryptonite=3_000, spice=1_500),
        )
        plan = plan_escape_flight(planet, target_coords="02:380:15", fuel_reserve_spice=500)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.ships, {"s1": 1})
        self.assertNotIn("s9", plan.ships)
        rem = plan.total_capacity - plan.fuel_reserved_spice
        self.assertGreater(rem, 1_000 + 4_000)
        self.assertLess(rem, 1_000 + 4_000 + 3_000)
        self.assertEqual(plan.cargo.spice, 1_000)
        self.assertEqual(plan.cargo.metal, 4_000)
        self.assertEqual(plan.cargo.kryptonite, rem - 5_000)
        self.assertEqual(plan.cargo.pig_iron, 0)

        # Without mobile cargo ships (only stationary s9), Escape flight is not feasible.
        s9_only = _make_planet(ships={"s9": 10})
        self.assertFalse(plan_escape_flight(s9_only, target_coords="02:380:15").feasible)

        # Under hostile alert, planner emits P0 ALERT_HOSTILE (advisory) + P0 ESCAPE_FLIGHT
        # (SpaceInvasion Classic Escape flights are invisible to Asteroid Galaxy scanners).
        colony = _make_planet(planet_id=102, coords="02:380:15", ships={})
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Commander",
            timestamp=1_700_000_000.0,
            planets=(planet, colony),
            active_planet_id=101,
            fleet_alerts=(FleetAlert(mission="Attack", summary="Incoming hostile attack in 00:12:00", hostile=True),),
        )
        actions_guarded = plan_actions(state, config=StrategyConfig(planner_mode="priority", allow_fleetsave=False))
        alert_act = next(a for a in actions_guarded if a.kind == "ALERT_HOSTILE")
        esc_guarded = next(a for a in actions_guarded if a.kind == "ESCAPE_FLIGHT")
        self.assertEqual(alert_act.priority, "P0")
        self.assertFalse(alert_act.executable_now)
        self.assertEqual(esc_guarded.priority, "P0")
        self.assertFalse(esc_guarded.executable_now)

        actions_live = plan_actions(state, config=StrategyConfig(planner_mode="priority", allow_fleetsave=True))
        esc_live = next(a for a in actions_live if a.kind == "ESCAPE_FLIGHT")
        self.assertEqual(esc_live.priority, "P0")
        self.assertTrue(esc_live.executable_now)
        self.assertIn("02:380:15", esc_live.target_name)

    def test_bash_limit_3_per_24h_exemptions_and_server_banner_override(self) -> None:
        t0 = 1_700_000_000.0
        tracker = BashTracker()
        active_target = "02:380:09"
        inactive_target = "02:380:04"
        spacia_target = "02:380:05"

        # Active planet: 3 attacks allowed in rolling 24h, 4th blocked, re-opened after 24h window.
        for idx in range(3):
            self.assertTrue(tracker.can_attack(active_target, timestamp=t0 + idx * 60.0))
            tracker.record_attack(active_target, timestamp=t0 + idx * 60.0)
        self.assertEqual(tracker.attacks_last_24h(active_target, timestamp=t0 + 300.0), 3)
        self.assertFalse(tracker.can_attack(active_target, timestamp=t0 + 300.0))
        self.assertTrue(tracker.can_attack(active_target, timestamp=t0 + 86401.0))

        # Yellow inactives and Spacia are exempt from the 3/24h active cap.
        for idx in range(5):
            tracker.record_attack(inactive_target, timestamp=t0 + idx * 60.0)
            tracker.record_attack(spacia_target, timestamp=t0 + idx * 60.0)
        self.assertTrue(tracker.can_attack(inactive_target, is_inactive=True, timestamp=t0 + 600.0))
        self.assertTrue(tracker.can_attack(spacia_target, is_spacia=True, timestamp=t0 + 600.0))

        # Server bash-block banner overrides even yellow-inactive / Spacia exemptions until reset.
        bash_html = (
            "<div>Attack (forbidden): You have attacked this planet too often! "
            "Attack-Bash-Regulations active. Reset in 04:30:00</div>"
        )
        parsed = parse_send_fleet_mission_block(bash_html, "ATTACK")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        reason, cooldown_s, is_bash = parsed
        self.assertTrue(is_bash)
        self.assertGreaterEqual(cooldown_s, 4 * 3600.0 + 30 * 60.0)
        self.assertIn("04:30:00", reason)

        tracker.record_block(inactive_target, t0 + 600.0 + cooldown_s, reason=reason)
        tracker.record_block(spacia_target, t0 + 600.0 + cooldown_s, reason=reason)
        self.assertFalse(tracker.can_attack(inactive_target, is_inactive=True, timestamp=t0 + 700.0))
        self.assertFalse(tracker.can_attack(spacia_target, is_spacia=True, timestamp=t0 + 700.0))
        # Once the server bash cooldown expires, exemptions resume.
        self.assertTrue(
            tracker.can_attack(inactive_target, is_inactive=True, timestamp=t0 + 600.0 + cooldown_s + 1.0)
        )
        self.assertTrue(
            tracker.can_attack(spacia_target, is_spacia=True, timestamp=t0 + 600.0 + cooldown_s + 1.0)
        )
