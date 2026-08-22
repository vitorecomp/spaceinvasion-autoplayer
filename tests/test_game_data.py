"""Unit tests for wiki-synced `spaceinvasion.game_data` catalog and helpers."""

from __future__ import annotations

import unittest

from spaceinvasion import game_data
from spaceinvasion.state import Resources


class TestGameData(unittest.TestCase):
    def test_catalog_completeness_and_ids(self) -> None:
        self.assertEqual(len(game_data.BUILDING_BY_ID), 21)
        self.assertGreaterEqual(len(game_data.RESEARCHES), 15)
        self.assertGreaterEqual(len(game_data.SHIPS), 15)
        self.assertGreaterEqual(len(game_data.DEFENSES), 11)

    def test_merchant_rates_and_spice_equivalent(self) -> None:
        self.assertEqual(
            game_data.MERCHANT_RATES_IN_SPICE,
            {"pig_iron": 6.0, "metal": 2.5, "kryptonite": 3.0, "spice": 1.0},
        )
        res = Resources(pig_iron=60, metal=25, kryptonite=30, spice=10)
        self.assertAlmostEqual(game_data.spice_equivalent(res), 40.0, places=5)

    def test_blast_furnace_two_to_one_pig_iron_and_no_mining_tech_boost(self) -> None:
        lvl3 = game_data.building_level_stats(2, 3)
        self.assertGreater(lvl3["prod"], 0)
        self.assertEqual(lvl3["pig_iron_used"], lvl3["prod"] * 2)
        base = game_data.mine_production_per_hour(2, 3, mining_tech_level=0)
        boosted = game_data.mine_production_per_hour(2, 3, mining_tech_level=10)
        self.assertEqual(base, boosted)

    def test_spice_temperature_interpolation_and_mining_tech(self) -> None:
        cold = game_data.mine_production_per_hour(4, 3, temperature=-120, mining_tech_level=0)
        mid = game_data.mine_production_per_hour(4, 3, temperature=20, mining_tech_level=0)
        hot = game_data.mine_production_per_hour(4, 3, temperature=120, mining_tech_level=0)
        self.assertGreater(cold, mid)
        self.assertGreater(mid, hot)

        boosted = game_data.mine_production_per_hour(4, 3, temperature=20, mining_tech_level=5)
        self.assertAlmostEqual(boosted, mid * 1.10, places=5)

    def test_energy_deltas_and_warehouse_capacity(self) -> None:
        self.assertEqual(game_data.building_energy_delta(5, 1), 11)
        self.assertEqual(game_data.building_energy_delta(4, 1), -9)
        wh1 = game_data.building_level_stats(11, 1)["storage_capacity"]
        wh2 = game_data.building_level_stats(11, 2)["storage_capacity"]
        self.assertEqual(wh1, 200_000)
        self.assertEqual(wh2, 400_000)

    def test_missing_prerequisites_and_uk_us_aliases(self) -> None:
        missing_wf = game_data.missing_prerequisites(
            "buildings",
            9,
            buildings={"b7": 2},
            researches={"r2": 0},
        )
        slugs = {item["slug"] for item in missing_wf}
        self.assertEqual(slugs, {"energy-technology", "development-center"})

        self.assertEqual(game_data.resolve_unit("research", "Defence systems")["id"], "r10")
        self.assertEqual(game_data.resolve_unit("research", "Particle analyser")["id"], "r12")

    def test_large_suffix_costs_and_decimal_unit_build_times(self) -> None:
        l11_micro = game_data.building_cost(17, 11)
        self.assertEqual(l11_micro["metal"], 512_000_000)
        self.assertEqual(l11_micro["kryptonite"], 1_020_000_000)
        self.assertAlmostEqual(game_data.SHIPS["solar-cells"]["base_build_time_s"], 1013.3333, places=4)
        self.assertAlmostEqual(game_data.SHIPS["battleship"]["base_build_time_s"], 19166.667, places=3)


if __name__ == "__main__":
    unittest.main()
