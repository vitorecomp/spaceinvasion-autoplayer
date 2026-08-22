"""Non-secret strategy profile loader (`config.yaml` / `config.json`) for SpaceInvasion."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

DEFAULT_SHIPYARD_TARGETS: dict[str, int] = {
    "small-fighter": 12,
    "small-transport": 6,
    "spy-probe": 10,
    "colony-ship": 1,
    "recycler": 4,
    "starcruiser": 6,
    "phoenix": 10,
    "transmitter": 6,
    "battleship": 8,
    "stealth-bomber": 4,
    "destroyer": 4,
}


@dataclass(frozen=True)
class StrategyConfig:
    """Empire strategy toggles and safety thresholds loaded from `config.yaml`."""

    playstyle: str = "fleeter"  # "fleeter" | "hybrid" | "miner"
    merchant_mode: bool = False
    allow_research: bool = True
    allow_shipyard: bool = True
    allow_multi_planet: bool = True
    allow_fleetsave: bool = False
    allow_galaxy_scan: bool = False
    allow_raids: bool = False
    allow_pvp_in_az: bool = False
    allow_colonize: bool = False
    storage_warn_ratio: float = 0.85
    blast_pig_iron_max_share: float = 0.70
    combat_safety_margin: float = 1.4
    raid_min_loot_spice_eq: float = 1500.0
    fleet_save_fuel_reserve_spice: int = 500
    max_actions_per_hour: int = 60
    hot_planet_solar_temp_threshold: int = 40
    min_free_fields_warning: int = 20
    shipyard_targets: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SHIPYARD_TARGETS)
    )


def _strip_yaml_comment(line: str) -> str:
    in_quote: str | None = None
    for idx, ch in enumerate(line):
        if ch in ('"', "'"):
            if in_quote is None:
                in_quote = ch
            elif in_quote == ch:
                in_quote = None
        elif ch == "#" and in_quote is None:
            return line[:idx]
    return line


def _parse_scalar(raw: str) -> Any:
    val = raw.strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    low = val.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none", "~"):
        return None
    if val.startswith("{") or val.startswith("["):
        return json.loads(val)
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse flat and one-level nested `key: value` YAML without external dependencies."""
    root: dict[str, Any] = {}
    current_section: str | None = None
    for raw_line in text.splitlines():
        cleaned = _strip_yaml_comment(raw_line).rstrip()
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        stripped = cleaned.strip()
        if ":" not in stripped:
            continue
        key, raw_val = stripped.split(":", 1)
        key = key.strip()
        val_part = raw_val.strip()
        if indent == 0:
            if not val_part:
                root[key] = {}
                current_section = key
            else:
                root[key] = _parse_scalar(val_part)
                current_section = None
        elif current_section is not None and isinstance(root.get(current_section), dict):
            root[current_section][key] = _parse_scalar(val_part)
    return root


def strategy_config_from_dict(data: dict[str, Any]) -> StrategyConfig:
    """Build a validated `StrategyConfig` from parsed mapping data."""
    targets = dict(DEFAULT_SHIPYARD_TARGETS)
    raw_targets = data.get("shipyard_targets")
    if isinstance(raw_targets, dict):
        for k, v in raw_targets.items():
            targets[str(k)] = max(0, int(v))

    return StrategyConfig(
        playstyle=str(data.get("playstyle", "fleeter")).strip().lower() or "fleeter",
        merchant_mode=bool(data.get("merchant_mode", False)),
        allow_research=bool(data.get("allow_research", True)),
        allow_shipyard=bool(data.get("allow_shipyard", True)),
        allow_multi_planet=bool(data.get("allow_multi_planet", True)),
        allow_fleetsave=bool(data.get("allow_fleetsave", False)),
        allow_galaxy_scan=bool(data.get("allow_galaxy_scan", False)),
        allow_raids=bool(data.get("allow_raids", False)),
        allow_pvp_in_az=bool(data.get("allow_pvp_in_az", False)),
        allow_colonize=bool(data.get("allow_colonize", False)),
        storage_warn_ratio=max(0.1, min(0.99, float(data.get("storage_warn_ratio", 0.85)))),
        blast_pig_iron_max_share=max(0.1, min(1.0, float(data.get("blast_pig_iron_max_share", 0.70)))),
        combat_safety_margin=max(1.0, float(data.get("combat_safety_margin", 1.4))),
        raid_min_loot_spice_eq=max(0.0, float(data.get("raid_min_loot_spice_eq", 1500.0))),
        fleet_save_fuel_reserve_spice=max(0, int(data.get("fleet_save_fuel_reserve_spice", 500))),
        max_actions_per_hour=max(1, int(data.get("max_actions_per_hour", 60))),
        hot_planet_solar_temp_threshold=int(data.get("hot_planet_solar_temp_threshold", 40)),
        min_free_fields_warning=max(1, int(data.get("min_free_fields_warning", 20))),
        shipyard_targets=targets,
    )


def load_strategy_config(path: Path = Path("config.yaml")) -> StrategyConfig:
    """Load `StrategyConfig` from `path` (YAML or JSON), falling back to safe defaults."""
    if not path.exists():
        return StrategyConfig()
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return StrategyConfig()
    if path.suffix.lower() == ".json":
        parsed = json.loads(text)
    else:
        parsed = _parse_simple_yaml(text)
    if not isinstance(parsed, dict):
        return StrategyConfig()
    return strategy_config_from_dict(parsed)
