"""Unit tests for `spaceinvasion.config` strategy profile loading."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from spaceinvasion.config import StrategyConfig, load_strategy_config


class TestStrategyConfig(unittest.TestCase):
    def test_missing_file_returns_safe_defaults(self) -> None:
        cfg = load_strategy_config(Path("/nonexistent/does_not_exist.yaml"))
        self.assertEqual(cfg, StrategyConfig())
        self.assertFalse(cfg.merchant_mode)
        self.assertFalse(cfg.allow_fleetsave)
        self.assertTrue(cfg.allow_research)

    def test_loads_nested_yaml_profile_with_comments(self) -> None:
        yaml_text = """
# Strategy override
merchant_mode: true   # Skip Blast furnace post-tutorial
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
            self.assertFalse(cfg.allow_fleetsave)
            self.assertAlmostEqual(cfg.combat_safety_margin, 1.55)
            self.assertEqual(cfg.shipyard_targets["small-transport"], 10)
            self.assertEqual(cfg.shipyard_targets["transmitter"], 2)


if __name__ == "__main__":
    unittest.main()
