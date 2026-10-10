"""Frontend schema export and deterministic Markdown settings reference generator (stdlib only)."""

from __future__ import annotations

from spaceinvasion.core.settings.schema import (
    REMOVED_GA_NN_FIELDS,
    SETTING_GROUPS,
    VALID_PLANNER_MODES,
    SettingSpec,
)
from spaceinvasion.core.settings.validator import ALL_SETTING_SPECS


def _format_default(spec: SettingSpec) -> str:
    val = spec.default_json_value()
    if isinstance(val, bool):
        return "`true`" if val else "`false`"
    if isinstance(val, dict):
        return "`{}`" if not val else f"`{len(val)} entries`"
    return f"`{val}`"


def _format_bounds(spec: SettingSpec) -> str:
    if spec.enum_values:
        return " \\| ".join(f"`{v}`" for v in spec.enum_values)
    if spec.kind == "bool":
        return "`true` \\| `false`"
    if spec.kind == "planet_id":
        return "`0` (auto) or colony `planet_id`"
    if spec.min_value is not None and spec.max_value is not None:
        unit_suffix = f" {spec.unit}" if spec.unit else ""
        return f"`{spec.min_value}` .. `{spec.max_value}`{unit_suffix}"
    if spec.min_value is not None:
        unit_suffix = f" {spec.unit}" if spec.unit else ""
        return f"`>= {spec.min_value}`{unit_suffix}"
    return "—"


def export_frontend_settings_schema() -> dict[str, object]:
    """Export the canonical JSON-serializable schema consumed by the React Console."""
    specs_by_group: dict[str, list[SettingSpec]] = {grp.id: [] for grp in SETTING_GROUPS}
    for spec in ALL_SETTING_SPECS:
        if spec.show_in_config_page and spec.group in specs_by_group:
            specs_by_group[spec.group].append(spec)

    groups_payload: list[dict[str, object]] = []
    for grp in SETTING_GROUPS:
        groups_payload.append(
            {
                "id": grp.id,
                "headingId": grp.heading_id,
                "title": grp.title,
                "subtitle": grp.subtitle,
                "items": [spec.to_frontend_item() for spec in specs_by_group[grp.id]],
            }
        )

    visible_specs = [spec for spec in ALL_SETTING_SPECS if spec.show_in_config_page]
    bool_keys = [spec.key for spec in visible_specs if spec.control == "checkbox"]
    number_defaults: dict[str, float | int] = {}
    for spec in visible_specs:
        if spec.control == "number" and isinstance(spec.default, (int, float)) and not isinstance(spec.default, bool):
            number_defaults[spec.key] = spec.default

    return {
        "schema_version": 1,
        "planner_modes": list(VALID_PLANNER_MODES),
        "removed_keys": sorted(REMOVED_GA_NN_FIELDS),
        "groups": groups_payload,
        "all_keys": [spec.key for spec in ALL_SETTING_SPECS],
        "bool_keys": bool_keys,
        "number_defaults": number_defaults,
    }


def generate_settings_reference_markdown() -> str:
    """Generate deterministic Markdown documentation for all canonical `StrategyConfig` settings."""
    specs_by_group: dict[str, list[SettingSpec]] = {grp.id: [] for grp in SETTING_GROUPS}
    for spec in ALL_SETTING_SPECS:
        if spec.group in specs_by_group:
            specs_by_group[spec.group].append(spec)

    lines: list[str] = [
        "# Strategy Settings Reference",
        "",
        "Generated from `spaceinvasion.core.settings`. Every surviving `StrategyConfig` field is listed below.",
        "",
        f"Removed legacy `ga_nn` keys (ignored on load, rejected on strict API patches): "
        f"{', '.join(f'`{k}`' for k in sorted(REMOVED_GA_NN_FIELDS))}.",
        "",
    ]

    for grp in SETTING_GROUPS:
        items = specs_by_group[grp.id]
        lines.extend(
            [
                f"## {grp.title}",
                "",
                grp.subtitle,
                "",
                "| Setting | Type | Default | Bounds / Choices | Summary |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for spec in items:
            summary = (spec.summary or spec.mechanics_help).replace("|", "\\|")
            lines.append(
                f"| `{spec.key}` | `{spec.kind}` | {_format_default(spec)} | {_format_bounds(spec)} | {summary} |"
            )
        lines.append("")
        for spec in items:
            help_text = spec.mechanics_help or spec.summary
            lines.append(f"- **`{spec.key}` ({spec.title})**: {help_text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
