"""Pure SpaceInvasion Classic 1:1 zero-rapidfire combat simulator and raid scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from typing import Protocol

from backend.combat.cargo import fleet_cargo_capacity
from backend.combat.flight import (
    BashTracker,
    estimate_fleet_flight_seconds,
    estimate_resource_per_hour,
    parse_coords_distance,
)
from backend.core.catalog import (
    DEFENSE_SLUG_BY_KEY,
    SHIP_SLUG_BY_KEY,
    cap_json_int,
    resolve_unit,
    safe_finite_float,
    safe_int,
    safe_key,
    spice_equivalent,
)
from backend.core.model import GameState, Resources


class SpyReportLike(Protocol):
    """Read-only structural protocol satisfied by both new and legacy `SpyReport` dataclasses."""

    @property
    def target_coords(self) -> str: ...

    @property
    def owner(self) -> str: ...

    @property
    def resources(self) -> Resources: ...

    @property
    def fleet(self) -> Mapping[str, int]: ...

    @property
    def defense(self) -> Mapping[str, int]: ...

    @property
    def dug_in(self) -> bool | None: ...

    @property
    def is_spacia(self) -> bool: ...


class GalaxySlotLike(Protocol):
    """Read-only structural protocol satisfied by both new and legacy `GalaxySlot` dataclasses."""

    @property
    def coords(self) -> str: ...

    @property
    def owner(self) -> str: ...

    @property
    def status_color(self) -> str: ...

    @property
    def is_spacia(self) -> bool: ...


@dataclass(frozen=True)
class CombatReport:
    """Minimal pure-domain espionage snapshot consumed by `simulate_raid_outcome`."""

    target_coords: str
    owner: str = ""
    resources: Resources = Resources()
    fleet: dict[str, int] = field(default_factory=dict)
    defense: dict[str, int] = field(default_factory=dict)
    dug_in: bool | None = None
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
    mission: str
    distance_score: float
    loot_spice_equivalent: float
    combat: CombatEstimate
    is_spacia: bool = False
    is_inactive: bool = False
    total_plunder_resources: int = 0
    flight_seconds: float = 0.0
    resources_per_hour: float = 0.0


def _nonneg_int(value: object) -> int:
    return max(0, safe_int(value, 0))


def unit_combat_score(
    category: str,
    counts: Mapping[str, int],
    *,
    atk_lvl: int = 0,
    shd_lvl: int = 0,
    def_lvl: int = 0,
) -> tuple[float, float]:
    """Return `(weighted_power, stealth_strike)` respecting +10%/lvl techs & 1:1 zero rapidfire."""
    if not isinstance(counts, Mapping):
        return 0.0, 0.0
    key_map = SHIP_SLUG_BY_KEY if category == "ships" else DEFENSE_SLUG_BY_KEY
    atk_mult = 1.0 + 0.10 * min(100_000, _nonneg_int(atk_lvl))
    shd_mult = 1.0 + 0.10 * min(100_000, _nonneg_int(shd_lvl))
    def_mult = 1.0 + 0.10 * min(100_000, _nonneg_int(def_lvl))

    total_power = 0.0
    stealth_power = 0.0
    for key, cnt in counts.items():
        n = min(10**100, safe_int(cnt, 0))
        if n <= 0:
            continue
        key_s = safe_key(key)
        slug = key_map.get(key_s, key_s)
        info = resolve_unit(category, slug)
        if not info:
            continue
        stats = info.get("stats")
        if not isinstance(stats, Mapping):
            continue
        atk = float(safe_finite_float(stats.get("attack", 0), 0.0)) * atk_mult
        shd = float(safe_finite_float(stats.get("shield", 0), 0.0)) * shd_mult
        hull = float(safe_finite_float(stats.get("hull", 0), 0.0)) * def_mult
        total_power += n * (atk + (shd * 0.5) + (hull * 0.04))
        if slug == "stealth-bomber":
            stealth_power += n * atk
    return total_power, stealth_power


def _plunder_cap(raw_amt: object, ratio: float) -> int:
    amt = _nonneg_int(raw_amt)
    if amt <= 0 or ratio <= 0.0:
        return 0
    try:
        return max(0, int(amt * ratio))
    except OverflowError:
        return max(0, (amt * int(round(ratio * 10_000))) // 10_000)


def _compute_expected_loot(resources: Resources, cap: int, plunder_ratio: float) -> Resources:
    max_pig = _plunder_cap(resources.pig_iron, plunder_ratio)
    max_met = _plunder_cap(resources.metal, plunder_ratio)
    max_kry = _plunder_cap(resources.kryptonite, plunder_ratio)
    max_spi = _plunder_cap(resources.spice, plunder_ratio)

    rem = cap
    loot_spi = min(max_spi, rem)
    rem -= loot_spi
    loot_met = min(max_met, rem)
    rem -= loot_met
    loot_kry = min(max_kry, rem)
    rem -= loot_kry
    loot_pig = min(max_pig, rem)
    return Resources(pig_iron=loot_pig, metal=loot_met, kryptonite=loot_kry, spice=loot_spi)


def simulate_raid_outcome(
    attacker_ships: Mapping[str, int],
    report: SpyReportLike,
    *,
    attacker_researches: Mapping[str, int] | None = None,
    defender_researches: Mapping[str, int] | None = None,
    safety_margin: float = 1.4,
    plunder_ratio: float = 0.5,
) -> CombatEstimate:
    """Evaluate expected 1:1 zero-rapidfire combat margin and cargo-capped loot."""
    a_res = attacker_researches if isinstance(attacker_researches, Mapping) else {}
    d_res = defender_researches if isinstance(defender_researches, Mapping) else {}

    atk_power, stealth_strike = unit_combat_score(
        "ships",
        attacker_ships,
        atk_lvl=_nonneg_int(a_res.get("r8", 0)),
        shd_lvl=_nonneg_int(a_res.get("r9", 0)),
        def_lvl=_nonneg_int(a_res.get("r10", 0)),
    )
    def_ship_power, _ = unit_combat_score(
        "ships",
        report.fleet,
        atk_lvl=_nonneg_int(d_res.get("r8", 0)),
        shd_lvl=_nonneg_int(d_res.get("r9", 0)),
        def_lvl=_nonneg_int(d_res.get("r10", 0)),
    )
    def_turret_power, _ = (
        (0.0, 0.0)
        if report.dug_in
        else unit_combat_score(
            "defense",
            report.defense,
            atk_lvl=_nonneg_int(d_res.get("r8", 0)),
            shd_lvl=_nonneg_int(d_res.get("r9", 0)),
            def_lvl=_nonneg_int(d_res.get("r10", 0)),
        )
    )
    def_power = def_ship_power + max(0.0, def_turret_power - stealth_strike * 0.5)

    p_ratio = max(0.0, min(1.0, safe_finite_float(plunder_ratio, 0.0)))
    raw_margin = safe_finite_float(safety_margin, 1.4)
    s_margin = raw_margin if (math.isfinite(raw_margin) and raw_margin >= 0.0) else 1.4

    ratio = (atk_power / def_power) if def_power > 0 else (999.0 if atk_power > 0 else 0.0)
    cap = fleet_cargo_capacity(attacker_ships)
    loot = _compute_expected_loot(report.resources, cap, p_ratio)
    loot_sp_eq = spice_equivalent(loot)
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


def score_raid_target(
    origin_coords: str,
    slot: GalaxySlotLike,
    report: SpyReportLike,
    attacker_ships: Mapping[str, int],
    *,
    attacker_researches: Mapping[str, int] | None = None,
    bash_tracker: BashTracker | None = None,
    safety_margin: float = 1.4,
    min_loot_spice_eq: float = 1500.0,
    allow_pvp_in_az: bool = False,
    timestamp: float | None = None,
) -> RaidRecommendation | None:
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

    dist = parse_coords_distance(origin_coords, slot.coords)
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
        resources_per_hour=estimate_resource_per_hour(total_plunder, flight_s),
    )


def simulate_combat_matchup(
    state: GameState,
    *,
    attacker_ships: Mapping[str, int],
    defender_ships: Mapping[str, int] | None = None,
    defender_defense: Mapping[str, int] | None = None,
    defender_researches: Mapping[str, int] | None = None,
    target_resources: Mapping[str, int] | None = None,
    target_coords: str = "01:001:01",
    safety_margin: float = 1.4,
) -> dict[str, object]:
    """Run 1:1 zero-rapidfire combat simulator with attacker's current techs and safety threshold."""
    clean_atk = {safe_key(k): cnt for k, v in attacker_ships.items() if (cnt := _nonneg_int(v)) > 0}
    clean_def_ships = (
        {safe_key(k): cnt for k, v in defender_ships.items() if (cnt := _nonneg_int(v)) > 0}
        if isinstance(defender_ships, Mapping)
        else {}
    )
    clean_def_defs = (
        {safe_key(k): cnt for k, v in defender_defense.items() if (cnt := _nonneg_int(v)) > 0}
        if isinstance(defender_defense, Mapping)
        else {}
    )
    clean_def_res = (
        {safe_key(k): cnt for k, v in defender_researches.items() if (cnt := _nonneg_int(v)) > 0}
        if isinstance(defender_researches, Mapping)
        else {}
    )
    res_map = target_resources if isinstance(target_resources, Mapping) else {}
    report = CombatReport(
        target_coords=target_coords,
        owner="Simulated Target",
        resources=Resources(
            pig_iron=_nonneg_int(res_map.get("pig_iron", 0)),
            metal=_nonneg_int(res_map.get("metal", 0)),
            kryptonite=_nonneg_int(res_map.get("kryptonite", 0)),
            spice=_nonneg_int(res_map.get("spice", 0)),
        ),
        fleet=clean_def_ships,
        defense=clean_def_defs,
    )
    raw_margin = safe_finite_float(safety_margin, 1.4)
    eff_margin = max(1.0, raw_margin if raw_margin > 0.0 else 1.4)
    est = simulate_raid_outcome(
        clean_atk,
        report,
        attacker_researches=state.researches,
        defender_researches=clean_def_res,
        safety_margin=eff_margin,
    )
    plunder_map = {
        "pig_iron": cap_json_int(max(0, int(est.expected_loot.pig_iron))),
        "metal": cap_json_int(max(0, int(est.expected_loot.metal))),
        "kryptonite": cap_json_int(max(0, int(est.expected_loot.kryptonite))),
        "spice": cap_json_int(max(0, int(est.expected_loot.spice))),
    }
    return {
        "safe_to_attack": est.safe_to_attack,
        "win_ratio": est.power_ratio,
        "safety_margin": eff_margin,
        "attacker_power": est.attacker_power,
        "defender_power": est.defender_power,
        "defender_researches": {k: cap_json_int(v) for k, v in clean_def_res.items()},
        "stealth_first_strike_power": est.stealth_first_strike_power,
        "cargo_capacity": cap_json_int(max(0, int(est.cargo_capacity))),
        "loot_spice_equivalent": est.loot_spice_equivalent,
        "expected_plunder": plunder_map,
        "total_plunder": cap_json_int(sum(plunder_map.values())),
        "notes": est.reason,
    }
