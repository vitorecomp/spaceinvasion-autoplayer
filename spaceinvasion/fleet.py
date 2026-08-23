"""M5-M6 fleet safety, combat estimation, espionage, Spacia raid, and colony helpers."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
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


def fleet_cargo_capacity(ships: Mapping[str, int], *, miner_spec_level: int = 0) -> int:
    """Return total fleet cargo capacity in units across all non-zero ships."""
    total = 0
    for key, count in ships.items():
        cnt = int(count or 0)
        if cnt <= 0:
            continue
        slug = SHIP_SLUG_BY_KEY.get(str(key), str(key))
        info = game_data.resolve_unit("ships", slug)
        if not info:
            continue
        cargo = int((info.get("stats") or {}).get("cargo", 0))
        total += cnt * cargo
    if miner_spec_level > 0:
        total = int(total * (1.0 + 0.05 * miner_spec_level))
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
    ships_to_save: dict[str, int] = {}
    for key, count in planet.ships.items():
        cnt = int(count or 0)
        if cnt <= 0:
            continue
        # Solar cells (`s9`) are stationary orbital units unless explicitly moved
        if str(key) == "s9" and not save_solar_cells:
            continue
        ships_to_save[str(key)] = cnt

    cap = fleet_cargo_capacity(ships_to_save)
    if not ships_to_save or cap <= 0:
        return EscapeFlightPlan(
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_coords=target_coords,
            ships={},
            cargo=Resources(),
            total_capacity=0,
            fuel_reserved_spice=fuel_reserve_spice,
            duration_hint=duration_hint,
            feasible=False,
            reason="No mobile ships with cargo capacity stationed on planet",
        )

    avail_spice = max(0, planet.resources.spice - max(0, fuel_reserve_spice))
    if planet.resources.spice < min(fuel_reserve_spice, 50) and cap > 0:
        return EscapeFlightPlan(
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_coords=target_coords,
            ships=ships_to_save,
            cargo=Resources(),
            total_capacity=cap,
            fuel_reserved_spice=fuel_reserve_spice,
            duration_hint=duration_hint,
            feasible=False,
            reason=f"Insufficient Spice for escape-flight fuel ({planet.resources.spice} available)",
        )

    rem = max(0, cap - fuel_reserve_spice)
    take_spice = min(avail_spice, rem)
    rem -= take_spice
    take_metal = min(planet.resources.metal, rem)
    rem -= take_metal
    take_kryp = min(planet.resources.kryptonite, rem)
    rem -= take_kryp
    take_pig = min(planet.resources.pig_iron, rem)

    cargo = Resources(
        pig_iron=take_pig,
        metal=take_metal,
        kryptonite=take_kryp,
        spice=take_spice,
    )
    return EscapeFlightPlan(
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_coords=target_coords,
        ships=ships_to_save,
        cargo=cargo,
        total_capacity=cap,
        fuel_reserved_spice=fuel_reserve_spice,
        duration_hint=duration_hint,
        feasible=True,
        reason=f"Packed {cargo.format_short()} into {sum(ships_to_save.values())} ships",
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
    key_map = SHIP_SLUG_BY_KEY if category == "ships" else DEFENSE_SLUG_BY_KEY
    atk_mult = 1.0 + 0.10 * max(0, atk_lvl)
    shd_mult = 1.0 + 0.10 * max(0, shd_lvl)
    def_mult = 1.0 + 0.10 * max(0, def_lvl)

    total_power = 0.0
    stealth_power = 0.0
    for key, cnt in counts.items():
        n = int(cnt or 0)
        if n <= 0:
            continue
        slug = key_map.get(str(key), str(key))
        info = game_data.resolve_unit(category, slug)
        if not info:
            continue
        stats = info.get("stats") or {}
        atk = float(stats.get("attack", 0)) * atk_mult
        shd = float(stats.get("shield", 0)) * shd_mult
        hull = float(stats.get("hull", 0)) * def_mult
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
    safety_margin: float = 1.4,
    plunder_ratio: float = 0.5,
) -> CombatEstimate:
    """Evaluate expected combat margin and cargo-capped loot against a spy report."""
    res_map = attacker_researches or {}
    atk_tech = int(res_map.get("r8", 0))
    shd_tech = int(res_map.get("r9", 0))
    def_tech = int(res_map.get("r10", 0))

    atk_power, stealth_strike = _unit_combat_score(
        "ships",
        attacker_ships,
        atk_lvl=atk_tech,
        shd_lvl=shd_tech,
        def_lvl=def_tech,
    )
    def_ship_power, _ = _unit_combat_score("ships", report.fleet)
    def_turret_power, _ = (
        (0.0, 0.0) if report.dug_in else _unit_combat_score("defense", report.defense)
    )
    # Stealth bombers hit non-dug-in defenses before round 1
    reduced_turret_power = max(0.0, def_turret_power - stealth_strike * 0.5)
    def_power = def_ship_power + reduced_turret_power

    ratio = (atk_power / def_power) if def_power > 0 else (999.0 if atk_power > 0 else 0.0)
    cap = fleet_cargo_capacity(attacker_ships)

    # Plunder up to `plunder_ratio` (default 50%) capped by cargo capacity, highest value first
    max_pig = int(report.resources.pig_iron * plunder_ratio)
    max_met = int(report.resources.metal * plunder_ratio)
    max_kry = int(report.resources.kryptonite * plunder_ratio)
    max_spi = int(report.resources.spice * plunder_ratio)

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
    safe = atk_power > 0 and cap > 0 and ratio >= safety_margin

    if atk_power <= 0 or cap <= 0:
        reason = "No combat/cargo fleet provided"
    elif ratio < safety_margin:
        reason = f"Safety margin too low ({ratio:.2f}x < {safety_margin:.2f}x required for battle luck)"
    else:
        reason = f"Safe margin {ratio:.2f}x >= {safety_margin:.2f}x; est loot {loot_sp_eq:.0f} Spice-eq"

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

    def _prune(self, coords: str, now: float) -> None:
        dq = self._history[coords]
        cutoff = now - 86400.0
        while dq and dq[0] < cutoff:
            dq.popleft()

    def record_attack(self, target_coords: str, *, timestamp: Optional[float] = None) -> None:
        now = time.time() if timestamp is None else float(timestamp)
        self._prune(target_coords, now)
        self._history[target_coords].append(now)

    def attacks_last_24h(self, target_coords: str, *, timestamp: Optional[float] = None) -> int:
        now = time.time() if timestamp is None else float(timestamp)
        self._prune(target_coords, now)
        return len(self._history[target_coords])

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


def _parse_coords_distance(a: str, b: str) -> float:
    pa = [int(x) for x in re.findall(r"\d+", a)[:3]]
    pb = [int(x) for x in re.findall(r"\d+", b)[:3]]
    if len(pa) < 3 or len(pb) < 3:
        return 100.0
    if pa[0] != pb[0]:
        return 20000.0 * abs(pa[0] - pb[0])
    if pa[1] != pb[1]:
        return 2700.0 + 95.0 * abs(pa[1] - pb[1])
    return 1000.0 + 5.0 * abs(pa[2] - pb[2])


_GALAXY_POS_CELL_RE = re.compile(r"^\s*(?:#\s*)?\[?\s*0?(\d{1,2})\s*\]?\.?\s*$")
_GALAXY_BRACKETED_PAIR_RE = re.compile(r"\[\s*(\d{1,2})\s*[:/]\s*(\d{1,3})\s*\](?!\s*[:/]\s*\d)")
_GALAXY_COORDS_TRIPLE_RE = re.compile(r"\b(\d{1,2}):(\d{1,3}):(\d{1,2})\b")
_GALAXY_COORD_CONTEXT_PREFIX_RE = re.compile(
    r"(?:Galaxy|Solar\s*System|System|Sonnensystem)\s*(?::\s*)?$",
    re.IGNORECASE,
)
_GALAXY_PAIR_NOISE_PREFIX_RE = re.compile(
    r"(?:server\s*time|time|clock|uhr|zeit|timer|countdown|eta|updated|stand|fields?|felder)\s*:?\s*$",
    re.IGNORECASE,
)
_GALAXY_TRIPLE_NOISE_SUFFIX_RE = re.compile(
    r"^\s*(?:utc|gmt|cet|cest|mez|mesz|uhr|am|pm)\b",
    re.IGNORECASE,
)
_GALAXY_INACTIVE_TAG_RE = re.compile(r"\(\s*i+\s*\)", re.IGNORECASE)
_GALAXY_YELLOW_HEX_RE = re.compile(
    r"(?<![0-9a-f])#ff0(?![0-9a-f])|(?<![0-9a-f])#ffff00(?![0-9a-f])|rgb\(\s*255\s*,\s*255\s*,\s*0\s*\)",
    re.IGNORECASE,
)
_GALAXY_RED_DARK_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:8b0000|990000|800000)(?![0-9a-f])",
    re.IGNORECASE,
)
_GALAXY_GREEN_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:0f0|00ff00|008000)(?![0-9a-f])|rgb\(\s*0\s*,\s*(?:255|128)\s*,\s*0\s*\)",
    re.IGNORECASE,
)
_GALAXY_BLUE_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:00f|0000ff|3399ff)(?![0-9a-f])|rgb\(\s*0\s*,\s*0\s*,\s*255\s*\)",
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
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:white|spectral)(?![a-z0-9])",
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
_GALAXY_DEBRIS_NUMBER_RE = re.compile(r"^[\d.,\s]+(?:k|m)?\s*(?:debris|tf|metal|pig\s*iron)?$", re.IGNORECASE)


def _valid_gal(val: Any) -> Optional[int]:
    try:
        n = int(str(val).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 99 else None


def _valid_sys(val: Any) -> Optional[int]:
    try:
        n = int(str(val).strip())
    except (TypeError, ValueError):
        return None
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

    # 1. Input fields (`galaxy`/`gal`, `system`/`sun`)
    for inp in soup.find_all("input"):
        name = str(inp.get("name") or inp.get("id") or "").strip().lower()
        val = inp.get("value")
        if val is None:
            continue
        if name in ("galaxy", "gal") and gal_found is None:
            gal_found = _valid_gal(val)
        elif name in ("system", "sun") and sys_found is None:
            sys_found = _valid_sys(val)

    # 2. Selected <option> inside <select name="galaxy|gal|system|sun">
    if gal_found is None or sys_found is None:
        for sel in soup.find_all("select"):
            name = str(sel.get("name") or sel.get("id") or "").strip().lower()
            opt = sel.find("option", selected=True) or sel.find("option", attrs={"selected": True})
            if opt is None:
                continue
            raw_val = opt.get("value") if opt.get("value") is not None else opt.get_text(strip=True)
            if name in ("galaxy", "gal") and gal_found is None:
                gal_found = _valid_gal(raw_val)
            elif name in ("system", "sun") and sys_found is None:
                sys_found = _valid_sys(raw_val)

    if gal_found is not None and sys_found is not None:
        return gal_found, sys_found

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
        re.IGNORECASE,
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
        hg = _valid_gal(m_br.group(1))
        hs = _valid_sys(m_br.group(2))
        if hg is not None and hs is not None:
            return (gal_found or hg), (sys_found or hs)

    # 4. High-confidence `GG:SSS:PP` (`1 <= PP <= 16`) inside clean <a>, bracketed, 3-digit system, coord-context, or >=2 occurrences
    in_link_set: set[tuple[int, int]] = set()
    clean_triples: list[tuple[tuple[int, int], bool, bool, bool, bool]] = []
    clean_triple_counts: Counter[tuple[int, int]] = Counter()
    for m_tr in _GALAXY_COORDS_TRIPLE_RE.finditer(text):
        tg = _valid_gal(m_tr.group(1))
        ts = _valid_sys(m_tr.group(2))
        tp = int(m_tr.group(3))
        if tg is None or ts is None or not (1 <= tp <= 16):
            continue
        is_noise = bool(
            _GALAXY_PAIR_NOISE_PREFIX_RE.search(text[: m_tr.start()])
            or _GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(text[m_tr.end() :])
        )
        if is_noise:
            continue
        key = (tg, ts)
        clean_triple_counts[key] += 1
        m_start, m_end = m_tr.start(), m_tr.end()
        is_in_link = any(s_a <= m_start and m_end <= e_a for s_a, e_a in link_spans)
        if is_in_link:
            in_link_set.add(key)
        before_ch = text[m_start - 1] if m_start > 0 else ""
        after_ch = text[m_end] if m_end < len(text) else ""
        is_bracketed = before_ch == "[" and after_ch == "]"
        is_three_digit_sys = len(m_tr.group(2)) == 3
        has_coord_context = bool(_GALAXY_COORD_CONTEXT_PREFIX_RE.search(text[:m_start]))
        clean_triples.append((key, is_in_link, is_bracketed, is_three_digit_sys, has_coord_context))

    strong_triple_counts: Counter[tuple[int, int]] = Counter()
    for key, is_in_link, is_bracketed, is_three_digit_sys, has_coord_context in clean_triples:
        if is_in_link or key in in_link_set or is_bracketed or is_three_digit_sys or has_coord_context:
            strong_triple_counts[key] += 1

    for key, cnt in clean_triple_counts.items():
        if cnt >= 2 and key not in strong_triple_counts:
            strong_triple_counts[key] = cnt

    if strong_triple_counts:
        (mg, ms), _ = strong_triple_counts.most_common(1)[0]
        return (gal_found or mg), (sys_found or ms)

    # 5. URL query parameters (`galaxy`/`gal`, `system`/`sun`)
    if url:
        qs = parse_qs(urlparse(url).query)
        if gal_found is None:
            for key in ("galaxy", "gal"):
                if key in qs and qs[key]:
                    gal_found = _valid_gal(qs[key][0])
                    if gal_found is not None:
                        break
        if sys_found is None:
            for key in ("system", "sun"):
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
        gal = det_gal if galaxy is None else int(galaxy)
        sys_num = det_sys if system is None else int(system)
    else:
        gal = int(galaxy)
        sys_num = int(system)

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

    return tuple(slots)


def parse_spy_report_html(html: str, *, target_coords: str = "") -> SpyReport:
    """Parse an espionage or uninhabited-ecology report snippet into `SpyReport`."""
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())

    coords = target_coords
    m_c = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", text)
    if m_c and not coords:
        coords = m_c.group(1)

    def _find_res(label: str) -> int:
        m = re.search(rf"{label}\s*:?\s*([\d.]+)", text, re.IGNORECASE)
        return parse_si_int(m.group(1)) if m else 0

    res = Resources(
        pig_iron=_find_res(r"Pig\s+iron"),
        metal=_find_res(r"Metal"),
        kryptonite=_find_res(r"Kryptonite"),
        spice=_find_res(r"Spice"),
    )

    uninhabited_fields: Optional[int] = None
    m_fld = re.search(r"(?:approx\.?|estimated|expected)?\s*(\d{2,3})\s*(?:building\s*)?fields", text, re.IGNORECASE)
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
        m_u = re.search(rf"{re.escape(info['name'])}\s*:?\s*([\d.]+)", text, re.IGNORECASE)
        if m_u:
            fleet[skey] = parse_si_int(m_u.group(1))

    defense: dict[str, int] = {}
    for dkey, slug in DEFENSE_SLUG_BY_KEY.items():
        info = game_data.DEFENSES.get(slug)
        if not info:
            continue
        m_u = re.search(rf"{re.escape(info['name'])}\s*:?\s*([\d.]+)", text, re.IGNORECASE)
        if m_u:
            defense[dkey] = parse_si_int(m_u.group(1))

    is_spacia = "spacia" in text.lower()
    owner = "Spacia" if is_spacia else ""
    if not owner:
        m_own = re.search(
            r"(?:Player|Owner|Spieler)\s*:\s*(.+?)(?=\s+(?:Resources|Pig\s+iron|Metal|Kryptonite|Spice|Fleet|Defense|Buildings|Approx|Dug|\d{1,2}:\d{1,3}:\d{1,2}|$)|$)",
            text,
            re.IGNORECASE,
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

    def _looks_like_spy_block(txt: str) -> bool:
        low = txt.lower()
        return bool(_GALAXY_COORDS_TRIPLE_RE.search(txt)) and any(k in low for k in keywords)

    blocks: list[str] = []
    # 1. Explicit message containers
    containers = soup.select("div.message, div.msg, tr.message, tr.msg, tr[id^='msg'], div[id^='msg']")
    for el in containers:
        if _looks_like_spy_block(el.get_text(" ", strip=True)):
            blocks.append(str(el))

    # 2. Fallback: innermost table rows / divs containing spy blocks without nested spy containers
    if not blocks:
        candidates = soup.find_all(["tr", "div", "li"])
        for el in candidates:
            txt = el.get_text(" ", strip=True)
            if not _looks_like_spy_block(txt):
                continue
            has_child_spy = any(
                child is not el and _looks_like_spy_block(child.get_text(" ", strip=True))
                for child in el.find_all(["tr", "div", "li"])
            )
            if not has_child_spy:
                blocks.append(str(el))

    # 3. Last resort: single report in whole page text
    if not blocks and _looks_like_spy_block(soup.get_text(" ", strip=True)):
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
