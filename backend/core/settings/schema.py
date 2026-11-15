"""Canonical strategy settings schema types, groups, and constants (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SettingKind = Literal["bool", "int", "float", "enum", "planet_id", "int_map"]
SettingControlKind = Literal["select", "number", "checkbox", "planet-select", "map"]
SettingDefaultValue = bool | int | float | str | tuple[tuple[str, int], ...]

VALID_PLANNER_MODES: tuple[str, ...] = ("priority", "ai_planner")
VALID_PLAYSTYLES: tuple[str, ...] = ("fleeter", "hybrid", "miner")

# Fields removed per Refactor Plan Decision D1 (legacy ga_nn rollout engine retired).
REMOVED_GA_NN_FIELDS: frozenset[str] = frozenset(
    {
        "ga_horizon_steps",
        "ga_nn_weight",
        "ga_seed",
    }
)

DEFAULT_SHIPYARD_TARGETS: tuple[tuple[str, int], ...] = (
    ("small-fighter", 12),
    ("small-transport", 6),
    ("spy-probe", 10),
    ("colony-ship", 1),
    ("recycler", 4),
    ("starcruiser", 6),
    ("phoenix", 10),
    ("transmitter", 6),
    ("battleship", 8),
    ("stealth-bomber", 4),
    ("destroyer", 4),
)

DEFAULT_FLEET_RESEARCH_TARGETS: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class SettingOption:
    """Single labeled choice for an enum-backed strategy setting."""

    value: str
    label: str


@dataclass(frozen=True)
class SettingGroupSpec:
    """Logical section grouping settings in the Console and Markdown reference."""

    id: str
    heading_id: str
    title: str
    subtitle: str


@dataclass(frozen=True)
class SettingSpec:
    """Canonical metadata, defaults, bounds, and SpaceInvasion mechanics help for one setting."""

    key: str
    title: str
    group: str
    kind: SettingKind
    default: SettingDefaultValue
    min_value: int | float | None = None
    max_value: int | float | None = None
    step: int | float | None = None
    enum_values: tuple[str, ...] = ()
    enum_options: tuple[SettingOption, ...] = ()
    unit: str = ""
    summary: str = ""
    mechanics_help: str = ""
    bounds_label: str = ""
    control: SettingControlKind = "number"
    show_in_config_page: bool = True
    legacy_aliases: tuple[tuple[str, str], ...] = ()

    def default_json_value(self) -> bool | int | float | str | dict[str, int]:
        """Return a JSON-serializable copy of `default`."""
        if isinstance(self.default, tuple):
            return {k: v for k, v in self.default}
        return self.default

    def to_frontend_item(self) -> dict[str, object]:
        """Serialize this setting into the Console `ConfigItemSpec` JSON shape."""
        item: dict[str, object] = {
            "key": self.key,
            "id": f"cfg-{self.key}",
            "title": self.title,
            "boundsLabel": self.bounds_label,
            "description": self.mechanics_help or self.summary,
            "control": self.control,
            "defaultValue": self.default_json_value(),
        }
        if self.enum_options:
            item["options"] = [{"value": opt.value, "label": opt.label} for opt in self.enum_options]
        if self.min_value is not None:
            item["min"] = self.min_value
        if self.max_value is not None:
            item["max"] = self.max_value
        if self.step is not None:
            item["step"] = self.step
        if self.unit:
            item["unit"] = self.unit
        return item


SETTING_GROUPS: tuple[SettingGroupSpec, ...] = (
    SettingGroupSpec(
        id="doctrine",
        heading_id="cfg-grp-doctrine",
        title="Planner Engine & Empire Doctrine",
        subtitle=(
            "Core planning engine selection, playstyle doctrine weighting, "
            "multi-planet scheduling scope, and colony expansion field thresholds."
        ),
    ),
    SettingGroupSpec(
        id="economy",
        heading_id="cfg-grp-economy",
        title="Economy, Power & Storage Automation",
        subtitle=(
            "Blast furnace (b2) 2:1 Pig iron throttling, planetary power generation policy, "
            "warehouse overflow guards, research/shipyard queues, and free IDT spins."
        ),
    ),
    SettingGroupSpec(
        id="galaxy",
        heading_id="cfg-grp-galaxy",
        title="Galaxy Scanner & Sweep Pacing",
        subtitle=(
            "Spiral solar-system recon, the smart 2-phase scanner (exploratory 1-400 coverage, "
            "then a weighted refresh by proximity and attack history), multi-page 2D Galaxy "
            "si.navigate sweeps, delay and jitter between clicks, and free-slot colonization."
        ),
    ),
    SettingGroupSpec(
        id="combat",
        heading_id="cfg-grp-combat",
        title="Espionage, Raiding & Fleet Safety",
        subtitle=(
            "Reserved spy probe flight seats, spy origin colony selection, zero-rapidfire 1:1 "
            "combat simulator gates, 3/24h Bash tracking, and P0 SAVE Escape flights."
        ),
    ),
    SettingGroupSpec(
        id="runtime",
        heading_id="cfg-grp-runtime",
        title="Runtime Pacing & Evolutionary Search",
        subtitle=(
            "Autonomous Commander tick cadence, rolling hourly DOM mutation budget, "
            "page-view rate ceiling, and evolutionary search hyperparameters."
        ),
    ),
)
