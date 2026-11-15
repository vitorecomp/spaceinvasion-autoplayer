"""Canonical strategy settings schema, strict validator, and reference generators."""

from __future__ import annotations

from backend.core.settings.docs import (
    export_frontend_settings_schema,
    generate_settings_reference_markdown,
)
from backend.core.settings.schema import (
    DEFAULT_FLEET_RESEARCH_TARGETS,
    DEFAULT_SHIPYARD_TARGETS,
    REMOVED_GA_NN_FIELDS,
    SETTING_GROUPS,
    VALID_PLANNER_MODES,
    VALID_PLAYSTYLES,
    SettingControlKind,
    SettingDefaultValue,
    SettingGroupSpec,
    SettingKind,
    SettingOption,
    SettingSpec,
)
from backend.core.settings.validator import (
    ALL_SETTING_SPECS,
    SETTING_SPEC_BY_KEY,
    SettingsValidationError,
    normalize_loaded_settings,
    normalize_planner_mode,
    validate_config_patch,
)

__all__ = [
    "ALL_SETTING_SPECS",
    "DEFAULT_FLEET_RESEARCH_TARGETS",
    "DEFAULT_SHIPYARD_TARGETS",
    "REMOVED_GA_NN_FIELDS",
    "SETTING_GROUPS",
    "SETTING_SPEC_BY_KEY",
    "SettingControlKind",
    "SettingDefaultValue",
    "SettingGroupSpec",
    "SettingKind",
    "SettingOption",
    "SettingSpec",
    "SettingsValidationError",
    "VALID_PLANNER_MODES",
    "VALID_PLAYSTYLES",
    "export_frontend_settings_schema",
    "generate_settings_reference_markdown",
    "normalize_loaded_settings",
    "normalize_planner_mode",
    "validate_config_patch",
]
