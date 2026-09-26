"""Unit tests for `spaceinvasion.config` strategy profile loading."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from spaceinvasion.config import (
    StrategyConfig,
    VALID_PLANNER_MODES,
    load_strategy_config,
    normalize_planner_mode,
    save_strategy_config,
    strategy_config_from_dict,
)


class TestStrategyConfig(unittest.TestCase):
    def test_missing_file_returns_safe_defaults(self) -> None:
        cfg = load_strategy_config(Path("/nonexistent/does_not_exist.yaml"))
        self.assertEqual(cfg, StrategyConfig())
        self.assertFalse(cfg.merchant_mode)
        self.assertTrue(cfg.auto_throttle_blast_furnace)
        self.assertFalse(cfg.allow_fleetsave)
        self.assertTrue(cfg.allow_research)
        self.assertEqual(cfg.planner_mode, "priority")
        self.assertEqual(cfg.ga_population_size, 24)
        self.assertEqual(cfg.ga_generations, 12)
        self.assertEqual(cfg.ga_horizon_steps, 6)
        self.assertAlmostEqual(cfg.ga_mutation_rate, 0.25)
        self.assertAlmostEqual(cfg.ga_nn_weight, 0.35)
        self.assertIsNone(cfg.ga_seed)

    def test_loads_config_example_yaml_defaults(self) -> None:
        cfg = load_strategy_config(Path("config.example.yaml"))
        self.assertEqual(cfg.planner_mode, "priority")
        self.assertEqual(cfg.ga_population_size, 24)
        self.assertEqual(cfg.ga_generations, 12)
        self.assertEqual(cfg.ga_horizon_steps, 6)
        self.assertIsNone(cfg.ga_seed)
        self.assertIn("small-fighter", cfg.shipyard_targets)

    def test_ga_nn_planner_mode_yaml_roundtrip_and_zero_seed(self) -> None:
        for seed_yaml, expected_seed in (("42", 42), ("0", 0), ("null", None)):
            yaml_text = f"""
playstyle: miner
planner_mode: ga_nn
ga_population_size: 32
ga_generations: 16
ga_horizon_steps: 8
ga_mutation_rate: 0.3
ga_nn_weight: 0.45
ga_seed: {seed_yaml}
shipyard_targets:
  small-transport: 12
"""
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "config.yaml"
                p.write_text(yaml_text, encoding="utf-8")
                cfg = load_strategy_config(p)
                self.assertEqual(cfg.planner_mode, "ga_nn")
                self.assertEqual(cfg.playstyle, "miner")
                self.assertEqual(cfg.ga_population_size, 32)
                self.assertEqual(cfg.ga_generations, 16)
                self.assertEqual(cfg.ga_horizon_steps, 8)
                self.assertAlmostEqual(cfg.ga_mutation_rate, 0.3)
                self.assertAlmostEqual(cfg.ga_nn_weight, 0.45)
                self.assertEqual(cfg.ga_seed, expected_seed)
                self.assertEqual(cfg.shipyard_targets["small-transport"], 12)

                out = Path(tmp) / "saved.yaml"
                save_strategy_config(cfg, out)
                reloaded = load_strategy_config(out)
                self.assertEqual(reloaded, cfg)

    def test_planner_mode_normalization_and_ga_clamping(self) -> None:
        self.assertEqual(VALID_PLANNER_MODES, ("priority", "ai_planner", "ga_nn"))
        self.assertEqual(normalize_planner_mode(" GA_NN "), "ga_nn")
        self.assertEqual(normalize_planner_mode(" AI_PLANNER "), "ai_planner")
        self.assertEqual(normalize_planner_mode("ai_planner", strict=True), "ai_planner")
        self.assertEqual(normalize_planner_mode("invalid_mode"), "priority")
        self.assertEqual(normalize_planner_mode(""), "priority")
        self.assertEqual(normalize_planner_mode(None), "priority")
        with self.assertRaises(ValueError):
            normalize_planner_mode("bad_mode", strict=True)

        clamped = strategy_config_from_dict(
            {
                "planner_mode": "unknown",
                "ga_population_size": 1,
                "ga_generations": 999,
                "ga_horizon_steps": 0,
                "ga_mutation_rate": 2.5,
                "ga_nn_weight": -0.5,
                "ga_seed": "~",
            }
        )
        self.assertEqual(clamped.planner_mode, "priority")
        self.assertEqual(clamped.ga_population_size, 4)
        self.assertEqual(clamped.ga_generations, 200)
        self.assertEqual(clamped.ga_horizon_steps, 1)
        self.assertAlmostEqual(clamped.ga_mutation_rate, 1.0)
        self.assertAlmostEqual(clamped.ga_nn_weight, 0.0)
        self.assertIsNone(clamped.ga_seed)

        null_nums = strategy_config_from_dict(
            {
                "ga_population_size": None,
                "ga_generations": None,
                "ga_horizon_steps": None,
                "ga_mutation_rate": None,
                "ga_nn_weight": None,
                "ga_seed": 0,
            }
        )
        self.assertEqual(null_nums.ga_population_size, 24)
        self.assertEqual(null_nums.ga_generations, 12)
        self.assertEqual(null_nums.ga_horizon_steps, 6)
        self.assertEqual(null_nums.ga_seed, 0)

    def test_empty_yaml_scalars_and_non_finite_ga_floats_fallback_safely(self) -> None:
        yaml_text = """
planner_mode: ga_nn
ga_population_size:
ga_generations:
ga_horizon_steps:
ga_mutation_rate:
ga_nn_weight:
ga_seed:
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "empty_ga.yaml"
            p.write_text(yaml_text, encoding="utf-8")
            cfg = load_strategy_config(p)
            self.assertEqual(cfg.planner_mode, "ga_nn")
            self.assertEqual(cfg.ga_population_size, 24)
            self.assertEqual(cfg.ga_generations, 12)
            self.assertEqual(cfg.ga_horizon_steps, 6)
            self.assertAlmostEqual(cfg.ga_mutation_rate, 0.25)
            self.assertAlmostEqual(cfg.ga_nn_weight, 0.35)
            self.assertIsNone(cfg.ga_seed)

        bad_cfg = strategy_config_from_dict(
            {
                "ga_population_size": "abc",
                "ga_generations": {},
                "ga_horizon_steps": "bad",
                "ga_mutation_rate": float("nan"),
                "ga_nn_weight": float("inf"),
                "ga_seed": "invalid_seed",
            }
        )
        self.assertEqual(bad_cfg.ga_population_size, 24)
        self.assertEqual(bad_cfg.ga_generations, 12)
        self.assertEqual(bad_cfg.ga_horizon_steps, 6)
        self.assertAlmostEqual(bad_cfg.ga_mutation_rate, 0.25)
        self.assertAlmostEqual(bad_cfg.ga_nn_weight, 0.35)
        self.assertIsNone(bad_cfg.ga_seed)

    def test_loads_nested_yaml_profile_with_comments(self) -> None:
        yaml_text = """
# Strategy override
merchant_mode: true   # Skip Blast furnace post-tutorial
auto_throttle_blast_furnace: false
allow_fleetsave: false
combat_safety_margin: 1.55
shipyard_targets:
  small-transport: 10
  transmitter: 2
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.yaml"
            p.write_text(yaml_text, encoding="utf-8")
            cfg = load_strategy_config(p)
            self.assertTrue(cfg.merchant_mode)
            self.assertFalse(cfg.auto_throttle_blast_furnace)
            self.assertFalse(cfg.allow_fleetsave)
            self.assertAlmostEqual(cfg.combat_safety_margin, 1.55)
            self.assertEqual(cfg.shipyard_targets["small-transport"], 10)
            self.assertEqual(cfg.shipyard_targets["transmitter"], 2)

            out = Path(tmp) / "saved.yaml"
            save_strategy_config(cfg, out)
            reloaded = load_strategy_config(out)
            self.assertEqual(reloaded, cfg)

    def test_malformed_json_and_broken_brace_yaml_scalars_fallback_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_json = Path(tmp) / "broken.json"
            bad_json.write_text("{bad json", encoding="utf-8")
            self.assertEqual(load_strategy_config(bad_json), StrategyConfig())

            bad_brace_yaml = Path(tmp) / "brace.yaml"
            bad_brace_yaml.write_text("ga_seed: {broken\nplanner_mode: ga_nn\n", encoding="utf-8")
            cfg = load_strategy_config(bad_brace_yaml)
            self.assertIsNone(cfg.ga_seed)
            self.assertEqual(cfg.planner_mode, "ga_nn")

    def test_mapping_proxy_non_dict_invalid_utf8_and_non_finite_bool_and_yaml(self) -> None:
        from types import MappingProxyType
        from spaceinvasion.config import _safe_cfg_bool, strategy_config_from_dict

        self.assertEqual(strategy_config_from_dict([1, 2, 3]), StrategyConfig())  # type: ignore[arg-type]
        self.assertEqual(strategy_config_from_dict(None), StrategyConfig())
        proxy_cfg = strategy_config_from_dict(
            MappingProxyType(
                {
                    "planner_mode": "ga_nn",
                    "shipyard_targets": MappingProxyType({"small-transport": 7}),
                }
            )
        )
        self.assertEqual(proxy_cfg.planner_mode, "ga_nn")
        self.assertEqual(proxy_cfg.shipyard_targets["small-transport"], 7)

        self.assertTrue(_safe_cfg_bool(float("nan"), True))
        self.assertFalse(_safe_cfg_bool(float("nan"), False))
        self.assertFalse(_safe_cfg_bool(float("inf"), False))
        self.assertTrue(_safe_cfg_bool(b"true", False))
        self.assertFalse(_safe_cfg_bool(b"false", True))

        with tempfile.TemporaryDirectory() as tmp:
            bad_utf8 = Path(tmp) / "bad.yaml"
            bad_utf8.write_bytes(b"\xff\xfe\xfd")
            self.assertEqual(load_strategy_config(bad_utf8), StrategyConfig())

            nan_cfg = StrategyConfig(combat_safety_margin=float("nan"), storage_warn_ratio=float("inf"))
            out = Path(tmp) / "sanitized.yaml"
            save_strategy_config(nan_cfg, out)
            reloaded = load_strategy_config(out)
            self.assertTrue(0.5 <= reloaded.storage_warn_ratio <= 1.0)
            self.assertGreater(reloaded.combat_safety_margin, 0.0)

    def test_5000_digit_and_extreme_int_clamping_and_null_seed_roundtrip(self) -> None:
        huge_pos = "9" * 5000
        huge_neg = "-" + "9" * 5000
        yaml_text = f"""
playstyle: miner
planner_mode: ga_nn
ga_seed: {huge_pos}
max_actions_per_hour: {huge_pos}
fleet_save_fuel_reserve_spice: {huge_neg}
hot_planet_solar_temp_threshold: {huge_pos}
min_free_fields_warning: {huge_pos}
shipyard_targets:
  small-transport: {huge_pos}
  small-fighter: 0
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "huge.yaml"
            p.write_text(yaml_text, encoding="utf-8")
            cfg = load_strategy_config(p)
            self.assertIsNotNone(cfg.ga_seed)
            self.assertLessEqual(abs(int(cfg.ga_seed or 0)), 10**200)
            self.assertGreaterEqual(cfg.max_actions_per_hour, 1)
            self.assertGreaterEqual(cfg.fleet_save_fuel_reserve_spice, 0)
            self.assertLessEqual(abs(cfg.hot_planet_solar_temp_threshold), 10**200)
            self.assertLessEqual(cfg.min_free_fields_warning, 10**200)
            self.assertLessEqual(cfg.shipyard_targets.get("small-transport", 0), 10**200)

            out = Path(tmp) / "saved.yaml"
            save_strategy_config(cfg, out)
            reloaded = load_strategy_config(out)
            self.assertEqual(reloaded.planner_mode, "ga_nn")

            # Preserve ga_seed=0 and null/~ semantics
            p_tilde = Path(tmp) / "tilde.yaml"
            p_tilde.write_text("ga_seed: ~\n", encoding="utf-8")
            self.assertIsNone(load_strategy_config(p_tilde).ga_seed)
            p_zero = Path(tmp) / "zero.yaml"
            p_zero.write_text("ga_seed: 0\n", encoding="utf-8")
            self.assertEqual(load_strategy_config(p_zero).ga_seed, 0)

        huge_dict_cfg = strategy_config_from_dict(
            {
                "ga_seed": 10**5000,
                "max_actions_per_hour": 10**5000,
                "fleet_save_fuel_reserve_spice": -(10**5000),
                "hot_planet_solar_temp_threshold": 10**5000,
                "min_free_fields_warning": 10**5000,
                "shipyard_targets": {"small-transport": 10**5000, 123: 5},  # type: ignore[dict-item]
            }
        )
        self.assertLessEqual(abs(int(huge_dict_cfg.ga_seed or 0)), 10**200)
        self.assertLessEqual(huge_dict_cfg.shipyard_targets["small-transport"], 10**200)

        with tempfile.TemporaryDirectory() as tmp:
            weird = StrategyConfig(
                playstyle=123,  # type: ignore[arg-type]
                shipyard_targets={456: 2},  # type: ignore[dict-item]
            )
            weird_path = Path(tmp) / "weird.yaml"
            save_strategy_config(weird, weird_path)
            self.assertTrue(weird_path.exists())

    def test_adv_yaml_roundtrip_preserves_hash_and_colon_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for val in ("miner#pro", "a:b # c", 'miner"quote#tag', "123", "true"):
                p = Path(tmp) / "special.yaml"
                cfg = strategy_config_from_dict({"playstyle": val})
                save_strategy_config(cfg, p)
                reloaded = load_strategy_config(p)
                self.assertEqual(reloaded.playstyle, val)

            plain_p = Path(tmp) / "plain.yaml"
            save_strategy_config(strategy_config_from_dict({"playstyle": "fleeter"}), plain_p)
            self.assertIn("playstyle: fleeter\n", plain_p.read_text(encoding="utf-8"))
            self.assertEqual(load_strategy_config(plain_p).playstyle, "fleeter")

    def test_r7_yaml_roundtrip_unicode_line_separators_and_shipyard_target_keys(self) -> None:
        from spaceinvasion.config import DEFAULT_SHIPYARD_TARGETS

        with tempfile.TemporaryDirectory() as tmp:
            for weird_ps in (
                "line1\u2028line2",
                "line1\u2029line2",
                "line1\x0bline2",
                "line1\x0cline2",
                "line1\x1cline2",
                "line1\x85line2",
                "line1\nallow_raids: true",
                "line1\r\nline2",
            ):
                p = Path(tmp) / "ps.yaml"
                cfg = StrategyConfig(playstyle=weird_ps, allow_raids=False)
                save_strategy_config(cfg, p)
                reloaded = load_strategy_config(p)
                self.assertEqual(reloaded.playstyle, weird_ps)
                self.assertFalse(reloaded.allow_raids)

            special_targets = {
                **DEFAULT_SHIPYARD_TARGETS,
                "unit#1": 5,
                "a:b": 3,
                "a:b # c": 8,
                "true": 7,
                "null": 2,
                " 12 ": 4,
                "   ": 9,
                '"quoted"': 6,
                "'single'": 11,
                "a\\": 13,
                "line1\u2028line2": 5,
                "line1\x0bline2": 7,
                "line1\nline2": 10,
                "small-fighter": 99,
            }
            tp = Path(tmp) / "targets.yaml"
            save_strategy_config(StrategyConfig(shipyard_targets=special_targets), tp)
            loaded_t = load_strategy_config(tp)
            for k, v in special_targets.items():
                self.assertEqual(loaded_t.shipyard_targets.get(k), v, msg=f"key={k!r}")
            self.assertNotIn("a", loaded_t.shipyard_targets)
            self.assertNotIn("12", loaded_t.shipyard_targets)

            hand_yaml = (
                "playstyle: line1\u2028line2\n"
                "allow_raids: false\n"
                "shipyard_targets:\n"
                "  line1\x0bline2: 5\n"
                "  'it''s:unit#1': 17  # inline comment\n"
                "  small-fighter: 77\n"
            )
            hp = Path(tmp) / "hand.yaml"
            hp.write_text(hand_yaml, encoding="utf-8")
            loaded_h = load_strategy_config(hp)
            self.assertEqual(loaded_h.playstyle, "line1\u2028line2")
            self.assertFalse(loaded_h.allow_raids)
            self.assertEqual(loaded_h.shipyard_targets["line1\x0bline2"], 5)
            self.assertEqual(loaded_h.shipyard_targets["it's:unit#1"], 17)
            self.assertEqual(loaded_h.shipyard_targets["small-fighter"], 77)

            clamped_int_key = strategy_config_from_dict({"shipyard_targets": {10**5000: 5}})
            self.assertIn(str(10**200), clamped_int_key.shipyard_targets)

    def test_r8_strip_yaml_comment_and_key_value_preserve_mid_token_quotes(self) -> None:
        from spaceinvasion.config import _parse_simple_yaml

        yaml_text = (
            "playstyle: Bob's miner # fav\n"
            "shipyard_targets:\n"
            "  unit'1: 5 # inline comment\n"
            '  unit"1: 7 # inline comment\n'
            "  'it''s:unit#1': 11 # quoted single\n"
            "  small-fighter: 99\n"
        )
        parsed = _parse_simple_yaml(yaml_text)
        self.assertEqual(parsed["playstyle"], "Bob's miner")
        self.assertEqual(parsed["shipyard_targets"]["unit'1"], 5)
        self.assertEqual(parsed["shipyard_targets"]['unit"1'], 7)
        self.assertEqual(parsed["shipyard_targets"]["it's:unit#1"], 11)
        self.assertEqual(parsed["shipyard_targets"]["small-fighter"], 99)

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "r8_quotes.yaml"
            p.write_text(yaml_text, encoding="utf-8")
            cfg = load_strategy_config(p)
            self.assertEqual(cfg.playstyle, "bob's miner")
            self.assertEqual(cfg.shipyard_targets["unit'1"], 5)
            self.assertEqual(cfg.shipyard_targets['unit"1'], 7)
            self.assertEqual(cfg.shipyard_targets["it's:unit#1"], 11)
            self.assertEqual(cfg.shipyard_targets["small-fighter"], 99)

            out = Path(tmp) / "r8_saved.yaml"
            save_strategy_config(cfg, out)
            self.assertEqual(load_strategy_config(out), cfg)

    def test_r9_rejects_arabic_indic_ints_and_huge_playstyle_or_planner_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for fname, txt in (
                (
                    "quoted.yaml",
                    'ga_seed: "\u0664\u0662"\nmax_actions_per_hour: "\u0661\u0665"\nga_population_size: "\u0661\u0666"\n',
                ),
                (
                    "unquoted.yaml",
                    "ga_seed: \u0664\u0662\nmax_actions_per_hour: \u0661\u0665\nga_population_size: \u0661\u0666\n",
                ),
            ):
                p = Path(tmp) / fname
                p.write_text(txt, encoding="utf-8")
                cfg = load_strategy_config(p)
                self.assertIsNone(cfg.ga_seed)
                self.assertEqual(cfg.max_actions_per_hour, 60)
                self.assertEqual(cfg.ga_population_size, 24)

        dict_cfg = strategy_config_from_dict(
            {
                "playstyle": 10**5000,
                "planner_mode": 10**5000,
                "ga_seed": "\u0664\u0662",
                "max_actions_per_hour": "\u0661\u0665",
            }
        )
        self.assertEqual(dict_cfg.playstyle, "fleeter")
        self.assertEqual(dict_cfg.planner_mode, "priority")
        self.assertIsNone(dict_cfg.ga_seed)
        self.assertEqual(dict_cfg.max_actions_per_hour, 60)
        self.assertEqual(normalize_planner_mode(10**5000), "priority")
        with self.assertRaises(ValueError):
            normalize_planner_mode(10**5000, strict=True)

    def test_r10_tuple_frozenset_huge_int_keys_and_save(self) -> None:
        import json
        from dataclasses import asdict
        from spaceinvasion.config import _sanitize_cfg_json_tree, save_strategy_config

        huge = 10**5000
        tree = _sanitize_cfg_json_tree(
            {
                (huge, "slot"): frozenset({huge, b"ok"}),
                frozenset({huge}): {"nested": huge},
            }
        )
        dumped = json.dumps(tree, allow_nan=False)
        self.assertIn("1" + ("0" * 200), dumped)

        cfg = strategy_config_from_dict(
            {
                (huge, "ignored"): 123,  # type: ignore[dict-item]
                frozenset({huge}): 456,  # type: ignore[dict-item]
                "playstyle": "miner",
                "planner_mode": "ga_nn",
            }
        )
        self.assertEqual(cfg.playstyle, "miner")
        self.assertEqual(cfg.planner_mode, "ga_nn")
        with tempfile.TemporaryDirectory() as tmp:
            out_p = Path(tmp) / "cfg.yaml"
            save_strategy_config(cfg, out_p)
            reloaded = load_strategy_config(out_p)
            self.assertEqual(asdict(reloaded)["playstyle"], "miner")
            self.assertEqual(asdict(reloaded)["planner_mode"], "ga_nn")


    def test_r11_frozenset_pathlike_unknown_scalar_and_secret_key_redaction(self) -> None:
        import json
        from pathlib import Path
        from spaceinvasion.config import (
            _safe_cfg_bool,
            _sanitize_cfg_json_tree,
            normalize_planner_mode,
        )

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"https://u/?sid=CFG_BPATH_SEC"

        class _BadStrObj:
            def __str__(self) -> str:
                return "https://u/?sid=CFG_OBJ_SEC"

        class _RaiseStrObj:
            def __str__(self) -> str:
                raise RuntimeError("boom")

        self.assertIs(_safe_cfg_bool(frozenset({"false"}), False), False)
        self.assertIs(_safe_cfg_bool(frozenset({"true"}), False), False)
        self.assertEqual(normalize_planner_mode(Path("ga_nn")), "ga_nn")

        cfg = strategy_config_from_dict(
            {
                "allow_shipyard": frozenset({"false"}),
                "min_free_fields_warning": frozenset({9}),
                "combat_safety_margin": frozenset({2.5}),
                "playstyle": frozenset({"miner"}),
                "planner_mode": frozenset({"ga_nn"}),
            }
        )
        default_cfg = StrategyConfig()
        self.assertEqual(cfg.allow_shipyard, default_cfg.allow_shipyard)
        self.assertEqual(cfg.min_free_fields_warning, default_cfg.min_free_fields_warning)
        self.assertEqual(cfg.combat_safety_margin, default_cfg.combat_safety_margin)
        self.assertEqual(cfg.playstyle, default_cfg.playstyle)
        self.assertEqual(cfg.planner_mode, default_cfg.planner_mode)

        san = _sanitize_cfg_json_tree(
            {
                "p": Path("https://u/?sid=CFG_PATH_SEC"),
                "bp": _BytesPath(),
                "err": RuntimeError("bad ?sid=CFG_ERR_SEC"),
                "obj": _BadStrObj(),
                "boom": _RaiseStrObj(),
                "password": "CFG_PW_SEC",
                "plain": object(),
            }
        )
        dumped = json.dumps(san, allow_nan=False)
        for sec in ("CFG_PATH_SEC", "CFG_BPATH_SEC", "CFG_ERR_SEC", "CFG_OBJ_SEC", "CFG_PW_SEC"):
            self.assertNotIn(sec, dumped)

    def test_r12_config_fspath_raise_str_authorization_bearer_json_kv_and_deep_keys(self) -> None:
        import ast
        import json
        from spaceinvasion import config as cfg_mod

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _RaiseStr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        class _BoolRaise:
            def __bool__(self) -> bool:
                raise RuntimeError("bool boom")

        tree_ast = ast.parse(Path("spaceinvasion/core/config.py").read_text(encoding="utf-8"))
        internal_imports = [
            ast.dump(node)
            for node in ast.walk(tree_ast)
            if (isinstance(node, ast.Import) and any(a.name.startswith("spaceinvasion") for a in node.names))
            or (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("spaceinvasion"))
        ]
        self.assertEqual(internal_imports, [])

        self.assertEqual(normalize_planner_mode(_RaiseFspath()), "priority")
        self.assertEqual(normalize_planner_mode(_IntPath()), "priority")
        self.assertEqual(normalize_planner_mode(_RaiseStr()), "priority")
        self.assertIsInstance(cfg_mod._safe_cfg_key_repr(_RaiseStrRepr()), str)
        self.assertIsInstance(cfg_mod._safe_cfg_key(_RaiseFspath()), str)
        self.assertEqual(cfg_mod._safe_cfg_int(_RaiseFspath(), 7), 7)
        self.assertEqual(cfg_mod._safe_cfg_int(_IntPath(), 7), 7)
        self.assertIsNone(cfg_mod._safe_cfg_optional_int(_RaiseFspath()))
        self.assertEqual(cfg_mod._safe_cfg_float(_RaiseFspath(), 0.25), 0.25)
        self.assertFalse(cfg_mod._safe_cfg_bool(_BoolRaise(), False))

        cfg = strategy_config_from_dict(
            {
                _IntPath(): 1,
                _RaiseFspath(): 2,
                "planner_mode": _RaiseFspath(),
                "playstyle": _RaiseStrRepr(),
                "shipyard_targets": {_IntPath(): 1, _RaiseFspath(): 2},
            }
        )
        self.assertEqual(cfg.planner_mode, "priority")

        with tempfile.TemporaryDirectory() as tmp:
            ok_path = Path(tmp) / "ok.yaml"
            save_strategy_config(cfg, ok_path)
            self.assertTrue(ok_path.exists())
            bad_cfg = StrategyConfig(
                playstyle=_RaiseStrRepr(),  # type: ignore[arg-type]
                merchant_mode=_BoolRaise(),  # type: ignore[arg-type]
                storage_warn_ratio=float("nan"),
                ga_population_size=_IntPath(),  # type: ignore[arg-type]
                shipyard_targets={_RaiseFspath(): 1, "small-fighter": _RaiseFspath()},  # type: ignore[dict-item]
            )
            bad_path = Path(tmp) / "bad_ok.yaml"
            save_strategy_config(bad_cfg, bad_path)
            self.assertTrue(bad_path.exists())
            with self.assertRaises((AttributeError, RuntimeError, TypeError)):
                save_strategy_config(cfg, _RaiseFspath())  # type: ignore[arg-type]

        deep_key: object = Path("?password=DP_5_SECRET")
        for _ in range(5):
            deep_key = (deep_key,)
        san = cfg_mod._sanitize_cfg_json_tree(
            {
                "auth_header": "Authorization: Bearer AUTH_TOK_99",
                "json_blob": '{"password": "JSON_PW_SECRET", "api_key": "JSON_KEY_SECRET"}',
                "authorization": "DICT_AUTH_SEC",
                "long_s": "\u017fid=LATIN_LONG_S_SEC",
                deep_key: "ok",
                "exc": RuntimeError(10**5000, "?password=EP_SECRET"),
                "broken_key_map": {_RaiseStrRepr(): _RaiseFspath(), _IntPath(): "ok"},
            }
        )
        dumped = json.dumps(san, allow_nan=False)
        for sec in (
            "AUTH_TOK_99",
            "JSON_PW_SECRET",
            "JSON_KEY_SECRET",
            "DICT_AUTH_SEC",
            "LATIN_LONG_S_SEC",
            "DP_5_SECRET",
            "EP_SECRET",
        ):
            self.assertNotIn(sec, dumped)
        self.assertIn("authorization: bearer redacted", dumped.lower())

    def test_r13_config_compound_secrets_unicode_fastpath_quoted_bearer_and_shipyard_targets(self) -> None:
        import json
        from spaceinvasion import config as cfg_mod

        san_text_cases = (
            ("client_secret=CS_1&user_password=UP_2&bot_api_key=BK_3&session_cookie=SC_4&access_passwd=AP_5&session_sid=SS_6",
             ("CS_1", "UP_2", "BK_3", "SC_4", "AP_5", "SS_6")),
            ("oauth_authorization=Bearer+OAUTH_TOK&my_authorization=PLAIN_AUTH",
             ("OAUTH_TOK", "PLAIN_AUTH")),
            ('Authorization: Bearer "QUOTED_BEAR" Bearer <ANG_BEAR> Authorization: Bearer [BRK_BEAR]',
             ("QUOTED_BEAR", "ANG_BEAR", "BRK_BEAR")),
            ("Authorization=Bearer%20%22ENC_Q_BEAR%22&x=Bearer%20%3cENC_ANG%3e",
             ("ENC_Q_BEAR", "ENC_ANG")),
            ("?s\u0130d=TURK_I1&?s\u0131d=TURK_I2&?cook\u0130e=TURK_CK&?ap\u0131_key=TURK_AK&?cl\u0130ent_secret=TURK_CS",
             ("TURK_I1", "TURK_I2", "TURK_CK", "TURK_AK", "TURK_CS")),
        )
        for raw, secrets_list in san_text_cases:
            red = cfg_mod._redact_cfg_text(raw)
            for sec in secrets_list:
                self.assertNotIn(sec, red, f"Leaked {sec} in {red!r}")
            self.assertIn("REDACTED", red)

        self.assertEqual(cfg_mod._redact_cfg_text('Authorization: Bearer "SECRET"'), 'Authorization: Bearer "REDACTED"')
        self.assertEqual(cfg_mod._redact_cfg_text("Bearer <SECRET>"), "Bearer <REDACTED>")
        self.assertEqual(cfg_mod._redact_cfg_text("Authorization: Bearer [SECRET]"), "Authorization: Bearer [REDACTED]")
        self.assertEqual(cfg_mod._redact_cfg_text('Authorization: Bearer ""'), 'Authorization: Bearer ""')
        self.assertEqual(cfg_mod._redact_cfg_text("Authorization: Bearer <>"), "Authorization: Bearer <>")
        self.assertEqual(cfg_mod._redact_cfg_text("Authorization: Bearer []"), "Authorization: Bearer []")
        self.assertEqual(
            cfg_mod._redact_cfg_text('{"auth_header": "Authorization: Bearer MY_TOK"}'),
            '{"auth_header": "Authorization: Bearer REDACTED"}',
        )

        benign = "has_password=true&has_sid=false&no_secret=false&is_secret=false&cookie_path=/tmp/c&cookie_names=a,b&token=ok&bsid=123"
        self.assertEqual(cfg_mod._redact_cfg_text(benign), benign)

        san_tree = cfg_mod._sanitize_cfg_json_tree(
            {
                "client_secret": "DICT_CSEC",
                "user_password": "DICT_UPW",
                "bot_api_key": "DICT_BKEY",
                "session_cookie": "DICT_SC",
                "X-API-Key": "DICT_XAPI",
                "proxy_authorization": "DICT_PAUTH",
                "session_sid": "DICT_SSID",
                "has_password": "true",
                "has_sid": "false",
                "no_secret": "false",
                "cookie_path": "/tmp/cookies.json",
                "cookie_names": ["PHPSESSID"],
                "token": "benign_tok",
                "json_str": '{"client_secret": "JSON_CSEC", "has_password": "true"}',
            }
        )
        self.assertEqual(san_tree["has_password"], "true")
        self.assertEqual(san_tree["has_sid"], "false")
        self.assertEqual(san_tree["no_secret"], "false")
        self.assertEqual(san_tree["cookie_path"], "/tmp/cookies.json")
        self.assertEqual(san_tree["token"], "benign_tok")
        self.assertIn('"has_password": "true"', san_tree["json_str"])
        dumped = json.dumps(san_tree, allow_nan=False)
        for sec in ("DICT_CSEC", "DICT_UPW", "DICT_BKEY", "DICT_SC", "DICT_XAPI", "DICT_PAUTH", "DICT_SSID", "JSON_CSEC"):
            self.assertNotIn(sec, dumped)

        cyc_exc = RuntimeError("cyc")
        cyc_exc.args = (cyc_exc, cyc_exc)
        self.assertIsInstance(cfg_mod._safe_cfg_exc_text(cyc_exc), str)


    def test_r14_config_shipyard_aliases_urlenc_json_esc_quotes_and_dag20_cycles(self) -> None:
        import time
        from spaceinvasion.config import (
            _parse_simple_yaml,
            _redact_cfg_text,
            _safe_cfg_exc_text,
            _safe_cfg_key,
            _sanitize_cfg_json_tree,
            save_strategy_config,
            strategy_config_from_dict,
        )

        # 1. shipyard_targets canonicalization + last-wins + bare digits preserved verbatim
        cfg = strategy_config_from_dict(
            {
                "shipyard_targets": {
                    "s4": 10,
                    "Small fighter": 25,
                    "ship[13]": 3,
                    "1": 7,
                    " 12 ": 9,
                    "custom_unit": 4,
                }
            }
        )
        self.assertEqual(cfg.shipyard_targets.get("small-fighter"), 25)
        self.assertEqual(cfg.shipyard_targets.get("battleship"), 3)
        self.assertEqual(cfg.shipyard_targets.get("1"), 7)
        self.assertEqual(cfg.shipyard_targets.get(" 12 "), 9)
        self.assertEqual(cfg.shipyard_targets.get("custom_unit"), 4)
        self.assertNotIn("s4", cfg.shipyard_targets)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "strategy.yaml"
            save_strategy_config(cfg, out_path)
            saved_text = out_path.read_text(encoding="utf-8")
            self.assertIn("small-fighter: 25", saved_text)
            self.assertIn("battleship: 3", saved_text)
            self.assertNotIn("s4:", saved_text)

        # 2. _parse_simple_yaml preserves U+2028 and \x0b inside single scalar lines
        yaml_u2028 = "account_name: 'a\u2028b\x0bc'\n"
        self.assertEqual(_parse_simple_yaml(yaml_u2028)["account_name"], "a\u2028b\x0bc")

        # 3. Linear redaction: urlenc JSON, escaped JSON quotes, spaced brackets, unclosed, schemeless Bearer, zero ReDoS
        vectors = (
            '%22password%22%3A%22CFG_UE_PW%22',
            '%22Bearer%20CFG_UE_BR%22',
            'Bearer %3CCFG_UE_ANG%3E',
            'Bearer %5BCFG_UE_BRK%5D',
            '\\"password\\":\\"CFG_ESC_PW\\"',
            '\\\\"password\\\\":\\\\"CFG_DESC_PW\\\\"',
            'Bearer [MY CFG SECRET]',
            'Bearer <MY CFG ANG>',
            'Bearer "UNCLOSED_CFG_Q',
            'Bearer CFG_SCHEMELESS_BR',
        )
        for v in vectors:
            red = _redact_cfg_text(v)
            for marker in ("CFG_UE_PW", "CFG_UE_BR", "CFG_UE_ANG", "CFG_UE_BRK", "CFG_ESC_PW", "CFG_DESC_PW", "MY CFG SECRET", "MY CFG ANG", "UNCLOSED_CFG_Q", "CFG_SCHEMELESS_BR"):
                self.assertNotIn(marker, red, f"Leaked {marker} in {red!r}")
        # Bearer %3C...%3E / %5B...%5D check directly on delimited tokens
        self.assertNotIn("CFG_SEC1", _redact_cfg_text("Bearer %3CCFG_SEC1%3E"))
        self.assertNotIn("CFG_SEC2", _redact_cfg_text("Bearer %5BCFG_SEC2%5D"))

        self.assertEqual(_redact_cfg_text("Authorization: Bearer REDACTED"), "Authorization: Bearer REDACTED")
        self.assertEqual(_redact_cfg_text("has_sid=false&has_password=true"), "has_sid=false&has_password=true")

        redos_s = 'password=' + ('\\' * 5000) + '"'
        t0 = time.perf_counter()
        _ = _redact_cfg_text(redos_s)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 250.0)

        # 4. dag20 + cycles in _safe_cfg_exc_text / _safe_cfg_key / _sanitize_cfg_json_tree
        cyc: list[object] = []
        cyc.append(cyc)
        self.assertEqual(_sanitize_cfg_json_tree(cyc), ["[Cycle]"])
        exc_cyc = RuntimeError("Bearer CFG_EXC_SEC")
        exc_cyc.args = (exc_cyc, {"password": "CFG_MAP_SEC"})
        exc_s = str(_sanitize_cfg_json_tree(exc_cyc))
        self.assertNotIn("CFG_EXC_SEC", exc_s)
        self.assertNotIn("CFG_MAP_SEC", exc_s)
        self.assertIsInstance(_safe_cfg_exc_text(exc_cyc), str)

        dag_exc: BaseException = RuntimeError("leaf")
        for _ in range(20):
            dag_exc = RuntimeError(dag_exc, dag_exc, dag_exc, dag_exc)
        t1 = time.perf_counter()
        self.assertIsInstance(_safe_cfg_exc_text(dag_exc), str)
        self.assertIsInstance(_safe_cfg_key(dag_exc), str)
        self.assertLess((time.perf_counter() - t1) * 1000.0, 50.0)

    def test_galaxy_scan_delay_jitter_and_spy_config_fields_clamping_and_roundtrip(self) -> None:
        from spaceinvasion.db.storage import GameDatabase

        defaults = StrategyConfig()
        self.assertAlmostEqual(defaults.galaxy_scan_click_delay_seconds, 1.5)
        self.assertAlmostEqual(defaults.galaxy_scan_click_jitter_seconds, 0.6)
        self.assertEqual(defaults.reserved_spy_slots, 1)
        self.assertEqual(defaults.spy_dispatch_planet_id, 0)

        low_clamped = strategy_config_from_dict(
            {
                "galaxy_scan_click_delay_seconds": 0.01,
                "galaxy_scan_click_jitter_seconds": -5.0,
                "reserved_spy_slots": -3,
                "spy_dispatch_planet_id": -99,
            }
        )
        self.assertAlmostEqual(low_clamped.galaxy_scan_click_delay_seconds, 0.2)
        self.assertAlmostEqual(low_clamped.galaxy_scan_click_jitter_seconds, 0.0)
        self.assertEqual(low_clamped.reserved_spy_slots, 0)
        self.assertEqual(low_clamped.spy_dispatch_planet_id, 0)

        high_clamped = strategy_config_from_dict(
            {
                "galaxy_scan_click_delay_seconds": 500.0,
                "galaxy_scan_click_jitter_seconds": 120.0,
                "reserved_spy_slots": 99,
                "spy_dispatch_planet_id": None,
            }
        )
        self.assertAlmostEqual(high_clamped.galaxy_scan_click_delay_seconds, 60.0)
        self.assertAlmostEqual(high_clamped.galaxy_scan_click_jitter_seconds, 30.0)
        self.assertEqual(high_clamped.reserved_spy_slots, 10)
        self.assertEqual(high_clamped.spy_dispatch_planet_id, 0)

        empty_pid = strategy_config_from_dict({"spy_dispatch_planet_id": ""})
        self.assertEqual(empty_pid.spy_dispatch_planet_id, 0)

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg = StrategyConfig(
                galaxy_scan_click_delay_seconds=2.75,
                galaxy_scan_click_jitter_seconds=0.85,
                reserved_spy_slots=3,
                spy_dispatch_planet_id=12476,
            )
            yaml_path = tp / "cfg.yaml"
            save_strategy_config(cfg, yaml_path)
            loaded_yaml = load_strategy_config(yaml_path)
            self.assertAlmostEqual(loaded_yaml.galaxy_scan_click_delay_seconds, 2.75)
            self.assertAlmostEqual(loaded_yaml.galaxy_scan_click_jitter_seconds, 0.85)
            self.assertEqual(loaded_yaml.reserved_spy_slots, 3)
            self.assertEqual(loaded_yaml.spy_dispatch_planet_id, 12476)

            db_path = tp / "cfg.db"
            db = GameDatabase(db_path, attack_history_path="")
            db.close()
            save_strategy_config(cfg, db_path)
            loaded_db = load_strategy_config(db_path)
            self.assertAlmostEqual(loaded_db.galaxy_scan_click_delay_seconds, 2.75)
            self.assertAlmostEqual(loaded_db.galaxy_scan_click_jitter_seconds, 0.85)
            self.assertEqual(loaded_db.reserved_spy_slots, 3)
            self.assertEqual(loaded_db.spy_dispatch_planet_id, 12476)

    def test_ai_planner_mode_and_spare_pct_roundtrip_and_clamping(self) -> None:
        from spaceinvasion.db.storage import GameDatabase

        default_cfg = StrategyConfig()
        self.assertAlmostEqual(default_cfg.ai_resource_spare_pct, 10.0)

        low_c = strategy_config_from_dict(
            {"planner_mode": "AI_PLANNER", "ai_resource_spare_pct": -15.0}
        )
        self.assertEqual(low_c.planner_mode, "ai_planner")
        self.assertAlmostEqual(low_c.ai_resource_spare_pct, 0.0)

        high_c = strategy_config_from_dict(
            {"planner_mode": "ai_planner", "ai_resource_spare_pct": 250.0}
        )
        self.assertEqual(high_c.planner_mode, "ai_planner")
        self.assertAlmostEqual(high_c.ai_resource_spare_pct, 100.0)

        nan_c = strategy_config_from_dict({"ai_resource_spare_pct": float("nan")})
        self.assertAlmostEqual(nan_c.ai_resource_spare_pct, 10.0)

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg = StrategyConfig(planner_mode="ai_planner", ai_resource_spare_pct=17.5)
            yaml_path = tp / "ai_cfg.yaml"
            save_strategy_config(cfg, yaml_path)
            loaded_yaml = load_strategy_config(yaml_path)
            self.assertEqual(loaded_yaml.planner_mode, "ai_planner")
            self.assertAlmostEqual(loaded_yaml.ai_resource_spare_pct, 17.5)

            db_path = tp / "ai_cfg.db"
            db = GameDatabase(db_path, attack_history_path="")
            db.close()
            save_strategy_config(cfg, db_path)
            loaded_db = load_strategy_config(db_path)
            self.assertEqual(loaded_db.planner_mode, "ai_planner")
            self.assertAlmostEqual(loaded_db.ai_resource_spare_pct, 17.5)

    def test_fleet_research_targets_and_allow_fleet_research_roundtrip(self) -> None:
        from spaceinvasion.db.storage import GameDatabase

        default_cfg = StrategyConfig()
        self.assertTrue(default_cfg.allow_fleet_research)
        self.assertEqual(default_cfg.fleet_research_targets, {})

        parsed = strategy_config_from_dict(
            {
                "allow_fleet_research": False,
                "fleet_research_targets": {
                    "weapons-technology": 8,
                    "research[9]": 120,  # clamps to 100
                    "r6": 5,
                    "Defense Systems": 0,  # stripped when <= 0
                    "r10": -3,  # stripped when <= 0
                },
            }
        )
        self.assertFalse(parsed.allow_fleet_research)
        self.assertEqual(parsed.fleet_research_targets, {"r8": 8, "r9": 100, "r6": 5})

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg = StrategyConfig(
                allow_fleet_research=True,
                fleet_research_targets={"r6": 6, "r9": 8, "r11": 5},
            )
            yaml_path = tp / "fleet_res.yaml"
            save_strategy_config(cfg, yaml_path)
            loaded_yaml = load_strategy_config(yaml_path)
            self.assertTrue(loaded_yaml.allow_fleet_research)
            self.assertEqual(loaded_yaml.fleet_research_targets, {"r6": 6, "r9": 8, "r11": 5})

            # Clearing targets back to {} omits section and round-trips cleanly
            empty_cfg = StrategyConfig(allow_fleet_research=False, fleet_research_targets={})
            empty_yaml = tp / "fleet_res_empty.yaml"
            save_strategy_config(empty_cfg, empty_yaml)
            self.assertNotIn("fleet_research_targets:", empty_yaml.read_text(encoding="utf-8"))
            loaded_empty = load_strategy_config(empty_yaml)
            self.assertFalse(loaded_empty.allow_fleet_research)
            self.assertEqual(loaded_empty.fleet_research_targets, {})

            db_path = tp / "fleet_res.db"
            db = GameDatabase(db_path, attack_history_path="")
            db.close()
            save_strategy_config(cfg, db_path)
            loaded_db = load_strategy_config(db_path)
            self.assertTrue(loaded_db.allow_fleet_research)
            self.assertEqual(loaded_db.fleet_research_targets, {"r6": 6, "r9": 8, "r11": 5})

    def test_spy_reserved_slots_only_default_and_roundtrip(self) -> None:
        from spaceinvasion.db.storage import GameDatabase

        default_cfg = StrategyConfig()
        self.assertFalse(default_cfg.spy_reserved_slots_only)

        parsed = strategy_config_from_dict(
            {
                "reserved_spy_slots": 2,
                "spy_reserved_slots_only": True,
            }
        )
        self.assertEqual(parsed.reserved_spy_slots, 2)
        self.assertTrue(parsed.spy_reserved_slots_only)

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            yaml_path = tp / "spy_res_only.yaml"
            save_strategy_config(parsed, yaml_path)
            self.assertIn("spy_reserved_slots_only: true", yaml_path.read_text(encoding="utf-8"))
            loaded_yaml = load_strategy_config(yaml_path)
            self.assertTrue(loaded_yaml.spy_reserved_slots_only)
            self.assertEqual(loaded_yaml.reserved_spy_slots, 2)

            db_path = tp / "spy_res_only.db"
            db = GameDatabase(db_path, attack_history_path="")
            db.close()
            save_strategy_config(parsed, db_path)
            loaded_db = load_strategy_config(db_path)
            self.assertTrue(loaded_db.spy_reserved_slots_only)
            self.assertEqual(loaded_db.reserved_spy_slots, 2)


if __name__ == "__main__":
    unittest.main()
