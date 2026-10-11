"""Core typed domain dataclasses for SpaceInvasion Classic (Phase 2.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from spaceinvasion.core._model_helpers import (
    contains_non_dict_mapping,
    deep_plain_value,
    normalize_int_mapping,
    normalize_str_mapping,
    safe_ratio,
)
from spaceinvasion.core.coercion import coerce_int, fmt_compact_int, safe_sequence


@dataclass(frozen=True)
class Resources:
    """Four primary SpaceInvasion Classic resources."""

    pig_iron: int = 0
    metal: int = 0
    kryptonite: int = 0
    spice: int = 0

    @classmethod
    def from_list(cls, values: Sequence[object]) -> Resources:
        """Build ``Resources`` from a 0..4-element sequence."""
        seq = safe_sequence(values)
        padded: list[object] = list(seq[:4]) + [0] * max(0, 4 - len(seq))
        return cls(
            pig_iron=max(0, coerce_int(padded[0], 0)),
            metal=max(0, coerce_int(padded[1], 0)),
            kryptonite=max(0, coerce_int(padded[2], 0)),
            spice=max(0, coerce_int(padded[3], 0)),
        )

    def can_afford(self, cost: Resources) -> bool:
        """Return True iff every resource meets or exceeds ``cost``."""
        return (
            coerce_int(self.pig_iron, 0) >= coerce_int(cost.pig_iron, 0)
            and coerce_int(self.metal, 0) >= coerce_int(cost.metal, 0)
            and coerce_int(self.kryptonite, 0) >= coerce_int(cost.kryptonite, 0)
            and coerce_int(self.spice, 0) >= coerce_int(cost.spice, 0)
        )

    def missing(self, cost: Resources) -> Resources:
        """Return per-resource shortfalls needed to cover ``cost``."""
        return Resources(
            pig_iron=max(0, coerce_int(cost.pig_iron, 0) - coerce_int(self.pig_iron, 0)),
            metal=max(0, coerce_int(cost.metal, 0) - coerce_int(self.metal, 0)),
            kryptonite=max(0, coerce_int(cost.kryptonite, 0) - coerce_int(self.kryptonite, 0)),
            spice=max(0, coerce_int(cost.spice, 0) - coerce_int(self.spice, 0)),
        )

    def is_zero(self) -> bool:
        """Return True iff all four resource amounts are zero."""
        return (
            coerce_int(self.pig_iron, 0) == 0
            and coerce_int(self.metal, 0) == 0
            and coerce_int(self.kryptonite, 0) == 0
            and coerce_int(self.spice, 0) == 0
        )

    def total(self) -> int:
        """Return total sum of non-negative resource amounts."""
        return (
            max(0, coerce_int(self.pig_iron, 0))
            + max(0, coerce_int(self.metal, 0))
            + max(0, coerce_int(self.kryptonite, 0))
            + max(0, coerce_int(self.spice, 0))
        )

    def format_short(self) -> str:
        """Format non-zero resource amounts as a compact human-readable label."""
        parts: list[str] = []
        pig = coerce_int(self.pig_iron, 0)
        met = coerce_int(self.metal, 0)
        kry = coerce_int(self.kryptonite, 0)
        spi = coerce_int(self.spice, 0)
        if pig:
            parts.append(f"PigIron={fmt_compact_int(pig, grouped=True)}")
        if met:
            parts.append(f"Metal={fmt_compact_int(met, grouped=True)}")
        if kry:
            parts.append(f"Krypt={fmt_compact_int(kry, grouped=True)}")
        if spi:
            parts.append(f"Spice={fmt_compact_int(spi, grouped=True)}")
        return ", ".join(parts) if parts else "0"

    def to_dict(self) -> dict[str, int]:
        """Serialize resources into a plain JSON-safe dict."""
        return {
            "pig_iron": coerce_int(self.pig_iron, 0),
            "metal": coerce_int(self.metal, 0),
            "kryptonite": coerce_int(self.kryptonite, 0),
            "spice": coerce_int(self.spice, 0),
        }


@dataclass(frozen=True)
class EnergyBalance:
    """Instantaneous planetary energy production and demand."""

    produced: int = 0
    used: int = 0

    @property
    def net(self) -> int:
        """Return net energy (produced minus used)."""
        return coerce_int(self.produced, 0) - coerce_int(self.used, 0)

    def to_dict(self) -> dict[str, int]:
        """Serialize energy balance into a plain JSON-safe dict."""
        return {"produced": coerce_int(self.produced, 0), "used": coerce_int(self.used, 0)}


@dataclass(frozen=True)
class BuildingOffer:
    """Parsed building card from ``internalBuildings``."""

    building_id: int
    name: str
    current_level: int
    target_level: int
    cost: Resources
    can_build: bool
    status_text: str
    build_href: str | None = None


@dataclass(frozen=True)
class ResearchOffer:
    """Parsed research card from ``internalResearch``."""

    research_id: int
    research_key: str
    name: str
    current_level: int
    target_level: int
    cost: Resources
    can_research: bool
    status_text: str
    research_href: str | None = None
    active: bool = False


@dataclass(frozen=True)
class UnitOffer:
    """Parsed ship or defense card from ``internalWeaponFactory`` or ``internalDefense``."""

    unit_type: str
    unit_id: int
    name: str
    owned: int
    cost: Resources
    can_build: bool
    input_name: str | None = None
    status_text: str = ""


ShipOffer = UnitOffer
DefenseOffer = UnitOffer


@dataclass(frozen=True)
class TutorialTask:
    """One requirement line inside the active tutorial step."""

    description: str
    building_name: str | None = None
    building_id: int | None = None
    current: int = 0
    target: int = 0
    category: str = "building"
    research_id: str | None = None

    @property
    def completed(self) -> bool:
        """Return True iff the requirement target is positive and met."""
        tgt = coerce_int(self.target, 0)
        cur = coerce_int(self.current, 0)
        return tgt > 0 and cur >= tgt


@dataclass(frozen=True)
class TutorialStatus:
    """Active SpaceInvasion tutorial progress and tasks."""

    step: int
    total_steps: int
    tasks: tuple[TutorialTask, ...] = field(default_factory=tuple)
    reward_text: str = ""
    can_complete: bool = False


@dataclass(frozen=True)
class QueueJob:
    """Active construction, research, shipyard, or fleet movement job."""

    category: str
    name: str
    building_id: int | None = None
    target_level: int | None = None
    remaining_text: str = ""
    remaining_seconds: float | None = None
    finish_ts: float | None = None
    queue_slot: int = 1
    detail: str = ""
    origin_coords: str = ""
    cargo: Resources | None = None
    expected_cargo: Resources | None = None
    delivered_cargo: Resources | None = None
    delivered: bool = False


@dataclass(frozen=True)
class FleetAlert:
    """Active fleet movement observed on ``internalHome``."""

    mission: str
    hostile: bool
    summary: str


@dataclass(frozen=True)
class PlanetState:
    """Full state of a single planet or asteroid."""

    planet_id: int
    name: str
    coords: str
    planet_type: str
    temperature: int
    fields_used: int
    fields_max: int
    resources: Resources
    storage: Resources
    energy: EnergyBalance
    buildings: dict[str, int] = field(default_factory=dict)
    building_names: dict[str, str] = field(default_factory=dict)
    ships: dict[str, int] = field(default_factory=dict)
    defense: dict[str, int] = field(default_factory=dict)
    building_offers: tuple[BuildingOffer, ...] = field(default_factory=tuple)
    ship_offers: tuple[UnitOffer, ...] = field(default_factory=tuple)
    defense_offers: tuple[UnitOffer, ...] = field(default_factory=tuple)
    economy_rates: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.resources, Resources):
            object.__setattr__(self, "resources", Resources())
        if not isinstance(self.storage, Resources):
            object.__setattr__(self, "storage", Resources())
        if not isinstance(self.energy, EnergyBalance):
            object.__setattr__(self, "energy", EnergyBalance())
        f_used = max(0, coerce_int(self.fields_used, 0))
        if f_used != self.fields_used or type(self.fields_used) is bool:
            object.__setattr__(self, "fields_used", f_used)
        f_max = max(0, coerce_int(self.fields_max, 0))
        if f_max != self.fields_max or type(self.fields_max) is bool:
            object.__setattr__(self, "fields_max", f_max)
        for attr, val in (
            ("building_offers", self.building_offers),
            ("ship_offers", self.ship_offers),
            ("defense_offers", self.defense_offers),
        ):
            if type(val) is not tuple:
                object.__setattr__(self, attr, safe_sequence(val))
        for attr, mapping in (
            ("buildings", self.buildings),
            ("ships", self.ships),
            ("defense", self.defense),
            ("economy_rates", self.economy_rates),
        ):
            norm = normalize_int_mapping(mapping)
            if norm is not mapping:
                object.__setattr__(self, attr, norm)
        bn_norm = normalize_str_mapping(self.building_names)
        if bn_norm is not self.building_names:
            object.__setattr__(self, "building_names", bn_norm)

    @property
    def fields_remaining(self) -> int:
        """Return remaining buildable planetary fields."""
        f_max = max(0, coerce_int(self.fields_max, 0))
        f_used = max(0, coerce_int(self.fields_used, 0))
        return max(0, f_max - f_used) if f_max > 0 else 0

    def building_level(self, building_id: int) -> int:
        """Return the current level of building ``b<building_id>``."""
        return max(0, coerce_int(self.buildings.get(f"b{building_id}", 0), 0))

    def storage_fill_ratios(self) -> dict[str, float]:
        """Return ``resources / storage`` fill ratios for all four resources."""
        return {
            "pig_iron": safe_ratio(self.resources.pig_iron, self.storage.pig_iron),
            "metal": safe_ratio(self.resources.metal, self.storage.metal),
            "kryptonite": safe_ratio(self.resources.kryptonite, self.storage.kryptonite),
            "spice": safe_ratio(self.resources.spice, self.storage.spice),
        }


@dataclass(frozen=True)
class GameState:
    """Complete snapshot of the player's empire in the current universe."""

    universe_id: int
    user_id: int
    nickname: str
    timestamp: float
    planets: tuple[PlanetState, ...]
    active_planet_id: int | None = None
    researches: dict[str, int] = field(default_factory=dict)
    research_names: dict[str, str] = field(default_factory=dict)
    specs: dict[str, int] = field(default_factory=dict)
    unread_messages: dict[str, int] = field(default_factory=dict)
    tutorial: TutorialStatus | None = None
    active_queues: tuple[QueueJob, ...] = field(default_factory=tuple)
    fleet_alerts: tuple[FleetAlert, ...] = field(default_factory=tuple)
    research_offers: tuple[ResearchOffer, ...] = field(default_factory=tuple)
    speedup_factor: float | None = None
    newbie_protection: bool = False
    account_points: int | None = None
    account_rank: int | None = None
    raid_targets: tuple[dict[str, object], ...] = field(default_factory=tuple)
    raid_stats: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for attr, mapping in (
            ("researches", self.researches),
            ("specs", self.specs),
            ("unread_messages", self.unread_messages),
        ):
            norm = normalize_int_mapping(mapping)
            if norm is not mapping:
                object.__setattr__(self, attr, norm)
        rn_norm = normalize_str_mapping(self.research_names)
        if rn_norm is not self.research_names:
            object.__setattr__(self, "research_names", rn_norm)
        if isinstance(self.raid_stats, Mapping):
            if not isinstance(self.raid_stats, dict) or contains_non_dict_mapping(self.raid_stats):
                object.__setattr__(self, "raid_stats", deep_plain_value(self.raid_stats))
        else:
            object.__setattr__(self, "raid_stats", {})
        self._normalize_raid_targets()

    def _normalize_raid_targets(self) -> None:
        rt = self.raid_targets
        if not rt:
            if not isinstance(rt, tuple):
                object.__setattr__(self, "raid_targets", ())
            return
        changed = not isinstance(rt, tuple)
        norm_rt: list[dict[str, object]] = []
        for item in rt:
            if isinstance(item, Mapping):
                if not isinstance(item, dict) or contains_non_dict_mapping(item):
                    changed = True
                    plain = deep_plain_value(item)
                    if isinstance(plain, dict):
                        norm_rt.append(plain)
                else:
                    norm_rt.append(item)
            else:
                changed = True
        if changed:
            object.__setattr__(self, "raid_targets", tuple(norm_rt))

    def active_planet(self) -> PlanetState | None:
        """Return the currently selected ``PlanetState``, falling back to ``planets[0]``."""
        if not self.planets:
            return None
        if self.active_planet_id is not None:
            for planet in self.planets:
                if planet.planet_id == self.active_planet_id:
                    return planet
        return self.planets[0]
