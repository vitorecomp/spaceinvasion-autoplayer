"""Strict config patch validation and load-time normalization (stdlib only)."""

from __future__ import annotations

from collections.abc import Mapping
import math
from types import MappingProxyType

from backend.core.settings.schema import (
    REMOVED_GA_NN_FIELDS,
    VALID_PLANNER_MODES,
    SettingSpec,
)
from backend.core.settings.specs_combat import COMBAT_SPECS, RUNTIME_SPECS
from backend.core.settings.specs_core import DOCTRINE_SPECS, ECONOMY_SPECS
from backend.core.settings.specs_galaxy import GALAXY_SPECS

ALL_SETTING_SPECS: tuple[SettingSpec, ...] = (
    *DOCTRINE_SPECS,
    *ECONOMY_SPECS,
    *GALAXY_SPECS,
    *COMBAT_SPECS,
    *RUNTIME_SPECS,
)

SETTING_SPEC_BY_KEY: Mapping[str, SettingSpec] = MappingProxyType({spec.key: spec for spec in ALL_SETTING_SPECS})


class SettingsValidationError(ValueError):
    """Structured HTTP 400 validation error for strategy settings patches."""

    def __init__(self, field: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
        self.status_code = status_code

    def to_error_payload(self) -> dict[str, object]:
        """Serialize into the canonical Game API error envelope."""
        return {
            "ok": False,
            "error": self.message,
            "field": self.field,
            "status_code": self.status_code,
        }


def _parse_strict_int_value(field: str, raw: object, *, min_val: int | None, max_val: int | None) -> int:
    if isinstance(raw, bool) or raw is None or isinstance(raw, (Mapping, list, tuple, set, frozenset)):
        raise SettingsValidationError(field, f"Config field {field!r} must be an integer.")
    parsed: int
    if isinstance(raw, int):
        parsed = raw
    elif isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer():
            raise SettingsValidationError(field, f"Config field {field!r} must be an integer.")
        parsed = int(raw)
    elif isinstance(raw, str):
        txt = raw.strip()
        if not txt or not txt.isascii():
            raise SettingsValidationError(field, f"Config field {field!r} must be an integer.")
        body = txt[1:] if txt[0] in ("+", "-") else txt
        if not body or not body.isdigit():
            raise SettingsValidationError(field, f"Config field {field!r} must be an integer.")
        parsed = int(txt, 10)
    else:
        raise SettingsValidationError(field, f"Config field {field!r} must be an integer.")
    if min_val is not None and parsed < min_val:
        raise SettingsValidationError(field, f"Config field {field!r} must be >= {min_val}.")
    if max_val is not None and parsed > max_val:
        raise SettingsValidationError(field, f"Config field {field!r} must be <= {max_val}.")
    return parsed


def _parse_strict_float_value(field: str, raw: object, *, min_val: float | None, max_val: float | None) -> float:
    if isinstance(raw, bool) or raw is None or isinstance(raw, (Mapping, list, tuple, set, frozenset)):
        raise SettingsValidationError(field, f"Config field {field!r} must be a finite number.")
    parsed: float
    if isinstance(raw, (int, float)):
        parsed = float(raw)
    elif isinstance(raw, str):
        txt = raw.strip()
        if not txt or not txt.isascii() or "_" in txt:
            raise SettingsValidationError(field, f"Config field {field!r} must be a finite number.")
        try:
            parsed = float(txt)
        except ValueError as exc:
            raise SettingsValidationError(field, f"Config field {field!r} must be a finite number.") from exc
    else:
        raise SettingsValidationError(field, f"Config field {field!r} must be a finite number.")
    if not math.isfinite(parsed):
        raise SettingsValidationError(field, f"Config field {field!r} must be a finite number.")
    if min_val is not None and parsed < min_val:
        raise SettingsValidationError(field, f"Config field {field!r} must be >= {min_val}.")
    if max_val is not None and parsed > max_val:
        raise SettingsValidationError(field, f"Config field {field!r} must be <= {max_val}.")
    return parsed


def _parse_bool_value(field: str, raw: object, *, strict_bools: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if strict_bools:
        raise SettingsValidationError(field, f"Config field {field!r} must be a boolean.")
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    raise SettingsValidationError(field, f"Config field {field!r} must be a boolean.")


def _parse_int_map(spec: SettingSpec, raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise SettingsValidationError(
            spec.key,
            f"Config field {spec.key!r} must be a mapping of string keys to integer values.",
        )
    min_i = int(spec.min_value) if spec.min_value is not None else None
    max_i = int(spec.max_value) if spec.max_value is not None else None
    out: dict[str, int] = {}
    for raw_k, raw_v in raw.items():
        if not isinstance(raw_k, str) or not raw_k.strip():
            raise SettingsValidationError(
                spec.key,
                f"Config field {spec.key!r} keys must be non-empty strings.",
            )
        clean_k = raw_k.strip()
        out[clean_k] = _parse_strict_int_value(f"{spec.key}.{clean_k}", raw_v, min_val=min_i, max_val=max_i)
    return out


def normalize_planner_mode(value: object, *, strict: bool = False) -> str:
    """Validate/normalize `planner_mode` (`priority` | `ai_planner`; maps legacy `ga_nn` -> `priority` on load)."""
    if value is None:
        if strict:
            raise SettingsValidationError(
                "planner_mode",
                f"'planner_mode' must be a non-empty string ({', '.join(VALID_PLANNER_MODES)}).",
            )
        return "priority"
    if not isinstance(value, str):
        if strict:
            raise SettingsValidationError(
                "planner_mode",
                f"'planner_mode' must be a non-empty string ({', '.join(VALID_PLANNER_MODES)}).",
            )
        return "priority"
    mode = value.strip().lower()
    if not mode:
        if strict:
            raise SettingsValidationError(
                "planner_mode",
                f"'planner_mode' must be a non-empty string ({', '.join(VALID_PLANNER_MODES)}).",
            )
        return "priority"
    if mode in VALID_PLANNER_MODES:
        return mode
    if not strict and mode == "ga_nn":
        return "priority"
    if strict:
        raise SettingsValidationError(
            "planner_mode",
            f"Unsupported planner_mode {mode!r}; expected one of {VALID_PLANNER_MODES}.",
        )
    return "priority"


def _validate_single_setting(spec: SettingSpec, raw: object, *, strict_bools: bool) -> object:
    if spec.kind == "bool":
        return _parse_bool_value(spec.key, raw, strict_bools=strict_bools)
    if spec.kind == "int":
        min_i = int(spec.min_value) if spec.min_value is not None else None
        max_i = int(spec.max_value) if spec.max_value is not None else None
        return _parse_strict_int_value(spec.key, raw, min_val=min_i, max_val=max_i)
    if spec.kind == "planet_id":
        if raw in (None, ""):
            return 0
        min_i = int(spec.min_value) if spec.min_value is not None else 0
        max_i = int(spec.max_value) if spec.max_value is not None else 2**63 - 1
        return _parse_strict_int_value(spec.key, raw, min_val=min_i, max_val=max_i)
    if spec.kind == "float":
        min_f = float(spec.min_value) if spec.min_value is not None else None
        max_f = float(spec.max_value) if spec.max_value is not None else None
        return _parse_strict_float_value(spec.key, raw, min_val=min_f, max_val=max_f)
    if spec.kind == "enum":
        if spec.key == "planner_mode":
            return normalize_planner_mode(raw, strict=True)
        if not isinstance(raw, str) or not raw.strip():
            raise SettingsValidationError(spec.key, f"{spec.key!r} must be a non-empty string.")
        norm = raw.strip().lower()
        if spec.enum_values and norm not in spec.enum_values:
            raise SettingsValidationError(
                spec.key,
                f"Unsupported {spec.key} {norm!r}; expected one of {spec.enum_values}.",
            )
        return norm
    return _parse_int_map(spec, raw)


def validate_config_patch(
    patch: Mapping[str, object],
    *,
    strict_bools: bool = True,
    reject_unknown: bool = True,
) -> dict[str, object]:
    """Validate and coerce an incoming API config patch against `ALL_SETTING_SPECS`."""
    if not isinstance(patch, Mapping):
        raise SettingsValidationError("config", "Config patch must be a JSON object.")
    validated: dict[str, object] = {}
    for raw_key, raw_val in patch.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise SettingsValidationError("config", "Config keys must be non-empty strings.")
        key = raw_key.strip()
        spec = SETTING_SPEC_BY_KEY.get(key)
        if spec is None:
            if reject_unknown:
                if key in REMOVED_GA_NN_FIELDS:
                    raise SettingsValidationError(
                        key,
                        f"Config field {key!r} was removed with the legacy ga_nn optimizer.",
                    )
                raise SettingsValidationError(key, f"Unknown config field {key!r}.")
            continue
        validated[key] = _validate_single_setting(spec, raw_val, strict_bools=strict_bools)
    return validated


def normalize_loaded_settings(raw: Mapping[str, object]) -> dict[str, object]:
    """Load-time normalizer: defaults missing fields, drops removed ga_nn keys, maps `ga_nn` -> `priority`."""
    result: dict[str, object] = {spec.key: spec.default_json_value() for spec in ALL_SETTING_SPECS}
    for spec in ALL_SETTING_SPECS:
        if spec.key not in raw:
            continue
        val = raw[spec.key]
        if spec.key == "planner_mode":
            result[spec.key] = normalize_planner_mode(val, strict=False)
            continue
        try:
            result[spec.key] = _validate_single_setting(spec, val, strict_bools=False)
        except SettingsValidationError:
            result[spec.key] = spec.default_json_value()
    return result
