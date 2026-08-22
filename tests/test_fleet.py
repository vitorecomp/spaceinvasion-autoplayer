"""Unit tests for M5-M6 fleet safety, combat simulation, bash rules, Spacia & colony helpers."""

from __future__ import annotations

import unittest

from spaceinvasion.fleet import (
    BashTracker,
    GalaxySlot,
    SpyReport,
    parse_galaxy_system_html,
    parse_spy_report_html,
    pick_colony_candidates,
    plan_escape_flight,
    score_raid_target,
    simulate_raid_outcome,
)
from spaceinvasion.state import EnergyBalance, PlanetState, Resources


class TestFleetAndCombat(unittest.TestCase):
    def test_escape_flight_packs_highest_value_density_first(self) -> None:
        planet = PlanetState(
            planet_id=1,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(pig_iron=50000, metal=10000, kryptonite=15000, spice=5000),
            storage=Resources(200000, 200000, 200000, 200000),
            energy=EnergyBalance(200, 150),
            ships={"s1": 3, "s9": 10},  # 3x6,000 = 18,000 cargo; s9 Solar cells stay put
        )
        plan = plan_escape_flight(planet, target_coords="02:380:13", fuel_reserve_spice=500)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.ships, {"s1": 3})
        # Available capacity after 500 fuel reserve = 17,500 -> 4,500 Spice + 10,000 Metal + 3,000 Kryp + 0 Pig
        self.assertEqual(plan.cargo.spice, 4500)
        self.assertEqual(plan.cargo.metal, 10000)
        self.assertEqual(plan.cargo.kryptonite, 3000)
        self.assertEqual(plan.cargo.pig_iron, 0)

    def test_simulate_raid_checks_margin_and_stealth_bomber_first_strike(self) -> None:
        report = SpyReport(
            target_coords="02:381:05",
            resources=Resources(pig_iron=10000, metal=10000, kryptonite=10000, spice=4000),
            defense={"d1": 25},
            is_spacia=True,
        )
        weak = simulate_raid_outcome({"s4": 2, "s1": 2}, report, safety_margin=1.4)
        self.assertFalse(weak.safe_to_attack)

        strong = simulate_raid_outcome({"s7": 10, "s3": 4, "s14": 2}, report, safety_margin=1.4)
        self.assertTrue(strong.safe_to_attack)
        self.assertGreater(strong.stealth_first_strike_power, 0)
        self.assertEqual(strong.expected_loot, Resources(5000, 5000, 5000, 2000))

    def test_bash_tracker_caps_active_planets_but_exempts_spacia_and_inactives(self) -> None:
        bt = BashTracker()
        now = 1_000_000.0
        for i in range(3):
            self.assertTrue(bt.can_attack("02:100:05", is_inactive=False, timestamp=now + i))
            bt.record_attack("02:100:05", timestamp=now + i)
        self.assertFalse(bt.can_attack("02:100:05", is_inactive=False, timestamp=now + 100))
        self.assertTrue(bt.can_attack("02:100:05", is_inactive=True, timestamp=now + 100))
        self.assertTrue(bt.can_attack("02:100:05", is_spacia=True, timestamp=now + 100))
        self.assertTrue(bt.can_attack("02:100:05", in_declared_war=True, timestamp=now + 100))

    def test_galaxy_spy_and_colony_helpers(self) -> None:
        gal_html = """
        <table>
          <tr class="yellow"><td>5</td><td>Spacia Outpost</td><td>Spacia (i)</td><td>debris</td></tr>
          <tr><td>9</td><td></td><td></td><td></td></tr>
          <tr class="blue"><td>15</td><td>Protected</td><td>Newbie</td><td></td></tr>
        </table>
        """
        slots = parse_galaxy_system_html(gal_html, galaxy=2, system=380)
        self.assertEqual(len(slots), 3)
        self.assertTrue(slots[0].is_spacia)
        self.assertEqual(slots[0].status_color, "yellow")
        self.assertTrue(slots[0].has_debris)
        self.assertEqual(slots[1].status_color, "free")

        spy_html = "<div>Target 02:380:05 Spacia Pig iron: 40.000 Metal: 20.000 Kryptonite: 10.000 Spice: 6.000 Mortar: 2</div>"
        rep = parse_spy_report_html(spy_html)
        self.assertEqual(rep.defense.get("d1"), 2)
        self.assertEqual(rep.resources.pig_iron, 40000)

        rec = score_raid_target("02:380:12", slots[0], rep, {"s7": 5, "s3": 2})
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertTrue(rec.is_spacia)
        self.assertGreater(rec.loot_spice_equivalent, 1500.0)

        # Colony filtering respects >230 field strategy vs cold_spice
        free_slots = (
            GalaxySlot("02:380:04", 4, status_color="free"),
            GalaxySlot("02:380:09", 9, status_color="free"),
            GalaxySlot("02:380:15", 15, status_color="free"),
        )
        small_rep = SpyReport("02:380:04", uninhabited_fields=170)
        large_rep = SpyReport("02:380:09", uninhabited_fields=265)
        large_ranked = pick_colony_candidates(
            free_slots,
            goal="large_fields",
            min_fields_target=230,
            uninhabited_reports={"02:380:04": small_rep, "02:380:09": large_rep},
        )
        self.assertEqual(large_ranked[0].position, 9)
        self.assertNotIn(4, [s.position for s in large_ranked])

        cold_ranked = pick_colony_candidates(free_slots, goal="cold_spice")
        self.assertEqual(cold_ranked[0].position, 15)


if __name__ == "__main__":
    unittest.main()
