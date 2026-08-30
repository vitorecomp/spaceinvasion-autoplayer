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

    def test_clock_timestamps_not_misparsed_coord_normalization_and_combat_none_guards(self) -> None:
        from spaceinvasion.fleet import _extract_spy_coords, extract_galaxy_system_coords, parse_spy_messages_html

        msg_html = """
        <div class="msg">
          <div>Report time: 14:35:12 (23:59:59) Target [02:380:04]</div>
          <div>Pig iron: 10000 Metal: 5000 Kryptonite: 2500 Spice: 1000</div>
        </div>
        """
        self.assertEqual(extract_galaxy_system_coords("System (14:35:12) View [2:380]"), (2, 380))
        self.assertEqual(
            extract_galaxy_system_coords(
                '<a href="indexInternal.es?action=internalGalaxy&gal=2&sun=380">Jump (14:35:12)</a>',
                url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&gal=2&sun=380",
            ),
            (2, 380),
        )
        self.assertEqual(
            _extract_spy_coords("Report (12:05:12 - Server) Planet 2:380:4 Pig iron: 1000"),
            "2:380:4",
        )
        spy_msgs = parse_spy_messages_html(msg_html)
        self.assertEqual(len(spy_msgs), 1)
        self.assertEqual(spy_msgs[0].target_coords, "02:380:04")

        tracker = BashTracker()
        for offset in (10.0, 20.0, 30.0):
            tracker.record_attack("2:380:4", timestamp=900.0 + offset)
        self.assertEqual(tracker.attacks_last_24h("02:380:04", timestamp=1000.0), 3)
        self.assertFalse(tracker.can_attack("02:380:04", is_inactive=False, is_spacia=False, timestamp=1000.0))

        rep = SpyReport(
            target_coords="02:380:04",
            owner="Idle",
            resources=Resources(10_000, 5_000, 2_000, 1_000),
            fleet={"s4": None},  # type: ignore[dict-item]
            defense={"d1": "bad"},  # type: ignore[dict-item]
        )
        res = simulate_raid_outcome(
            {"s4": 10, "s1": 5},
            rep,
            attacker_researches={"r8": None, "r9": "bad", "r10": None},  # type: ignore[dict-item]
        )
        self.assertGreater(res.attacker_power, 0.0)
        for bad_ratio in (float("nan"), float("inf"), float("-inf"), -0.5):
            r_bad = simulate_raid_outcome({"s4": 10, "s1": 5}, rep, plunder_ratio=bad_ratio)
            self.assertEqual(r_bad.loot_spice_equivalent, 0.0)
            self.assertTrue(r_bad.expected_loot.is_zero())
        r_over = simulate_raid_outcome({"s4": 10, "s1": 50}, rep, plunder_ratio=2.0)
        self.assertEqual(r_over.expected_loot.pig_iron, 10_000)

    def test_spy_and_galaxy_extractors_exclude_clock_timestamps(self) -> None:
        from spaceinvasion.fleet import _extract_spy_coords, normalize_coords

        self.assertEqual(_extract_spy_coords("Report at [12:05:12] Pig iron: 5000"), "")
        self.assertEqual(_extract_spy_coords("Report at 12:05:12 Pig iron: 5000"), "")
        self.assertEqual(_extract_spy_coords("Report [12:05:12] target [02:380:04] Pig iron: 5000"), "02:380:04")
        self.assertEqual(_extract_spy_coords("Report [14:35:12] target 2:380:4 Pig iron: 5000"), "2:380:4")
        self.assertEqual(_extract_spy_coords("Report 12:05:12 target 02:380:04 Pig iron: 5000"), "02:380:04")
        rep_norm = parse_spy_report_html("<html><body><div>[14:35:12] target 2:380:4</div><div>Pig iron 1000</div></body></html>")
        self.assertEqual(normalize_coords(rep_norm.target_coords), "02:380:04")
        self.assertEqual(
            extract_galaxy_system_coords("<div>Server time [12:05:12]</div>", default=(9, 99)),
            (9, 99),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>[14:35:12] Target [02:380:04]</div>", default=(1, 1)),
            (2, 380),
        )
        parsed = parse_spy_report_html("<html><body><div>[12:05:12]</div><div>Pig iron 1000</div></body></html>")
        self.assertEqual(parsed.target_coords, "")

    def test_huge_int_ships_and_research_avoid_overflow_in_combat_cargo_and_escape(self) -> None:
        import math
        from spaceinvasion.fleet import fleet_cargo_capacity

        cap = fleet_cargo_capacity({"s1": 10**400}, miner_spec_level=10**400)
        self.assertGreater(cap, 0)

        sim = simulate_raid_outcome(
            {"s4": 10**1_000_000},
            SpyReport(
                target_coords="02:380:05",
                fleet={"s4": 10**400},
                defense={"d1": 10**400},
                resources=Resources(50_000, 20_000, 10_000, 5_000),
            ),
            attacker_researches={"r4": 10**400, "r5": 10**400, "r6": 10**400, "r8": 10**400},
        )
        self.assertTrue(math.isfinite(sim.attacker_power))
        self.assertTrue(math.isfinite(sim.defender_power))
        self.assertTrue(math.isfinite(sim.loot_spice_equivalent))

        p_huge = PlanetState(
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
            ships={"s1": 10**1_000_000},
        )
        esc = plan_escape_flight(p_huge, target_coords="02:380:13", fuel_reserve_spice=500)
        self.assertTrue(esc.feasible)
        self.assertIn("~1e", esc.reason)

    def test_gap05_non_0m_clocks_excluded_while_coord_context_and_02_38_04_preserved(self) -> None:
        from spaceinvasion.fleet import _extract_spy_coords

        self.assertEqual(extract_galaxy_system_coords("<div>[14:15:12]</div>", default=(7, 77)), (7, 77))
        self.assertEqual(
            extract_galaxy_system_coords(
                "<table><tr><td>14:15:12</td></tr><tr><td>14:15:12</td></tr></table>",
                default=(7, 77),
            ),
            (7, 77),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>[14:15:12]</div><div>[14:15:12]</div>",
                default=(9, 99),
            ),
            (9, 99),
        )
        self.assertEqual(extract_galaxy_system_coords("<div>[14:15]</div>", default=(9, 99)), (9, 99))
        self.assertEqual(
            extract_galaxy_system_coords("<div>[14:15:12] System [02:38:04]</div>", default=(7, 77)),
            (2, 38),
        )
        self.assertEqual(
            extract_galaxy_system_coords("<div>System [02:38:04]</div>", default=(9, 99)),
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
                "<table><tr><td><a href='#'>02:38:04</a></td></tr></table>",
                default=(2, 380),
            ),
            (2, 38),
        )

        self.assertEqual(_extract_spy_coords("[14:15:12] Spy report Pig iron 500"), "")
        self.assertEqual(_extract_spy_coords("14:15:12 Spy report Pig iron 500"), "")
        self.assertEqual(_extract_spy_coords("[14:15:12] Fleet arrived without coordinates"), "")
        self.assertEqual(_extract_spy_coords("14:15:12 Fleet arrived without coordinates"), "")
        self.assertEqual(_extract_spy_coords("Spy report at [14:15:12]"), "")
        self.assertEqual(_extract_spy_coords("[14:15:12] Spy report for Planet [02:38:04]"), "02:38:04")
        self.assertEqual(_extract_spy_coords("Report [12:15:12] target 02:38:04 Pig iron: 5000"), "02:38:04")
        self.assertEqual(_extract_spy_coords("Planet [12:15:08] Pig iron: 5000"), "12:15:08")
        self.assertEqual(_extract_spy_coords("Target: 12:15:08 Pig iron: 5000"), "12:15:08")
        self.assertEqual(_extract_spy_coords("Planet [02:05:04] Pig iron: 5000"), "02:05:04")
        self.assertEqual(parse_spy_messages_html("<div>[12:15:12] Pig iron: 5000</div>"), ())

    def test_r8_normalize_coords_and_galaxy_parsers_reject_arabic_indic_digits(self) -> None:
        from spaceinvasion.fleet import _parse_coords_distance, normalize_coords

        arabic_coords = "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"
        self.assertEqual(normalize_coords(arabic_coords), arabic_coords)
        self.assertEqual(normalize_coords("2:380:4"), "02:380:04")
        self.assertEqual(_parse_coords_distance(arabic_coords, "01:001:01"), 100.0)
        self.assertEqual(
            extract_galaxy_system_coords(
                "<div>Galaxy [\u0660\u0662:\u0663\u0668\u0660]</div>",
                default=(5, 55),
            ),
            (5, 55),
        )

    def test_r9_simulate_raid_outcome_defender_researches_and_arabic_indic_parsers(self) -> None:
        rep = SpyReport(
            target_coords="02:380:04",
            owner="Teched",
            resources=Resources(10_000, 10_000, 5_000, 2_000),
            fleet={"s4": 10},
            defense={"d1": 5},
            dug_in=False,
        )
        c0 = simulate_raid_outcome({"s4": 30}, rep, defender_researches={"r8": 0, "r9": 0, "r10": 0})
        c15 = simulate_raid_outcome({"s4": 30}, rep, defender_researches={"r8": 15, "r9": 15, "r10": 15})
        self.assertAlmostEqual(c15.defender_power, c0.defender_power * 2.5, places=6)
        self.assertLess(c15.power_ratio, c0.power_ratio)

        # Arabic-Indic rejection in <input>, <select>, url=, and spy report fields
        self.assertEqual(
            extract_galaxy_system_coords(
                '<input name="galaxy" value="\u0662"><input name="system" value="\u0663\u0668\u0660">',
                default=(7, 77),
            ),
            (7, 77),
        )
        self.assertEqual(
            extract_galaxy_system_coords(
                '<select name="galaxy"><option selected value="\u0662">2</option></select>'
                '<select name="system"><option selected value="\u0663\u0668\u0660">380</option></select>',
                default=(7, 77),
            ),
            (7, 77),
        )
        slots = parse_galaxy_system_html(
            '<table><tr><td class="pos">1</td><td>Planet</td></tr></table>',
            url="?galaxy=\u0662&system=\u0663\u0668\u0660",
        )
        self.assertEqual(slots[0].coords, "01:001:01")
        ar_coords = "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"
        spy_ar = parse_spy_report_html(
            f"<div>Target [{ar_coords}] Pig iron: \u0665\u0660\u0660 Metal: \u0661.\u0662\u0660\u0660 Small fighter: \u0665 Mortar: \u0663</div>"
        )
        self.assertEqual(spy_ar.target_coords, "")
        self.assertEqual(spy_ar.resources, Resources())
        self.assertEqual(spy_ar.fleet, {})
        self.assertEqual(spy_ar.defense, {})

    def test_r10_normalize_coords_bytes_and_frozenset_safe_key(self) -> None:
        from spaceinvasion import fleet
        from spaceinvasion.fleet import SpyReport, fleet_cargo_capacity, normalize_coords, simulate_raid_outcome
        from spaceinvasion.state import Resources

        self.assertEqual(normalize_coords(b"2:380:4"), "02:380:04")  # type: ignore[arg-type]
        norm_huge = normalize_coords(10**5000)  # type: ignore[arg-type]
        self.assertIsInstance(norm_huge, str)
        self.assertLessEqual(len(norm_huge), 250)
        self.assertIsInstance(normalize_coords((10**5000, "x")), str)  # type: ignore[arg-type]
        self.assertEqual(fleet._safe_fleet_int(b"25"), 25)
        self.assertEqual(fleet._safe_fleet_int(frozenset({1})), 0)
        self.assertFalse(fleet._valid_gal(frozenset({1})))
        self.assertFalse(fleet._valid_sys(frozenset({1})))

        cap = fleet_cargo_capacity({10**5000: 5, "s2": 2})  # type: ignore[dict-item]
        self.assertGreater(cap, 0)
        rep = SpyReport(
            target_coords="02:380:04",
            owner="target",
            resources=Resources(100, 100, 100, 100),
            fleet={10**5000: 1},  # type: ignore[dict-item]
            defense={frozenset({10**5000}): 1},  # type: ignore[dict-item]
        )
        sim = simulate_raid_outcome({"s4": 10, 10**5000: 2}, rep)  # type: ignore[dict-item]
        self.assertTrue(sim.safe_to_attack)

    def test_r12_normalize_coords_resists_broken_fspath_and_raise_str_obj(self) -> None:
        from pathlib import Path
        from spaceinvasion import fleet as flt

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        self.assertIsInstance(flt.normalize_coords(_RaiseFspath()), str)
        self.assertIsInstance(flt.normalize_coords(_IntPath()), str)
        self.assertIsInstance(flt.normalize_coords(_RaiseStrRepr()), str)
        self.assertEqual(flt.normalize_coords(Path("2:380:12")), "02:380:12")
        self.assertFalse(flt._valid_gal(_RaiseFspath()))
        self.assertFalse(flt._valid_gal(_IntPath()))
        self.assertFalse(flt._valid_sys(_RaiseFspath()))
        self.assertFalse(flt._valid_sys(_IntPath()))

        planet = PlanetState(
            planet_id=1,
            name="P",
            coords="02:380:12",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=200,
            resources=Resources(1000, 1000, 1000, 1000),
            storage=Resources(50_000, 50_000, 50_000, 50_000),
            energy=EnergyBalance(50, 10),
            ships={"s1": 1},
        )
        ep = plan_escape_flight(
            planet,
            target_coords=_RaiseFspath(),  # type: ignore[arg-type]
            fuel_reserve_spice=_RaiseFspath(),  # type: ignore[arg-type]
            duration_hint=_RaiseFspath(),  # type: ignore[arg-type]
        )
        self.assertTrue(ep.feasible)
        self.assertIsInstance(ep.target_coords, str)
        self.assertIsInstance(ep.duration_hint, str)
        ep_empty = plan_escape_flight(
            PlanetState(
                planet_id=2,
                name="P2",
                coords="02:380:13",
                planet_type="normal",
                temperature=20,
                fields_used=1,
                fields_max=200,
                resources=Resources(1000, 1000, 1000, 1000),
                storage=Resources(50_000, 50_000, 50_000, 50_000),
                energy=EnergyBalance(50, 10),
                ships={},
            ),
            target_coords=_RaiseFspath(),  # type: ignore[arg-type]
            fuel_reserve_spice=_RaiseFspath(),  # type: ignore[arg-type]
            duration_hint=_RaiseFspath(),  # type: ignore[arg-type]
        )
        self.assertFalse(ep_empty.feasible)
        self.assertIsInstance(ep_empty.target_coords, str)
        self.assertIsInstance(ep_empty.duration_hint, str)

        sim = simulate_raid_outcome(
            {_RaiseFspath(): 5, "s4": 10},  # type: ignore[dict-item]
            SpyReport(target_coords="02:381:05", resources=Resources(1000, 1000, 1000, 1000), is_spacia=True),
        )
        self.assertTrue(sim.safe_to_attack)

        bt = BashTracker()
        bt.record_attack(_RaiseFspath(), timestamp=_RaiseFspath())  # type: ignore[arg-type]
        self.assertTrue(bt.can_attack(_RaiseFspath(), timestamp=_IntPath()))  # type: ignore[arg-type]

    def test_r13_extract_spy_link_coords_excludes_clock_triples(self) -> None:
        self.assertEqual(
            parse_spy_report_html(
                '<div>Pig iron: 1000 <a href="internalGalaxy.php?gal=2&sun=150">12:05:12</a></div>'
            ).target_coords,
            "",
        )
        self.assertEqual(
            parse_spy_report_html(
                '<div>Pig iron: 1000 <a href="internalFleet.php?gal=2&sun=150">[14:35:12]</a></div>'
            ).target_coords,
            "",
        )
        self.assertEqual(
            parse_spy_report_html(
                '<div>Pig iron: 1000 <a href="internalGalaxy.php?gal=2&sun=150">04:05:12</a></div>'
            ).target_coords,
            "",
        )
        self.assertEqual(
            parse_spy_report_html(
                '<div>Pig iron: 1000 <a href="internalGalaxy.php?gal=2&sun=150">2:150:8</a></div>'
            ).target_coords,
            "2:150:8",
        )
        self.assertEqual(
            parse_spy_report_html(
                '<div>Pig iron: 1000 <a href="internalGalaxy.php?gal=2&sun=380">Target: 12:15:08</a></div>'
            ).target_coords,
            "12:15:08",
        )
        self.assertEqual(
            parse_spy_report_html(
                '<div>Planet <a href="internalGalaxy.php?gal=2&sun=38">02:38:04</a> Pig iron: 1000</div>'
            ).target_coords,
            "02:38:04",
        )

    def test_r14_parse_galaxy_system_html_resists_huge_10e5000_coords(self) -> None:
        from spaceinvasion.fleet import parse_galaxy_system_html

        html = '<table><tr><td>1</td><td>Planet</td><td>User</td><td></td></tr></table>'
        huge = 10**5000
        entries_huge = parse_galaxy_system_html(html, galaxy=huge, system=huge)  # type: ignore[arg-type]
        self.assertIsInstance(entries_huge, tuple)
        if entries_huge:
            self.assertRegex(entries_huge[0].coords, r"^\d{2}:\d{3}:\d{2}$")

        class _Boom:
            def __int__(self) -> int:
                raise RuntimeError("boom")

        entries_bad = parse_galaxy_system_html(html, galaxy=_Boom(), system=_Boom())  # type: ignore[arg-type]
        self.assertIsInstance(entries_bad, tuple)
        if entries_bad:
            self.assertEqual(entries_bad[0].coords, "01:001:01")

    def test_2d_graphical_galaxy_planet_hit_colors_spacia_and_si_tpl_coords(self) -> None:
        from spaceinvasion.fleet import extract_galaxy_system_coords, parse_galaxy_system_html

        html_2d = """
        <html><body data-galaxy="1">
        <script>window.SI_TPL=window.SI_TPL||{};SI_TPL.galaxy={sid:'abc',spacia:97563,galaxy:2,sun:378};</script>
        <div id="planet-list">02:380:12 Colony 2 System</div>
        <span id="planet-coords">02:380:12</span>
        <div class="planet-hit" data-planet="12433">
          <div><span style="color:#cacaca;">X: 520 Y: 90</span><div>1</div></div>
        </div>
        <div class="planet-hit" data-planet="12434">
          <div class="rubble-orbit" name="rubblefield"></div>
          <div><span style="color:#c9d324;"><div class="spacia-planet">Spacia Planet</div><br/>(Spacia)</span><div>2</div></div>
        </div>
        <div class="planet-hit" data-planet="12438">
          <div><span style="color:#347187;"><b>Takayama</b><br/>(wieselhaar)</span><div>6</div></div>
        </div>
        <div class="planet-hit" data-planet="12447">
          <div id="astro-12447" name="astro"></div>
          <div><span style="color:#c9d324;"><b>Colony</b><br/>(Sigilo_respec)</span><div>15</div></div>
        </div>
        </body></html>
        """
        gal, sys_n = extract_galaxy_system_coords(
            html_2d,
            url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&g=2&s=378",
        )
        self.assertEqual((gal, sys_n), (2, 378))

        slots = {s.position: s for s in parse_galaxy_system_html(html_2d)}
        self.assertEqual(slots[1].status_color, "free")
        self.assertTrue(slots[2].is_spacia)
        self.assertTrue(slots[2].has_debris)
        self.assertEqual(slots[2].coords, "02:378:02")
        self.assertEqual(slots[6].status_color, "blue")
        self.assertEqual(slots[6].owner, "wieselhaar")
        self.assertEqual(slots[15].status_color, "yellow")
        self.assertEqual(slots[15].owner, "Sigilo_respec")
        self.assertTrue(slots[15].has_asteroid)

        # Edge cases: JSON-quoted SI_TPL.galaxy scoped past unrelated JS, data-pos on planet-hit,
        # parenthesized planet names, (i) suffix outside (owner), #496c2a green, and spaced opacity: 0.6
        html_2d_edge = """
        <html><body>
        <script>window.otherCfg={galaxy:1,sun:9};SI_TPL.galaxy={"sid":"abc","galaxy":"3","sun":"142"};</script>
        <div class="planet-hit" data-pos="1" data-planet="20001">
          <div><span style="color:#cacaca;">X: 110 Y: 40</span><div>1</div></div>
        </div>
        <div class="planet-hit" data-pos="4" data-planet="20004">
          <div><span><b>Colony (Main)</b><br/>(RealOwner) (i)</span><div>4</div></div>
        </div>
        <div class="planet-hit" data-planet="20007">
          <div><span style="color:#496c2a;"><b>AllyBase</b><br/>(Mate)</span><div>7</div></div>
        </div>
        <div class="planet-hit" data-planet="20009">
          <div><span style="opacity: 0.6;"><b>GhostWorld</b><br/>(SpecUser)</span><div>9</div></div>
        </div>
        </body></html>
        """
        self.assertEqual(extract_galaxy_system_coords(html_2d_edge), (3, 142))
        edge_slots = {s.position: s for s in parse_galaxy_system_html(html_2d_edge)}
        self.assertEqual(edge_slots[1].status_color, "free")
        self.assertEqual(edge_slots[4].name, "Colony (Main)")
        self.assertEqual(edge_slots[4].owner, "RealOwner")
        self.assertEqual(edge_slots[4].status_color, "yellow")
        self.assertEqual(edge_slots[4].coords, "03:142:04")
        self.assertEqual(edge_slots[7].status_color, "green")
        self.assertEqual(edge_slots[9].status_color, "white")

    def test_bash_protection_banner_parser_block_override_and_german_sicherheitspruefung(self) -> None:
        from spaceinvasion.auth import AntiBotChallengeError, detect_antibot_challenge
        from spaceinvasion.fleet import BashTracker, parse_send_fleet_mission_block

        bash_html = """
        <html><body>
          <div class="info-box">
            Attack-Bash-Regulations: 3 attacks in 24 hours on this planet
            ( currently: 3 &bull; Reset in 16:31:50 )
          </div>
          <div class="error-box">
            Attack (forbidden): Player was attacked too often by you (bash protection)
          </div>
          <div class="error-box">
            Transport (forbidden): the target is inactive
          </div>
          <form id="fleet" action="indexInternal.es?action=internalSendFleetFinish">
            <input name="todo" type="radio" value="SAVE" /> Escape flight
          </form>
        </body></html>
        """
        blk = parse_send_fleet_mission_block(bash_html, "ATTACK")
        self.assertIsNotNone(blk)
        assert blk is not None
        reason, cooldown_s, is_bash = blk
        self.assertIn("bash protection", reason.lower())
        self.assertTrue(is_bash)
        self.assertAlmostEqual(cooldown_s, 16 * 3600 + 31 * 60 + 50 + 30.0, places=1)

        # Benign Transport (forbidden) on yellow inactives must NOT block ATTACK, SPY, or RECYCLE
        benign_inactive_html = """
        <html><body>
          <div class="error-box">Transport (forbidden): the target is inactive</div>
          <form id="fleet" action="indexInternal.es?action=internalSendFleetFinish">
            <input name="todo" type="radio" value="ATTACK" /> Attack
            <input name="todo" type="radio" value="RECYCLE" /> Recycle
          </form>
        </body></html>
        """
        self.assertIsNone(parse_send_fleet_mission_block(benign_inactive_html, "ATTACK"))
        self.assertIsNone(parse_send_fleet_mission_block(benign_inactive_html, "SPY"))
        self.assertIsNone(parse_send_fleet_mission_block(benign_inactive_html, "RECYCLE"))

        # Server-enforced block overrides is_spacia / is_inactive until blocked_until_ts expires
        bt = BashTracker()
        bt.record_block("02:383:02", 2000.0, reason=reason)
        self.assertFalse(bt.can_attack("02:383:02", is_inactive=True, is_spacia=True, timestamp=1500.0))
        self.assertAlmostEqual(bt.blocked_until("02:383:02", timestamp=1500.0), 2000.0, places=2)
        self.assertTrue(bt.can_attack("02:383:02", is_inactive=True, is_spacia=True, timestamp=2001.0))
        self.assertEqual(bt.blocked_until("02:383:02", timestamp=2001.0), 0.0)

        # German Cloudflare modal ("Sicherheitsprüfung") is detected by auth.py and raises in client.py
        from unittest.mock import MagicMock
        from spaceinvasion.client import GameBrowserClient

        modal_html = """
        <div class="modal">
          <h2>⚠️ Sicherheitsprüfung</h2>
          <p>Bitte kurz bestätigen, dass du kein Bot bist, um fortzufahren.</p>
        </div>
        """
        diag = detect_antibot_challenge(200, modal_html)
        self.assertIsNotNone(diag)
        self.assertIn("Anti-bot or CAPTCHA challenge detected", str(diag))

        browser_client = GameBrowserClient(MagicMock(), detach_on_close=False)
        with self.assertRaises(AntiBotChallengeError):
            browser_client._check_page_guards(modal_html)

    def test_own_planet_header_stripping_and_multirow_checkbox_inbox_parser(self) -> None:
        from spaceinvasion.fleet import (
            extract_message_folder_links,
            parse_inbox_messages_html,
            parse_spy_messages_html,
        )

        own_header_only_html = """
        <html><body>
          <div id="planet-list">02:380:12 Colony</div>
          <span id="planet-coords">[02:380:12]</span>
          <form id="ress">
            <span id="roheisen">Pig iron: 269.575</span>
            <span id="metall">Metal: 7.044</span>
            <span id="kryptonit">Kryptonite: 400.432</span>
            <span id="spice">Spice: 157.956</span>
          </form>
          <div class="unread-banner">
            <a href="indexInternal.es?action=internalMessages&sid=SECRET123">You have 13 unread Messages!</a>
          </div>
          <table>
            <tr><td><a href="indexInternal.es?action=internalMessages&type=spio&sid=SECRET123">Espionage</a></td><td>(3 / 8)</td></tr>
            <tr><td><a href="indexInternal.es?action=internalMessages&type=battle&sid=SECRET123">Battle reports</a></td><td>(2 / 5)</td></tr>
          </table>
        </body></html>
        """
        self.assertEqual(parse_spy_messages_html(own_header_only_html), ())
        self.assertEqual(parse_inbox_messages_html(own_header_only_html), ())

        folders = {f[0]: f for f in extract_message_folder_links(own_header_only_html)}
        self.assertEqual(folders["spio"][2:], (3, 8))
        self.assertEqual(folders["battle"][2:], (2, 5))
        self.assertNotIn("SECRET123", folders["spio"][1])

        multirow_inbox_html = """
        <html><body>
          <span id="planet-coords">[02:380:12]</span>
          <form id="ress">
            <span id="roheisen">Pig iron: 269.575</span>
            <span id="metall">Metal: 7.044</span>
            <span id="kryptonit">Kryptonite: 400.432</span>
            <span id="spice">Spice: 157.956</span>
          </form>
          <form action="indexInternal.es?action=internalMessages&sid=SECRET999" method="post">
            <table>
              <tr>
                <td><input type="checkbox" name="checkall" onclick="checkAll(this)" /></td>
                <th>Date</th><th>Sender</th><th>Subject</th>
              </tr>
              <tr>
                <td><input type="checkbox" name="del[501]" value="501" /></td>
                <td>2026-11-16 16:01:12</td>
                <td>Fleet Command</td>
                <td>Espionage report on ColdVault [02:381:07]</td>
              </tr>
              <tr>
                <td colspan="4">
                  Player: StarMiner | Pig iron: 88.000 Metal: 44.000 Kryptonite: 21.000 Spice: 12.000
                  Pig iron mine: 14 Fusion plant: 12 Energy technology: 5
                  Small transporter: 6 Mortar: 2 Defenses are not dug-in
                </td>
              </tr>
              <tr>
                <td><input type="checkbox" name="del[502]" value="502" /></td>
                <td>2026-11-16 16:02:45</td>
                <td>Fleet Command</td>
                <td>Battle report [02:382:04]</td>
              </tr>
              <tr>
                <td colspan="4">
                  Attacker won the battle! Plundered Pig iron: 10.000 Metal: 5.000.
                  Debris field created: Pig iron: 18.000 Metal: 9.000
                </td>
              </tr>
              <tr>
                <td><input type="checkbox" name="del[503]" value="503" /></td>
                <td>2026-11-16 16:05:10</td>
                <td>Recycler Fleet</td>
                <td>Recycling report [02:380:04]</td>
              </tr>
              <tr>
                <td colspan="4">
                  Your Recycler fleet harvested Pig iron: 15.000 Metal: 7.500 at [02:380:04].
                </td>
              </tr>
              <tr>
                <td colspan="4">
                  <select name="deltype"><option value="marked">Delete marked</option></select>
                  <input type="submit" name="delbtn" value="OK" />
                </td>
              </tr>
            </table>
          </form>
        </body></html>
        """
        spy_only = parse_spy_messages_html(multirow_inbox_html, exclude_coords=("02:380:12",))
        self.assertEqual(len(spy_only), 1)
        self.assertEqual(spy_only[0].target_coords, "02:381:07")
        self.assertEqual(spy_only[0].server_msg_id, "501")
        self.assertEqual(spy_only[0].resources.pig_iron, 88_000)
        self.assertEqual(spy_only[0].buildings.get("b1"), 14)
        self.assertEqual(spy_only[0].buildings.get("b5"), 12)
        self.assertEqual(spy_only[0].researches.get("r2"), 5)

        inbox_msgs = parse_inbox_messages_html(
            multirow_inbox_html,
            folder_type="inbox",
            exclude_coords=("02:380:12",),
        )
        self.assertEqual([m.category for m in inbox_msgs], ["spy_report", "battle_report", "recycle_report"])
        self.assertEqual([m.server_msg_id for m in inbox_msgs], ["501", "502", "503"])
        self.assertTrue(inbox_msgs[1].parsed_data["has_debris"])
        self.assertEqual(inbox_msgs[2].coords, "02:380:04")
        for m in inbox_msgs:
            self.assertNotIn("SECRET999", m.raw_html)

    def test_real_retro_recycle_4res_fleet_return_battle_dot_thousands_embedded_spy_json_and_colony_grades(self) -> None:
        from spaceinvasion.fleet import (
            grade_colony_candidate,
            parse_inbox_messages_html,
            parse_spy_report_html,
        )

        retro_inbox_html = """
        <html><body>
          <table>
            <tr>
              <td><input type="checkbox" id="check_38" name="check_38" value="38" /></td>
              <td>2026-11-16 14:44:40</td>
              <td>System</td>
              <td>
                Space rubble [02:383:14] recycling report
                <div class="si-expanded-body" data-si-expanded="1">
                  02:380:12 Colony [02:380:12] Colony Pig iron 86.812 11% Metal 11.813 12% Kryptonite 386.451 48% Spice 201.549 50%
                  You receive the following resources: 10000 Pig iron, 10000 Metal, 10000 Kryptonite, 10000 Spice ? Total: 8 / 10
                  Message from System: Space rubble [02:383:14] recycling report Your fleet (5 Recycler) from planet Colony [02:380:12]
                  reached its goal Spacia Planet [02:383:14] and processed 17036 Pig iron, 12036 Metal, 14310 Kryptonite, 18098 Spice pieces of rubble there.
                  ' Fleet reports&#9733; Favourites Received on: From: Subject:
                </div>
              </td>
            </tr>
            <tr>
              <td><input type="checkbox" id="check_35" name="check_35" value="35" /></td>
              <td>2026-11-16 14:35:00</td>
              <td>System</td>
              <td>
                Colony [02:380:12] fleet return
                <div class="si-expanded-body" data-si-expanded="1">
                  Message from System: Colony [02:380:12] fleet return One of your fleets (1 Recycler) is returning from duty
                  from Spacia Planet [02:380:05] to its home planet Colony [02:380:12]. The assignment was to: Recycle.
                  6250 Pig iron, 6250 Metal, 6250 Kryptonite, 6250 Spice were unloaded.
                  &#9733; Favourites
                </div>
              </td>
            </tr>
            <tr>
              <td><input type="checkbox" id="check_32" name="check_32" value="32" /></td>
              <td>2026-11-16 13:20:00</td>
              <td>Fleet Command</td>
              <td>
                <span data-bsum="16254211">Looted 5.842 &#8644; [02:383:02] Spacia</span>
                <div class="si-expanded-body" data-si-expanded="1" data-battle-json='{"ok":true,"rounds":1,"winner":1,"attacker":{"name":"ventkx86","lost":"0"},"defender":{"name":"Spacia","lost":"0"},"stolen":[{"r":1,"v":"3.075"},{"r":2,"v":"3.075"},{"r":3,"v":"3.075"},{"r":4,"v":"3.075"}],"rubble":[{"r":1,"v":"1.200"},{"r":2,"v":"600"}]}'>
                  Battle report #16254211 | Rounds: 1 | Winner: 1 | Attacker: ventkx86 (Lost: 0) | Defender: Spacia (Lost: 0) | Plundered Pig iron: 3.075 Metal: 3.075 Kryptonite: 3.075 Spice: 3.075
                </div>
              </td>
            </tr>
          </table>
        </body></html>
        """
        msgs = parse_inbox_messages_html(retro_inbox_html, folder_type="fleet", exclude_coords=("02:380:12",))
        by_id = {m.server_msg_id: m for m in msgs}
        self.assertEqual(set(by_id.keys()), {"38", "35", "32"})

        rec = by_id["38"]
        self.assertEqual(rec.category, "recycle_report")
        self.assertEqual(rec.coords, "02:383:14")
        self.assertNotIn("86.812", rec.body_text)
        self.assertEqual(rec.parsed_data["recycler_count"], 5)
        self.assertEqual(
            rec.parsed_data["harvested"],
            {"pig_iron": 17036, "metal": 12036, "kryptonite": 14310, "spice": 18098},
        )
        self.assertAlmostEqual(rec.parsed_data["harvested_spice_eq"], 30521.73, places=2)

        ret = by_id["35"]
        self.assertEqual(ret.category, "fleet_return")
        self.assertEqual(ret.parsed_data["unloaded"]["pig_iron"], 6250)

        bat = by_id["32"]
        self.assertEqual(bat.category, "battle_report")
        self.assertEqual(bat.coords, "02:383:02")
        self.assertEqual(bat.parsed_data["battle_id"], "16254211")
        self.assertEqual(bat.parsed_data["attacker"], "ventkx86")
        self.assertEqual(bat.parsed_data["defender"], "Spacia")
        self.assertEqual(
            bat.parsed_data["loot"],
            {"pig_iron": 3075, "metal": 3075, "kryptonite": 3075, "spice": 3075},
        )
        self.assertAlmostEqual(bat.parsed_data["loot_spice_eq"], 5842.5, places=2)
        self.assertEqual(bat.parsed_data["debris"], {"pig_iron": 1200, "metal": 600})

        spy_json_html = """
        <div>
          Spaceinvasion { "info": { "si": "classic", "v": 1, "type": "spy-report" }, "reportID": 24850463,
          "planet": { "id": 12514, "coords": "02:383:02", "type": "PLANET", "name": "Spacia Planet",
          "resources": [ 17020, 65030, 76151, 40501 ], "energy": 2630399,
          "buildings": { "b1": { "name": "Pig iron mine", "level": 31 }, "b4": { "name": "Spice mine", "level": 28 } },
          "ships": false, "defense": false, "researches": false } }
          Pig ironMetalKryptoniteSpiceEnergy17.02065.03076.15140.5012.630.399
        </div>
        """
        srep = parse_spy_report_html(spy_json_html)
        self.assertEqual(srep.target_coords, "02:383:02")
        self.assertEqual(srep.planet_name, "Spacia Planet")
        self.assertTrue(srep.is_spacia)
        self.assertEqual((srep.resources.pig_iron, srep.resources.metal, srep.resources.kryptonite, srep.resources.spice), (17020, 65030, 76151, 40501))
        self.assertEqual(srep.buildings, {"b1": 31, "b4": 28})

        g_s = grade_colony_candidate("02:380:12", "02:381:09", uninhabited_fields=320, min_fields_target=300)
        self.assertEqual((g_s["grade"], g_s["meets_target"], g_s["probed"], g_s["system_distance"]), ("S", True, True, 1))
        self.assertIn("Pos 7-12", g_s["orbital_band"])
        g_opt = grade_colony_candidate("02:382:08", origin_coords="02:380:12", uninhabited_fields=302, min_fields_target=300)
        self.assertEqual((g_opt["grade"], g_opt["meets_target"], g_opt["system_distance"]), ("A+", True, 2))
        g_strat = grade_colony_candidate(coords="02:380:10", position=10, uninhabited_fields=None, origin_coords="02:380:12", min_fields_target=300)
        self.assertEqual((g_strat["grade"], g_strat["probed"]), ("?", False))
        self.assertIn("Prime Large-Field Belt", g_strat["orbital_zone"])

    def test_fleet_return_with_own_header_never_parsed_as_spacia_spy_report(self) -> None:
        from spaceinvasion.fleet import (
            is_non_spy_notice_text,
            parse_inbox_messages_html,
            parse_spy_messages_html,
        )

        # Live RETRO regression: expanded `fleet return` message containing F_Hauptframe own-planet
        # resource bar + Tutorial reward text + `from Spacia Planet [02:380:14]` must NEVER be parsed
        # as a SpyReport for 02:380:14 (which poisoned free rubble slots as fake 800k Spacia targets).
        fleet_return_html = """
        <html><body>
          <table>
            <tr id="msg_7881299569787197">
              <td><input type="checkbox" id="check_7881299569787197" name="check_7881299569787197" value="7881299569787197" /></td>
              <td>2026-11-17 14:19:26</td>
              <td>System</td>
              <td>
                Colony [02:380:12] fleet return
                <div class="si-expanded-body" data-si-expanded="1">
                  02:380:12 Colony [02:380:12] Colony Pig iron 185.293 6% Metal 7.790 1% Kryptonite 656.686 41% Spice 551.163 69% Energy 342 / 5.954
                  Tutorial 10 / 11 You receive the following resources: 10000 Pig iron, 10000 Metal, 10000 Kryptonite, 10000 Spice
                  Message from System: Colony [02:380:12] fleet return One of your fleets (46 Recycler) is returning from duty
                  from Spacia Planet [02:380:14] to its home planet Colony [02:380:12]. The assignment was to: Recycle.
                  0 Pig iron, 0 Metal, 0 Kryptonite, 0 Spice were unloaded. ' Fleet reports&#9733; Favourites
                </div>
              </td>
            </tr>
          </table>
        </body></html>
        """
        self.assertEqual(parse_spy_messages_html(fleet_return_html, exclude_coords=("02:380:12",)), ())
        msgs = parse_inbox_messages_html(fleet_return_html, folder_type="inbox", exclude_coords=("02:380:12",))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].category, "fleet_return")
        self.assertIsNone(msgs[0].spy_report)
        self.assertTrue(is_non_spy_notice_text(msgs[0].body_text))

    def test_extract_galaxy_system_coords_prefers_live_json_element_over_stale_ajax_dom(self) -> None:
        from spaceinvasion.fleet import extract_galaxy_system_coords

        # After an in-place 2D Galaxy AJAX `window.si.navigate(2, 387)`, `<input value="129">`,
        # inline `SI_TPL.galaxy = {galaxy: 2, sun: 129}`, and `location.href` remain frozen on
        # the initial page load (02:129), while `#json-element` holds live `window.si.result`.
        ajax_html = """
        <html><body>
          <script>SI_TPL.galaxy = { galaxy: 2, sun: 129 };</script>
          <input name="galaxy" value="2" />
          <input name="system" value="129" />
          <textarea id="json-element">{"position":{"galaxy":2,"system":387},"planets":{}}</textarea>
        </body></html>
        """
        gal, sys_n = extract_galaxy_system_coords(
            ajax_html,
            url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&g=2&s=129",
            default=(1, 1),
        )
        self.assertEqual((gal, sys_n), (2, 387))


if __name__ == "__main__":
    unittest.main()
