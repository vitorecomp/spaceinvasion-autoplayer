"""M5-M6 fleet safety, combat estimation, espionage, Spacia raid, and colony helpers."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
import math
import os
import re
import time
from typing import Any, Deque, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from spaceinvasion import game_data
from spaceinvasion.state import PlanetState, Resources, parse_si_int

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


_COORDS_NORM_RE = re.compile(
    r"^\s*\[?\s*(\d{1,2})\s*[:/\-]\s*(\d{1,3})\s*[:/\-]\s*(\d{1,2})\s*\]?\s*$",
    re.ASCII,
)


def normalize_coords(coords: Any) -> str:
    """Normalize SpaceInvasion coordinates like `'2:380:4'` to `'02:380:04'`."""
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
    """Enforce SpaceInvasion's 3-attacks-per-24h bash cap on active players."""

    def __init__(self, *, max_active_attacks_24h: int = 3, war_max_24h: int = 5) -> None:
        self.max_active_attacks_24h = max_active_attacks_24h
        self.war_max_24h = war_max_24h
        self._history: dict[str, Deque[float]] = defaultdict(deque)

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
        if is_inactive or is_spacia:
            return True
        cap = self.war_max_24h if in_declared_war else self.max_active_attacks_24h
        return self.attacks_last_24h(target_coords, timestamp=timestamp) < cap


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
    """Infer `(galaxy, system)` from Galaxy HTML inputs/selects/headers or URL query params."""
    soup = BeautifulSoup(html or "", "html.parser")
    gal_found: Optional[int] = None
    sys_found: Optional[int] = None

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
            has_debris = hit.select_one(".rubble-orbit, [name='rubblefield']") is not None
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


def parse_spy_report_html(html: str, *, target_coords: str = "") -> SpyReport:
    """Parse an espionage or uninhabited-ecology report snippet into `SpyReport`."""
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())

    coords = target_coords
    if not coords:
        coords = _extract_spy_coords(text) or _extract_spy_link_coords(soup)

    def _find_res(label: str) -> int:
        m = re.search(rf"{label}\s*:?\s*([\d.]+)", text, re.IGNORECASE | re.ASCII)
        return parse_si_int(m.group(1)) if m else 0

    res = Resources(
        pig_iron=_find_res(r"Pig\s+iron"),
        metal=_find_res(r"Metal"),
        kryptonite=_find_res(r"Kryptonite"),
        spice=_find_res(r"Spice"),
    )

    uninhabited_fields: Optional[int] = None
    m_fld = re.search(
        r"(?:approx\.?|estimated|expected)?\s*(\d{2,3})\s*(?:building\s*)?fields",
        text,
        re.IGNORECASE | re.ASCII,
    )
    if m_fld:
        uninhabited_fields = int(m_fld.group(1))

    dug_in: Optional[bool] = None
    if "dug in" in text.lower() or "dug-in" in text.lower():
        dug_in = "not dug in" not in text.lower()

    fleet: dict[str, int] = {}
    for skey, slug in SHIP_SLUG_BY_KEY.items():
        info = game_data.SHIPS.get(slug)
        if not info:
            continue
        m_u = re.search(rf"{re.escape(info['name'])}\s*:?\s*([\d.]+)", text, re.IGNORECASE | re.ASCII)
        if m_u:
            fleet[skey] = parse_si_int(m_u.group(1))

    defense: dict[str, int] = {}
    for dkey, slug in DEFENSE_SLUG_BY_KEY.items():
        info = game_data.DEFENSES.get(slug)
        if not info:
            continue
        m_u = re.search(rf"{re.escape(info['name'])}\s*:?\s*([\d.]+)", text, re.IGNORECASE | re.ASCII)
        if m_u:
            defense[dkey] = parse_si_int(m_u.group(1))

    is_spacia = "spacia" in text.lower()
    owner = "Spacia" if is_spacia else ""
    if not owner:
        m_own = re.search(
            r"(?:Player|Owner|Spieler)\s*:\s*(.+?)(?=\s+(?:Resources|Pig\s+iron|Metal|Kryptonite|Spice|Fleet|Defense|Buildings|Approx|Dug|\d{1,2}:\d{1,3}:\d{1,2}|$)|$)",
            text,
            re.IGNORECASE | re.ASCII,
        )
        if m_own:
            owner = m_own.group(1).strip(" |:-")

    return SpyReport(
        target_coords=coords,
        owner=owner,
        resources=res,
        fleet=fleet,
        defense=defense,
        dug_in=dug_in,
        uninhabited_fields=uninhabited_fields,
        is_spacia=is_spacia,
    )


def parse_spy_messages_html(html: str) -> tuple[SpyReport, ...]:
    """Parse multiple espionage reports from `internalMessages&type=spio` HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    keywords = ("pig iron", "metal", "kryptonite", "spice", "fields")

    def _looks_like_spy_block(txt: str, el: Any = None) -> bool:
        low = txt.lower()
        if not any(k in low for k in keywords):
            return False
        return bool(_extract_spy_coords(txt) or (el is not None and _extract_spy_link_coords(el)))

    blocks: list[str] = []
    # 1. Explicit message containers
    containers = soup.select("div.message, div.msg, tr.message, tr.msg, tr[id^='msg'], div[id^='msg']")
    for el in containers:
        if _looks_like_spy_block(el.get_text(" ", strip=True), el):
            blocks.append(str(el))

    # 2. Fallback: innermost table rows / divs containing spy blocks without nested spy containers
    if not blocks:
        candidates = soup.find_all(["tr", "div", "li"])
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

    # 3. Last resort: single report in whole page text
    if not blocks and _looks_like_spy_block(soup.get_text(" ", strip=True), soup):
        blocks.append(html)

    reports: list[SpyReport] = []
    seen_coords: set[str] = set()
    for blk in blocks:
        rep = parse_spy_report_html(blk)
        if not rep.target_coords:
            continue
        if rep.target_coords in seen_coords:
            continue
        seen_coords.add(rep.target_coords)
        reports.append(rep)
    return tuple(reports)


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
    return RaidRecommendation(
        target_coords=slot.coords,
        target_owner=slot.owner or report.owner,
        mission="ATTACK",
        distance_score=score,
        loot_spice_equivalent=combat.loot_spice_equivalent,
        combat=combat,
        is_spacia=is_spacia,
        is_inactive=is_inactive,
    )


def pick_colony_candidates(
    slots: tuple[GalaxySlot, ...],
    *,
    goal: str = "large_fields",
    min_fields_target: int = 230,
    uninhabited_reports: Optional[Mapping[str, SpyReport]] = None,
) -> list[GalaxySlot]:
    """Filter & rank free galaxy slots for large economy colonies (pos 7-12) or cold Spice (13-16)."""
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
            # Mid-outer slots 7-12 offer best statistical odds for >=230-300+ field rolls
            pos_score = 100.0 - abs(slot.position - 9.5) * 12.0
        if rep and rep.uninhabited_fields:
            pos_score += rep.uninhabited_fields * 0.25
        filtered.append((pos_score, slot))

    filtered.sort(key=lambda pair: (-pair[0], pair[1].position))
    return [slot for _, slot in filtered]
