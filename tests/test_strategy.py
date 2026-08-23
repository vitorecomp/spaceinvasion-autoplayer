"""Unit tests for priority-ordered dry-run strategy planner."""

from __future__ import annotations

from dataclasses import asdict, replace
import unittest
from unittest import mock

from spaceinvasion.config import StrategyConfig
from spaceinvasion.ranking import compute_action_eta
from spaceinvasion.state import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    TutorialStatus,
    TutorialTask,
    UnitOffer,
)
from spaceinvasion.strategy import (
    BLAST_FURNACE,
    FUSION_PLANT,
    KRYPTONITE_MINE,
    PIG_IRON_MINE,
    PIG_IRON_WAREHOUSE,
    SOLAR_CELLS_ID,
    SPICE_MINE,
    THERMOSOLAR_PLANT,
    WEAPONS_FACTORY,
    PlannedAction,
    plan_actions,
)


def _sample_planet(
    *,
    resources: Resources = Resources(pig_iron=1200, metal=0, kryptonite=800, spice=0),
    storage: Resources = Resources(100000, 100000, 100000, 100000),
    energy: EnergyBalance = EnergyBalance(0, 0),
    buildings: dict[str, int] | None = None,
    offers: tuple[BuildingOffer, ...] = (),
) -> PlanetState:
    return PlanetState(
        planet_id=12476,
        name="Colony",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=0,
        fields_max=230,
        resources=resources,
        storage=storage,
        energy=energy,
        buildings=buildings or {},
        building_offers=offers,
    )


class TestStrategyPlanner(unittest.TestCase):
    def test_tutorial_step1_prioritizes_fusion_plant_first_when_energy_zero(self) -> None:
        offers = (
            BuildingOffer(
                building_id=PIG_IRON_MINE,
                name="Pig iron mine",
                current_level=0,
                target_level=1,
                cost=Resources(78, 0, 25, 0),
                can_build=True,
                status_text="Build level 1",
                build_href="indexInternal.es?action=internalBuildItem&buildingID=1",
            ),
            BuildingOffer(
                building_id=KRYPTONITE_MINE,
                name="Kryptonite mine",
                current_level=0,
                target_level=1,
                cost=Resources(90, 0, 55, 0),
                can_build=True,
                status_text="Build level 1",
                build_href="indexInternal.es?action=internalBuildItem&buildingID=3",
            ),
            BuildingOffer(
                building_id=FUSION_PLANT,
                name="Fusion plant",
                current_level=0,
                target_level=1,
                cost=Resources(80, 0, 35, 0),
                can_build=True,
                status_text="Build level 1",
                build_href="indexInternal.es?action=internalBuildItem&buildingID=5",
            ),
        )
        tut = TutorialStatus(
            step=1,
            total_steps=11,
            tasks=(
                TutorialTask("Increase your Fusion plant level 0 / 3", "Fusion plant", 5, 0, 3),
                TutorialTask("Increase your Pig iron mine level 0 / 3", "Pig iron mine", 1, 0, 3),
                TutorialTask("Increase your Kryptonite mine level 0 / 3", "Kryptonite mine", 3, 0, 3),
            ),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(_sample_planet(offers=offers),),
            tutorial=tut,
        )

        actions = plan_actions(state)
        self.assertGreaterEqual(len(actions), 3)
        self.assertEqual(actions[0].target_id, FUSION_PLANT)
        self.assertTrue(actions[0].executable_now)
        self.assertEqual(actions[0].priority, "P2")

    def test_hostile_fleet_and_negative_energy_outrank_tutorial(self) -> None:
        offers = (
            BuildingOffer(
                building_id=FUSION_PLANT,
                name="Fusion plant",
                current_level=1,
                target_level=2,
                cost=Resources(160, 0, 70, 0),
                can_build=True,
                status_text="Build level 2",
                build_href="href",
            ),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(
                _sample_planet(
                    resources=Resources(90000, 0, 1000, 0),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(10, 25),
                    offers=offers,
                ),
            ),
            fleet_alerts=(FleetAlert("ATTACK", True, "Incoming attack in 00:12:00"),),
        )

        actions = plan_actions(state)
        self.assertEqual(actions[0].priority, "P0")
        self.assertEqual(actions[0].kind, "ALERT_HOSTILE")
        self.assertEqual(actions[1].priority, "P1")
        self.assertEqual(actions[1].target_id, FUSION_PLANT)
        p1_ids = [a.target_id for a in actions if a.priority == "P1"]
        self.assertIn(PIG_IRON_WAREHOUSE, p1_ids)

    def test_tutorial_skips_tasks_already_covered_by_build_queue(self) -> None:
        offer = BuildingOffer(
            building_id=KRYPTONITE_MINE,
            name="Kryptonite mine",
            current_level=2,
            target_level=3,
            cost=Resources(100, 0, 50, 0),
            can_build=True,
            status_text="Build level 3",
            build_href="href",
        )
        tut = TutorialStatus(
            step=1,
            total_steps=11,
            tasks=(
                TutorialTask("Increase your Fusion plant level 2 / 3", "Fusion plant", 5, 2, 3),
                TutorialTask("Increase your Kryptonite mine level 2 / 3", "Kryptonite mine", 3, 2, 3),
            ),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(
                _sample_planet(
                    energy=EnergyBalance(10, 5),
                    buildings={"b5": 2, "b3": 2},
                    offers=(offer,),
                ),
            ),
            tutorial=tut,
            active_queues=(QueueJob("building", "Fusion plant", building_id=5, target_level=3),),
        )
        actions = plan_actions(state)
        self.assertEqual([a.target_id for a in actions], [KRYPTONITE_MINE])

    def test_claim_tutorial_is_emitted_when_can_complete(self) -> None:
        tut = TutorialStatus(
            step=1,
            total_steps=11,
            tasks=(
                TutorialTask("Increase your Fusion plant level 3 / 3", "Fusion plant", 5, 3, 3),
            ),
            reward_text="5232 Pig iron",
            can_complete=True,
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(_sample_planet(energy=EnergyBalance(45, 38)),),
            tutorial=tut,
        )
        actions = plan_actions(state)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "CLAIM_TUTORIAL")
        self.assertTrue(actions[0].executable_now)

    def test_tutorial_uses_relative_task_deltas_not_absolute_levels(self) -> None:
        # In Tutorial 2, buildings start at L3 while tasks reset to 0 / 3 and 0 / 4.
        offers = (
            BuildingOffer(
                building_id=PIG_IRON_MINE,
                name="Pig iron mine",
                current_level=3,
                target_level=4,
                cost=Resources(180, 0, 60, 0),
                can_build=True,
                status_text="Build level 4",
                build_href="href1",
            ),
            BuildingOffer(
                building_id=FUSION_PLANT,
                name="Fusion plant",
                current_level=3,
                target_level=4,
                cost=Resources(276, 0, 121, 0),
                can_build=True,
                status_text="Build level 4",
                build_href="href5",
            ),
        )
        tut = TutorialStatus(
            step=2,
            total_steps=11,
            tasks=(
                TutorialTask("Increase your Fusion plant level 0 / 4", "Fusion plant", 5, 0, 4),
                TutorialTask("Increase your Pig iron mine level 0 / 3", "Pig iron mine", 1, 0, 3),
            ),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(
                _sample_planet(
                    energy=EnergyBalance(45, 38),
                    buildings={"b1": 3, "b5": 3},
                    offers=offers,
                ),
            ),
            tutorial=tut,
        )
        actions = plan_actions(state)
        self.assertEqual({a.target_id for a in actions}, {FUSION_PLANT, PIG_IRON_MINE})

    def test_economy_promotes_proactive_fusion_when_next_mine_would_starve_energy(self) -> None:
        # Energy net is +3, but any mine upgrade here needs >= 9 energy -> proactive Fusion P1.
        offers = (
            BuildingOffer(PIG_IRON_MINE, "Pig iron mine", 5, 6, Resources(600, 0, 200, 0), True, "Build", "h1"),
            BuildingOffer(FUSION_PLANT, "Fusion plant", 4, 5, Resources(400, 0, 175, 0), True, "Build", "h5"),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(
                _sample_planet(
                    resources=Resources(5000, 2000, 3000, 1000),
                    energy=EnergyBalance(50, 47),
                    buildings={"b1": 5, "b2": 3, "b3": 4, "b4": 2, "b5": 4},
                    offers=offers,
                ),
            ),
        )
        actions = plan_actions(state)
        self.assertEqual(actions[0].priority, "P1")
        self.assertEqual(actions[0].target_id, FUSION_PLANT)
        self.assertIn("Proactive power", actions[0].reason)

    def test_blast_furnace_capped_when_pig_iron_supply_too_low(self) -> None:
        # Pig iron L2 produces 63/h, Blast furnace L4 uses 98 Pig iron/h (>70%) -> Pig iron mine before Blast.
        offers = (
            BuildingOffer(PIG_IRON_MINE, "Pig iron mine", 2, 3, Resources(175, 0, 56, 0), True, "Build", "h1"),
            BuildingOffer(BLAST_FURNACE, "Blast furnace", 3, 4, Resources(255, 0, 85, 0), True, "Build", "h2"),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(
                _sample_planet(
                    resources=Resources(2000, 500, 2000, 200),
                    energy=EnergyBalance(200, 40),
                    buildings={"b1": 2, "b2": 3, "b3": 2, "b4": 1, "b5": 7},
                    offers=offers,
                ),
            ),
        )
        actions = plan_actions(state)
        mine_ids = [a.target_id for a in actions if a.target_id in (PIG_IRON_MINE, BLAST_FURNACE)]
        self.assertEqual(mine_ids[0], PIG_IRON_MINE)
        blast_action = next(a for a in actions if a.target_id == BLAST_FURNACE)
        self.assertEqual(blast_action.priority, "P3")

    def test_economy_unlocks_weapons_factory_and_emits_non_executable_research_advisories(self) -> None:
        # Mines >= L4, Dev Center L3, Research lab L1, Energy Tech L1 -> unlocks Weapons factory & advisories.
        wf_offer = BuildingOffer(9, "Weapons factory", 0, 1, Resources(300, 200, 0, 350), True, "Build", "h9")
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ventkx86",
            timestamp=0.0,
            planets=(
                _sample_planet(
                    resources=Resources(5000, 5000, 5000, 2000),
                    energy=EnergyBalance(400, 120),
                    buildings={"b1": 5, "b2": 4, "b3": 5, "b4": 2, "b5": 9, "b7": 3, "b8": 1},
                    offers=(wf_offer,),
                ),
            ),
            researches={"r1": 0, "r2": 1},
        )
        actions = plan_actions(state)
        self.assertIn(WEAPONS_FACTORY, [a.target_id for a in actions if a.executable_now])
        advisories = [a for a in actions if a.kind in ("RESEARCH", "SHIP")]
        self.assertTrue(advisories)
        self.assertTrue(all(not a.executable_now for a in advisories))

    def test_spice_mine_roi_improves_on_colder_planet(self) -> None:
        cold = _sample_planet(
            resources=Resources(500, 500, 500, 0),
            energy=EnergyBalance(100, 20),
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 0, "b5": 5},
        )
        cold = PlanetState(
            planet_id=cold.planet_id,
            name=cold.name,
            coords=cold.coords,
            planet_type=cold.planet_type,
            temperature=-120,
            fields_used=cold.fields_used,
            fields_max=cold.fields_max,
            resources=cold.resources,
            storage=cold.storage,
            energy=cold.energy,
            buildings=cold.buildings,
        )
        hot = PlanetState(
            planet_id=cold.planet_id,
            name=cold.name,
            coords=cold.coords,
            planet_type=cold.planet_type,
            temperature=120,
            fields_used=cold.fields_used,
            fields_max=cold.fields_max,
            resources=cold.resources,
            storage=cold.storage,
            energy=cold.energy,
            buildings=cold.buildings,
        )
        cold_spice = next(a for a in plan_actions(GameState(31, 1, "u", 0.0, (cold,))) if a.target_id == SPICE_MINE)
        hot_spice = next(a for a in plan_actions(GameState(31, 1, "u", 0.0, (hot,))) if a.target_id == SPICE_MINE)
        cold_pb = float(cold_spice.reason.split("h Spice payback")[0].rsplit("(", 1)[1])
        hot_pb = float(hot_spice.reason.split("h Spice payback")[0].rsplit("(", 1)[1])
        self.assertLess(cold_pb, hot_pb)

    def test_merchant_mode_skips_blast_furnace_and_runs_executable_research(self) -> None:
        r_offer = ResearchOffer(
            research_id=2,
            research_key="r2",
            name="Energy technology",
            current_level=0,
            target_level=1,
            cost=Resources(0, 400, 800, 400),
            can_research=True,
            status_text="Research level 1",
            research_href="href_r2",
        )
        tut = TutorialStatus(
            step=4,
            total_steps=11,
            tasks=(
                TutorialTask("Upgrade your Energy technology 0 / 2", "Energy technology", None, 0, 2, "research", "r2"),
            ),
        )
        tut_state = GameState(
            31,
            1,
            "u",
            0.0,
            (_sample_planet(resources=Resources(5000, 5000, 5000, 2000), energy=EnergyBalance(100, 40), buildings={"b8": 1}),),
            tutorial=tut,
            research_offers=(r_offer,),
        )
        tut_actions = plan_actions(tut_state, config=StrategyConfig(allow_research=True))
        self.assertEqual(tut_actions[0].kind, "RESEARCH")
        self.assertTrue(tut_actions[0].executable_now)

        eco_state = GameState(
            31,
            1,
            "u",
            0.0,
            (_sample_planet(energy=EnergyBalance(100, 40), buildings={"b1": 5, "b2": 2, "b3": 4, "b4": 2, "b5": 8}),),
        )
        eco_actions = plan_actions(eco_state, config=StrategyConfig(merchant_mode=True))
        self.assertNotIn(BLAST_FURNACE, [a.target_id for a in eco_actions if a.kind in ("BUILD", "WAIT_RESOURCES")])

    def test_multi_planet_colony_build_and_hot_solar_cells_on_tight_fields(self) -> None:
        home = _sample_planet(
            resources=Resources(50, 50, 50, 50),
            energy=EnergyBalance(500, 200),
            buildings={"b1": 8, "b2": 6, "b3": 8, "b4": 6, "b5": 12},
        )
        hot_tight_colony = PlanetState(
            planet_id=22222,
            name="HotEdge",
            coords="02:380:01",
            planet_type="normal",
            temperature=120,
            fields_used=220,
            fields_max=230,  # 10 fields left!
            resources=Resources(10000, 10000, 10000, 5000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(40, 38),
            buildings={"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 4, "b9": 1},
            ship_offers=(UnitOffer("ship", SOLAR_CELLS_ID, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),),
        )
        state = GameState(31, 1, "u", 0.0, (home, hot_tight_colony), active_planet_id=home.planet_id)
        actions = plan_actions(state, config=StrategyConfig(allow_multi_planet=True, allow_shipyard=True))
        solar = [a for a in actions if a.kind == "BUILD_SHIP" and a.target_id == SOLAR_CELLS_ID]
        self.assertTrue(solar)
        self.assertTrue(solar[0].executable_now)
        self.assertEqual(solar[0].planet_id, 22222)
        # Remote colony mines become executable via cross-planet switch when affordable
        remote_builds = [a for a in actions if a.planet_id == 22222 and a.kind == "BUILD"]
        self.assertTrue(any(a.executable_now for a in remote_builds))

    def test_thermosolar_selected_when_cheaper_on_cold_spice_planet(self) -> None:
        cold = PlanetState(
            planet_id=12476,
            name="ColdSpice",
            coords="02:380:15",
            planet_type="normal",
            temperature=-120,
            fields_used=100,
            fields_max=230,
            resources=Resources(50000, 50000, 50000, 10000),
            storage=Resources(500000, 500000, 500000, 500000),
            energy=EnergyBalance(100, 150),
            buildings={"b1": 15, "b2": 10, "b3": 14, "b4": 14, "b5": 15, "b6": 0},
        )
        state = GameState(31, 1, "u", 0.0, (cold,), researches={"r2": 4})
        actions = plan_actions(state)
        self.assertEqual(actions[0].priority, "P1")
        self.assertEqual(actions[0].target_id, THERMOSOLAR_PLANT)

    def test_tutorial_defers_extra_fusion_when_ample_spare_energy_exists(self) -> None:
        offers = (
            BuildingOffer(FUSION_PLANT, "Fusion plant", 10, 11, Resources(8000, 0, 3500, 0), True, "Build level 11", "h5"),
            BuildingOffer(SPICE_MINE, "Spice mine", 2, 3, Resources(0, 400, 150, 0), True, "Build level 3", "h4"),
        )
        tut = TutorialStatus(
            step=4,
            total_steps=11,
            tasks=(
                TutorialTask("Increase your Fusion plant level 0 / 2", "Fusion plant", 5, 0, 2),
                TutorialTask("Increase your Spice mine level 0 / 3", "Spice mine", 4, 0, 3),
            ),
        )
        state = GameState(
            31,
            1,
            "u",
            0.0,
            (_sample_planet(energy=EnergyBalance(300, 120), buildings={"b4": 2, "b5": 10}, offers=offers),),
            tutorial=tut,
        )
        actions = plan_actions(state)
        self.assertEqual(actions[0].target_id, SPICE_MINE)
        self.assertEqual(actions[-1].target_id, FUSION_PLANT)

    def test_auto_throttle_blast_furnace_bucket_pauses_and_resumes(self) -> None:
        # Scenario 1: Waiting on Pig iron for next mine with Blast furnace running -> P1 THROTTLE_BUILDING 0%
        p_starved = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(50, 2000, 2000, 1000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(200, 120),
            buildings={"b1": 4, "b2": 6, "b3": 4, "b4": 3, "b5": 8},
            economy_rates={"resource2": 100},
        )
        st_starved = GameState(31, 1, "u", 0.0, (p_starved,), speedup_factor=1.0)
        acts_pause = plan_actions(st_starved, config=StrategyConfig(auto_throttle_blast_furnace=True))
        th_pause = [a for a in acts_pause if a.kind == "THROTTLE_BUILDING"]
        self.assertTrue(th_pause)
        self.assertEqual(th_pause[0].priority, "P1")
        self.assertEqual(th_pause[0].target_level, 0)
        self.assertTrue(th_pause[0].executable_now)

        # Disabled via config flag -> omitted
        acts_off = plan_actions(st_starved, config=StrategyConfig(auto_throttle_blast_furnace=False))
        self.assertFalse(any(a.kind == "THROTTLE_BUILDING" for a in acts_off))

        # Scenario 2: Paused at 0%, now missing Metal -> P1 THROTTLE_BUILDING 100%
        p_need_metal = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(4000, 10, 4000, 4000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(200, 120),
            buildings={"b1": 8, "b2": 6, "b3": 6, "b4": 4, "b5": 9, "b7": 1},
            economy_rates={"resource2": 0},
        )
        st_need_met = GameState(31, 1, "u", 0.0, (p_need_metal,), speedup_factor=1.0)
        acts_resume = plan_actions(st_need_met, config=StrategyConfig(auto_throttle_blast_furnace=True))
        th_resume = [a for a in acts_resume if a.kind == "THROTTLE_BUILDING"]
        self.assertTrue(th_resume)
        self.assertEqual(th_resume[0].target_level, 100)
        self.assertTrue(th_resume[0].executable_now)

        # Scenario 3: Hysteresis — if P1 blocker needs Pig iron (0 Metal) while b2 is already 0%,
        # stay at 0% even if P2/P3 wants Metal (avoid 0% <-> 100% oscillation)
        p_stay_paused = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=55,
            fields_max=230,
            resources=Resources(1300, 100, 9000, 800),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(800, 770),
            buildings={"b1": 12, "b2": 11, "b3": 11, "b4": 8, "b5": 13, "b7": 4},
            economy_rates={"resource2": 0},
        )
        st_stay = GameState(31, 1, "u", 0.0, (p_stay_paused,), speedup_factor=10.0)
        acts_stay = plan_actions(st_stay, config=StrategyConfig(auto_throttle_blast_furnace=True))
        self.assertEqual(acts_stay[0].priority, "P1")
        self.assertEqual(acts_stay[0].target_id, FUSION_PLANT)
        self.assertFalse(any(a.kind == "THROTTLE_BUILDING" for a in acts_stay))

    def test_tutorial_step6_plans_ship_and_milestone_alongside_economy(self) -> None:
        p = _sample_planet(
            resources=Resources(6000, 6000, 6000, 2000),
            energy=EnergyBalance(400, 200),
            buildings={"b1": 10, "b2": 8, "b3": 8, "b4": 6, "b5": 10, "b9": 2},
            offers=(
                BuildingOffer(1, "Pig iron mine", 10, 11, Resources(3000, 0, 1000, 0), True, "Build level 11", "h1"),
            ),
        )
        p = PlanetState(
            planet_id=p.planet_id,
            name=p.name,
            coords=p.coords,
            planet_type=p.planet_type,
            temperature=p.temperature,
            fields_used=p.fields_used,
            fields_max=p.fields_max,
            resources=p.resources,
            storage=p.storage,
            energy=p.energy,
            buildings=p.buildings,
            building_offers=p.building_offers,
            ship_offers=(
                UnitOffer("ship", 1, "Small transport", 1, Resources(0, 3000, 3000, 0), True, "ship[1]"),
            ),
        )
        tut = TutorialStatus(
            step=6,
            total_steps=11,
            tasks=(
                TutorialTask("Build a Small transport 1 / 2", "Small transport", 1, 1, 2, "ship"),
                TutorialTask("Have a successful fight 0 / 1", "Have a successful fight", None, 0, 1, "milestone"),
            ),
        )
        st = GameState(31, 1, "u", 0.0, (p,), tutorial=tut)
        actions = plan_actions(st, config=StrategyConfig(allow_shipyard=True))
        kinds = {a.kind for a in actions}
        self.assertIn("BUILD_SHIP", kinds)
        self.assertIn("SCAN_GALAXY", kinds)
        self.assertIn("BUILD", kinds)

        # Once a yellow inactive target is scanned into state.raid_targets, ATTACK replaces SCAN_GALAXY
        st_with_target = GameState(
            31,
            1,
            "u",
            0.0,
            (
                PlanetState(
                    planet_id=p.planet_id,
                    name=p.name,
                    coords=p.coords,
                    planet_type=p.planet_type,
                    temperature=p.temperature,
                    fields_used=p.fields_used,
                    fields_max=p.fields_max,
                    resources=p.resources,
                    storage=p.storage,
                    energy=p.energy,
                    buildings=p.buildings,
                    ships={"s1": 2},
                    building_offers=p.building_offers,
                    ship_offers=p.ship_offers,
                ),
            ),
            tutorial=tut,
            raid_targets=(
                {
                    "target_coords": "02:380:04",
                    "owner": "SleepyTarget",
                    "planet_name": "InactivePlanet",
                    "status_color": "yellow",
                    "pig_iron": 0,
                    "metal": 0,
                    "kryptonite": 0,
                    "spice": 0,
                    "fleet": {},
                    "defense": {},
                    "is_spacia": False,
                },
            ),
        )
        guarded_atk = [a for a in plan_actions(st_with_target, config=StrategyConfig(allow_raids=False)) if a.kind == "ATTACK"]
        self.assertEqual(len(guarded_atk), 1)
        self.assertFalse(guarded_atk[0].executable_now)

        live_atk = [a for a in plan_actions(st_with_target, config=StrategyConfig(allow_raids=True)) if a.kind == "ATTACK"]
        self.assertEqual(len(live_atk), 1)
        self.assertTrue(live_atk[0].executable_now)

    def test_unaffordable_shipyard_targets_appear_in_planned_actions(self) -> None:
        # SpaceInvasion omits <input name="ship[...]"> when unaffordable -> can_build=False.
        # Fleet build targets must still show as non-executable BUILD_SHIP items with missing resources.
        p = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=40,
            fields_max=230,
            resources=Resources(400, 100, 200, 50),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 300),
            buildings={"b1": 10, "b2": 8, "b3": 9, "b4": 6, "b5": 11, "b9": 2},
            ships={"s1": 1},
            ship_offers=(
                UnitOffer("ship", 1, "Small transport", 1, Resources(0, 3000, 3000, 0), False, None, "Affordable: no"),
                UnitOffer("ship", 4, "Small fighter", 0, Resources(1500, 0, 500, 0), False, None, "Affordable: no"),
            ),
        )
        st = GameState(31, 1, "u", 0.0, (p,))
        actions = plan_actions(st, config=StrategyConfig(allow_shipyard=True))
        ships = [a for a in actions if a.kind == "BUILD_SHIP"]
        self.assertTrue(ships)
        self.assertTrue(all(not a.executable_now for a in ships))
        ship_names = {a.target_name for a in ships}
        self.assertIn("Small fighter", ship_names)
        self.assertIn("Small transport", ship_names)
        sf = next(a for a in ships if a.target_name == "Small fighter")
        self.assertEqual(sf.missing.pig_iron, 1100)
        self.assertEqual(sf.missing.kryptonite, 300)

    def test_planned_action_6arg_positional_compat_and_eta_defaults(self) -> None:
        act = PlannedAction("P2", "BUILD", 12476, "02:380:12", "Pig iron mine", "legacy 6-arg")
        self.assertEqual(act.wait_hours, 0.0)
        self.assertEqual(act.wait_seconds, 0.0)
        self.assertEqual(act.wait_eta, "Ready")
        self.assertEqual(act.duration_seconds, 0.0)
        self.assertEqual(act.duration_text, "Instant")
        self.assertEqual(act.total_hours, 0.0)
        self.assertEqual(act.total_seconds, 0.0)
        self.assertEqual(act.total_eta, "Ready")
        self.assertIsNone(act.eta_bottleneck)

        cloned = replace(act, wait_eta="12m", eta_bottleneck="pig_iron")
        self.assertEqual(cloned.wait_eta, "12m")
        self.assertEqual(asdict(cloned)["eta_bottleneck"], "pig_iron")

    def test_priority_plan_actions_attaches_etas_and_preserves_empty_planets(self) -> None:
        empty_st = GameState(31, 1, "u", 0.0, (), active_planet_id=None)
        self.assertEqual(plan_actions(empty_st, config=StrategyConfig()), [])

        p_blocked = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=40,
            fields_max=230,
            resources=Resources(500, 0, 2000, 100),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 200),
            buildings={"b1": 10, "b2": 8, "b3": 9, "b4": 5, "b5": 11, "b7": 3},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 10, 11, Resources(2000, 0, 800, 0), False, "Not enough resources"),
                BuildingOffer(7, "Development center", 3, 4, Resources(0, 4800, 2400, 1600), False, "Not enough resources"),
            ),
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
        )
        st = GameState(31, 1, "u", 0.0, (p_blocked,), speedup_factor=2.0)
        acts = plan_actions(st, config=StrategyConfig(planner_mode="priority"))
        self.assertGreater(len(acts), 0)
        for act in acts:
            expected = compute_action_eta(st, act)
            self.assertEqual(act.wait_eta, expected["wait_eta"])
            self.assertEqual(act.duration_text, expected["duration_text"])
            self.assertEqual(act.total_eta, expected["total_eta"])
            self.assertEqual(act.eta_bottleneck, expected["eta_bottleneck"])
        met_blocked = [a for a in acts if a.cost.metal > 0 and a.missing.metal > 0]
        self.assertTrue(met_blocked)
        self.assertEqual(met_blocked[0].wait_eta, "—")
        self.assertEqual(met_blocked[0].eta_bottleneck, "metal")

    def test_plan_actions_routes_ga_nn_normalizes_and_attaches_etas(self) -> None:
        empty_st = GameState(31, 1, "u", 0.0, (), active_planet_id=None)
        self.assertEqual(plan_actions(empty_st, config=StrategyConfig(planner_mode="ga_nn", ga_seed=42)), [])

        p = _sample_planet(
            resources=Resources(10_000, 8_000, 6_000, 3_000),
            energy=EnergyBalance(400, 150),
            buildings={"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 8, "b7": 2, "b8": 2},
            offers=(
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(600, 0, 200, 0), True, "Build level 6", "h1"),
                BuildingOffer(3, "Kryptonite mine", 4, 5, Resources(500, 0, 300, 0), True, "Build level 5", "h3"),
            ),
        )
        st = GameState(31, 1, "u", 1_700_000_000.0, (p,), researches={"r2": 1}, speedup_factor=5.0)
        acts_ga = plan_actions(st, config=StrategyConfig(planner_mode="ga_nn", ga_seed=42, ga_generations=4, ga_population_size=8))
        acts_ws = plan_actions(st, config=StrategyConfig(planner_mode=" GA_NN ", ga_seed=42, ga_generations=4, ga_population_size=8))
        self.assertGreater(len(acts_ga), 0)
        self.assertEqual(acts_ga, acts_ws)
        for act in acts_ga:
            expected = compute_action_eta(st, act)
            self.assertEqual(act.wait_eta, expected["wait_eta"])
            self.assertEqual(act.duration_text, expected["duration_text"])
            self.assertEqual(act.total_eta, expected["total_eta"])
            self.assertEqual(act.eta_bottleneck, expected["eta_bottleneck"])

        # Unknown planner_mode falls back cleanly to priority mode
        acts_unk = plan_actions(st, config=StrategyConfig(planner_mode="unknown_mode"))
        acts_pri = plan_actions(st, config=StrategyConfig(planner_mode="priority"))
        self.assertEqual(acts_unk, acts_pri)

    def test_plan_actions_ga_nn_does_not_suppress_layer4_when_state_raid_stats_empty(self) -> None:
        p = _sample_planet(buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 7})
        st_empty = GameState(31, 1, "u", 1_700_000_000.0, (p,), raid_stats={})
        cfg = StrategyConfig(planner_mode="ga_nn", ga_seed=42)

        with mock.patch("spaceinvasion.optimizer.optimize_plan_actions", return_value=[]) as mocked_opt:
            plan_actions(st_empty, config=cfg)
            mocked_opt.assert_called_once()
            self.assertNotIn("raid_stats", mocked_opt.call_args.kwargs)

            non_empty = {"overall": {"attempts": 3, "wins": 3, "success_rate": 1.0}}
            st_pop = GameState(31, 1, "u", 1_700_000_000.0, (p,), raid_stats=non_empty)
            plan_actions(st_pop, config=cfg)
            self.assertEqual(mocked_opt.call_args.kwargs.get("raid_stats"), non_empty)

            explicit_supp = {}
            plan_actions(st_pop, config=cfg, raid_stats=explicit_supp)
            self.assertIs(mocked_opt.call_args.kwargs.get("raid_stats"), explicit_supp)

            plan_actions(
                st_pop,
                config=cfg,
                now_ts=1234.0,
                scanned_systems={"02:380": 1200.0},
                throttle_cooldowns={1: 1210.0},
            )
            self.assertEqual(mocked_opt.call_args.kwargs.get("now_ts"), 1234.0)
            self.assertEqual(mocked_opt.call_args.kwargs.get("scanned_systems"), {"02:380": 1200.0})
            self.assertEqual(mocked_opt.call_args.kwargs.get("throttle_cooldowns"), {1: 1210.0})

    def test_status_color_omitted_active_asteroid_vs_commander_slots_and_non_finite_fields(self) -> None:
        from spaceinvasion.strategy import _max_build_slots

        p_ast = PlanetState(
            planet_id=1,
            name="Ast",
            coords="02:380:01",
            planet_type="asteroid",
            temperature=0,
            fields_used=1,
            fields_max=10,
            resources=Resources(),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(0, 0),
            buildings={"b1": 1},
        )
        st_norm = GameState(31, 1, "u", 1_700_000_000.0, (p_ast,))
        self.assertEqual(_max_build_slots(p_ast, st_norm), 1)
        p_norm = _sample_planet(buildings={"b1": 4})
        st_cmd = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p_norm,),
            specs={"construction_commander": 1},
            raid_targets=(
                {
                    "coords": "02:380:09",
                    "owner": "ActivePlayer",
                    "is_inactive": False,
                    "is_spacia": False,
                    "resources": {"pig_iron": 50_000, "metal": 20_000, "kryptonite": 10_000, "spice": 5_000},
                },
            ),
        )
        self.assertEqual(_max_build_slots(p_norm, st_cmd), 6)
        p_norm_ships = PlanetState(
            planet_id=p_norm.planet_id,
            name=p_norm.name,
            coords=p_norm.coords,
            planet_type=p_norm.planet_type,
            temperature=p_norm.temperature,
            fields_used=p_norm.fields_used,
            fields_max=p_norm.fields_max,
            buildings=p_norm.buildings,
            ships={"s1": 10, "s4": 30},
            resources=p_norm.resources,
            storage=p_norm.storage,
            energy=p_norm.energy,
            building_offers=p_norm.building_offers,
            economy_rates={"resource1": "bad", "resource2": float("nan")},  # type: ignore[dict-item]
        )
        st_no_pvp = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p_norm_ships,),
            researches={"r1": 1},
            raid_targets=st_cmd.raid_targets,
        )
        acts = plan_actions(st_no_pvp, config=StrategyConfig(allow_raids=True, allow_pvp_in_az=False))
        self.assertFalse(any(a.kind == "ATTACK" and a.executable_now for a in acts))

    def test_priority_mode_guards_nan_storage_warn_ratio_and_corrupt_raid_targets_and_scans(self) -> None:
        p_full = PlanetState(
            planet_id=101,
            name="NearFull",
            coords="01:010:01",
            planet_type="normal",
            temperature=20,
            fields_used=20,
            fields_max=220,
            resources=Resources(95_000, 1_000, 1_000, 1_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b11": 1},
            ships={"s1": 5, "s4": 10},
        )
        corrupt_target = {
            "coords": "01:010:09",
            "owner": "CorruptIdle",
            "status_color": "yellow",
            "is_inactive": True,
            "last_scanned_ts": "bad",
            "last_spied_ts": float("nan"),
            "resources": {"pig_iron": "bad", "metal": None, "kryptonite": float("inf")},
            "fleet": {"s4": "bad"},
            "defense": {"d1": "bad"},
        }
        st = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (p_full,),
            active_planet_id=101,
            raid_targets=(corrupt_target,),
        )
        for bad_ratio in (float("nan"), float("inf")):
            acts = plan_actions(
                st,
                config=StrategyConfig(planner_mode="priority", allow_raids=True),
                storage_warn_ratio=bad_ratio,
                scanned_systems={"01:010": "bad"},  # type: ignore[dict-item]
            )
            self.assertTrue(
                any(a.priority == "P1" and a.target_id == 11 for a in acts),
                f"Expected P1 warehouse guard with storage_warn_ratio={bad_ratio}: {acts}",
            )

    def test_huge_metal_blast_furnace_and_huge_energy_strings_safe(self) -> None:
        p_paused = PlanetState(
            planet_id=101,
            name="P1",
            coords="02:380:12",
            planet_type="normal",
            temperature=30,
            fields_used=20,
            fields_max=200,
            resources=Resources(pig_iron=10**400, metal=10**400, kryptonite=1000, spice=500),
            storage=Resources(10**400, 10**400, 100000, 100000),
            energy=EnergyBalance(0, 10**5000),
            buildings={"b1": 5, "b2": 5, "b3": 5, "b4": 5, "b5": 5},
            building_offers=(
                BuildingOffer(FUSION_PLANT, "Fusion plant", 5, 6, Resources(10, 0, 10, 0), True, "Build"),
            ),
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
        )
        p_active = replace(
            p_paused,
            planet_id=102,
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
        )
        for pl in (p_paused, p_active):
            st = GameState(
                31,
                1,
                "u",
                1_700_000_000.0,
                (pl,),
                active_planet_id=pl.planet_id,
                tutorial=TutorialStatus(
                    step=10**5000,
                    total_steps=11,
                    can_complete=True,
                    tasks=(TutorialTask("Build", "Build", building_id=1, current=0, target=10**5000),),
                ),
            )
            acts = plan_actions(st, config=StrategyConfig(planner_mode="priority"))
            self.assertTrue(len(acts) > 0)
            seen = set()
            for a in acts:
                self.assertIsInstance(a.reason, str)
                self.assertIsInstance(a.target_name, str)
                key = (a.kind, a.planet_id, a.target_id, a.target_name)
                self.assertNotIn(key, seen)
                seen.add(key)

    def test_huge_10e400_and_million_digit_resources_trigger_p1_warehouse_in_priority_and_ga_nn(self) -> None:
        for huge in (10**400, 10**1_000_000):
            planet = _sample_planet(
                resources=Resources(huge, 1000, 1000, 1000),
                storage=Resources(100000, 100000, 100000, 100000),
                energy=EnergyBalance(500, 100),
                buildings={"b1": 5, "b5": 5, "b11": 1},
                offers=(
                    BuildingOffer(
                        building_id=PIG_IRON_WAREHOUSE,
                        name="Pig iron warehouse",
                        current_level=1,
                        target_level=2,
                        cost=Resources(1000, 0, 0, 0),
                        can_build=True,
                        status_text="Build level 2",
                        build_href="indexInternal.es?action=internalBuildItem&buildingID=11",
                    ),
                ),
            )
            state = GameState(
                universe_id=31,
                user_id=103603,
                nickname="ventkx86",
                timestamp=1700000000.0,
                planets=(planet,),
                active_planet_id=planet.planet_id,
            )
            for mode in ("priority", "ga_nn"):
                acts = plan_actions(
                    state,
                    config=StrategyConfig(planner_mode=mode, ga_population_size=4, ga_generations=2, ga_horizon_steps=2, ga_seed=1),
                )
                p1_wh = [a for a in acts if a.priority == "P1" and a.kind == "BUILD" and a.target_id == PIG_IRON_WAREHOUSE]
                self.assertTrue(p1_wh, f"Expected P1 warehouse action for mode={mode}, huge={huge.bit_length()}b")

    def test_r8_plan_actions_allow_raids_survives_non_ascii_superscript_coords(self) -> None:
        from spaceinvasion.state import game_state_from_dict

        gs = game_state_from_dict(
            {
                "planets": [
                    {
                        "planet_id": 1,
                        "name": "H",
                        "coords": "1:²:3",
                        "resources": {"pig_iron": 10000, "metal": 5000, "kryptonite": 5000, "spice": 2000},
                        "storage": {"pig_iron": 100000, "metal": 100000, "kryptonite": 100000, "spice": 100000},
                        "energy": {"produced": 200, "used": 50},
                        "ships": {"s4": 10, "s1": 5},
                    }
                ],
                "raid_targets": [
                    {
                        "coords": "1:³:4",
                        "name": "T",
                        "is_inactive": True,
                        "resources": {"pig_iron": 5000, "metal": 2000, "kryptonite": 1000, "spice": 500},
                    }
                ],
            }
        )
        acts = plan_actions(gs, config=StrategyConfig(allow_raids=True), now_ts=1700000000.0)
        self.assertIsInstance(acts, list)

    def test_r9_priority_enforces_asteroid_rules_and_zero_free_fields(self) -> None:
        # 1) Asteroid must never schedule ground mines, power plants, or warehouses in priority mode
        ast_planet = PlanetState(
            planet_id=99,
            name="Ast",
            coords="01:001:01",
            planet_type="asteroid",
            temperature=-50,
            fields_used=5,
            fields_max=20,
            resources=Resources(99_999, 99_999, 99_999, 99_999),
            storage=Resources(1_000, 1_000, 1_000, 1_000),
            energy=EnergyBalance(0, 10),
            buildings={"b19": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 0, 1, Resources(78, 0, 25, 0), True, "Build"),
                BuildingOffer(5, "Fusion plant", 0, 1, Resources(80, 0, 35, 0), True, "Build"),
                BuildingOffer(11, "Pig iron warehouse", 0, 1, Resources(2000, 800, 0, 0), True, "Build"),
            ),
        )
        # 2) Full-field normal planet (fields_used == fields_max > 0) must emit no executable BUILD
        full_home = PlanetState(
            planet_id=1,
            name="Home",
            coords="01:001:01",
            planet_type="normal",
            temperature=20,
            fields_used=200,
            fields_max=200,
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(100, 200),
            buildings={"b1": 10, "b2": 8, "b3": 8, "b4": 7, "b5": 10, "b7": 3, "b8": 3, "b9": 3},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 10, 11, Resources(1000, 1000, 500, 0), True, "Build", "h5"),
                BuildingOffer(11, "Pig iron warehouse", 1, 2, Resources(1000, 500, 0, 0), True, "Build", "h11"),
            ),
        )
        st = GameState(31, 1, "u", 1_700_000_000.0, (full_home, ast_planet), active_planet_id=1)
        acts = plan_actions(st, config=StrategyConfig(planner_mode="priority", allow_multi_planet=True))
        ast_forbidden = [
            a
            for a in acts
            if a.planet_id == 99 and a.target_id in {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14}
        ]
        self.assertEqual(ast_forbidden, [])
        home_builds = [a for a in acts if a.planet_id == 1 and a.kind == "BUILD"]
        self.assertEqual(home_builds, [])
        self.assertEqual([a for a in acts if a.kind == "BUILD" and a.executable_now], [])

        # Full-field planet with Weapons factory and Solar cells offer prefers 0-field Solar cells
        full_with_s9 = PlanetState(
            planet_id=full_home.planet_id,
            name=full_home.name,
            coords=full_home.coords,
            planet_type=full_home.planet_type,
            temperature=full_home.temperature,
            fields_used=200,
            fields_max=200,
            resources=full_home.resources,
            storage=full_home.storage,
            energy=full_home.energy,
            buildings=full_home.buildings,
            building_offers=full_home.building_offers,
            ship_offers=(UnitOffer("ship", SOLAR_CELLS_ID, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),),
        )
        acts_s9 = plan_actions(
            GameState(31, 1, "u", 1_700_000_000.0, (full_with_s9,), active_planet_id=1),
            config=StrategyConfig(planner_mode="priority", allow_shipyard=True),
        )
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == SOLAR_CELLS_ID and a.executable_now for a in acts_s9))
        self.assertEqual([a for a in acts_s9 if a.kind == "BUILD"], [])

    def test_r10_zero_free_fields_blocks_power_even_with_deficit_and_raid_case_normalization(self) -> None:
        from spaceinvasion import strategy as strat
        from spaceinvasion.state import TutorialStatus, TutorialTask

        self.assertEqual(strat._safe_strategy_int(b"77"), 77)
        self.assertEqual(strat._safe_strategy_int("\u0667\u0667"), 0)
        self.assertEqual(strat._safe_strategy_float(b"1.5"), 1.5)
        self.assertEqual(strat._safe_strategy_float("\u0661.\u0665"), 0.0)

        # 1. Zero free fields + severe energy deficit + tutorial b5 task must NOT emit BUILD
        full_tutorial_p = PlanetState(
            planet_id=101,
            name="FullDeficit",
            coords="01:001:01",
            planet_type="normal",
            temperature=40,
            fields_used=200,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(10, 160),
            buildings={"b1": 10, "b2": 10, "b9": 1},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 0, 1, Resources(100, 100, 100, 100), True, "?b=5"),
                BuildingOffer(6, "Thermal power plant", 0, 1, Resources(100, 100, 100, 100), True, "?b=6"),
            ),
            ship_offers=(
                UnitOffer("ship", SOLAR_CELLS_ID, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),
            ),
        )
        tut_st = GameState(
            universe_id=31,
            user_id=1,
            nickname="u",
            timestamp=1_700_000_000.0,
            planets=(full_tutorial_p,),
            active_planet_id=101,
            tutorial=TutorialStatus(
                step=1,
                total_steps=5,
                tasks=(TutorialTask("Build Fusion plant level 1", building_name="Fusion plant", building_id=5, current=0, target=1),),
                can_complete=False,
            ),
        )
        acts_full = plan_actions(tut_st, config=StrategyConfig(planner_mode="priority", allow_shipyard=True))
        self.assertEqual([a for a in acts_full if a.kind == "BUILD"], [])
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == SOLAR_CELLS_ID and a.executable_now for a in acts_full))

        # 2. Hot tight-field planet (0 < fields_rem <= min_free_fields_warning) prefers Solar cells over b6
        hot_tight_p = PlanetState(
            planet_id=102,
            name="HotTight",
            coords="01:001:02",
            planet_type="normal",
            temperature=80,
            fields_used=196,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(15, 14),
            buildings={"b1": 6, "b2": 5, "b3": 5, "b9": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 6, 7, Resources(500, 200, 0, 0), True, "?b=1"),
                BuildingOffer(6, "Thermal power plant", 0, 1, Resources(200, 100, 100, 0), True, "?b=6"),
            ),
            ship_offers=(
                UnitOffer("ship", SOLAR_CELLS_ID, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),
            ),
        )
        acts_tight = plan_actions(
            GameState(31, 1, "u", 1_700_000_000.0, (hot_tight_p,), active_planet_id=102),
            config=StrategyConfig(planner_mode="priority", allow_shipyard=True, hot_planet_solar_temp_threshold=60, min_free_fields_warning=5),
        )
        self.assertEqual([a for a in acts_tight if a.kind == "BUILD" and a.target_id == 6], [])
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == SOLAR_CELLS_ID for a in acts_tight))

        # 3. Mixed-case Raid colony emits zero BUILD/BUILD_SHIP/THROTTLE actions
        raid_case_p = PlanetState(
            planet_id=103,
            name="RaidMixed",
            coords="01:001:03",
            planet_type=" Raid ",
            temperature=20,
            fields_used=5,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(10, 100),
            buildings={"b1": 5, "b2": 3},
            building_offers=(BuildingOffer(1, "Pig iron mine", 5, 6, Resources(100, 50, 0, 0), True, "?b=1"),),
            ship_offers=(UnitOffer("ship", SOLAR_CELLS_ID, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),),
        )
        self.assertEqual(strat._norm_planet_type(raid_case_p), "raid")
        self.assertFalse(strat._is_building_allowed_on_planet(raid_case_p, 1, raid_case_p.building_offers))
        raid_st = GameState(31, 1, "u", 1_700_000_000.0, (raid_case_p,), active_planet_id=103)
        ast_case_p = PlanetState(
            planet_id=104,
            name="AstMixed",
            coords="01:001:04",
            planet_type=" ASTEROID ",
            temperature=10,
            fields_used=2,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(50, 10),
        )
        self.assertEqual(strat._norm_planet_type(ast_case_p), "asteroid")
        self.assertEqual(strat._max_build_slots(ast_case_p, raid_st), 1)
        acts_rc = plan_actions(
            raid_st,
            config=StrategyConfig(planner_mode="priority", allow_shipyard=True),
        )
        self.assertEqual([a for a in acts_rc if a.kind in ("BUILD", "BUILD_SHIP", "THROTTLE_BUILDING")], [])

    def test_r11_research_offer_map_bytes_huge_and_tuple_offers_b8_asteroid(self) -> None:
        import json
        import spaceinvasion.strategy as strat

        ast_p = PlanetState(
            planet_id=204,
            name="AstTuple",
            coords="01:001:04",
            planet_type=" ASTEROID ",
            temperature=10,
            fields_used=2,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(50, 10),
        )
        norm_p = PlanetState(
            planet_id=205,
            name="Norm",
            coords="01:001:05",
            planet_type="normal",
            temperature=20,
            fields_used=2,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(50, 10),
        )
        st = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (ast_p, norm_p),
            active_planet_id=204,
            research_offers=(
                ResearchOffer(1, 10**5000, "Huge", 0, 1, Resources(100, 0, 0, 0), True, "ok", "?r=1"),  # type: ignore[arg-type]
                ResearchOffer(2, b"r2", "Energy technology", 0, 1, Resources(0, 400, 800, 400), True, "ok", "?r=2"),  # type: ignore[arg-type]
            ),
        )
        rmap = strat._research_offer_map(st)
        self.assertIn("r2", rmap)
        json.dumps({k: v.name for k, v in rmap.items()}, allow_nan=False)

        b8_offer = BuildingOffer(8, "Research lab", 0, 1, Resources(0, 800, 0, 300), True, "?b=8")
        self.assertTrue(strat._is_building_allowed_on_planet(ast_p, 8, (b8_offer,)))
        self.assertFalse(strat._is_building_allowed_on_planet(ast_p, 8, ()))
        self.assertFalse(strat._is_building_allowed_on_planet(ast_p, 1, ()))
        self.assertTrue(strat._is_building_allowed_on_planet(ast_p, 19, ()))
        self.assertFalse(strat._is_building_allowed_on_planet(norm_p, 19, ()))
        b1_offer = BuildingOffer(1, "Pig iron mine", 0, 1, Resources(78, 0, 25, 0), True, "?b=1")
        forged_act = strat._make_build_action("P2", ast_p, 1, "Pig iron mine", "Mine", {1: b1_offer})
        self.assertFalse(forged_act.executable_now)
        self.assertIsNone(forged_act.action_href)
        self.assertIn("not permitted on asteroid", forged_act.reason)

    def test_r12_priority_raid_colony_blocks_tutorial_research_ships_and_huge_reward_text(self) -> None:
        from spaceinvasion import strategy as strat

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        raid_p = PlanetState(
            planet_id=501,
            name="RaidCol",
            coords="02:380:15",
            planet_type=" Raid ",
            temperature=20,
            fields_used=1,
            fields_max=10,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1, "b9": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 0, 1, Resources(60, 15, 0, 0), True, "Build", "?b=1"),
                BuildingOffer(7, "Development center", 1, 2, Resources(400, 120, 200, 0), True, "Build", "?b=7"),
            ),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),
            ),
        )
        tut_mix = TutorialStatus(
            step=4,
            total_steps=11,
            tasks=(
                TutorialTask("Upgrade your Mining technology 0 / 1", "Mining technology", None, 0, 1, "research", "r1"),
                TutorialTask("Build a Small fighter 0 / 1", "Small fighter", 4, 0, 1, "ship"),
                TutorialTask("Increase your Pig iron mine level 0 / 3", "Pig iron mine", 1, 0, 3),
            ),
            reward_text=10**5000,  # type: ignore[arg-type]
            can_complete=False,
        )
        r_offer = ResearchOffer(
            research_id=1,
            research_key="r1",
            name="Mining technology",
            current_level=0,
            target_level=1,
            cost=Resources(200, 100, 100, 0),
            can_research=True,
            status_text="Research level 1",
            research_href="?r=1",
        )
        st_raid = GameState(
            31,
            1,
            "u",
            0.0,
            (raid_p,),
            active_planet_id=501,
            tutorial=tut_mix,
            research_offers=(r_offer,),
        )
        cfg = StrategyConfig(allow_research=True, allow_shipyard=True, allow_multi_planet=False)
        tut_acts = plan_actions(st_raid, config=cfg)
        fleet_acts = strat._plan_tutorial_fleet_milestones(st_raid, raid_p, cfg)
        self.assertFalse(any(a.kind == "RESEARCH" for a in tut_acts))
        self.assertFalse(any(a.kind == "BUILD" and a.target_id == 1 for a in tut_acts))
        self.assertFalse(any(a.kind == "BUILD_SHIP" and a.planet_id == 501 for a in tut_acts))
        self.assertEqual(fleet_acts, [])

        forged_r_raid = strat._make_research_action(
            "P2", raid_p, st_raid, "r1", "Mining technology", {"r1": r_offer}, False, allow_live=True
        )
        self.assertFalse(forged_r_raid.executable_now)
        self.assertIsNone(forged_r_raid.action_href)

        ast_no_lab = PlanetState(
            planet_id=502,
            name="AstNoLab",
            coords="02:380:16",
            planet_type="asteroid",
            temperature=10,
            fields_used=1,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1},
        )
        st_ast = GameState(
            31,
            1,
            "u",
            0.0,
            (ast_no_lab,),
            active_planet_id=502,
            tutorial=tut_mix,
            research_offers=(r_offer,),
        )
        forged_r_ast = strat._make_research_action(
            "P2", ast_no_lab, st_ast, "r1", "Mining technology", {"r1": r_offer}, False, allow_live=True
        )
        self.assertFalse(forged_r_ast.executable_now)
        self.assertIsNone(forged_r_ast.action_href)
        self.assertFalse(
            any(a.kind == "RESEARCH" and a.executable_now for a in plan_actions(st_ast, config=cfg))
        )

        for bad_rew in (10**5000, _RaiseFspath(), _RaiseStrRepr()):
            st_claim = GameState(
                31,
                1,
                "u",
                0.0,
                (_sample_planet(),),
                tutorial=TutorialStatus(
                    step=1,
                    total_steps=11,
                    tasks=(TutorialTask(_RaiseStrRepr(), _RaiseFspath(), 1, 1, 1),),  # type: ignore[arg-type]
                    reward_text=bad_rew,  # type: ignore[arg-type]
                    can_complete=True,
                ),
            )
            claim_acts = plan_actions(st_claim, config=StrategyConfig())
            self.assertEqual(claim_acts[0].kind, "CLAIM_TUTORIAL")
            self.assertIsInstance(claim_acts[0].reason, str)

    def test_r13_research_ship_b8_b9_exec_gates_and_raid_asteroid_tutorial_parity(self) -> None:
        from spaceinvasion.state import QueueJob

        # 1. Normal planet with b8=0 + stale ResearchOffer must NOT mark RESEARCH executable_now=True
        p_no_b8_b9 = PlanetState(
            planet_id=701,
            name="NoLabYard",
            coords="02:380:11",
            planet_type="normal",
            temperature=30,
            fields_used=10,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b5": 5, "b7": 3, "b8": 0, "b9": 0},
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),
                UnitOffer("ship", 9, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),
            ),
        )
        r2_offer = ResearchOffer(2, "r2", "Energy technology", 0, 1, Resources(0, 400, 800, 400), True, "Research", "?r=2")
        r1_offer = ResearchOffer(1, "r1", "Mining technology", 0, 1, Resources(200, 100, 100, 0), True, "Research", "?r=1")
        tut_r2 = TutorialStatus(4, 11, (TutorialTask("Upgrade Energy technology 0/1", "Energy technology", None, 0, 1, "research", "r2"),))
        st_r2 = GameState(31, 1, "u", 1000.0, (p_no_b8_b9,), active_planet_id=701, tutorial=tut_r2, research_offers=(r2_offer,))
        acts_r2 = plan_actions(st_r2, config=StrategyConfig(allow_research=True))
        res_acts = [a for a in acts_r2 if a.kind == "RESEARCH"]
        self.assertTrue(res_acts)
        self.assertFalse(any(a.executable_now for a in res_acts))
        self.assertTrue(all(a.action_href is None for a in res_acts))

        # 2. b8=1, r2=0 + stale ResearchOffer for r1 (requires r2>=4, b8>=2) -> executable_now=False
        p_b8_1 = PlanetState(
            planet_id=702,
            name="Lab1",
            coords="02:380:12",
            planet_type="normal",
            temperature=30,
            fields_used=12,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b5": 5, "b7": 3, "b8": 1, "b9": 0},
        )
        tut_r1 = TutorialStatus(4, 11, (TutorialTask("Upgrade Mining technology 0/1", "Mining technology", None, 0, 1, "research", "r1"),))
        st_r1 = GameState(31, 1, "u", 1000.0, (p_b8_1,), active_planet_id=702, researches={"r2": 0}, tutorial=tut_r1, research_offers=(r1_offer,))
        acts_r1 = plan_actions(st_r1, config=StrategyConfig(allow_research=True))
        self.assertFalse(any(a.kind == "RESEARCH" and a.executable_now for a in acts_r1))

        # 3. b9=0 and queued b9 + stale ship_offers in tutorial, shipyard_targets, and hot planet prefer_s9
        tut_ship = TutorialStatus(6, 11, (TutorialTask("Build a Small fighter 0/1", "Small fighter", 4, 0, 1, "ship"),))
        st_ship_q = GameState(
            31,
            1,
            "u",
            1000.0,
            (p_no_b8_b9,),
            active_planet_id=701,
            tutorial=tut_ship,
            active_queues=(QueueJob("building", "Weapons factory", 9, 1, "5m"),),
        )
        acts_ship_q = plan_actions(st_ship_q, config=StrategyConfig(allow_shipyard=True))
        self.assertFalse(any(a.kind == "BUILD_SHIP" and a.executable_now for a in acts_ship_q))

        p_hot_nob9 = PlanetState(
            planet_id=703,
            name="HotNoYard",
            coords="02:380:13",
            planet_type="normal",
            temperature=65,
            fields_used=195,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(100, 100),
            buildings={"b1": 5, "b5": 5, "b7": 3, "b9": 0},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(600, 200, 0, 0), True, "Build", "?b=1"),
                BuildingOffer(5, "Fusion plant", 5, 6, Resources(500, 200, 100, 0), True, "Build", "?b=5"),
            ),
            ship_offers=(
                UnitOffer("ship", 9, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),
                UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),
            ),
        )
        st_hot = GameState(
            31,
            1,
            "u",
            1000.0,
            (p_hot_nob9,),
            active_planet_id=703,
            active_queues=(QueueJob("building", "Weapons factory", 9, 1, "5m"),),
        )
        acts_hot = plan_actions(st_hot, config=StrategyConfig(allow_shipyard=True))
        self.assertFalse(any(a.kind == "BUILD_SHIP" and a.executable_now for a in acts_hot))

        # 4. Raid single-planet tutorial returns [] (ga_nn parity), Asteroid b8=0 falls through to b10/b7,
        #    and Raid active + Normal colony delegates tutorial cleanly when allow_multi_planet=True
        raid_p = PlanetState(
            planet_id=704,
            name="RaidSolo",
            coords="02:380:14",
            planet_type=" Raid ",
            temperature=20,
            fields_used=2,
            fields_max=10,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1, "b9": 1},
        )
        tut_bld_res = TutorialStatus(
            3,
            11,
            (
                TutorialTask("Build Research lab 0/1", "Research lab", 8, 0, 1, "building"),
                TutorialTask("Upgrade Energy technology 0/1", "Energy technology", None, 0, 1, "research", "r2"),
            ),
        )
        st_raid_solo = GameState(31, 1, "u", 1000.0, (raid_p,), active_planet_id=704, tutorial=tut_bld_res, research_offers=(r2_offer,))
        self.assertEqual(plan_actions(st_raid_solo, config=StrategyConfig(planner_mode="priority", allow_multi_planet=False)), [])

        ast_p = PlanetState(
            planet_id=705,
            name="AstSolo",
            coords="02:380:15",
            planet_type=" Asteroid ",
            temperature=10,
            fields_used=2,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1},
        )
        st_ast_solo = GameState(31, 1, "u", 1000.0, (ast_p,), active_planet_id=705, tutorial=tut_bld_res, research_offers=(r2_offer,))
        ast_prio = plan_actions(st_ast_solo, config=StrategyConfig(planner_mode="priority", allow_multi_planet=False))
        self.assertEqual(ast_prio, [])
        tut_ast_mix = TutorialStatus(
            3,
            11,
            (
                TutorialTask("Build Research lab 0/1", "Research lab", 8, 0, 1, "building"),
                TutorialTask("Upgrade Energy technology 0/1", "Energy technology", None, 0, 1, "research", "r2"),
                TutorialTask("Upgrade Development center 1/2", "Development center", 7, 1, 2, "building"),
            ),
        )
        ast_b7_prio = plan_actions(
            GameState(31, 1, "u", 1000.0, (ast_p,), active_planet_id=705, tutorial=tut_ast_mix, research_offers=(r2_offer,)),
            config=StrategyConfig(planner_mode="priority", allow_multi_planet=False),
        )
        self.assertEqual([(a.kind, a.target_id) for a in ast_b7_prio], [("WAIT_RESOURCES", 7)])

        norm_col = PlanetState(
            planet_id=706,
            name="NormCol",
            coords="02:380:16",
            planet_type="normal",
            temperature=25,
            fields_used=5,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 2},
        )
        st_multi = GameState(
            31,
            1,
            "u",
            1000.0,
            (raid_p, norm_col),
            active_planet_id=704,
            tutorial=tut_bld_res,
            research_offers=(r2_offer,),
        )
        multi_acts = plan_actions(st_multi, config=StrategyConfig(planner_mode="priority", allow_multi_planet=True, allow_research=True))
        self.assertTrue(any(a.planet_id == 706 and a.kind == "BUILD" and a.target_id == 8 and a.executable_now for a in multi_acts))
        self.assertFalse(any(a.kind == "RESEARCH" and a.executable_now for a in multi_acts))

    def test_r14_stale_queued_forged_build_offers_tutorial_ship_delegation_and_shipyard_aliases(self) -> None:
        from spaceinvasion.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            TutorialStatus,
            TutorialTask,
            UnitOffer,
        )
        from spaceinvasion.strategy import _make_build_action

        # 1. Stale offer_tgt != nxt_lvl (e.g. queued building card shows +1 level) falls back to nxt_lvl WAIT_RESOURCES non-executable
        home_p = PlanetState(
            planet_id=710,
            name="Home",
            coords="02:380:10",
            planet_type="normal",
            temperature=25,
            fields_used=15,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 4, "b7": 2},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 5, 6, Resources(100, 100, 0, 0), True, "Build", "?b=1"),
                BuildingOffer(9, "Weapons factory", 0, 1, Resources(500, 500, 500, 0), True, "Build", "?b=9"),
            ),
        )
        offers_map = {o.building_id: o for o in home_p.building_offers}
        stale_act = _make_build_action("P2", home_p, 1, "Pig iron mine", "stale test", offers_map)
        self.assertEqual(stale_act.kind, "WAIT_RESOURCES")
        self.assertEqual(stale_act.target_level, 5)
        self.assertFalse(stale_act.executable_now)
        self.assertIsNone(stale_act.action_href)

        # 2. Forged offer with missing prerequisites (Weapons factory b9 requires b7>=3 & r2>=1, currently b7=2) blocked even with live offer
        forged_act = _make_build_action("P2", home_p, 9, "Weapons factory", "forged prereq", offers_map)
        self.assertEqual(forged_act.kind, "WAIT_RESOURCES")
        self.assertFalse(forged_act.executable_now)

        # 3. Tutorial fleet milestone delegates from raid / wf=0 home to ready normal colony, skipping raid
        raid_p = PlanetState(
            planet_id=711,
            name="Raid",
            coords="02:380:11",
            planet_type="raid",
            temperature=10,
            fields_used=0,
            fields_max=100,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b9": 5},
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),),
        )
        home_nowf = PlanetState(
            planet_id=712,
            name="NoYard",
            coords="02:380:12",
            planet_type="normal",
            temperature=25,
            fields_used=5,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 4},
        )
        col_ready = PlanetState(
            planet_id=713,
            name="ColYard",
            coords="02:380:13",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 4, "b9": 3},
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),),
        )
        tut_ship = TutorialStatus(
            5,
            11,
            (TutorialTask("Build 1 Small fighter", "Small fighter", 4, 0, 1, "ship", "small_fighter"),),
        )
        st_del = GameState(
            31,
            1,
            "u",
            1000.0,
            (raid_p, home_nowf, col_ready),
            active_planet_id=712,
            researches={f"r{i}": 6 for i in range(1, 10)},
            tutorial=tut_ship,
        )
        del_acts = plan_actions(
            st_del,
            config=StrategyConfig(planner_mode="priority", allow_multi_planet=True, allow_shipyard=True),
        )
        self.assertTrue(any(a.planet_id == 713 and a.kind == "BUILD_SHIP" and a.target_id == 4 and a.executable_now for a in del_acts))
        self.assertFalse(any(a.planet_id == 711 and a.executable_now for a in del_acts))

        # 4. Alias normalization in strategy.shipyard_targets (last-wins; bare digits skipped)
        cfg_alias = StrategyConfig(planner_mode="priority", allow_shipyard=True, shipyard_targets={"s4": 0, "Small fighter": 2, "1": 99})
        st_alias = GameState(
            31,
            1,
            "u",
            1000.0,
            (col_ready,),
            active_planet_id=713,
            researches={f"r{i}": 6 for i in range(1, 10)},
        )
        alias_acts = plan_actions(st_alias, config=cfg_alias)
        ship4 = [a for a in alias_acts if a.kind == "BUILD_SHIP" and a.target_id == 4]
        self.assertTrue(ship4 and "0/2" in ship4[0].reason)
        cfg_zero = StrategyConfig(planner_mode="priority", allow_shipyard=True, shipyard_targets={"Small fighter": 2, "s4": 0})
        zero_acts = plan_actions(st_alias, config=cfg_zero)
        self.assertFalse(any(a.kind == "BUILD_SHIP" and a.target_id == 4 for a in zero_acts))

    def test_zero_metal_blast_furnace_paused_holds_b2_upgrades_reserves_power_and_recovers(self) -> None:
        # Exact Colony 12476 live deadlock reproduction: b2=18 at 0%, Metal=22,925 blocking Dev Center L7 & Spice Mine L14
        offers_live = (
            BuildingOffer(1, "Pig iron mine", 18, 19, Resources(129_922, 0, 41_642, 0), True, "Build", "?b=1"),
            BuildingOffer(2, "Blast furnace", 18, 19, Resources(103_415, 0, 34_472, 0), True, "Build", "?b=2"),
            BuildingOffer(5, "Fusion plant", 19, 20, Resources(201_212, 0, 88_030, 0), True, "Build", "?b=5"),
            BuildingOffer(7, "Development center", 6, 7, Resources(0, 38_400, 19_200, 12_800), False, "Need Metal"),
            BuildingOffer(4, "Spice mine", 13, 14, Resources(0, 46_680, 16_975, 0), False, "Need Metal"),
        )
        # 1. With Fusion L19 + Thermosolar L8 finished (`e_prod=3050, e_used=2532`, net=+518 >= 445, but 518-445=73 < Pig L19 163 energy)
        #    and PigIron=150,000 (affordable for Pig L19 129,922, still saving toward Fusion L20 201,212):
        #    Must NOT upgrade paused Blast furnace L18->L19, and must hold Pig iron mine L19 from stealing energy!
        offers_saving = (
            BuildingOffer(1, "Pig iron mine", 18, 19, Resources(129_922, 0, 41_642, 0), True, "Build", "?b=1"),
            BuildingOffer(2, "Blast furnace", 18, 19, Resources(103_415, 0, 34_472, 0), True, "Build", "?b=2"),
            BuildingOffer(5, "Fusion plant", 19, 20, Resources(201_212, 0, 88_030, 0), False, "Wait"),
            BuildingOffer(7, "Development center", 6, 7, Resources(0, 38_400, 19_200, 12_800), False, "Need Metal"),
            BuildingOffer(4, "Spice mine", 13, 14, Resources(0, 46_680, 16_975, 0), False, "Need Metal"),
        )
        p_mid = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=108,
            fields_max=230,
            resources=Resources(150_000, 22_925, 210_000, 43_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(3050, 2532),
            buildings={"b1": 18, "b2": 18, "b3": 18, "b4": 13, "b5": 19, "b6": 8, "b7": 6, "b8": 1, "b9": 2},
            building_offers=offers_saving,
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100, "resource5": 100},
        )
        st_mid = GameState(31, 103603, "u", 1000.0, (p_mid,), active_planet_id=12476, speedup_factor=20.0)
        acts_mid = plan_actions(st_mid, config=StrategyConfig(auto_throttle_blast_furnace=True))
        self.assertFalse(any(a.target_id == 2 and a.kind == "BUILD" and a.executable_now for a in acts_mid))
        self.assertFalse(any(a.target_id == 1 and a.kind == "BUILD" and a.executable_now for a in acts_mid))

        # 2. Once Fusion L20 finishes (`e_prod=3620, e_used=2532`, net=+1088 covers b2 445 + Pig L19 163):
        #    Planner immediately emits P1 THROTTLE_BUILDING Blast furnace 100%!
        p_ready = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=109,
            fields_max=230,
            resources=Resources(150_000, 22_925, 210_000, 43_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(3620, 2532),
            buildings={"b1": 18, "b2": 18, "b3": 18, "b4": 13, "b5": 20, "b6": 8, "b7": 6, "b8": 1, "b9": 2},
            building_offers=offers_live,
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100, "resource5": 100},
        )
        st_ready = GameState(31, 103603, "u", 2000.0, (p_ready,), active_planet_id=12476, speedup_factor=20.0)
        acts_ready = plan_actions(st_ready, config=StrategyConfig(auto_throttle_blast_furnace=True))
        thr_ready = [a for a in acts_ready if a.kind == "THROTTLE_BUILDING"]
        self.assertTrue(thr_ready and thr_ready[0].target_level == 100 and thr_ready[0].executable_now)

    def test_storage_capped_warehouse_inflight_ship_hold_and_tutorial_spy_milestone(self) -> None:
        # 1. Storage-capped P1 Fusion plant (cost 201,212 > 200,000 cap) triggers P1 Pig iron warehouse;
        #    active own-fleet flight holds combat/transport ship replacement from draining Metal.
        offers = (
            BuildingOffer(5, "Fusion plant", 19, 20, Resources(201_212, 0, 88_030, 0), False, "Wait"),
            BuildingOffer(11, "Pig iron warehouse", 1, 2, Resources(4_000, 1_600, 0, 0), True, "Build", "?b=11"),
        )
        s_offers = (
            UnitOffer("ship", 4, "Small fighter", 0, Resources(3_000, 1_000, 0, 0), True, "ship_4"),
        )
        p_cap = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=110,
            fields_max=230,
            resources=Resources(40_000, 5_000, 200_000, 40_000),
            storage=Resources(200_000, 100_000, 800_000, 200_000),
            energy=EnergyBalance(3075, 3167),
            buildings={"b1": 19, "b2": 18, "b3": 18, "b4": 13, "b5": 19, "b6": 9, "b7": 6, "b8": 1, "b9": 2, "b11": 1},
            building_offers=offers,
            ship_offers=s_offers,
            economy_rates={"resource2": 100},
        )
        st_cap = GameState(
            31,
            103603,
            "u",
            1000.0,
            (p_cap,),
            active_planet_id=12476,
            active_queues=(QueueJob("fleet", "Attack 02:383:02"),),
            researches={"r4": 4},
            speedup_factor=20.0,
        )
        acts_cap = plan_actions(
            st_cap,
            config=StrategyConfig(allow_shipyard=True, shipyard_targets={"small-fighter": 4}),
        )
        wh_acts = [a for a in acts_cap if a.target_id == 11 and a.kind == "BUILD"]
        self.assertTrue(wh_acts and wh_acts[0].priority == "P1" and wh_acts[0].executable_now)
        sf_acts = [a for a in acts_cap if a.kind == "BUILD_SHIP" and a.target_id == 4]
        self.assertTrue(sf_acts and not sf_acts[0].executable_now and "in transit" in sf_acts[0].reason)

        # 2. Tutorial spy milestone auto-builds Spy probe when 0 owned, then dispatches P2 SPY when owned
        tut_spy = TutorialStatus(
            step=7,
            total_steps=11,
            can_complete=False,
            tasks=(TutorialTask("Spy on another planet 0 / 1", "Spy on another planet", None, 0, 1, "milestone"),),
        )
        probe_offer = UnitOffer("ship", 10, "Spy probe", 0, Resources(0, 0, 1_000, 0), True, "ship_10")
        p_no_probe = replace(
            p_cap,
            energy=EnergyBalance(3500, 2500),
            buildings={**p_cap.buildings, "b9": 3},
            ship_offers=(probe_offer,),
            ships={},
        )
        st_no_probe = GameState(
            31,
            103603,
            "u",
            1000.0,
            (p_no_probe,),
            active_planet_id=12476,
            researches={"r4": 3, "r7": 2},
            tutorial=tut_spy,
        )
        acts_build_probe = plan_actions(st_no_probe, config=StrategyConfig(allow_shipyard=True, allow_galaxy_scan=True))
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == 10 and a.executable_now for a in acts_build_probe))

        p_has_probe = replace(p_no_probe, ships={"s10": 2})
        st_has_probe = GameState(
            31,
            103603,
            "u",
            1000.0,
            (p_has_probe,),
            active_planet_id=12476,
            tutorial=tut_spy,
            raid_targets=({"coords": "02:378:15", "owner": "Sigilo_respec", "status_color": "yellow", "last_scanned_ts": 990.0},),
        )
        acts_spy = plan_actions(st_has_probe, config=StrategyConfig(allow_galaxy_scan=True), now_ts=1000.0)
        self.assertTrue(any(a.kind == "SPY" and a.executable_now for a in acts_spy))

    def test_tutorial_steps_6_to_11_end_to_end_progression_without_stalling(self) -> None:
        from spaceinvasion.state import QueueJob

        # Step 6a: Raid fleet in flight (12x s4, 6x s1 -> 02:383:02) holds duplicate ATTACK and non-tutorial ships
        tut6_active = TutorialStatus(
            step=6,
            total_steps=11,
            can_complete=False,
            tasks=(
                TutorialTask("Build a Small transport 2 / 2", "Small transport", 1, 2, 2, "ship"),
                TutorialTask("Have a successful fight 0 / 1", "Have a successful fight", None, 0, 1, "milestone"),
            ),
        )
        p6 = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=110,
            fields_max=230,
            resources=Resources(120_000, 35_000, 200_000, 45_000),
            storage=Resources(500_000, 500_000, 800_000, 500_000),
            energy=EnergyBalance(3500, 2500),
            buildings={"b1": 18, "b2": 18, "b3": 18, "b4": 13, "b5": 20, "b6": 8, "b7": 6, "b8": 1, "b9": 2},
            ships={"s1": 2, "s4": 4},
            building_offers=(
                BuildingOffer(8, "Research lab", 1, 2, Resources(400, 800, 400, 0), True, "Build", "?b=8"),
                BuildingOffer(4, "Spice mine", 13, 14, Resources(0, 46_680, 16_975, 0), False, "Need Metal"),
                BuildingOffer(12, "Metal warehouse", 1, 2, Resources(2_000, 2_000, 0, 0), True, "Build", "?b=12"),
            ),
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 4, Resources(3_000, 1_000, 0, 0), True, "ship[4]"),
                UnitOffer("ship", 10, "Spy probe", 0, Resources(0, 0, 1_000, 0), True, "ship[10]"),
            ),
        )
        st6_inflight = GameState(
            31,
            103603,
            "u",
            1000.0,
            (p6,),
            active_planet_id=12476,
            researches={"r2": 4, "r3": 2, "r4": 4},
            tutorial=tut6_active,
            active_queues=(QueueJob("fleet", "Attack 02:383:02 00:04:12"),),
            raid_targets=({"coords": "02:383:02", "owner": "Spacia", "is_spacia": True, "status_color": "purple"},),
        )
        acts6_hold = plan_actions(
            st6_inflight,
            config=StrategyConfig(allow_raids=True, allow_shipyard=True, shipyard_targets={"small-fighter": 10}),
            now_ts=1000.0,
        )
        self.assertFalse(any(a.kind == "ATTACK" and a.executable_now for a in acts6_hold))
        self.assertFalse(any(a.kind == "BUILD_SHIP" and a.target_id == 4 and a.executable_now for a in acts6_hold))

        # Step 6b: Raid lands -> tutorialComplete becomes True -> CLAIM_TUTORIAL executable immediately
        tut6_done = replace(
            tut6_active,
            can_complete=True,
            reward_text="10.000 Pig iron 5.000 Metal",
            tasks=(
                TutorialTask("Build a Small transport 2 / 2", "Small transport", 1, 2, 2, "ship"),
                TutorialTask("Have a successful fight 1 / 1", "Have a successful fight", None, 1, 1, "milestone"),
            ),
        )
        st6_claim = replace(st6_inflight, tutorial=tut6_done, active_queues=())
        acts6_claim = plan_actions(st6_claim, config=StrategyConfig(allow_raids=True))
        self.assertEqual(acts6_claim[0].kind, "CLAIM_TUTORIAL")
        self.assertTrue(acts6_claim[0].executable_now)

        # Step 7: Mixed Spy tech (0/2) + Build Spy probes (0/2) + Spy milestone starting from b8=1, r7=0
        tut7 = TutorialStatus(
            step=7,
            total_steps=11,
            can_complete=False,
            tasks=(
                TutorialTask("Upgrade your Spy technology 0 / 2", "Spy technology", None, 0, 2, "research", "r7"),
                TutorialTask("Build Spy probes 0 / 2", "Spy probe", 10, 0, 2, "ship"),
                TutorialTask("Spy on another planet 0 / 1", "Spy on another planet", None, 0, 1, "milestone"),
            ),
        )
        st7_need_b8 = GameState(
            31,
            103603,
            "u",
            1100.0,
            (p6,),
            active_planet_id=12476,
            researches={"r2": 4, "r3": 2, "r4": 4, "r7": 0},
            tutorial=tut7,
        )
        acts7_b8 = plan_actions(st7_need_b8, config=StrategyConfig(allow_research=True, allow_shipyard=True))
        self.assertTrue(any(a.kind == "BUILD" and a.target_id == 8 and a.executable_now for a in acts7_b8))
        self.assertFalse(any(a.kind == "BUILD_SHIP" and a.target_id == 10 and a.executable_now for a in acts7_b8))

        # Once b8=3, Spy tech r7 L1 executes; when r7 L2 is active in lab (1/2 + 1 active), research doesn't block mines
        p7_b8_3 = replace(p6, buildings={**p6.buildings, "b8": 3})
        r7_offer = ResearchOffer(7, "r7", "Spy technology", 0, 1, Resources(200, 1000, 200, 0), True, "Research", "?r=7")
        st7_r7 = GameState(
            31,
            103603,
            "u",
            1200.0,
            (p7_b8_3,),
            active_planet_id=12476,
            researches={"r2": 4, "r3": 2, "r4": 4, "r7": 0},
            research_offers=(r7_offer,),
            tutorial=tut7,
        )
        acts7_r7 = plan_actions(st7_r7, config=StrategyConfig(allow_research=True, allow_shipyard=True))
        self.assertTrue(any(a.kind == "RESEARCH" and a.target_id == 7 and a.executable_now for a in acts7_r7))

        # Once r7=2, builds Spy probe s10; once s10>=1 with home system scanned, spirals SCAN_GALAXY to 02:379, then SPY
        st7_build_probe = GameState(
            31,
            103603,
            "u",
            1300.0,
            (p7_b8_3,),
            active_planet_id=12476,
            researches={"r2": 4, "r3": 2, "r4": 4, "r7": 2},
            tutorial=replace(
                tut7,
                tasks=(
                    TutorialTask("Upgrade your Spy technology 2 / 2", "Spy technology", None, 2, 2, "research", "r7"),
                    TutorialTask("Build Spy probes 0 / 2", "Spy probe", 10, 0, 2, "ship"),
                    TutorialTask("Spy on another planet 0 / 1", "Spy on another planet", None, 0, 1, "milestone"),
                ),
            ),
        )
        acts7_probe = plan_actions(st7_build_probe, config=StrategyConfig(allow_shipyard=True, allow_galaxy_scan=True))
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == 10 and a.executable_now for a in acts7_probe))

        p7_with_probes = replace(p7_b8_3, ships={"s10": 2})
        st7_spiral = replace(
            st7_build_probe,
            planets=(p7_with_probes,),
            tutorial=replace(
                tut7,
                tasks=(
                    TutorialTask("Build Spy probes 2 / 2", "Spy probe", 10, 2, 2, "ship"),
                    TutorialTask("Spy on another planet 0 / 1", "Spy on another planet", None, 0, 1, "milestone"),
                ),
            ),
        )
        acts7_spiral = plan_actions(
            st7_spiral,
            config=StrategyConfig(allow_galaxy_scan=True),
            scanned_systems={"02:380": 1290.0},
            now_ts=1300.0,
        )
        self.assertTrue(any(a.kind == "SCAN_GALAXY" and "02:381" in a.target_name and a.executable_now for a in acts7_spiral))

        # Steps 8–11: Recycle -> s11 prereq unlock (b9 L4 + r3 L6 + r5 L4), Colonize -> s8 prereq unlock (b9 L4 + r4 L8),
        # Defense/Escape advisories keep executable_now=False, non-urgent Fusion stays after mines (bucket=4),
        # and storage-capped tutorial build triggers warehouse expansion!
        tut_late = TutorialStatus(
            step=10,
            total_steps=11,
            can_complete=False,
            tasks=(
                TutorialTask("Increase your Fusion plant level 0 / 2", "Fusion plant", 5, 0, 2, "building"),
                TutorialTask("Increase your Spice mine level 0 / 2", "Spice mine", 4, 0, 2, "building"),
                TutorialTask("Recycle a debris field 0 / 1", "Recycle a debris field", None, 0, 1, "milestone"),
                TutorialTask("Colonize a planet 0 / 1", "Colonize a planet", None, 0, 1, "milestone"),
                TutorialTask("Build a Mortar 0 / 5", "Build a Mortar", None, 0, 5, "milestone"),
                TutorialTask("Send an escape flight 0 / 1", "Send an escape flight", None, 0, 1, "milestone"),
            ),
        )
        p_late = replace(
            p7_with_probes,
            resources=Resources(120_000, 10_000, 200_000, 45_000),
            storage=Resources(500_000, 40_000, 800_000, 500_000),  # Spice mine needs 46,680 Metal > 40,000 cap (fill 25% < 85%)!
            buildings={**p7_with_probes.buildings, "b12": 1},
            building_offers=(
                BuildingOffer(4, "Spice mine", 13, 14, Resources(0, 46_680, 16_975, 0), False, "Cap"),
                BuildingOffer(5, "Fusion plant", 20, 21, Resources(50_000, 0, 20_000, 0), True, "Build", "?b=5"),
                BuildingOffer(9, "Weapons factory", 2, 3, Resources(1_600, 800, 400, 0), True, "Build", "?b=9"),
                BuildingOffer(12, "Metal warehouse", 1, 2, Resources(2_000, 2_000, 0, 0), True, "Build", "?b=12"),
            ),
        )
        st_late = GameState(
            31,
            103603,
            "u",
            1400.0,
            (p_late,),
            active_planet_id=12476,
            researches={"r2": 4, "r3": 2, "r4": 4, "r7": 2},
            research_offers=(
                ResearchOffer(3, "r3", "Engine research", 2, 3, Resources(0, 1_000, 500, 0), True, "Research", "?r=3"),
                ResearchOffer(4, "r4", "Rocket engine", 4, 5, Resources(2_000, 0, 0, 1_000), True, "Research", "?r=4"),
            ),
            tutorial=tut_late,
        )
        acts_late = plan_actions(st_late, config=StrategyConfig(allow_research=True, allow_shipyard=True))
        # Non-urgent Fusion is ordered behind Spice mine in tutorial_actions (and Metal warehouse expands cap!)
        b_ids_ordered = [a.target_id for a in acts_late if a.kind in ("BUILD", "WAIT_RESOURCES")]
        self.assertIn(4, b_ids_ordered)
        self.assertIn(5, b_ids_ordered)
        self.assertIn(9, b_ids_ordered)  # Unlocks Weapons factory toward L4 for Recycler/Colony ship
        self.assertIn(12, b_ids_ordered)  # Expands Metal warehouse for storage-capped Spice mine!
        # Compare raw bucket ordering in _plan_tutorial_actions: Spice mine (4) before non-urgent Fusion (5)
        from spaceinvasion.strategy import _effective_building_levels_and_energy, _offer_map, _plan_tutorial_actions

        eff_late, proj_late = _effective_building_levels_and_energy(st_late, p_late, is_active_planet=True)
        raw_tut_b = _plan_tutorial_actions(
            st_late, p_late, _offer_map(p_late), {}, {}, eff_late, proj_late, False, False, StrategyConfig()
        )
        self.assertEqual([a.target_id for a in raw_tut_b], [4, 5])
        # Research prerequisites r3/r4 unlocked for s11/s8
        self.assertTrue(any(a.kind == "RESEARCH" and a.target_id in (3, 4) and a.executable_now for a in acts_late))
        # Escape milestone emits P2 ESCAPE_FLIGHT targeting adjacent same-system slot; remaining 3 advisories stay executable_now=False
        esc_late = [a for a in acts_late if a.kind == "ESCAPE_FLIGHT"]
        self.assertEqual(len(esc_late), 1)
        self.assertEqual((esc_late[0].priority, esc_late[0].target_name, esc_late[0].executable_now), ("P2", "Save -> 02:380:13", False))
        tut_advisories = [a for a in acts_late if a.kind == "TUTORIAL_TASK"]
        self.assertEqual(len(tut_advisories), 3)
        self.assertTrue(all(not a.executable_now and a.action_href is None for a in tut_advisories))

        # Live Step 7/11 ("Make an escape flight 0 / 1"):
        # - 0 non-s9 ships -> P2 BUILD_SHIP Small transport (target_id=1)
        # - >=1 non-s9 ship & !own_fleet_in_flight -> P2 ESCAPE_FLIGHT (Save -> 02:380:13 / wrap 16->01) & holds ATTACK!
        tut_esc7 = TutorialStatus(
            step=7,
            total_steps=11,
            can_complete=False,
            tasks=(TutorialTask("Make an escape flight 0 / 1", "Make an escape flight", None, 0, 1, "milestone"),),
        )
        p_only_s9 = replace(
            p6,
            ships={"s9": 10},
            ship_offers=(UnitOffer("ship", 1, "Small transport", 0, Resources(0, 3_000, 3_000, 0), True, "ship[1]"),),
        )
        st_esc_need_s1 = GameState(31, 103603, "u", 1500.0, (p_only_s9,), active_planet_id=12476, researches={"r2": 4, "r3": 2, "r4": 4}, tutorial=tut_esc7)
        acts_esc_s1 = plan_actions(st_esc_need_s1, config=StrategyConfig(allow_shipyard=True, allow_fleetsave=True))
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == 1 and a.executable_now for a in acts_esc_s1))

        p_pos16 = replace(p6, coords="02:380:16", ships={"s1": 2, "s4": 6})
        st_esc_ready = GameState(
            31,
            103603,
            "u",
            1510.0,
            (p_pos16,),
            active_planet_id=12476,
            researches={"r2": 4, "r3": 2, "r4": 4},
            tutorial=tut_esc7,
            raid_targets=({"coords": "02:380:04", "owner": "Idle", "status_color": "yellow", "resources": {"pig_iron": 50_000}},),
        )
        acts_esc_go = plan_actions(
            st_esc_ready,
            config=StrategyConfig(allow_fleetsave=True, allow_raids=True),
            now_ts=1510.0,
        )
        esc_go = next((a for a in acts_esc_go if a.kind == "ESCAPE_FLIGHT"), None)
        self.assertIsNotNone(esc_go)
        assert esc_go is not None
        self.assertEqual((esc_go.priority, esc_go.target_name, esc_go.executable_now), ("P2", "Save -> 02:380:01", True))
        self.assertFalse(any(a.kind == "ATTACK" and a.executable_now for a in acts_esc_go))


if __name__ == "__main__":
    unittest.main()
