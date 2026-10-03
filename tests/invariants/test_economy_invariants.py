"""Core SpaceInvasion Classic economy, tutorial ordering, and advisory invariants.

Tagged ``@pytest.mark.invariants``. These tests protect fundamental economy and
planner invariants through public surfaces and must never be deleted.
"""

from __future__ import annotations

import unittest

import pytest

from spaceinvasion.combat.ranking import estimate_planet_hourly_economy
from spaceinvasion.core import game_data
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    TutorialStatus,
    TutorialTask,
    UnitOffer,
)
from spaceinvasion.planner.strategy import plan_actions

pytestmark = pytest.mark.invariants


@pytest.mark.invariants
class TestEconomyInvariants(unittest.TestCase):
    """Blast furnace 2:1, Mining tech exclusion, tutorial Fusion bucket=4, and advisory executable_now=False."""

    def test_blast_furnace_2_to_1_pig_iron_and_not_boosted_by_mining_tech(self) -> None:
        # Exact 2:1 Pig iron consumption per Metal produced across Blast furnace levels,
        # and zero Mining technology (r1) bonus on b2 (unlike b1/b3/b4).
        for lvl in (1, 3, 5, 10, 15, 20):
            met_r0 = game_data.mine_production_per_hour(2, lvl, mining_tech_level=0)
            met_r15 = game_data.mine_production_per_hour(2, lvl, mining_tech_level=15)
            pig_used = game_data.blast_furnace_pig_iron_used(lvl)
            self.assertGreater(met_r0, 0.0)
            self.assertEqual(met_r0, met_r15, f"Blast furnace L{lvl} must not be boosted by Mining tech")
            self.assertAlmostEqual(pig_used, 2.0 * met_r0, places=6)

        # Contrast: Pig iron (b1), Kryptonite (b3), and Spice (b4) mines ARE boosted +2%/lvl by r1.
        for bid in (1, 3, 4):
            base = game_data.mine_production_per_hour(bid, 10, temperature=20, mining_tech_level=0)
            boosted = game_data.mine_production_per_hour(bid, 10, temperature=20, mining_tech_level=10)
            self.assertAlmostEqual(boosted, base * 1.20, places=6)

        # Planet hourly economy preserves exact 2:1 ratio and Mining tech exclusion end-to-end.
        planet = PlanetState(
            planet_id=101,
            name="Smelter",
            coords="02:380:12",
            planet_type="normal",
            temperature=25,
            fields_used=20,
            fields_max=230,
            resources=Resources(50_000, 10_000, 10_000, 5_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=500, used=200),
            buildings={"b1": 8, "b2": 6, "b3": 5, "b4": 4, "b5": 10},
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
        )
        state_r0 = GameState(31, 1, "Cmdr", 1_700_000_000.0, (planet,), active_planet_id=101, researches={"r1": 0})
        state_r20 = GameState(31, 1, "Cmdr", 1_700_000_000.0, (planet,), active_planet_id=101, researches={"r1": 20})

        econ_r0 = estimate_planet_hourly_economy(state_r0, planet)
        econ_r20 = estimate_planet_hourly_economy(state_r20, planet)

        self.assertEqual(econ_r0["metal_per_hr"], econ_r20["metal_per_hr"])
        self.assertEqual(econ_r0["blast_furnace_pig_used_per_hr"], econ_r20["blast_furnace_pig_used_per_hr"])
        self.assertAlmostEqual(econ_r0["blast_furnace_pig_used_per_hr"], 2.0 * econ_r0["metal_per_hr"], places=1)
        self.assertAlmostEqual(
            econ_r0["pig_iron_per_hr"],
            econ_r0["pig_iron_gross_per_hr"] - econ_r0["blast_furnace_pig_used_per_hr"],
            places=1,
        )
        self.assertGreater(econ_r20["pig_iron_gross_per_hr"], econ_r0["pig_iron_gross_per_hr"])

    def test_tutorial_defers_non_urgent_fusion_plant_behind_mines_when_energy_positive(self) -> None:
        # Comfortably positive net energy (+200): non-urgent Fusion plant (b5) stays in bucket=4,
        # AFTER mines (bucket=3) and Development center (bucket=1).
        offers = (
            BuildingOffer(1, "Pig iron mine", 2, 3, Resources(200, 0, 100, 0), True, "Build", "href_b1"),
            BuildingOffer(3, "Kryptonite mine", 2, 3, Resources(200, 0, 100, 0), True, "Build", "href_b3"),
            BuildingOffer(5, "Fusion plant", 3, 4, Resources(200, 0, 100, 0), True, "Build", "href_b5"),
            BuildingOffer(7, "Development center", 1, 2, Resources(400, 200, 100, 0), True, "Build", "href_b7"),
        )
        tutorial = TutorialStatus(
            step=2,
            total_steps=11,
            tasks=(
                TutorialTask("Upgrade Fusion plant 3/4", "Fusion plant", 5, 3, 4, "building"),
                TutorialTask("Upgrade Pig iron mine 2/3", "Pig iron mine", 1, 2, 3, "building"),
                TutorialTask("Upgrade Kryptonite mine 2/3", "Kryptonite mine", 3, 2, 3, "building"),
                TutorialTask("Upgrade Development center 1/2", "Development center", 7, 1, 2, "building"),
            ),
        )
        planet_surplus = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=230,
            resources=Resources(50_000, 50_000, 50_000, 10_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=300, used=100),  # net=+200, comfortably positive
            buildings={"b1": 2, "b3": 2, "b5": 3, "b7": 1},
            building_offers=offers,
        )
        state_surplus = GameState(
            31, 1, "Cmdr", 1_700_000_000.0, (planet_surplus,), active_planet_id=101, tutorial=tutorial
        )
        acts_surplus = plan_actions(state_surplus, config=StrategyConfig(planner_mode="priority"))
        tut_build_ids = [
            a.target_id
            for a in acts_surplus
            if a.kind == "BUILD" and "Tutorial" in a.reason
        ]
        self.assertIn(5, tut_build_ids)
        self.assertIn(1, tut_build_ids)
        self.assertIn(3, tut_build_ids)
        self.assertIn(7, tut_build_ids)
        # Development center (bucket=1) < mines (bucket=3) < non-urgent Fusion plant (bucket=4)
        self.assertLess(tut_build_ids.index(7), tut_build_ids.index(1))
        self.assertLess(tut_build_ids.index(1), tut_build_ids.index(5))
        self.assertLess(tut_build_ids.index(3), tut_build_ids.index(5))

        # Contrast: when net energy is negative/starving, Fusion plant is promoted to bucket=0 (before mines).
        planet_deficit = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=230,
            resources=Resources(50_000, 50_000, 50_000, 10_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=90, used=100),  # net=-10
            buildings={"b1": 2, "b3": 2, "b5": 3, "b7": 1},
            building_offers=offers,
        )
        state_deficit = GameState(
            31, 1, "Cmdr", 1_700_000_000.0, (planet_deficit,), active_planet_id=101, tutorial=tutorial
        )
        acts_deficit = plan_actions(state_deficit, config=StrategyConfig(planner_mode="priority"))
        build_ids_deficit = [a.target_id for a in acts_deficit if a.kind == "BUILD"]
        self.assertGreater(len(build_ids_deficit), 0)
        self.assertEqual(build_ids_deficit[0], 5)

    def test_non_building_advisories_without_live_dom_links_keep_executable_now_false(self) -> None:
        # Planet with locked/non-actionable offers (no live href / can_build=False),
        # hostile alert, and non-building tutorial advisory.
        planet = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=230,
            resources=Resources(50, 0, 50, 0),  # unaffordable -> WAIT_RESOURCES advisories
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(produced=200, used=100),
            buildings={"b1": 3, "b5": 5, "b7": 2, "b8": 1, "b9": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 3, 4, Resources(500, 0, 200, 0), False, "Need resources", None),
            ),
            ship_offers=(
                UnitOffer(4, "Small fighter", 0, Resources(3000, 1000, 0, 0), False, "Locked", None),
            ),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Cmdr",
            timestamp=1_700_000_000.0,
            planets=(planet,),
            active_planet_id=101,
            fleet_alerts=(FleetAlert(mission="Attack", summary="Hostile inbound", hostile=True),),
            research_offers=(
                ResearchOffer("r2", "Energy technology", 0, 1, Resources(0, 800, 400, 0), False, "Locked", None),
            ),
            tutorial=TutorialStatus(
                step=4,
                total_steps=11,
                tasks=(
                    TutorialTask("Rename your home planet 0/1", "Rename planet", None, 0, 1, "other"),
                ),
            ),
        )
        actions = plan_actions(
            state,
            config=StrategyConfig(planner_mode="priority", allow_fleetsave=False, allow_shipyard=True),
        )
        self.assertGreater(len(actions), 0)
        for act in actions:
            if act.kind in ("ALERT_HOSTILE", "WAIT_RESOURCES", "TUTORIAL_TASK", "NONE"):
                self.assertFalse(
                    act.executable_now,
                    f"Advisory action {act.kind} ({act.target_name}) must keep executable_now=False",
                )
            if act.kind in ("RESEARCH", "BUILD_SHIP") and not act.action_href:
                self.assertFalse(
                    act.executable_now,
                    f"Non-building action {act.kind} without live DOM href must keep executable_now=False",
                )
