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
        self.assertEqual(VALID_PLANNER_MODES, ("priority", "ga_nn"))
        self.assertEqual(normalize_planner_mode(" GA_NN "), "ga_nn")
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


if __name__ == "__main__":
    unittest.main()
