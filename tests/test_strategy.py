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


if __name__ == "__main__":
    unittest.main()
