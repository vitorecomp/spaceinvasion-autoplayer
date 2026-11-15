"""Non-secret strategy profile loader (`config.json` / schema-driven validation) for SpaceInvasion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path

from backend.core._config_coerce import (
    _SAFE_COERCE_ERRORS,
    safe_cfg_bool,
    safe_cfg_exc_text,
    safe_cfg_float,
    safe_cfg_int,
    safe_cfg_key,
    safe_cfg_key_repr,
    safe_cfg_optional_int,
    sanitize_cfg_json_tree,
)
from backend.core._config_coerce import (
    canonicalize_fleet_research_key as _canonicalize_fleet_research_key,
)
from backend.core._config_coerce import (
    canonicalize_shipyard_target_key as _canonicalize_shipyard_target_key,
)
from backend.core._config_coerce import (
    clamp_cfg_int as _clamp_cfg_int,
)
from backend.core._config_coerce import (
    decode_cfg_bytes as _decode_cfg_bytes,
)
from backend.core._config_coerce import (
    safe_cfg_bool as _safe_cfg_bool,
)
from backend.core._config_coerce import (
    safe_cfg_exc_text as _safe_cfg_exc_text,
)
from backend.core._config_coerce import (
    safe_cfg_float as _safe_cfg_float,
)
from backend.core._config_coerce import (
    safe_cfg_int as _safe_cfg_int,
)
from backend.core._config_coerce import (
    safe_cfg_key as _safe_cfg_key,
)
from backend.core._config_coerce import (
    safe_cfg_key_repr as _safe_cfg_key_repr,
)
from backend.core._config_coerce import (
    safe_cfg_optional_int as _safe_cfg_optional_int,
)
from backend.core._config_coerce import (
    safe_fspath as _safe_fspath,
)
from backend.core._config_coerce import (
    sanitize_cfg_json_tree as _sanitize_cfg_json_tree,
)
from backend.core.redaction import redact_text as _redact_cfg_text
from backend.core.settings.schema import (
    DEFAULT_FLEET_RESEARCH_TARGETS as _SCHEMA_FLEET_RESEARCH_TARGETS,
)
from backend.core.settings.schema import (
    DEFAULT_SHIPYARD_TARGETS as _SCHEMA_SHIPYARD_TARGETS,
)
from backend.core.settings.schema import (
    VALID_PLANNER_MODES,
    SettingSpec,
)
from backend.core.settings.validator import ALL_SETTING_SPECS

DEFAULT_SHIPYARD_TARGETS: dict[str, int] = dict(_SCHEMA_SHIPYARD_TARGETS)
DEFAULT_FLEET_RESEARCH_TARGETS: dict[str, int] = dict(_SCHEMA_FLEET_RESEARCH_TARGETS)


def normalize_planner_mode(value: object, *, strict: bool = False) -> str:
    """Validate and normalize `planner_mode` (`'priority'` default or `'ai_planner'`; legacy `'ga_nn'` maps to `'priority'`)."""
    if value is None:
        return "priority"
    if isinstance(value, os.PathLike):
        ok, fsp = _safe_fspath(value)
        if not ok:
            if strict:
                raise ValueError(f"Unsupported planner_mode; expected one of {VALID_PLANNER_MODES}.")
            return "priority"
        value = fsp
        if value is None:
            return "priority"
    if isinstance(value, (bool, int, float, Mapping, list, tuple, set, frozenset)):
        if strict:
            raise ValueError(f"Unsupported planner_mode {type(value).__name__}; expected one of {VALID_PLANNER_MODES}.")
        return "priority"
    if isinstance(value, (bytes, bytearray)):
        value = _decode_cfg_bytes(value)
    try:
        mode = str(value).strip().lower()
    except _SAFE_COERCE_ERRORS as exc:
        if strict:
            raise ValueError(f"Unsupported planner_mode; expected one of {VALID_PLANNER_MODES}.") from exc
        return "priority"
    if not mode or mode == "ga_nn":
        return "priority"
    if mode in VALID_PLANNER_MODES:
        return mode
    if strict:
        raise ValueError(f"Unsupported planner_mode {_redact_cfg_text(mode)!r}; expected one of {VALID_PLANNER_MODES}.")
    return "priority"


@dataclass(frozen=True)
class StrategyConfig:
    """Empire strategy toggles and safety thresholds loaded from PostgreSQL or JSON profile."""

    playstyle: str = "fleeter"
    merchant_mode: bool = False
    auto_throttle_blast_furnace: bool = True
    allow_research: bool = True
    allow_fleet_research: bool = True
    allow_shipyard: bool = True
    allow_multi_planet: bool = True
    allow_fleetsave: bool = False
    allow_galaxy_scan: bool = False
    allow_raids: bool = False
    allow_pvp_in_az: bool = False
    allow_colonize: bool = False
    allow_transport: bool = False
    allow_transmitter: bool = False
    forbid_planetary_solar: bool = False
    storage_warn_ratio: float = 0.85
    blast_pig_iron_max_share: float = 0.70
    combat_safety_margin: float = 1.4
    raid_min_loot_spice_eq: float = 1500.0
    attack_min_res_per_hour: float = 0.0
    recycle_min_res_per_hour: float = 0.0
    fleet_save_fuel_reserve_spice: int = 500
    action_batch_interval_seconds: int = 120
    max_actions_per_hour: int = 60
    page_view_limit_per_minute: int = 60
    hot_planet_solar_temp_threshold: int = 40
    min_free_fields_warning: int = 20
    min_colony_fields: int = 300
    max_planets: int = 11
    galaxy_sweep_interval_seconds: int = 270
    galaxy_sweep_pages: int = 100
    galaxy_scan_click_delay_seconds: float = 1.5
    galaxy_scan_click_jitter_seconds: float = 0.6
    galaxy_scan_explore_first: bool = True
    galaxy_scan_refresh_pct: int = 0
    galaxy_scan_all_galaxies: bool = False
    galaxy_scan_block_other_actions: bool = True
    galaxy_scan_near_weight: float = 2.0
    galaxy_scan_attack_weight: float = 3.0
    reserved_spy_slots: int = 1
    spy_reserved_slots_only: bool = False
    spy_dispatch_planet_id: int = 0
    planner_mode: str = "priority"
    ai_resource_spare_pct: float = 10.0
    ai_adaptive_execution: bool = False
    ga_population_size: int = 24
    ga_generations: int = 12
    ga_mutation_rate: float = 0.25
    shipyard_targets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SHIPYARD_TARGETS))
    fleet_research_targets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_FLEET_RESEARCH_TARGETS))


def _coerce_playstyle(raw_playstyle: object) -> str:
    if isinstance(raw_playstyle, os.PathLike):
        ok, fsp = _safe_fspath(raw_playstyle)
        raw_playstyle = fsp if ok else "fleeter"
        if isinstance(raw_playstyle, (bytes, bytearray)):
            raw_playstyle = _decode_cfg_bytes(raw_playstyle)
    if raw_playstyle is None or isinstance(
        raw_playstyle, (Mapping, list, tuple, set, frozenset, bytes, bytearray, int, float, bool)
    ):
        return "fleeter"
    try:
        return str(raw_playstyle).strip().lower() or "fleeter"
    except _SAFE_COERCE_ERRORS:
        return "fleeter"


def _coerce_int_map(spec: SettingSpec, raw_map: object) -> dict[str, int]:
    if spec.key == "shipyard_targets":
        targets = dict(DEFAULT_SHIPYARD_TARGETS)
        if isinstance(raw_map, Mapping):
            for k, v in raw_map.items():
                key_str = _canonicalize_shipyard_target_key(_safe_cfg_key(k))
                targets[key_str] = max(0, _safe_cfg_int(v, targets.get(key_str, 0)))
        return targets
    fleet_targets = dict(DEFAULT_FLEET_RESEARCH_TARGETS)
    if isinstance(raw_map, Mapping):
        max_v = int(spec.max_value) if spec.max_value is not None else 100
        for k, v in raw_map.items():
            key_str = _canonicalize_fleet_research_key(_safe_cfg_key(k))
            if not key_str:
                continue
            lvl = max(0, min(max_v, _safe_cfg_int(v, 0)))
            if lvl > 0:
                fleet_targets[key_str] = lvl
            else:
                fleet_targets.pop(key_str, None)
    return fleet_targets


def _coerce_setting_from_spec(spec: SettingSpec, raw_val: object) -> object:
    if spec.kind == "bool":
        return _safe_cfg_bool(raw_val, bool(spec.default))
    if spec.kind == "int_map":
        return _coerce_int_map(spec, raw_val)
    if spec.kind == "enum":
        if spec.key == "playstyle":
            return _coerce_playstyle(raw_val)
        if isinstance(raw_val, os.PathLike):
            ok, fsp = _safe_fspath(raw_val)
            raw_val = fsp if ok else "priority"
        if isinstance(raw_val, (Mapping, list, tuple, set, frozenset, int, float, bool)):
            raw_val = "priority"
        elif isinstance(raw_val, (bytes, bytearray)):
            raw_val = _decode_cfg_bytes(raw_val)
        return normalize_planner_mode(raw_val)
    if spec.kind in ("int", "planet_id"):
        default_i = int(spec.default) if isinstance(spec.default, int) else 0
        val_i = _safe_cfg_int(raw_val, default_i)
        if spec.min_value is not None:
            val_i = max(int(spec.min_value), val_i)
        if spec.max_value is not None:
            val_i = min(int(spec.max_value), val_i)
        return val_i
    default_f = float(spec.default) if isinstance(spec.default, (int, float)) else 0.0
    val_f = _safe_cfg_float(raw_val, default_f)
    if spec.min_value is not None:
        val_f = max(float(spec.min_value), val_f)
    if spec.max_value is not None:
        val_f = min(float(spec.max_value), val_f)
    return val_f


def strategy_config_from_dict(data: object) -> StrategyConfig:
    """Build a schema-validated `StrategyConfig` from parsed mapping data using `ALL_SETTING_SPECS`."""
    if not isinstance(data, Mapping):
        return StrategyConfig()
    kwargs: dict[str, object] = {}
    for spec in ALL_SETTING_SPECS:
        raw_val = data.get(spec.key, spec.default if spec.kind != "int_map" else None)
        kwargs[spec.key] = _coerce_setting_from_spec(spec, raw_val)
    return StrategyConfig(**kwargs)  # type: ignore[arg-type]


def _serialize_strategy_config_json(cfg: StrategyConfig, *, indent: int | None = None) -> str:
    clean_cfg = strategy_config_from_dict(asdict(cfg))
    cfg_dict = _sanitize_cfg_json_tree(asdict(clean_cfg))
    return json.dumps(cfg_dict, indent=indent, sort_keys=True, allow_nan=False)


def load_strategy_config(path: Path | str) -> StrategyConfig:
    """Load `StrategyConfig` from an explicit JSON file path."""
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        raise ValueError("Use backend.db.config_store.load_strategy_config for PostgreSQL DSNs.")
    path_obj = Path(path) if isinstance(path, str) else path
    if not path_obj.exists():
        return StrategyConfig()
    try:
        text = path_obj.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return StrategyConfig()
    if not text.strip():
        return StrategyConfig()
    try:
        parsed = _sanitize_cfg_json_tree(json.loads(text))
    except (ValueError, TypeError):
        return StrategyConfig()
    if not isinstance(parsed, Mapping):
        return StrategyConfig()
    return strategy_config_from_dict(parsed)


def save_strategy_config(cfg: StrategyConfig, path: Path | str) -> Path:
    """Persist `StrategyConfig` to an explicit JSON file path."""
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        raise ValueError("Use backend.db.config_store.save_strategy_config for PostgreSQL DSNs.")
    if not isinstance(path, Path):
        ok_fsp, fsp_val = _safe_fspath(path)
        if not ok_fsp:
            raise TypeError("Expected Path, str, or os.PathLike for config path.")
        path = Path(os.fsdecode(fsp_val))  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_strategy_config_json(cfg, indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "DEFAULT_FLEET_RESEARCH_TARGETS",
    "DEFAULT_SHIPYARD_TARGETS",
    "StrategyConfig",
    "VALID_PLANNER_MODES",
    "_canonicalize_fleet_research_key",
    "_canonicalize_shipyard_target_key",
    "_clamp_cfg_int",
    "_decode_cfg_bytes",
    "_safe_cfg_bool",
    "_safe_cfg_exc_text",
    "_safe_cfg_float",
    "_safe_cfg_int",
    "_safe_cfg_key",
    "_safe_cfg_key_repr",
    "_safe_cfg_optional_int",
    "_safe_fspath",
    "_sanitize_cfg_json_tree",
    "_serialize_strategy_config_json",
    "load_strategy_config",
    "normalize_planner_mode",
    "safe_cfg_bool",
    "safe_cfg_exc_text",
    "safe_cfg_float",
    "safe_cfg_int",
    "safe_cfg_key",
    "safe_cfg_key_repr",
    "safe_cfg_optional_int",
    "sanitize_cfg_json_tree",
    "save_strategy_config",
    "strategy_config_from_dict",
]
