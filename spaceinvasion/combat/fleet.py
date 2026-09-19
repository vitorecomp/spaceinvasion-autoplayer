"""M5-M6 fleet safety, combat estimation, espionage, Spacia raid, and colony helpers."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import os
import re
import time
from typing import Any, Collection, Deque, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from spaceinvasion.core import game_data
from spaceinvasion.core.logging_utils import redact_sensitive
from spaceinvasion.core.state import PlanetState, Resources, parse_si_int

# Map `#json-container` / form ship keys (`s1`..) to wiki slugs
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
class EscapeFlightPlan:
    """Simulated or executable fleet-save dispatch maximizing saved resource value."""

    planet_id: int
    planet_coords: str
    target_coords: str
    ships: dict[str, int]
    cargo: Resources
    total_capacity: int
    fuel_reserved_spice: int
    duration_hint: str
    feasible: bool
    reason: str


@dataclass(frozen=True)
class GalaxySlot:
    """Single planet coordinate row parsed from the Galaxy map."""

    coords: str
    position: int
    name: str = ""
    owner: str = ""
    status_color: str = "free"  # free, yellow, light_red, dark_red, blue, green, white, purple
    has_debris: bool = False
    is_spacia: bool = False
    spy_href: Optional[str] = None
    has_asteroid: bool = False
    attack_href: Optional[str] = None
    colonize_min_fields: Optional[int] = None
    colonize_max_fields: Optional[int] = None
    uninhabited_fields: Optional[int] = None
    temperature: Optional[int] = None
    debris_pig_iron: int = 0
    debris_metal: int = 0
    debris_kryptonite: int = 0
    debris_spice: int = 0


@dataclass(frozen=True)
class SpyReport:
    """Structured espionage or uninhabited-planet ecology report."""

    target_coords: str
    owner: str = ""
    resources: Resources = Resources()
    fleet: dict[str, int] = field(default_factory=dict)
    defense: dict[str, int] = field(default_factory=dict)
    dug_in: Optional[bool] = None
    uninhabited_fields: Optional[int] = None
    is_spacia: bool = False
    buildings: dict[str, int] = field(default_factory=dict)
    researches: dict[str, int] = field(default_factory=dict)
    planet_name: str = ""
    server_msg_id: str = ""
    msg_date_text: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class InboxMessage:
    """Single archived SpaceInvasion `internalMessages` entry (any folder/category)."""

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
    parsed_data: dict[str, Any] = field(default_factory=dict)
    spy_report: Optional[SpyReport] = None
    body_complete: bool = True


@dataclass(frozen=True)
class CombatEstimate:
    """Zero-rapidfire SpaceInvasion battle power and plunder feasibility estimate."""

    attacker_power: float
    defender_power: float
    power_ratio: float
    stealth_first_strike_power: float
    expected_loot: Resources
    loot_spice_equivalent: float
    cargo_capacity: int
    safe_to_attack: bool
    reason: str


@dataclass(frozen=True)
class RaidRecommendation:
    """Scored farm / Spacia / debris target candidate."""

    target_coords: str
    target_owner: str
    mission: str  # ATTACK | RECYCLE
    distance_score: float
    loot_spice_equivalent: float
    combat: CombatEstimate
    is_spacia: bool = False
    is_inactive: bool = False
    total_plunder_resources: int = 0
    flight_seconds: float = 0.0
    resources_per_hour: float = 0.0


_COORDS_NORM_RE = re.compile(
    r"^\s*\[?\s*(\d{1,2})\s*[:/\-]\s*(\d{1,3})\s*[:/\-]\s*(\d{1,2})\s*\]?\s*$",
    re.ASCII,
)


def normalize_coords(coords: Any) -> str:
    """Normalize SpaceInvasion coordinates like `'2:380:4'` to `'02:380:04'`."""
    if type(coords) is str:
        if (
            len(coords) == 9
            and coords[2] == ":"
            and coords[6] == ":"
            and coords[0:2].isascii()
            and coords[0:2].isdigit()
            and coords[3:6].isascii()
            and coords[3:6].isdigit()
            and coords[7:9].isascii()
            and coords[7:9].isdigit()
        ):
            return coords
        raw = coords.strip()
    else:
        raw = game_data.safe_key(coords).strip() if coords is not None else ""
    m = _COORDS_NORM_RE.match(raw)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):03d}:{int(m.group(3)):02d}"
    return raw


def _safe_fleet_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii():
            return default
        value = txt
    try:
        return int(value)
    except Exception:
        try:
            f = float(value)
            return int(f) if math.isfinite(f) else default
        except Exception:
            return default


def fleet_cargo_capacity(ships: Mapping[str, int], *, miner_spec_level: int = 0) -> int:
    """Return total fleet cargo capacity in units across all non-zero ships."""
    total = 0
    if not isinstance(ships, Mapping):
        return 0
    for key, count in ships.items():
        cnt = _safe_fleet_int(count, 0)
        if cnt <= 0:
            continue
        key_s = game_data.safe_key(key)
        slug = SHIP_SLUG_BY_KEY.get(key_s, key_s)
        info = game_data.resolve_unit("ships", slug)
        if not info:
            continue
        cargo = _safe_fleet_int((info.get("stats") or {}).get("cargo", 0), 0)
        total += cnt * cargo
    spec_lvl = min(100_000, max(0, _safe_fleet_int(miner_spec_level, 0)))
    if spec_lvl > 0:
        try:
            total = int(total * (1.0 + 0.05 * spec_lvl))
        except OverflowError:
            total = (total * (100 + 5 * spec_lvl)) // 100
    return total


def plan_escape_flight(
    planet: PlanetState,
    *,
    target_coords: str,
    fuel_reserve_spice: int = 500,
    duration_hint: str = "next_login",
    save_solar_cells: bool = False,
) -> EscapeFlightPlan:
    """Pack saved resources in optimal value density: Spice -> Metal -> Kryptonite -> Pig iron."""
    fuel_reserve = max(0, _safe_fleet_int(fuel_reserve_spice, 0))
    p_coords_s = game_data.safe_key(planet.coords)
    t_coords_s = game_data.safe_key(target_coords)
    dur_hint_s = game_data.safe_key(duration_hint)
    ships_to_save: dict[str, int] = {}
    for key, count in planet.ships.items():
        cnt = _safe_fleet_int(count, 0)
        if cnt <= 0:
            continue
        # Solar cells (`s9`) are stationary orbital units unless explicitly moved
        key_s = game_data.safe_key(key)
        if key_s == "s9" and not save_solar_cells:
            continue
        ships_to_save[key_s] = cnt

    cap = fleet_cargo_capacity(ships_to_save)
    if not ships_to_save or cap <= 0:
        return EscapeFlightPlan(
            planet_id=planet.planet_id,
            planet_coords=p_coords_s,
            target_coords=t_coords_s,
            ships={},
            cargo=Resources(),
            total_capacity=0,
            fuel_reserved_spice=fuel_reserve,
            duration_hint=dur_hint_s,
            feasible=False,
            reason="No mobile ships with cargo capacity stationed on planet",
        )

    res_spice = _safe_fleet_int(getattr(planet.resources, "spice", 0), 0)
    res_metal = max(0, _safe_fleet_int(getattr(planet.resources, "metal", 0), 0))
    res_kryp = max(0, _safe_fleet_int(getattr(planet.resources, "kryptonite", 0), 0))
    res_pig = max(0, _safe_fleet_int(getattr(planet.resources, "pig_iron", 0), 0))
    avail_spice = max(0, res_spice - fuel_reserve)
    if res_spice < min(fuel_reserve, 50) and cap > 0:
        return EscapeFlightPlan(
            planet_id=planet.planet_id,
            planet_coords=p_coords_s,
            target_coords=t_coords_s,
            ships=ships_to_save,
            cargo=Resources(),
            total_capacity=cap,
            fuel_reserved_spice=fuel_reserve,
            duration_hint=dur_hint_s,
            feasible=False,
            reason=f"Insufficient Spice for escape-flight fuel ({game_data.fmt_compact_int(res_spice)} available)",
        )

    rem = max(0, cap - fuel_reserve)
    take_spice = min(avail_spice, rem)
    rem -= take_spice
    take_metal = min(res_metal, rem)
    rem -= take_metal
    take_kryp = min(res_kryp, rem)
    rem -= take_kryp
    take_pig = min(res_pig, rem)

    cargo = Resources(
        pig_iron=take_pig,
        metal=take_metal,
        kryptonite=take_kryp,
        spice=take_spice,
    )
    total_ship_cnt = sum(ships_to_save.values())
    ship_cnt_str = game_data.fmt_compact_int(total_ship_cnt)
    return EscapeFlightPlan(
        planet_id=planet.planet_id,
        planet_coords=p_coords_s,
        target_coords=t_coords_s,
        ships=ships_to_save,
        cargo=cargo,
        total_capacity=cap,
        fuel_reserved_spice=fuel_reserve,
        duration_hint=dur_hint_s,
        feasible=True,
        reason=f"Packed {cargo.format_short()} into {ship_cnt_str} ships",
    )


def _unit_combat_score(
    category: str,
    counts: Mapping[str, int],
    *,
    atk_lvl: int = 0,
    shd_lvl: int = 0,
    def_lvl: int = 0,
) -> tuple[float, float]:
    """Return `(weighted_power, stealth_strike)` respecting +10%/lvl techs & zero rapidfire."""
    if not isinstance(counts, Mapping):
        return 0.0, 0.0
    key_map = SHIP_SLUG_BY_KEY if category == "ships" else DEFENSE_SLUG_BY_KEY
    atk_mult = 1.0 + 0.10 * min(100_000, max(0, _safe_fleet_int(atk_lvl, 0)))
    shd_mult = 1.0 + 0.10 * min(100_000, max(0, _safe_fleet_int(shd_lvl, 0)))
    def_mult = 1.0 + 0.10 * min(100_000, max(0, _safe_fleet_int(def_lvl, 0)))

    total_power = 0.0
    stealth_power = 0.0
    for key, cnt in counts.items():
        n = min(10**100, _safe_fleet_int(cnt, 0))
        if n <= 0:
            continue
        key_s = game_data.safe_key(key)
        slug = key_map.get(key_s, key_s)
        info = game_data.resolve_unit(category, slug)
        if not info:
            continue
        stats = info.get("stats") or {}
        atk = float(stats.get("attack", 0) or 0) * atk_mult
        shd = float(stats.get("shield", 0) or 0) * shd_mult
        hull = float(stats.get("hull", 0) or 0) * def_mult
        # Balanced effective power blending firepower with shield+hull survivability
        unit_score = n * (atk + (shd * 0.5) + (hull * 0.04))
        total_power += unit_score
        if slug == "stealth-bomber":
            stealth_power += n * atk
    return total_power, stealth_power


def simulate_raid_outcome(
    attacker_ships: Mapping[str, int],
    report: SpyReport,
    *,
    attacker_researches: Optional[Mapping[str, int]] = None,
    defender_researches: Optional[Mapping[str, int]] = None,
    safety_margin: float = 1.4,
    plunder_ratio: float = 0.5,
) -> CombatEstimate:
    """Evaluate expected combat margin and cargo-capped loot against a spy report."""
    res_map = attacker_researches if isinstance(attacker_researches, Mapping) else {}
    atk_tech = max(0, _safe_fleet_int(res_map.get("r8", 0), 0))
    shd_tech = max(0, _safe_fleet_int(res_map.get("r9", 0), 0))
    def_tech = max(0, _safe_fleet_int(res_map.get("r10", 0), 0))

    def_res_map = defender_researches if isinstance(defender_researches, Mapping) else {}
    d_atk_tech = max(0, _safe_fleet_int(def_res_map.get("r8", 0), 0))
    d_shd_tech = max(0, _safe_fleet_int(def_res_map.get("r9", 0), 0))
    d_def_tech = max(0, _safe_fleet_int(def_res_map.get("r10", 0), 0))

    atk_power, stealth_strike = _unit_combat_score(
        "ships",
        attacker_ships,
        atk_lvl=atk_tech,
        shd_lvl=shd_tech,
        def_lvl=def_tech,
    )
    def_ship_power, _ = _unit_combat_score(
        "ships",
        report.fleet,
        atk_lvl=d_atk_tech,
        shd_lvl=d_shd_tech,
        def_lvl=d_def_tech,
    )
    def_turret_power, _ = (
        (0.0, 0.0)
        if report.dug_in
        else _unit_combat_score(
            "defense",
            report.defense,
            atk_lvl=d_atk_tech,
            shd_lvl=d_shd_tech,
            def_lvl=d_def_tech,
        )
    )
    # Stealth bombers hit non-dug-in defenses before round 1
    reduced_turret_power = max(0.0, def_turret_power - stealth_strike * 0.5)
    def_power = def_ship_power + reduced_turret_power

    try:
        p_ratio = float(plunder_ratio)
    except Exception:
        p_ratio = 0.0
    if not math.isfinite(p_ratio):
        p_ratio = 0.0
    p_ratio = max(0.0, min(1.0, p_ratio))

    try:
        s_margin = float(safety_margin)
    except Exception:
        s_margin = 1.4
    if not math.isfinite(s_margin) or s_margin < 0.0:
        s_margin = 1.4

    ratio = (atk_power / def_power) if def_power > 0 else (999.0 if atk_power > 0 else 0.0)
    cap = fleet_cargo_capacity(attacker_ships)

    def _plunder_cap(raw_amt: Any) -> int:
        amt = max(0, _safe_fleet_int(raw_amt, 0))
        if amt <= 0 or p_ratio <= 0.0:
            return 0
        try:
            return max(0, int(amt * p_ratio))
        except OverflowError:
            return max(0, (amt * int(round(p_ratio * 10_000))) // 10_000)

    # Plunder up to `plunder_ratio` (default 50%) capped by cargo capacity, highest value first
    rep_res = getattr(report, "resources", None)
    max_pig = _plunder_cap(getattr(rep_res, "pig_iron", 0))
    max_met = _plunder_cap(getattr(rep_res, "metal", 0))
    max_kry = _plunder_cap(getattr(rep_res, "kryptonite", 0))
    max_spi = _plunder_cap(getattr(rep_res, "spice", 0))

    rem = cap
    loot_spi = min(max_spi, rem)
    rem -= loot_spi
    loot_met = min(max_met, rem)
    rem -= loot_met
    loot_kry = min(max_kry, rem)
    rem -= loot_kry
    loot_pig = min(max_pig, rem)

    loot = Resources(pig_iron=loot_pig, metal=loot_met, kryptonite=loot_kry, spice=loot_spi)
    loot_sp_eq = game_data.spice_equivalent(loot)
    safe = atk_power > 0 and cap > 0 and ratio >= s_margin

    if atk_power <= 0 or cap <= 0:
        reason = "No combat/cargo fleet provided"
    elif ratio < s_margin:
        reason = f"Safety margin too low ({ratio:.2f}x < {s_margin:.2f}x required for battle luck)"
    else:
        reason = f"Safe margin {ratio:.2f}x >= {s_margin:.2f}x; est loot {loot_sp_eq:.0f} Spice-eq"

    return CombatEstimate(
        attacker_power=atk_power,
        defender_power=def_power,
        power_ratio=ratio,
        stealth_first_strike_power=stealth_strike,
        expected_loot=loot,
        loot_spice_equivalent=loot_sp_eq,
        cargo_capacity=cap,
        safe_to_attack=safe,
        reason=reason,
    )


class BashTracker:
    """Enforce SpaceInvasion's 3-attacks-per-24h bash cap and server-enforced target blocks."""

    def __init__(self, *, max_active_attacks_24h: int = 3, war_max_24h: int = 5) -> None:
        self.max_active_attacks_24h = max_active_attacks_24h
        self.war_max_24h = war_max_24h
        self._history: dict[str, Deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._block_reasons: dict[str, str] = {}

    @staticmethod
    def _norm_coords(target_coords: str) -> str:
        return normalize_coords(target_coords)

    @staticmethod
    def _resolve_ts(timestamp: Optional[float]) -> float:
        if timestamp is None:
            return time.time()
        try:
            ts = float(timestamp)
        except Exception:
            return time.time()
        return ts if math.isfinite(ts) else time.time()

    def _prune(self, coords: str, now: float) -> None:
        dq = self._history[coords]
        cutoff = now - 86400.0
        while dq and dq[0] < cutoff:
            dq.popleft()

    def record_attack(self, target_coords: str, *, timestamp: Optional[float] = None) -> None:
        now = self._resolve_ts(timestamp)
        key = self._norm_coords(target_coords)
        self._prune(key, now)
        self._history[key].append(now)

    def record_block(
        self,
        target_coords: str,
        blocked_until_ts: float,
        *,
        reason: str = "",
    ) -> None:
        key = self._norm_coords(target_coords)
        if not key:
            return
        try:
            until = float(blocked_until_ts)
        except Exception:
            return
        if not math.isfinite(until) or until <= 0.0:
            return
        prev = self._blocked_until.get(key, 0.0)
        if until >= prev:
            self._blocked_until[key] = until
            if reason:
                self._block_reasons[key] = game_data.safe_key(reason).strip()

    def blocked_until(self, target_coords: str, *, timestamp: Optional[float] = None) -> float:
        now = self._resolve_ts(timestamp)
        key = self._norm_coords(target_coords)
        until = self._blocked_until.get(key, 0.0)
        if until <= now:
            if key in self._blocked_until:
                self._blocked_until.pop(key, None)
                self._block_reasons.pop(key, None)
            return 0.0
        return until

    def block_reason(self, target_coords: str, *, timestamp: Optional[float] = None) -> str:
        if self.blocked_until(target_coords, timestamp=timestamp) <= 0.0:
            return ""
        return self._block_reasons.get(self._norm_coords(target_coords), "")

    def attacks_last_24h(self, target_coords: str, *, timestamp: Optional[float] = None) -> int:
        now = self._resolve_ts(timestamp)
        key = self._norm_coords(target_coords)
        self._prune(key, now)
        return len(self._history[key])

    def can_attack(
        self,
        target_coords: str,
        *,
        is_inactive: bool = False,
        is_spacia: bool = False,
        in_declared_war: bool = False,
        timestamp: Optional[float] = None,
    ) -> bool:
        if self.blocked_until(target_coords, timestamp=timestamp) > 0.0:
            return False
        if is_inactive or is_spacia:
            return True
        cap = self.war_max_24h if in_declared_war else self.max_active_attacks_24h
        return self.attacks_last_24h(target_coords, timestamp=timestamp) < cap


_MISSION_FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "ATTACK": re.compile(
        r"\b(?:Attack\s*\(\s*forbidden\s*\)|Angriff\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "SPY": re.compile(
        r"\b(?:(?:Spying|Espionage|Spy)\s*\(\s*forbidden\s*\)|Spionage\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "RECYCLE": re.compile(
        r"\b(?:(?:Recycl(?:e|ing)|Harvest|Rubble(?:\s+field)?|Debris(?:\s+field)?)\s*\(\s*forbidden\s*\)|(?:Abbau|Recycl(?:e|ing)|Tr[üu]mmerfeld)\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "ESCAPE": re.compile(
        r"\b(?:(?:Escape(?:\s+flight)?|Save)\s*\(\s*forbidden\s*\)|Flucht(?:flug)?\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "COLONIZE": re.compile(
        r"\b(?:(?:Colonize|Colonisation|Colonization)\s*\(\s*forbidden\s*\)|Kolonisierung\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "STATION": re.compile(
        r"\b(?:(?:Station(?:ing)?|Deploy(?:ment)?)\s*\(\s*forbidden\s*\)|Stationier(?:en|ung)\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
}


_STATION_SHIP_TOKEN_RE = re.compile(r"\b(\d{1,9})\s*x\s*(s\d{1,3})\b", re.IGNORECASE | re.ASCII)


def parse_station_ships_spec(text: str) -> dict[str, int]:
    """Extract `{ship_key: qty}` pairs from a formatted `STATION` label (e.g. `[15x s11, 6x s4]`)."""
    out: dict[str, int] = {}
    for m in _STATION_SHIP_TOKEN_RE.finditer(str(text or "")):
        qty = max(0, game_data.safe_int(m.group(1), 0))
        skey = m.group(2).lower()
        if qty > 0 and skey in SHIP_SLUG_BY_KEY and skey != "s9":
            out[skey] = out.get(skey, 0) + qty
    return out


def format_station_ships_spec(ships: Mapping[str, int]) -> str:
    """Format `{ship_key: qty}` into canonical `[Nx s11, Mx s4]` order."""
    parts = [
        f"{game_data.fmt_compact_int(int(qty))}x {skey}"
        for skey, qty in sorted(
            ships.items(),
            key=lambda kv: (int(kv[0][1:]) if kv[0].startswith("s") and kv[0][1:].isdigit() else 999, kv[0]),
        )
        if int(qty) > 0 and skey in SHIP_SLUG_BY_KEY and skey != "s9"
    ]
    return ", ".join(parts)
_BASH_RESET_TIMER_RE = re.compile(
    r"(?:Reset\s+in|Zur[üu]cksetzen\s+in)\s+(\d{1,3}):(\d{2}):(\d{2})",
    re.IGNORECASE,
)


_PLAYER_PROTECTION_BLOCK_RE = re.compile(
    r"(?:"
    r"this\s+(?:user|player)\s+can(?:'t|\s*not)\s+be\s+(?:attacked|spied)"
    r"|(?:user|player)\s+can(?:'t|\s*not)\s+be\s+(?:attacked|spied)"
    r"|dieser\s+spieler\s+kann\s+nicht\s+(?:angegriffen|ausspioniert)\s+werden"
    r"|spieler\s+kann\s+nicht\s+(?:angegriffen|ausspioniert)\s+werden"
    r"|target\s+is\s+in\s+(?:vacation\s+mode|urlaubsmodus|newbie\s+protection|noob\s+protection)"
    r"|player\s+is\s+in\s+(?:vacation\s+mode|urlaubsmodus|newbie\s+protection|noob\s+protection)"
    r"|(?:out\s+of|outside)\s+(?:your\s+)?attack\s*zone(?:\s+range)?"
    r"|au[ßs]erhalb\s+(?:deiner\s+|ihrer\s+)?angriffszone"
    r")!?",
    re.IGNORECASE,
)


def parse_send_fleet_mission_block(
    html: str,
    mission_kind: str,
) -> Optional[tuple[str, float, bool]]:
    """Parse mission-specific `(forbidden)`, player-protection, or bash-protection banners.

    Returns `(reason, cooldown_seconds, is_bash_block)` or `None` when the requested
    `mission_kind` is not explicitly forbidden (e.g., ignores benign `Transport (forbidden)`
    notices on inactive targets when launching `ATTACK`, `SPY`, `RECYCLE`, or `ESCAPE`).
    """
    if not isinstance(html, str) or not html.strip():
        return None
    mk = game_data.safe_key(mission_kind).strip().upper()
    pat = _MISSION_FORBIDDEN_PATTERNS.get(mk)
    if pat is None:
        return None

    soup = BeautifulSoup(html, "html.parser")
    lines = [
        " ".join(raw_ln.split()).strip()
        for raw_ln in soup.get_text("\n", strip=True).splitlines()
        if raw_ln and raw_ln.strip()
    ]
    joined_lines = "\n".join(lines)
    m_forb = pat.search(joined_lines)
    if m_forb is None and mk in ("ATTACK", "SPY"):
        m_prot = _PLAYER_PROTECTION_BLOCK_RE.search(joined_lines)
        if m_prot is not None:
            prot_msg = " ".join(m_prot.group(0).split()).strip()
            return (prot_msg[:240], 21600.0, False)
    if m_forb is None and mk == "RECYCLE":
        low_all = joined_lines.lower()
        for phrase in (
            "no rubble field",
            "no debris field",
            "kein trümmerfeld",
            "kein truemmerfeld",
        ):
            if phrase in low_all:
                return (f"Recycle (forbidden): {phrase}", 1800.0, False)
    if m_forb is None:
        return None

    full_match = " ".join(m_forb.group(0).split()).strip()
    m_reset = _BASH_RESET_TIMER_RE.search(joined_lines)
    reset_seconds: Optional[float] = None
    reset_label = ""
    if m_reset is not None:
        hh = int(m_reset.group(1))
        mm = int(m_reset.group(2))
        ss = int(m_reset.group(3))
        reset_seconds = float(hh * 3600 + mm * 60 + ss) + 30.0
        reset_label = f"{hh:02d}:{mm:02d}:{ss:02d}"

    low_match = full_match.lower()
    low_page = joined_lines.lower()
    is_bash = mk == "ATTACK" and (
        "bash" in low_match
        or "too often" in low_match
        or "zu oft" in low_match
        or ("attack-bash-regulations" in low_page and m_reset is not None)
    )
    if reset_seconds is not None and reset_seconds > 0.0:
        cooldown_s = min(7 * 86400.0, max(60.0, reset_seconds))
    elif is_bash:
        cooldown_s = 86400.0
    elif any(
        tok in low_match
        for tok in (
            "vacation",
            "urlaub",
            "urlaubsmodus",
            "newbie",
            "noob",
            "spectral",
            "attackzone",
            "attack zone",
            "angriffszone",
            "zone",
            "strong",
            "weak",
            "can't be attacked",
            "cannot be attacked",
        )
    ):
        cooldown_s = 21600.0
    elif mk == "RECYCLE":
        cooldown_s = 1800.0
    else:
        cooldown_s = 3600.0

    reason_out = f"{full_match} (Reset in {reset_label})" if reset_label else full_match
    return (reason_out[:240], cooldown_s, is_bash)


def _parse_coords_distance(a: Any, b: Any) -> float:
    sa = game_data.safe_key(a) if a is not None else ""
    sb = game_data.safe_key(b) if b is not None else ""
    pa = [min(999_999, int(x[:9])) for x in re.findall(r"\d+", sa, re.ASCII)[:3]]
    pb = [min(999_999, int(x[:9])) for x in re.findall(r"\d+", sb, re.ASCII)[:3]]
    if len(pa) < 3 or len(pb) < 3:
        return 100.0
    if pa[0] != pb[0]:
        return 20000.0 * abs(pa[0] - pb[0])
    if pa[1] != pb[1]:
        return 2700.0 + 95.0 * abs(pa[1] - pb[1])
    return 1000.0 + 5.0 * abs(pa[2] - pb[2])


ENGINE_RESEARCH_BY_NAME: dict[str, str] = {
    "rocket engine": "r4",
    "diffusion engine": "r5",
    "warp engine": "r6",
}

DEFAULT_UNKNOWN_DEBRIS_RESOURCES: int = 25_000


def ship_effective_speed(
    ship_key: Any,
    researches: Optional[Mapping[str, Any]] = None,
) -> float:
    """Return effective SpaceInvasion ship speed (`base_speed * (1 + 0.10 * engine_level)`)."""
    key_s = game_data.safe_key(ship_key)
    slug = SHIP_SLUG_BY_KEY.get(key_s, key_s)
    info = game_data.resolve_unit("ships", slug)
    if not info:
        return 0.0
    stats = info.get("stats") or {}
    base_speed = float(max(0, _safe_fleet_int(stats.get("speed", 0), 0)))
    if base_speed <= 0.0:
        return 0.0
    eng_name = str(stats.get("engine") or "").strip().lower()
    r_key = ENGINE_RESEARCH_BY_NAME.get(eng_name)
    eng_lvl = 0
    if r_key and isinstance(researches, Mapping):
        eng_lvl = min(10_000, max(0, _safe_fleet_int(researches.get(r_key, 0), 0)))
    return base_speed * (1.0 + 0.10 * eng_lvl)


def fleet_effective_speed(
    ships: Mapping[str, int],
    researches: Optional[Mapping[str, Any]] = None,
) -> float:
    """Return the slowest mobile ship's effective speed in a multi-ship fleet."""
    if not isinstance(ships, Mapping):
        return 0.0
    slowest: Optional[float] = None
    for key, count in ships.items():
        if _safe_fleet_int(count, 0) <= 0:
            continue
        key_s = game_data.safe_key(key)
        if key_s in ("s9", "solar-cells"):
            continue
        spd = ship_effective_speed(key_s, researches)
        if spd > 0.0 and (slowest is None or spd < slowest):
            slowest = spd
    return float(slowest or 0.0)


def _flight_distance_units(origin_coords: Any, target_coords: Any) -> float:
    """SpaceInvasion Classic linear flight distance calibrated to live RETRO timings."""
    sa = game_data.safe_key(origin_coords) if origin_coords is not None else ""
    sb = game_data.safe_key(target_coords) if target_coords is not None else ""
    pa = [min(999_999, int(x[:9])) for x in re.findall(r"\d+", sa, re.ASCII)[:3]]
    pb = [min(999_999, int(x[:9])) for x in re.findall(r"\d+", sb, re.ASCII)[:3]]
    if len(pa) < 3 or len(pb) < 3:
        return 1000.0
    dg = abs(pa[0] - pb[0])
    if dg > 0:
        return 20_000.0 * dg
    ds = abs(pa[1] - pb[1])
    if ds > 0:
        return 1000.0 + 175.0 * ds
    dp = abs(pa[2] - pb[2])
    if dp > 0:
        return 1000.0 + 5.0 * dp
    return 500.0


def estimate_fleet_flight_seconds(
    origin_coords: Any,
    target_coords: Any,
    ships: Mapping[str, int],
    *,
    researches: Optional[Mapping[str, Any]] = None,
    round_trip: bool = True,
) -> float:
    """Estimate SpaceInvasion Classic flight duration in seconds (round-trip by default)."""
    speed = fleet_effective_speed(ships, researches)
    if speed <= 0.0:
        speed = 3000.0  # Safe fallback (Small transport base speed)
    dist = _flight_distance_units(origin_coords, target_coords)
    one_way_s = 10.0 + (4765.0 * dist) / max(1.0, speed)
    total_s = (2.0 * one_way_s) if round_trip else one_way_s
    return round(max(1.0, total_s), 2)


def estimate_resource_per_hour(total_resources: Any, flight_seconds: Any) -> float:
    """Return expected resource throughput per hour (`total_resources / (flight_seconds / 3600)`)."""
    try:
        res_f = float(total_resources)
        sec_f = float(flight_seconds)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not (math.isfinite(res_f) and math.isfinite(sec_f)) or res_f <= 0.0 or sec_f <= 0.0:
        return 0.0
    hours = max(sec_f / 3600.0, 1.0 / 3600.0)
    val = res_f / hours
    return round(val, 2) if math.isfinite(val) else 0.0


def format_flight_duration(seconds: Any) -> str:
    """Format a flight duration in seconds into a compact human string (`48m`, `1h 12m`)."""
    try:
        sec_f = float(seconds)
    except (TypeError, ValueError, OverflowError):
        return "0m"
    if not math.isfinite(sec_f) or sec_f <= 0.0:
        return "0m"
    sec_i = int(round(sec_f))
    if sec_i < 60:
        return f"{sec_i}s"
    mins_total = (sec_i + 30) // 60
    if mins_total < 60:
        return f"{mins_total}m"
    hours, mins = divmod(mins_total, 60)
    if hours < 24:
        return f"{hours}h {mins:02d}m" if mins else f"{hours}h"
    days, rem_h = divmod(hours, 24)
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


def extract_target_debris_resources(raw_target: Mapping[str, Any]) -> int:
    """Extract known total debris resources (Pig iron + Metal + Kryptonite + Spice) from a target dict."""
    if not isinstance(raw_target, Mapping):
        return 0
    explicit_total = max(0, _safe_fleet_int(raw_target.get("debris_total", 0), 0))
    if explicit_total > 0:
        return explicit_total
    deb_map = raw_target.get("debris")
    if isinstance(deb_map, Mapping):
        map_total = sum(
            max(0, _safe_fleet_int(deb_map.get(k, 0), 0))
            for k in ("pig_iron", "metal", "kryptonite", "spice")
        )
        if map_total > 0:
            return map_total
    avail_map = raw_target.get("debris_available")
    if isinstance(avail_map, Mapping):
        avail_total = sum(
            max(0, _safe_fleet_int(avail_map.get(k, 0), 0))
            for k in ("pig_iron", "metal", "kryptonite", "spice")
        )
        if avail_total > 0:
            return avail_total
    flat_total = (
        max(0, _safe_fleet_int(raw_target.get("debris_pig_iron", 0), 0))
        + max(0, _safe_fleet_int(raw_target.get("debris_metal", 0), 0))
        + max(0, _safe_fleet_int(raw_target.get("debris_kryptonite", 0), 0))
        + max(0, _safe_fleet_int(raw_target.get("debris_spice", 0), 0))
    )
    return flat_total


def select_transport_ships(
    stationed_ships: Mapping[str, int],
    cargo_units: int,
    *,
    miner_spec_level: int = 0,
) -> dict[str, int]:
    """Select the minimal stationed cargo ship mix (`s3` -> `s2` -> `s1`) able to carry `cargo_units`."""
    if not isinstance(stationed_ships, Mapping) or cargo_units <= 0:
        return {}
    rem = int(cargo_units)
    chosen: dict[str, int] = {}
    # Prefer largest/fastest pure cargo hulls first: Transmitter (s3, 250k) -> Large transport (s2, 30k) -> Small transport (s1, 6k)
    for skey in ("s3", "s2", "s1"):
        avail = max(0, _safe_fleet_int(stationed_ships.get(skey, 0), 0))
        if avail <= 0 or rem <= 0:
            continue
        cap_one = fleet_cargo_capacity({skey: 1}, miner_spec_level=miner_spec_level)
        if cap_one <= 0:
            continue
        need_n = min(avail, (rem + cap_one - 1) // cap_one)
        if need_n > 0:
            chosen[skey] = need_n
            rem = max(0, rem - need_n * cap_one)
    return chosen


def pack_transport_cargo(
    need: Resources,
    surplus: Resources,
    dest_free_storage: Optional[Resources],
    capacity: int,
) -> Resources:
    """Pack transport cargo satisfying `need` bounded by `surplus`, `dest_free_storage`, and `capacity`."""
    cap_left = max(0, int(capacity))
    if cap_left <= 0:
        return Resources()
    avail_by_res: dict[str, int] = {}
    for rk in ("pig_iron", "metal", "kryptonite", "spice"):
        n_v = max(0, int(getattr(need, rk, 0)))
        s_v = max(0, int(getattr(surplus, rk, 0)))
        bound = min(n_v, s_v)
        if dest_free_storage is not None:
            bound = min(bound, max(0, int(getattr(dest_free_storage, rk, 0))))
        avail_by_res[rk] = bound
    total_want = sum(avail_by_res.values())
    if total_want <= 0:
        return Resources()
    if total_want <= cap_left:
        return Resources(
            pig_iron=avail_by_res["pig_iron"],
            metal=avail_by_res["metal"],
            kryptonite=avail_by_res["kryptonite"],
            spice=avail_by_res["spice"],
        )
    # Proportionally allocate when total demand exceeds fleet cargo capacity, prioritizing rare resources for remainder
    packed = {rk: int((avail_by_res[rk] * cap_left) // total_want) for rk in avail_by_res}
    rem_cap = cap_left - sum(packed.values())
    for rk in ("spice", "metal", "kryptonite", "pig_iron"):
        if rem_cap <= 0:
            break
        room = avail_by_res[rk] - packed[rk]
        take = min(room, rem_cap)
        if take > 0:
            packed[rk] += take
            rem_cap -= take
    return Resources(
        pig_iron=packed["pig_iron"],
        metal=packed["metal"],
        kryptonite=packed["kryptonite"],
        spice=packed["spice"],
    )


_GALAXY_POS_CELL_RE = re.compile(r"^\s*(?:#\s*)?\[?\s*0?(\d{1,2})\s*\]?\.?\s*$", re.ASCII)
_GALAXY_BRACKETED_PAIR_RE = re.compile(r"\[\s*(\d{1,2})\s*[:/]\s*(\d{1,3})\s*\](?!\s*[:/]\s*\d)", re.ASCII)
_GALAXY_COORDS_TRIPLE_RE = re.compile(r"\b(\d{1,2}):(\d{1,3}):(\d{1,2})\b", re.ASCII)
_COORD_CONTEXT_PREFIX_RE = re.compile(
    r"(?:^|[\s(\[\]|,/;-])"
    r"(?:"
    r"targets?|ziels?|zielplanet(?:en)?"
    r"|planets?|planeten|asteroids?|monde?|moons?"
    r"|coords?|coordinates?|koord(?:inaten)?"
    r"|galax(?:y|ie)|solar\s*system|system|sonnensystem"
    r"|pos(?:ition)?"
    r"|location|ort|origin|destination|herkunft"
    r"|(?:espionage|spy)\s+report\s+(?:on|for)|spying\s+(?:on|for)"
    r"|spionagebericht\s+(?:auf|f(?:[üu]|ue)r|(?:[üu]|ue)ber|von)"
    r"|(?:attack|raid|probe|spy|spied|angriff|spionage)\s+(?:on|auf)"
    r"|besuchen"
    r")\s*(?:[:=\-]\s*)?\[?\s*$",
    re.IGNORECASE,
)
_GALAXY_COORD_CONTEXT_PREFIX_RE = _COORD_CONTEXT_PREFIX_RE
_GALAXY_PAIR_NOISE_PREFIX_RE = re.compile(
    r"(?:server\s*time|report\s*time|date|datum|timestamp|sent|gesendet|time|clock|uhr|zeit|timer|countdown|eta|updated|stand|fields?|felder|arrival|ankunft)\s*:?\s*[\(\[]?\s*$",
    re.IGNORECASE,
)
_GALAXY_TRIPLE_NOISE_SUFFIX_RE = re.compile(
    r"^\s*\)?\s*(?:utc|gmt|cet|cest|mez|mesz|uhr|zeit|am|pm)\b",
    re.IGNORECASE,
)
_GALAXY_PAREN_CLOCK_NOISE_RE = re.compile(
    r"\b(?:server|time|report|date|datum|timestamp|sent|gesendet|clock|uhr|zeit|timer|countdown|eta|updated|stand|utc|gmt|cet|cest|mez|mesz|am|pm)\b",
    re.IGNORECASE,
)


def _is_clock_0m_triple(g_str: str, s_str: str, p_str: str) -> bool:
    """Return True for unambiguous `H:0M:SS` / `HH:0M:SS` clock timestamps (`00..09` minute)."""
    return (
        len(g_str) in (1, 2)
        and len(s_str) == 2
        and s_str.startswith("0")
        and len(p_str) == 2
        and 0 <= int(g_str) <= 23
    )


def _is_clock_shaped_triple(g_str: str, s_str: str, p_str: str) -> bool:
    """Return True for `HH:MM:SS` two-digit triples (`00<=HH<=23`, `00<=MM<=59`)."""
    return (
        len(g_str) == 2
        and len(s_str) == 2
        and len(p_str) == 2
        and 0 <= int(g_str) <= 23
        and 0 <= int(s_str) <= 59
    )


def _is_parenthesized_noise_triple(text: str, m_start: int, m_end: int) -> bool:
    before_ch = text[m_start - 1] if m_start > 0 else ""
    after_ch = text[m_end] if m_end < len(text) else ""
    if before_ch == "(" and after_ch == ")":
        return True
    open_idx = -1
    for i in range(m_start - 1, -1, -1):
        ch = text[i]
        if ch in (")", "[", "]"):
            break
        if ch == "(":
            open_idx = i
            break
    if open_idx == -1:
        return False
    close_idx = -1
    for j in range(m_end, len(text)):
        ch = text[j]
        if ch in ("(", "[", "]"):
            break
        if ch == ")":
            close_idx = j
            break
    if close_idx == -1:
        return False
    interior = text[open_idx + 1 : close_idx]
    if _GALAXY_PAREN_CLOCK_NOISE_RE.search(interior):
        return True
    left_in = text[open_idx + 1 : m_start].strip()
    right_in = text[m_end : close_idx].strip()
    if (not left_in and right_in.startswith(("-", "—", "|", "@"))) or (
        not right_in and left_in.endswith(("-", "—", "|", "@", ":"))
    ):
        return True
    return False


_GALAXY_INACTIVE_TAG_RE = re.compile(r"\(\s*i+\s*\)", re.IGNORECASE)
_GALAXY_YELLOW_HEX_RE = re.compile(
    r"(?<![0-9a-f])#ff0(?![0-9a-f])|(?<![0-9a-f])#(?:ffff00|c9d324|d4af37|e6c200|cfc628|f0c000)(?![0-9a-f])|rgb\(\s*255\s*,\s*255\s*,\s*0\s*\)",
    re.IGNORECASE,
)
_GALAXY_RED_DARK_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:8b0000|990000|800000)(?![0-9a-f])",
    re.IGNORECASE,
)
_GALAXY_GREEN_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:0f0|00ff00|008000|496c2a)(?![0-9a-f])|rgb\(\s*0\s*,\s*(?:255|128)\s*,\s*0\s*\)",
    re.IGNORECASE,
)
_GALAXY_BLUE_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:00f|0000ff|3399ff|347187|2b6a80|3b82a6)(?![0-9a-f])|rgb\(\s*0\s*,\s*0\s*,\s*255\s*\)",
    re.IGNORECASE,
)
_GALAXY_SRC_STATUS_RE = re.compile(
    r"(?:galaxy_blue|galaxy_white|galaxy_green|galaxy_purple|galaxy_yellow|galaxy_dark_red|galaxy_darkred|dark_red|darkred|dark-red|vacation|urlaub|newbie|noob|spectral|operator|inaktiv|inactive)",
    re.IGNORECASE,
)
_GALAXY_ATTR_BLUE_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:blue|vacation|urlaub|newbie|noob)(?![a-z0-9])",
    re.IGNORECASE,
)
_GALAXY_ATTR_WHITE_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:white(?!\s*-\s*space)|spectral)(?![a-z0-9])",
    re.IGNORECASE,
)
_GALAXY_ATTR_GREEN_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:green|alliance|ally|turquoise)(?![a-z0-9])",
    re.IGNORECASE,
)
_GALAXY_ATTR_PURPLE_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:purple|pink|operator)(?![a-z0-9])",
    re.IGNORECASE,
)
_GALAXY_ATTR_DARK_RED_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?dark[_-]?red(?![a-z0-9])",
    re.IGNORECASE,
)
_GALAXY_ATTR_YELLOW_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:yellow|inaktiv|inactive)(?![a-z0-9])|color\s*:\s*yellow(?![a-z0-9])",
    re.IGNORECASE,
)
_GALAXY_TEXT_BLUE_RE = re.compile(
    r"\b(?:vacation|urlaub|newbie|noob)\b|\(\s*[vun]\s*\)",
    re.IGNORECASE,
)
_GALAXY_TEXT_WHITE_RE = re.compile(
    r"\bspectral\b|\(\s*s\s*\)",
    re.IGNORECASE,
)
_GALAXY_TEXT_PURPLE_RE = re.compile(
    r"\boperator\b|\(\s*go\s*\)",
    re.IGNORECASE,
)
_GALAXY_TEXT_DARK_RED_RE = re.compile(
    r"\btoo\s+strong\b",
    re.IGNORECASE,
)
_GALAXY_NON_NAME_TOKEN_RE = re.compile(
    r"^(?:debris|rubble|trümmer|truemmer|recycle|spy|spio|espionage|probe|attack|angriff|colonize|message|msg|send\s+message|asteroid|\[a\]|a|-|—)$",
    re.IGNORECASE,
)
_GALAXY_DEBRIS_NUMBER_RE = re.compile(r"^[\d.,\s]+(?:k|m)?\s*(?:debris|tf|metal|pig\s*iron)?$", re.IGNORECASE | re.ASCII)


def _valid_gal(val: Any) -> Optional[int]:
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return None
    if val is None or isinstance(val, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(val, int):
        return val if 1 <= val <= 99 else None
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    try:
        s = str(val).strip()
    except Exception:
        return None
    if not s or not (s.isascii() and s.isdigit()):
        return None
    trimmed = s.lstrip("0") or "0"
    if len(trimmed) > 3:
        return None
    n = int(trimmed)
    return n if 1 <= n <= 99 else None


def _valid_sys(val: Any) -> Optional[int]:
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return None
    if val is None or isinstance(val, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(val, int):
        return val if 1 <= val <= 999 else None
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    try:
        s = str(val).strip()
    except Exception:
        return None
    if not s or not (s.isascii() and s.isdigit()):
        return None
    trimmed = s.lstrip("0") or "0"
    if len(trimmed) > 4:
        return None
    n = int(trimmed)
    return n if 1 <= n <= 999 else None


def extract_galaxy_system_coords(
    html: str,
    *,
    url: str = "",
    default: tuple[int, int] = (1, 1),
) -> tuple[int, int]:
    """Infer `(galaxy, system)` from Galaxy HTML JSON payload, inputs/selects/headers, or URL query params."""
    soup = BeautifulSoup(html or "", "html.parser")
    gal_found: Optional[int] = None
    sys_found: Optional[int] = None

    # 0. Live 2D Galaxy `#json-element` payload (`window.si.result.position`) — authoritative after AJAX
    #    `window.si.navigate(g, s)` because `<input value="...">`, inline `SI_TPL.galaxy`, and `location.href`
    #    remain frozen on the initial full-page load coordinates.
    json_el = soup.find(id="json-element")
    if json_el is not None:
        raw_jtxt = (json_el.string if json_el.string is not None else json_el.get_text("", strip=True)) or ""
        if raw_jtxt.strip():
            try:
                parsed_j = json.loads(raw_jtxt)
            except Exception:
                parsed_j = None
            if isinstance(parsed_j, Mapping):
                pos_j = parsed_j.get("position")
                src_pos = pos_j if isinstance(pos_j, Mapping) else parsed_j
                jg = _valid_gal(src_pos.get("galaxy", src_pos.get("gal")))
                js = _valid_sys(src_pos.get("system", src_pos.get("sun")))
                if jg is not None and js is not None:
                    return jg, js

    # 1. Input fields (`galaxy`/`gal`/`g`, `system`/`sun`/`s`)
    for inp in soup.find_all("input"):
        name = str(inp.get("name") or inp.get("id") or "").strip().lower()
        val = inp.get("value")
        if val is None:
            continue
        if name in ("galaxy", "gal", "g") and gal_found is None:
            gal_found = _valid_gal(val)
        elif name in ("system", "sun", "s") and sys_found is None:
            sys_found = _valid_sys(val)

    # 2. Selected <option> inside <select name="galaxy|gal|g|system|sun|s">
    if gal_found is None or sys_found is None:
        for sel in soup.find_all("select"):
            name = str(sel.get("name") or sel.get("id") or "").strip().lower()
            opt = sel.find("option", selected=True) or sel.find("option", attrs={"selected": True})
            if opt is None:
                continue
            raw_val = opt.get("value") if opt.get("value") is not None else opt.get_text(strip=True)
            if name in ("galaxy", "gal", "g") and gal_found is None:
                gal_found = _valid_gal(raw_val)
            elif name in ("system", "sun", "s") and sys_found is None:
                sys_found = _valid_sys(raw_val)

    if gal_found is not None and sys_found is not None:
        return gal_found, sys_found

    # 2b. 2D/Modern Galaxy `SI_TPL.galaxy = {..., galaxy: G, sun: S, ...}` script metadata
    if isinstance(html, str) and "SI_TPL.galaxy" in html:
        m_tpl_block = re.search(r"SI_TPL\.galaxy\s*=\s*\{([^}]*)\}", html, re.ASCII)
        tpl_scope = (
            m_tpl_block.group(1)
            if m_tpl_block is not None
            else html[html.find("SI_TPL.galaxy") : html.find("SI_TPL.galaxy") + 512]
        )
        m_tpl_g = re.search(
            r"""(?:^|[,{\s.;])["']?(?:galaxy|gal)["']?\s*[:=]\s*["']?(\d{1,2})["']?(?![0-9])""",
            tpl_scope,
            re.ASCII,
        )
        m_tpl_s = re.search(
            r"""(?:^|[,{\s.;])["']?(?:sun|system)["']?\s*[:=]\s*["']?(\d{1,3})["']?(?![0-9])""",
            tpl_scope,
            re.ASCII,
        )
        if m_tpl_g and gal_found is None:
            gal_found = _valid_gal(m_tpl_g.group(1))
        if m_tpl_s and sys_found is None:
            sys_found = _valid_sys(m_tpl_s.group(1))
        if gal_found is not None and sys_found is not None:
            return gal_found, sys_found

    # Remove own-planet switcher badges (`#planet-coords`, `#planet-list`) before text coordinate fallback
    for own_el in soup.select("#planet-coords, #planet-list"):
        own_el.decompose()

    # 3a. Header text (`Galaxy/System [GG:SSS]` or `System: [GG:SSS]` or `System GG:SSS`)
    text_parts: list[str] = []
    link_spans: list[tuple[int, int]] = []
    pos = 0
    last_a_id: Optional[int] = None
    for s in soup.strings:
        cleaned = " ".join(str(s).split())
        if not cleaned:
            continue
        a_tag = s.find_parent("a")
        a_id = id(a_tag) if a_tag is not None else None
        if text_parts:
            pos += 1
        start = pos
        end = start + len(cleaned)
        text_parts.append(cleaned)
        pos = end
        if a_id is not None:
            if link_spans and last_a_id == a_id:
                prev_start, _ = link_spans[-1]
                link_spans[-1] = (prev_start, end)
            else:
                link_spans.append((start, end))
        last_a_id = a_id
    text = " ".join(text_parts)
    m_hdr = re.search(
        r"(?:Galaxy|Solar\s*System|System|Sonnensystem)\s*(?::\s*)?\[?\s*(\d{1,2})\s*[:/]\s*(\d{1,3})(?!\d)\s*\]?(?!\s*[:/]\s*\d)",
        text,
        re.IGNORECASE | re.ASCII,
    )
    if m_hdr:
        hg = _valid_gal(m_hdr.group(1))
        hs = _valid_sys(m_hdr.group(2))
        if hg is not None and hs is not None:
            return (gal_found or hg), (sys_found or hs)

    # 3b. Explicit bracketed `[GG:SSS]` / `[GG/SSS]`
    for m_br in _GALAXY_BRACKETED_PAIR_RE.finditer(text):
        if _GALAXY_PAIR_NOISE_PREFIX_RE.search(text[: m_br.start()]):
            continue
        if _GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(text[m_br.end() :]):
            continue
        g_s, s_s = m_br.group(1), m_br.group(2)
        hg = _valid_gal(g_s)
        hs = _valid_sys(s_s)
        if hg is None or hs is None:
            continue
        is_clock_pair = (
            len(g_s) == 2 and len(s_s) == 2 and 0 <= hg <= 23 and 0 <= hs <= 59
        )
        if is_clock_pair and not _COORD_CONTEXT_PREFIX_RE.search(text[: m_br.start()]):
            continue
        return (gal_found or hg), (sys_found or hs)

    # 4. High-confidence `GG:SSS:PP` (`1 <= PP <= 16`) inside clean <a>, bracketed non-clock, 3-digit system, coord-context, or distinct-slot rows
    in_link_set: set[tuple[int, int]] = set()
    clean_triples: list[tuple[tuple[int, int], bool, bool, bool, bool, bool]] = []
    clean_triple_counts: Counter[tuple[int, int]] = Counter()
    distinct_positions: dict[tuple[int, int], set[int]] = defaultdict(set)
    non_clock_keys: set[tuple[int, int]] = set()
    for m_tr in _GALAXY_COORDS_TRIPLE_RE.finditer(text):
        g_str, s_str, p_str = m_tr.group(1), m_tr.group(2), m_tr.group(3)
        tg = _valid_gal(g_str)
        ts = _valid_sys(s_str)
        tp = int(p_str)
        if tg is None or ts is None or not (1 <= tp <= 16):
            continue
        m_start, m_end = m_tr.start(), m_tr.end()
        before_ch = text[m_start - 1] if m_start > 0 else ""
        after_ch = text[m_end] if m_end < len(text) else ""
        is_noise = bool(
            _is_parenthesized_noise_triple(text, m_start, m_end)
            or _GALAXY_PAIR_NOISE_PREFIX_RE.search(text[:m_start])
            or _GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(text[m_end:])
        )
        if is_noise:
            continue
        key = (tg, ts)
        clean_triple_counts[key] += 1
        distinct_positions[key].add(tp)
        is_in_link = any(s_a <= m_start and m_end <= e_a for s_a, e_a in link_spans)
        if is_in_link:
            in_link_set.add(key)
        is_bracketed = before_ch == "[" and after_ch == "]"
        is_three_digit_sys = len(s_str) == 3
        has_coord_context = bool(_COORD_CONTEXT_PREFIX_RE.search(text[:m_start]))
        is_clock_shape = _is_clock_shaped_triple(g_str, s_str, p_str) or _is_clock_0m_triple(
            g_str, s_str, p_str
        )
        if not is_clock_shape:
            non_clock_keys.add(key)
        clean_triples.append((key, is_in_link, is_bracketed, is_three_digit_sys, has_coord_context, is_clock_shape))

    strong_triple_counts: Counter[tuple[int, int]] = Counter()
    strong_non_clock_counts: Counter[tuple[int, int]] = Counter()
    ctx_triple_counts: Counter[tuple[int, int]] = Counter()
    for key, is_in_link, is_bracketed, is_three_digit_sys, has_coord_context, is_clock_shape in clean_triples:
        if has_coord_context:
            ctx_triple_counts[key] += 1
        if (
            is_in_link
            or key in in_link_set
            or (is_bracketed and not is_clock_shape)
            or is_three_digit_sys
            or has_coord_context
        ):
            strong_triple_counts[key] += 1
            if not is_clock_shape or key in non_clock_keys:
                strong_non_clock_counts[key] += 1

    for key, cnt in clean_triple_counts.items():
        if key not in strong_triple_counts and (
            (cnt >= 2 and key in non_clock_keys) or len(distinct_positions[key]) >= 2
        ):
            strong_triple_counts[key] = cnt
            if key in non_clock_keys:
                strong_non_clock_counts[key] = cnt

    if strong_non_clock_counts:
        (mg, ms), _ = strong_non_clock_counts.most_common(1)[0]
        return (gal_found or mg), (sys_found or ms)
    if ctx_triple_counts:
        (mg, ms), _ = ctx_triple_counts.most_common(1)[0]
        return (gal_found or mg), (sys_found or ms)
    if strong_triple_counts:
        (mg, ms), _ = strong_triple_counts.most_common(1)[0]
        return (gal_found or mg), (sys_found or ms)

    # 5. URL query parameters (`galaxy`/`gal`/`g`, `system`/`sun`/`s`)
    if url:
        qs = parse_qs(urlparse(url).query)
        if gal_found is None:
            for key in ("galaxy", "gal", "g"):
                if key in qs and qs[key]:
                    gal_found = _valid_gal(qs[key][0])
                    if gal_found is not None:
                        break
        if sys_found is None:
            for key in ("system", "sun", "s"):
                if key in qs and qs[key]:
                    sys_found = _valid_sys(qs[key][0])
                    if sys_found is not None:
                        break
        if gal_found is not None and sys_found is not None:
            return gal_found, sys_found

    return (
        gal_found if gal_found is not None else default[0],
        sys_found if sys_found is not None else default[1],
    )


def extract_galaxy_coords_from_html(
    html: str,
    url: str = "",
    *,
    default: tuple[int, int] = (1, 1),
) -> tuple[int, int]:
    """Alias for `extract_galaxy_system_coords` supporting positional `url`."""
    return extract_galaxy_system_coords(html, url=url, default=default)


def _extract_action_href_from_el(el: Any) -> str:
    href = str(el.get("href") or "").strip()
    onclick = str(el.get("onclick") or "").strip()
    if href and href not in ("#", "javascript:void(0)", "javascript:void(0);", "javascript:;"):
        return href
    if onclick:
        m_url = re.search(r"['\"]([^'\"]*indexInternal\.es[^'\"]*)['\"]", onclick, re.IGNORECASE)
        if m_url:
            return m_url.group(1)
        return onclick
    return href


def _extract_slot_attr_blobs(container: Any) -> tuple[str, str]:
    style_parts: list[str] = []
    semantic_parts: list[str] = []
    if container is None:
        return "", ""
    nodes = [container, *container.find_all(True)]
    for el in nodes:
        cls = el.get("class")
        if cls:
            if isinstance(cls, (list, tuple)):
                style_parts.extend(str(c) for c in cls if c)
            else:
                style_parts.append(str(cls))
        for attr in ("style", "color", "data-status", "data-color", "data-state"):
            val = el.get(attr)
            if val:
                style_parts.append(str(val))
        src = el.get("src")
        if src:
            for tok in _GALAXY_SRC_STATUS_RE.findall(str(src)):
                style_parts.append(tok)
        for attr in ("title", "alt"):
            val = el.get(attr)
            if val:
                semantic_parts.append(str(val))
    return " ".join(style_parts).lower(), " ".join(semantic_parts).lower()


def _classify_slot_status(
    *,
    occupied: bool,
    is_spacia: bool,
    row_html: str,
    row_text: str,
    style_attrs: Optional[str] = None,
    semantic_attrs: Optional[str] = None,
) -> str:
    if not occupied:
        return "free"
    if is_spacia:
        return "yellow"
    if style_attrs is None or semantic_attrs is None:
        frag = BeautifulSoup(row_html or "", "html.parser")
        parsed_style, parsed_semantic = _extract_slot_attr_blobs(frag)
        if style_attrs is None:
            style_attrs = parsed_style
        if semantic_attrs is None:
            semantic_attrs = parsed_semantic
    semantic_text = f"{row_text or ''} {semantic_attrs}".lower()
    if (
        _GALAXY_ATTR_BLUE_RE.search(style_attrs)
        or _GALAXY_BLUE_HEX_RE.search(style_attrs)
        or _GALAXY_TEXT_BLUE_RE.search(semantic_text)
    ):
        return "blue"
    if _GALAXY_ATTR_WHITE_RE.search(style_attrs) or _GALAXY_TEXT_WHITE_RE.search(semantic_text):
        return "white"
    if _GALAXY_ATTR_GREEN_RE.search(style_attrs) or _GALAXY_GREEN_HEX_RE.search(style_attrs):
        return "green"
    if _GALAXY_ATTR_PURPLE_RE.search(style_attrs) or _GALAXY_TEXT_PURPLE_RE.search(semantic_text):
        return "purple"
    if (
        _GALAXY_ATTR_DARK_RED_RE.search(style_attrs)
        or _GALAXY_RED_DARK_HEX_RE.search(style_attrs)
        or _GALAXY_TEXT_DARK_RED_RE.search(semantic_text)
    ):
        return "dark_red"
    if (
        _GALAXY_ATTR_YELLOW_RE.search(style_attrs)
        or _GALAXY_YELLOW_HEX_RE.search(style_attrs)
        or _GALAXY_INACTIVE_TAG_RE.search(semantic_text)
    ):
        return "yellow"
    return "light_red"


def _extract_slot_links(container: Any) -> tuple[Optional[str], Optional[str]]:
    spy_href: Optional[str] = None
    attack_href: Optional[str] = None
    for el in container.find_all(["a", "button", "span", "img"]):
        if el.name not in ("a", "button") and not el.get("onclick"):
            continue
        href = str(el.get("href") or "")
        onclick = str(el.get("onclick") or "")
        title = str(el.get("title") or "")
        txt = el.get_text(" ", strip=True)
        img_alts = " ".join(str(img.get("alt") or img.get("title") or img.get("src") or "") for img in el.find_all("img"))
        blob = f"{href} {onclick} {title} {txt} {img_alts}".lower()
        if spy_href is None and (
            any(tok in blob for tok in ("spy", "spio", "espionage", "probe", "s10", "mission=5", "typ=5"))
        ):
            val = _extract_action_href_from_el(el)
            if val:
                spy_href = val
        if attack_href is None and (
            any(tok in blob for tok in ("attack", "angriff", "mission=1", "typ=1"))
        ):
            val = _extract_action_href_from_el(el)
            if val:
                attack_href = val
    return spy_href, attack_href


def _is_non_name_cell(cell: Any, cell_text: str) -> bool:
    if not cell_text:
        return True
    cls = " ".join(str(c) for c in (cell.get("class") or ())).lower()
    if any(tok in cls for tok in ("debris", "tf", "action", "actions", "icon", "img", "thumb", "asteroid", "moon")):
        return True
    low = cell_text.strip().lower()
    if _GALAXY_NON_NAME_TOKEN_RE.match(low):
        return True
    if _GALAXY_DEBRIS_NUMBER_RE.match(low):
        return True
    # Pure action cell where all visible text comes from Spy/Attack/Colonize/Recycle/Msg buttons
    links = cell.find_all(["a", "button"])
    if links:
        link_texts = [a.get_text(" ", strip=True) for a in links if a.get_text(" ", strip=True)]
        if link_texts and all(_GALAXY_NON_NAME_TOKEN_RE.match(lt.lower()) for lt in link_texts):
            remainder = cell_text
            for lt in link_texts:
                remainder = remainder.replace(lt, " ")
            if not re.sub(r"[\s|/,;\-\[\]()]+", "", remainder):
                return True
    return False


def parse_galaxy_system_html(
    html: str,
    *,
    galaxy: Optional[int] = None,
    system: Optional[int] = None,
    url: str = "",
) -> tuple[GalaxySlot, ...]:
    """Parse galaxy system rows for status colors, Spacia flags, empty slots, and debris."""
    soup = BeautifulSoup(html or "", "html.parser")
    if galaxy is None or system is None:
        det_gal, det_sys = extract_galaxy_system_coords(html, url=url, default=(1, 1))
        gal = det_gal if galaxy is None else _safe_fleet_int(galaxy, det_gal)
        sys_num = det_sys if system is None else _safe_fleet_int(system, det_sys)
        if abs(gal) > 999_999:
            gal = det_gal
        if abs(sys_num) > 999_999:
            sys_num = det_sys
    else:
        gal = _safe_fleet_int(galaxy, 1)
        sys_num = _safe_fleet_int(system, 1)
        if abs(gal) > 999_999:
            gal = 1
        if abs(sys_num) > 999_999:
            sys_num = 1

    slots: list[GalaxySlot] = []
    seen_pos: set[int] = set()

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        pos: Optional[int] = None
        pos_idx = 0
        raw_attr_pos = tr.get("data-pos") or tr.get("data-pla")
        if raw_attr_pos is not None:
            m_attr = _GALAXY_POS_CELL_RE.match(str(raw_attr_pos))
            if m_attr:
                cand = int(m_attr.group(1))
                if 1 <= cand <= 16:
                    pos = cand
                    pos_idx = -1
        if pos is None:
            for idx in range(min(2, len(tds))):
                pos_txt = tds[idx].get_text(" ", strip=True)
                m_pos = _GALAXY_POS_CELL_RE.match(pos_txt)
                if m_pos:
                    cand = int(m_pos.group(1))
                    if 1 <= cand <= 16:
                        pos = cand
                        pos_idx = idx
                        break
                elif idx == 0 and pos_txt:
                    # First cell has non-empty non-position text -> not a slot row
                    break
        if pos is None or pos in seen_pos:
            continue
        seen_pos.add(pos)

        data_cells = tds[pos_idx + 1 :] if pos_idx >= 0 else tds
        texts: list[str] = []
        for cell in data_cells:
            txt = " ".join(cell.get_text(" ", strip=True).split())
            if _is_non_name_cell(cell, txt):
                continue
            texts.append(txt)

        planet_name = texts[0] if len(texts) >= 1 else ""
        owner = texts[1] if len(texts) >= 2 else ""

        row_html = str(tr).lower()
        row_text = " ".join(tr.get_text(" ", strip=True).split())
        spy_href, attack_href = _extract_slot_links(tr)
        has_debris = any(tok in row_html for tok in ("debris", "trümmer", "truemmer", "rubble", "recycle"))
        has_asteroid = bool(re.search(r"\[\s*a\s*\]|\basteroid\b", row_text, re.IGNORECASE) or tr.select(".asteroid, [class*='asteroid']"))
        is_spacia = "spacia" in row_text.lower() or "spacia" in row_html
        if is_spacia and planet_name and not owner:
            owner = "Spacia"

        occupied = bool(planet_name or owner or spy_href or attack_href)
        style_attrs, semantic_attrs = _extract_slot_attr_blobs(tr)
        status_color = _classify_slot_status(
            occupied=occupied,
            is_spacia=is_spacia,
            row_html=row_html,
            row_text=row_text,
            style_attrs=style_attrs,
            semantic_attrs=semantic_attrs,
        )

        coords = f"{gal:02d}:{sys_num:03d}:{pos:02d}"
        slots.append(
            GalaxySlot(
                coords=coords,
                position=pos,
                name=planet_name,
                owner=owner,
                status_color=status_color,
                has_debris=has_debris,
                is_spacia=is_spacia,
                spy_href=spy_href,
                has_asteroid=has_asteroid,
                attack_href=attack_href,
            )
        )

    # Fallback for Modern/3D div/li slot cards when no table rows matched
    if not slots:
        for card in soup.select("[data-pos], [data-pla], .galaxy-row, .galaxy-slot, .galaxy_row, li.slot"):
            card_classes = card.get("class") or ()
            if isinstance(card_classes, str):
                card_classes = card_classes.split()
            if "planet-hit" in card_classes:
                continue
            raw_pos = card.get("data-pos") or card.get("data-pla")
            pos: Optional[int] = None
            if raw_pos is not None:
                m_attr = _GALAXY_POS_CELL_RE.match(str(raw_pos))
                if m_attr:
                    cand = int(m_attr.group(1))
                    if 1 <= cand <= 16:
                        pos = cand
            if pos is None:
                pos_el = card.select_one(".pos, .position, .slot-pos, .gal-pos")
                if pos_el is not None:
                    m_pos = _GALAXY_POS_CELL_RE.match(pos_el.get_text(" ", strip=True))
                    if m_pos:
                        cand = int(m_pos.group(1))
                        if 1 <= cand <= 16:
                            pos = cand
            if pos is None or pos in seen_pos:
                continue
            seen_pos.add(pos)

            p_el = card.select_one(".planet, .planet-name, .planet_name, .slot-planet")
            o_el = card.select_one(".owner, .player, .player-name, .player_name, .slot-owner")
            planet_name = " ".join(p_el.get_text(" ", strip=True).split()) if p_el is not None else ""
            owner = " ".join(o_el.get_text(" ", strip=True).split()) if o_el is not None else ""
            if not planet_name and not owner:
                raw_parts = [
                    " ".join(ch.get_text(" ", strip=True).split())
                    for ch in card.find_all(["span", "div"], recursive=False)
                ]
                clean_parts = [
                    part
                    for part in raw_parts
                    if part and not _GALAXY_POS_CELL_RE.match(part) and not _GALAXY_NON_NAME_TOKEN_RE.match(part.lower())
                ]
                if len(clean_parts) >= 1:
                    planet_name = clean_parts[0]
                if len(clean_parts) >= 2:
                    owner = clean_parts[1]

            row_html = str(card).lower()
            row_text = " ".join(card.get_text(" ", strip=True).split())
            spy_href, attack_href = _extract_slot_links(card)
            has_debris = any(tok in row_html for tok in ("debris", "trümmer", "truemmer", "rubble", "recycle"))
            has_asteroid = bool(re.search(r"\[\s*a\s*\]|\basteroid\b", row_text, re.IGNORECASE) or card.select(".asteroid, [class*='asteroid']"))
            is_spacia = "spacia" in row_text.lower() or "spacia" in row_html
            if is_spacia and planet_name and not owner:
                owner = "Spacia"

            occupied = bool(planet_name or owner or spy_href or attack_href)
            style_attrs, semantic_attrs = _extract_slot_attr_blobs(card)
            status_color = _classify_slot_status(
                occupied=occupied,
                is_spacia=is_spacia,
                row_html=row_html,
                row_text=row_text,
                style_attrs=style_attrs,
                semantic_attrs=semantic_attrs,
            )
            coords = f"{gal:02d}:{sys_num:03d}:{pos:02d}"
            slots.append(
                GalaxySlot(
                    coords=coords,
                    position=pos,
                    name=planet_name,
                    owner=owner,
                    status_color=status_color,
                    has_debris=has_debris,
                    is_spacia=is_spacia,
                    spy_href=spy_href,
                    has_asteroid=has_asteroid,
                    attack_href=attack_href,
                )
            )

    # Fallback for SpaceInvasion Classic 2D Graphical Map (`div.planet-hit` populated via `si.getData()`)
    if not slots:
        for hit in soup.select("div.planet-hit"):
            pos: Optional[int] = None
            for div_child in reversed(hit.find_all("div")):
                if div_child.find("div") is not None:
                    continue
                pos_txt = div_child.get_text(" ", strip=True)
                if pos_txt.isascii() and pos_txt.isdigit():
                    cand = int(pos_txt)
                    if 1 <= cand <= 16:
                        pos = cand
                        break
            if pos is None or pos in seen_pos:
                continue
            seen_pos.add(pos)

            spacia_el = hit.select_one(".spacia-planet")
            b_el = hit.find("b")
            occupied = spacia_el is not None or b_el is not None
            planet_name = ""
            owner = ""
            if spacia_el is not None:
                planet_name = " ".join(spacia_el.get_text(" ", strip=True).split())
            elif b_el is not None:
                planet_name = " ".join(b_el.get_text(" ", strip=True).split())

            span_txt = ""
            if occupied:
                label_span = (spacia_el or b_el).find_parent("span") if (spacia_el or b_el) is not None else None
                span_txt = " ".join((label_span or hit).get_text(" ", strip=True).split())
                rem_txt = (
                    span_txt[len(planet_name) :].strip()
                    if (planet_name and span_txt.startswith(planet_name))
                    else span_txt
                )
                for m_own in re.finditer(r"\(([^()]+)\)", rem_txt):
                    cand_own = m_own.group(1).strip()
                    if not cand_own:
                        continue
                    if re.fullmatch(r"i+|[vuns]|go", cand_own, re.IGNORECASE):
                        continue
                    owner = cand_own
                    break

            is_spacia = spacia_el is not None or owner.lower() == "spacia"
            if is_spacia and planet_name and not owner:
                owner = "Spacia"

            spy_href, attack_href = _extract_slot_links(hit)
            rubble_el = hit.select_one(".rubble-orbit, [name='rubblefield']")
            has_debris = False
            if rubble_el is not None and not rubble_el.has_attr("hidden"):
                r_style = str(rubble_el.get("style") or "")
                has_debris = not bool(
                    re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", r_style, re.IGNORECASE)
                )
            has_asteroid = hit.select_one("[name='astro'], [id^='astro-']") is not None
            row_html = str(hit).lower()
            row_text = span_txt if occupied else ""
            style_attrs, semantic_attrs = _extract_slot_attr_blobs(hit)
            if occupied and re.search(
                r"opacity\s*:\s*0\.6(?![0-9])|0px\s+0px\s+2px\s+#fff(?:fff)?(?![0-9a-f])",
                style_attrs,
                re.IGNORECASE,
            ):
                status_color = "white"
            else:
                status_color = _classify_slot_status(
                    occupied=occupied,
                    is_spacia=is_spacia,
                    row_html=row_html,
                    row_text=row_text,
                    style_attrs=style_attrs,
                    semantic_attrs=semantic_attrs,
                )
            coords = f"{gal:02d}:{sys_num:03d}:{pos:02d}"
            slots.append(
                GalaxySlot(
                    coords=coords,
                    position=pos,
                    name=planet_name,
                    owner=owner,
                    status_color=status_color,
                    has_debris=has_debris,
                    is_spacia=is_spacia,
                    spy_href=spy_href,
                    has_asteroid=has_asteroid,
                    attack_href=attack_href,
                )
            )

    # Enrich free slots (or synthesize slots when DOM cards absent) from `#json-element` 2D Galaxy payload
    json_el = soup.find(id="json-element")
    if json_el is not None:
        raw_jtxt = (json_el.string if json_el.string is not None else json_el.get_text("", strip=True)) or ""
        parsed_j: Any = None
        if raw_jtxt.strip():
            try:
                parsed_j = json.loads(raw_jtxt)
            except Exception:
                parsed_j = None
        planets_j = parsed_j.get("planets") if isinstance(parsed_j, Mapping) else None
        pos_meta: dict[
            int,
            tuple[
                Optional[int],
                Optional[int],
                Optional[int],
                Optional[int],
                bool,
                str,
                str,
                bool,
                bool,
                int,
                int,
                int,
                int,
            ],
        ] = {}
        if isinstance(planets_j, Mapping):
            iter_items = list(planets_j.items())
        elif isinstance(planets_j, Sequence) and not isinstance(planets_j, (str, bytes)):
            iter_items = [(idx + 1, val) for idx, val in enumerate(planets_j)]
        else:
            iter_items = []
        for raw_pk, pdata in iter_items:
            if not isinstance(pdata, Mapping):
                continue
            p_pos = _safe_fleet_int(pdata.get("pla", pdata.get("position", raw_pk)), 0)
            if not (1 <= p_pos <= 16):
                continue
            uid_v = _safe_fleet_int(pdata.get("uid", pdata.get("user_id", 0)), 0)
            pname_j = str(pdata.get("name") or pdata.get("planet_name") or "").strip()
            owner_j = str(pdata.get("uname") or pdata.get("owner") or "").strip()
            is_sp_j = "spacia" in pname_j.lower() or "spacia" in owner_j.lower() or bool(pdata.get("spacia"))
            is_free_j = (uid_v == 0) and not pname_j and not owner_j and not is_sp_j
            c_min: Optional[int] = None
            c_max: Optional[int] = None
            uf_exact: Optional[int] = None
            temp_j: Optional[int] = None
            raw_temp = pdata.get("temperature")
            if raw_temp is not None and not isinstance(raw_temp, bool):
                t_parsed = game_data.safe_optional_int(raw_temp, None)
                if t_parsed is not None and -300 <= t_parsed <= 600:
                    temp_j = t_parsed
            if is_free_j:
                col_arr = pdata.get("colonize")
                if isinstance(col_arr, Sequence) and not isinstance(col_arr, (str, bytes)) and len(col_arr) >= 2:
                    v0 = _safe_fleet_int(col_arr[0], 0)
                    v1 = _safe_fleet_int(col_arr[1], 0)
                    if v0 > 0 and v1 >= v0:
                        c_min, c_max = v0, v1
                elif isinstance(col_arr, Mapping):
                    v0 = _safe_fleet_int(
                        col_arr.get("minFields", col_arr.get("min_fields", col_arr.get("min", 0))),
                        0,
                    )
                    v1 = _safe_fleet_int(
                        col_arr.get("maxFields", col_arr.get("max_fields", col_arr.get("max", 0))),
                        0,
                    )
                    if v0 > 0 and v1 >= v0:
                        c_min, c_max = v0, v1
                    vf = col_arr.get("fields", col_arr.get("uninhabited_fields"))
                    if isinstance(vf, int) and not isinstance(vf, bool) and 20 <= vf <= 1000:
                        uf_exact = vf
                elif isinstance(col_arr, int) and not isinstance(col_arr, bool) and 20 <= col_arr <= 1000:
                    uf_exact = col_arr
                raw_exact = pdata.get("uninhabited_fields", pdata.get("fields"))
                if isinstance(raw_exact, int) and not isinstance(raw_exact, bool) and 20 <= raw_exact <= 1000:
                    uf_exact = raw_exact
            # SpaceInvasion RETRO `#json-element` exposes `rubblefield1` as the authoritative live
            # `[pig_iron, metal, kryptonite, spice, flag]` array; legacy `rubblefield` can retain
            # ghost/stale amounts on completely clean slots, so NEVER fall back from `rubblefield1`!
            has_rubble_key = False
            raw_rubble: Any = None
            if "rubblefield1" in pdata:
                has_rubble_key = True
                raw_rubble = pdata.get("rubblefield1")
            else:
                for rk in ("rubblefield", "rubble", "debris", "tf"):
                    if rk in pdata:
                        has_rubble_key = True
                        raw_rubble = pdata.get(rk)
                        break
            d_pig = d_met = d_kry = d_spi = 0
            if isinstance(raw_rubble, Sequence) and not isinstance(raw_rubble, (str, bytes)):
                vals4 = [max(0, _safe_fleet_int(x, 0)) for x in raw_rubble[:4]]
                while len(vals4) < 4:
                    vals4.append(0)
                d_pig, d_met, d_kry, d_spi = vals4[0], vals4[1], vals4[2], vals4[3]
                has_deb_j = (d_pig + d_met + d_kry + d_spi) > 0
            elif isinstance(raw_rubble, Mapping):
                d_pig = max(0, _safe_fleet_int(raw_rubble.get("pig_iron", raw_rubble.get("pig", 0)), 0))
                d_met = max(0, _safe_fleet_int(raw_rubble.get("metal", 0), 0))
                d_kry = max(0, _safe_fleet_int(raw_rubble.get("kryptonite", 0), 0))
                d_spi = max(0, _safe_fleet_int(raw_rubble.get("spice", 0), 0))
                has_deb_j = (d_pig + d_met + d_kry + d_spi) > 0
            else:
                has_deb_j = bool(raw_rubble)
            pos_meta[p_pos] = (
                c_min,
                c_max,
                uf_exact,
                temp_j,
                is_free_j,
                pname_j,
                owner_j,
                has_deb_j,
                has_rubble_key,
                d_pig,
                d_met,
                d_kry,
                d_spi,
            )
        if pos_meta:
            if slots:
                enriched_slots: list[GalaxySlot] = []
                for sl in slots:
                    meta_p = pos_meta.get(sl.position)
                    if meta_p is not None:
                        (
                            c_min,
                            c_max,
                            uf_exact,
                            temp_j,
                            _,
                            _,
                            _,
                            has_deb_j,
                            has_rubble_key,
                            d_pig,
                            d_met,
                            d_kry,
                            d_spi,
                        ) = meta_p
                        upd_kwargs: dict[str, Any] = {}
                        if sl.status_color == "free":
                            if c_min is not None:
                                upd_kwargs["colonize_min_fields"] = c_min
                            if c_max is not None:
                                upd_kwargs["colonize_max_fields"] = c_max
                            if uf_exact is not None:
                                upd_kwargs["uninhabited_fields"] = uf_exact
                            if temp_j is not None:
                                upd_kwargs["temperature"] = temp_j
                        elif temp_j is not None and sl.temperature is None:
                            upd_kwargs["temperature"] = temp_j
                        if has_rubble_key:
                            upd_kwargs["has_debris"] = bool(has_deb_j)
                            upd_kwargs["debris_pig_iron"] = d_pig if has_deb_j else 0
                            upd_kwargs["debris_metal"] = d_met if has_deb_j else 0
                            upd_kwargs["debris_kryptonite"] = d_kry if has_deb_j else 0
                            upd_kwargs["debris_spice"] = d_spi if has_deb_j else 0
                        if upd_kwargs:
                            sl = replace(sl, **upd_kwargs)
                    enriched_slots.append(sl)
                slots = enriched_slots
            else:
                for p_pos in sorted(pos_meta):
                    (
                        c_min,
                        c_max,
                        uf_exact,
                        temp_j,
                        is_free_j,
                        pname_j,
                        owner_j,
                        has_deb_j,
                        _has_rubble_key,
                        d_pig,
                        d_met,
                        d_kry,
                        d_spi,
                    ) = pos_meta[p_pos]
                    coords = f"{gal:02d}:{sys_num:03d}:{p_pos:02d}"
                    is_sp_j = "spacia" in pname_j.lower() or "spacia" in owner_j.lower()
                    slots.append(
                        GalaxySlot(
                            coords=coords,
                            position=p_pos,
                            name=pname_j,
                            owner=("Spacia" if (is_sp_j and not owner_j) else owner_j),
                            status_color=("free" if is_free_j else ("yellow" if is_sp_j else "light_red")),
                            has_debris=has_deb_j,
                            is_spacia=is_sp_j,
                            debris_pig_iron=d_pig if has_deb_j else 0,
                            debris_metal=d_met if has_deb_j else 0,
                            debris_kryptonite=d_kry if has_deb_j else 0,
                            debris_spice=d_spi if has_deb_j else 0,
                            colonize_min_fields=c_min,
                            colonize_max_fields=c_max,
                            uninhabited_fields=uf_exact,
                            temperature=temp_j,
                        )
                    )

    return tuple(slots)


_SPY_BRACKETED_COORDS_RE = re.compile(r"\[\s*(\d{1,2}):(\d{1,3}):(\d{1,2})\s*\]", re.ASCII)


def _extract_spy_coords(text: str) -> str:
    candidates: list[tuple[int, int, int, str]] = []
    bracketed_spans: list[tuple[int, int]] = []
    for m_br in _SPY_BRACKETED_COORDS_RE.finditer(text):
        bracketed_spans.append((m_br.start(), m_br.end()))
        g_str, s_str, p_str = m_br.group(1), m_br.group(2), m_br.group(3)
        tg = _valid_gal(g_str)
        ts = _valid_sys(s_str)
        tp = int(p_str)
        if tg is None or ts is None or not (1 <= tp <= 16):
            continue
        m_start, m_end = m_br.start(), m_br.end()
        if (
            _is_parenthesized_noise_triple(text, m_start, m_end)
            or _GALAXY_PAIR_NOISE_PREFIX_RE.search(text[:m_start])
            or _GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(text[m_end:])
        ):
            continue
        is_clock = _is_clock_shaped_triple(g_str, s_str, p_str) or _is_clock_0m_triple(
            g_str, s_str, p_str
        )
        has_ctx = bool(_COORD_CONTEXT_PREFIX_RE.search(text[:m_start]))
        if is_clock and not has_ctx:
            continue
        coord_txt = f"{g_str}:{s_str}:{p_str}"
        candidates.append((0 if has_ctx else 1, 1 if is_clock else 0, 0, coord_txt))
    for m_tr in _GALAXY_COORDS_TRIPLE_RE.finditer(text):
        m_start, m_end = m_tr.start(), m_tr.end()
        if any(bs <= m_start and m_end <= be for bs, be in bracketed_spans):
            continue
        g_str, s_str, p_str = m_tr.group(1), m_tr.group(2), m_tr.group(3)
        tg = _valid_gal(g_str)
        ts = _valid_sys(s_str)
        tp = int(p_str)
        if tg is None or ts is None or not (1 <= tp <= 16):
            continue
        if _is_parenthesized_noise_triple(text, m_start, m_end):
            continue
        if (
            _GALAXY_PAIR_NOISE_PREFIX_RE.search(text[:m_start])
            or _GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(text[m_end:])
        ):
            continue
        is_clock = _is_clock_shaped_triple(g_str, s_str, p_str) or _is_clock_0m_triple(
            g_str, s_str, p_str
        )
        has_ctx = bool(_COORD_CONTEXT_PREFIX_RE.search(text[:m_start]))
        if is_clock and not has_ctx:
            continue
        candidates.append((0 if has_ctx else 1, 1 if is_clock else 0, 1, m_tr.group(0)))
    if not candidates:
        return ""
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    return candidates[0][3]


def _extract_spy_link_coords(soup: Any) -> str:
    if soup is None:
        return ""
    for a in soup.find_all("a"):
        meta = f"{a.get('href') or ''} {a.get('onclick') or ''}".lower()
        if not any(
            tok in meta
            for tok in (
                "internalgalaxy",
                "internalfleet",
                "p_galaxy",
                "sendspy",
                "gal=",
                "galaxy=",
                "sun=",
                "system=",
            )
        ):
            continue
        a_txt = " ".join(a.get_text(" ", strip=True).split())
        if not a_txt:
            continue
        for m_tr in _GALAXY_COORDS_TRIPLE_RE.finditer(a_txt):
            g_str, s_str, p_str = m_tr.group(1), m_tr.group(2), m_tr.group(3)
            tg = _valid_gal(g_str)
            ts = _valid_sys(s_str)
            tp = int(p_str)
            if tg is None or ts is None or not (1 <= tp <= 16):
                continue
            m_start, m_end = m_tr.start(), m_tr.end()
            if (
                _is_parenthesized_noise_triple(a_txt, m_start, m_end)
                or _GALAXY_PAIR_NOISE_PREFIX_RE.search(a_txt[:m_start])
                or _GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(a_txt[m_end:])
            ):
                continue
            is_clock = _is_clock_shaped_triple(g_str, s_str, p_str) or _is_clock_0m_triple(
                g_str, s_str, p_str
            )
            has_ctx = bool(_COORD_CONTEXT_PREFIX_RE.search(a_txt[:m_start]))
            if is_clock and not has_ctx:
                continue
            return m_tr.group(0)
    return ""


_GERMAN_SHIP_ALIASES: dict[str, tuple[str, ...]] = {
    "s1": ("Kleiner Transporter",),
    "s2": ("Großer Transporter", "Grosser Transporter"),
    "s3": ("Transmitter",),
    "s4": ("Kleiner Jäger", "Kleiner Jaeger"),
    "s5": ("Kriegsschiff",),
    "s6": ("Sternenkreuzer",),
    "s7": ("Phönix", "Phoenix"),
    "s8": ("Kolonieschiff",),
    "s9": ("Solarzellen", "Solarzelle"),
    "s10": ("Spionagesonde",),
    "s11": ("Recycler",),
    "s13": ("Schlachtschiff",),
    "s14": ("Tarnkappenbomber",),
    "s15": ("Zerstörer", "Zerstoerer"),
    "s16": ("Imperiale Sternenbasis",),
}

_GERMAN_DEFENSE_ALIASES: dict[str, tuple[str, ...]] = {
    "d1": ("Mörser", "Moerser"),
    "d2": ("Lasergeschützbatterie", "Lasergeschuetzbatterie"),
    "d3": ("Ionenkanone",),
    "d4": ("Positronenfeuerzeug", "Positronengeschütz", "Positronengeschuetz"),
    "d5": ("Turboflak",),
    "d6": ("Intervallkanone",),
    "d7": ("Solarkanone",),
    "d8": ("Leichte Gravitonkanone",),
    "d9": ("Schwere Gravitonkanone",),
    "d10": (" Kleine Schildkuppel", "Kleine Schildkuppel"),
    "d11": ("Große Schildkuppel", "Grosse Schildkuppel"),
}

_GERMAN_BUILDING_ALIASES: dict[str, tuple[str, ...]] = {
    "b1": ("Roheisenmine",),
    "b2": ("Hochofen",),
    "b3": ("Kryptonitmine",),
    "b4": ("Spicemine",),
    "b5": ("Fusionskraftwerk",),
    "b6": ("Thermosolarkraftwerk",),
    "b7": ("Entwicklungszentrum",),
    "b8": ("Forschungslabor",),
    "b9": ("Waffenfabrik",),
    "b10": ("Flottenstützpunkt", "Flottenstuetzpunkt"),
    "b11": ("Schutzschild", "Schildgenerator"),
    "b12": ("Roheisenlager",),
    "b13": ("Metalllager",),
    "b14": ("Kryptonitlager",),
    "b15": ("Spicelager",),
    "b16": ("Mikrosystembeschleuniger",),
    "b17": ("Teleporter",),
    "b18": ("Galaxiescanner",),
}

_GERMAN_RESEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "r1": ("Bergbautechnik",),
    "r2": ("Energietechnik",),
    "r3": ("Raketentriebwerk", "Verbrennungstriebwerk"),
    "r4": ("Diffusionstriebwerk",),
    "r5": ("Spionagetechnik",),
    "r6": ("Warpantrieb",),
    "r7": ("Waffentechnik",),
    "r8": ("Schildtechnik",),
    "r9": ("Zielsysteme",),
    "r10": ("Schiffskonstruktion", "Raumschiffpanzerung"),
    "r11": ("Gravitonforschung", "Gravitontechnik"),
    "r12": ("Teleportation",),
    "r15": ("Schwarzes-Loch-Forschung",),
}

_MSG_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?|\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*,?\s*\d{2}:\d{2}(?::\d{2})?)\b",
    re.ASCII,
)

_MSG_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^check[_-](\d{1,20})$", re.IGNORECASE | re.ASCII),
    re.compile(r"(?:del|delete|msg|message|id)\s*\[\s*(\d{1,20})\s*\]", re.IGNORECASE | re.ASCII),
    re.compile(r"(?:msg[_-]?id|message[_-]?id|del[_-]?id|del|delete|read|reportID|battle|id)\s*=\s*(\d{1,20})\b", re.IGNORECASE | re.ASCII),
    re.compile(r"\b(?:deleteMessage|delMsg|showMessage|openMessage|toggleMessage|readMessage|showSpionageReport|showBattleReport)\s*\(\s*['\"]?(\d{1,20})['\"]?", re.IGNORECASE | re.ASCII),
    re.compile(r"^(?:msg|message|row|tr[_-]?msg)[_-]?(\d{1,20})$", re.IGNORECASE | re.ASCII),
)

_FOLDER_COUNTER_IDS: frozenset[str] = frozenset(
    {
        "msg-normal",
        "msg-alliance",
        "msg-spio",
        "msg-battle",
        "msg-fleet",
        "msg-admin",
        "msg-outgoing",
        "msg-system",
        "msg-economy",
    }
)


def _is_folder_counter_node(el: Any) -> bool:
    if el is None:
        return False
    eid = str(el.get("id") or "").strip().lower()
    if eid in _FOLDER_COUNTER_IDS:
        return True
    cls_raw = el.get("class") or ()
    cls_tokens = [str(x).lower() for x in (cls_raw if isinstance(cls_raw, list) else str(cls_raw).split())]
    if "messagebox" in cls_tokens:
        return True
    if eid.startswith("msg-") and "info" in cls_tokens:
        return True
    return False


def _norm_coord_triple(coords: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", str(coords or ""), re.ASCII)
    if not m:
        return ""
    g, s, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= g <= 99 and 1 <= s <= 999 and 1 <= p <= 16):
        return ""
    return f"{g:02d}:{s:03d}:{p:02d}"


def _is_select_all_checkbox(inp: Any) -> bool:
    nm = str(inp.get("name") or "").strip().lower()
    cid = str(inp.get("id") or "").strip().lower()
    if nm == "check" or cid == "check":
        return True
    for attr in ("name", "id", "class", "onclick", "onchange", "title"):
        raw = inp.get(attr)
        if isinstance(raw, list):
            raw = " ".join(str(x) for x in raw)
        txt = str(raw or "").lower()
        if any(
            tok in txt
            for tok in ("allmessages", "checkall", "selectall", "markall", "toggleall", "all_msg", "setall")
        ):
            return True
    return False


def _extract_element_msg_id(el: Any) -> str:
    if el is None or _is_folder_counter_node(el):
        return ""
    for attr in ("data-msg-id", "data-message-id", "data-id", "data-mfav", "id"):
        val = str(el.get(attr) or "").strip()
        if not val:
            continue
        if attr == "id" and val.lower() in _FOLDER_COUNTER_IDS:
            continue
        if attr != "id" and val.isascii() and val.isdigit():
            return val
        for pat in _MSG_ID_PATTERNS:
            m = pat.search(val)
            if m:
                return m.group(1)
    for cb in el.find_all("input", attrs={"type": re.compile(r"^checkbox$", re.IGNORECASE)}):
        if _is_select_all_checkbox(cb):
            continue
        val = str(cb.get("value") or "").strip()
        name = str(cb.get("name") or "").strip()
        cid = str(cb.get("id") or "").strip()
        for blob in (name, cid):
            for pat in _MSG_ID_PATTERNS:
                m = pat.search(blob)
                if m:
                    return m.group(1)
        if val.isascii() and val.isdigit() and val not in ("0", "1"):
            return val
    for tag in el.find_all(["span", "a", "button", "input", "tr", "div", "td"]):
        if _is_folder_counter_node(tag):
            continue
        for attr in ("data-mfav", "data-msg-id", "data-id", "href", "onclick", "id", "name", "data-bsum"):
            val = str(tag.get(attr) or "").strip()
            if not val:
                continue
            if attr in ("data-mfav", "data-msg-id", "data-id", "data-bsum") and val.isascii() and val.isdigit():
                return val
            for pat in _MSG_ID_PATTERNS:
                m = pat.search(val)
                if m:
                    return m.group(1)
    return ""


def _strip_own_planet_header_dom(soup: BeautifulSoup) -> None:
    """Remove `F_Hauptframe` top planet/resource/folder header bars so header chrome is never parsed as messages."""
    for sel in (
        "#planet-coords",
        "#planet-list",
        "form#ress",
        "#ress",
        "select[name='planet']",
        "#top-bar",
        ".top-bar",
        "#resource-bar",
        ".resource-bar",
        "#infobox",
        ".messagebox",
        "div.info[id^='msg-']",
        "#roheisen",
        "#metall",
        "#kryptonit",
        "#spice",
    ):
        for node in list(soup.select(sel)):
            if sel in ("#roheisen", "#metall", "#kryptonit", "#spice"):
                parent_tbl = node.find_parent("table") or node.find_parent("div")
                if parent_tbl is not None and not parent_tbl.select(
                    "input[type='checkbox'], div.message, div.msg, tr.message, tr.msg, a[href*='internalMessages']"
                ):
                    txt_low = parent_tbl.get_text(" ", strip=True).lower()
                    if not any(
                        tok in txt_low
                        for tok in ("espionage", "spionage", "battle", "kampfbericht", "player:", "spieler:")
                    ):
                        parent_tbl.decompose()
                        continue
            node.decompose()
    for node in list(soup.find_all(attrs={"id": True})):
        if _is_folder_counter_node(node):
            node.decompose()
    for form in list(soup.find_all("form")):
        act = str(form.get("action") or "").lower()
        if "newplanet=" in act:
            form.decompose()
    for a in list(soup.find_all("a", href=True)):
        a_txt = " ".join(a.get_text(" ", strip=True).split())
        if re.search(r"(?:unread\s+messages?|ungelesene\s+nachrichten?)", a_txt, re.IGNORECASE):
            a.decompose()


def _extract_embedded_spy_json(text: str) -> Optional[dict[str, Any]]:
    """Extract embedded SpaceInvasion Classic `{"info": ..., "type": "spy-report", "planet": ...}` JSON if present."""
    if "spy-report" not in text:
        return None
    marker = text.find("spy-report")
    decoder = json.JSONDecoder()
    search_start = max(0, marker - 400)
    idx = text.find("{", search_start)
    while 0 <= idx < marker:
        try:
            obj, _ = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                info = obj.get("info")
                if (isinstance(info, Mapping) and str(info.get("type") or "") == "spy-report") or isinstance(
                    obj.get("planet"), Mapping
                ):
                    return obj
        except ValueError:
            pass
        idx = text.find("{", idx + 1)
    return None


def _parse_embedded_unit_val(entry: Any) -> int:
    if isinstance(entry, Mapping):
        for k in ("level", "count", "amount", "v", "val"):
            if entry.get(k) is not None:
                return _parse_embedded_unit_val(entry.get(k))
        return 0
    if isinstance(entry, bool) or entry is None:
        return 0
    if isinstance(entry, int):
        return max(0, entry)
    if isinstance(entry, float):
        return max(0, int(round(entry))) if math.isfinite(entry) else 0
    s = str(entry).strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+|\d+", s, re.ASCII):
        return parse_si_int(s)
    return max(0, game_data.safe_int(s, 0))


_INNER_MESSAGE_BODY_RE = re.compile(
    r"(?:Message\s+from|Nachricht\s+von)\s+[^:]{1,60}:\s*(.+?)(?=\s*['\"]?\s*(?:Fleet\s+reports|Battle\s+reports|Espionage|System|User|Alliance)\s*[\u2605*]|\s*[\u2605*]\s*Favourites|\s*Received\s+on:\s*From:|\s*Empfangen\s+am:|$)",
    re.IGNORECASE,
)

_EXPLICIT_SPY_REPORT_TOKENS: tuple[str, ...] = (
    "espionage report",
    "spionagebericht",
    "spy report",
    '"spy-report"',
    "'spy-report'",
)

_NON_SPY_NOTICE_TOKENS: tuple[str, ...] = (
    "fleet return",
    "flottenrückkehr",
    "flottenrueckkehr",
    "returning from duty",
    "is returning from",
    "kehrt von",
    "zurückgekehrt",
    "zurueckgekehrt",
    "were unloaded",
    "wurden ausgeladen",
    "the assignment was to:",
    "der auftrag war:",
    "to its home planet",
    "zu seinem heimatplaneten",
    "colonization report",
    "kolonisierungsbericht",
    "planet was colonized",
    "planet wurde kolonisiert",
    "colonization failed",
    "kolonisierung fehlgeschlagen",
    "battle report",
    "combat report",
    "kampfbericht",
    "attacker",
    "angreifer",
    "plundered",
    "looted",
    "erbeutet",
    "debris field",
    "trümmerfeld",
    "truemmerfeld",
    "recycling report",
    "space rubble",
    "harvested",
    "recycled",
    "trümmerfeld abgebaut",
    "delivered",
    "geliefert",
)


def _extract_inner_message_body(text: str) -> str:
    """Return the inner `Message from ...:` body when full `F_Hauptframe` chrome surrounds a read message."""
    if not text:
        return ""
    m_inner = _INNER_MESSAGE_BODY_RE.search(text)
    return m_inner.group(1).strip() if m_inner else text


def is_non_spy_notice_text(text: str) -> bool:
    """Return True when `text` is a fleet-return, battle, recycle, transport, or colonization notice — not a spy report."""
    if not text:
        return False
    low = str(text).lower()
    if any(tok in low for tok in _EXPLICIT_SPY_REPORT_TOKENS):
        return False
    return any(tok in low for tok in _NON_SPY_NOTICE_TOKENS)


def parse_spy_report_html(html: str, *, target_coords: str = "") -> SpyReport:
    """Parse an espionage or uninhabited-ecology report snippet into `SpyReport`."""
    soup = BeautifulSoup(html, "html.parser")
    server_msg_id = _extract_element_msg_id(soup)
    _strip_own_planet_header_dom(soup)
    full_text = " ".join(soup.get_text(" ", strip=True).split())
    embedded_json = _extract_embedded_spy_json(full_text)
    text = full_text if embedded_json is not None else _extract_inner_message_body(full_text)
    clean_raw_text = redact_sensitive(text)[:4000]

    coords = target_coords
    if embedded_json is not None and isinstance(embedded_json.get("planet"), Mapping):
        emb_c = _norm_coord_triple(str(embedded_json["planet"].get("coords") or ""))
        if emb_c:
            coords = emb_c
    if not coords:
        coords = _extract_spy_coords(text) or _extract_spy_link_coords(soup)

    m_dt = _MSG_DATE_RE.search(text)
    msg_date_text = m_dt.group(1).strip() if m_dt else ""

    planet_name = ""
    if embedded_json is not None and isinstance(embedded_json.get("planet"), Mapping):
        emb_pn = str(embedded_json["planet"].get("name") or "").strip()
        if emb_pn:
            planet_name = emb_pn[:60]
    if not planet_name:
        m_pl = re.search(
            r"(?:on|auf|for|von|planet)\s+([A-Za-z0-9_.\- ]{1,40}?)\s*\[\s*\d{1,2}:\d{1,3}:\d{1,2}\s*\]",
            text,
            re.IGNORECASE | re.ASCII,
        )
        if m_pl:
            cand_pl = m_pl.group(1).strip(" -:|")
            if cand_pl.lower() not in ("espionage report", "spionagebericht", "report", "planet"):
                planet_name = cand_pl

    def _find_res(label: str) -> int:
        m = re.search(rf"{label}\s*:?\s*([\d.]+)", text, re.IGNORECASE | re.ASCII)
        return parse_si_int(m.group(1)) if m else 0

    res = Resources(
        pig_iron=_find_res(r"(?:Pig\s+iron|Roheisen)"),
        metal=_find_res(r"(?:Metal|Metall)"),
        kryptonite=_find_res(r"(?:Kryptonite|Kryptonit)"),
        spice=_find_res(r"Spice"),
    )
    if embedded_json is not None and isinstance(embedded_json.get("planet"), Mapping):
        emb_res = embedded_json["planet"].get("resources")
        if isinstance(emb_res, Sequence) and not isinstance(emb_res, (str, bytes)) and len(emb_res) >= 4:
            res = Resources(
                pig_iron=_parse_embedded_unit_val(emb_res[0]),
                metal=_parse_embedded_unit_val(emb_res[1]),
                kryptonite=_parse_embedded_unit_val(emb_res[2]),
                spice=_parse_embedded_unit_val(emb_res[3]),
            )

    uninhabited_fields: Optional[int] = None
    m_fld = re.search(
        r"(?:approx\.?|estimated|expected|ca\.?)?\s*([0-9]{2,3})\s*(?:building\s*|bau)?(?:fields|felder)\b",
        text,
        re.IGNORECASE,
    )
    if m_fld:
        uninhabited_fields = int(m_fld.group(1))
    else:
        m_fld_alt = re.search(
            r"(?:building\s*fields|baufelder|planet\s*size|fields|felder)\s*(?::|=)\s*(?:[0-9]{1,3}\s*/\s*)?([0-9]{2,3})\b",
            text,
            re.IGNORECASE,
        )
        if m_fld_alt:
            uninhabited_fields = int(m_fld_alt.group(1))

    low_text = text.lower()
    dug_in: Optional[bool] = None
    if "dug in" in low_text or "dug-in" in low_text:
        dug_in = "not dug in" not in low_text
    elif "eingegraben" in low_text:
        dug_in = "nicht eingegraben" not in low_text

    fleet: dict[str, int] = {}
    for skey, slug in SHIP_SLUG_BY_KEY.items():
        names: list[str] = []
        info = game_data.SHIPS.get(slug)
        if info and info.get("name"):
            names.append(str(info["name"]))
        names.extend(_GERMAN_SHIP_ALIASES.get(skey, ()))
        for nm in names:
            m_u = re.search(rf"\b{re.escape(nm.strip())}\s*:?\s*([0-9][0-9.]*)", text, re.IGNORECASE)
            if m_u:
                fleet[skey] = parse_si_int(m_u.group(1))
                break

    defense: dict[str, int] = {}
    for dkey, slug in DEFENSE_SLUG_BY_KEY.items():
        names_d: list[str] = []
        info = game_data.DEFENSES.get(slug)
        if info and info.get("name"):
            names_d.append(str(info["name"]))
        names_d.extend(_GERMAN_DEFENSE_ALIASES.get(dkey, ()))
        for nm in names_d:
            m_u = re.search(rf"\b{re.escape(nm.strip())}\s*:?\s*([0-9][0-9.]*)", text, re.IGNORECASE)
            if m_u:
                defense[dkey] = parse_si_int(m_u.group(1))
                break

    buildings: dict[str, int] = {}
    for info in game_data.BUILDINGS.values():
        bid = info.get("id")
        if not bid:
            continue
        bkey = f"b{bid}"
        names_b: list[str] = []
        if info.get("name"):
            names_b.append(str(info["name"]))
        names_b.extend(_GERMAN_BUILDING_ALIASES.get(bkey, ()))
        for nm in names_b:
            m_b = re.search(rf"\b{re.escape(nm.strip())}\s*(?:\(\s*Level\s*|:)?\s*([0-9]{{1,3}})\b", text, re.IGNORECASE)
            if m_b:
                buildings[bkey] = parse_si_int(m_b.group(1))
                break

    researches: dict[str, int] = {}
    for info in game_data.RESEARCHES.values():
        rkey = str(info.get("id") or "")
        if not rkey:
            continue
        names_r: list[str] = []
        if info.get("name"):
            names_r.append(str(info["name"]))
        names_r.extend(_GERMAN_RESEARCH_ALIASES.get(rkey, ()))
        for nm in names_r:
            m_r = re.search(rf"\b{re.escape(nm.strip())}\s*(?:\(\s*Level\s*|:)?\s*([0-9]{{1,3}})\b", text, re.IGNORECASE)
            if m_r:
                researches[rkey] = parse_si_int(m_r.group(1))
                break

    if embedded_json is not None and isinstance(embedded_json.get("planet"), Mapping):
        pl_j = embedded_json["planet"]
        emb_b = pl_j.get("buildings")
        if isinstance(emb_b, Mapping):
            for bk, bv in emb_b.items():
                bk_s = str(bk).strip().lower()
                if re.fullmatch(r"b\d{1,2}", bk_s):
                    lvl_v = _parse_embedded_unit_val(bv)
                    if lvl_v > 0:
                        buildings[bk_s] = lvl_v
        emb_s = pl_j.get("ships") or pl_j.get("fleet")
        if isinstance(emb_s, Mapping):
            for sk, sv in emb_s.items():
                sk_s = str(sk).strip().lower()
                if sk_s in SHIP_SLUG_BY_KEY:
                    cnt_v = _parse_embedded_unit_val(sv)
                    if cnt_v > 0:
                        fleet[sk_s] = cnt_v
        emb_d = pl_j.get("defense") or pl_j.get("defenses")
        if isinstance(emb_d, Mapping):
            for dk, dv in emb_d.items():
                dk_s = str(dk).strip().lower()
                if dk_s in DEFENSE_SLUG_BY_KEY:
                    cnt_v = _parse_embedded_unit_val(dv)
                    if cnt_v > 0:
                        defense[dk_s] = cnt_v
        emb_r = pl_j.get("researches") or embedded_json.get("researches")
        if isinstance(emb_r, Mapping):
            for rk, rv in emb_r.items():
                rk_s = str(rk).strip().lower()
                if re.fullmatch(r"r\d{1,2}", rk_s):
                    lvl_v = _parse_embedded_unit_val(rv)
                    if lvl_v > 0:
                        researches[rk_s] = lvl_v

    is_spacia = "spacia" in low_text
    owner = "Spacia" if is_spacia else ""
    if not owner and embedded_json is not None:
        pl_j = embedded_json.get("planet") if isinstance(embedded_json.get("planet"), Mapping) else {}
        for cand_own in (embedded_json.get("player"), pl_j.get("owner"), pl_j.get("player")):
            if isinstance(cand_own, Mapping) and cand_own.get("name"):
                owner = str(cand_own["name"]).strip()[:80]
                break
            if isinstance(cand_own, str) and cand_own.strip():
                owner = cand_own.strip()[:80]
                break
    if not owner:
        m_own = re.search(
            r"(?:Player|Owner|Spieler)\s*:\s*(.+?)(?=\s+(?:Resources|Rohstoffe|Pig\s+iron|Roheisen|Metal|Metall|Kryptonite|Kryptonit|Spice|Fleet|Flotte|Defense|Verteidigung|Buildings|Gebäude|Gebaeude|Research|Forschung|Approx|Dug|Eingegraben|[0-9]{1,2}:[0-9]{1,3}:[0-9]{1,2}|$)|$)",
            text,
            re.IGNORECASE,
        )
        if m_own:
            owner = m_own.group(1).strip(" |:-()'\"")

    if uninhabited_fields is None and embedded_json is not None and isinstance(embedded_json.get("planet"), Mapping):
        pl_j = embedded_json["planet"]
        for fk in ("maxFields", "max_fields", "fields"):
            if pl_j.get(fk) is not None and not isinstance(pl_j.get(fk), Mapping):
                fv = _parse_embedded_unit_val(pl_j.get(fk))
                if 30 <= fv <= 600:
                    uninhabited_fields = fv
                    break

    # Avoid treating occupied-planet field counts as uninhabited colony fields unless explicitly uninhabited
    if (
        uninhabited_fields is not None
        and (owner or is_spacia or buildings)
        and not any(tok in low_text for tok in ("uninhabited", "unbewohnt", "free planet", "empty planet"))
    ):
        uninhabited_fields = None

    return SpyReport(
        target_coords=coords,
        owner=owner,
        resources=res,
        fleet=fleet,
        defense=defense,
        dug_in=dug_in,
        uninhabited_fields=uninhabited_fields,
        is_spacia=is_spacia,
        buildings=buildings,
        researches=researches,
        planet_name=planet_name,
        server_msg_id=server_msg_id,
        msg_date_text=msg_date_text,
        raw_text=clean_raw_text,
    )


def _group_table_message_blocks(soup: BeautifulSoup) -> list[str]:
    """Group multi-`<tr>` SpaceInvasion `internalMessages` entries (header `<tr>` with checkbox/msgID + body `<tr>`s)."""
    grouped: list[str] = []
    seen_tr_ids: set[int] = set()
    for tbl in soup.find_all("table"):
        # Skip outer layout tables that merely wrap sub-tables with checkboxes
        direct_rows: list[Any] = []
        tbody = tbl.find("tbody", recursive=False)
        row_parent = tbody if tbody is not None else tbl
        for tr in row_parent.find_all("tr", recursive=False):
            direct_rows.append(tr)
        if not direct_rows:
            continue
        header_indices: list[int] = []
        for idx, tr in enumerate(direct_rows):
            if id(tr) in seen_tr_ids:
                continue
            cbs = [
                cb
                for cb in tr.find_all("input", attrs={"type": re.compile(r"^checkbox$", re.IGNORECASE)})
                if not _is_select_all_checkbox(cb)
            ]
            # Do not treat a sub-table wrapper row holding multiple checkboxes as a single header
            if len(cbs) == 1:
                header_indices.append(idx)
                continue
            tr_id = str(tr.get("id") or "")
            tr_cls = " ".join(tr.get("class") or ())
            if (
                re.match(r"^(?:msg|message)[_-]?\d+$", tr_id, re.IGNORECASE)
                or any(c in ("message", "msg", "msg-head", "msg-header") for c in tr_cls.split())
                or tr.get("data-msg-id")
                or tr.get("data-message-id")
            ):
                header_indices.append(idx)
        if not header_indices:
            continue
        for pos_i, start_idx in enumerate(header_indices):
            end_idx = header_indices[pos_i + 1] if (pos_i + 1) < len(header_indices) else len(direct_rows)
            chunk: list[str] = []
            for r_idx in range(start_idx, end_idx):
                tr = direct_rows[r_idx]
                if r_idx > start_idx:
                    # Stop before footer delete-controls row (`select[name*="del"]` or submit button)
                    if tr.find("select") is not None or tr.find("input", attrs={"type": re.compile(r"^(?:submit|button)$", re.IGNORECASE)}) is not None:
                        if not _extract_spy_coords(tr.get_text(" ", strip=True)):
                            break
                seen_tr_ids.add(id(tr))
                chunk.append(str(tr))
            if chunk:
                grouped.append("<table>" + "".join(chunk) + "</table>")
    return grouped


def parse_spy_messages_html(
    html: str,
    *,
    exclude_coords: Iterable[str] = (),
) -> tuple[SpyReport, ...]:
    """Parse multiple espionage reports from `internalMessages&type=spio` or main inbox HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    _strip_own_planet_header_dom(soup)
    excl_norm = {_norm_coord_triple(c) for c in (exclude_coords or ()) if _norm_coord_triple(c)}
    keywords = (
        "pig iron",
        "roheisen",
        "metal",
        "metall",
        "kryptonite",
        "kryptonit",
        "spice",
        "fields",
        "felder",
        "espionage",
        "spionage",
    )

    def _looks_like_spy_block(txt: str, el: Any = None) -> bool:
        inner_txt = _extract_inner_message_body(txt)
        low_full = txt.lower()
        low = inner_txt.lower()
        if not any(k in low for k in keywords):
            return False
        has_explicit_spy = any(
            tok in low or tok in low_full for tok in _EXPLICIT_SPY_REPORT_TOKENS
        )
        # Exclude unexpanded header-only `Colony [coords] spy report` rows that lack any payload
        if (
            el is not None
            and el.select_one('[onclick*="showSpionageReport"]') is not None
            and el.find(attrs={"data-si-expanded": "1"}) is None
            and el.select_one(".si-expanded-body") is None
            and not any(rk in low for rk in ("pig iron", "roheisen", "kryptonite", "kryptonit", "fields", "felder"))
        ):
            return False
        # Exclude counter-espionage warnings that have no resource/field payload
        if (
            ("foreign fleet" in low or "fremde flotte" in low or "counter-espionage" in low or "spionageabwehr" in low)
            and not has_explicit_spy
            and not any(rk in low for rk in ("pig iron", "roheisen", "kryptonite", "kryptonit", "fields", "felder"))
        ):
            return False
        # Exclude fleet-return, colonization, battle, recycle, and transport notices
        if not has_explicit_spy and (is_non_spy_notice_text(inner_txt) or is_non_spy_notice_text(txt)):
            return False
        return bool(_extract_spy_coords(inner_txt) or _extract_spy_coords(txt) or (el is not None and _extract_spy_link_coords(el)))

    blocks: list[str] = []
    # 1. Explicit message containers (merging adjacent body `<tr>` when header is a `<tr>`)
    containers = [
        el
        for el in soup.select(
            "div.message, div.msg, tr.message, tr.msg, tr[id^='msg'], div[id^='msg'], [data-msg-id], [data-message-id]"
        )
        if not _is_folder_counter_node(el)
    ]
    for el in containers:
        combined_html = str(el)
        if el.name == "tr":
            nxt = el.find_next_sibling("tr")
            if nxt is not None and nxt not in containers and not _extract_element_msg_id(nxt):
                combined_html = f"<table>{el}{nxt}</table>"
        comb_soup = BeautifulSoup(combined_html, "html.parser")
        if _looks_like_spy_block(comb_soup.get_text(" ", strip=True), comb_soup):
            blocks.append(combined_html)

    # 1b. Checkbox-grouped multi-`<tr>` table messages (header row + resource/fleet body row)
    if not blocks:
        for grp_html in _group_table_message_blocks(soup):
            grp_soup = BeautifulSoup(grp_html, "html.parser")
            if _looks_like_spy_block(grp_soup.get_text(" ", strip=True), grp_soup):
                blocks.append(grp_html)

    # 2. Fallback: innermost table rows / divs containing spy blocks without nested spy containers
    if not blocks:
        candidates = [el for el in soup.find_all(["tr", "div", "li"]) if not _is_folder_counter_node(el)]
        for el in candidates:
            txt = el.get_text(" ", strip=True)
            if not _looks_like_spy_block(txt, el):
                continue
            has_child_spy = any(
                child is not el and _looks_like_spy_block(child.get_text(" ", strip=True), child)
                for child in el.find_all(["tr", "div", "li"])
            )
            if not has_child_spy:
                blocks.append(str(el))

    # 3. Last resort: single report in whole page text (only with explicit spy/uninhabited marker)
    if not blocks:
        page_txt = soup.get_text(" ", strip=True)
        page_low = page_txt.lower()
        if (
            any(tok in page_low for tok in (*_EXPLICIT_SPY_REPORT_TOKENS, "uninhabited", "unbewohnt"))
            and _looks_like_spy_block(page_txt, soup)
        ):
            blocks.append(str(soup))

    reports: list[SpyReport] = []
    seen_keys: set[tuple[str, str]] = set()
    for blk in blocks:
        rep = parse_spy_report_html(blk)
        if not rep.target_coords:
            continue
        if is_non_spy_notice_text(rep.raw_text):
            continue
        norm_c = _norm_coord_triple(rep.target_coords)
        if norm_c and norm_c in excl_norm:
            continue
        dedup_key = (norm_c or rep.target_coords, rep.server_msg_id)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        reports.append(rep)
    return tuple(reports)


def extract_message_folder_links(html: str) -> list[tuple[str, str, int, int]]:
    """Extract `(folder_type, href, unread_count, total_count)` links from `internalMessages` or `F_Navi`."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: dict[str, tuple[str, str, int, int]] = {}

    def _parse_counts(txt: str, primary_txt: str = "") -> tuple[int, int]:
        m_slash = re.search(r"(\d[\d.]*)\s*/\s*(\d[\d.]*)", primary_txt or txt, re.ASCII)
        if not m_slash and primary_txt:
            m_slash = re.search(r"(\d[\d.]*)\s*/\s*(\d[\d.]*)", txt, re.ASCII)
        if m_slash:
            return parse_si_int(m_slash.group(1)), parse_si_int(m_slash.group(2))
        m_par = re.search(r"\(\s*(\d[\d.]*)\s*\)", primary_txt or txt, re.ASCII)
        if m_par:
            u = parse_si_int(m_par.group(1))
            return u, u
        nums = [parse_si_int(m.group(1)) for m in re.finditer(r"\b(\d[\d.]*)\b", txt, re.ASCII)]
        if len(nums) >= 2:
            return nums[0], nums[1]
        if len(nums) == 1:
            return nums[0], nums[0]
        return 0, 0

    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if "action=internalmessages" not in href.lower():
            continue
        m_type = re.search(r"[?&](?:type|cat|category)=([a-zA-Z0-9_-]+)", href)
        ftype = m_type.group(1).lower() if m_type else ""
        if not ftype:
            continue
        row = a.find_parent("tr") or a.find_parent("li") or a
        txt = " ".join(row.get_text(" ", strip=True).split())
        a_txt = " ".join(a.get_text(" ", strip=True).split())
        unread, total = _parse_counts(txt, a_txt)
        clean_href = redact_sensitive(href)
        prev = found.get(ftype)
        if prev is None or (unread, total) > (prev[2], prev[3]):
            found[ftype] = (ftype, clean_href, unread, total)

    for box in soup.find_all(attrs={"onclick": True}):
        oc = str(box.get("onclick") or "").strip()
        if "action=internalmessages" not in oc.lower():
            continue
        m_url = re.search(r"['\"]([^'\"]*action=internalMessages[^'\"]*)['\"]", oc, re.IGNORECASE)
        href = m_url.group(1).strip() if m_url else oc
        m_type = re.search(r"[?&](?:type|cat|category)=([a-zA-Z0-9_-]+)", href, re.IGNORECASE)
        ftype = m_type.group(1).lower() if m_type else ""
        if not ftype:
            continue
        info_el = box.find(attrs={"id": f"msg-{ftype}"}) or box.select_one(".info")
        info_txt = " ".join(info_el.get_text(" ", strip=True).split()) if info_el is not None else ""
        box_txt = " ".join(box.get_text(" ", strip=True).split())
        unread, total = _parse_counts(box_txt, info_txt)
        clean_href = redact_sensitive(href)
        prev = found.get(ftype)
        if prev is None or (unread, total) > (prev[2], prev[3]):
            found[ftype] = (ftype, clean_href, unread, total)

    for ftype in ("normal", "alliance", "spio", "battle", "fleet", "admin"):
        if ftype in found:
            continue
        cnt_el = soup.find(attrs={"id": f"msg-{ftype}"})
        if cnt_el is not None:
            cnt_txt = " ".join(cnt_el.get_text(" ", strip=True).split())
            unread, total = _parse_counts(cnt_txt, cnt_txt)
            found[ftype] = (ftype, f"indexInternal.es?action=internalMessages&type={ftype}", unread, total)

    return list(found.values())


def is_non_recycle_fleet_notice_text(text: str, subject: str = "") -> bool:
    """Return True when inbox message text is a stationing, transport, fleet-return, spy,
    or colonize notice that merely mentions 'Recycler' in its ship roster rather than a
    genuine debris-field recycling report."""
    low = (text or "").strip().lower()
    subj_low = (subject or "").strip().lower()
    combined = f"{subj_low} {low}".strip()
    if not combined:
        return False
    if any(
        tok in combined
        for tok in (
            "recycling report",
            "trümmerfeldbericht",
            "truemmerfeldbericht",
            "space rubble",
            "trümmerfeld abgebaut",
            "couldn't find any more resources",
            "could not find any more resources",
            "pieces of rubble",
        )
    ):
        return False
    if any(
        tok in combined
        for tok in (
            "was re-stationed",
            "re-stationed",
            "restationed",
            "has been stationed",
            "wurde stationiert",
            "umstationiert",
            "stationiert",
            "it delivers",
            "has delivered",
            "delivered resources",
            "fleet return",
            "flottenrückkehr",
            "flottenrueckkehr",
            "returning from duty",
            "were unloaded",
            "wurden ausgeladen",
            "espionage report",
            "spionagebericht",
            "spy report",
        )
    ):
        return True
    if re.search(r"\]\s*station\b", combined):
        return True
    if re.search(r"\bstation\s*$", subj_low):
        return True
    return False


def _classify_and_parse_inbox_block(
    block_html: str,
    *,
    folder_type: str = "",
    exclude_coords: SetLike = frozenset(),
) -> Optional[InboxMessage]:
    soup = BeautifulSoup(block_html or "", "html.parser")
    # Exclude dummy folder counter fragments (`0 / 7`) if passed directly
    if re.fullmatch(r"\d[\d.]*\s*/\s*\d[\d.]*", " ".join(soup.get_text(" ", strip=True).split())):
        return None
    text = " ".join(soup.get_text(" ", strip=True).split()).strip()
    if not text or len(text) < 4:
        return None
    low = text.lower()
    if low in ("no messages", "keine nachrichten", "delete marked", "markierte löschen", "proceed"):
        return None

    has_expanded = bool(
        soup.find(attrs={"data-si-expanded": "1"}) is not None
        or soup.select_one(".si-expanded-body") is not None
    )
    needs_expand = bool(
        soup.select_one(
            '[onclick*="showSpionageReport"], [onclick*="showBattleReport"], '
            '[data-bsum], a[href*="action=internalMessages"][href*="read="]'
        )
    )
    body_complete = has_expanded or not needs_expand

    server_msg_id = _extract_element_msg_id(soup)
    m_dt = _MSG_DATE_RE.search(text)
    msg_date_text = m_dt.group(1).strip() if m_dt else ""

    sender = ""
    subject = ""
    # Extract sender / subject from header cells when structured as a table row
    first_tr = soup.find("tr")
    if first_tr is not None:
        cells: list[str] = []
        for td in first_tr.find_all(["td", "th"], recursive=False):
            td_clone = BeautifulSoup(str(td), "html.parser")
            for exp_div in td_clone.select(".si-expanded-body"):
                exp_div.decompose()
            cells.append(" ".join(td_clone.get_text(" ", strip=True).split()).strip())
        non_empty = [c for c in cells if c and not _MSG_DATE_RE.fullmatch(c)]
        if len(non_empty) >= 2:
            sender = non_empty[0][:120]
            subject = non_empty[1][:200]
        elif len(non_empty) == 1:
            subject = non_empty[0][:200]
    if not subject:
        hdr_el = soup.select_one(".subject, .msg-subject, .title, .msg-title, b, strong, th")
        if hdr_el is not None:
            subject = " ".join(hdr_el.get_text(" ", strip=True).split())[:200]

    m_inner = re.search(
        r"(?:Message\s+from|Nachricht\s+von)\s+[^:]{1,60}:\s*(.+?)(?=\s*['\"]?\s*(?:Fleet\s+reports|Battle\s+reports|Espionage|System|User|Alliance)\s*[\u2605*]|\s*[\u2605*]\s*Favourites|\s*Received\s+on:\s*From:|$)",
        text,
        re.IGNORECASE,
    )
    inner_body = m_inner.group(1).strip() if m_inner else ""
    body_for_parse = inner_body if inner_body else text
    low = body_for_parse.lower()
    subj_low = subject.lower()

    coords_raw = _extract_spy_coords(body_for_parse) or _extract_spy_coords(text) or _extract_spy_link_coords(soup)
    norm_coords = _norm_coord_triple(coords_raw)
    if norm_coords and norm_coords in exclude_coords:
        # Check if there is a second target coordinate in the message (e.g. "from [own] to [target]")
        for m_br in _SPY_BRACKETED_COORDS_RE.finditer(body_for_parse):
            cand_n = _norm_coord_triple(f"{m_br.group(1)}:{m_br.group(2)}:{m_br.group(3)}")
            if cand_n and cand_n not in exclude_coords:
                norm_coords = cand_n
                break

    def _res_pair(lbl: str, src_txt: str = "") -> int:
        seg_t = src_txt or body_for_parse
        m = re.search(rf"{lbl}\s*:?\s*([\d.]+)", seg_t, re.IGNORECASE | re.ASCII)
        if m:
            return parse_si_int(m.group(1))
        m_rev = re.search(rf"([\d.]+)\s+(?:units?\s+of\s+)?{lbl}\b", seg_t, re.IGNORECASE | re.ASCII)
        return parse_si_int(m_rev.group(1)) if m_rev else 0

    category = "other"
    parsed_data: dict[str, Any] = {}
    spy_rep: Optional[SpyReport] = None
    f_low = (folder_type or "").strip().lower()

    battle_json_el = soup.find(attrs={"data-battle-json": True})
    bsum_el = soup.find(attrs={"data-bsum": True})

    is_counter_spy = any(
        tok in low or tok in subj_low
        for tok in ("foreign fleet", "fremde flotte", "counter-espionage", "spionageabwehr", "spied on", "ausspioniert")
    ) and not any(tok in low or tok in subj_low for tok in ("espionage report", "spionagebericht", "spy report"))

    is_fleet_return_notice = (
        any(
            tok in subj_low or tok in low
            for tok in (
                "fleet return",
                "flottenrückkehr",
                "flottenrueckkehr",
                "returning from duty",
                "were unloaded",
                "wurden ausgeladen",
            )
        )
        and "recycling report" not in subj_low
        and "trümmerfeldbericht" not in subj_low
    )

    is_fleet_station_notice = (
        not is_fleet_return_notice
        and not any(
            tok in subj_low or tok in low
            for tok in ("recycling report", "trümmerfeldbericht", "truemmerfeldbericht", "space rubble")
        )
        and (
            any(
                tok in subj_low or tok in low
                for tok in (
                    "was re-stationed",
                    "re-stationed",
                    "restationed",
                    "has been stationed",
                    "wurde stationiert",
                    "umstationiert",
                    "stationiert",
                    "it delivers",
                    "has delivered",
                )
            )
            or bool(re.search(r"\]\s*station\b", f"{subj_low} {low}"))
            or bool(re.search(r"\bstation\s*$", subj_low))
        )
    )

    is_battle = (
        f_low == "battle"
        or battle_json_el is not None
        or bsum_el is not None
        or any(
            tok in low or tok in subj_low
            for tok in (
                "battle report",
                "combat report",
                "kampfbericht",
                "attacker",
                "angreifer",
                "plundered",
                "looted",
                "erbeutet",
                "debris field",
                "trümmerfeld",
                "truemmerfeld",
            )
        )
    )
    is_recycle = (
        not is_battle
        and not is_fleet_return_notice
        and not is_fleet_station_notice
        and not is_non_recycle_fleet_notice_text(body_for_parse, subject)
        and any(
            tok in low or tok in subj_low
            for tok in (
                "recycling report",
                "trümmerfeldbericht",
                "truemmerfeldbericht",
                "space rubble",
                "pieces of rubble",
                "harvested",
                "recycled",
                "processed",
                "abbau",
                "verwertet",
                "trümmerfeld abgebaut",
                "couldn't find any more resources",
                "could not find any more resources",
            )
        )
        and not any(tok in low or tok in subj_low for tok in ("espionage report", "spionagebericht", "spy report"))
    )

    is_spy = (
        not is_counter_spy
        and not is_battle
        and not is_recycle
        and not is_fleet_return_notice
        and not is_fleet_station_notice
        and (
            f_low == "spio"
            or any(tok in low or tok in subj_low for tok in ("espionage report", "spionagebericht", "spy report", "uninhabited", "unbewohnt"))
            or (
                bool(norm_coords)
                and norm_coords not in exclude_coords
                and sum(1 for rk in ("pig iron", "roheisen", "metal", "metall", "kryptonite", "kryptonit", "spice") if rk in low) >= 2
            )
            or bool(re.search(r"\b[0-9]{2,3}\s*(?:building\s*|bau)?(?:fields|felder)\b", low))
        )
    )

    if is_spy:
        rep = parse_spy_report_html(block_html, target_coords=norm_coords or "")
        rep_norm = _norm_coord_triple(rep.target_coords)
        if rep_norm and rep_norm not in exclude_coords:
            category = "spy_report"
            norm_coords = rep_norm
            has_spy_payload = bool(
                body_complete
                or rep.uninhabited_fields is not None
                or rep.resources.total > 0
                or rep.buildings
                or rep.fleet
                or rep.defense
                or rep.researches
            )
            if has_spy_payload:
                spy_rep = rep
            parsed_data = {
                "target_coords": rep_norm,
                "owner": rep.owner,
                "planet_name": rep.planet_name,
                "is_spacia": rep.is_spacia,
                "resources": {
                    "pig_iron": rep.resources.pig_iron,
                    "metal": rep.resources.metal,
                    "kryptonite": rep.resources.kryptonite,
                    "spice": rep.resources.spice,
                },
                "buildings": dict(rep.buildings),
                "fleet": dict(rep.fleet),
                "defense": dict(rep.defense),
                "researches": dict(rep.researches),
                "dug_in": rep.dug_in,
                "uninhabited_fields": rep.uninhabited_fields,
            }
    elif is_counter_spy:
        category = "counter_spy"
        parsed_data = {"coords": norm_coords}
    elif is_battle:
        category = "battle_report"
        battle_id = str(bsum_el.get("data-bsum") or "").strip() if bsum_el is not None else ""
        if not battle_id:
            m_bid = re.search(r"showBattleReport\s*\(\s*['\"]?(\d+)['\"]?", block_html, re.IGNORECASE | re.ASCII)
            if m_bid:
                battle_id = m_bid.group(1)
        if not battle_id:
            m_bid_txt = re.search(r"Battle\s+report\s*#(\d+)", body_for_parse, re.IGNORECASE | re.ASCII)
            if m_bid_txt:
                battle_id = m_bid_txt.group(1)
        plunder_tail = body_for_parse
        m_ptail = re.search(r"(?:Plundered|Erbeutet)\s+(.+)$", body_for_parse, re.IGNORECASE)
        if m_ptail:
            plunder_tail = m_ptail.group(1)
        loot_dict = {
            "pig_iron": _res_pair(r"(?:Pig\s+iron|Roheisen)", plunder_tail),
            "metal": _res_pair(r"(?:Metal|Metall)", plunder_tail),
            "kryptonite": _res_pair(r"(?:Kryptonite|Kryptonit)", plunder_tail),
            "spice": _res_pair(r"Spice", plunder_tail),
        }
        debris_pig = 0
        debris_met = 0
        m_deb = re.search(
            r"(?:debris|rubble|trümmer|truemmer)[^.]{0,120}?(?:Pig\s+iron|Roheisen)\s*:?\s*([0-9][0-9.]*)[^.]{0,60}?(?:Metal|Metall)\s*:?\s*([0-9][0-9.]*)",
            body_for_parse,
            re.IGNORECASE,
        )
        if m_deb:
            debris_pig = parse_si_int(m_deb.group(1))
            debris_met = parse_si_int(m_deb.group(2))
        attacker_name = ""
        defender_name = ""
        outcome = "win"
        rounds = 0
        attacker_lost = 0
        defender_lost = 0
        m_att_txt = re.search(
            r"\bAttacker\s*:\s*([^|(]+?)\s*\(\s*Lost\s*:\s*([0-9][0-9.]*)\s*\)",
            body_for_parse,
            re.IGNORECASE,
        )
        if m_att_txt:
            attacker_name = m_att_txt.group(1).strip()[:120]
            attacker_lost = parse_si_int(m_att_txt.group(2))
        else:
            m_lost = re.search(r"\bLost\s*:?\s*([0-9][0-9.]*)", body_for_parse, re.IGNORECASE | re.ASCII)
            if m_lost:
                attacker_lost = parse_si_int(m_lost.group(1))
        m_def_txt = re.search(
            r"\bDefender\s*:\s*([^|(]+?)\s*\(\s*Lost\s*:\s*([0-9][0-9.]*)\s*\)",
            body_for_parse,
            re.IGNORECASE,
        )
        if m_def_txt:
            defender_name = m_def_txt.group(1).strip()[:120]
            defender_lost = parse_si_int(m_def_txt.group(2))
        else:
            m_dest = re.search(r"\bDestroyed\s*:?\s*([0-9][0-9.]*)", body_for_parse, re.IGNORECASE | re.ASCII)
            if m_dest:
                defender_lost = parse_si_int(m_dest.group(1))
        m_rnd = re.search(r"\bRounds\s*:?\s*([0-9]{1,3})\b", body_for_parse, re.IGNORECASE | re.ASCII)
        if m_rnd:
            rounds = int(m_rnd.group(1))
        m_win_code = re.search(r"\bWinner\s*:\s*([012])\b", body_for_parse, re.IGNORECASE | re.ASCII)
        if m_win_code:
            wc = int(m_win_code.group(1))
            outcome = "win" if wc == 1 else ("loss" if wc == 2 else "draw")
        elif any(tok in low for tok in ("defender has won", "verteidiger hat die schlacht gewonnen", "fleet was destroyed")):
            outcome = "loss"
        elif any(tok in low for tok in ("ended in a draw", "endet unentschieden", "draw")):
            outcome = "draw"

        loot_spice_eq = round(
            loot_dict["pig_iron"] / 6.0
            + loot_dict["metal"] / 2.5
            + loot_dict["kryptonite"] / 3.0
            + float(loot_dict["spice"]),
            2,
        )
        if loot_spice_eq <= 0.0:
            m_loot_tot = re.search(r"\bLooted\s*:?\s*([0-9][0-9.]*)", body_for_parse, re.IGNORECASE | re.ASCII)
            if m_loot_tot:
                loot_spice_eq = float(parse_si_int(m_loot_tot.group(1)))

        if battle_json_el is not None:
            try:
                bj = json.loads(str(battle_json_el.get("data-battle-json") or "{}"))
            except (ValueError, TypeError):
                bj = {}
            if isinstance(bj, dict) and bj.get("ok"):
                rounds = game_data.safe_int(bj.get("rounds"), rounds)
                win_code = game_data.safe_int(bj.get("winner"), 1)
                outcome = "win" if win_code == 1 else ("loss" if win_code == 2 else "draw")
                att_obj = bj.get("attacker") if isinstance(bj.get("attacker"), Mapping) else {}
                def_obj = bj.get("defender") if isinstance(bj.get("defender"), Mapping) else {}
                attacker_name = str(att_obj.get("name") or attacker_name).strip()[:120]
                defender_name = str(def_obj.get("name") or defender_name).strip()[:120]
                attacker_lost = _parse_embedded_unit_val(att_obj.get("lost")) if att_obj.get("lost") is not None else attacker_lost
                defender_lost = _parse_embedded_unit_val(def_obj.get("lost")) if def_obj.get("lost") is not None else defender_lost
                stolen_arr = bj.get("stolen")
                if isinstance(stolen_arr, list):
                    bj_loot = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
                    rmap = {1: "pig_iron", 2: "metal", 3: "kryptonite", 4: "spice"}
                    for item in stolen_arr:
                        if isinstance(item, Mapping):
                            rk = rmap.get(game_data.safe_int(item.get("r"), 0))
                            if rk:
                                bj_loot[rk] = max(0, _parse_embedded_unit_val(item.get("v")))
                    loot_dict = bj_loot
                    loot_spice_eq = round(
                        loot_dict["pig_iron"] / 6.0
                        + loot_dict["metal"] / 2.5
                        + loot_dict["kryptonite"] / 3.0
                        + float(loot_dict["spice"]),
                        2,
                    )
                if bj.get("stolenSpice") is not None and loot_spice_eq <= 0.0:
                    loot_spice_eq = float(max(0, _parse_embedded_unit_val(bj.get("stolenSpice"))))
                rubble_arr = bj.get("rubble")
                if isinstance(rubble_arr, list):
                    debris_pig = 0
                    debris_met = 0
                    for item in rubble_arr:
                        if isinstance(item, Mapping):
                            rc = game_data.safe_int(item.get("r"), 0)
                            if rc == 1:
                                debris_pig = max(0, _parse_embedded_unit_val(item.get("v")))
                            elif rc == 2:
                                debris_met = max(0, _parse_embedded_unit_val(item.get("v")))
        if not defender_name and "spacia" in low:
            defender_name = "Spacia"

        debris_spice_eq = round(debris_pig / 6.0 + debris_met / 2.5, 2)
        parsed_data = {
            "battle_id": battle_id,
            "coords": norm_coords,
            "attacker": attacker_name,
            "defender": defender_name,
            "outcome": outcome,
            "rounds": rounds,
            "loot": dict(loot_dict),
            "loot_spice_eq": loot_spice_eq,
            "resources_mentioned": dict(loot_dict),
            "attacker_lost_spice": attacker_lost,
            "defender_lost_spice": defender_lost,
            "debris": {"pig_iron": debris_pig, "metal": debris_met},
            "debris_spice_eq": debris_spice_eq,
            "has_debris": (debris_pig + debris_met) > 0,
        }
    elif is_recycle:
        category = "recycle_report"
        # Prefer target rubble coordinate in subject (`Space rubble [02:383:14] recycling report`) or `goal ... [02:383:14]`
        m_subj_c = _SPY_BRACKETED_COORDS_RE.search(subject)
        if m_subj_c:
            cand_sc = _norm_coord_triple(f"{m_subj_c.group(1)}:{m_subj_c.group(2)}:{m_subj_c.group(3)}")
            if cand_sc and cand_sc not in exclude_coords:
                norm_coords = cand_sc
        else:
            m_goal_c = re.search(
                r"(?:goal|ziel|at|bei)\s+[^\[\]]{0,60}\[\s*(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})\s*\]",
                body_for_parse,
                re.IGNORECASE,
            )
            if m_goal_c:
                cand_gc = _norm_coord_triple(f"{m_goal_c.group(1)}:{m_goal_c.group(2)}:{m_goal_c.group(3)}")
                if cand_gc and cand_gc not in exclude_coords:
                    norm_coords = cand_gc
        rec_count = 0
        m_rc = re.search(r"\b([0-9][0-9.]*)\s*Recycler\b", body_for_parse, re.IGNORECASE | re.ASCII)
        if m_rc:
            rec_count = parse_si_int(m_rc.group(1))
        cargo_cap = 0
        m_cap = re.search(
            r"(?:capacity|ladekapazit[aä]t|storage)[^0-9]{0,30}([0-9][0-9.]*)",
            body_for_parse,
            re.IGNORECASE,
        )
        if m_cap:
            cargo_cap = parse_si_int(m_cap.group(1))

        # Prefer numbers appearing after `harvested` / `processed` / `collected` / `abgebaut`
        harv_tail = body_for_parse
        m_htail = re.search(
            r"(?:harvested|collected|recycled|gathered|processed|abgebaut|gesammelt|verwertet)(.+)$",
            body_for_parse,
            re.IGNORECASE,
        )
        if m_htail:
            harv_tail = m_htail.group(1)

        def _extract_res_from_segment(seg: str, lbl: str) -> int:
            m1 = re.search(rf"{lbl}\s*:?\s*([\d.]+)", seg, re.IGNORECASE | re.ASCII)
            if m1:
                return parse_si_int(m1.group(1))
            m2 = re.search(rf"([\d.]+)\s*(?:units?\s+of\s+)?{lbl}\b", seg, re.IGNORECASE | re.ASCII)
            return parse_si_int(m2.group(1)) if m2 else 0

        harv_pig = _extract_res_from_segment(harv_tail, r"(?:Pig\s+iron|Roheisen)")
        harv_met = _extract_res_from_segment(harv_tail, r"(?:Metal|Metall)")
        harv_kry = _extract_res_from_segment(harv_tail, r"(?:Kryptonite|Kryptonit)")
        harv_spi = _extract_res_from_segment(harv_tail, r"Spice")
        if (harv_pig + harv_met + harv_kry + harv_spi) == 0 and harv_tail != body_for_parse:
            harv_pig = _extract_res_from_segment(body_for_parse, r"(?:Pig\s+iron|Roheisen)")
            harv_met = _extract_res_from_segment(body_for_parse, r"(?:Metal|Metall)")
            harv_kry = _extract_res_from_segment(body_for_parse, r"(?:Kryptonite|Kryptonit)")
            harv_spi = _extract_res_from_segment(body_for_parse, r"Spice")

        avail_pig = harv_pig
        avail_met = harv_met
        if m_htail:
            head_seg = body_for_parse[: m_htail.start()]
            ap = _extract_res_from_segment(head_seg, r"(?:Pig\s+iron|Roheisen)")
            am = _extract_res_from_segment(head_seg, r"(?:Metal|Metall)")
            if ap > 0 or am > 0:
                avail_pig, avail_met = ap, am

        harv_spice_eq = round(harv_pig / 6.0 + harv_met / 2.5 + harv_kry / 3.0 + float(harv_spi), 2)
        parsed_data = {
            "coords": norm_coords,
            "recycler_count": rec_count,
            "cargo_capacity": cargo_cap,
            "debris_available": {"pig_iron": avail_pig, "metal": avail_met},
            "harvested": {
                "pig_iron": harv_pig,
                "metal": harv_met,
                "kryptonite": harv_kry,
                "spice": harv_spi,
            },
            "harvested_spice_eq": harv_spice_eq,
        }
    elif any(tok in low or tok in subj_low for tok in ("coloniz", "kolonisier")):
        category = "colonize_report"
        parsed_data = {"coords": norm_coords}
    elif is_fleet_station_notice or (
        not is_fleet_return_notice
        and any(tok in low or tok in subj_low for tok in ("transport", "delivered", "geliefert", "liefert"))
    ):
        category = "fleet_transport"
        parsed_data = {
            "coords": norm_coords,
            "resources": {
                "pig_iron": _res_pair(r"(?:Pig\s+iron|Roheisen)"),
                "metal": _res_pair(r"(?:Metal|Metall)"),
                "kryptonite": _res_pair(r"(?:Kryptonite|Kryptonit)"),
                "spice": _res_pair(r"Spice"),
            },
        }
    elif is_fleet_return_notice or f_low == "fleet" or any(tok in low or tok in subj_low for tok in ("fleet", "flotte", "returned", "zurückgekehrt", "escape flight", "tarnflug")):
        category = "fleet_return"
        parsed_data = {
            "coords": norm_coords,
            "unloaded": {
                "pig_iron": _res_pair(r"(?:Pig\s+iron|Roheisen)"),
                "metal": _res_pair(r"(?:Metal|Metall)"),
                "kryptonite": _res_pair(r"(?:Kryptonite|Kryptonit)"),
                "spice": _res_pair(r"Spice"),
            },
        }
    elif f_low in ("normal", "alliance"):
        category = "player"
    elif f_low == "admin":
        category = "system"

    clean_text = redact_sensitive(inner_body if inner_body else text)[:8000]
    clean_html = redact_sensitive(block_html)[:16000]
    clean_sender = redact_sensitive(sender)[:120]
    clean_subject = redact_sensitive(subject)[:240]

    if server_msg_id:
        msg_uid = f"id:{server_msg_id}"
    else:
        digest = hashlib.sha256(
            f"{f_low}|{category}|{msg_date_text}|{clean_sender}|{clean_subject}|{norm_coords}|{clean_text}".encode("utf-8")
        ).hexdigest()[:24]
        msg_uid = f"sha:{digest}"

    return InboxMessage(
        msg_uid=msg_uid,
        server_msg_id=server_msg_id,
        folder_type=f_low or "inbox",
        category=category,
        msg_date_text=msg_date_text,
        sender=clean_sender,
        subject=clean_subject,
        coords=norm_coords,
        body_text=clean_text,
        raw_html=clean_html,
        parsed_data=parsed_data,
        spy_report=spy_rep,
        body_complete=body_complete,
    )


SetLike = Collection[str]


def parse_inbox_messages_html(
    html: str,
    *,
    folder_type: str = "",
    exclude_coords: Iterable[str] = (),
) -> tuple[InboxMessage, ...]:
    """Parse all messages (espionage, battle, recycle, fleet, system, player) from `internalMessages` HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    _strip_own_planet_header_dom(soup)
    excl_norm = {_norm_coord_triple(c) for c in (exclude_coords or ()) if _norm_coord_triple(c)}

    raw_blocks: list[str] = []
    # 1. Explicit `.message` / `.msg` / `id^="msg"` containers (excluding folder counters)
    containers = [
        el
        for el in soup.select(
            "div.message, div.msg, tr.message, tr.msg, tr[id^='msg'], div[id^='msg'], [data-msg-id], [data-message-id]"
        )
        if not _is_folder_counter_node(el)
    ]
    if containers:
        for el in containers:
            if el.name == "tr":
                nxt = el.find_next_sibling("tr")
                if nxt is not None and nxt not in containers and not _extract_element_msg_id(nxt):
                    raw_blocks.append(f"<table>{el}{nxt}</table>")
                    continue
            raw_blocks.append(str(el))

    # 2. Multi-row checkbox-anchored `<table>` messages
    if not raw_blocks:
        raw_blocks.extend(_group_table_message_blocks(soup))

    # 3. Fallback: standalone spy/battle/recycle/fleet blocks when no message classes or checkboxes exist
    if not raw_blocks:
        for rep in parse_spy_messages_html(str(soup), exclude_coords=excl_norm):
            raw_blocks.append(
                f"<div class='msg'>{rep.raw_text or rep.target_coords}</div>"
            )

    messages: list[InboxMessage] = []
    seen_uids: set[str] = set()
    for blk in raw_blocks:
        msg = _classify_and_parse_inbox_block(
            blk,
            folder_type=folder_type,
            exclude_coords=excl_norm,
        )
        if msg is None or msg.msg_uid in seen_uids:
            continue
        # Ignore folder-index navigation rows (e.g. `Espionage (3)` links on the folder overview screen)
        blk_soup = BeautifulSoup(blk, "html.parser")
        folder_anchors = [
            a
            for a in blk_soup.find_all("a", href=True)
            if "action=internalmessages" in str(a.get("href") or "").lower()
            and re.search(r"[?&](?:type|cat|category)=", str(a.get("href") or ""), re.IGNORECASE)
            and "read=" not in str(a.get("href") or "").lower()
        ]
        if folder_anchors and not msg.server_msg_id and msg.spy_report is None and len(msg.body_text) < 120:
            continue
        seen_uids.add(msg.msg_uid)
        messages.append(msg)
    return tuple(messages)


def score_raid_target(
    origin_coords: str,
    slot: GalaxySlot,
    report: SpyReport,
    attacker_ships: Mapping[str, int],
    *,
    attacker_researches: Optional[Mapping[str, int]] = None,
    bash_tracker: Optional[BashTracker] = None,
    safety_margin: float = 1.4,
    min_loot_spice_eq: float = 1500.0,
    allow_pvp_in_az: bool = False,
    timestamp: Optional[float] = None,
) -> Optional[RaidRecommendation]:
    """Score a scanned target for safe raiding (Yellow/Spacia default; PvP opt-in)."""
    is_inactive = slot.status_color == "yellow"
    is_spacia = slot.is_spacia or report.is_spacia
    if not (is_inactive or is_spacia):
        if not (allow_pvp_in_az and slot.status_color == "light_red"):
            return None
    if bash_tracker is not None and not bash_tracker.can_attack(
        slot.coords,
        is_inactive=is_inactive,
        is_spacia=is_spacia,
        timestamp=timestamp,
    ):
        return None

    combat = simulate_raid_outcome(
        attacker_ships,
        report,
        attacker_researches=attacker_researches,
        safety_margin=safety_margin,
    )
    if not combat.safe_to_attack or combat.loot_spice_equivalent < min_loot_spice_eq:
        return None

    dist = _parse_coords_distance(origin_coords, slot.coords)
    # Wiki Spacia insight: slightly further/overlooked Spacia worlds upgrade mines higher
    bonus = 1.15 if (is_spacia and dist > 1020) else 1.0
    score = (combat.loot_spice_equivalent * bonus) / max(1.0, dist / 1000.0)
    total_plunder = (
        max(0, int(combat.expected_loot.pig_iron))
        + max(0, int(combat.expected_loot.metal))
        + max(0, int(combat.expected_loot.kryptonite))
        + max(0, int(combat.expected_loot.spice))
    )
    flight_s = estimate_fleet_flight_seconds(
        origin_coords,
        slot.coords,
        attacker_ships,
        researches=attacker_researches,
        round_trip=True,
    )
    res_per_h = estimate_resource_per_hour(total_plunder, flight_s)
    return RaidRecommendation(
        target_coords=slot.coords,
        target_owner=slot.owner or report.owner,
        mission="ATTACK",
        distance_score=score,
        loot_spice_equivalent=combat.loot_spice_equivalent,
        combat=combat,
        is_spacia=is_spacia,
        is_inactive=is_inactive,
        total_plunder_resources=total_plunder,
        flight_seconds=flight_s,
        resources_per_hour=res_per_h,
    )


def grade_colony_candidate(
    first_coords: str = "",
    second_coords: Optional[str] = None,
    *,
    coords: Optional[str] = None,
    origin_coords: str = "",
    position: Optional[int] = None,
    uninhabited_fields: Optional[int] = None,
    colonize_min_fields: Optional[int] = None,
    colonize_max_fields: Optional[int] = None,
    temperature: Optional[int] = None,
    spied: bool = False,
    min_fields_target: int = 300,
    last_spied_ts: float = 0.0,
    last_scanned_ts: float = 0.0,
) -> dict[str, Any]:
    """Grade an uninhabited/free galaxy slot (`S`..`D` when spied/scouted, `?` when unprobed)."""
    if coords is not None:
        eff_coords = coords
        eff_origin = origin_coords or first_coords
    elif second_coords is not None:
        eff_origin = origin_coords or first_coords
        eff_coords = second_coords
    else:
        eff_coords = first_coords
        eff_origin = origin_coords

    norm_c = normalize_coords(eff_coords)
    m = re.fullmatch(r"(\d+):(\d+):(\d+)", norm_c, re.ASCII)
    gal = int(m.group(1)) if m else 0
    sys_num = int(m.group(2)) if m else 0
    pos = int(position) if (position is not None and 1 <= int(position) <= 16) else (int(m.group(3)) if m else 0)

    norm_orig = normalize_coords(eff_origin) if eff_origin else norm_c
    m_orig = re.fullmatch(r"(\d+):(\d+):(\d+)", norm_orig, re.ASCII)
    orig_gal = int(m_orig.group(1)) if m_orig else gal
    orig_sys = int(m_orig.group(2)) if m_orig else sys_num
    sys_dist = abs(gal - orig_gal) * 400 + abs(sys_num - orig_sys) if (gal > 0 and sys_num > 0) else 0

    dist = _parse_coords_distance(norm_orig or norm_c, norm_c) if norm_c else 0.0
    dist_penalty = min(5, int(max(0.0, dist - 1000.0) / 800.0))
    min_tgt = max(50, min(600, game_data.safe_int(min_fields_target, 300)))

    if 7 <= pos <= 12:
        zone_label = "Prime Large-Field Belt (Pos 7-12)"
        est_temp = "10°C .. 55°C"
    elif 13 <= pos <= 16:
        zone_label = "Cold Spice Belt (Pos 13-16)"
        est_temp = "-70°C .. 5°C"
    elif 4 <= pos <= 6:
        zone_label = "Temperate Mid Belt (Pos 4-6)"
        est_temp = "55°C .. 95°C"
    else:
        zone_label = "Hot Inner Core (Pos 1-3)"
        est_temp = "100°C .. 220°C"

    fields_val = game_data.safe_optional_int(uninhabited_fields, None)
    if fields_val is not None and fields_val <= 0:
        fields_val = None
    c_min_val = game_data.safe_optional_int(colonize_min_fields, None)
    if c_min_val is not None and c_min_val <= 0:
        c_min_val = None
    c_max_val = game_data.safe_optional_int(colonize_max_fields, None)
    if c_max_val is not None and c_max_val <= 0:
        c_max_val = None
    if c_min_val is not None and c_max_val is not None and c_min_val > c_max_val:
        c_min_val, c_max_val = c_max_val, c_min_val
    temp_val = game_data.safe_optional_int(temperature, None)
    if temp_val is not None and not (-300 <= temp_val <= 600):
        temp_val = None

    has_range_ecology = c_max_val is not None
    is_spied = bool(spied or fields_val is not None or has_range_ecology or last_spied_ts > 0.0)
    is_probed = bool((is_spied and fields_val is not None) or has_range_ecology)
    fields_display = ""

    if fields_val is not None:
        fields_display = f"{fields_val} fields"
        if fields_val >= 310:
            grade = "S"
            base_score = min(100, 95 + (fields_val - 310) // 4)
        elif fields_val >= 300:
            grade = "A+"
            base_score = 90 + min(4, int((fields_val - 300) * 0.5))
        elif fields_val >= 270:
            grade = "A"
            base_score = 78 + min(11, int((fields_val - 270) * 0.4))
        elif fields_val >= 230:
            grade = "B"
            base_score = 62 + min(15, int((fields_val - 230) * 0.4))
        elif fields_val >= 180:
            grade = "C"
            base_score = 40 + min(21, int((fields_val - 180) * 0.42))
        else:
            grade = "D"
            base_score = max(5, min(39, int(fields_val * 0.22)))
        score = max(1, min(100, base_score - dist_penalty))
        meets_target = fields_val >= min_tgt
        recommendation = "COLONIZE_NOW" if meets_target else "SKIP_TOO_SMALL"
        if meets_target:
            reason = f"Confirmed {fields_val} fields >= {min_tgt} target ({zone_label}) — ready to colonize"
        else:
            reason = f"Confirmed {fields_val} fields < {min_tgt} target — skip for {min_tgt}+ end-game focus"
    elif has_range_ecology and c_max_val is not None:
        eff_min_f = c_min_val if (c_min_val is not None and c_min_val <= c_max_val) else c_max_val
        fields_display = f"{eff_min_f}–{c_max_val} fields" if eff_min_f != c_max_val else f"{c_max_val} fields"
        meets_target = c_max_val >= min_tgt
        if c_max_val >= 340:
            grade = "S" if meets_target else "B"
            base_score = min(100, 95 + max(0, (eff_min_f - 130) // 5) + max(0, (c_max_val - 340) // 5))
        elif c_max_val >= 310:
            grade = "A+" if meets_target else "B"
            base_score = 90 + min(4, max(0, (c_max_val - 310) // 8))
        elif c_max_val >= 300:
            grade = "A" if meets_target else "B"
            base_score = 84 + min(5, max(0, (c_max_val - 300) // 2))
        elif c_max_val >= 270:
            grade = "A" if meets_target else "B"
            base_score = 68 + min(12, int((c_max_val - 270) * 0.4))
        elif c_max_val >= 230:
            grade = "B" if meets_target else "C"
            base_score = 50 + min(15, int((c_max_val - 230) * 0.35))
        else:
            grade = "D"
            base_score = max(10, min(44, int(c_max_val * 0.18)))
        score = max(1, min(100, base_score - dist_penalty))
        recommendation = "COLONIZE_NOW" if meets_target else "SKIP_TOO_SMALL"
        temp_suffix = f", {temp_val}°C" if temp_val is not None else ""
        if meets_target:
            reason = (
                f"Scouted {eff_min_f}–{c_max_val} fields (max {c_max_val} >= {min_tgt} target{temp_suffix}) "
                "— ready to colonize"
            )
        else:
            reason = (
                f"Scouted {eff_min_f}–{c_max_val} fields (max {c_max_val} < {min_tgt} target{temp_suffix}) "
                f"— skip for {min_tgt}+ end-game focus"
            )
    else:
        grade = "?"
        if 7 <= pos <= 12:
            pot = 82 - int(abs(pos - 9.5) * 3.0)
            recommendation = "PROBE_PRIME_SLOT"
            reason = f"Unprobed prime slot (pos {pos}) — highest statistical odds for {min_tgt}+ fields"
        elif 4 <= pos <= 6:
            pot = 56 - abs(pos - 5) * 3
            recommendation = "PROBE_SECONDARY"
            reason = f"Unprobed temperate slot (pos {pos}) — moderate field odds; probe after pos 7-12"
        elif 13 <= pos <= 16:
            pot = 48 - abs(pos - 14) * 2
            recommendation = "PROBE_COLD_SPICE"
            reason = f"Unprobed cold Spice slot (pos {pos}) — bonus Spice output, typically smaller field roll"
        else:
            pot = 30
            recommendation = "LOW_FIELD_ODDS"
            reason = f"Unprobed hot inner slot (pos {pos}) — low probability of {min_tgt}+ fields"
        score = max(5, min(85, pot - dist_penalty))
        meets_target = False

    return {
        "coords": norm_c,
        "galaxy": gal,
        "system": sys_num,
        "position": pos,
        "spied": is_probed,
        "probed": is_probed,
        "uninhabited_fields": fields_val,
        "colonize_min_fields": c_min_val,
        "colonize_max_fields": c_max_val,
        "temperature": temp_val,
        "fields_display": fields_display,
        "grade": grade,
        "score": int(score),
        "meets_target": bool(meets_target),
        "min_fields_target": min_tgt,
        "zone_label": zone_label,
        "orbital_zone": zone_label,
        "orbital_band": f"{zone_label} ({temp_val}°C)" if temp_val is not None else f"{zone_label} ({est_temp})",
        "est_temp_range": est_temp,
        "distance": round(float(dist), 1),
        "system_distance": int(sys_dist),
        "recommendation": recommendation,
        "reason": reason,
        "last_spied_ts": float(last_spied_ts or 0.0),
        "last_scanned_ts": float(last_scanned_ts or 0.0),
    }


def pick_colony_candidates(
    slots: tuple[GalaxySlot, ...],
    *,
    goal: str = "large_fields",
    min_fields_target: int = 300,
    uninhabited_reports: Optional[Mapping[str, SpyReport]] = None,
) -> list[GalaxySlot]:
    """Filter & rank free galaxy slots for large economy colonies (pos 7-12, 300+ fields) or cold Spice (13-16)."""
    reports = uninhabited_reports or {}
    free = [s for s in slots if s.status_color == "free"]
    filtered: list[tuple[float, GalaxySlot]] = []

    for slot in free:
        rep = reports.get(slot.coords)
        if rep and rep.uninhabited_fields is not None and rep.uninhabited_fields < min_fields_target:
            if goal != "cold_spice":
                continue
        if goal == "cold_spice":
            # Edge cold positions 13-16 yield maximum Spice
            pos_score = 100.0 - abs(slot.position - 15) * 15.0
        else:
            # Mid-outer slots 7-12 offer best statistical odds for >=300 field rolls
            pos_score = 100.0 - abs(slot.position - 9.5) * 12.0
        if rep and rep.uninhabited_fields:
            pos_score += rep.uninhabited_fields * 0.25
        filtered.append((pos_score, slot))

    filtered.sort(key=lambda pair: (-pair[0], pair[1].position))
    return [slot for _, slot in filtered]


def _parse_gal_sys_pair(raw: Any) -> Optional[tuple[int, int]]:
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        g = _safe_fleet_int(raw[0], 0)
        s = _safe_fleet_int(raw[1], 0)
        if 1 <= g <= 14 and 1 <= s <= 400:
            return (g, s)
        return None
    txt = game_data.safe_key(raw).strip() if raw is not None else ""
    if not txt:
        return None
    m = re.search(r"(\d{1,2})\s*[:/\-]\s*(\d{1,3})", txt, re.ASCII)
    if not m:
        return None
    g = int(m.group(1))
    s = int(m.group(2))
    if 1 <= g <= 14 and 1 <= s <= 400:
        return (g, s)
    return None


def _safe_scan_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        s = value.strip()
        if not s or not s.isascii():
            return default
        try:
            f = float(s)
            return f if math.isfinite(f) else default
        except Exception:
            return default
    return default


def galaxy_sector_distance(
    galaxy: int,
    system: int,
    own_colonies: Sequence[tuple[int, int]],
    *,
    systems_per_galaxy: int = 400,
) -> int:
    """Return minimum circular system distance (`min(|s-own_s|, N-|s-own_s|)`) or cross-galaxy penalized distance."""
    n_sys = max(1, _safe_fleet_int(systems_per_galaxy, 400))
    if not own_colonies:
        return 0
    best: Optional[int] = None
    for col_g, col_s in own_colonies:
        delta = abs(int(system) - int(col_s))
        circ = min(delta, max(0, n_sys - delta))
        dist = circ if int(col_g) == int(galaxy) else (250 * abs(int(galaxy) - int(col_g)) + circ)
        if best is None or dist < best:
            best = dist
    return best if best is not None else 0


ARROW_NAV_MAX_DISTANCE = 5


def _circular_system_delta(
    from_sys: int,
    to_sys: int,
    *,
    systems_per_galaxy: int = 400,
) -> tuple[int, int]:
    """Return `(shortest_circular_distance, direction)` where `direction` is `+1`, `-1`, or `0`."""
    n_sys = max(1, _safe_fleet_int(systems_per_galaxy, 400))
    fwd = (int(to_sys) - int(from_sys)) % n_sys
    bwd = (int(from_sys) - int(to_sys)) % n_sys
    if fwd == 0:
        return 0, 0
    if fwd <= bwd:
        return fwd, 1
    return bwd, -1


def _score_walk_sequence(
    seq: Sequence[int],
    *,
    start_sys: Optional[int],
    systems_per_galaxy: int,
    arrow_max_distance: int,
) -> tuple[int, int, int]:
    """Score a within-galaxy system walk by `(non_arrow_jumps, total_steps, first_step_dist)`."""
    if not seq:
        return (0, 0, 0)
    non_arrow = 0
    total_steps = 0
    first_dist = 0
    prev = start_sys
    if prev is not None:
        first_dist, _ = _circular_system_delta(prev, seq[0], systems_per_galaxy=systems_per_galaxy)
        if first_dist >= arrow_max_distance:
            non_arrow += 1
        total_steps += first_dist
    for idx in range(len(seq) - 1):
        d, _ = _circular_system_delta(seq[idx], seq[idx + 1], systems_per_galaxy=systems_per_galaxy)
        if d >= arrow_max_distance:
            non_arrow += 1
        total_steps += d
    return (non_arrow, total_steps, first_dist)


def order_sectors_for_smooth_walk(
    sectors: Sequence[Mapping[str, Any]],
    *,
    primary_colony_by_galaxy: Optional[Mapping[int, int]] = None,
    current_cursor: Optional[tuple[int, int]] = None,
    systems_per_galaxy: int = 400,
    arrow_max_distance: int = ARROW_NAV_MAX_DISTANCE,
) -> list[dict[str, Any]]:
    """Order selected `(galaxy, system)` sector dicts along the smoothest arrow-friendly circular walk per galaxy."""
    n_sys = max(1, _safe_fleet_int(systems_per_galaxy, 400))
    eff_arrow_max = max(2, _safe_fleet_int(arrow_max_distance, ARROW_NAV_MAX_DISTANCE))
    by_gal: dict[int, dict[int, dict[str, Any]]] = {}
    gal_order: list[int] = []
    for item in sectors:
        if not isinstance(item, Mapping):
            continue
        g = _safe_fleet_int(item.get("galaxy"), 0)
        s = _safe_fleet_int(item.get("system"), 0)
        if not (1 <= g <= 14 and 1 <= s <= n_sys):
            continue
        if g not in by_gal:
            by_gal[g] = {}
            gal_order.append(g)
        if s not in by_gal[g]:
            by_gal[g][s] = dict(item)

    cur_g: Optional[int] = None
    cur_s: Optional[int] = None
    if (
        isinstance(current_cursor, (tuple, list))
        and len(current_cursor) == 2
    ):
        cg_v = _safe_fleet_int(current_cursor[0], 0)
        cs_v = _safe_fleet_int(current_cursor[1], 0)
        if 1 <= cg_v <= 14 and 1 <= cs_v <= n_sys:
            cur_g, cur_s = cg_v, cs_v

    if cur_g is not None and cur_g in by_gal and gal_order and gal_order[0] != cur_g:
        gal_order = [cur_g] + [g for g in gal_order if g != cur_g]

    col_map = primary_colony_by_galaxy if isinstance(primary_colony_by_galaxy, Mapping) else {}
    ordered: list[dict[str, Any]] = []
    for g in gal_order:
        sys_map = by_gal[g]
        sys_sorted = sorted(sys_map.keys())
        k = len(sys_sorted)
        if k <= 1:
            ordered.extend(sys_map[s] for s in sys_sorted)
            continue
        anchor_sys = _safe_fleet_int(col_map.get(g), sys_sorted[0])
        if not (1 <= anchor_sys <= n_sys):
            anchor_sys = sys_sorted[0]
        if k >= n_sys:
            if cur_g == g and cur_s is not None and cur_s in sys_map:
                start_idx = sys_sorted.index(cur_s)
            else:
                start_idx = sys_sorted.index(anchor_sys) if anchor_sys in sys_map else 0
        else:
            best_gap_key: Optional[tuple[int, int, int]] = None
            start_idx = 0
            for idx in range(k):
                cur_slot = sys_sorted[idx]
                nxt_s = sys_sorted[(idx + 1) % k]
                gap = (nxt_s - cur_slot) if idx + 1 < k else ((nxt_s + n_sys) - cur_slot)
                delta_anchor = abs(nxt_s - anchor_sys)
                circ_anchor = min(delta_anchor, max(0, n_sys - delta_anchor))
                cand_key = (gap, -circ_anchor, -nxt_s)
                if best_gap_key is None or cand_key > best_gap_key:
                    best_gap_key = cand_key
                    start_idx = (idx + 1) % k
        arc_fwd = [sys_sorted[(start_idx + offset) % k] for offset in range(k)]

        if cur_g == g and cur_s is not None:
            arc_bwd = list(reversed(arc_fwd))
            # Also evaluate a cursor-anchored nearest-neighbor walk that prefers `< arrow_max_distance` steps.
            rem_set = set(sys_sorted)
            nn_walk: list[int] = []
            head = cur_s
            pref_dir = 1
            while rem_set:
                chosen: Optional[int] = None
                for step_d in range(0, eff_arrow_max):
                    if step_d == 0:
                        if head in rem_set:
                            chosen = head
                            break
                        continue
                    c_pref = ((head - 1 + pref_dir * step_d) % n_sys) + 1
                    if c_pref in rem_set:
                        chosen = c_pref
                        break
                    c_opp = ((head - 1 - pref_dir * step_d) % n_sys) + 1
                    if c_opp in rem_set:
                        chosen = c_opp
                        pref_dir = -pref_dir
                        break
                if chosen is None:
                    best_s: Optional[int] = None
                    best_k: Optional[tuple[int, int, int]] = None
                    for cand in rem_set:
                        cd, cdir = _circular_system_delta(head, cand, systems_per_galaxy=n_sys)
                        ck = (cd, 0 if cdir == pref_dir else 1, cand)
                        if best_k is None or ck < best_k:
                            best_k = ck
                            best_s = cand
                    chosen = best_s
                    if chosen is not None:
                        _, cdir = _circular_system_delta(head, chosen, systems_per_galaxy=n_sys)
                        if cdir != 0:
                            pref_dir = cdir
                assert chosen is not None
                rem_set.remove(chosen)
                nn_walk.append(chosen)
                head = chosen

            best_seq = min(
                (arc_fwd, arc_bwd, nn_walk),
                key=lambda seq: _score_walk_sequence(
                    seq,
                    start_sys=cur_s,
                    systems_per_galaxy=n_sys,
                    arrow_max_distance=eff_arrow_max,
                ),
            )
            ordered.extend(sys_map[s_val] for s_val in best_seq)
        else:
            ordered.extend(sys_map[s_val] for s_val in arc_fwd)
    return ordered


def _select_contiguous_exploratory_sectors(
    unscanned_pool: Sequence[dict[str, Any]],
    target_count: int,
    *,
    colony_gal_set: set[int],
    current_cursor: Optional[tuple[int, int]] = None,
    systems_per_galaxy: int = 400,
    arrow_max_distance: int = ARROW_NAV_MAX_DISTANCE,
) -> list[dict[str, Any]]:
    """Select up to `target_count` unscanned sectors favoring contiguous `< arrow_max_distance` runs for arrow navigation."""
    if target_count <= 0 or not unscanned_pool:
        return []
    if len(unscanned_pool) <= target_count:
        return list(unscanned_pool)

    n_sys = max(1, _safe_fleet_int(systems_per_galaxy, 400))
    eff_arrow_max = max(2, _safe_fleet_int(arrow_max_distance, ARROW_NAV_MAX_DISTANCE))

    cur_g: Optional[int] = None
    cur_s: Optional[int] = None
    if isinstance(current_cursor, (tuple, list)) and len(current_cursor) == 2:
        cg_v = _safe_fleet_int(current_cursor[0], 0)
        cs_v = _safe_fleet_int(current_cursor[1], 0)
        if 1 <= cg_v <= 14 and 1 <= cs_v <= n_sys:
            cur_g, cur_s = cg_v, cs_v

    tiers: list[list[tuple[int, dict[str, Any]]]] = [[], []]
    for rank_idx, item in enumerate(unscanned_pool):
        g = _safe_fleet_int(item.get("galaxy"), 0)
        tiers[0 if g in colony_gal_set else 1].append((rank_idx, item))

    picked: list[dict[str, Any]] = []
    for tier_items in tiers:
        if len(picked) >= target_count or not tier_items:
            continue
        by_gal: dict[int, dict[int, tuple[int, dict[str, Any]]]] = {}
        gal_priority_order: list[int] = []
        for rank_idx, item in tier_items:
            g = _safe_fleet_int(item.get("galaxy"), 0)
            s = _safe_fleet_int(item.get("system"), 0)
            if not (1 <= g <= 14 and 1 <= s <= n_sys):
                continue
            if g not in by_gal:
                by_gal[g] = {}
                gal_priority_order.append(g)
            if s not in by_gal[g]:
                by_gal[g][s] = (rank_idx, item)

        if cur_g is not None and cur_g in by_gal:
            gal_priority_order = [cur_g] + [g for g in gal_priority_order if g != cur_g]

        for g in gal_priority_order:
            if len(picked) >= target_count:
                break
            rem = by_gal[g]
            if not rem:
                continue

            if cur_g == g and cur_s is not None:
                # Anchor at the unscanned system closest to the live browser cursor and march unidirectionally.
                s0 = min(
                    rem.keys(),
                    key=lambda s: (
                        _circular_system_delta(cur_s, s, systems_per_galaxy=n_sys)[0],
                        0 if _circular_system_delta(cur_s, s, systems_per_galaxy=n_sys)[1] >= 0 else 1,
                        rem[s][0],
                    ),
                )
                _, init_dir = _circular_system_delta(cur_s, s0, systems_per_galaxy=n_sys)
                pref_dir = init_dir if init_dir != 0 else 1
                _, item0 = rem.pop(s0)
                picked.append(item0)
                head = s0

                while len(picked) < target_count and rem:
                    nxt: Optional[int] = None
                    for step_d in range(1, eff_arrow_max):
                        cand_p = ((head - 1 + pref_dir * step_d) % n_sys) + 1
                        if cand_p in rem:
                            nxt = cand_p
                            break
                    if nxt is None:
                        for step_d in range(1, eff_arrow_max):
                            cand_o = ((head - 1 - pref_dir * step_d) % n_sys) + 1
                            if cand_o in rem:
                                nxt = cand_o
                                pref_dir = -pref_dir
                                break
                    if nxt is None:
                        nxt = min(
                            rem.keys(),
                            key=lambda s: (
                                _circular_system_delta(head, s, systems_per_galaxy=n_sys)[0],
                                0 if _circular_system_delta(head, s, systems_per_galaxy=n_sys)[1] == pref_dir else 1,
                                rem[s][0],
                            ),
                        )
                        _, jump_dir = _circular_system_delta(head, nxt, systems_per_galaxy=n_sys)
                        if jump_dir != 0:
                            pref_dir = jump_dir
                    _, item_n = rem.pop(nxt)
                    picked.append(item_n)
                    head = nxt
                cur_g = None
            else:
                # Grow a contiguous arc from the highest-priority unscanned seed in this galaxy.
                s0 = min(rem.keys(), key=lambda s: rem[s][0])
                _, item0 = rem.pop(s0)
                picked.append(item0)
                arc_left = s0
                arc_right = s0

                while len(picked) < target_count and rem:
                    r_cand: Optional[tuple[int, int]] = None
                    l_cand: Optional[tuple[int, int]] = None
                    for step_d in range(1, eff_arrow_max):
                        c_r = ((arc_right - 1 + step_d) % n_sys) + 1
                        if c_r in rem:
                            r_cand = (step_d, c_r)
                            break
                    for step_d in range(1, eff_arrow_max):
                        c_l = ((arc_left - 1 - step_d) % n_sys) + 1
                        if c_l in rem:
                            l_cand = (step_d, c_l)
                            break

                    if r_cand is not None and l_cand is not None:
                        r_d, r_s = r_cand
                        l_d, l_s = l_cand
                        r_rank, r_item = rem[r_s]
                        l_rank, l_item = rem[l_s]
                        r_key = (_safe_fleet_int(r_item.get("distance"), 0), r_d, 0, r_rank)
                        l_key = (_safe_fleet_int(l_item.get("distance"), 0), l_d, 1, l_rank)
                        if r_key <= l_key:
                            _, chosen_item = rem.pop(r_s)
                            picked.append(chosen_item)
                            arc_right = r_s
                        else:
                            _, chosen_item = rem.pop(l_s)
                            picked.append(chosen_item)
                            arc_left = l_s
                    elif r_cand is not None:
                        _, r_s = r_cand
                        _, chosen_item = rem.pop(r_s)
                        picked.append(chosen_item)
                        arc_right = r_s
                    elif l_cand is not None:
                        _, l_s = l_cand
                        _, chosen_item = rem.pop(l_s)
                        picked.append(chosen_item)
                        arc_left = l_s
                    else:
                        s_new = min(rem.keys(), key=lambda s: rem[s][0])
                        _, chosen_item = rem.pop(s_new)
                        picked.append(chosen_item)
                        arc_left = s_new
                        arc_right = s_new

    return picked


def _extract_own_colonies_and_coords(
    own_coords: Sequence[str] | str,
    *,
    systems_per_galaxy: int = 400,
) -> tuple[list[tuple[int, int]], set[str]]:
    raw_items: Sequence[Any]
    if isinstance(own_coords, (str, bytes, bytearray)):
        raw_items = [own_coords]
    elif isinstance(own_coords, Sequence):
        raw_items = own_coords
    elif isinstance(own_coords, Iterable):
        raw_items = list(own_coords)
    else:
        raw_items = ()

    colonies: list[tuple[int, int]] = []
    seen_gs: set[tuple[int, int]] = set()
    norm_set: set[str] = set()
    for raw in raw_items:
        txt = game_data.safe_key(raw).strip() if raw is not None else ""
        if not txt:
            continue
        nc = normalize_coords(txt)
        if re.fullmatch(r"\d{2}:\d{3}:\d{2}", nc, re.ASCII):
            norm_set.add(nc)
        pair = _parse_gal_sys_pair(nc or txt)
        if pair is not None and 1 <= pair[1] <= systems_per_galaxy and pair not in seen_gs:
            seen_gs.add(pair)
            colonies.append(pair)
    return colonies, norm_set


def _format_age_short(age_seconds: Optional[float]) -> str:
    if age_seconds is None or not math.isfinite(age_seconds):
        return "unscanned"
    sec = max(0, int(round(age_seconds)))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec / 3600.0:.1f}h"
    return f"{sec / 86400.0:.1f}d"


def _compile_sector_intel_view(
    *,
    own_colonies: Sequence[tuple[int, int]],
    own_norm_coords: set[str],
    sector_intel: Optional[Mapping[str, Any]],
    galaxies: Optional[Sequence[int]],
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]],
    raid_targets: Optional[Sequence[Mapping[str, Any]]],
    raid_stats: Optional[Mapping[str, Any]],
    raid_history: Optional[Sequence[Mapping[str, Any]]],
    systems_per_galaxy: int,
    eff_now: float,
) -> tuple[list[int], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    n_sys = max(1, _safe_fleet_int(systems_per_galaxy, 400))
    active_galaxies: list[int] = []
    if galaxies is not None and not isinstance(galaxies, (str, bytes, bytearray)):
        for g_raw in galaxies:
            gi = _safe_fleet_int(g_raw, 0)
            if 1 <= gi <= 14 and gi not in active_galaxies:
                active_galaxies.append(gi)
    if not active_galaxies and own_colonies:
        for gi, _ in own_colonies:
            if 1 <= gi <= 14 and gi not in active_galaxies:
                active_galaxies.append(gi)
    if not active_galaxies and isinstance(sector_intel, Mapping):
        raw_gals = sector_intel.get("galaxies")
        if isinstance(raw_gals, Sequence) and not isinstance(raw_gals, (str, bytes, bytearray)):
            for g_raw in raw_gals:
                gi = _safe_fleet_int(g_raw, 0)
                if 1 <= gi <= 14 and gi not in active_galaxies:
                    active_galaxies.append(gi)
    if not active_galaxies:
        active_galaxies = [1]

    sectors_data: dict[str, dict[str, Any]] = {}
    owner_stats: dict[str, dict[str, Any]] = {}

    def _ensure_sec(gal: int, sys_num: int) -> dict[str, Any]:
        skey = f"{gal:02d}:{sys_num:03d}"
        entry = sectors_data.get(skey)
        if entry is None:
            entry = {
                "galaxy": gal,
                "system": sys_num,
                "last_scanned_ts": 0.0,
                "scan_count": 0,
                "occupied_count": 0,
                "target_count": 0,
                "inactive_count": 0,
                "spacia_count": 0,
                "debris_count": 0,
                "free_count": 0,
                "prime_colony_free_count": 0,
                "direct_raid_count": 0,
                "direct_recency_raid_score": 0.0,
                "direct_loot_spice_eq": 0.0,
                "last_attacked_ts": 0.0,
                "owner_planet_counts": {},
                "owner_display": {},
            }
            sectors_data[skey] = entry
        return entry

    if isinstance(sector_intel, Mapping):
        raw_owner_stats = sector_intel.get("owner_raid_stats")
        if isinstance(raw_owner_stats, Mapping):
            for ok_raw, ov in raw_owner_stats.items():
                if not isinstance(ov, Mapping):
                    continue
                disp = game_data.safe_key(ov.get("owner") or ok_raw).strip()
                if not disp or disp.lower() == "spacia":
                    continue
                okey = disp.lower()
                owner_stats[okey] = {
                    "owner": disp,
                    "raid_count": max(0, _safe_fleet_int(ov.get("raid_count"), 0)),
                    "recency_raid_score": max(0.0, _safe_scan_float(ov.get("recency_raid_score"), 0.0)),
                    "loot_spice_eq": max(0.0, _safe_scan_float(ov.get("loot_spice_eq"), 0.0)),
                }
        raw_owner_counts = sector_intel.get("owner_attack_counts")
        if isinstance(raw_owner_counts, Mapping):
            for ok_raw, cnt_raw in raw_owner_counts.items():
                disp = game_data.safe_key(ok_raw).strip()
                cnt = max(0, _safe_fleet_int(cnt_raw, 0))
                if not disp or disp.lower() == "spacia" or cnt <= 0:
                    continue
                okey = disp.lower()
                cur_os = owner_stats.get(okey)
                if cur_os is None:
                    owner_stats[okey] = {
                        "owner": disp,
                        "raid_count": cnt,
                        "recency_raid_score": float(cnt),
                        "loot_spice_eq": 0.0,
                    }
                else:
                    cur_os["raid_count"] = max(int(cur_os["raid_count"]), cnt)

        raw_sectors = sector_intel.get("sectors")
        if isinstance(raw_sectors, Mapping):
            for skey_raw, sv in raw_sectors.items():
                if not isinstance(sv, Mapping):
                    continue
                pair = _parse_gal_sys_pair(skey_raw)
                gal = _safe_fleet_int(sv.get("galaxy"), pair[0] if pair else 0)
                sys_num = _safe_fleet_int(sv.get("system"), pair[1] if pair else 0)
                if not (1 <= gal <= 14 and 1 <= sys_num <= n_sys):
                    continue
                sec = _ensure_sec(gal, sys_num)
                sec["last_scanned_ts"] = max(
                    sec["last_scanned_ts"],
                    _safe_scan_float(sv.get("last_scanned_ts"), 0.0),
                )
                sec["scan_count"] = max(sec["scan_count"], _safe_fleet_int(sv.get("scan_count"), 0))
                for k_int in (
                    "occupied_count",
                    "target_count",
                    "inactive_count",
                    "spacia_count",
                    "debris_count",
                    "free_count",
                    "prime_colony_free_count",
                    "direct_raid_count",
                ):
                    sec[k_int] = max(sec[k_int], _safe_fleet_int(sv.get(k_int), 0))
                sec["direct_recency_raid_score"] = max(
                    sec["direct_recency_raid_score"],
                    _safe_scan_float(sv.get("direct_recency_raid_score"), 0.0),
                )
                sec["direct_loot_spice_eq"] = max(
                    sec["direct_loot_spice_eq"],
                    _safe_scan_float(sv.get("direct_loot_spice_eq"), 0.0),
                )
                sec["last_attacked_ts"] = max(
                    sec["last_attacked_ts"],
                    _safe_scan_float(sv.get("last_attacked_ts"), 0.0),
                )
                raw_op = sv.get("owner_planet_counts")
                if isinstance(raw_op, Mapping):
                    for oname_raw, pcnt_raw in raw_op.items():
                        disp = game_data.safe_key(oname_raw).strip()
                        pcnt = max(0, _safe_fleet_int(pcnt_raw, 0))
                        if disp and disp.lower() != "spacia" and pcnt > 0:
                            okey = disp.lower()
                            sec["owner_planet_counts"][okey] = max(
                                sec["owner_planet_counts"].get(okey, 0),
                                pcnt,
                            )
                            sec["owner_display"].setdefault(okey, disp)
                raw_owners_list = sv.get("owners")
                if isinstance(raw_owners_list, Sequence) and not isinstance(raw_owners_list, (str, bytes, bytearray)):
                    for oname_raw in raw_owners_list:
                        disp = game_data.safe_key(oname_raw).strip()
                        if disp and disp.lower() != "spacia":
                            okey = disp.lower()
                            sec["owner_planet_counts"].setdefault(okey, 1)
                            sec["owner_display"].setdefault(okey, disp)

    if scanned_systems is not None:
        if isinstance(scanned_systems, Mapping):
            for raw_k, raw_ts in scanned_systems.items():
                pair = _parse_gal_sys_pair(raw_k)
                if pair is None or not (1 <= pair[1] <= n_sys):
                    continue
                ts_val = _safe_scan_float(raw_ts, 0.0)
                if ts_val > 0.0:
                    sec = _ensure_sec(pair[0], pair[1])
                    sec["last_scanned_ts"] = max(sec["last_scanned_ts"], ts_val)
                    sec["scan_count"] = max(sec["scan_count"], 1)
        elif isinstance(scanned_systems, Iterable) and not isinstance(scanned_systems, (str, bytes, bytearray)):
            fallback_ts = max(1.0, eff_now - 3600.0)
            for raw_k in scanned_systems:
                pair = _parse_gal_sys_pair(raw_k)
                if pair is None or not (1 <= pair[1] <= n_sys):
                    continue
                sec = _ensure_sec(pair[0], pair[1])
                if sec["last_scanned_ts"] <= 0.0:
                    sec["last_scanned_ts"] = fallback_ts
                sec["scan_count"] = max(sec["scan_count"], 1)

    coord_owner: dict[str, str] = {}
    coord_spacia: set[str] = set()
    if isinstance(raid_targets, Sequence) and not isinstance(raid_targets, (str, bytes, bytearray)):
        per_sec_owner_coords: dict[tuple[int, int], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        per_sec_debris_coords: dict[tuple[int, int], set[str]] = defaultdict(set)
        per_sec_inact_coords: dict[tuple[int, int], set[str]] = defaultdict(set)
        per_sec_spacia_coords: dict[tuple[int, int], set[str]] = defaultdict(set)
        per_sec_free_coords: dict[tuple[int, int], set[str]] = defaultdict(set)
        per_sec_prime_coords: dict[tuple[int, int], set[str]] = defaultdict(set)
        for trow in raid_targets:
            if not isinstance(trow, Mapping):
                continue
            if bool(trow.get("is_own_planet", False)) or str(trow.get("merged_into") or "").strip():
                continue
            nc = normalize_coords(trow.get("coords") or trow.get("target_coords") or "")
            if not nc or nc in own_norm_coords:
                continue
            m_c = re.fullmatch(r"(\d{2}):(\d{3}):(\d{2})", nc, re.ASCII)
            if not m_c:
                continue
            tg, ts_i, tp = int(m_c.group(1)), int(m_c.group(2)), int(m_c.group(3))
            if not (1 <= tg <= 14 and 1 <= ts_i <= n_sys):
                continue
            sec = _ensure_sec(tg, ts_i)
            scolor = game_data.safe_key(trow.get("status_color") or "").strip().lower()
            is_sp = bool(trow.get("is_spacia", False))
            owner_disp = game_data.safe_key(trow.get("owner") or "").strip()
            if is_sp or owner_disp.lower() == "spacia":
                coord_spacia.add(nc)
                per_sec_spacia_coords[(tg, ts_i)].add(nc)
            elif owner_disp and scolor != "free":
                coord_owner[nc] = owner_disp
                okey = owner_disp.lower()
                per_sec_owner_coords[(tg, ts_i)][okey].add(nc)
                sec["owner_display"].setdefault(okey, owner_disp)
            if bool(trow.get("has_debris", False)):
                per_sec_debris_coords[(tg, ts_i)].add(nc)
            if scolor == "yellow" or (bool(trow.get("is_inactive", False)) and not is_sp and scolor != "free"):
                per_sec_inact_coords[(tg, ts_i)].add(nc)
            if scolor == "free":
                per_sec_free_coords[(tg, ts_i)].add(nc)
                uf = _safe_fleet_int(trow.get("uninhabited_fields"), 0)
                cmax = _safe_fleet_int(trow.get("colonize_max_fields"), 0)
                if uf >= 300 or cmax >= 300 or (uf == 0 and cmax == 0 and 7 <= tp <= 12):
                    per_sec_prime_coords[(tg, ts_i)].add(nc)

        for (tg, ts_i), omap in per_sec_owner_coords.items():
            sec = _ensure_sec(tg, ts_i)
            for okey, cset in omap.items():
                sec["owner_planet_counts"][okey] = max(sec["owner_planet_counts"].get(okey, 0), len(cset))
        for (tg, ts_i), cset in per_sec_debris_coords.items():
            sec = _ensure_sec(tg, ts_i)
            sec["debris_count"] = max(sec["debris_count"], len(cset))
        for (tg, ts_i), cset in per_sec_inact_coords.items():
            sec = _ensure_sec(tg, ts_i)
            sec["inactive_count"] = max(sec["inactive_count"], len(cset))
        for (tg, ts_i), cset in per_sec_spacia_coords.items():
            sec = _ensure_sec(tg, ts_i)
            sec["spacia_count"] = max(sec["spacia_count"], len(cset))
        for (tg, ts_i), cset in per_sec_free_coords.items():
            sec = _ensure_sec(tg, ts_i)
            sec["free_count"] = max(sec["free_count"], len(cset))
        for (tg, ts_i), cset in per_sec_prime_coords.items():
            sec = _ensure_sec(tg, ts_i)
            sec["prime_colony_free_count"] = max(sec["prime_colony_free_count"], len(cset))
        for sec in sectors_data.values():
            sec["target_count"] = max(sec["target_count"], sec["inactive_count"] + sec["spacia_count"])

    if sector_intel is None:
        raw_hist_rows: list[Mapping[str, Any]] = []
        if isinstance(raid_history, Sequence) and not isinstance(raid_history, (str, bytes, bytearray)):
            for r in raid_history:
                if isinstance(r, Mapping):
                    raw_hist_rows.append(r)
        if isinstance(raid_stats, Mapping):
            rs_hist = raid_stats.get("history")
            if isinstance(rs_hist, Sequence) and not isinstance(rs_hist, (str, bytes, bytearray)) and not raw_hist_rows:
                for r in rs_hist:
                    if isinstance(r, Mapping):
                        raw_hist_rows.append(r)

        if raw_hist_rows:
            for r in raw_hist_rows:
                nc = normalize_coords(r.get("target_coords") or r.get("coords") or "")
                if not nc or nc in own_norm_coords:
                    continue
                pair = _parse_gal_sys_pair(nc)
                if pair is None or not (1 <= pair[1] <= n_sys):
                    continue
                rts = _safe_scan_float(
                    r.get("attacked_ts")
                    if r.get("attacked_ts") is not None
                    else (r.get("ts") if r.get("ts") is not None else r.get("timestamp")),
                    eff_now,
                )
                loot_sp = _safe_scan_float(r.get("loot_spice_eq"), 0.0)
                if loot_sp <= 0.0 and isinstance(r.get("loot"), Mapping):
                    loot_map = r["loot"]
                    loot_sp = float(
                        game_data.spice_equivalent(
                            Resources(
                                pig_iron=max(0, _safe_fleet_int(loot_map.get("pig_iron"), 0)),
                                metal=max(0, _safe_fleet_int(loot_map.get("metal"), 0)),
                                kryptonite=max(0, _safe_fleet_int(loot_map.get("kryptonite"), 0)),
                                spice=max(0, _safe_fleet_int(loot_map.get("spice"), 0)),
                            )
                        )
                    )
                decay = 0.25 + 0.75 * math.exp(-max(0.0, eff_now - rts) / (7.0 * 86400.0))
                sec = _ensure_sec(pair[0], pair[1])
                sec["direct_raid_count"] += 1
                sec["direct_recency_raid_score"] += decay
                sec["direct_loot_spice_eq"] += max(0.0, loot_sp)
                sec["last_attacked_ts"] = max(sec["last_attacked_ts"], rts)

                owner_disp = coord_owner.get(nc, "")
                if owner_disp and nc not in coord_spacia and owner_disp.lower() != "spacia":
                    okey = owner_disp.lower()
                    os_entry = owner_stats.setdefault(
                        okey,
                        {
                            "owner": owner_disp,
                            "raid_count": 0,
                            "recency_raid_score": 0.0,
                            "loot_spice_eq": 0.0,
                        },
                    )
                    os_entry["raid_count"] += 1
                    os_entry["recency_raid_score"] += decay
                    os_entry["loot_spice_eq"] += max(0.0, loot_sp)
        elif isinstance(raid_stats, Mapping) and isinstance(raid_stats.get("per_target"), Mapping):
            for tc_raw, pt in raid_stats["per_target"].items():
                if not isinstance(pt, Mapping):
                    continue
                nc = normalize_coords(tc_raw)
                pair = _parse_gal_sys_pair(nc)
                attempts = max(0, _safe_fleet_int(pt.get("attempts"), 0))
                if pair is None or not (1 <= pair[1] <= n_sys) or attempts <= 0:
                    continue
                loot_sp = max(0.0, _safe_scan_float(pt.get("total_loot_spice_eq"), 0.0))
                sec = _ensure_sec(pair[0], pair[1])
                sec["direct_raid_count"] += attempts
                sec["direct_recency_raid_score"] += float(attempts) * 0.75
                sec["direct_loot_spice_eq"] += loot_sp
                owner_disp = coord_owner.get(nc, "")
                if owner_disp and nc not in coord_spacia and owner_disp.lower() != "spacia":
                    okey = owner_disp.lower()
                    os_entry = owner_stats.setdefault(
                        okey,
                        {
                            "owner": owner_disp,
                            "raid_count": 0,
                            "recency_raid_score": 0.0,
                            "loot_spice_eq": 0.0,
                        },
                    )
                    os_entry["raid_count"] += attempts
                    os_entry["recency_raid_score"] += float(attempts) * 0.75
                    os_entry["loot_spice_eq"] += loot_sp

    return active_galaxies, sectors_data, owner_stats


def score_galaxy_scan_sectors(
    *,
    own_coords: Sequence[str] | str = (),
    sector_intel: Optional[Mapping[str, Any]] = None,
    galaxies: Optional[Sequence[int]] = None,
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]] = None,
    raid_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    explore_first: bool = True,
    near_weight: float = 2.0,
    attack_weight: float = 3.0,
    collect_colonies: bool = True,
    min_freshness_seconds: float = 120.0,
    max_radius: Optional[int] = None,
    systems_per_galaxy: int = 400,
    now_ts: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Score and rank galaxy solar systems `(1..400)` for 2-phase smart scanning."""
    eff_now = _safe_scan_float(now_ts, 0.0)
    if eff_now <= 0.0 and isinstance(sector_intel, Mapping):
        eff_now = _safe_scan_float(sector_intel.get("now_ts"), 0.0)
    if eff_now <= 0.0:
        eff_now = time.time()

    n_sys = max(1, _safe_fleet_int(systems_per_galaxy, 400))
    eff_near_w = max(0.0, min(20.0, _safe_scan_float(near_weight, 2.0)))
    eff_attack_w = max(0.0, min(20.0, _safe_scan_float(attack_weight, 3.0)))
    eff_fresh_s = max(0.0, _safe_scan_float(min_freshness_seconds, 120.0))
    rad_cap = _safe_fleet_int(max_radius, -1) if max_radius is not None else -1

    own_colonies, own_norm_coords = _extract_own_colonies_and_coords(
        own_coords,
        systems_per_galaxy=n_sys,
    )
    active_galaxies, sectors_data, owner_stats = _compile_sector_intel_view(
        own_colonies=own_colonies,
        own_norm_coords=own_norm_coords,
        sector_intel=sector_intel,
        galaxies=galaxies,
        scanned_systems=scanned_systems,
        raid_targets=raid_targets,
        raid_stats=raid_stats,
        raid_history=raid_history,
        systems_per_galaxy=n_sys,
        eff_now=eff_now,
    )
    eff_colonies = own_colonies if own_colonies else [(active_galaxies[0], 1)]
    colony_gal_set = {cg for cg, _ in eff_colonies}
    primary_sys_by_gal: dict[int, int] = {}
    for cg, cs in eff_colonies:
        primary_sys_by_gal.setdefault(cg, cs)

    scored: list[dict[str, Any]] = []
    near_scale = eff_near_w * 2.5
    default_staleness = (7.0 * 86400.0) / 1800.0
    col_int = [(int(cg), int(cs)) for cg, cs in eff_colonies]
    for gal in active_galaxies:
        anchor_sys = primary_sys_by_gal.get(gal, eff_colonies[0][1])
        same_gal = gal in colony_gal_set
        gal_prefix = f"{gal:02d}:"
        same_gal_cols = tuple(cs for cg, cs in col_int if cg == gal)
        other_gal_cols = tuple((250 * abs(gal - cg), cs) for cg, cs in col_int if cg != gal)
        only_same_gal_fast = bool(same_gal_cols) and (n_sys <= 500 or not other_gal_cols)
        for sys_num in range(1, n_sys + 1):
            if only_same_gal_fast:
                best_d = n_sys
                for cs in same_gal_cols:
                    delta = sys_num - cs
                    if delta < 0:
                        delta = -delta
                    circ = n_sys - delta if (n_sys - delta) < delta else delta
                    if circ < best_d:
                        best_d = circ
                        if best_d == 0:
                            break
                dist = best_d
            else:
                best_d = 10**9
                for cs in same_gal_cols:
                    delta = abs(sys_num - cs)
                    circ = delta if delta <= (n_sys - delta) else (n_sys - delta)
                    if circ < best_d:
                        best_d = circ
                for g_pen, cs in other_gal_cols:
                    delta = abs(sys_num - cs)
                    circ = delta if delta <= (n_sys - delta) else (n_sys - delta)
                    cand_d = g_pen + circ
                    if cand_d < best_d:
                        best_d = cand_d
                dist = best_d if best_d < 10**9 else 0
            if rad_cap >= 0 and dist > rad_cap:
                continue

            skey = f"{gal_prefix}{sys_num:03d}"
            sec = sectors_data.get(skey)
            prox_norm = (1.0 / (1.0 + dist / 20.0)) if same_gal else (0.15 / (1.0 + dist / 200.0))
            near_score = near_scale * prox_norm
            ring_offset = (sys_num - anchor_sys) % n_sys

            if sec is None:
                importance = 1.0 + near_score
                refresh_score = importance * default_staleness
                if explore_first:
                    item_phase = "exploratory"
                    reason = f"unscanned sector (dist {dist})"
                else:
                    item_phase = "targeted_refresh"
                    if same_gal and dist <= 25 and eff_near_w > 0.0:
                        reason = f"near d={dist}, age never"
                    elif same_gal:
                        reason = f"d={dist}, age never"
                    else:
                        reason = f"cross-gal d={dist}, age never"
                scored.append(
                    {
                        "sector": skey,
                        "galaxy": gal,
                        "system": sys_num,
                        "phase": item_phase,
                        "scanned": False,
                        "is_fresh": False,
                        "score": round(refresh_score, 4),
                        "importance": round(importance, 4),
                        "near_score": round(near_score, 4),
                        "attack_score": 0.0,
                        "secondary_score": 0.0,
                        "staleness_factor": round(default_staleness, 4),
                        "distance": dist,
                        "last_scanned_ts": 0.0,
                        "age_seconds": None,
                        "direct_raid_count": 0,
                        "direct_loot_spice_eq": 0.0,
                        "owner_affinity_raid_count": 0,
                        "frequent_owners": [],
                        "debris_count": 0,
                        "target_count": 0,
                        "free_count": 0,
                        "reason": reason,
                        "_same_gal": 0 if same_gal else 1,
                        "_ring_offset": ring_offset,
                    }
                )
                continue

            last_ts = _safe_scan_float(sec["last_scanned_ts"], 0.0)
            scanned = last_ts > 0.0
            age_s: Optional[float] = round(max(0.0, eff_now - last_ts), 3) if scanned else None
            is_fresh = bool(scanned and age_s is not None and age_s < eff_fresh_s)

            direct_raids = int(sec["direct_raid_count"])
            direct_recency = float(sec["direct_recency_raid_score"])
            direct_loot = float(sec["direct_loot_spice_eq"])
            debris_cnt = int(sec["debris_count"])
            inact_cnt = int(sec["inactive_count"])
            spacia_cnt = int(sec["spacia_count"])
            target_cnt = max(int(sec["target_count"]), inact_cnt + spacia_cnt)
            free_cnt = int(sec["free_count"])
            prime_free_cnt = int(sec["prime_colony_free_count"])

            eff_direct = max(direct_recency, 0.6 * float(direct_raids))
            direct_freq_sig = math.log1p(max(0.0, eff_direct))
            direct_loot_sig = 0.35 * math.log1p(max(0.0, direct_loot) / 5000.0)

            affinity_sig = 0.0
            owner_affinity_raids = 0
            freq_owners: list[str] = []
            if sec["owner_planet_counts"]:
                owner_entries: list[tuple[int, float, str, int]] = []
                for okey, pcnt in sec["owner_planet_counts"].items():
                    os_info = owner_stats.get(okey)
                    if os_info is None or int(os_info["raid_count"]) <= 0:
                        continue
                    o_raids = int(os_info["raid_count"])
                    o_loot = float(os_info["loot_spice_eq"])
                    o_disp = str(os_info["owner"] or sec["owner_display"].get(okey, okey))
                    owner_entries.append((o_raids, o_loot, o_disp, int(pcnt)))
                owner_entries.sort(key=lambda t: (-t[0], -t[1], t[2]))
                for o_raids, o_loot, o_disp, pcnt in owner_entries:
                    owner_affinity_raids += o_raids
                    pmult = 1.0 + 0.35 * max(0, pcnt - 1)
                    affinity_sig += (
                        math.log1p(float(o_raids))
                        + 0.25 * math.log1p(max(0.0, o_loot) / 10000.0)
                    ) * pmult
                    freq_owners.append(f"{o_disp}({o_raids})")

            attack_score = eff_attack_w * (
                1.6 * direct_freq_sig + direct_loot_sig + 1.4 * affinity_sig
            )

            debris_bonus = min(3.0, 1.25 * debris_cnt)
            farm_bonus = min(2.5, 0.55 * inact_cnt + 0.75 * spacia_cnt)
            colony_bonus = (
                min(1.8, 0.45 * prime_free_cnt + 0.10 * min(6, free_cnt))
                if collect_colonies
                else 0.0
            )
            secondary_score = debris_bonus + farm_bonus + colony_bonus

            importance = 1.0 + near_score + attack_score + secondary_score
            eff_age = age_s if (scanned and age_s is not None) else (7.0 * 86400.0)
            staleness_factor = max(60.0, eff_age) / 1800.0
            refresh_score = importance * staleness_factor

            item_phase = "exploratory" if (explore_first and not scanned) else "targeted_refresh"
            if item_phase == "exploratory":
                reason = f"unscanned sector (dist {dist})"
            else:
                tags: list[str] = []
                if same_gal and dist <= 25 and eff_near_w > 0.0:
                    tags.append(f"near d={dist}")
                elif same_gal:
                    tags.append(f"d={dist}")
                else:
                    tags.append(f"cross-gal d={dist}")
                if direct_raids > 0:
                    tags.append(f"raided {direct_raids}x")
                if freq_owners:
                    tags.append("freq-target " + ",".join(freq_owners[:2]))
                if debris_cnt > 0:
                    tags.append(f"debris x{debris_cnt}")
                elif target_cnt > 0:
                    tags.append(f"targets x{target_cnt}")
                elif collect_colonies and prime_free_cnt > 0:
                    tags.append(f"colony-slots x{prime_free_cnt}")
                tags.append(f"age {_format_age_short(age_s)}")
                reason = ", ".join(tags)

            scored.append(
                {
                    "sector": skey,
                    "galaxy": gal,
                    "system": sys_num,
                    "phase": item_phase,
                    "scanned": scanned,
                    "is_fresh": is_fresh,
                    "score": round(refresh_score, 4),
                    "importance": round(importance, 4),
                    "near_score": round(near_score, 4),
                    "attack_score": round(attack_score, 4),
                    "secondary_score": round(secondary_score, 4),
                    "staleness_factor": round(staleness_factor, 4),
                    "distance": int(dist),
                    "last_scanned_ts": round(last_ts, 3),
                    "age_seconds": age_s,
                    "direct_raid_count": direct_raids,
                    "direct_loot_spice_eq": round(direct_loot, 2),
                    "owner_affinity_raid_count": owner_affinity_raids,
                    "frequent_owners": freq_owners,
                    "debris_count": debris_cnt,
                    "target_count": target_cnt,
                    "free_count": free_cnt,
                    "reason": reason,
                    "_same_gal": 0 if same_gal else 1,
                    "_ring_offset": ring_offset,
                }
            )

    if explore_first:
        scored.sort(
            key=lambda it: (
                0 if not it["scanned"] else (1 if not it["is_fresh"] else 2),
                it["_same_gal"] if not it["scanned"] else 0,
                it["distance"] if not it["scanned"] else -it["score"],
                it["_ring_offset"] if not it["scanned"] else -it["importance"],
                it["distance"],
                it["galaxy"],
                it["system"],
            )
        )
    else:
        scored.sort(
            key=lambda it: (
                0 if not it["is_fresh"] else 1,
                -it["score"],
                -it["importance"],
                it["distance"],
                it["galaxy"],
                it["system"],
            )
        )

    for it in scored:
        it.pop("_same_gal", None)
        it.pop("_ring_offset", None)
    return scored


def select_smart_galaxy_scan_batch(
    *,
    own_coords: Sequence[str] | str = (),
    sector_intel: Optional[Mapping[str, Any]] = None,
    galaxies: Optional[Sequence[int]] = None,
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]] = None,
    raid_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    batch_size: int = 100,
    explore_first: bool = True,
    refresh_pct: Optional[float] = None,
    near_weight: float = 2.0,
    attack_weight: float = 3.0,
    collect_colonies: bool = True,
    min_freshness_seconds: float = 120.0,
    max_radius: Optional[int] = None,
    smooth_walk: bool = True,
    current_cursor: Optional[tuple[int, int]] = None,
    systems_per_galaxy: int = 400,
    now_ts: Optional[float] = None,
) -> dict[str, Any]:
    """Select a smart galaxy scan batch (`exploratory`, `mixed`, or `targeted_refresh`) with smooth walk order.

    When `refresh_pct` (0..100) is configured above 0, allocates that share of `batch_size` to refreshing the
    highest-priority already-scanned sectors and the remaining `100 - refresh_pct`% to discovering unscanned
    sectors (spilling any unused quota into the other pool so the sweep budget is never wasted).
    """
    n_sys = max(1, _safe_fleet_int(systems_per_galaxy, 400))
    eff_batch = max(1, _safe_fleet_int(batch_size, 100))

    raw_refresh_pct = (
        max(0.0, min(100.0, _safe_scan_float(refresh_pct, 0.0)))
        if refresh_pct is not None
        else None
    )
    use_explicit_split = raw_refresh_pct is not None and raw_refresh_pct > 0.0
    eff_refresh_pct = (
        raw_refresh_pct
        if use_explicit_split
        else (0.0 if explore_first else 100.0)
    )

    own_colonies, _ = _extract_own_colonies_and_coords(
        own_coords,
        systems_per_galaxy=n_sys,
    )
    ranked = score_galaxy_scan_sectors(
        own_coords=own_coords,
        sector_intel=sector_intel,
        galaxies=galaxies,
        scanned_systems=scanned_systems,
        raid_targets=raid_targets,
        raid_stats=raid_stats,
        raid_history=raid_history,
        explore_first=bool(explore_first or use_explicit_split),
        near_weight=near_weight,
        attack_weight=attack_weight,
        collect_colonies=collect_colonies,
        min_freshness_seconds=min_freshness_seconds,
        max_radius=max_radius,
        systems_per_galaxy=n_sys,
        now_ts=now_ts,
    )

    active_galaxies: list[int] = []
    for it in ranked:
        gi = int(it["galaxy"])
        if gi not in active_galaxies:
            active_galaxies.append(gi)
    if not active_galaxies:
        active_galaxies = [1]

    if max_radius is None:
        total_systems = len(active_galaxies) * n_sys
    else:
        total_systems = len(ranked)
    explored_systems = sum(1 for it in ranked if it["scanned"])
    unscanned_systems = max(0, total_systems - explored_systems)
    coverage_pct = round((explored_systems / total_systems) * 100.0, 2) if total_systems > 0 else 0.0

    primary_sys_by_gal: dict[int, int] = {}
    colony_gal_set: set[int] = set()
    for cg, cs in own_colonies:
        primary_sys_by_gal.setdefault(cg, cs)
        colony_gal_set.add(cg)

    def _build_scanned_pool(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        pool = [dict(it, phase="targeted_refresh") for it in items if it["scanned"]]
        pool.sort(
            key=lambda it: (
                0 if not it["is_fresh"] else 1,
                -it["score"],
                -it["importance"],
                it["distance"],
                it["galaxy"],
                it["system"],
            )
        )
        for it in pool:
            if str(it.get("reason") or "").startswith("unscanned"):
                it["reason"] = f"d={it['distance']}, age {_format_age_short(it['age_seconds'])}"
        return pool

    if use_explicit_split:
        unscanned_pool = [dict(it, phase="exploratory") for it in ranked if not it["scanned"]]
        scanned_pool = _build_scanned_pool(ranked)
        target_ref = min(eff_batch, max(0, int(round(eff_batch * (eff_refresh_pct / 100.0)))))
        target_exp = max(0, eff_batch - target_ref)
        take_ref = list(scanned_pool[:target_ref])
        eff_exp_budget = target_exp + max(0, target_ref - len(take_ref))
        take_exp = _select_contiguous_exploratory_sectors(
            unscanned_pool,
            eff_exp_budget,
            colony_gal_set=colony_gal_set,
            current_cursor=current_cursor if eff_refresh_pct < 100.0 else None,
            systems_per_galaxy=n_sys,
        )
        if len(take_exp) < eff_exp_budget:
            spill_ref = eff_exp_budget - len(take_exp)
            take_ref.extend(scanned_pool[len(take_ref) : len(take_ref) + spill_ref])

        if eff_refresh_pct >= 100.0:
            if smooth_walk and len(take_ref) > 1:
                walk_ref = order_sectors_for_smooth_walk(
                    take_ref,
                    primary_colony_by_galaxy=primary_sys_by_gal,
                    current_cursor=current_cursor,
                    systems_per_galaxy=n_sys,
                )
            else:
                walk_ref = take_ref
            exp_cursor = (
                (int(walk_ref[-1]["galaxy"]), int(walk_ref[-1]["system"]))
                if walk_ref
                else current_cursor
            )
            if smooth_walk and len(take_exp) > 1:
                walk_exp = order_sectors_for_smooth_walk(
                    take_exp,
                    primary_colony_by_galaxy=primary_sys_by_gal,
                    current_cursor=exp_cursor,
                    systems_per_galaxy=n_sys,
                )
            else:
                walk_exp = take_exp
        else:
            if smooth_walk and len(take_exp) > 1:
                walk_exp = order_sectors_for_smooth_walk(
                    take_exp,
                    primary_colony_by_galaxy=primary_sys_by_gal,
                    current_cursor=current_cursor,
                    systems_per_galaxy=n_sys,
                )
            else:
                walk_exp = take_exp
            ref_cursor = (
                (int(walk_exp[-1]["galaxy"]), int(walk_exp[-1]["system"]))
                if walk_exp
                else current_cursor
            )
            if smooth_walk and len(take_ref) > 1:
                walk_ref = order_sectors_for_smooth_walk(
                    take_ref,
                    primary_colony_by_galaxy=primary_sys_by_gal,
                    current_cursor=ref_cursor,
                    systems_per_galaxy=n_sys,
                )
            else:
                walk_ref = take_ref

        if walk_exp and walk_ref:
            phase = "mixed"
        elif walk_exp:
            phase = "exploratory"
        else:
            phase = "targeted_refresh"

        if eff_refresh_pct >= 100.0:
            priority_selected = [*take_ref, *take_exp]
            selected_sectors = [*walk_ref, *walk_exp]
        else:
            priority_selected = [*take_exp, *take_ref]
            selected_sectors = [*walk_exp, *walk_ref]
    elif explore_first and unscanned_systems > 0:
        phase = "exploratory"
        unscanned_pool = [dict(it, phase="exploratory") for it in ranked if not it["scanned"]]
        take_exp = _select_contiguous_exploratory_sectors(
            unscanned_pool,
            eff_batch,
            colony_gal_set=colony_gal_set,
            current_cursor=current_cursor,
            systems_per_galaxy=n_sys,
        )
        rem_budget = max(0, eff_batch - len(take_exp))
        take_ref: list[dict[str, Any]] = []
        if rem_budget > 0:
            scanned_pool = _build_scanned_pool(ranked)
            take_ref.extend(scanned_pool[:rem_budget])
        priority_selected = [*take_exp, *take_ref]
        if smooth_walk and len(take_exp) > 1:
            walk_exp = order_sectors_for_smooth_walk(
                take_exp,
                primary_colony_by_galaxy=primary_sys_by_gal,
                current_cursor=current_cursor,
                systems_per_galaxy=n_sys,
            )
        else:
            walk_exp = take_exp
        ref_cursor = (
            (int(walk_exp[-1]["galaxy"]), int(walk_exp[-1]["system"]))
            if walk_exp
            else current_cursor
        )
        if smooth_walk and len(take_ref) > 1:
            walk_ref = order_sectors_for_smooth_walk(
                take_ref,
                primary_colony_by_galaxy=primary_sys_by_gal,
                current_cursor=ref_cursor,
                systems_per_galaxy=n_sys,
            )
        else:
            walk_ref = take_ref
        selected_sectors = [*walk_exp, *walk_ref]
    else:
        phase = "targeted_refresh"
        refresh_ranked = score_galaxy_scan_sectors(
            own_coords=own_coords,
            sector_intel=sector_intel,
            galaxies=galaxies,
            scanned_systems=scanned_systems,
            raid_targets=raid_targets,
            raid_stats=raid_stats,
            raid_history=raid_history,
            explore_first=False,
            near_weight=near_weight,
            attack_weight=attack_weight,
            collect_colonies=collect_colonies,
            min_freshness_seconds=min_freshness_seconds,
            max_radius=max_radius,
            systems_per_galaxy=n_sys,
            now_ts=now_ts,
        )
        priority_selected = [dict(it, phase="targeted_refresh") for it in refresh_ranked[:eff_batch]]
        walk_exp = []
        if smooth_walk and len(priority_selected) > 1:
            walk_ref = order_sectors_for_smooth_walk(
                priority_selected,
                primary_colony_by_galaxy=primary_sys_by_gal,
                current_cursor=current_cursor,
                systems_per_galaxy=n_sys,
            )
        else:
            walk_ref = priority_selected
        selected_sectors = walk_ref

    sample_targets = [
        {
            "sector": it["sector"],
            "galaxy": it["galaxy"],
            "system": it["system"],
            "phase": it["phase"],
            "score": it["score"],
            "distance": it["distance"],
            "direct_raid_count": it["direct_raid_count"],
            "owner_affinity_raid_count": it["owner_affinity_raid_count"],
            "reason": it["reason"],
        }
        for it in priority_selected[:5]
    ]

    return {
        "phase": phase,
        "scan_phase": phase,
        "galaxies": active_galaxies,
        "explored_systems": explored_systems,
        "unscanned_systems": unscanned_systems,
        "total_systems": total_systems,
        "coverage_pct": coverage_pct,
        "refresh_pct": round(eff_refresh_pct, 1),
        "discovery_pct": round(100.0 - eff_refresh_pct, 1),
        "exploratory_count": len(walk_exp),
        "refresh_count": len(walk_ref),
        "selected_sectors": selected_sectors,
        "sample_targets": sample_targets,
    }
