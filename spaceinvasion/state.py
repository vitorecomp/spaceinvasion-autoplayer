"""Typed game state models and pure HTML/JSON parsers for SpaceInvasion Classic."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
from dataclasses import dataclass, field
import json
import math
import os
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from spaceinvasion import game_data

BUILDING_ID_BY_NAME: dict[str, int] = {
    "pig iron mine": 1,
    "blast furnace": 2,
    "kryptonite mine": 3,
    "spice mine": 4,
    "fusion plant": 5,
    "thermosolar plant": 6,
    "development center": 7,
    "research lab": 8,
    "weapons factory": 9,
    "fleet base": 10,
    "pig iron warehouse": 11,
    "metal warehouse": 12,
    "kryptonite warehouse": 13,
    "spice warehouse": 14,
    "particle cannon": 15,
    "particle shield": 16,
    "microsystem accelerator": 17,
    "teleporter": 18,
    "galaxy scanner": 19,
    "tunnel system": 20,
    "quantum array": 21,
}

_INT_RE = re.compile(r"[\d.]+", re.ASCII)


def parse_si_int(raw: Any) -> int:
    """Parse SpaceInvasion dot-grouped integer strings like `'2.000'` -> `2000`."""
    if isinstance(raw, os.PathLike):
        try:
            raw = os.fspath(raw)
        except Exception:
            return 0
    if raw is None or isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        val = -raw if raw < 0 else raw
        if val.bit_length() > 13_288:
            return game_data.MAX_JSON_SAFE_INT
        return val
    if isinstance(raw, (bytes, bytearray)):
        raw_s = bytes(raw).decode("utf-8", errors="replace")
    else:
        try:
            raw_s = str(raw)
        except ValueError:
            return game_data.MAX_JSON_SAFE_INT
        except Exception:
            return 0
    digits = re.sub(r"[^\d]", "", raw_s, flags=re.ASCII)
    if not digits:
        return 0
    if len(digits) > 4000:
        return game_data.MAX_JSON_SAFE_INT
    return int(digits)


def _parse_bounded_int(
    raw: Any,
    default: int = 0,
    *,
    cap: int = game_data.MAX_JSON_SAFE_INT,
) -> int:
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, int):
        return game_data.cap_json_int(raw, limit=cap, default=default)
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return default
        if s.isascii() and s.isdigit():
            trimmed = s.lstrip("0") or "0"
            if len(trimmed) > 4000:
                return cap
            return game_data.cap_json_int(int(trimmed), limit=cap, default=default)
        if len(s) > 1 and s[0] in "+-" and s[1:].isascii() and s[1:].isdigit():
            sign = -1 if s[0] == "-" else 1
            trimmed = s[1:].lstrip("0") or "0"
            if len(trimmed) > 4000:
                return -cap if sign < 0 else cap
            return game_data.cap_json_int(sign * int(trimmed), limit=cap, default=default)
        return default
    return game_data.cap_json_int(game_data.safe_int(raw, default), limit=cap, default=default)


def _safe_state_int(value: Any, default: int = 0) -> int:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isascii():
            return default
    return game_data.safe_int(value, default)


def _safe_state_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isascii():
            return default
        value = s
    try:
        f = float(value)
    except Exception:
        return default
    return f if math.isfinite(f) else default


def _safe_optional_state_int(value: Any) -> Optional[int]:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isascii():
            return None
    return game_data.safe_optional_int(value, None)


def _safe_optional_state_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isascii():
            return None
        value = s
    try:
        f = float(value)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _safe_state_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, os.PathLike):
        try:
            value = os.fspath(value)
        except Exception:
            return game_data.safe_key(value)
        if value is None:
            return default
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return game_data.fmt_compact_int(value) if abs(value).bit_length() >= 12_000 else str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return game_data.safe_key(value)


def _safe_optional_state_str(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(value, os.PathLike):
        try:
            value = os.fspath(value)
        except Exception:
            return game_data.safe_key(value)
        if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
            return None
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, int) and abs(value).bit_length() >= 12_000:
        return game_data.fmt_compact_int(value)
    return game_data.safe_key(value)


def _safe_state_seq(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(value)
    return ()


def _contains_non_dict_mapping(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> bool:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [32_768]

    def _walk(val: Any, d: int) -> bool:
        tv = type(val)
        if tv is dict:
            vid = id(val)
            if vid in seen or d >= 64 or budget[0] <= 0:
                return False
            budget[0] -= 1
            seen.add(vid)
            try:
                for v in val.values():
                    if budget[0] <= 0:
                        break
                    if _walk(v, d + 1):
                        return True
                return False
            finally:
                seen.discard(vid)
        if tv is list or tv is tuple:
            vid = id(val)
            if vid in seen or d >= 64 or budget[0] <= 0:
                return False
            budget[0] -= 1
            seen.add(vid)
            try:
                for v in val:
                    if budget[0] <= 0:
                        break
                    if _walk(v, d + 1):
                        return True
                return False
            finally:
                seen.discard(vid)
        if tv in (None, bool, int, float, str, bytes):
            return False
        if isinstance(val, Mapping):
            if not isinstance(val, dict):
                return True
            vid = id(val)
            if vid in seen or d >= 64 or budget[0] <= 0:
                return False
            budget[0] -= 1
            seen.add(vid)
            try:
                for v in val.values():
                    if budget[0] <= 0:
                        break
                    if _walk(v, d + 1):
                        return True
                return False
            finally:
                seen.discard(vid)
        if isinstance(val, (list, tuple)):
            vid = id(val)
            if vid in seen or d >= 64 or budget[0] <= 0:
                return False
            budget[0] -= 1
            seen.add(vid)
            try:
                for v in val:
                    if budget[0] <= 0:
                        break
                    if _walk(v, d + 1):
                        return True
                return False
            finally:
                seen.discard(vid)
        return False

    return _walk(obj, _depth)


def _deep_plain_value(
    value: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [32_768]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool or tv is int or tv is float or tv is str or tv is bytes:
            return val
        is_dict = tv is dict
        is_list = tv is list
        is_tuple = tv is tuple
        if is_dict or is_list or is_tuple or isinstance(val, (Mapping, list, tuple)):
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= 64 or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_dict or isinstance(val, Mapping):
                    out: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        sk = k if type(k) is str else game_data.safe_key(k)
                        out[sk] = _walk(v, d + 1)
                    return out
                if is_list or isinstance(val, list):
                    out_l: list[Any] = []
                    for v in val:
                        if budget[0] <= 0:
                            out_l.append("[Truncated]")
                            break
                        out_l.append(_walk(v, d + 1))
                    return out_l
                out_t: list[Any] = []
                for v in val:
                    if budget[0] <= 0:
                        out_t.append("[Truncated]")
                        break
                    out_t.append(_walk(v, d + 1))
                return tuple(out_t)
            finally:
                seen.discard(vid)
        return copy.deepcopy(val)

    return _walk(value, _depth)


@dataclass(frozen=True)
class Resources:
    """Four primary SpaceInvasion Classic resources."""

    pig_iron: int = 0
    metal: int = 0
    kryptonite: int = 0
    spice: int = 0

    @classmethod
    def from_list(cls, values: list[int] | tuple[int, ...]) -> Resources:
        seq = _safe_state_seq(values)
        padded = list(seq[:4]) + [0] * max(0, 4 - len(seq))
        return cls(
            pig_iron=max(0, _safe_state_int(padded[0], 0)),
            metal=max(0, _safe_state_int(padded[1], 0)),
            kryptonite=max(0, _safe_state_int(padded[2], 0)),
            spice=max(0, _safe_state_int(padded[3], 0)),
        )

    def can_afford(self, cost: Resources) -> bool:
        return (
            _safe_state_int(self.pig_iron, 0) >= _safe_state_int(getattr(cost, "pig_iron", 0), 0)
            and _safe_state_int(self.metal, 0) >= _safe_state_int(getattr(cost, "metal", 0), 0)
            and _safe_state_int(self.kryptonite, 0) >= _safe_state_int(getattr(cost, "kryptonite", 0), 0)
            and _safe_state_int(self.spice, 0) >= _safe_state_int(getattr(cost, "spice", 0), 0)
        )

    def missing(self, cost: Resources) -> Resources:
        return Resources(
            pig_iron=max(0, _safe_state_int(getattr(cost, "pig_iron", 0), 0) - _safe_state_int(self.pig_iron, 0)),
            metal=max(0, _safe_state_int(getattr(cost, "metal", 0), 0) - _safe_state_int(self.metal, 0)),
            kryptonite=max(0, _safe_state_int(getattr(cost, "kryptonite", 0), 0) - _safe_state_int(self.kryptonite, 0)),
            spice=max(0, _safe_state_int(getattr(cost, "spice", 0), 0) - _safe_state_int(self.spice, 0)),
        )

    def is_zero(self) -> bool:
        return (
            _safe_state_int(self.pig_iron, 0) == 0
            and _safe_state_int(self.metal, 0) == 0
            and _safe_state_int(self.kryptonite, 0) == 0
            and _safe_state_int(self.spice, 0) == 0
        )

    def format_short(self) -> str:
        parts = []
        pig = _safe_state_int(self.pig_iron, 0)
        met = _safe_state_int(self.metal, 0)
        kry = _safe_state_int(self.kryptonite, 0)
        spi = _safe_state_int(self.spice, 0)
        if pig:
            parts.append(f"PigIron={game_data.fmt_compact_int(pig, grouped=True)}")
        if met:
            parts.append(f"Metal={game_data.fmt_compact_int(met, grouped=True)}")
        if kry:
            parts.append(f"Krypt={game_data.fmt_compact_int(kry, grouped=True)}")
        if spi:
            parts.append(f"Spice={game_data.fmt_compact_int(spi, grouped=True)}")
        return ", ".join(parts) if parts else "0"


@dataclass(frozen=True)
class EnergyBalance:
    """Instantaneous planetary energy production and demand."""

    produced: int = 0
    used: int = 0

    @property
    def net(self) -> int:
        return _safe_state_int(self.produced, 0) - _safe_state_int(self.used, 0)


@dataclass(frozen=True)
class BuildingOffer:
    """Parsed building card from `internalBuildings`."""

    building_id: int
    name: str
    current_level: int
    target_level: int
    cost: Resources
    can_build: bool
    status_text: str
    build_href: Optional[str] = None


@dataclass(frozen=True)
class ResearchOffer:
    """Parsed research card from `internalResearch`."""

    research_id: int
    research_key: str
    name: str
    current_level: int
    target_level: int
    cost: Resources
    can_research: bool
    status_text: str
    research_href: Optional[str] = None
    active: bool = False


@dataclass(frozen=True)
class UnitOffer:
    """Parsed ship or defense card from `internalWeaponFactory` or `internalDefense`."""

    unit_type: str  # "ship" | "defense"
    unit_id: int
    name: str
    owned: int
    cost: Resources
    can_build: bool
    input_name: Optional[str] = None
    status_text: str = ""


@dataclass(frozen=True)
class TutorialTask:
    """One requirement line inside the active tutorial step."""

    description: str
    building_name: Optional[str] = None
    building_id: Optional[int] = None
    current: int = 0
    target: int = 0
    category: str = "building"
    research_id: Optional[str] = None

    @property
    def completed(self) -> bool:
        tgt = _safe_state_int(self.target, 0)
        cur = _safe_state_int(self.current, 0)
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
    """Active construction, research, or shipyard job."""

    category: str
    name: str
    building_id: Optional[int] = None
    target_level: Optional[int] = None
    remaining_text: str = ""


@dataclass(frozen=True)
class FleetAlert:
    """Active fleet movement observed on `internalHome`."""

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
        f_used = max(0, _safe_state_int(self.fields_used, 0))
        if f_used != self.fields_used or type(self.fields_used) is bool:
            object.__setattr__(self, "fields_used", f_used)
        f_max = max(0, _safe_state_int(self.fields_max, 0))
        if f_max != self.fields_max or type(self.fields_max) is bool:
            object.__setattr__(self, "fields_max", f_max)
        for fname in ("buildings", "building_names", "ships", "defense", "economy_rates"):
            val = getattr(self, fname)
            if not isinstance(val, dict):
                if isinstance(val, Mapping):
                    if fname in ("buildings", "ships", "defense", "economy_rates"):
                        object.__setattr__(
                            self,
                            fname,
                            {
                                game_data.safe_key(k): (
                                    0 if isinstance(v, bool) else (max(0, v) if isinstance(v, int) else v)
                                )
                                for k, v in val.items()
                            },
                        )
                    else:
                        object.__setattr__(
                            self,
                            fname,
                            {game_data.safe_key(k): v for k, v in val.items()},
                        )
                else:
                    object.__setattr__(self, fname, {})
            elif fname in ("buildings", "ships", "defense", "economy_rates") and any(
                isinstance(v, bool) or (isinstance(v, int) and v < 0) for v in val.values()
            ):
                object.__setattr__(
                    self,
                    fname,
                    {
                        game_data.safe_key(k): (
                            0 if isinstance(v, bool) else (max(0, v) if isinstance(v, int) else v)
                        )
                        for k, v in val.items()
                    },
                )

    @property
    def fields_remaining(self) -> int:
        f_max = max(0, _safe_state_int(self.fields_max, 0))
        f_used = max(0, _safe_state_int(self.fields_used, 0))
        return max(0, f_max - f_used) if f_max > 0 else 0

    def building_level(self, building_id: int) -> int:
        if not isinstance(self.buildings, Mapping):
            return 0
        return max(0, _safe_state_int(self.buildings.get(f"b{building_id}", 0), 0))

    def storage_fill_ratios(self) -> dict[str, float]:
        return {
            rk: game_data.safe_big_ratio(
                getattr(self.resources, rk, 0),
                getattr(self.storage, rk, 0),
                cap=1e200,
            )
            for rk in ("pig_iron", "metal", "kryptonite", "spice")
        }


RAID_COLONY_FORBIDDEN_BUILDINGS: frozenset[int] = frozenset(
    {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14, 19, 20, 21}
)


def _extract_planet_type_token(raw: Any) -> str:
    if isinstance(raw, os.PathLike):
        try:
            raw = os.fspath(raw)
        except Exception:
            return ""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return ""
    return game_data.safe_key(raw).strip().lower()


def normalize_planet_type(planet_or_type: Any) -> str:
    """Return normalized lowercase planet_type ('normal', 'asteroid', 'raid', 'moon', ...)."""
    if isinstance(planet_or_type, Mapping):
        txt = _extract_planet_type_token(planet_or_type.get("planet_type"))
        if not txt:
            txt = _extract_planet_type_token(planet_or_type.get("type", "normal"))
        return txt or "normal"
    if isinstance(planet_or_type, (str, bytes, bytearray, os.PathLike)):
        return _extract_planet_type_token(planet_or_type) or "normal"
    raw = getattr(planet_or_type, "planet_type", planet_or_type)
    return _extract_planet_type_token(raw) or "normal"


def is_asteroid_planet(planet_or_type: Any) -> bool:
    return normalize_planet_type(planet_or_type) == "asteroid"


def is_raid_planet(planet_or_type: Any) -> bool:
    return normalize_planet_type(planet_or_type) == "raid"


def is_building_allowed_on_planet_type(
    planet_or_type: Any,
    building_id: int,
    *,
    has_b8_offer: bool = False,
    allow_asteroid_research_lab: bool = False,
) -> bool:
    """Enforce REQ-BLD-003 building eligibility for asteroids, raid colonies, and normal planets."""
    if isinstance(building_id, bool):
        return False
    if isinstance(building_id, int):
        bid = building_id
    elif isinstance(building_id, float):
        if not math.isfinite(building_id) or not building_id.is_integer():
            return False
        bid = int(building_id)
    elif isinstance(building_id, (str, bytes, bytearray)):
        raw_s = bytes(building_id).decode("utf-8", errors="replace") if isinstance(building_id, (bytes, bytearray)) else building_id
        s = raw_s.strip()
        if not s or not s.isascii():
            return False
        if not (s.isdigit() or (len(s) > 1 and s[0] in "+-" and s[1:].isdigit())) or len(s) > 100:
            return False
        bid = int(s)
    else:
        return False
    info = game_data.BUILDING_BY_ID.get(bid)
    if not info:
        return False
    ptype = normalize_planet_type(planet_or_type)
    if ptype == "asteroid":
        if bool(info.get("asteroid_ok", False)):
            return True
        return bid == 8 and (bool(allow_asteroid_research_lab) or bool(has_b8_offer))
    if bid == 19:
        return False
    if ptype == "raid" and bid in RAID_COLONY_FORBIDDEN_BUILDINGS:
        return False
    return True


@dataclass(frozen=True)
class GameState:
    """Complete snapshot of the player's empire in the current universe."""

    universe_id: int
    user_id: int
    nickname: str
    timestamp: float
    planets: tuple[PlanetState, ...]
    active_planet_id: Optional[int] = None
    researches: dict[str, int] = field(default_factory=dict)
    research_names: dict[str, str] = field(default_factory=dict)
    specs: dict[str, int] = field(default_factory=dict)
    unread_messages: dict[str, int] = field(default_factory=dict)
    tutorial: Optional[TutorialStatus] = None
    active_queues: tuple[QueueJob, ...] = field(default_factory=tuple)
    fleet_alerts: tuple[FleetAlert, ...] = field(default_factory=tuple)
    research_offers: tuple[ResearchOffer, ...] = field(default_factory=tuple)
    speedup_factor: Optional[float] = None
    newbie_protection: bool = False
    account_points: Optional[int] = None
    account_rank: Optional[int] = None
    raid_targets: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    raid_stats: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for fname in ("researches", "research_names", "specs", "unread_messages"):
            val = getattr(self, fname)
            if not isinstance(val, dict):
                if isinstance(val, Mapping):
                    if fname in ("researches", "specs", "unread_messages"):
                        object.__setattr__(
                            self,
                            fname,
                            {
                                game_data.safe_key(k): (
                                    0 if isinstance(v, bool) else (max(0, v) if isinstance(v, int) else v)
                                )
                                for k, v in val.items()
                            },
                        )
                    else:
                        object.__setattr__(
                            self,
                            fname,
                            {game_data.safe_key(k): v for k, v in val.items()},
                        )
                else:
                    object.__setattr__(self, fname, {})
            elif fname in ("researches", "specs", "unread_messages") and any(
                isinstance(v, bool) or (isinstance(v, int) and v < 0) for v in val.values()
            ):
                object.__setattr__(
                    self,
                    fname,
                    {
                        game_data.safe_key(k): (
                            0 if isinstance(v, bool) else (max(0, v) if isinstance(v, int) else v)
                        )
                        for k, v in val.items()
                    },
                )
        rs = self.raid_stats
        if isinstance(rs, Mapping):
            if not isinstance(rs, dict) or _contains_non_dict_mapping(rs):
                object.__setattr__(self, "raid_stats", _deep_plain_value(rs))
        else:
            object.__setattr__(self, "raid_stats", {})
        rt = self.raid_targets
        if rt:
            changed = not isinstance(rt, tuple)
            norm_rt: list[dict[str, Any]] = []
            for item in rt:
                if isinstance(item, Mapping):
                    if not isinstance(item, dict) or _contains_non_dict_mapping(item):
                        changed = True
                        norm_rt.append(_deep_plain_value(item))
                    else:
                        norm_rt.append(item)
                else:
                    changed = True
            if changed:
                object.__setattr__(self, "raid_targets", tuple(norm_rt))
        elif not isinstance(rt, tuple):
            object.__setattr__(self, "raid_targets", ())

    def active_planet(self) -> Optional[PlanetState]:
        if not self.planets:
            return None
        if self.active_planet_id is not None:
            for planet in self.planets:
                if planet.planet_id == self.active_planet_id:
                    return planet
        return self.planets[0]


def _extract_levels_and_names(mapping: Any) -> tuple[dict[str, int], dict[str, str]]:
    levels: dict[str, int] = {}
    names: dict[str, str] = {}
    if not isinstance(mapping, Mapping):
        return levels, names
    for key, val in mapping.items():
        if isinstance(val, Mapping):
            k_str = game_data.safe_key(key)
            levels[k_str] = max(0, _safe_state_int(val.get("level", 0), 0))
            if val.get("name"):
                names[k_str] = _safe_state_str(val["name"])
    return levels, names


def parse_planets_overview_json(json_text: str) -> GameState:
    """Parse `#json-container` from `internalPlanetsOverview` into a `GameState`."""
    raw_payload = json.loads(json_text)
    payload = game_data.sanitize_json_tree(
        raw_payload, max_int=10**4000, nonfinite_float=0.0
    )
    if not isinstance(payload, Mapping):
        payload = {}
    info = payload.get("info") if isinstance(payload.get("info"), Mapping) else {}
    user = info.get("user") if isinstance(info.get("user"), Mapping) else {}
    uni = info.get("universe") if isinstance(info.get("universe"), Mapping) else {}

    planets: list[PlanetState] = []
    for raw_p in _safe_state_seq(payload.get("planets")):
        if not isinstance(raw_p, Mapping):
            continue
        b_levels, b_names = _extract_levels_and_names(raw_p.get("buildings"))
        s_levels, _ = _extract_levels_and_names(raw_p.get("ships"))
        d_levels, _ = _extract_levels_and_names(raw_p.get("defense"))
        energy_raw = raw_p.get("energy") if isinstance(raw_p.get("energy"), Mapping) else {}
        fields_max = max(0, _safe_state_int(raw_p.get("fields", 0), 0))
        fields_used = sum(b_levels.values())

        planets.append(
            PlanetState(
                planet_id=max(0, _safe_state_int(raw_p.get("id", 0), 0)),
                name=_safe_state_str(raw_p.get("name", "")),
                coords=_safe_state_str(raw_p.get("coords", "")),
                planet_type=normalize_planet_type({"type": raw_p.get("type")}),
                temperature=_safe_state_int(raw_p.get("temperature", 0), 0),
                fields_used=fields_used,
                fields_max=fields_max,
                resources=Resources.from_list(raw_p.get("resources", [])),
                storage=Resources.from_list(raw_p.get("storage", [])),
                energy=EnergyBalance(
                    produced=max(0, _safe_state_int(energy_raw.get("produced", 0), 0)),
                    used=max(0, _safe_state_int(energy_raw.get("used", 0), 0)),
                ),
                buildings=b_levels,
                building_names=b_names,
                ships=s_levels,
                defense=d_levels,
            )
        )

    r_levels, r_names = _extract_levels_and_names(payload.get("researches"))
    specs_raw = payload.get("specs")
    specs = (
        {game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in specs_raw.items()}
        if isinstance(specs_raw, Mapping)
        else {}
    )

    return GameState(
        universe_id=max(0, _safe_state_int(uni.get("id", 0), 0)),
        user_id=max(0, _safe_state_int(user.get("userID", 0), 0)),
        nickname=_safe_state_str(user.get("nickname", "")),
        timestamp=_safe_state_float(info.get("ts", 0.0), 0.0),
        planets=tuple(planets),
        active_planet_id=planets[0].planet_id if planets else None,
        researches=r_levels,
        research_names=r_names,
        specs=specs,
    )


def _parse_cost_from_text(text: Any) -> Resources:
    """Extract `Required: X Pig iron Y Metal Z Kryptonite W Spice` values."""
    text_s = _safe_state_str(text)
    if not text_s:
        return Resources()
    req_match = re.search(r"Required:\s*(.*?)(?:Active Building|Upgrade|Build level|Not enough|Cancel|$)", text_s, re.IGNORECASE)
    segment = req_match.group(1) if req_match else text_s

    def _find_amt(label: str) -> int:
        m = re.search(rf"([\d.]+)\s*{label}", segment, re.IGNORECASE | re.ASCII)
        return parse_si_int(m.group(1)) if m else 0

    return Resources(
        pig_iron=_find_amt(r"Pig\s+iron"),
        metal=_find_amt(r"Metal"),
        kryptonite=_find_amt(r"Kryptonite"),
        spice=_find_amt(r"Spice"),
    )


def _find_item_card_row(
    detail_anchor,
    detail_type: str,
    action_param: Optional[str],
    item_id: int,
) -> Any:
    """Walk upward from an item detail anchor to its single-item card container."""
    node = detail_anchor
    fallback = detail_anchor.parent
    detail_re = re.compile(rf"action=internalDetail&type={re.escape(detail_type)}&id=(\d+)", re.ASCII)
    for _ in range(10):
        if node is None or node.parent is None:
            break
        node = node.parent
        other_ids = {
            m.group(1).lstrip("0") or "0"
            for a in node.find_all("a", href=True)
            for m in [detail_re.search(a["href"])]
            if m
        }
        if len(other_ids) > 1:
            break
        fallback = node
        txt = node.get_text(" ", strip=True)
        has_req = "Required:" in txt
        has_action = bool(action_param and node.select_one(f'a[href*="{action_param}={item_id}"]') is not None)
        if has_req and (has_action or node.name in ("table", "tbody", "tr", "div")):
            return node
    return fallback


def _find_card_row(detail_anchor, building_id: int) -> Any:
    """Backward-compatible wrapper for building card traversal."""
    return _find_item_card_row(detail_anchor, "building", "buildingID", building_id)


def parse_building_queue(html: str) -> tuple[QueueJob, ...]:
    """Parse active and queued jobs from `Construction queue (...)` in `internalBuildings`."""
    soup = BeautifulSoup(html, "html.parser")
    queue_table = None
    for tbl in soup.find_all("table"):
        txt = " ".join(tbl.get_text(" ", strip=True).split())
        if "Construction queue" in txt:
            queue_table = tbl
            break
    if queue_table is None:
        return ()

    jobs: list[QueueJob] = []
    seen: set[tuple[str, int]] = set()
    for m in re.finditer(r"([A-Za-z][A-Za-z ]+?)\s*\(\s*Level\s+(\d+)\s*\)", queue_table.get_text(" ", strip=True), re.ASCII):
        bname = " ".join(m.group(1).split())
        lvl = _parse_bounded_int(m.group(2), 0)
        key = (bname.lower(), lvl)
        if key in seen:
            continue
        seen.add(key)
        jobs.append(
            QueueJob(
                category="building",
                name=bname,
                building_id=BUILDING_ID_BY_NAME.get(bname.lower()),
                target_level=lvl,
            )
        )
    return tuple(jobs)


def parse_buildings_page(
    html: str,
    current_levels: Optional[dict[str, int]] = None,
) -> tuple[BuildingOffer, ...]:
    """Parse building cards, costs, and actionable build links from `internalBuildings`."""
    soup = BeautifulSoup(html, "html.parser")
    levels = current_levels or {}
    seen_ids: set[int] = set()
    offers: list[BuildingOffer] = []

    def _parse_card(card: Any, bid: int, name: str) -> None:
        card_text = " ".join(card.get_text(" ", strip=True).split()) if card else name
        cost = _parse_cost_from_text(card_text)
        build_link = (
            card.select_one(f'a[href*="action=internalBuildItem"][href*="buildingID={bid}"]')
            if card
            else None
        )
        cur_lvl = max(0, _parse_bounded_int(levels.get(f"b{bid}", 0), 0))
        target_lvl = cur_lvl + 1

        if build_link is not None:
            link_text = " ".join(build_link.get_text(" ", strip=True).split())
            m_lvl = re.search(r"(\d+)", link_text, re.ASCII)
            if m_lvl:
                parsed_tgt = _parse_bounded_int(m_lvl.group(1), -1)
                if parsed_tgt >= 0:
                    target_lvl = parsed_tgt
                    cur_lvl = max(cur_lvl, target_lvl - 1)
            offers.append(
                BuildingOffer(
                    building_id=bid,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_build=True,
                    status_text=link_text,
                    build_href=build_link.get("href"),
                )
            )
        else:
            status = "Not enough resources"
            if "not enough resources" not in card_text.lower():
                if "in construction" in card_text.lower() or "cancel" in card_text.lower():
                    status = "In construction"
                else:
                    status = "Unavailable"
            offers.append(
                BuildingOffer(
                    building_id=bid,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_build=False,
                    status_text=status,
                    build_href=None,
                )
            )

    # Prefer explicit `div.build-card.b-<id>` blocks when present in live DOM
    for card in soup.select("div.build-card"):
        cls_id = next(
            (
                _parse_bounded_int(c[2:], -1)
                for c in (card.get("class") or [])
                if isinstance(c, str) and re.fullmatch(r"b-\d+", c, re.ASCII)
            ),
            None,
        )
        if cls_id is not None and cls_id < 0:
            cls_id = None
        detail_a = card.select_one('a[href*="action=internalDetail&type=building&id="]')
        if cls_id is None and detail_a is not None:
            m_id = re.search(r"id=(\d+)", detail_a["href"], re.ASCII)
            parsed_cls = _parse_bounded_int(m_id.group(1), -1) if m_id else -1
            cls_id = parsed_cls if parsed_cls >= 0 else None
        if cls_id is None or cls_id in seen_ids:
            continue
        name = " ".join(detail_a.get_text(" ", strip=True).split()) if detail_a else ""
        if not name:
            continue
        seen_ids.add(cls_id)
        _parse_card(card, cls_id, name)

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        m_id = re.search(r"action=internalDetail&type=building&id=(\d+)", href, re.ASCII)
        if not m_id:
            continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if not name:
            continue
        bid = _parse_bounded_int(m_id.group(1), -1)
        if bid < 0 or bid in seen_ids:
            continue
        seen_ids.add(bid)
        card = _find_item_card_row(anchor, "building", "buildingID", bid)
        _parse_card(card, bid, name)

    return tuple(offers)


def parse_research_page(
    html: str,
    researches: Optional[dict[str, int]] = None,
) -> tuple[ResearchOffer, ...]:
    """Parse research cards, costs, active status, and `researchID` links from `internalResearch`."""
    soup = BeautifulSoup(html, "html.parser")
    levels = researches or {}
    seen_ids: set[int] = set()
    offers: list[ResearchOffer] = []

    for anchor in soup.find_all("a", href=True):
        m_id = re.search(r"action=internalDetail&type=research&id=(\d+)", anchor["href"], re.ASCII)
        if not m_id:
            continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if not name:
            continue
        rid_num = _parse_bounded_int(m_id.group(1), -1)
        if rid_num < 0 or rid_num in seen_ids:
            continue
        seen_ids.add(rid_num)

        card = _find_item_card_row(anchor, "research", "researchID", rid_num)
        card_text = " ".join(card.get_text(" ", strip=True).split()) if card else name
        cost = _parse_cost_from_text(card_text)
        rkey = f"r{rid_num}"
        cur_lvl = max(0, _parse_bounded_int(levels.get(rkey, 0), 0))
        target_lvl = cur_lvl + 1

        res_link = card.select_one(f'a[href*="internalBuildItem"][href*="researchID={rid_num}"]') if card else None
        card_lower = card_text.lower()
        is_active = any(tok in card_lower for tok in ("cancel", "abort", "in research"))

        if res_link is not None:
            link_text = " ".join(res_link.get_text(" ", strip=True).split())
            m_lvl = re.search(r"(\d+)", link_text, re.ASCII)
            if m_lvl:
                parsed_tgt = _parse_bounded_int(m_lvl.group(1), -1)
                if parsed_tgt >= 0:
                    target_lvl = parsed_tgt
                    cur_lvl = max(cur_lvl, target_lvl - 1)
            offers.append(
                ResearchOffer(
                    research_id=rid_num,
                    research_key=rkey,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_research=True,
                    status_text=link_text,
                    research_href=res_link.get("href"),
                    active=is_active,
                )
            )
        else:
            status = "In research" if is_active else ("Not enough resources" if "not enough" in card_lower else "Unavailable")
            offers.append(
                ResearchOffer(
                    research_id=rid_num,
                    research_key=rkey,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_research=False,
                    status_text=status,
                    research_href=None,
                    active=is_active,
                )
            )

    return tuple(offers)


def parse_unit_factory_page(
    html: str,
    *,
    unit_type: str = "ship",
    owned_counts: Optional[dict[str, int]] = None,
) -> tuple[UnitOffer, ...]:
    """Parse unit build cards from `internalWeaponFactory` (`ship`) or `internalDefense` (`defense`)."""
    soup = BeautifulSoup(html, "html.parser")
    owned_map = owned_counts or {}
    detail_type = "defense" if unit_type == "defense" else "ship"
    seen_ids: set[int] = set()
    offers: list[UnitOffer] = []
    prefix = "d" if detail_type == "defense" else "s"

    for anchor in soup.find_all("a", href=True):
        m_id = re.search(rf"action=internalDetail&type={detail_type}&id=(\d+)", anchor["href"], re.ASCII)
        if not m_id:
            continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if not name:
            continue
        uid = _parse_bounded_int(m_id.group(1), -1)
        if uid < 0 or uid in seen_ids:
            continue
        seen_ids.add(uid)

        card = _find_item_card_row(anchor, detail_type, None, uid)
        card_text = " ".join(card.get_text(" ", strip=True).split()) if card else name
        cost = _parse_cost_from_text(card_text)

        owned = max(0, _parse_bounded_int(owned_map.get(f"{prefix}{uid}", 0), 0))
        m_own = re.search(r"(?:Available|On planet|Currently)\s*:\s*([\d.]+)", card_text, re.IGNORECASE | re.ASCII)
        if m_own and owned == 0:
            owned = parse_si_int(m_own.group(1))

        input_name: Optional[str] = None
        if card:
            for inp in card.find_all("input"):
                itype = (inp.get("type") or "text").lower()
                iname = inp.get("name") or ""
                if itype in ("text", "number") and iname and iname not in ("sid", "packamount"):
                    if not inp.has_attr("disabled"):
                        input_name = str(iname)
                    break

        can_build = input_name is not None
        status = "Buildable" if can_build else "Locked / insufficient"
        offers.append(
            UnitOffer(
                unit_type=detail_type,
                unit_id=uid,
                name=name,
                owned=owned,
                cost=cost,
                can_build=can_build,
                input_name=input_name,
                status_text=status,
            )
        )

    return tuple(offers)


def parse_tutorial_from_home(html: str) -> Optional[TutorialStatus]:
    """Parse tutorial banner step and task lines from `internalHome` or `internalBuildings`."""
    soup = BeautifulSoup(html, "html.parser")
    full_text = " ".join(soup.get_text(" ", strip=True).split())
    step_m = re.search(r"Tutorial\s+(\d+)\s*/\s*(\d+)", full_text, re.ASCII)
    if not step_m:
        return None

    step = _parse_bounded_int(step_m.group(1), 0)
    total = _parse_bounded_int(step_m.group(2), 0)

    tut_div = soup.find(id="tutorial.text")
    tut_text = " ".join(tut_div.get_text(" ", strip=True).split()) if tut_div else full_text

    def _parse_tut_counter(raw_cnt: str) -> int:
        txt = raw_cnt.strip()
        if "." in txt:
            txt = txt.replace(".", "")
        return _parse_bounded_int(txt, 0)

    def _unit_singular_plural_variants(raw_name: str) -> list[str]:
        clean = " ".join(raw_name.split()).strip()
        if not clean:
            return []
        variants: list[str] = [clean]
        low = clean.lower()
        if low.endswith("ies") and len(clean) > 3:
            variants.append(f"{clean[:-3]}y")
        if low.endswith("s") and len(clean) > 2:
            variants.append(clean[:-1])
        if low.endswith("es") and len(clean) > 3:
            variants.append(clean[:-2])
        if not low.endswith("s"):
            variants.append(f"{clean}s")
        dedup_v: list[str] = []
        for cand in variants:
            if cand and cand not in dedup_v:
                dedup_v.append(cand)
        return dedup_v

    def _resolve_catalog_unit(category: str, raw_name: str) -> Optional[dict]:
        for cand in _unit_singular_plural_variants(raw_name):
            unit = game_data.resolve_unit(category, cand)
            if unit:
                return unit
        return None

    def _resolve_ship_id(slug_or_name: str) -> Optional[int]:
        unit = _resolve_catalog_unit("ships", slug_or_name)
        if not unit:
            return None
        from spaceinvasion.fleet import SHIP_SLUG_BY_KEY

        slug = str(unit.get("slug") or "")
        for skey, sslug in SHIP_SLUG_BY_KEY.items():
            if sslug == slug and skey.startswith("s") and skey[1:].isascii() and skey[1:].isdigit():
                return int(skey[1:])
        return None

    def _resolve_building_name_and_id(raw_name: str) -> tuple[Optional[str], Optional[int]]:
        for cand in _unit_singular_plural_variants(raw_name):
            bid = BUILDING_ID_BY_NAME.get(cand.lower())
            if bid is not None:
                binfo = game_data.BUILDING_BY_ID.get(bid)
                return str((binfo or {}).get("name") or cand), bid
            unit = game_data.resolve_unit("buildings", cand)
            if unit and unit.get("id") is not None:
                return str(unit.get("name") or cand), int(unit["id"])
        return None, None

    def _classify_task(label: str, cur: int, tgt: int) -> TutorialTask:
        desc = f"{label} {cur} / {tgt}"
        inc_m = re.match(r"Increase your\s+([A-Za-z -]+?)\s+level$", label, re.IGNORECASE)
        if inc_m:
            bname = " ".join(inc_m.group(1).split())
            canon_bname, bid = _resolve_building_name_and_id(bname)
            return TutorialTask(
                description=desc,
                building_name=canon_bname or bname,
                building_id=bid,
                current=cur,
                target=tgt,
                category="building",
            )
        upg_m = re.match(r"(?:Upgrade|Research)\s+(?:your\s+)?([A-Za-z -]+?)(?:\s+level)?$", label, re.IGNORECASE)
        if upg_m:
            rname = " ".join(upg_m.group(1).split())
            unit = _resolve_catalog_unit("research", rname)
            if unit or label.lower().startswith("upgrade your "):
                rid = str(unit["id"]) if unit and unit.get("id") else None
                return TutorialTask(
                    description=desc,
                    building_name=str(unit["name"]) if unit and unit.get("name") else rname,
                    building_id=None,
                    current=cur,
                    target=tgt,
                    category="research",
                    research_id=rid,
                )
        bld_m = re.match(
            r"(?:Build|Construct|Produce)\s+(?:(?:an?|\d+)\s+)?([A-Za-z][A-Za-z -]*?)(?:\s+level)?$",
            label,
            re.IGNORECASE,
        )
        if bld_m:
            uname = " ".join(bld_m.group(1).split())
            ship_unit = _resolve_catalog_unit("ships", uname)
            if ship_unit:
                return TutorialTask(
                    description=desc,
                    building_name=str(ship_unit["name"]),
                    building_id=_resolve_ship_id(str(ship_unit["slug"])),
                    current=cur,
                    target=tgt,
                    category="ship",
                )
            canon_bname, bid = _resolve_building_name_and_id(uname)
            if bid is not None:
                return TutorialTask(
                    description=desc,
                    building_name=canon_bname or uname,
                    building_id=bid,
                    current=cur,
                    target=tgt,
                    category="building",
                )
        return TutorialTask(
            description=desc,
            building_name=label,
            building_id=None,
            current=cur,
            target=tgt,
            category="milestone",
        )

    tasks: list[TutorialTask] = []
    # Pattern matches lines like "Increase your Fusion plant level 0 / 3"
    for m in re.finditer(
        r"(Increase your\s+([A-Za-z -]+?)\s+level)\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)",
        full_text,
        re.IGNORECASE | re.ASCII,
    ):
        tasks.append(
            _classify_task(
                " ".join(m.group(1).split()),
                _parse_tut_counter(m.group(3)),
                _parse_tut_counter(m.group(4)),
            )
        )

    # Tutorial 4+ research lines like "Upgrade your Energy technology 0 / 2"
    for m in re.finditer(
        r"(Upgrade your\s+([A-Za-z -]+?))\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)",
        full_text,
        re.IGNORECASE | re.ASCII,
    ):
        tasks.append(
            _classify_task(
                " ".join(m.group(1).split()),
                _parse_tut_counter(m.group(3)),
                _parse_tut_counter(m.group(4)),
            )
        )

    # Tutorial ship/building/defense build lines like "Build a Small transport 1 / 2" or "Build Spy probes 0 / 2"
    for m in re.finditer(
        r"\b((?:Build|Construct|Produce)\s+(?:(?:an?|\d+)\s+)?([A-Za-z][A-Za-z -]{1,45}?))\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)",
        full_text,
        re.IGNORECASE | re.ASCII,
    ):
        uname = " ".join(m.group(2).split())
        if (
            _resolve_catalog_unit("ships", uname)
            or _resolve_building_name_and_id(uname)[1] is not None
            or _resolve_catalog_unit("defenses", uname)
        ):
            tasks.append(
                _classify_task(
                    " ".join(m.group(1).split()),
                    _parse_tut_counter(m.group(3)),
                    _parse_tut_counter(m.group(4)),
                )
            )

    # Parse structured task rows and `<br>`/newline lines inside `#tutorial.text` (captures combat/spy/recycle/colonize/escape milestones)
    if tut_div is not None:
        for tr in tut_div.find_all("tr"):
            if tr.find("tr") is not None:
                continue
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 2:
                continue
            cnt_txt = " ".join(tds[-1].get_text(" ", strip=True).split())
            cnt_m = re.match(r"^(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)$", cnt_txt, re.ASCII)
            if not cnt_m:
                continue
            label_parts = [
                " ".join(td.get_text(" ", strip=True).split())
                for td in tds[:-1]
            ]
            label = " ".join(p for p in label_parts if p).strip()
            if label and not re.match(r"^Tutorial\s+\d+", label, re.IGNORECASE | re.ASCII):
                tasks.append(
                    _classify_task(
                        label,
                        _parse_tut_counter(cnt_m.group(1)),
                        _parse_tut_counter(cnt_m.group(2)),
                    )
                )

        for raw_line in tut_div.get_text("\n", strip=True).splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            low_line = line.lower()
            if low_line.startswith("tutorial ") or "you receive the following" in low_line:
                continue
            line_m = re.match(
                r"^([A-Za-z][A-Za-z0-9 ()'+-]{2,80}?)\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)$",
                line,
                re.ASCII,
            )
            if line_m:
                lbl = line_m.group(1).strip()
                if lbl and not lbl.lower().startswith("tutorial"):
                    tasks.append(
                        _classify_task(
                            lbl,
                            _parse_tut_counter(line_m.group(2)),
                            _parse_tut_counter(line_m.group(3)),
                        )
                    )

    # Deduplicate identical task strings if duplicated across tutorial summary & body
    deduped: list[TutorialTask] = []
    seen_desc: set[str] = set()
    for t in tasks:
        key = t.description.lower()
        if key not in seen_desc:
            seen_desc.add(key)
            deduped.append(t)

    reward_m = re.search(
        r"You receive the following resources:\s*([^\n]+?)(?:$|Active|Pig iron mine)",
        tut_text,
    )
    reward_text = reward_m.group(1).strip() if reward_m else ""
    can_complete = soup.select_one('input[name="tutorialComplete"]') is not None

    return TutorialStatus(
        step=step,
        total_steps=total,
        tasks=tuple(deduped),
        reward_text=reward_text,
        can_complete=can_complete,
    )


def parse_home_metadata(html: str) -> tuple[Optional[int], Optional[float], bool, tuple[QueueJob, ...], tuple[FleetAlert, ...]]:
    """Extract active planet ID, speedup factor, newbie protection, queues, and fleet alerts."""
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())

    active_planet_id: Optional[int] = None
    m_pid = re.search(r"planetID\s*:\s*(\d+)", html, re.ASCII)
    if m_pid:
        pid_val = _parse_bounded_int(m_pid.group(1), -1)
        active_planet_id = pid_val if pid_val >= 0 else None

    speedup: Optional[float] = None
    m_spd = re.search(r"active factor:\s*x([0-9.]+)", text, re.IGNORECASE | re.ASCII)
    if m_spd:
        spd_val = _safe_optional_state_float(m_spd.group(1))
        speedup = spd_val if (spd_val is not None and spd_val > 0.0) else None

    newbie = "no attacks can be launched on you" in text.lower()

    queues: list[QueueJob] = []
    for box_id, cat in (
        ("sp-own-building", "building"),
        ("sp-own-research", "research"),
        ("sp-own-fleet", "fleet"),
    ):
        el = soup.find(id=box_id)
        if el:
            content = " ".join(el.get_text(" ", strip=True).split())
            if content:
                q_bid: Optional[int] = None
                q_lvl: Optional[int] = None
                if cat == "building":
                    m_bq = re.search(
                        r"([A-Za-z][A-Za-z ]+?)\s*\(\s*Level\s+(\d+)\s*\)",
                        content,
                        re.ASCII,
                    )
                    if m_bq:
                        bname = " ".join(m_bq.group(1).split()).lower()
                        q_bid = BUILDING_ID_BY_NAME.get(bname)
                        parsed_lvl = _parse_bounded_int(m_bq.group(2), 0)
                        q_lvl = parsed_lvl if parsed_lvl > 0 else None
                queues.append(
                    QueueJob(
                        category=cat,
                        name=content,
                        building_id=q_bid,
                        target_level=q_lvl,
                    )
                )

    alerts: list[FleetAlert] = []
    # Check for hostile attack indicators rendered on overview
    for tag in soup.select('[class*="attack"], [id*="attack"]'):
        msg = " ".join(tag.get_text(" ", strip=True).split())
        if msg and "no attacks can be launched on you" not in msg.lower() and "beware of the attacks" not in msg.lower():
            alerts.append(FleetAlert(mission="ATTACK", hostile=True, summary=msg[:200]))

    return active_planet_id, speedup, newbie, tuple(queues), tuple(alerts)


_MENU_POINTS_RANK_RE = re.compile(
    r"\b(?:Points|Punkte|Pontos)\s*[:\-]?\s*([\d.]+)(?:\s*(?:#|Rank|Platz|Pos)\s*[:\-]?\s*([\d.]+))?",
    re.IGNORECASE | re.ASCII,
)


def parse_menu_status(html: str) -> tuple[dict[str, int], Optional[int], Optional[int]]:
    """Extract unread message counts, server points, and rank from `F_Navi` (`internalMenu`)."""
    soup = BeautifulSoup(html, "html.parser")
    counts: dict[str, int] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "action=internalMessages" not in href:
            continue
        m_type = re.search(r"type=([a-z]+)", href)
        key = m_type.group(1) if m_type else "total"
        txt = " ".join(anchor.get_text(" ", strip=True).split())
        m_num = re.search(r"(\d+)", txt, re.ASCII)
        counts[key] = _parse_bounded_int(m_num.group(1), 0) if m_num else 0

    full_text = " ".join(soup.get_text(" ", strip=True).split())
    points: Optional[int] = None
    rank: Optional[int] = None
    m_stat = _MENU_POINTS_RANK_RE.search(full_text)
    if m_stat:
        points = parse_si_int(m_stat.group(1))
        if m_stat.group(2):
            rank = parse_si_int(m_stat.group(2))
    return counts, points, rank


def parse_economy_rates(html: str) -> dict[str, int]:
    """Extract per-building percentage throttle values (`resource1`..`resource6`) from `internalEconomy`."""
    soup = BeautifulSoup(html, "html.parser")
    rates: dict[str, int] = {}
    for sel in soup.find_all("select", attrs={"name": re.compile(r"^resource\d+$", re.ASCII)}):
        name = str(sel.get("name") or "")
        selected = sel.find("option", selected=True)
        if selected is None:
            options = sel.find_all("option")
            selected = options[0] if options else None
        if selected is not None:
            raw_val = str(selected.get("value") or selected.get_text(strip=True))
            rates[name] = parse_si_int(raw_val)
    return rates


_TICKER_INPUT_MAP: tuple[tuple[str, str], ...] = (
    ("roheisen", "pig_iron"),
    ("metall", "metal"),
    ("kryptonit", "kryptonite"),
    ("spice", "spice"),
)


def parse_ticker_rates(html: str) -> dict[str, float]:
    """Extract live per-second production rates from hidden `<form id="ress">` ticker inputs."""
    if not isinstance(html, str) or "metall" not in html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    ress_form = soup.find("form", id="ress")
    if ress_form is None:
        return {}
    rates: dict[str, float] = {}
    for input_id, key in _TICKER_INPUT_MAP:
        inp = ress_form.find("input", id=input_id)
        if inp is None:
            continue
        val = _safe_optional_state_float(inp.get("value"))
        if val is not None and val >= 0.0:
            rates[key] = val
    return rates


def parse_menu_messages(html: str) -> dict[str, int]:
    """Extract unread message counts per category from `F_Navi` (`internalMenu`)."""
    counts, _, _ = parse_menu_status(html)
    return counts


def _resources_from_mapping(raw: Any) -> Resources:
    if not isinstance(raw, Mapping):
        return Resources()
    return Resources(
        pig_iron=max(0, _safe_state_int(raw.get("pig_iron", 0), 0)),
        metal=max(0, _safe_state_int(raw.get("metal", 0), 0)),
        kryptonite=max(0, _safe_state_int(raw.get("kryptonite", 0), 0)),
        spice=max(0, _safe_state_int(raw.get("spice", 0), 0)),
    )


def _energy_from_mapping(raw: Any) -> EnergyBalance:
    if not isinstance(raw, Mapping):
        return EnergyBalance()
    return EnergyBalance(
        produced=max(0, _safe_state_int(raw.get("produced", 0), 0)),
        used=max(0, _safe_state_int(raw.get("used", 0), 0)),
    )


def game_state_from_dict(data: Mapping[str, Any]) -> GameState:
    """Reconstruct a typed `GameState` from serialized JSON (e.g. `GET /state`)."""
    if not isinstance(data, Mapping):
        data = {}
    planets: list[PlanetState] = []
    for raw_p in _safe_state_seq(data.get("planets")):
        if not isinstance(raw_p, Mapping):
            continue
        offers_list: list[BuildingOffer] = []
        for o in _safe_state_seq(raw_p.get("building_offers")):
            if not isinstance(o, Mapping):
                continue
            cur_lvl = max(0, _safe_state_int(o.get("current_level", 0), 0))
            offers_list.append(
                BuildingOffer(
                    building_id=max(0, _safe_state_int(o.get("building_id", 0), 0)),
                    name=_safe_state_str(o.get("name") or ""),
                    current_level=cur_lvl,
                    target_level=max(0, _safe_state_int(o.get("target_level", cur_lvl + 1), cur_lvl + 1)),
                    cost=_resources_from_mapping(o.get("cost")),
                    can_build=bool(o.get("can_build", False)),
                    status_text=_safe_state_str(o.get("status_text") or ""),
                    build_href=_safe_optional_state_str(o.get("build_href")),
                )
            )
        offers = tuple(offers_list)
        ship_offers = tuple(
            UnitOffer(
                unit_type=_safe_state_str(u.get("unit_type") or "ship"),
                unit_id=max(0, _safe_state_int(u.get("unit_id", 0), 0)),
                name=_safe_state_str(u.get("name") or ""),
                owned=max(0, _safe_state_int(u.get("owned", 0), 0)),
                cost=_resources_from_mapping(u.get("cost")),
                can_build=bool(u.get("can_build", False)),
                input_name=_safe_optional_state_str(u.get("input_name")),
                status_text=_safe_state_str(u.get("status_text") or ""),
            )
            for u in _safe_state_seq(raw_p.get("ship_offers"))
            if isinstance(u, Mapping)
        )
        defense_offers = tuple(
            UnitOffer(
                unit_type=_safe_state_str(u.get("unit_type") or "defense"),
                unit_id=max(0, _safe_state_int(u.get("unit_id", 0), 0)),
                name=_safe_state_str(u.get("name") or ""),
                owned=max(0, _safe_state_int(u.get("owned", 0), 0)),
                cost=_resources_from_mapping(u.get("cost")),
                can_build=bool(u.get("can_build", False)),
                input_name=_safe_optional_state_str(u.get("input_name")),
                status_text=_safe_state_str(u.get("status_text") or ""),
            )
            for u in _safe_state_seq(raw_p.get("defense_offers"))
            if isinstance(u, Mapping)
        )
        b_raw = raw_p.get("buildings")
        bn_raw = raw_p.get("building_names")
        s_raw = raw_p.get("ships")
        d_raw = raw_p.get("defense")
        e_raw = raw_p.get("economy_rates")
        planets.append(
            PlanetState(
                planet_id=max(0, _safe_state_int(raw_p.get("planet_id", 0), 0)),
                name=_safe_state_str(raw_p.get("name") or ""),
                coords=_safe_state_str(raw_p.get("coords") or ""),
                planet_type=normalize_planet_type(raw_p),
                temperature=_safe_state_int(raw_p.get("temperature", 0), 0),
                fields_used=max(0, _safe_state_int(raw_p.get("fields_used", 0), 0)),
                fields_max=max(0, _safe_state_int(raw_p.get("fields_max", 0), 0)),
                resources=_resources_from_mapping(raw_p.get("resources")),
                storage=_resources_from_mapping(raw_p.get("storage")),
                energy=_energy_from_mapping(raw_p.get("energy")),
                buildings={game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in b_raw.items()} if isinstance(b_raw, Mapping) else {},
                building_names={game_data.safe_key(k): _safe_state_str(v) for k, v in bn_raw.items()} if isinstance(bn_raw, Mapping) else {},
                ships={game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in s_raw.items()} if isinstance(s_raw, Mapping) else {},
                defense={game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in d_raw.items()} if isinstance(d_raw, Mapping) else {},
                building_offers=offers,
                ship_offers=ship_offers,
                defense_offers=defense_offers,
                economy_rates={game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in e_raw.items()} if isinstance(e_raw, Mapping) else {},
            )
        )

    raw_tut = data.get("tutorial")
    tutorial: Optional[TutorialStatus] = None
    if isinstance(raw_tut, Mapping):
        tasks_list: list[TutorialTask] = []
        for t in _safe_state_seq(raw_tut.get("tasks")):
            if not isinstance(t, Mapping):
                continue
            t_bid = _safe_optional_state_int(t.get("building_id"))
            tasks_list.append(
                TutorialTask(
                    description=_safe_state_str(t.get("description") or ""),
                    building_name=_safe_optional_state_str(t.get("building_name")),
                    building_id=t_bid if (t_bid is not None and t_bid >= 0) else None,
                    current=max(0, _safe_state_int(t.get("current", 0), 0)),
                    target=max(0, _safe_state_int(t.get("target", 0), 0)),
                    category=_safe_state_str(t.get("category") or "building"),
                    research_id=_safe_optional_state_str(t.get("research_id")),
                )
            )
        tutorial = TutorialStatus(
            step=max(0, _safe_state_int(raw_tut.get("step", 0), 0)),
            total_steps=max(0, _safe_state_int(raw_tut.get("total_steps", 0), 0)),
            tasks=tuple(tasks_list),
            reward_text=_safe_state_str(raw_tut.get("reward_text") or ""),
            can_complete=bool(raw_tut.get("can_complete", False)),
        )

    queues_list: list[QueueJob] = []
    for q in _safe_state_seq(data.get("active_queues")):
        if not isinstance(q, Mapping):
            continue
        q_bid = _safe_optional_state_int(q.get("building_id"))
        q_tgt = _safe_optional_state_int(q.get("target_level"))
        queues_list.append(
            QueueJob(
                category=_safe_state_str(q.get("category") or ""),
                name=_safe_state_str(q.get("name") or ""),
                building_id=q_bid if (q_bid is not None and q_bid >= 0) else None,
                target_level=max(0, q_tgt) if q_tgt is not None else None,
                remaining_text=_safe_state_str(q.get("remaining_text") or ""),
            )
        )
    queues = tuple(queues_list)
    alerts = tuple(
        FleetAlert(
            mission=_safe_state_str(a.get("mission") or ""),
            hostile=bool(a.get("hostile", False)),
            summary=_safe_state_str(a.get("summary") or ""),
        )
        for a in _safe_state_seq(data.get("fleet_alerts"))
        if isinstance(a, Mapping)
    )

    res_offers_list: list[ResearchOffer] = []
    for r in _safe_state_seq(data.get("research_offers")):
        if not isinstance(r, Mapping):
            continue
        rid = game_data.cap_json_int(max(0, _safe_state_int(r.get("research_id", 0), 0)))
        cur_r = max(0, _safe_state_int(r.get("current_level", 0), 0))
        raw_rkey = _safe_optional_state_str(r.get("research_key"))
        rkey = raw_rkey if raw_rkey else (f"r{rid}" if rid else "")
        res_offers_list.append(
            ResearchOffer(
                research_id=rid,
                research_key=rkey,
                name=_safe_state_str(r.get("name") or ""),
                current_level=cur_r,
                target_level=max(0, _safe_state_int(r.get("target_level", cur_r + 1), cur_r + 1)),
                cost=_resources_from_mapping(r.get("cost")),
                can_research=bool(r.get("can_research", False)),
                status_text=_safe_state_str(r.get("status_text") or ""),
                research_href=_safe_optional_state_str(r.get("research_href")),
                active=bool(r.get("active", False)),
            )
        )
    res_offers = tuple(res_offers_list)

    raw_raid_stats = data.get("raid_stats")
    raid_stats = (
        _deep_plain_value(raw_raid_stats)
        if isinstance(raw_raid_stats, Mapping)
        else {}
    )

    r_raw = data.get("researches")
    rn_raw = data.get("research_names")
    sp_raw = data.get("specs")
    um_raw = data.get("unread_messages")
    ap_id = _safe_optional_state_int(data.get("active_planet_id"))
    acc_pts = _safe_optional_state_int(data.get("account_points"))
    acc_rank = _safe_optional_state_int(data.get("account_rank"))

    return GameState(
        universe_id=max(0, _safe_state_int(data.get("universe_id", 0), 0)),
        user_id=max(0, _safe_state_int(data.get("user_id", 0), 0)),
        nickname=_safe_state_str(data.get("nickname", "")),
        timestamp=_safe_state_float(data.get("timestamp", 0.0), 0.0),
        planets=tuple(planets),
        active_planet_id=ap_id if (ap_id is not None and ap_id >= 0) else None,
        researches={game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in r_raw.items()} if isinstance(r_raw, Mapping) else {},
        research_names={game_data.safe_key(k): _safe_state_str(v) for k, v in rn_raw.items()} if isinstance(rn_raw, Mapping) else {},
        specs={game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in sp_raw.items()} if isinstance(sp_raw, Mapping) else {},
        unread_messages={game_data.safe_key(k): max(0, _safe_state_int(v, 0)) for k, v in um_raw.items()} if isinstance(um_raw, Mapping) else {},
        tutorial=tutorial,
        active_queues=queues,
        fleet_alerts=alerts,
        research_offers=res_offers,
        speedup_factor=_safe_optional_state_float(data.get("speedup_factor")),
        newbie_protection=bool(data.get("newbie_protection", False)),
        account_points=max(0, acc_pts) if acc_pts is not None else None,
        account_rank=max(0, acc_rank) if acc_rank is not None else None,
        raid_targets=tuple(_deep_plain_value(t) for t in _safe_state_seq(data.get("raid_targets")) if isinstance(t, Mapping)),
        raid_stats=raid_stats,
    )
