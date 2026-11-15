"""Typed dataclasses and static unit-slug maps returned by ``backend.parsing``."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.model import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    TutorialStatus,
    TutorialTask,
    UnitOffer,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

# Map `#json-container` / form unit keys (`s1`.., `d1`..) to SpaceInvasion wiki slugs.
SHIP_SLUG_BY_KEY: dict[str, str] = {
    "s1": "small-transport",
    "s2": "large-transport",
    "s3": "transmitter",
    "s4": "small-fighter",
    "s5": "warship",
    "s6": "starcruiser",
    "s7": "phoenix",
    "s8": "colony-ship",
    "s9": "solar-cells",
    "s10": "spy-probe",
    "s11": "recycler",
    "s13": "battleship",
    "s14": "stealth-bomber",
    "s15": "destroyer",
    "s16": "imperial-starbase",
}

DEFENSE_SLUG_BY_KEY: dict[str, str] = {
    "d1": "mortar",
    "d2": "laser-cannon-battery",
    "d3": "ion-cannon",
    "d4": "positron-lighter",
    "d5": "turboflak",
    "d6": "interval-cannon",
    "d7": "solar-cannon",
    "d8": "light-graviton-cannon",
    "d9": "heavy-graviton-cannon",
    "d10": "small-shield-dome",
    "d11": "large-shield-dome",
}


@dataclass(frozen=True)
class GalaxySlot:
    """Single planet coordinate row parsed from the Galaxy map."""

    coords: str
    position: int
    name: str = ""
    owner: str = ""
    status_color: str = "free"
    has_debris: bool = False
    is_spacia: bool = False
    spy_href: str | None = None
    has_asteroid: bool = False
    attack_href: str | None = None
    colonize_min_fields: int | None = None
    colonize_max_fields: int | None = None
    uninhabited_fields: int | None = None
    temperature: int | None = None
    debris_pig_iron: int = 0
    debris_metal: int = 0
    debris_kryptonite: int = 0
    debris_spice: int = 0


@dataclass(frozen=True)
class SpyReport:
    """Structured espionage or uninhabited-planet ecology report."""

    target_coords: str
    owner: str = ""
    resources: Resources = field(default_factory=Resources)
    fleet: dict[str, int] = field(default_factory=dict)
    defense: dict[str, int] = field(default_factory=dict)
    dug_in: bool | None = None
    uninhabited_fields: int | None = None
    is_spacia: bool = False
    buildings: dict[str, int] = field(default_factory=dict)
    researches: dict[str, int] = field(default_factory=dict)
    planet_name: str = ""
    server_msg_id: str = ""
    msg_date_text: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class InboxMessage:
    """Single archived SpaceInvasion ``internalMessages`` entry (any folder/category)."""

    msg_uid: str
    server_msg_id: str = ""
    folder_type: str = ""
    category: str = "other"
    msg_date_text: str = ""
    sender: str = ""
    subject: str = ""
    coords: str = ""
    body_text: str = ""
    raw_html: str = ""
    parsed_data: JsonObject = field(default_factory=dict)
    spy_report: SpyReport | None = None
    body_complete: bool = True


__all__ = [
    "BuildingOffer",
    "DEFENSE_SLUG_BY_KEY",
    "EnergyBalance",
    "FleetAlert",
    "GalaxySlot",
    "GameState",
    "InboxMessage",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "PlanetState",
    "QueueJob",
    "ResearchOffer",
    "Resources",
    "SHIP_SLUG_BY_KEY",
    "SpyReport",
    "TutorialStatus",
    "TutorialTask",
    "UnitOffer",
]
