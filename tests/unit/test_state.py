"""Unit tests for SpaceInvasion state JSON/HTML parsers."""

from __future__ import annotations

from dataclasses import asdict, fields as dataclass_fields
import json
import unittest

from spaceinvasion.state import (
    GameState,
    game_state_from_dict,
    parse_building_queue,
    parse_buildings_page,
    parse_economy_rates,
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

    def test_parse_economy_rates_extracts_selects_and_roundtrips(self) -> None:
        econ_html = """
        <form id="ress" action="indexInternal.es?action=internalEconomy" method="POST">
          <select name="resource1">
            <option value="100" selected="selected">100%</option>
            <option value="0">0%</option>
          </select>
          <select name="resource2">
            <option value="100">100%</option>
            <option value="0" selected="selected">0%</option>
          </select>
          <select name="resource3">
            <option value="80" selected>80%</option>
          </select>
          <select name="resource4">
            <option value="100" selected>100%</option>
          </select>
        </form>
        """
        rates = parse_economy_rates(econ_html)
        self.assertEqual(
            rates,
            {"resource1": 100, "resource2": 0, "resource3": 80, "resource4": 100},
        )
        gs = parse_planets_overview_json(SAMPLE_JSON_CONTAINER)
        raw = {
            "universe_id": gs.universe_id,
            "user_id": gs.user_id,
            "nickname": gs.nickname,
            "timestamp": gs.timestamp,
            "planets": [
                {
                    **{
                        "planet_id": gs.planets[0].planet_id,
                        "name": gs.planets[0].name,
                        "coords": gs.planets[0].coords,
                        "planet_type": gs.planets[0].planet_type,
                        "temperature": gs.planets[0].temperature,
                        "fields_used": gs.planets[0].fields_used,
                        "fields_max": gs.planets[0].fields_max,
                        "resources": gs.planets[0].resources.__dict__,
                        "storage": gs.planets[0].storage.__dict__,
                        "energy": gs.planets[0].energy.__dict__,
                        "buildings": gs.planets[0].buildings,
                        "economy_rates": rates,
                    }
                }
            ],
        }
        rt = game_state_from_dict(raw)
        self.assertEqual(rt.planets[0].economy_rates["resource2"], 0)

    def test_parse_tutorial_step6_ships_and_combat_milestones(self) -> None:
        html = """
        <div>Tutorial 6 / 11</div>
        <div id="tutorial.text">
          <table><tr><td>
            <table width="100%">
              <tr><td>Build a Small transport</td><td><font color="#E54C4C">1 / 2</font></td></tr>
              <tr><td>Have a successful fight</td><td><font color="#E54C4C">0 / 1</font></td></tr>
            </table>
          </td></tr></table>
          You receive the following resources: 10000 Pig iron, 10000 Metal, 10000 Kryptonite, 2000 Spice
        </div>
        """
        tut = parse_tutorial_from_home(html)
        self.assertIsNotNone(tut)
        self.assertEqual(tut.step, 6)
        self.assertEqual(len(tut.tasks), 2)
        self.assertEqual(tut.tasks[0].category, "ship")
        self.assertEqual(tut.tasks[0].building_id, 1)
        self.assertEqual((tut.tasks[0].current, tut.tasks[0].target), (1, 2))
        self.assertEqual(tut.tasks[1].category, "milestone")
        self.assertEqual((tut.tasks[1].current, tut.tasks[1].target), (0, 1))

    def test_game_state_raid_stats_default_roundtrip_and_corrupt_fallbacks(self) -> None:
        self.assertFalse(hasattr(GameState, "raid_stats"))
        self.assertIn("raid_stats", {f.name for f in dataclass_fields(GameState)})

        gs1 = parse_planets_overview_json(SAMPLE_JSON_CONTAINER)
        gs2 = GameState(31, 1, "u", 0.0, gs1.planets)
        self.assertEqual(gs1.raid_stats, {})
        self.assertEqual(gs2.raid_stats, {})
        self.assertIsNot(gs1.raid_stats, gs2.raid_stats)

        sample_stats = {
            "overall": {"attempts": 4, "wins": 3, "losses": 1, "success_rate": 0.75},
            "per_target": {"02:380:04": {"attempts": 4, "wins": 3, "success_rate": 0.75}},
            "history": [{"target_coords": "02:380:04", "attacked_ts": 1000.0, "outcome": "win"}],
            "telemetry_summary": {"rank_snapshots_count": 2},
        }
        gs_with_stats = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1_700_000_000.0,
            planets=gs1.planets,
            active_planet_id=12476,
            raid_targets=({"coords": "02:380:04", "status_color": "yellow"},),
            raid_stats=sample_stats,
        )
        raw = asdict(gs_with_stats)
        rebuilt = game_state_from_dict(raw)
        self.assertEqual(rebuilt, gs_with_stats)
        self.assertIsNot(rebuilt.raid_stats, raw["raid_stats"])
        self.assertIsNot(rebuilt.raid_stats["overall"], raw["raid_stats"]["overall"])
        self.assertIsNot(rebuilt.raid_stats["per_target"], raw["raid_stats"]["per_target"])
        self.assertIsNot(rebuilt.raid_stats["history"], raw["raid_stats"]["history"])

        rebuilt.raid_stats["overall"]["attempts"] = 999
        rebuilt.raid_stats["per_target"]["02:380:04"]["wins"] = 99
        rebuilt.raid_stats["history"].append({"target_coords": "02:380:99"})
        self.assertEqual(raw["raid_stats"]["overall"]["attempts"], 4)
        self.assertEqual(raw["raid_stats"]["per_target"]["02:380:04"]["wins"], 3)
        self.assertEqual(len(raw["raid_stats"]["history"]), 1)

        for bad_val in (None, [], "corrupt", 42):
            bad_dict = {**raw, "raid_stats": bad_val}
            recovered = game_state_from_dict(bad_dict)
            self.assertEqual(recovered.raid_stats, {})
        omitted = dict(raw)
        omitted.pop("raid_stats", None)
        self.assertEqual(game_state_from_dict(omitted).raid_stats, {})

    def test_mapping_proxy_normalization_deepcopy_and_numeric_guards(self) -> None:
        import copy
        from dataclasses import asdict
        from types import MappingProxyType

        raw = {
            "universe_id": 31,
            "user_id": 10,
            "nickname": "u",
            "timestamp": 100.0,
            "active_planet_id": 1,
            "planets": [
                MappingProxyType(
                    {
                        "planet_id": 1,
                        "name": "Home",
                        "coords": "01:001:01",
                        "buildings": MappingProxyType({"b1": 4, "b2": "bad", "b3": None, "b4": float("nan")}),
                        "economy_rates": MappingProxyType({"resource1": 100}),
                    }
                )
            ],
            "raid_targets": [MappingProxyType({"coords": "01:001:02", "resources": MappingProxyType({"spice": 50})})],
            "raid_stats": MappingProxyType({"overall": MappingProxyType({"attempts": 1})}),
        }
        gs = game_state_from_dict(raw)
        self.assertIsInstance(gs.raid_stats, dict)
        self.assertIsInstance(gs.raid_stats["overall"], dict)
        self.assertEqual(gs.planets[0].building_level(1), 4)
        self.assertEqual(gs.planets[0].building_level(2), 0)
        self.assertEqual(gs.planets[0].building_level(3), 0)
        self.assertEqual(gs.planets[0].building_level(4), 0)
        gs_copy = copy.deepcopy(gs)
        gs_dict = asdict(gs_copy)
        self.assertEqual(gs_dict["raid_targets"][0]["resources"]["spice"], 50)

        nested_res = {"pig_iron": 123}
        raw_mut = {
            "universe_id": 31,
            "user_id": 10,
            "nickname": "u",
            "timestamp": 100.0,
            "active_planet_id": 1,
            "planets": [],
            "raid_targets": [{"coords": "01:001:02", "resources": nested_res}],
        }
        gs_mut = game_state_from_dict(raw_mut)
        nested_res["pig_iron"] = 999_999
        self.assertEqual(gs_mut.raid_targets[0]["resources"]["pig_iron"], 123)

    def test_game_state_from_dict_accepts_partial_offer_dicts(self) -> None:
        gs_partial = game_state_from_dict(
            {
                "universe_id": 31,
                "user_id": 1,
                "nickname": "u",
                "timestamp": 100.0,
                "planets": [
                    {
                        "planet_id": 1,
                        "name": "P",
                        "coords": "01:010:01",
                        "building_offers": [{"building_id": 1, "name": "Mine"}],
                        "ship_offers": [{"unit_id": 4, "name": "Small fighter"}],
                        "defense_offers": [{"unit_id": 1, "name": "Mortar"}],
                    }
                ],
                "research_offers": [{"research_id": 2, "name": "Energy technology"}],
            }
        )
        self.assertEqual(len(gs_partial.planets), 1)
        b_off = gs_partial.planets[0].building_offers[0]
        self.assertEqual(b_off.building_id, 1)
        self.assertEqual(b_off.current_level, 0)
        self.assertEqual(b_off.target_level, 1)
        self.assertFalse(b_off.can_build)
        s_off = gs_partial.planets[0].ship_offers[0]
        self.assertEqual(s_off.unit_type, "ship")
        self.assertFalse(s_off.can_build)
        d_off = gs_partial.planets[0].defense_offers[0]
        self.assertEqual(d_off.unit_type, "defense")
        self.assertFalse(d_off.can_build)
        r_off = gs_partial.research_offers[0]
        self.assertEqual(r_off.research_key, "r2")
        self.assertEqual(r_off.target_level, 1)
        self.assertFalse(r_off.can_research)

    def test_game_state_from_dict_coerces_corrupt_queues_and_scalars_safely(self) -> None:
        from spaceinvasion.config import StrategyConfig
        from spaceinvasion.state import Resources
        from spaceinvasion.strategy import plan_actions

        corrupt_dict = {
            "universe_id": "31",
            "user_id": "101",
            "username": "u",
            "timestamp": "1000.0",
            "active_planet_id": "bad",
            "speedup_factor": "bad",
            "account_points": "bad",
            "account_rank": "bad",
            "specs": "not_a_dict",
            "planets": [
                {
                    "planet_id": 101,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 25,
                    "fields_used": 10,
                    "fields_max": 200,
                    "resources": {"pig_iron": 2000, "metal": 1000, "kryptonite": 1000, "spice": 500},
                    "storage": {"pig_iron": 100_000, "metal": 100_000, "kryptonite": 100_000, "spice": 100_000},
                    "energy": {"produced": 200, "used": 100},
                    "buildings": {"b1": 2},
                    "building_offers": "bad",
                    "ship_offers": 123,
                    "defense_offers": None,
                }
            ],
            "active_queues": [
                {
                    "planet_id": "bad",
                    "queue_type": "building",
                    "building_id": "bad",
                    "name": "Mine",
                    "target_level": "bad",
                }
            ],
            "fleet_alerts": "bad",
            "research_offers": 99,
            "raid_targets": "bad",
            "tutorial": {
                "completed": False,
                "current_step": "bad",
                "tasks": [{"step": 1, "title": "Build", "building_id": "bad", "completed": False}],
            },
        }
        gs = game_state_from_dict(corrupt_dict)
        self.assertIsNone(gs.active_planet_id)
        self.assertIsNone(gs.speedup_factor)
        self.assertIsNone(gs.account_points)
        self.assertIsNone(gs.active_queues[0].building_id)
        self.assertIsNone(gs.active_queues[0].target_level)
        self.assertIsNone(gs.tutorial.tasks[0].building_id)
        p_acts = plan_actions(gs, config=StrategyConfig(planner_mode="priority"))
        self.assertIsInstance(p_acts, list)
        ga_acts = plan_actions(gs, config=StrategyConfig(planner_mode="ga_nn", ga_population_size=4, ga_generations=1))
        self.assertIsInstance(ga_acts, list)

        gs_scalar_containers = game_state_from_dict(
            {
                "planets": 123,
                "active_queues": "bad",
                "fleet_alerts": 456,
                "tutorial": {"tasks": 789},
            }
        )
        self.assertEqual(gs_scalar_containers.planets, ())
        self.assertEqual(gs_scalar_containers.active_queues, ())
        self.assertEqual(gs_scalar_containers.fleet_alerts, ())
        self.assertEqual(gs_scalar_containers.tutorial.tasks, ())

        huge_short = Resources(pig_iron=10**1_000_000).format_short()
        self.assertIn("~1e", huge_short)

        from spaceinvasion.state import EnergyBalance, PlanetState

        corrupt_res = Resources(
            pig_iron=-(10**5000),  # type: ignore[arg-type]
            metal="bad",  # type: ignore[arg-type]
            kryptonite=float("nan"),  # type: ignore[arg-type]
            spice=10**400,
        )
        corrupt_short = corrupt_res.format_short()
        self.assertIsInstance(corrupt_short, str)
        self.assertIn("~1e", corrupt_short)
        bad_planet = PlanetState(
            planet_id=1,
            name="P",
            coords="01:001:01",
            planet_type="normal",
            temperature="bad",  # type: ignore[arg-type]
            fields_used=0,
            fields_max=100,
            resources=corrupt_res,
            storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance("bad", float("nan")),  # type: ignore[arg-type]
            buildings={"b1": "bad", "b2": 10**5000},  # type: ignore[dict-item]
        )
        self.assertEqual(bad_planet.building_level(1), 0)
        self.assertEqual(bad_planet.building_level(2), 10**5000)

    def test_storage_fill_ratios_handles_huge_and_degenerate_values(self) -> None:
        import math
        from spaceinvasion.state import EnergyBalance, PlanetState, Resources

        for huge in (10**308, 10**400, 10**1_000_000):
            planet = PlanetState(
                planet_id=101,
                name="Colony",
                coords="01:001:01",
                planet_type="normal",
                temperature=20,
                fields_used=1,
                fields_max=200,
                resources=Resources(huge, huge, huge, huge),
                storage=Resources(100000, 100000, 100000, 100000),
                energy=EnergyBalance(100, 50),
            )
            ratios = planet.storage_fill_ratios()
            for key in ("pig_iron", "metal", "kryptonite", "spice"):
                self.assertTrue(math.isfinite(ratios[key]))
                self.assertGreater(ratios[key], 1.0)

        zero_storage = PlanetState(
            planet_id=102,
            name="Zero",
            coords="01:001:02",
            planet_type="normal",
            temperature=20,
            fields_used=1,
            fields_max=200,
            resources=Resources(10**400, 0, 500, 100),
            storage=Resources(0, 0, -5, float("nan")),  # type: ignore[arg-type]
            energy=EnergyBalance(0, 0),
        )
        zr = zero_storage.storage_fill_ratios()
        self.assertEqual(zr, {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0})

        equal_huge = PlanetState(
            planet_id=103,
            name="EqualHuge",
            coords="01:001:03",
            planet_type="normal",
            temperature=20,
            fields_used=1,
            fields_max=200,
            resources=Resources(10**400, 10**400, 10**400, 10**400),
            storage=Resources(10**400, 10**400, 10**400, 10**400),
            energy=EnergyBalance(0, 0),
        )
        er = equal_huge.storage_fill_ratios()
        self.assertEqual(er, {"pig_iron": 1.0, "metal": 1.0, "kryptonite": 1.0, "spice": 1.0})

    def test_html_parsers_and_from_dict_resist_5000_digit_and_malformed_numbers(self) -> None:
        huge = "9" * 5000
        q_html = (
            f"<table><tr><th>Construction queue ( 1 Elements )</th></tr>"
            f"<tr><td>Pig iron mine (Level {huge})</td><td>{huge}:01:02</td></tr></table>"
        )
        q_jobs = parse_building_queue(q_html)
        self.assertEqual(len(q_jobs), 1)
        self.assertEqual(q_jobs[0].building_id, 1)
        self.assertEqual(q_jobs[0].target_level, 10**200)

        b_html = (
            f'<div class="build-card b-{huge}"><span class="title">Mine (Level {huge})</span></div>'
            f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=building&id={huge}">Mine</a></td>'
            f'<td>Required: 100 Pig iron</td>'
            f'<td><a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID={huge}#{huge}">Build level {huge}</a></td>'
            f"</tr></table>"
        )
        b_offers = parse_buildings_page(b_html, {})
        self.assertIsInstance(b_offers, tuple)
        self.assertTrue(all(o.building_id <= 10**200 for o in b_offers))

        r_html = (
            f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=research&id={huge}">Mining</a></td>'
            f'<td><a class="bau" href="indexInternal.es?action=internalResearch&researchID={huge}">Research level {huge}</a></td>'
            f"</tr></table>"
        )
        r_offers = parse_research_page(r_html, {})
        self.assertIsInstance(r_offers, tuple)
        self.assertTrue(all(o.research_id <= 10**200 for o in r_offers))

        u_html = (
            f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id={huge}">Ship</a></td>'
            f'<td><form><input name="shipID" value="{huge}"/><input name="count" value="{huge}"/></form></td>'
            f"</tr></table>"
        )
        u_offers = parse_unit_factory_page(u_html, unit_type="ship")
        self.assertIsInstance(u_offers, tuple)
        self.assertTrue(all(o.unit_id <= 10**200 for o in u_offers))

        t_html = (
            f'<div>Tutorial {huge} / {huge}</div>'
            f'<div id="tutorial.text">Increase your Pig iron mine level {huge} / {huge}'
            f'<form><input name="tutorialComplete" value="{huge}"/></form></div>'
        )
        tut = parse_tutorial_from_home(t_html)
        self.assertIsNotNone(tut)

        _, spd_bad, _, _, _ = parse_home_metadata(
            f"<script>SI_TPL.headerLoad={{planetID:{huge}}};</script>"
            f"<div>Up to {huge} points,no attacks</div><div>active factor: x1.2.3</div>"
        )
        self.assertIsNone(spd_bad)

        _, spd_inf, _, _, _ = parse_home_metadata(f"<div>active factor: x{'9' * 500}</div>")
        self.assertIsNone(spd_inf)

        counts, pts, rank = parse_menu_status(f'<a href="x">User ({huge})</a> Points {huge} # {huge}')
        self.assertIsInstance(counts, dict)
        self.assertIsInstance(pts, int)
        self.assertIsInstance(rank, int)

        st = game_state_from_dict({"research_offers": [{"research_id": 10**5000, "name": "Bad"}]})
        self.assertIsInstance(st, GameState)

    def test_r9_rejects_arabic_indic_parsers_and_clamps_negative_json_levels(self) -> None:
        self.assertEqual(parse_si_int("2.000"), 2000)
        self.assertEqual(parse_si_int("\u0662.\u0660\u0660\u0660"), 0)
        self.assertEqual(
            parse_economy_rates(
                '<select name="resource\u0662"><option selected value="80">80%</option></select>'
                '<select name="resource1"><option selected value="\u0668\u0660">80%</option></select>'
            ),
            {"resource1": 0},
        )
        gs_neg = parse_planets_overview_json(
            json.dumps(
                {
                    "planets": [
                        {
                            "id": 1,
                            "name": "P",
                            "coords": "1:2:3",
                            "fields": -20,
                            "buildings": {"b1": {"level": -10}, "b2": {"level": -5}},
                            "ships": {"s4": {"level": -3}},
                            "defense": {"d1": {"level": -2}},
                        }
                    ],
                    "researches": {"r1": {"level": -4}},
                }
            )
        )
        pl = gs_neg.active_planet()
        self.assertIsNotNone(pl)
        assert pl is not None
        self.assertEqual(pl.fields_used, 0)
        self.assertEqual(pl.fields_max, 0)
        self.assertEqual(pl.fields_remaining, 0)
        self.assertEqual(pl.buildings["b1"], 0)
        self.assertEqual(pl.ships["s4"], 0)
        self.assertEqual(pl.defense["d1"], 0)
        self.assertEqual(gs_neg.researches["r1"], 0)

    def test_r10_negative_clamping_bytes_safe_key_and_signed_preservations(self) -> None:
        from spaceinvasion import state as st_mod
        from spaceinvasion.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            game_state_from_dict,
            parse_buildings_page,
        )

        # Resources.from_list clamps negatives; direct Resources(...) stays signed; EnergyBalance.net stays signed
        self.assertEqual(Resources.from_list([-10, -5, -2, -1]), Resources(0, 0, 0, 0))
        self.assertEqual(Resources(-10, -5, -2, -1).pig_iron, -10)
        self.assertEqual(EnergyBalance(10, 50).net, -40)

        p = PlanetState(
            planet_id=1,
            name="ColdSpice",
            coords="1:2:3",
            planet_type="normal",
            temperature=-110,
            fields_used=-5,
            fields_max=-10,
            resources=Resources(100, 100, 100, 100),
            storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(10, 50),
            buildings={"b1": -3, (10**5000, "x"): 2},  # type: ignore[dict-item]
            ships={"s4": -7},
            defense={"d1": -4},
        )
        self.assertEqual(p.temperature, -110)
        self.assertEqual(p.fields_used, 0)
        self.assertEqual(p.fields_max, 0)
        self.assertEqual(p.fields_remaining, 0)
        self.assertEqual(p.buildings["b1"], 0)
        self.assertEqual(p.building_level(-1), 0)
        self.assertEqual(p.ships["s4"], 0)
        self.assertEqual(p.defense["d1"], 0)
        self.assertEqual(p.energy.net, -40)

        huge_p = PlanetState(
            planet_id=2,
            name="Huge",
            coords="1:2:4",
            planet_type="normal",
            temperature=20,
            fields_used=10**5000,
            fields_max=10**10000,
            resources=Resources(0, 0, 0, 0),
            storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(0, 0),
            buildings={"b2": 10**5000},
        )
        self.assertEqual(huge_p.fields_used, 10**5000)
        self.assertEqual(huge_p.buildings["b2"], 10**5000)

        self.assertEqual(st_mod._safe_state_int(b"42"), 42)
        self.assertEqual(st_mod._safe_state_int("\u0664\u0662"), 0)
        self.assertEqual(st_mod._safe_state_float(b"2.5"), 2.5)
        self.assertEqual(st_mod._safe_state_float("\u0662.\u0665"), 0.0)

        gs = game_state_from_dict(
            {
                "universe_id": 31,
                "user_id": 1,
                "nickname": "u",
                "timestamp": b"1000.5",
                "researches": {"r1": -8, (10**5000, "r"): 2},
                "planets": [
                    {
                        "planet_id": 1,
                        "name": "P",
                        "coords": "1:2:3",
                        "temperature": -110,
                        "fields_used": -4,
                        "fields_max": -9,
                        "energy": {"produced": 5, "used": 20},
                        "buildings": {b"b1": -2},
                    }
                ],
            }
        )
        self.assertEqual(gs.researches["r1"], 0)
        self.assertEqual(gs.planets[0].temperature, -110)
        self.assertEqual(gs.planets[0].fields_used, 0)
        self.assertEqual(gs.planets[0].energy.net, -15)
        offers = parse_buildings_page("", current_levels={"b1": -5})
        self.assertEqual(offers, ())

    def test_r12_normalize_planet_type_blank_path_fallback_and_broken_pathlike(self) -> None:
        from pathlib import Path
        from spaceinvasion import state as st_mod

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _BytesPath:
            def __init__(self, raw: bytes) -> None:
                self._raw = raw

            def __fspath__(self) -> bytes:
                return self._raw

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        self.assertEqual(
            st_mod.normalize_planet_type({"planet_type": Path("   "), "type": " Asteroid "}),
            "asteroid",
        )
        self.assertEqual(
            st_mod.normalize_planet_type({"planet_type": _BytesPath(b"   "), "type": "Raid"}),
            "raid",
        )
        self.assertEqual(
            st_mod.normalize_planet_type({"planet_type": _RaiseFspath(), "type": "Asteroid"}),
            "asteroid",
        )
        self.assertEqual(
            st_mod.normalize_planet_type({"planet_type": _IntPath(), "type": " Raid "}),
            "raid",
        )
        self.assertEqual(st_mod.normalize_planet_type(_IntPath()), "normal")
        self.assertIsInstance(st_mod.normalize_planet_type(_RaiseStrRepr()), str)

        gs = st_mod.game_state_from_dict(
            {
                "planets": [
                    {
                        "planet_id": 1,
                        "name": _RaiseFspath(),
                        "coords": "01:001:01",
                        "planet_type": Path("  "),
                        "type": " Asteroid ",
                    }
                ]
            }
        )
        self.assertEqual(gs.planets[0].planet_type, "asteroid")
        self.assertIsInstance(gs.planets[0].name, str)
        self.assertEqual(st_mod.parse_si_int(_RaiseFspath()), 0)
        self.assertIsInstance(st_mod._safe_state_str(_RaiseStrRepr()), str)
        self.assertIsInstance(st_mod._safe_state_str(_RaiseFspath()), str)
        self.assertIsNotNone(st_mod._safe_optional_state_str(_IntPath()))
        self.assertIsNotNone(st_mod._safe_optional_state_str(_RaiseFspath()))
        self.assertEqual(st_mod._parse_cost_from_text(_RaiseFspath()), st_mod.Resources())
        self.assertEqual(st_mod._parse_cost_from_text(_IntPath()), st_mod.Resources())

    def test_r14_deep_plain_value_and_non_dict_mapping_resist_cycles_and_dag20(self) -> None:
        import time
        from types import MappingProxyType
        from spaceinvasion import state as st_mod

        cyc_list: list[object] = []
        cyc_list.append(cyc_list)
        cyc_dict: dict[str, object] = {}
        cyc_dict["self"] = cyc_dict

        self.assertFalse(st_mod._contains_non_dict_mapping(cyc_list))
        self.assertFalse(st_mod._contains_non_dict_mapping(cyc_dict))

        plain_list = st_mod._deep_plain_value(cyc_list)
        plain_dict = st_mod._deep_plain_value(cyc_dict)
        self.assertEqual(plain_list, ["[Cycle]"])
        self.assertEqual(plain_dict, {"self": "[Cycle]"})

        dag: object = [1, MappingProxyType({"k": 2})]
        for _ in range(20):
            dag = [dag, dag, dag]
        t0 = time.perf_counter()
        has_nd = st_mod._contains_non_dict_mapping(dag)
        plain_dag = st_mod._deep_plain_value(dag)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertTrue(has_nd)
        self.assertIsNotNone(plain_dag)
        self.assertLess(elapsed_ms, 100.0)

    def test_parse_tutorial_steps_7_to_11_variants(self) -> None:
        html_step7 = """
        <div>Tutorial 7 / 11</div>
        <div id="tutorial.text">
          <table>
            <tr><td><img src="r7.png"/></td><td>Upgrade your Spy technology</td><td>0 / 2</td></tr>
            <tr><td><img src="s10.png"/></td><td>Build Spy probes</td><td>0 / 2</td></tr>
            <tr><td><img src="spy.png"/></td><td>Spy on another planet</td><td>0 / 1</td></tr>
          </table>
          You receive the following resources: 5.000 Pig iron 2.500 Kryptonite
        </div>
        """
        tut7 = parse_tutorial_from_home(html_step7)
        self.assertIsNotNone(tut7)
        assert tut7 is not None
        self.assertEqual(tut7.step, 7)
        self.assertEqual(len(tut7.tasks), 3)
        self.assertEqual((tut7.tasks[0].category, tut7.tasks[0].research_id), ("research", "r7"))
        self.assertEqual((tut7.tasks[1].category, tut7.tasks[1].building_id), ("ship", 10))
        self.assertEqual(tut7.tasks[2].category, "milestone")

        html_steps_8_11 = """
        <div>Tutorial 10 / 11</div>
        <div id="tutorial.text">
          Build a Fleet base 0 / 1<br/>
          Build a Solar cell 0 / 5<br/>
          Build 2 Recyclers 0 / 2<br/>
          Build a Colony ship 0 / 1<br/>
          Build a Mortar 0 / 5<br/>
          Recycle a debris field 0 / 1<br/>
          Colonize a planet 0 / 1<br/>
          Send an escape flight 0 / 1
        </div>
        """
        tut10 = parse_tutorial_from_home(html_steps_8_11)
        self.assertIsNotNone(tut10)
        assert tut10 is not None
        by_cat = [(t.category, t.building_id, t.building_name) for t in tut10.tasks]
        self.assertIn(("building", 10, "Fleet base"), by_cat)
        self.assertIn(("ship", 9, "Solar cells"), by_cat)
        self.assertIn(("ship", 11, "Recycler"), by_cat)
        self.assertIn(("ship", 8, "Colony ship"), by_cat)
        milestones = [t.building_name for t in tut10.tasks if t.category == "milestone"]
        self.assertIn("Build a Mortar", milestones)
        self.assertIn("Recycle a debris field", milestones)
        self.assertIn("Colonize a planet", milestones)
        self.assertIn("Send an escape flight", milestones)

        # Live Step 7/11 ("Make an escape flight 0 / 1") + recallFleet fallback when #sp-own-fleet is absent
        live_step7_html = """
        <html><body>
          <script>var cfg = {planetID: 12476};</script>
          <div>Tutorial 7 / 11</div>
          <div id="tutorial.text">
            <table><tr><td>Make an escape flight</td><td>0 / 1</td></tr></table>
          </div>
          <table><tr><td>
            Returning raid 02:383:02 -> 02:380:12 00:02:15
            <form action="indexInternal.es?action=internalHome&recallFleet=991" method="POST">
              <input type="submit" value="Recall"/>
            </form>
          </td></tr></table>
        </body></html>
        """
        tut_live7 = parse_tutorial_from_home(live_step7_html)
        self.assertIsNotNone(tut_live7)
        assert tut_live7 is not None
        self.assertEqual(tut_live7.step, 7)
        self.assertEqual(len(tut_live7.tasks), 1)
        self.assertEqual(tut_live7.tasks[0].category, "milestone")
        self.assertIn("escape flight", tut_live7.tasks[0].description.lower())
        pid7, _, _, queues7, _ = parse_home_metadata(live_step7_html)
        self.assertEqual(pid7, 12476)
        self.assertTrue(any(q.category == "fleet" for q in queues7))

    def test_fadata_origin_coords_and_per_planet_fleet_slot_accounting(self) -> None:
        from spaceinvasion.core.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            QueueJob,
            Resources,
            count_in_flight_fleets_for_planet,
            extract_in_flight_station_dest_coords,
            has_in_flight_fleet_from_planet,
        )

        # Live internalHome var faData includes both outbound ("[02:380:12] Main") and
        # returning ("Main [02:380:12]") start strings, plus a 2-leg duplicate return row.
        fadata_html = """
        <html><body>
        <script>
        var faData = [
          {"title": "Own fleets", "fleets": [
            {"id": 101, "a": "TRANSPORT", "start": "[02:380:12] Home", "dest": "[02:380:08] ColonyA", "return": false, "t": 120},
            {"id": 102, "a": "TRANSPORT", "start": "[02:380:12] Home", "dest": "[02:380:08] ColonyA", "return": true, "t": 360},
            {"id": 103, "a": "STATION", "start": "[02:380:12] Home", "dest": "[02:380:09] ColonyB", "return": false, "t": 240}
          ]},
          {"title": "Returning fleets", "fleets": [
            {"id": 104, "a": "RECYCLE", "start": "ColonyA [02:380:08]", "dest": "[02:380:14] Debris", "return": true, "t": 95}
          ]}
        ];
        </script>
        </body></html>
        """
        _, _, _, queues, _ = parse_home_metadata(fadata_html)
        fleet_jobs = [q for q in queues if q.category == "fleet"]
        # 2-leg Transport deduplicates to 1 active outbound leg + 1 Station + 1 Returning Recycle = 3 flights
        self.assertEqual(len(fleet_jobs), 3)
        by_name = {q.name: q for q in fleet_jobs}
        tr_job = next(q for q in fleet_jobs if "Transport" in q.name)
        st_job = next(q for q in fleet_jobs if "Station" in q.name)
        rec_job = next(q for q in fleet_jobs if "Recycle" in q.name)
        self.assertEqual(tr_job.origin_coords, "02:380:12")
        self.assertEqual(tr_job.remaining_seconds, 120.0)
        self.assertEqual(st_job.origin_coords, "02:380:12")
        self.assertEqual(rec_job.origin_coords, "02:380:08")
        self.assertTrue(by_name)

        p_home = PlanetState(
            planet_id=1,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=30,
            fields_used=50,
            fields_max=230,
            resources=Resources(1000, 1000, 1000, 1000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 200),
        )
        p_col_a = PlanetState(
            planet_id=2,
            name="ColonyA",
            coords="02:380:08",
            planet_type="normal",
            temperature=20,
            fields_used=20,
            fields_max=320,
            resources=Resources(1000, 1000, 1000, 1000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(200, 100),
        )
        p_col_b = PlanetState(
            planet_id=3,
            name="ColonyB",
            coords="02:380:09",
            planet_type="normal",
            temperature=10,
            fields_used=20,
            fields_max=310,
            resources=Resources(1000, 1000, 1000, 1000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(200, 100),
        )
        gs = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=2000.0,
            planets=(p_home, p_col_a, p_col_b),
            active_planet_id=1,
            active_queues=tuple(queues),
        )

        # Strictly per-planet slot attribution
        self.assertEqual(count_in_flight_fleets_for_planet(gs, p_home), 2)
        self.assertEqual(count_in_flight_fleets_for_planet(gs, p_col_a), 1)
        self.assertEqual(count_in_flight_fleets_for_planet(gs, p_col_b), 0)
        self.assertTrue(has_in_flight_fleet_from_planet(gs, p_home))
        self.assertTrue(has_in_flight_fleet_from_planet(gs, p_col_a))
        self.assertFalse(has_in_flight_fleet_from_planet(gs, p_col_b))

        # Station destination extraction
        self.assertEqual(extract_in_flight_station_dest_coords(gs), {"02:380:09"})

        # Roundtrip through game_state_from_dict preserves origin_coords
        from dataclasses import asdict
        gs_rt = game_state_from_dict(asdict(gs))
        self.assertEqual(count_in_flight_fleets_for_planet(gs_rt, p_home), 2)
        self.assertEqual(count_in_flight_fleets_for_planet(gs_rt, p_col_a), 1)
        self.assertEqual(count_in_flight_fleets_for_planet(gs_rt, p_col_b), 0)

        # Legacy QueueJob without origin_coords falls back conservatively to counting on any planet
        gs_legacy = GameState(
            universe_id=31,
            user_id=1,
            nickname="cmdr",
            timestamp=2000.0,
            planets=(p_home, p_col_b),
            active_planet_id=1,
            active_queues=(QueueJob(category="fleet", name="Attack → 02:380:15", remaining_seconds=100.0, detail="Outbound"),),
        )
        self.assertEqual(count_in_flight_fleets_for_planet(gs_legacy, p_home), 1)
        self.assertEqual(count_in_flight_fleets_for_planet(gs_legacy, p_col_b), 1)

    def test_fadata_cargo_parsing_for_returning_attack_recycle_and_transport_roundtrip(self) -> None:
        from dataclasses import asdict
        from spaceinvasion.core.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            game_state_from_dict,
            parse_home_metadata,
        )

        fadata_html = """
        <html><body><script>
        var faData=[
          {"title":"Own fleet","type":"friendly","rows":[
            ,{"id":101,"t":600,"n":10,"tt":"10 Large transport<br><hr>50.000 Pig iron<br>25.000 Metal","a":'TRANSPORT',"c":0,"start":"[02:380:12] Main","dest":"[02:380:08] Colony"},
            {"id":102,"t":900,"n":6,"tt":"6 Small fighter<br><hr>None","a":'ATTACK',"c":0,"start":"[02:380:12] Main","dest":"[02:378:15] Target"}
          ]},
          {"title":"Returning Fleets","type":"friendly","rows":[
            ,{"id":201,"t":1200,"n":10,"tt":"10 Large transport<br><hr>None","a":'TRANSPORT',"c":0,"return":true,"start":"[02:380:12] Main","dest":"[02:380:08] Colony"},
            {"id":202,"t":1800,"n":6,"tt":"6 Small fighter<br><hr>None","a":'ATTACK',"c":0,"return":true,"start":"[02:380:12] Main","dest":"[02:378:15] Target"},
            {"id":203,"t":400,"n":12,"tt":"12 Recycler<br><hr>16.378 Pig iron<br>11.572 Metal<br>10.598 Kryptonite<br>4.784 Spice","a":'RECYCLE',"c":0,"return":true,"start":"Colony [02:380:12]","dest":"[02:381:06] " + "Debris"},
            {"id":204,"t":500,"n":20,"tt":"20 Small transport<br><hr>12.500 Pig iron<br>8.200 Metal","a":'ATTACK',"c":0,"return":true,"start":"Colony [02:380:08]","dest":"[02:377:04] Spacia"},
            {"id":205,"t":700,"n":4,"tt":"4 Recycler<br><hr>None","a":'RECYCLE',"c":0,"return":true,"start":"Colony [02:380:08]","dest":"[02:380:14] Debris"}
          ]}
        ];
        </script></body></html>
        """
        _, _, _, queues, _ = parse_home_metadata(fadata_html, now_ts=1_700_000_000.0)
        fleet_jobs = [q for q in queues if q.category == "fleet"]
        # Outbound TRANSPORT + outbound ATTACK pair with their future returns -> 5 total active flights
        self.assertEqual(len(fleet_jobs), 5)

        rec_full = next(q for q in fleet_jobs if "02:381:06" in q.name)
        self.assertEqual(rec_full.cargo, Resources(16378, 11572, 10598, 4784))

        atk_ret = next(q for q in fleet_jobs if "02:377:04" in q.name)
        self.assertEqual(atk_ret.cargo, Resources(12500, 8200, 0, 0))

        trn_out = next(q for q in fleet_jobs if "Transport →" in q.name)
        self.assertEqual(trn_out.cargo, Resources(50000, 25000, 0, 0))

        # Returning RECYCLE with <hr>None gets explicit 0-cargo Resources so UI distinguishes confirmed empty
        rec_empty = next(q for q in fleet_jobs if "02:380:14" in q.name)
        self.assertEqual(rec_empty.cargo, Resources(0, 0, 0, 0))

        # Outbound ATTACK with <hr>None stays cargo=None
        atk_out = next(q for q in fleet_jobs if "Attack →" in q.name)
        self.assertIsNone(atk_out.cargo)

        # JSON dict round-trip preserves cargo cleanly
        p1 = PlanetState(
            planet_id=1, name="Main", coords="02:380:12", planet_type="normal",
            temperature=20, fields_used=10, fields_max=250,
            resources=Resources(1, 1, 1, 1), storage=Resources(10, 10, 10, 10),
            energy=EnergyBalance(10, 5),
        )
        gs = GameState(
            universe_id=31, user_id=1, nickname="cmdr", timestamp=2000.0,
            planets=(p1,), active_planet_id=1, active_queues=tuple(queues),
        )
        gs_rt = game_state_from_dict(asdict(gs))
        rec_rt = next(q for q in gs_rt.active_queues if "02:381:06" in q.name)
        self.assertEqual(rec_rt.cargo, Resources(16378, 11572, 10598, 4784))
        rec_empty_rt = next(q for q in gs_rt.active_queues if "02:380:14" in q.name)
        self.assertEqual(rec_empty_rt.cargo, Resources(0, 0, 0, 0))
        atk_out_rt = next(q for q in gs_rt.active_queues if "Attack →" in q.name)
        self.assertIsNone(atk_out_rt.cargo)

    def test_queue_job_expected_delivered_roundtrip_and_building_origin_coords(self) -> None:
        from dataclasses import asdict
        from spaceinvasion.core.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            QueueJob,
            Resources,
            game_state_from_dict,
            is_undersized_fresh_colony,
            parse_building_queue,
            parse_home_metadata,
        )

        # Default QueueJob backward compatibility
        q_default = QueueJob(category="fleet", name="Attack → [02:378:15]")
        self.assertIsNone(q_default.expected_cargo)
        self.assertIsNone(q_default.delivered_cargo)
        self.assertFalse(q_default.delivered)

        # Round-trip expected_cargo, delivered_cargo, and delivered flag
        q_exp = QueueJob(
            category="fleet",
            name="Attack → [02:378:15]",
            detail="outbound · 6 Small fighter",
            origin_coords="02:380:12",
            expected_cargo=Resources(5000, 3000, 1000, 500),
        )
        q_del = QueueJob(
            category="fleet",
            name="Transport ↩ [02:380:08]",
            detail="returning · 10 Large transport",
            origin_coords="02:380:12",
            delivered=True,
            delivered_cargo=Resources(40000, 20000, 0, 5000),
        )
        p1 = PlanetState(
            planet_id=101, name="Main", coords="02:380:12", planet_type="normal",
            temperature=20, fields_used=5, fields_max=300,
            resources=Resources(100, 100, 100, 100), storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(50, 10),
        )
        p2 = PlanetState(
            planet_id=102, name="FreshColony", coords="02:380:08", planet_type="normal",
            temperature=20, fields_used=0, fields_max=180,
            resources=Resources(0, 0, 0, 0), storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(0, 0),
        )
        gs = GameState(
            universe_id=31, user_id=1, nickname="cmdr", timestamp=2000.0,
            planets=(p1, p2), active_planet_id=101, active_queues=(q_exp, q_del),
        )
        gs_rt = game_state_from_dict(asdict(gs))
        self.assertEqual(gs_rt.active_queues[0].expected_cargo, Resources(5000, 3000, 1000, 500))
        self.assertFalse(gs_rt.active_queues[0].delivered)
        self.assertTrue(gs_rt.active_queues[1].delivered)
        self.assertEqual(gs_rt.active_queues[1].delivered_cargo, Resources(40000, 20000, 0, 5000))

        # parse_building_queue stamps normalized origin_coords
        bld_html = """
        <table>
          <tr><td>Construction queue</td></tr>
          <tr><td>Pig iron mine (Level 4) <span id="sicntbar-1-1">00:05:00</span> <span id="sicntbar-1-2">20%</span></td></tr>
        </table>
        """
        b_jobs = parse_building_queue(bld_html, now_ts=1_700_000_000.0, origin_coords="2:380:12")
        self.assertEqual(len(b_jobs), 1)
        self.assertEqual(b_jobs[0].origin_coords, "02:380:12")

        # parse_home_metadata propagates origin_coords to building jobs AND sets transport flags
        home_html = """
        <html><body>
        <table><tr><td>Pig iron mine (Level 4)</td><td>00:05:00</td></tr></table>
        <script>
        var faData=[
          {"title":"Own fleet","type":"friendly","rows":[
            {"id":1,"t":300,"n":5,"tt":"5 Large transport<br><hr>10.000 Pig iron","a":'TRANSPORT',"c":0,"start":"[02:380:12] Main","dest":"[02:380:08] Colony"}
          ]},
          {"title":"Returning Fleets","type":"friendly","rows":[
            {"id":2,"t":200,"n":5,"tt":"5 Large transport<br><hr>None","a":'TRANSPORT',"c":0,"return":true,"start":"[02:380:12] Main","dest":"[02:380:09] Colony2"}
          ]}
        ];
        </script></body></html>
        """
        _, _, _, h_queues, _ = parse_home_metadata(home_html, now_ts=1_700_000_000.0, origin_coords="2:380:12")
        h_trn_out = next(q for q in h_queues if q.category == "fleet" and "Transport →" in q.name)
        self.assertEqual(h_trn_out.expected_cargo, Resources(10000, 0, 0, 0))
        self.assertFalse(h_trn_out.delivered)
        h_trn_ret = next(q for q in h_queues if q.category == "fleet" and "Transport" in q.name and "returning" in q.detail.lower())
        self.assertTrue(h_trn_ret.delivered)

        # is_undersized_fresh_colony scopes building queue checks by origin_coords
        gs_b = GameState(
            universe_id=31, user_id=1, nickname="cmdr", timestamp=2000.0,
            planets=(p1, p2), active_planet_id=101, active_queues=b_jobs,
        )
        # p1 has the active building job -> p2 (fresh undersized colony) is STILL detected as undersized!
        self.assertTrue(is_undersized_fresh_colony(p2, gs_b, 300))
        # Building job on p2 itself protects p2 from abandonment
        gs_b2 = GameState(
            universe_id=31, user_id=1, nickname="cmdr", timestamp=2000.0,
            planets=(p1, p2), active_planet_id=101,
            active_queues=(QueueJob(category="building", name="Pig iron mine (Level 1)", origin_coords="02:380:08"),),
        )
        self.assertFalse(is_undersized_fresh_colony(p2, gs_b2, 300))


if __name__ == "__main__":
    unittest.main()
