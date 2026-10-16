"""Unit tests for wiki-synced `spaceinvasion.game_data` catalog and helpers."""

from __future__ import annotations

import unittest

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.model import Resources


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

    def test_spice_equivalent_preserves_sub_unit_floats_and_guards_non_finite_inputs(self) -> None:
        import math

        self.assertAlmostEqual(game_data.spice_equivalent({"spice": 0.9}), 0.9, places=6)
        self.assertAlmostEqual(game_data.spice_equivalent({"pig_iron": 5.9}), 5.9 / 6.0, places=6)
        self.assertEqual(game_data.spice_equivalent({"spice": float("nan")}), 0.0)
        self.assertEqual(game_data.spice_equivalent({"pig_iron": float("inf")}), 0.0)
        self.assertEqual(game_data.spice_equivalent({"metal": 1e400}), 0.0)
        self.assertEqual(game_data.spice_equivalent(["bad"]), 0.0)
        val = game_data.spice_equivalent({"pig_iron": 6.0, "metal": float("nan"), "spice": 1.5})
        self.assertTrue(math.isfinite(val))
        self.assertAlmostEqual(val, 2.5, places=6)

    def test_missing_prerequisites_handles_corrupt_non_finite_levels(self) -> None:
        for bad in ("bad", None, float("inf"), float("nan"), -3):
            res_b = game_data.missing_prerequisites(
                "buildings",
                9,
                buildings={"b1": 1, "b7": bad, "b17": bad},  # type: ignore[dict-item]
                researches={"r2": bad},  # type: ignore[dict-item]
            )
            self.assertTrue(any(m["slug"] == "development-center" and m["current"] == 0 for m in res_b))
            self.assertTrue(any(m["slug"] == "energy-technology" and m["current"] == 0 for m in res_b))

    def test_resolve_unit_rejects_unicode_digits_and_huge_ints(self) -> None:
        self.assertIsNotNone(game_data.resolve_unit("buildings", "1"))
        self.assertIsNotNone(game_data.resolve_unit("research", "r1"))
        for bad in ("²", "\u0660", "\u0661", "1²", "9" * 5000):
            for category in ("buildings", "research", "ships", "defense"):
                self.assertIsNone(game_data.resolve_unit(category, bad))

    def test_mine_production_per_hour_clamps_huge_mining_tech_without_overflow(self) -> None:
        import math

        base_10 = game_data.mine_production_per_hour(1, 5, mining_tech_level=10)
        self.assertAlmostEqual(
            base_10,
            game_data.mine_production_per_hour(1, 5, mining_tech_level=0)
            * (1.0 + 10 * game_data.MINING_TECH_BONUS_PER_LEVEL),
            places=6,
        )
        capped = game_data.mine_production_per_hour(1, 5, mining_tech_level=100_000)
        for huge in (10**400, 10**1_000_000):
            prod = game_data.mine_production_per_hour(1, 5, mining_tech_level=huge)
            self.assertTrue(math.isfinite(prod))
            self.assertEqual(prod, capped)

    def test_safe_helpers_big_ratio_scale_json_sanitize_and_compact_format(self) -> None:
        import json
        import math

        self.assertEqual(game_data.safe_int("bad", 7), 7)
        self.assertEqual(game_data.safe_int(True, 7), 7)
        self.assertIsNone(game_data.safe_optional_int("NaN"))
        self.assertEqual(game_data.safe_finite_float(float("inf"), 1.5), 1.5)
        self.assertEqual(game_data.cap_json_int(10**18 + 123456789), 10**18 + 123456789)
        self.assertEqual(game_data.cap_json_int(10**500), game_data.MAX_JSON_SAFE_INT)
        self.assertEqual(game_data.cap_json_int(-(10**500)), -game_data.MAX_JSON_SAFE_INT)

        sanitized = game_data.sanitize_json_tree(
            {
                "nan": float("nan"),
                "inf": float("inf"),
                "huge": 10**1_000_000,
                "exact": 10**18 + 123456789,
                "nested": [float("-inf"), -(10**400)],
            }
        )
        encoded = json.dumps(sanitized, allow_nan=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["nan"], 0.0)
        self.assertEqual(decoded["inf"], 0.0)
        self.assertEqual(decoded["exact"], 10**18 + 123456789)
        self.assertEqual(decoded["huge"], game_data.MAX_JSON_SAFE_INT)
        self.assertEqual(decoded["nested"], [0.0, -game_data.MAX_JSON_SAFE_INT])

        ratio_one = game_data.safe_big_ratio(10**5000, 10**5000)
        self.assertTrue(math.isfinite(ratio_one))
        self.assertAlmostEqual(ratio_one, 1.0, places=4)
        self.assertEqual(game_data.safe_big_ratio(10**400, 0), 0.0)
        self.assertEqual(game_data.safe_big_ratio(float("nan"), 10), 0.0)
        self.assertEqual(game_data.safe_big_ratio(10**400, 2, cap=1e200), 1e200)

        scaled = game_data.safe_scale_round_int(10**400, 0.5)
        self.assertGreater(scaled, 10**399)
        self.assertLess(scaled, 10**400)
        self.assertEqual(game_data.safe_scale_round_int(10**400, float("nan")), 0)

        compact = game_data.fmt_compact_int(10**1_000_000)
        self.assertTrue(compact.startswith("~1e"))

    def test_sync_wiki_data_hardening_render_module_and_session_close(self) -> None:
        from unittest.mock import MagicMock, patch
        import scripts.sync_wiki_data as sync_wiki_data

        self.assertEqual(sync_wiki_data._parse_int("9" * 5000), 10**200)
        self.assertEqual(sync_wiki_data._parse_int("9" * 500 + "b"), 0)
        self.assertEqual(sync_wiki_data._parse_seconds("9" * 500), 0.0)
        self.assertEqual(sync_wiki_data._parse_float_pct("9" * 500), 0.0)

        payload = {
            "synced_at_utc": "2026-01-01T00:00:00Z",
            "source": "https://example.test/wiki",
            "merchant_rates_in_spice": {"pig_iron": 6.0, "metal": 2.5, "kryptonite": 3.0, "spice": 1.0},
            "buildings": {
                "pig-iron-mine": {
                    "slug": "pig-iron-mine",
                    "name": "Pig iron mine",
                    "id": 1,
                    "levels": {1: {"cost": {"pig_iron": 60, "metal": 0, "kryptonite": 20, "spice": 0}, "base_time_s": 10.0, "prod": 20}},
                }
            },
            "researches": {},
            "ships": {},
            "defenses": {},
        }
        rendered = sync_wiki_data.render_module(payload)
        ns: dict = {}
        exec(compile(rendered, "generated_game_data.py", "exec"), ns)
        self.assertEqual(ns["safe_int"]("9" * 5000, -1), -1)
        self.assertEqual(ns["cap_json_int"](10**400), 10**200)
        self.assertEqual(ns["safe_big_ratio"](10**400, 100000, cap=1e200), 1e200)
        self.assertIsNone(ns["resolve_unit"]("buildings", "9" * 5000))

        fake_sess = MagicMock()
        fake_sess.__enter__.return_value = fake_sess
        fake_sess.__exit__.return_value = False
        fake_sess.headers = {}
        with (
            patch.object(sync_wiki_data.requests, "Session", return_value=fake_sess),
            patch.object(sync_wiki_data, "_fetch_merchant_rates", side_effect=RuntimeError("offline")),
        ):
            with self.assertRaises(RuntimeError):
                sync_wiki_data.scrape_wiki()
        fake_sess.__exit__.assert_called_once()

    def test_adv_resolve_unit_and_helpers_resist_10e5000_and_sync_parity(self) -> None:
        import json
        from pathlib import Path
        import scripts.sync_wiki_data as sync_wiki_data

        for cat in (None, "building", "buildings", "research", "ships", "defense"):
            self.assertIsNone(game_data.resolve_unit(cat, 10**5000))  # type: ignore[arg-type]
            self.assertIsNone(game_data.resolve_unit(cat, True))  # type: ignore[arg-type]
        self.assertIsNotNone(game_data.resolve_unit("buildings", 1))
        self.assertIsNotNone(game_data.resolve_unit("buildings", "1"))
        self.assertEqual(game_data.building_level_stats(10**5000, 1), {})  # type: ignore[arg-type]
        self.assertEqual(
            game_data.building_cost(10**5000, 1),  # type: ignore[arg-type]
            {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
        )
        self.assertEqual(game_data.mine_energy_needed(10**5000, 1), 0)  # type: ignore[arg-type]
        self.assertEqual(game_data.building_energy_delta(10**5000, 1), 0)  # type: ignore[arg-type]
        self.assertEqual(
            game_data.missing_prerequisites("building", 10**5000, buildings={}, researches={}),  # type: ignore[arg-type]
            [],
        )
        sanitized_map = game_data.sanitize_json_tree({10**5000: 10**5000})
        json.dumps(sanitized_map, allow_nan=False)

        payload = {
            "synced_at_utc": game_data.WIKI_SYNCED_AT_UTC,
            "source": game_data.WIKI_SOURCE_URL,
            "merchant_rates_in_spice": game_data.MERCHANT_RATES_IN_SPICE,
            "buildings": game_data.BUILDINGS,
            "researches": game_data.RESEARCHES,
            "ships": game_data.SHIPS,
            "defenses": game_data.DEFENSES,
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_tables:
            sync_wiki_data.write_catalog_tables(payload, Path(tmp_tables))
            tables_dir = Path("spaceinvasion/core/catalog/tables")
            for fname in ("buildings.json", "research.json", "ships.json", "defenses.json"):
                self.assertEqual(
                    json.loads((Path(tmp_tables) / fname).read_text(encoding="utf-8")),
                    json.loads((tables_dir / fname).read_text(encoding="utf-8")),
                )

    def test_r10_safe_key_tuple_frozenset_bytes_and_wiki_ascii(self) -> None:
        import json
        import math
        from scripts import sync_wiki_data

        huge = 10**5000
        self.assertEqual(game_data.safe_key(b"b\xff"), "b\ufffd")
        tk = game_data.safe_key((huge, "x"))
        fk = game_data.safe_key(frozenset({huge}))
        self.assertTrue(isinstance(tk, str) and tk)
        self.assertTrue(isinstance(fk, str) and fk)
        sanitized = game_data.sanitize_json_tree(
            {(huge, 1): frozenset({huge, b"z"}), "neg": frozenset({-1})}
        )
        dumped = json.dumps(sanitized, allow_nan=False)
        self.assertIn("1" + ("0" * 200), dumped)

        self.assertEqual(game_data.safe_int("\u0664\u0662", 7), 7)
        self.assertEqual(game_data.safe_int(frozenset({1}), 7), 7)
        self.assertEqual(game_data.safe_finite_float("\u0662.\u0665", 3.5), 3.5)
        self.assertEqual(
            game_data.interpolate_temp_value({"\u0662\u0660": "\u0665\u0660"}, 20),  # type: ignore[arg-type]
            0.0,
        )
        self.assertTrue(
            math.isfinite(
                game_data.interpolate_temp_value(
                    {"min": 10, "max": 20, "100": huge}, 50  # type: ignore[arg-type]
                )
            )
        )
        self.assertEqual(sync_wiki_data._parse_int("\u0661\u0662\u0663"), 0)
        self.assertEqual(sync_wiki_data._parse_seconds("\u0661\u0660s"), 0)
        self.assertEqual(sync_wiki_data._parse_float_pct("\u0665\u0660%"), 0.0)

    def test_r12_safe_key_and_sanitize_json_resist_broken_fspath_intpath_and_raise_str(self) -> None:
        import json
        from pathlib import Path
        from scripts import sync_wiki_data
        from spaceinvasion.core import catalog as game_data

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"b1"

        class _RaiseStr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        self.assertIsNone(game_data.resolve_unit("buildings", _RaiseFspath()))
        self.assertIsNone(game_data.resolve_unit("buildings", _IntPath()))
        resolved_b1 = game_data.resolve_unit("buildings", _BytesPath())
        self.assertIsNotNone(resolved_b1)
        assert resolved_b1 is not None
        self.assertEqual(resolved_b1["id"], 1)
        self.assertEqual(resolved_b1["slug"], "pig-iron-mine")

        self.assertEqual(game_data.safe_int(_RaiseFspath(), default=-7), -7)
        self.assertEqual(game_data.safe_int(_IntPath(), default=-7), -7)
        self.assertEqual(game_data.safe_int(10**5000, default=-7), 10**5000)
        self.assertEqual(game_data.cap_json_int(10**5000, limit=100), 100)
        self.assertEqual(game_data.safe_optional_int(_RaiseFspath(), default=-1), -1)
        self.assertIsNone(game_data.safe_optional_int(_IntPath()))
        self.assertEqual(game_data.safe_finite_float(_RaiseFspath(), default=1.5), 1.5)
        self.assertEqual(game_data.safe_finite_float(_IntPath(), default=1.5), 1.5)

        deep_key: object = _IntPath()
        for _ in range(20):
            deep_key = (deep_key,)
        tree = {
            _IntPath(): _RaiseStr(),
            (_RaiseFspath(), _IntPath()): [_RaiseStrRepr(), Path("/tmp/x"), 10**5000],
            frozenset({_IntPath()}): _BytesPath(),
            deep_key: "deep_ok",
        }
        san = game_data.sanitize_json_tree(tree)
        dumped = json.dumps(san, allow_nan=False)
        self.assertIn("1" + ("0" * 200), dumped)
        self.assertIn("deep_ok", dumped)
        self.assertNotIn("0" * 500, dumped)
        self.assertEqual(game_data.safe_key(_RaiseStrRepr()), "_RaiseStrRepr")

        payload = {
            "synced_at_utc": game_data.WIKI_SYNCED_AT_UTC,
            "source": game_data.WIKI_SOURCE_URL,
            "merchant_rates_in_spice": game_data.MERCHANT_RATES_IN_SPICE,
            "buildings": game_data.BUILDINGS,
            "researches": game_data.RESEARCHES,
            "ships": game_data.SHIPS,
            "defenses": game_data.DEFENSES,
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_tables:
            sync_wiki_data.write_catalog_tables(payload, Path(tmp_tables))
            tables_dir = Path("spaceinvasion/core/catalog/tables")
            for fname in ("buildings.json", "research.json", "ships.json", "defenses.json"):
                self.assertEqual(
                    json.loads((Path(tmp_tables) / fname).read_text(encoding="utf-8")),
                    json.loads((tables_dir / fname).read_text(encoding="utf-8")),
                )


    def test_r14_safe_key_and_sanitize_json_dag20_cycles_and_sync_parity(self) -> None:
        import time
        from pathlib import Path
        from spaceinvasion.core import catalog as gd
        from scripts import sync_wiki_data as swd

        cyc_list: list[object] = []
        cyc_list.append(cyc_list)
        cyc_dict: dict[str, object] = {}
        cyc_dict["loop"] = cyc_dict
        self.assertEqual(gd.sanitize_json_tree(cyc_list), ["[Cycle]"])
        self.assertEqual(gd.sanitize_json_tree(cyc_dict), {"loop": "[Cycle]"})
        self.assertIsInstance(gd.safe_key(cyc_list), str)

        exc_cyc = RuntimeError("boom")
        exc_cyc.args = (exc_cyc, {"password": "GD_MAP_SECRET"}, 10**5000)
        sk_exc = gd.safe_key(exc_cyc)
        self.assertIn("'password': 'GD_MAP_SECRET'", sk_exc)

        dag: object = [1, {"k": "v"}]
        for _ in range(20):
            dag = (dag, dag, dag, dag)
        t0 = time.perf_counter()
        _ = gd.safe_key(dag)
        _ = gd.sanitize_json_tree(dag)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertLess(elapsed_ms, 50.0)

        from spaceinvasion.core.catalog import json_sanitize as gd_sanitize

        self.assertIn("_MAX_TREE_NODES = 32_768", swd._HELPER_CODE)
        self.assertIn('"[Cycle]"', Path(gd_sanitize.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
