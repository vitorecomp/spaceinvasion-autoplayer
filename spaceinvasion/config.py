"""Non-secret strategy profile loader (`config.yaml` / `config.json`) for SpaceInvasion."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Optional

VALID_PLANNER_MODES: tuple[str, ...] = ("priority", "ga_nn")


def normalize_planner_mode(value: Any, *, strict: bool = False) -> str:
    """Validate and normalize `planner_mode` (`'priority'` default or `'ga_nn'`)."""
    if value is None:
        return "priority"
    mode = str(value).strip().lower()
    if not mode:
        return "priority"
    if mode in VALID_PLANNER_MODES:
        return mode
    if strict:
        raise ValueError(
            f"Unsupported planner_mode {value!r}; expected one of {VALID_PLANNER_MODES}."
        )
    return "priority"

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
    auto_throttle_blast_furnace: bool = True
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
    planner_mode: str = "priority"  # "priority" | "ga_nn"
    ga_population_size: int = 24
    ga_generations: int = 12
    ga_horizon_steps: int = 6
    ga_mutation_rate: float = 0.25
    ga_nn_weight: float = 0.35
    ga_seed: Optional[int] = None
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
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return val
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


def _safe_cfg_int(value: Any, default: int) -> int:
    if value is None or isinstance(value, (dict, list, bool)):
        return default
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "~"):
        return default
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return default
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_cfg_optional_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or isinstance(value, (dict, list, bool)):
        return default
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "~"):
        return default
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return default
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_cfg_float(value: Any, default: float) -> float:
    if value is None or isinstance(value, (dict, list, bool)):
        return default
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "~"):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_cfg_bool(value: Any, default: bool) -> bool:
    if value is None or isinstance(value, (dict, list)):
        return default
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("", "null", "none", "~"):
            return default
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        return default
    return bool(value)


def strategy_config_from_dict(data: dict[str, Any]) -> StrategyConfig:
    """Build a validated `StrategyConfig` from parsed mapping data."""
    targets = dict(DEFAULT_SHIPYARD_TARGETS)
    raw_targets = data.get("shipyard_targets")
    if isinstance(raw_targets, dict):
        for k, v in raw_targets.items():
            targets[str(k)] = max(0, _safe_cfg_int(v, targets.get(str(k), 0)))

    raw_playstyle = data.get("playstyle", "fleeter")
    if raw_playstyle is None or isinstance(raw_playstyle, (dict, list)):
        playstyle = "fleeter"
    else:
        playstyle = str(raw_playstyle).strip().lower() or "fleeter"

    raw_mode = data.get("planner_mode", "priority")
    if isinstance(raw_mode, dict) and not raw_mode:
        raw_mode = "priority"

    return StrategyConfig(
        playstyle=playstyle,
        merchant_mode=_safe_cfg_bool(data.get("merchant_mode"), False),
        auto_throttle_blast_furnace=_safe_cfg_bool(data.get("auto_throttle_blast_furnace"), True),
        allow_research=_safe_cfg_bool(data.get("allow_research"), True),
        allow_shipyard=_safe_cfg_bool(data.get("allow_shipyard"), True),
        allow_multi_planet=_safe_cfg_bool(data.get("allow_multi_planet"), True),
        allow_fleetsave=_safe_cfg_bool(data.get("allow_fleetsave"), False),
        allow_galaxy_scan=_safe_cfg_bool(data.get("allow_galaxy_scan"), False),
        allow_raids=_safe_cfg_bool(data.get("allow_raids"), False),
        allow_pvp_in_az=_safe_cfg_bool(data.get("allow_pvp_in_az"), False),
        allow_colonize=_safe_cfg_bool(data.get("allow_colonize"), False),
        storage_warn_ratio=max(0.1, min(0.99, _safe_cfg_float(data.get("storage_warn_ratio"), 0.85))),
        blast_pig_iron_max_share=max(0.1, min(1.0, _safe_cfg_float(data.get("blast_pig_iron_max_share"), 0.70))),
        combat_safety_margin=max(1.0, _safe_cfg_float(data.get("combat_safety_margin"), 1.4)),
        raid_min_loot_spice_eq=max(0.0, _safe_cfg_float(data.get("raid_min_loot_spice_eq"), 1500.0)),
        fleet_save_fuel_reserve_spice=max(0, _safe_cfg_int(data.get("fleet_save_fuel_reserve_spice"), 500)),
        max_actions_per_hour=max(1, _safe_cfg_int(data.get("max_actions_per_hour"), 60)),
        hot_planet_solar_temp_threshold=_safe_cfg_int(data.get("hot_planet_solar_temp_threshold"), 40),
        min_free_fields_warning=max(1, _safe_cfg_int(data.get("min_free_fields_warning"), 20)),
        planner_mode=normalize_planner_mode(raw_mode),
        ga_population_size=max(4, min(200, _safe_cfg_int(data.get("ga_population_size"), 24))),
        ga_generations=max(1, min(200, _safe_cfg_int(data.get("ga_generations"), 12))),
        ga_horizon_steps=max(1, min(24, _safe_cfg_int(data.get("ga_horizon_steps"), 6))),
        ga_mutation_rate=max(0.0, min(1.0, _safe_cfg_float(data.get("ga_mutation_rate"), 0.25))),
        ga_nn_weight=max(0.0, min(1.0, _safe_cfg_float(data.get("ga_nn_weight"), 0.35))),
        ga_seed=_safe_cfg_optional_int(data.get("ga_seed"), None),
        shipyard_targets=targets,
    )


def load_strategy_config(path: Path = Path("config.yaml")) -> StrategyConfig:
    """Load `StrategyConfig` from `path` (YAML or JSON), falling back to safe defaults."""
    if not path.exists():
        return StrategyConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return StrategyConfig()
    if not text.strip():
        return StrategyConfig()
    if path.suffix.lower() == ".json":
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return StrategyConfig()
    else:
        parsed = _parse_simple_yaml(text)
    if not isinstance(parsed, dict):
        return StrategyConfig()
    return strategy_config_from_dict(parsed)


def save_strategy_config(cfg: StrategyConfig, path: Path = Path("config.yaml")) -> Path:
    """Persist `StrategyConfig` cleanly in simple YAML format matching `load_strategy_config`."""
    lines = [
        "# SpaceInvasion Autoplayer runtime strategy configuration",
        f"playstyle: {cfg.playstyle}",
        f"merchant_mode: {'true' if cfg.merchant_mode else 'false'}",
        f"auto_throttle_blast_furnace: {'true' if cfg.auto_throttle_blast_furnace else 'false'}",
        f"allow_research: {'true' if cfg.allow_research else 'false'}",
        f"allow_shipyard: {'true' if cfg.allow_shipyard else 'false'}",
        f"allow_multi_planet: {'true' if cfg.allow_multi_planet else 'false'}",
        f"allow_fleetsave: {'true' if cfg.allow_fleetsave else 'false'}",
        f"allow_galaxy_scan: {'true' if cfg.allow_galaxy_scan else 'false'}",
        f"allow_raids: {'true' if cfg.allow_raids else 'false'}",
        f"allow_pvp_in_az: {'true' if cfg.allow_pvp_in_az else 'false'}",
        f"allow_colonize: {'true' if cfg.allow_colonize else 'false'}",
        f"storage_warn_ratio: {cfg.storage_warn_ratio}",
        f"blast_pig_iron_max_share: {cfg.blast_pig_iron_max_share}",
        f"combat_safety_margin: {cfg.combat_safety_margin}",
        f"raid_min_loot_spice_eq: {cfg.raid_min_loot_spice_eq}",
        f"fleet_save_fuel_reserve_spice: {cfg.fleet_save_fuel_reserve_spice}",
        f"max_actions_per_hour: {cfg.max_actions_per_hour}",
        f"hot_planet_solar_temp_threshold: {cfg.hot_planet_solar_temp_threshold}",
        f"min_free_fields_warning: {cfg.min_free_fields_warning}",
        f"planner_mode: {cfg.planner_mode}",
        f"ga_population_size: {int(cfg.ga_population_size)}",
        f"ga_generations: {int(cfg.ga_generations)}",
        f"ga_horizon_steps: {int(cfg.ga_horizon_steps)}",
        f"ga_mutation_rate: {cfg.ga_mutation_rate}",
        f"ga_nn_weight: {cfg.ga_nn_weight}",
        f"ga_seed: {'null' if cfg.ga_seed is None else int(cfg.ga_seed)}",
        "shipyard_targets:",
    ]
    for slug, cnt in cfg.shipyard_targets.items():
        lines.append(f"  {slug}: {int(cnt)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
