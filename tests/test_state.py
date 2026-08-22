"""Unit tests for SpaceInvasion state JSON/HTML parsers."""

from __future__ import annotations

import json
import unittest

from spaceinvasion.state import (
    parse_building_queue,
    parse_buildings_page,
    parse_home_metadata,
    parse_menu_messages,
    parse_menu_status,
    parse_planets_overview_json,
    parse_research_page,
    parse_si_int,
    parse_tutorial_from_home,
    parse_unit_factory_page,
)

SAMPLE_JSON_CONTAINER = json.dumps(
    {
        "info": {
            "si": "classic",
            "v": 1,
            "user": {"userID": 103603, "nickname": "ventkx86"},
            "type": "planet-overview",
            "universe": {"id": 31},
            "ts": 1794704488.5,
        },
        "planets": [
            {
                "id": 12476,
                "type": "normal",
                "name": "Colony",
                "coords": "02:380:12",
                "temperature": 31,
                "fields": 230,
                "resources": [1297, 50, 845, 0],
                "energy": {"produced": 20, "used": 10},
                "storage": [100000, 100000, 100000, 100000],
                "buildings": {
                    "b1": {"name": "Pig iron mine", "level": 1},
                    "b5": {"name": "Fusion plant", "level": 1},
                },
                "ships": {"s1": {"name": "Small transport", "level": 0}},
                "defense": {"d1": {"name": "Mortar", "level": 0}},
            }
        ],
        "researches": {"r1": {"name": "Mining technology", "level": 2}},
        "specs": {"miner": 0, "fleeter": 0},
    }
)

SAMPLE_BUILDINGS_HTML = """
<html><body><table>
  <tr><td>
    <table>
      <tr><td><table><tr><td class="building3">
        <a href="indexInternal.es?action=internalDetail&type=building&id=1&sid=abc">Pig iron mine</a>
      </td></tr></table></td></tr>
      <tr><td>Required: 2.000 Pig iron 800 Metal 25 Kryptonite</td></tr>
      <tr><td class="mm_build">
        <a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=1&sid=abc#1">Build level 2</a>
      </td></tr>
    </table>
  </td></tr>
  <tr>
    <td><a href="indexInternal.es?action=internalDetail&type=building&id=4&sid=abc">Spice mine</a></td>
    <td>Required: 220 Metal 80 Kryptonite</td>
    <td>Not enough resources</td>
  </tr>
</table></body></html>
"""

SAMPLE_HOME_HTML = """
<html><body>
  <script>SI_TPL.headerLoad={planetID:12476};</script>
  <div>Up to 10.000 points,no attacks can be launched on you!</div>
  <div>active factor: x20.0</div>
  <div>Tutorial 1 / 11</div>
    <div id="tutorial.text">
      Increase your Fusion plant level 1 / 3
      Increase your Pig iron mine level 0 / 3
      Increase your Kryptonite mine level 0 / 3
      You receive the following resources: 5232 Pig iron, 900 Metal, 2758 Kryptonite, 200 Spice
      <form action="indexInternal.es?action=internalHome" method="POST">
        <input name="tutorialComplete" type="hidden" value="1"/>
        <input type="submit" value="Mission complete"/>
      </form>
    </div>
  <div id="sp-own-building">Pig iron mine (Level 2) 00:01:10</div>
</body></html>
"""

SAMPLE_MENU_HTML = """
<html><body>
  <a href="indexInternal.es?action=internalMessages&sid=abc">no new messages</a>
  <a href="indexInternal.es?action=internalMessages&type=normal&sid=abc">User (2)</a>
  <a href="indexInternal.es?action=internalMessages&type=spio&sid=abc">Spying</a>
</body></html>
"""


class TestStateParsers(unittest.TestCase):
    def test_parse_si_int_handles_dot_grouped_numbers(self) -> None:
        self.assertEqual(parse_si_int("2.000"), 2000)
        self.assertEqual(parse_si_int("7.883.995.585.380"), 7883995585380)
        self.assertEqual(parse_si_int("80"), 80)

    def test_parse_planets_overview_json(self) -> None:
        state = parse_planets_overview_json(SAMPLE_JSON_CONTAINER)
        self.assertEqual(state.universe_id, 31)
        self.assertEqual(state.nickname, "ventkx86")
        self.assertEqual(len(state.planets), 1)
        p = state.planets[0]
        self.assertEqual(p.planet_id, 12476)
        self.assertEqual(p.fields_used, 2)
        self.assertEqual(p.fields_max, 230)
        self.assertEqual(p.resources.pig_iron, 1297)
        self.assertEqual(p.energy.net, 10)
        self.assertEqual(p.building_level(1), 1)
        self.assertEqual(state.researches["r1"], 2)

    def test_parse_buildings_page_extracts_costs_and_build_links(self) -> None:
        offers = parse_buildings_page(SAMPLE_BUILDINGS_HTML, {"b1": 1, "b4": 0})
        self.assertEqual(len(offers), 2)

        pig = offers[0]
        self.assertEqual(pig.building_id, 1)
        self.assertEqual(pig.target_level, 2)
        self.assertTrue(pig.can_build)
        self.assertEqual(pig.cost.pig_iron, 2000)
        self.assertEqual(pig.cost.metal, 800)
        self.assertEqual(pig.cost.kryptonite, 25)
        self.assertIn("buildingID=1", pig.build_href or "")

        spice = offers[1]
        self.assertEqual(spice.building_id, 4)
        self.assertFalse(spice.can_build)
        self.assertEqual(spice.status_text, "Not enough resources")
        self.assertEqual(spice.cost.metal, 220)
        self.assertEqual(spice.cost.kryptonite, 80)

    def test_parse_tutorial_and_home_metadata(self) -> None:
        tut = parse_tutorial_from_home(SAMPLE_HOME_HTML)
        self.assertIsNotNone(tut)
        assert tut is not None
        self.assertEqual(tut.step, 1)
        self.assertEqual(tut.total_steps, 11)
        self.assertEqual(len(tut.tasks), 3)
        self.assertEqual(tut.tasks[0].building_id, 5)
        self.assertEqual(tut.tasks[0].current, 1)
        self.assertEqual(tut.tasks[0].target, 3)
        self.assertFalse(tut.tasks[0].completed)
        self.assertTrue(tut.can_complete)

        pid, spd, newbie, queues, alerts = parse_home_metadata(SAMPLE_HOME_HTML)
        self.assertEqual(pid, 12476)
        self.assertEqual(spd, 20.0)
        self.assertTrue(newbie)
        self.assertEqual(len(queues), 1)
        self.assertEqual(queues[0].category, "building")
        self.assertEqual(alerts, ())

    def test_parse_menu_messages(self) -> None:
        counts = parse_menu_messages(SAMPLE_MENU_HTML)
        self.assertEqual(counts["total"], 0)
        self.assertEqual(counts["normal"], 2)
        self.assertEqual(counts["spio"], 0)

    def test_parse_building_queue_and_build_card_divs(self) -> None:
        html = """
        <html><body>
          <table class="ecoshadow">
            <tr><td>Construction queue ( 2 Elements )</td></tr>
            <tr><td>Fusion plant (Level 2)</td></tr>
            <tr><td>Pig iron mine ( Level 1 ) abort 00:00:05</td></tr>
          </table>
          <div class="ecoshadow build-card b-5">
            <a href="indexInternal.es?action=internalDetail&type=building&id=5">Fusion plant</a>
            Required: 183 Pig iron 80 Kryptonite
            <a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=5#5">Build level 3</a>
          </div>
        </body></html>
        """
        queue = parse_building_queue(html)
        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0].building_id, 5)
        self.assertEqual(queue[0].target_level, 2)
        self.assertEqual(queue[1].building_id, 1)
        self.assertEqual(queue[1].target_level, 1)

        offers = parse_buildings_page(html, {"b5": 1})
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].target_level, 3)
        self.assertEqual(offers[0].cost.pig_iron, 183)

    def test_parse_research_shipyard_and_tutorial4_research_tasks(self) -> None:
        res_html = """
        <table><tr><td>
          <a class="menu" href="indexInternal.es?action=internalDetail&type=research&id=2&sid=abc">Energy technology</a>
          <b>Required:</b><b>400</b> Metal <b>800</b> Kryptonite <b>400</b> Spice
          <a class="bau" href="indexInternal.es?action=internalBuildItem&researchID=2&sid=abc">Research level 1</a>
        </td></tr></table>
        """
        r_offers = parse_research_page(res_html, {"r2": 0})
        self.assertEqual(len(r_offers), 1)
        self.assertEqual(r_offers[0].research_key, "r2")
        self.assertEqual(r_offers[0].cost.kryptonite, 800)
        self.assertTrue(r_offers[0].can_research)

        wf_html = """
        <form action="indexInternal.es?action=internalBuildItem&buildShip=1" method="POST">
          <table><tr><td>
            <a href="indexInternal.es?action=internalDetail&type=ship&id=1">Small transport</a>
            Required: 3.000 Metal 3.000 Kryptonite
            <input type="text" name="ship[1]" value="0"/>
          </td></tr></table>
        </form>
        """
        u_offers = parse_unit_factory_page(wf_html, unit_type="ship", owned_counts={"s1": 2})
        self.assertEqual(len(u_offers), 1)
        self.assertEqual(u_offers[0].input_name, "ship[1]")
        self.assertEqual(u_offers[0].cost.metal, 3000)
        self.assertEqual(u_offers[0].owned, 2)

        tut4_html = """
        <div>Tutorial 4 / 11</div>
        <div id="tutorial.text">
          Increase your Pig iron mine level 0 / 2
          Upgrade your Energy technology 0 / 2
          Upgrade your Engine research 0 / 2
          You receive the following resources: 900 Pig iron
        </div>
        """
        tut = parse_tutorial_from_home(tut4_html)
        self.assertIsNotNone(tut)
        assert tut is not None
        self.assertEqual(len(tut.tasks), 3)
        self.assertEqual(tut.tasks[1].category, "research")
        self.assertEqual(tut.tasks[1].research_id, "r2")
        self.assertEqual(tut.tasks[2].research_id, "r3")

    def test_parse_menu_status_extracts_points_and_rank(self) -> None:
        html = """
        <div>23:47:02 ventkx86 Points 1.250 # 393
          <a href="indexInternal.es?action=internalMessages">no new messages</a>
        </div>
        """
        counts, pts, rank = parse_menu_status(html)
        self.assertEqual(counts["total"], 0)
        self.assertEqual(pts, 1250)
        self.assertEqual(rank, 393)


if __name__ == "__main__":
    unittest.main()
