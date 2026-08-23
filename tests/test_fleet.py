"""Unit tests for M5-M6 fleet safety, combat simulation, bash rules, Spacia & colony helpers."""

from __future__ import annotations

import unittest

from spaceinvasion.fleet import (
    BashTracker,
    GalaxySlot,
    SpyReport,
    extract_galaxy_coords_from_html,
    extract_galaxy_system_coords,
    parse_galaxy_system_html,
    parse_spy_messages_html,
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

        pvp_slot = GalaxySlot("02:380:06", 6, name="Rival", owner="PlayerX", status_color="light_red")
        pvp_rep = SpyReport("02:380:06", owner="PlayerX", resources=rep.resources, defense=rep.defense, is_spacia=False)
        self.assertIsNone(score_raid_target("02:380:12", pvp_slot, pvp_rep, {"s7": 5, "s3": 2}, allow_pvp_in_az=False))
        self.assertIsNotNone(score_raid_target("02:380:12", pvp_slot, pvp_rep, {"s7": 5, "s3": 2}, allow_pvp_in_az=True))

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

    def test_parse_galaxy_system_html_handles_real_dom_variations_and_coords(self) -> None:
        html = """
        <html>
          <body>
            <div>System [02:385]</div>
            <input type="text" name="galaxy" value="2"/>
            <input type="text" name="system" value="385"/>
            <table>
              <tr><th>Pos</th><th>Planet</th><th>Player</th><th>Actions</th></tr>
              <tr>
                <td><img src="p.png"/></td>
                <td>[04]</td>
                <td>Colony</td>
                <td>Player (i)</td>
                <td><a href="indexInternal.es?action=internalFleet&amp;gal=2&amp;sun=385&amp;pla=4&amp;spy=1">Spy</a></td>
              </tr>
              <tr>
                <td>5.</td>
                <td>SleepyBase</td>
                <td>Sleepy (I)</td>
                <td><a href="#" onclick="sendSpy(2, 385, 5)">Espionage</a></td>
              </tr>
              <tr>
                <td>6</td>
                <td>Imperium</td>
                <td><span style="color:#ff0000">RedActive</span></td>
                <td><a href="indexInternal.es?action=internalFleet&amp;gal=2&amp;sun=385&amp;pla=6&amp;attack=1">Attack</a></td>
              </tr>
              <tr>
                <td>7</td>
                <td>BlueShield</td>
                <td>UrlPlayer (u)</td>
                <td></td>
              </tr>
              <tr>
                <td>8</td>
                <td><span style="color: #ffff00;">GoldMine</span></td>
                <td>GoldOwner</td>
                <td></td>
              </tr>
              <tr>
                <td>9</td>
                <td><font color="yellow">OldFont</font></td>
                <td>OldOwner</td>
                <td></td>
              </tr>
              <tr>
                <td>10</td>
                <td><span style="color: rgb(255, 255, 0);">RgbIdle</span></td>
                <td>RgbOwner</td>
                <td></td>
              </tr>
              <tr>
                <td>11</td>
                <td><span style="color: #00ff00;">AllyBase</span></td>
                <td>AllyFriend</td>
                <td></td>
              </tr>
              <tr>
                <td>12</td>
                <td>Spacia Node [A]</td>
                <td>Spacia</td>
                <td><a href="indexInternal.es?action=internalFleet&amp;gal=2&amp;sun=385&amp;pla=12&amp;spy=1">Spy</a></td>
              </tr>
            </table>
          </body>
        </html>
        """
        slots = parse_galaxy_system_html(html)
        by_pos = {s.position: s for s in slots}

        self.assertEqual(by_pos[4].coords, "02:385:04")
        self.assertEqual(by_pos[4].name, "Colony")
        self.assertEqual(by_pos[4].owner, "Player (i)")
        self.assertEqual(by_pos[4].status_color, "yellow")
        self.assertIsNotNone(by_pos[4].spy_href)

        self.assertEqual(by_pos[5].status_color, "yellow")
        self.assertIn("sendSpy", by_pos[5].spy_href or "")

        # #ff0000 red and word "Imperium" must NOT match #ff0 or (i)
        self.assertEqual(by_pos[6].name, "Imperium")
        self.assertEqual(by_pos[6].status_color, "light_red")
        self.assertIsNotNone(by_pos[6].attack_href)

        self.assertEqual(by_pos[7].status_color, "blue")
        self.assertEqual(by_pos[8].status_color, "yellow")
        self.assertEqual(by_pos[9].status_color, "yellow")
        self.assertEqual(by_pos[10].status_color, "yellow")
        self.assertEqual(by_pos[11].status_color, "green")

        self.assertTrue(by_pos[12].is_spacia)
        self.assertEqual(by_pos[12].status_color, "yellow")
        self.assertTrue(by_pos[12].has_asteroid)

        # Modern div card fallback
        modern_html = """
        <div class="galaxy-grid">
          <div class="galaxy-row" data-pos="3">
            <span class="planet-name">ModernColony</span>
            <span class="owner">ModernIdle (i)</span>
            <a href="indexInternal.es?action=internalFleet&spy=1">Spy</a>
          </div>
        </div>
        """
        modern_slots = parse_galaxy_system_html(modern_html, galaxy=3, system=120)
        self.assertEqual(len(modern_slots), 1)
        self.assertEqual(modern_slots[0].coords, "03:120:03")
        self.assertEqual(modern_slots[0].status_color, "yellow")
        self.assertIsNotNone(modern_slots[0].spy_href)

        # Coordinate extraction from inputs, selects, headers, and query params
        self.assertEqual(extract_galaxy_system_coords(html), (2, 385))
        self.assertEqual(
            extract_galaxy_coords_from_html(
                "<select name='gal'><option value='4' selected>4</option></select>"
                "<select name='sun'><option value='210' selected>210</option></select>"
            ),
            (4, 210),
        )
        self.assertEqual(
            extract_galaxy_coords_from_html("<div>System [05:111]</div>"),
            (5, 111),
        )
        self.assertEqual(
            extract_galaxy_coords_from_html(
                "<html></html>",
                url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy=7&system=42",
            ),
            (7, 42),
        )

    def test_parse_spy_messages_html_extracts_multiple_reports_and_owners(self) -> None:
        spio_html = """
        <html><body>
          <div class="message">
            Espionage report on 02:380:04 Player: Sleepy
            Pig iron: 30.000 Metal: 10.000 Kryptonite: 5.000 Spice: 2.000
            Small transport: 3 Mortar: 2
          </div>
          <div class="message">
            Espionage report on 02:380:09 Spacia
            Pig iron: 80.000 Metal: 40.000 Kryptonite: 20.000 Spice: 8.000
          </div>
        </body></html>
        """
        reports = parse_spy_messages_html(spio_html)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].target_coords, "02:380:04")
        self.assertEqual(reports[0].owner, "Sleepy")
        self.assertEqual(reports[0].fleet.get("s1"), 3)
        self.assertEqual(reports[0].defense.get("d1"), 2)
        self.assertTrue(reports[1].is_spacia)
        self.assertEqual(reports[1].resources.pig_iron, 80000)

    def test_extract_galaxy_system_coords_no_triple_backtracking_and_clocks_do_not_override(self) -> None:
        self.assertEqual(
            extract_galaxy_system_coords(
                "<table><tr><td><a href='indexInternal.es?action=internalFleet'>02:385:04</a></td></tr></table>"
            ),
            (2, 385),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>System 02:385:04</div>"),
            (2, 385),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>System: [02:385]</div>"),
            (2, 385),
        )
        # Clocks and field ratios never override headers, links, URLs, or defaults
        self.assertEqual(
            extract_galaxy_system_coords("<div>Server time 04:41 | System [05:111]</div>"),
            (5, 111),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>Server time 14:35 | Fields 12/230</div><table><tr><td><a href='#'>P 02:381:04</a></td></tr></table>"
            ),
            (2, 381),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>14:35:12</div>",
                url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy=2&system=385",
            ),
            (2, 385),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>Server time 04:41 | Fields 12/230</div>", default=(2, 380)),
            (2, 380),
        )
        # Unprefixed clocks, field ratios, and naked HH:MM:SS never override default
        self.assertEqual(
            extract_galaxy_system_coords("<div>14:35 | Fields 12/230</div>", default=(2, 380)),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>12/230 Fields</div>", default=(2, 380)),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>04:41</div>", default=(2, 380)),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>14:35:12</div>", default=(2, 380)),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>Server time: 14:35:12</div><div>ETA: 14:35:12</div>",
                default=(2, 380),
            ),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>14:35:12 UTC</div><div>14:35:12 UTC</div>",
                default=(2, 380),
            ),
            (2, 380),
        )
        # Valid coordinate syntaxes remain preserved
        self.assertEqual(
            extract_galaxy_system_coords("<div>System 02:38:04</div>", default=(2, 380)),
            (2, 38),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>02:385:04</div>", default=(2, 380)),
            (2, 385),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>02:385:04 | Fields 12/230</div>", default=(2, 380)),
            (2, 385),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>System [05:111] | Fields 12/230</div>", default=(2, 380)),
            (5, 111),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<table><tr><td><a href='#'>02:38:04</a></td></tr></table>",
                default=(2, 380),
            ),
            (2, 38),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<table><tr><td>02:38:01</td></tr><tr><td>02:38:04</td></tr></table>",
                default=(2, 380),
            ),
            (2, 38),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>14:35 | Fields 12/230</div>",
                url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy=2&system=385",
                default=(2, 380),
            ),
            (2, 385),
        )
        # Adv.8 & variants: noisy <a> clock timers never pollute in_link_set
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>Server time: <a href='#'>14:35:12</a></div><div>14:35:12</div>",
                default=(2, 380),
            ),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>Server time: <a href='#'>14:35:12</a></div><div>14:35:12</div>",
                url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy=2&system=385",
                default=(2, 380),
            ),
            (2, 385),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div><a href='#'>14:35:12</a> UTC</div><div>14:35:12</div>",
                default=(2, 380),
            ),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>Server time: <span><a href='#'>14:35:12</a></span></div><div>14:35:12</div>",
                default=(2, 380),
            ),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<table><tr><td>Server time:</td><td><a href='#'>14:35:12</a></td></tr></table><div>14:35:12</div>",
                default=(2, 380),
            ),
            (2, 380),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div><a href='#'>14:35:12 UTC</a></div><div>14:35:12</div>",
                default=(2, 380),
            ),
            (2, 380),
        )
        # Clean <a> coordinates still win alongside noisy <a> timers
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>Server time: <a href='#'>14:35:12</a></div><div><a href='#'>02:38:04</a></div>",
                default=(2, 380),
            ),
            (2, 38),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>System: <a href='#'>02:38:04</a></div>",
                default=(2, 380),
            ),
            (2, 38),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div><a href='#'><span>P</span> 02:38:04</a></div>",
                default=(2, 380),
            ),
            (2, 38),
        )

    def test_slot_status_classification_ignores_planet_and_owner_color_words_and_dark_red(self) -> None:
        html = """
        <table>
          <tr><td>1</td><td>Blue Lagoon</td><td><a href="indexInternal.es?action=internalAlliance">AllyOne (i)</a><img src="planet_blue_01.png"/></td></tr>
          <tr><td>2</td><td>Green Valley</td><td>FriendTwo (i)</td></tr>
          <tr><td>3</td><td>White Star</td><td>CloudThree (i)</td></tr>
          <tr><td>4</td><td>Ally Home</td><td>Alliance HQ (i)</td></tr>
          <tr><td>5</td><td>Pink Nebula</td><td>StarOwner (i)</td></tr>
          <tr><td>6</td><td>Yellow Stone</td><td>ActivePlayer</td></tr>
          <tr class="galaxy_dark_red"><td>7</td><td>Stronghold</td><td>HighRank</td></tr>
          <tr class="dark-red"><td>8</td><td>Citadel</td><td>Too strong</td></tr>
          <tr class="blue"><td>9</td><td>VacWorld</td><td>VacOwner</td></tr>
          <tr><td>10</td><td>NoobBase</td><td>NewPlayer (n)</td></tr>
          <tr><td>11</td><td>GhostMoon</td><td>Phantom (s)</td></tr>
          <tr><td>12</td><td><span class="galaxy_green">AllianceBase</span></td><td>Mate</td></tr>
          <tr><td>13</td><td>AdminRock</td><td>Operator (go)</td></tr>
        </table>
        """
        slots = parse_galaxy_system_html(html, galaxy=2, system=380)
        by_pos = {s.position: s for s in slots}
        for pos in (1, 2, 3, 4, 5):
            self.assertEqual(by_pos[pos].status_color, "yellow", f"pos {pos} misclassified")
        self.assertEqual(by_pos[6].status_color, "light_red")
        self.assertEqual(by_pos[7].status_color, "dark_red")
        self.assertEqual(by_pos[8].status_color, "dark_red")
        self.assertEqual(by_pos[9].status_color, "blue")
        self.assertEqual(by_pos[10].status_color, "blue")
        self.assertEqual(by_pos[11].status_color, "white")
        self.assertEqual(by_pos[12].status_color, "green")
        self.assertEqual(by_pos[13].status_color, "purple")


if __name__ == "__main__":
    unittest.main()
