"""Reusable E2E fixtures, config helpers, and independent forward sequence evaluator."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any, Mapping, Optional, Sequence
import unittest

from spaceinvasion.combat.flight import BashTracker
from spaceinvasion.combat.points import (
    estimate_account_points,
    estimate_hourly_economy,
    estimate_planet_hourly_economy,
)
from spaceinvasion.combat.simulator import simulate_raid_outcome
from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.catalog import SHIP_SLUG_BY_KEY
from spaceinvasion.parsing.models import SpyReport
from spaceinvasion.core.config import (
    StrategyConfig,
    strategy_config_from_dict,
)
from spaceinvasion.core.model import (
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


def _res_from_dict(raw: Mapping[str, Any]) -> Resources:
    return Resources(
        pig_iron=int(raw.get("pig_iron", 0) or 0),
        metal=int(raw.get("metal", 0) or 0),
        kryptonite=int(raw.get("kryptonite", 0) or 0),
        spice=int(raw.get("spice", 0) or 0),
    )


def make_building_offer(
    bid: int,
    cur_lvl: int = 0,
    *,
    cost: Optional[Resources] = None,
    can_build: bool = True,
    status_text: Optional[str] = None,
    href: Optional[str] = None,
) -> BuildingOffer:
    info = game_data.BUILDING_BY_ID.get(bid) or {}
    name = str(info.get("name") or f"Building {bid}")
    nxt_lvl = int(cur_lvl) + 1
    resolved_cost = cost if cost is not None else _res_from_dict(game_data.building_cost(bid, nxt_lvl))
    resolved_href = (
        href
        if href is not None
        else (f"indexInternal.es?action=internalBuildItem&buildingID={bid}&sid=test#{bid}" if can_build else None)
    )
    return BuildingOffer(
        building_id=bid,
        name=name,
        current_level=int(cur_lvl),
        target_level=nxt_lvl,
        cost=resolved_cost,
        can_build=bool(can_build),
        status_text=status_text or (f"Build level {nxt_lvl}" if can_build else "Unavailable"),
        build_href=resolved_href,
    )


def research_cost_for_level(rkey_or_id: str | int, target_level: int) -> Resources:
    rkey = f"r{rkey_or_id}" if isinstance(rkey_or_id, int) or str(rkey_or_id).isdigit() else str(rkey_or_id)
    info = game_data.RESEARCH_BY_ID.get(rkey) or {}
    levels = info.get("levels") or {}
    if int(target_level) in levels:
        return _res_from_dict((levels[int(target_level)] or {}).get("cost") or {})
    if levels:
        max_lvl = max(int(k) for k in levels.keys())
        base_cost = (levels[max_lvl] or {}).get("cost") or {}
        scale = 2.0 ** max(0, int(target_level) - max_lvl)
        return Resources(
            pig_iron=int(round(float(base_cost.get("pig_iron", 0) or 0) * scale)),
            metal=int(round(float(base_cost.get("metal", 0) or 0) * scale)),
            kryptonite=int(round(float(base_cost.get("kryptonite", 0) or 0) * scale)),
            spice=int(round(float(base_cost.get("spice", 0) or 0) * scale)),
        )
    return Resources()


def make_research_offer(
    rkey_or_id: str | int,
    cur_lvl: int = 0,
    *,
    cost: Optional[Resources] = None,
    can_research: bool = True,
    active: bool = False,
    status_text: Optional[str] = None,
    href: Optional[str] = None,
) -> ResearchOffer:
    rkey = f"r{rkey_or_id}" if isinstance(rkey_or_id, int) or str(rkey_or_id).isdigit() else str(rkey_or_id)
    rid_num = int(rkey[1:])
    info = game_data.RESEARCH_BY_ID.get(rkey) or {}
    name = str(info.get("name") or rkey)
    nxt_lvl = int(cur_lvl) + 1
    resolved_cost = cost if cost is not None else research_cost_for_level(rkey, nxt_lvl)
    resolved_href = (
        href
        if href is not None
        else (f"indexInternal.es?action=internalBuildItem&researchID={rid_num}&sid=test" if can_research else None)
    )
    return ResearchOffer(
        research_id=rid_num,
        research_key=rkey,
        name=name,
        current_level=int(cur_lvl),
        target_level=nxt_lvl,
        cost=resolved_cost,
        can_research=bool(can_research),
        status_text=status_text or (f"Research level {nxt_lvl}" if can_research else "Unavailable"),
        research_href=resolved_href,
        active=bool(active),
    )


def make_ship_offer(
    sid: int,
    owned: int = 0,
    *,
    cost: Optional[Resources] = None,
    can_build: bool = True,
    input_name: Optional[str] = None,
    status_text: Optional[str] = None,
) -> UnitOffer:
    slug = SHIP_SLUG_BY_KEY.get(f"s{sid}")
    info = game_data.SHIPS.get(slug or "") or {}
    name = str(info.get("name") or f"Ship s{sid}")
    resolved_cost = cost if cost is not None else _res_from_dict(info.get("base_cost") or {})
    resolved_inp = input_name if input_name is not None else (f"ship[{sid}]" if can_build else None)
    return UnitOffer(
        unit_type="ship",
        unit_id=int(sid),
        name=name,
        owned=int(owned),
        cost=resolved_cost,
        can_build=bool(can_build),
        input_name=resolved_inp,
        status_text=status_text or ("Buildable" if can_build else "Locked / insufficient"),
    )


def make_planet(
    *,
    planet_id: int = 12476,
    name: str = "Homeworld",
    coords: str = "02:380:12",
    planet_type: str = "normal",
    temperature: int = 31,
    fields_max: int = 230,
    fields_used: Optional[int] = None,
    resources: Resources = Resources(5000, 3000, 2000, 1000),
    storage: Resources = Resources(100_000, 100_000, 100_000, 100_000),
    energy: Optional[EnergyBalance] = None,
    buildings: Optional[dict[str, int]] = None,
    ships: Optional[dict[str, int]] = None,
    defense: Optional[dict[str, int]] = None,
    building_offers: Optional[tuple[BuildingOffer, ...]] = None,
    ship_offers: tuple[UnitOffer, ...] = (),
    defense_offers: tuple[UnitOffer, ...] = (),
    economy_rates: Optional[dict[str, int]] = None,
    researches: Optional[dict[str, int]] = None,
    auto_offers: bool = False,
) -> PlanetState:
    b_map = dict(buildings or {})
    b_names = {
        f"b{bid}": str((game_data.BUILDING_BY_ID.get(bid) or {}).get("name", f"Building {bid}"))
        for bkey in b_map
        for bid in ([int(bkey[1:])] if bkey.startswith("b") and bkey[1:].isdigit() else [])
    }
    used = sum(int(v) for v in b_map.values()) if fields_used is None else int(fields_used)

    if energy is None:
        prod = 0
        dem = 0
        for bkey, lvl in b_map.items():
            if not (bkey.startswith("b") and bkey[1:].isdigit()) or lvl <= 0:
                continue
            bid = int(bkey[1:])
            eval_e = int(game_data.building_level_stats(bid, int(lvl)).get("energy", 0) or 0)
            if eval_e >= 0:
                prod += eval_e
            else:
                dem += -eval_e
        energy = EnergyBalance(produced=prod, used=dem)

    offers = building_offers
    if offers is None:
        if auto_offers:
            res_map = researches or {}
            gen: list[BuildingOffer] = []
            for bid in sorted(game_data.BUILDING_BY_ID.keys()):
                info = game_data.BUILDING_BY_ID[bid]
                if planet_type == "asteroid" and not info.get("asteroid_ok", False):
                    continue
                cur = int(b_map.get(f"b{bid}", 0))
                unmet = game_data.missing_prerequisites("buildings", bid, buildings=b_map, researches=res_map)
                cost = _res_from_dict(game_data.building_cost(bid, cur + 1))
                if not cost.is_zero():
                    can_b = (not unmet) and resources.can_afford(cost) and used < fields_max
                    gen.append(make_building_offer(bid, cur, cost=cost, can_build=can_b))
            offers = tuple(gen)
        else:
            offers = ()

    return PlanetState(
        planet_id=int(planet_id),
        name=name,
        coords=coords,
        planet_type=planet_type,
        temperature=int(temperature),
        fields_used=used,
        fields_max=int(fields_max),
        resources=resources,
        storage=storage,
        energy=energy,
        buildings=b_map,
        building_names=b_names,
        ships=dict(ships or {}),
        defense=dict(defense or {}),
        building_offers=offers,
        ship_offers=ship_offers,
        defense_offers=defense_offers,
        economy_rates=dict(economy_rates or {}),
    )


def make_game_state(
    *,
    planets: Optional[tuple[PlanetState, ...]] = None,
    active_planet_id: Optional[int] = None,
    researches: Optional[dict[str, int]] = None,
    research_offers: tuple[ResearchOffer, ...] = (),
    tutorial: Optional[TutorialStatus] = None,
    active_queues: tuple[QueueJob, ...] = (),
    fleet_alerts: tuple[FleetAlert, ...] = (),
    speedup_factor: Optional[float] = 1.0,
    newbie_protection: bool = True,
    account_points: Optional[int] = None,
    account_rank: Optional[int] = None,
    raid_targets: tuple[dict[str, Any], ...] = (),
    timestamp: float = 1_700_000_000.0,
) -> GameState:
    p_tuple = planets if planets is not None else (make_planet(auto_offers=True),)
    act_id = active_planet_id if active_planet_id is not None else (p_tuple[0].planet_id if p_tuple else None)
    r_map = dict(researches or {})
    r_names = {rk: str((game_data.RESEARCH_BY_ID.get(rk) or {}).get("name", rk)) for rk in r_map}
    return GameState(
        universe_id=31,
        user_id=103603,
        nickname="ventkx86",
        timestamp=float(timestamp),
        planets=p_tuple,
        active_planet_id=act_id,
        researches=r_map,
        research_names=r_names,
        specs={"miner": 0, "fleeter": 0},
        unread_messages={"total": 0},
        tutorial=tutorial,
        active_queues=active_queues,
        fleet_alerts=fleet_alerts,
        research_offers=research_offers,
        speedup_factor=speedup_factor,
        newbie_protection=newbie_protection,
        account_points=account_points,
        account_rank=account_rank,
        raid_targets=raid_targets,
    )


def make_midgame_state(
    *,
    speedup_factor: float = 10.0,
    resources: Resources = Resources(10000, 6000, 4000, 1500),
) -> GameState:
    buildings = {"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 7, "b7": 1}
    researches = {"r2": 1}
    p = make_planet(
        buildings=buildings,
        resources=resources,
        researches=researches,
        auto_offers=True,
    )
    return make_game_state(
        planets=(p,),
        researches=researches,
        speedup_factor=speedup_factor,
    )


@dataclass(frozen=True)
class SequenceEvaluation:
    """Independent opaque-box forward simulation score & legality report."""

    total_score: float
    initial_points: float
    final_points: float
    points_delta: float
    initial_income_spice_eq: float
    final_income_spice_eq: float
    income_delta_spice_eq: float
    raid_loot_spice_eq: float
    legal: bool
    violations: tuple[str, ...] = field(default_factory=tuple)
    final_state: Optional[GameState] = None


def _parse_remaining_hours(text: str) -> Optional[float]:
    raw = str(text or "").strip()
    if not raw:
        return None
    m_d = re.search(r"(\d+)\s*d\s+", raw, re.IGNORECASE)
    days = int(m_d.group(1)) if m_d else 0
    m_hms = re.search(r"(\d+):(\d{2}):(\d{2})", raw)
    if m_hms:
        h, m, s = int(m_hms.group(1)), int(m_hms.group(2)), int(m_hms.group(3))
        return days * 24.0 + h + m / 60.0 + s / 3600.0
    m_ms = re.search(r"(\d+):(\d{2})", raw)
    if m_ms:
        m, s = int(m_ms.group(1)), int(m_ms.group(2))
        return days * 24.0 + m / 60.0 + s / 3600.0
    total_s = 0.0
    matched = False
    for val, unit in re.findall(r"(\d+)\s*([hms])", raw, re.IGNORECASE):
        matched = True
        u = unit.lower()
        if u == "h":
            total_s += int(val) * 3600.0
        elif u == "m":
            total_s += int(val) * 60.0
        else:
            total_s += int(val)
    return (days * 24.0 + total_s / 3600.0) if (matched or days) else None


def _research_time_hours(rkey: str, level: int, b8_level: int, speedup: float) -> float:
    rinfo = game_data.RESEARCH_BY_ID.get(rkey) or {}
    levels = rinfo.get("levels") or {}
    lvl_stats = levels.get(level) or levels.get(str(level)) or {}
    base_s = float(lvl_stats.get("base_time_s") or 0.0)
    if base_s <= 0.0:
        l1_stats = levels.get(1) or levels.get("1") or {}
        base1 = float(l1_stats.get("base_time_s") or rinfo.get("base_time_s") or 0.0)
        base_s = base1 * (2.0 ** max(0, level - 1))
    div = max(1.0, (int(b8_level) + 1) * float(speedup))
    return (base_s / div) / 3600.0


def evaluate_sequence_score(
    state: GameState,
    actions: Sequence[Any],
    *,
    config: Optional[StrategyConfig] = None,
    horizon_hours: float = 6.0,
) -> SequenceEvaluation:
    """Independently simulate `actions` forward using `game_data`, `ranking`, and `fleet` only."""
    cfg = config or StrategyConfig()
    initial_pts = float(estimate_account_points(state)["estimated_points"])
    initial_inc = float(estimate_hourly_economy(state)["spice_eq_per_hr"])

    active_pid = state.active_planet_id or (state.planets[0].planet_id if state.planets else None)
    sim_researches = dict(state.researches or {})
    sim_planets: dict[int, dict[str, Any]] = {}
    for p in state.planets:
        b_ends: list[float] = []
        if p.planet_id == active_pid:
            for q in state.active_queues or ():
                if str(getattr(q, "category", "")).lower() == "building":
                    rem_h = _parse_remaining_hours(getattr(q, "remaining_text", ""))
                    b_ends.append(rem_h if rem_h is not None else horizon_hours + 1.0)
        active_offer_count = sum(1 for o in (p.building_offers or ()) if getattr(o, "active", False))
        while len(b_ends) < active_offer_count:
            b_ends.append(horizon_hours + 1.0)
        sim_planets[p.planet_id] = {
            "planet": p,
            "resources": [
                float(p.resources.pig_iron),
                float(p.resources.metal),
                float(p.resources.kryptonite),
                float(p.resources.spice),
            ],
            "buildings": dict(p.buildings or {}),
            "ships": dict(p.ships or {}),
            "economy_rates": dict(p.economy_rates or {}),
            "energy_prod": int(p.energy.produced),
            "energy_used": int(p.energy.used),
            "fields_used": int(p.fields_used),
            "b_queue_ends": b_ends,
            "initial_b_slots": len(b_ends),
            "exec_b_count": 0,
        }

    violations: list[str] = []
    raid_loot_spice_eq = 0.0
    elapsed_hours = 0.0
    research_busy_until = 0.0
    initial_r_busy = False
    for q in state.active_queues or ():
        if str(getattr(q, "category", "")).lower() == "research":
            initial_r_busy = True
            rem_h = _parse_remaining_hours(getattr(q, "remaining_text", ""))
            research_busy_until = max(research_busy_until, rem_h if rem_h is not None else horizon_hours + 1.0)
    if any(getattr(o, "active", False) for o in (getattr(state, "research_offers", ()) or ())):
        initial_r_busy = True
        if research_busy_until <= 0.0:
            research_busy_until = horizon_hours + 1.0
    active_research: Optional[tuple[str, int, float]] = None
    exec_r_count = 0

    raid_targets_by_coords = {
        str(t.get("coords") or t.get("target_coords") or ""): t
        for t in (state.raid_targets or ())
        if (t.get("coords") or t.get("target_coords"))
    }
    bash = BashTracker()

    def _current_state_obj() -> GameState:
        plist = []
        for p in state.planets:
            sp = sim_planets[p.planet_id]
            res_vals = [max(0, int(round(x))) for x in sp["resources"]]
            plist.append(
                replace(
                    p,
                    resources=Resources.from_list(res_vals),
                    buildings=dict(sp["buildings"]),
                    ships=dict(sp["ships"]),
                    economy_rates=dict(sp["economy_rates"]),
                    energy=EnergyBalance(produced=sp["energy_prod"], used=sp["energy_used"]),
                    fields_used=sp["fields_used"],
                )
            )
        return replace(state, planets=tuple(plist), researches=dict(sim_researches))

    def _flush_completions() -> None:
        nonlocal active_research
        for sp_item in sim_planets.values():
            sp_item["b_queue_ends"] = [t for t in sp_item["b_queue_ends"] if t > elapsed_hours]
        if active_research is not None and active_research[2] <= elapsed_hours:
            sim_researches[active_research[0]] = active_research[1]
            active_research = None

    def _advance_all_planets(dt_hours: float) -> None:
        if dt_hours <= 0:
            return
        cur_state = _current_state_obj()
        for pid_item, sp_item in sim_planets.items():
            p_cur = next(pl for pl in cur_state.planets if pl.planet_id == pid_item)
            econ = estimate_planet_hourly_economy(cur_state, p_cur)
            sp_item["resources"][0] = max(0.0, sp_item["resources"][0] + float(econ["pig_iron_per_hr"]) * dt_hours)
            sp_item["resources"][1] = max(0.0, sp_item["resources"][1] + float(econ["metal_per_hr"]) * dt_hours)
            sp_item["resources"][2] = max(0.0, sp_item["resources"][2] + float(econ["kryptonite_per_hr"]) * dt_hours)
            sp_item["resources"][3] = max(0.0, sp_item["resources"][3] + float(econ["spice_per_hr"]) * dt_hours)

    def _wait_hours_for_cost(pid_target: int, sp_target: dict[str, Any], c_vals: list[float]) -> float:
        missing = [max(0.0, c_vals[i] - sp_target["resources"][i]) for i in range(4)]
        if not any(m > 0 for m in missing):
            return 0.0
        cur_state = _current_state_obj()
        cur_p = next(pl for pl in cur_state.planets if pl.planet_id == pid_target)
        econ = estimate_planet_hourly_economy(cur_state, cur_p)
        rates = [
            float(econ["pig_iron_per_hr"]),
            float(econ["metal_per_hr"]),
            float(econ["kryptonite_per_hr"]),
            float(econ["spice_per_hr"]),
        ]
        wait_needed = 0.0
        for i in range(4):
            if missing[i] > 0:
                if rates[i] <= 0:
                    return horizon_hours + 1.0
                wait_needed = max(wait_needed, missing[i] / rates[i])
        return wait_needed

    speedup = float(state.speedup_factor or 1.0)
    if speedup <= 0:
        speedup = 1.0

    for act in actions:
        _flush_completions()
        kind = str(getattr(act, "kind", ""))
        pid = int(getattr(act, "planet_id", 0))
        sp = sim_planets.get(pid)
        if sp is None:
            violations.append(f"Unknown planet_id {pid} for action {kind}")
            continue
        p_obj: PlanetState = sp["planet"]
        is_exec = bool(getattr(act, "executable_now", False))

        if kind in ("ALERT_HOSTILE", "ESCAPE_FLIGHT", "TUTORIAL_TASK", "SHIP", "SCAN_GALAXY"):
            continue

        if kind == "THROTTLE_BUILDING":
            bid = int(getattr(act, "target_id", 2) or 2)
            pct = 0 if getattr(act, "target_level", None) is None else int(getattr(act, "target_level"))
            rk = f"resource{bid}" if 1 <= bid <= 4 else "resource5"
            sp["economy_rates"][rk] = max(0, min(100, pct))
            continue

        if kind == "ATTACK":
            if not is_exec:
                continue
            if not cfg.allow_raids:
                violations.append("ATTACK executed when allow_raids=False")
                continue
            m = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", str(getattr(act, "target_name", "")))
            if not m:
                violations.append(
                    f"Invalid or missing coordinates on executable ATTACK: {getattr(act, 'target_name', '')!r}"
                )
                continue
            t_coords = m.group(1)
            raw_t = raid_targets_by_coords.get(t_coords)
            if raw_t is None:
                violations.append(f"Unknown ATTACK target coordinates {t_coords!r}")
                continue
            color = str(raw_t.get("status_color") or "yellow").lower()
            is_spacia = bool(raw_t.get("is_spacia", False))
            is_inactive = color == "yellow"
            if not (is_inactive or is_spacia) and not (cfg.allow_pvp_in_az and color == "light_red"):
                violations.append(f"ATTACK disallowed color={color} at {t_coords}")
                continue
            if not bash.can_attack(t_coords, is_inactive=is_inactive, is_spacia=is_spacia):
                violations.append(f"Bash violation at {t_coords}")
                continue
            res_map = raw_t.get("resources") if isinstance(raw_t.get("resources"), dict) else raw_t
            rep = SpyReport(
                target_coords=t_coords,
                owner=str(raw_t.get("owner") or ""),
                resources=_res_from_dict(res_map or {}),
                fleet={str(k): int(v) for k, v in (raw_t.get("fleet") or {}).items()},
                defense={str(k): int(v) for k, v in (raw_t.get("defense") or {}).items()},
                is_spacia=is_spacia,
            )
            atk_ships = {
                k: int(v)
                for k, v in sp["ships"].items()
                if k in ("s4", "s5", "s6", "s7", "s13", "s14", "s15", "s16", "s1", "s2", "s3") and int(v) > 0
            }
            combat = simulate_raid_outcome(
                atk_ships,
                rep,
                attacker_researches=sim_researches,
                safety_margin=cfg.combat_safety_margin,
            )
            if not combat.safe_to_attack:
                violations.append(f"Unsafe combat ratio {combat.power_ratio:.2f} at {t_coords}")
                continue
            bash.record_attack(t_coords)
            raid_loot_spice_eq += float(combat.loot_spice_equivalent)
            sp["resources"][0] += combat.expected_loot.pig_iron
            sp["resources"][1] += combat.expected_loot.metal
            sp["resources"][2] += combat.expected_loot.kryptonite
            sp["resources"][3] += combat.expected_loot.spice
            continue

        if kind in ("BUILD", "WAIT_RESOURCES") and getattr(act, "target_id", None) is not None:
            bid = int(getattr(act, "target_id"))
            info = game_data.BUILDING_BY_ID.get(bid)
            if not info:
                violations.append(f"Unknown building id b{bid} on planet {pid}")
                continue
            if p_obj.planet_type == "asteroid" and not info.get("asteroid_ok", False):
                violations.append(f"Non-asteroid building b{bid} on asteroid {pid}")
                continue
            if p_obj.planet_type == "raid" and bid in (1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14, 19, 20, 21):
                violations.append(f"Economy/banned building b{bid} on raid colony {pid}")
                continue
            if p_obj.fields_max > 0 and sp["fields_used"] >= p_obj.fields_max:
                violations.append(f"Fields exhausted on planet {pid} before b{bid}")
                continue
            max_b_slots = 1 if p_obj.planet_type == "asteroid" else 5
            unmet = game_data.missing_prerequisites(
                "buildings", bid, buildings=sp["buildings"], researches=sim_researches
            )
            if unmet and not is_exec and active_research is not None and active_research[2] <= horizon_hours:
                dt_r = max(0.0, active_research[2] - elapsed_hours)
                _advance_all_planets(dt_r)
                elapsed_hours += dt_r
                _flush_completions()
                unmet = game_data.missing_prerequisites(
                    "buildings", bid, buildings=sp["buildings"], researches=sim_researches
                )
            if unmet:
                violations.append(f"Unmet prereqs for b{bid} on planet {pid}: {unmet}")
                continue
            cur_lvl = int(sp["buildings"].get(f"b{bid}", 0))
            if getattr(act, "target_level", None) is not None and int(getattr(act, "target_level")) <= cur_lvl:
                violations.append(
                    f"Invalid target_level {getattr(act, 'target_level')} <= {cur_lvl} for b{bid} on planet {pid}"
                )
                continue
            nxt_lvl = cur_lvl + 1
            cost = game_data.building_cost(bid, nxt_lvl)
            c_vals = [float(cost.get(rk, 0)) for rk in ("pig_iron", "metal", "kryptonite", "spice")]
            if kind == "BUILD" and is_exec:
                sp["exec_b_count"] += 1
                if len(sp["b_queue_ends"]) >= max_b_slots or (sp["initial_b_slots"] + sp["exec_b_count"]) > max_b_slots:
                    violations.append(f"Building queue limit ({max_b_slots}) exceeded on planet {pid} for b{bid}")
                    continue
                if any(sp["resources"][i] + 1e-6 < c_vals[i] for i in range(4)):
                    violations.append(f"Insufficient resources for executable BUILD b{bid} L{nxt_lvl} on planet {pid}")
                    continue
                wait_h = 0.0
            else:
                if len(sp["b_queue_ends"]) >= max_b_slots:
                    slot_wait = max(0.0, min(sp["b_queue_ends"]) - elapsed_hours)
                    if elapsed_hours + slot_wait > horizon_hours:
                        continue
                    _advance_all_planets(slot_wait)
                    elapsed_hours += slot_wait
                    _flush_completions()
                wait_h = _wait_hours_for_cost(pid, sp, c_vals)
            if elapsed_hours + wait_h > horizon_hours:
                continue
            _advance_all_planets(wait_h)
            elapsed_hours += wait_h
            _flush_completions()
            for i in range(4):
                sp["resources"][i] = max(0.0, sp["resources"][i] - c_vals[i])
            base_s = float(game_data.building_level_stats(bid, nxt_lvl).get("base_time_s", 0.0) or 0.0)
            div = max(
                1.0, (int(sp["buildings"].get("b7", 0)) + 1) * (2 ** int(sp["buildings"].get("b17", 0))) * speedup
            )
            b_hrs = (base_s / div) / 3600.0
            sp["b_queue_ends"].append(elapsed_hours + b_hrs)
            if elapsed_hours + b_hrs <= horizon_hours:
                _advance_all_planets(b_hrs)
                elapsed_hours += b_hrs
                _flush_completions()
                sp["buildings"][f"b{bid}"] = nxt_lvl
                sp["fields_used"] += 1
                edelta = game_data.building_energy_delta(bid, nxt_lvl)
                if edelta >= 0:
                    sp["energy_prod"] += edelta
                else:
                    sp["energy_used"] += -edelta
            continue

        if kind == "RESEARCH" and getattr(act, "target_id", None) is not None:
            rid = int(getattr(act, "target_id"))
            rkey = f"r{rid}"
            if rkey not in game_data.RESEARCH_BY_ID:
                violations.append(f"Unknown research id {rkey}")
                continue
            if int(sp["buildings"].get("b8", 0)) <= 0:
                if is_exec:
                    violations.append(f"Executable RESEARCH {rkey} without Research lab on {pid}")
                continue
            if is_exec:
                exec_r_count += 1
                if initial_r_busy or exec_r_count > 1 or research_busy_until > elapsed_hours:
                    violations.append(f"Global research slot limit (1) exceeded on executable RESEARCH {rkey}")
                    continue
            elif research_busy_until > elapsed_hours:
                slot_wait = max(0.0, research_busy_until - elapsed_hours)
                if elapsed_hours + slot_wait > horizon_hours:
                    continue
                _advance_all_planets(slot_wait)
                elapsed_hours += slot_wait
                _flush_completions()
            unmet = game_data.missing_prerequisites(
                "research", rkey, buildings=sp["buildings"], researches=sim_researches
            )
            if unmet:
                if is_exec:
                    violations.append(f"Executable RESEARCH {rkey} with unmet prereqs {unmet}")
                continue
            cur_rlvl = int(sim_researches.get(rkey, 0))
            if getattr(act, "target_level", None) is not None and int(getattr(act, "target_level")) <= cur_rlvl:
                violations.append(
                    f"Invalid target_level {getattr(act, 'target_level')} <= {cur_rlvl} for RESEARCH {rkey}"
                )
                continue
            nxt_lvl = cur_rlvl + 1
            cost = research_cost_for_level(rkey, nxt_lvl)
            c_vals = [float(cost.pig_iron), float(cost.metal), float(cost.kryptonite), float(cost.spice)]
            if any(sp["resources"][i] + 1e-6 < c_vals[i] for i in range(4)):
                if is_exec:
                    violations.append(
                        f"Insufficient resources for executable RESEARCH {rkey} L{nxt_lvl} on planet {pid}"
                    )
                    continue
                wait_h = _wait_hours_for_cost(pid, sp, c_vals)
                if elapsed_hours + wait_h > horizon_hours:
                    continue
                _advance_all_planets(wait_h)
                elapsed_hours += wait_h
                _flush_completions()
            if any(sp["resources"][i] + 1e-6 < c_vals[i] for i in range(4)):
                continue
            for i in range(4):
                sp["resources"][i] = max(0.0, sp["resources"][i] - c_vals[i])
            r_hrs = _research_time_hours(rkey, nxt_lvl, int(sp["buildings"].get("b8", 0)), speedup)
            research_busy_until = elapsed_hours + r_hrs
            active_research = (rkey, nxt_lvl, research_busy_until)
            continue

        if kind == "BUILD_SHIP" and getattr(act, "target_id", None) is not None:
            sid_num = int(getattr(act, "target_id"))
            skey = f"s{sid_num}"
            slug = SHIP_SLUG_BY_KEY.get(skey)
            if not slug:
                violations.append(f"Unknown ship id {skey} on planet {pid}")
                continue
            if int(sp["buildings"].get("b9", 0)) <= 0:
                if is_exec:
                    violations.append(f"Executable BUILD_SHIP {skey} without Weapons factory on {pid}")
                continue
            unmet = game_data.missing_prerequisites("ships", slug, buildings=sp["buildings"], researches=sim_researches)
            if unmet:
                if is_exec:
                    violations.append(f"Executable BUILD_SHIP {skey} with unmet prereqs {unmet}")
                continue
            qty = max(1, int(getattr(act, "target_level", 1) or 1))
            base_cost = (game_data.SHIPS.get(slug) or {}).get("base_cost") or {}
            c_vals = [float(base_cost.get(rk, 0) or 0) * qty for rk in ("pig_iron", "metal", "kryptonite", "spice")]
            if any(sp["resources"][i] + 1e-6 < c_vals[i] for i in range(4)):
                if is_exec:
                    violations.append(f"Insufficient resources for executable BUILD_SHIP {qty}x {skey} on planet {pid}")
                continue
            for i in range(4):
                sp["resources"][i] = max(0.0, sp["resources"][i] - c_vals[i])
            sp["ships"][skey] = int(sp["ships"].get(skey, 0)) + qty
            if skey == "s9":
                e_map = (game_data.SHIPS["solar-cells"] or {}).get("solar_cell_energy_by_temp") or {}
                per_cell = int(round(game_data.interpolate_temp_value(e_map, p_obj.temperature)))
                sp["energy_prod"] += per_cell * qty

    if active_research is not None and active_research[2] <= horizon_hours:
        rem_r = max(0.0, active_research[2] - elapsed_hours)
        _advance_all_planets(rem_r)
        elapsed_hours += rem_r
        _flush_completions()

    final_state = _current_state_obj()
    final_pts = float(estimate_account_points(final_state)["estimated_points"])
    final_inc = float(estimate_hourly_economy(final_state)["spice_eq_per_hr"])
    pts_delta = final_pts - initial_pts
    inc_delta = final_inc - initial_inc
    penalty = 10_000.0 * len(violations)
    total_score = (pts_delta * 0.25) + final_inc + (inc_delta * 5.0) + (raid_loot_spice_eq * 0.05) - penalty
    return SequenceEvaluation(
        total_score=round(total_score, 4),
        initial_points=initial_pts,
        final_points=final_pts,
        points_delta=round(pts_delta, 4),
        initial_income_spice_eq=initial_inc,
        final_income_spice_eq=final_inc,
        income_delta_spice_eq=round(inc_delta, 4),
        raid_loot_spice_eq=round(raid_loot_spice_eq, 2),
        legal=len(violations) == 0,
        violations=tuple(violations),
        final_state=final_state,
    )
