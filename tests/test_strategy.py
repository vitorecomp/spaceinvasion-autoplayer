"""Unit tests for priority-ordered dry-run strategy planner."""

from __future__ import annotations

import unittest

from spaceinvasion.config import StrategyConfig
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


if __name__ == "__main__":
    unittest.main()
