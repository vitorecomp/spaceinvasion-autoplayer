"""M5-M6 fleet safety, combat estimation, espionage, Spacia raid, and colony helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import re
import time
from typing import Any, Deque, Mapping, Optional

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


def parse_galaxy_system_html(html: str, *, galaxy: int = 1, system: int = 1) -> tuple[GalaxySlot, ...]:
    """Parse galaxy system rows for status colors, Spacia flags, empty slots, and debris."""
    soup = BeautifulSoup(html, "html.parser")
    slots: list[GalaxySlot] = []
    seen_pos: set[int] = set()

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        pos_txt = tds[0].get_text(" ", strip=True)
        m_pos = re.fullmatch(r"(\d{1,2})", pos_txt)
        if not m_pos:
            continue
        pos = int(m_pos.group(1))
        if not (1 <= pos <= 16) or pos in seen_pos:
            continue
        seen_pos.add(pos)

        row_html = str(tr).lower()
        row_text = " ".join(tr.get_text(" ", strip=True).split())
        owner = ""
        planet_name = ""
        if len(tds) >= 3:
            planet_name = tds[1].get_text(" ", strip=True)
            owner = tds[2].get_text(" ", strip=True)

        status_color = "free" if not (planet_name or owner) else "light_red"
        for color_key, tokens in (
            ("yellow", ("yellow", "inaktiv", "inactive", "spacia", "#ff0", "#ffff00")),
            ("dark_red", ("darkred", "dark_red", "too strong")),
            ("blue", ("blue", "vacation", "urlaub", "newbie")),
            ("green", ("green", "alliance", "ally")),
            ("white", ("white", "spectral")),
            ("purple", ("purple", "pink", "operator")),
        ):
            if any(tok in row_html for tok in tokens):
                status_color = color_key
                break

        has_debris = any(tok in row_html for tok in ("debris", "trümmer", "rubble", "recycle"))
        is_spacia = "spacia" in row_text.lower() or "spacia" in row_html
        if is_spacia:
            status_color = "yellow"

        spy_a = tr.select_one('a[href*="Spy"], a[href*="spio"], a[href*="espionage"]')
        coords = f"{galaxy:02d}:{system:03d}:{pos:02d}"
        slots.append(
            GalaxySlot(
                coords=coords,
                position=pos,
                name=planet_name,
                owner=owner,
                status_color=status_color,
                has_debris=has_debris,
                is_spacia=is_spacia,
                spy_href=spy_a.get("href") if spy_a else None,
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

    return SpyReport(
        target_coords=coords,
        owner="Spacia" if "spacia" in text.lower() else "",
        resources=res,
        fleet=fleet,
        defense=defense,
        dug_in=dug_in,
        uninhabited_fields=uninhabited_fields,
        is_spacia="spacia" in text.lower(),
    )


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
) -> Optional[RaidRecommendation]:
    """Score a scanned target for safe profitable raiding (favoring overlooked Spacia/inactives)."""
    is_inactive = slot.status_color == "yellow"
    is_spacia = slot.is_spacia or report.is_spacia
    if slot.status_color not in ("yellow", "light_red"):
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
