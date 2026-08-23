"""Priority-ordered decision planner for SpaceInvasion Classic."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
import time
from typing import Mapping, Optional

from spaceinvasion import game_data
from spaceinvasion.config import StrategyConfig
from spaceinvasion.fleet import (
    SHIP_SLUG_BY_KEY,
    GalaxySlot,
    SpyReport,
    plan_escape_flight,
    score_raid_target,
)
from spaceinvasion.state import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    UnitOffer,
)

# SpaceInvasion building IDs matching `internalBuildings`
FUSION_PLANT = 5
THERMOSOLAR_PLANT = 6
PIG_IRON_MINE = 1
BLAST_FURNACE = 2
KRYPTONITE_MINE = 3
SPICE_MINE = 4
DEVELOPMENT_CENTER = 7
RESEARCH_LAB = 8
WEAPONS_FACTORY = 9
PIG_IRON_WAREHOUSE = 11
METAL_WAREHOUSE = 12
KRYPTONITE_WAREHOUSE = 13
SPICE_WAREHOUSE = 14

SOLAR_CELLS_ID = 9

_MINE_IDS = (PIG_IRON_MINE, BLAST_FURNACE, KRYPTONITE_MINE, SPICE_MINE)
_MINE_RES_KEY = {
    PIG_IRON_MINE: "pig_iron",
    BLAST_FURNACE: "metal",
    KRYPTONITE_MINE: "kryptonite",
    SPICE_MINE: "spice",
}
_MINE_LABELS = {
    PIG_IRON_MINE: "Pig iron mine",
    BLAST_FURNACE: "Blast furnace",
    KRYPTONITE_MINE: "Kryptonite mine",
    SPICE_MINE: "Spice mine",
}
_WAREHOUSE_BY_RESOURCE = {
    "pig_iron": (PIG_IRON_WAREHOUSE, "Pig iron warehouse"),
    "metal": (METAL_WAREHOUSE, "Metal warehouse"),
    "kryptonite": (KRYPTONITE_WAREHOUSE, "Kryptonite warehouse"),
    "spice": (SPICE_WAREHOUSE, "Spice warehouse"),
}


@dataclass(frozen=True)
class PlannedAction:
    """Single recommended or blocked empire action produced by the planner."""

    priority: str  # P0 (survival), P1 (safety), P2 (tutorial/economy/fleet/combat), P3 (expansion)
    kind: str      # ALERT_HOSTILE, ESCAPE_FLIGHT, BUILD, WAIT_RESOURCES, RESEARCH, BUILD_SHIP, SCAN_GALAXY, ATTACK, THROTTLE_BUILDING
    planet_id: int
    planet_coords: str
    target_name: str
    reason: str
    target_id: Optional[int] = None
    target_level: Optional[int] = None  # Or unit quantity for BUILD_SHIP
    cost: Resources = Resources()
    missing: Resources = Resources()
    executable_now: bool = False
    action_href: Optional[str] = None


def _resources_from_dict(raw: dict[str, int]) -> Resources:
    return Resources(
        pig_iron=int(raw.get("pig_iron", 0)),
        metal=int(raw.get("metal", 0)),
        kryptonite=int(raw.get("kryptonite", 0)),
        spice=int(raw.get("spice", 0)),
    )


def _offer_map(planet: PlanetState) -> dict[int, BuildingOffer]:
    return {offer.building_id: offer for offer in planet.building_offers}


def _research_offer_map(state: GameState) -> dict[str, ResearchOffer]:
    return {offer.research_key: offer for offer in state.research_offers}


def _ship_offer_map(planet: PlanetState) -> dict[int, UnitOffer]:
    return {offer.unit_id: offer for offer in planet.ship_offers}


def _apply_queue_cap_if_full(action: PlannedAction, queue_full: bool) -> PlannedAction:
    if not (queue_full and action.executable_now):
        return action
    return PlannedAction(
        priority=action.priority,
        kind="WAIT_RESOURCES",
        planet_id=action.planet_id,
        planet_coords=action.planet_coords,
        target_id=action.target_id,
        target_name=action.target_name,
        target_level=action.target_level,
        cost=action.cost,
        missing=action.missing,
        reason=f"{action.reason} (build queue full)",
        executable_now=False,
    )


def _make_build_action(
    priority: str,
    planet: PlanetState,
    building_id: int,
    fallback_name: str,
    reason: str,
    offers: dict[int, BuildingOffer],
    *,
    effective_level: Optional[int] = None,
    researches: Optional[dict[str, int]] = None,
    allow_remote_colony_build: bool = False,
) -> PlannedAction:
    offer = offers.get(building_id)
    if offer is None:
        cur_lvl = effective_level if effective_level is not None else planet.building_level(building_id)
        nxt_lvl = cur_lvl + 1
        wiki_cost = _resources_from_dict(game_data.building_cost(building_id, nxt_lvl))
        missing = planet.resources.missing(wiki_cost) if not wiki_cost.is_zero() else Resources()
        prereq_ok = not game_data.missing_prerequisites(
            "buildings",
            building_id,
            buildings=planet.buildings,
            researches=researches or {},
        )
        remote_ready = (
            allow_remote_colony_build
            and not wiki_cost.is_zero()
            and planet.resources.can_afford(wiki_cost)
            and prereq_ok
        )
        return PlannedAction(
            priority=priority,
            kind="BUILD" if remote_ready else "WAIT_RESOURCES",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=building_id,
            target_name=fallback_name,
            target_level=nxt_lvl,
            cost=wiki_cost,
            missing=missing,
            reason=reason if remote_ready else f"{reason} (building card not currently visible/unlocked)",
            executable_now=remote_ready,
        )

    missing = planet.resources.missing(offer.cost)
    can_exec = offer.can_build and planet.resources.can_afford(offer.cost)
    return PlannedAction(
        priority=priority,
        kind="BUILD" if can_exec else "WAIT_RESOURCES",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=offer.building_id,
        target_name=offer.name,
        target_level=offer.target_level,
        cost=offer.cost,
        missing=missing,
        reason=reason,
        executable_now=can_exec,
        action_href=offer.build_href if can_exec else None,
    )


def _queued_building_counts(state: GameState) -> dict[int, int]:
    """Count active/queued construction jobs per building ID."""
    counts: dict[int, int] = {}
    for job in state.active_queues:
        if job.category == "building" and job.building_id is not None:
            counts[job.building_id] = counts.get(job.building_id, 0) + 1
    return counts


def _effective_building_levels_and_energy(
    state: GameState,
    planet: PlanetState,
    *,
    is_active_planet: bool,
) -> tuple[dict[str, int], int]:
    """Project building levels and energy net after active queues complete."""
    sim_levels: dict[int, int] = {}
    eff_map: dict[str, int] = {str(k): int(v) for k, v in planet.buildings.items()}
    projected_net = planet.energy.net

    if not is_active_planet:
        return eff_map, projected_net

    for job in state.active_queues:
        if job.category != "building" or job.building_id is None:
            continue
        bid = int(job.building_id)
        cur_lvl = sim_levels.get(bid, planet.building_level(bid))
        tgt = int(job.target_level) if job.target_level and job.target_level > cur_lvl else cur_lvl + 1
        for step_lvl in range(cur_lvl + 1, tgt + 1):
            projected_net += game_data.building_energy_delta(bid, step_lvl)
        sim_levels[bid] = tgt
        eff_map[f"b{bid}"] = tgt

    return eff_map, projected_net


def _eff_level(planet: PlanetState, eff_buildings: dict[str, int], building_id: int) -> int:
    key = f"b{building_id}"
    return int(eff_buildings[key]) if key in eff_buildings else planet.building_level(building_id)


def _energy_starvation_delta(building_id: int, next_level: int, projected_energy_net: int) -> int:
    delta = game_data.building_energy_delta(building_id, next_level)
    if delta < 0 and (projected_energy_net + delta) < 0:
        return delta
    return 0


def _pick_best_power_building(
    planet: PlanetState,
    eff_buildings: dict[str, int],
    researches: dict[str, int],
) -> tuple[int, str, str]:
    """Select between Fusion plant (`b5`) and Thermosolar plant (`b6`) by cost per energy."""
    fusion_lvl = _eff_level(planet, eff_buildings, FUSION_PLANT)
    fusion_delta = max(1, game_data.building_energy_delta(FUSION_PLANT, fusion_lvl + 1))
    fusion_cost_sp = game_data.spice_equivalent(game_data.building_cost(FUSION_PLANT, fusion_lvl + 1))
    fusion_cpe = fusion_cost_sp / fusion_delta

    thermo_unmet = game_data.missing_prerequisites(
        "buildings",
        THERMOSOLAR_PLANT,
        buildings=eff_buildings,
        researches=researches,
    )
    spice_lvl = _eff_level(planet, eff_buildings, SPICE_MINE)
    if not thermo_unmet and spice_lvl >= 6:
        thermo_lvl = _eff_level(planet, eff_buildings, THERMOSOLAR_PLANT)
        thermo_row = game_data.building_level_stats(THERMOSOLAR_PLANT, thermo_lvl + 1)
        thermo_delta = max(0, game_data.building_energy_delta(THERMOSOLAR_PLANT, thermo_lvl + 1))
        spice_burn = float(thermo_row.get("spice_used", 0))
        spice_prod = game_data.mine_production_per_hour(
            SPICE_MINE,
            spice_lvl,
            temperature=planet.temperature,
            mining_tech_level=int(researches.get("r1", 0)),
        )
        if thermo_delta > 0 and spice_burn <= spice_prod * 0.5:
            # Penalize build cost with ~24h fuel burn so cold high-Spice planets only adopt when truly cheaper
            thermo_cost_sp = game_data.spice_equivalent(
                game_data.building_cost(THERMOSOLAR_PLANT, thermo_lvl + 1)
            ) + spice_burn * 12.0
            thermo_cpe = thermo_cost_sp / thermo_delta
            if thermo_cpe < fusion_cpe * 0.70:
                return (
                    THERMOSOLAR_PLANT,
                    "Thermosolar plant",
                    f"Cheaper mid-game power than Fusion ({thermo_cpe:.1f} vs {fusion_cpe:.1f} Spice/energy)",
                )

    return FUSION_PLANT, "Fusion plant", "No-fuel planetary energy generation"


def _make_research_action(
    priority: str,
    planet: PlanetState,
    state: GameState,
    res_key: str,
    reason: str,
    research_offers: dict[str, ResearchOffer],
    research_queue_busy: bool,
    *,
    allow_live: bool,
) -> PlannedAction:
    offer = research_offers.get(res_key)
    info = game_data.RESEARCH_BY_ID.get(res_key)
    cur_lvl = int(state.researches.get(res_key, 0))
    nxt_lvl = cur_lvl + 1
    name = (offer.name if offer else None) or (str(info["name"]) if info else state.research_names.get(res_key, res_key))
    rid_num = int(res_key[1:]) if res_key.startswith("r") and res_key[1:].isdigit() else None

    if offer is not None:
        can_exec = allow_live and offer.can_research and not research_queue_busy and planet.resources.can_afford(offer.cost)
        note = " (research slot busy)" if research_queue_busy else ""
        return PlannedAction(
            priority=priority,
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.research_id,
            target_name=offer.name,
            target_level=offer.target_level,
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason=f"{reason}{note}",
            executable_now=can_exec,
            action_href=offer.research_href if can_exec else None,
        )

    wiki_cost = _resources_from_dict((info or {}).get("levels", {}).get(nxt_lvl, {}).get("cost", {}))
    return PlannedAction(
        priority=priority,
        kind="RESEARCH",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=rid_num,
        target_name=name,
        target_level=nxt_lvl,
        cost=wiki_cost,
        missing=planet.resources.missing(wiki_cost),
        reason=reason,
        executable_now=False,
    )


def _plan_tutorial_actions(
    state: GameState,
    planet: PlanetState,
    offers: dict[int, BuildingOffer],
    research_offers: dict[str, ResearchOffer],
    queued_counts: dict[int, int],
    eff_buildings: dict[str, int],
    projected_net: int,
    queue_full: bool,
    research_queue_busy: bool,
    config: StrategyConfig,
) -> list[PlannedAction]:
    """Order incomplete tutorial building & research tasks with proactive power lookahead."""
    if not state.tutorial:
        return []

    building_tasks = []
    research_tasks = []
    for t in state.tutorial.tasks:
        if t.completed:
            continue
        if t.category == "research" and t.research_id:
            if t.current < t.target:
                research_tasks.append(t)
        elif t.category == "building" and t.building_id:
            eff_progress = t.current + queued_counts.get(t.building_id, 0)
            if eff_progress < t.target:
                building_tasks.append((t, eff_progress))

    if not building_tasks and not research_tasks:
        return []

    starving_task_note = ""
    has_fusion_task = any(t.building_id == FUSION_PLANT for t, _ in building_tasks)
    for t, _eff in building_tasks:
        bid = t.building_id or 0
        if bid in (FUSION_PLANT, THERMOSOLAR_PLANT):
            continue
        nxt_lvl = _eff_level(planet, eff_buildings, bid) + 1
        starve_delta = _energy_starvation_delta(bid, nxt_lvl, projected_net)
        if starve_delta < 0:
            starving_task_note = (
                f" (proactive power: {t.building_name or f'Building {bid}'} L{nxt_lvl} "
                f"needs {-starve_delta} energy, projected net {projected_net:+d})"
            )
            break

    need_power_first = projected_net <= 0 or bool(starving_task_note)

    def _sort_key(item) -> tuple[int, int, int]:
        task, eff = item
        bid = task.building_id or 99
        if bid == FUSION_PLANT and need_power_first:
            bucket = 0
        elif bid == DEVELOPMENT_CENTER:
            bucket = 1
        elif bid == FUSION_PLANT:
            # Defer non-urgent Fusion upgrades behind mines when ample spare energy exists
            bucket = 4
        else:
            bucket = 3
        return (bucket, eff, bid)

    ordered = sorted(building_tasks, key=_sort_key)
    actions: list[PlannedAction] = []

    if need_power_first and not has_fusion_task and building_tasks and planet.energy.net >= 0:
        fusion_act = _make_build_action(
            "P2",
            planet,
            FUSION_PLANT,
            "Fusion plant",
            f"Proactive power before tutorial mines{starving_task_note}",
            offers,
            effective_level=_eff_level(planet, eff_buildings, FUSION_PLANT),
            researches=state.researches,
        )
        actions.append(_apply_queue_cap_if_full(fusion_act, queue_full))

    for task, eff in ordered:
        bid = task.building_id
        assert bid is not None
        reason = (
            f"Tutorial {state.tutorial.step}/{state.tutorial.total_steps}: "
            f"{task.description} (progress {eff}/{task.target})"
        )
        if bid == FUSION_PLANT and need_power_first:
            reason += starving_task_note or " (energy first for 100% mine output)"
        act = _make_build_action(
            "P2",
            planet,
            bid,
            task.building_name or f"Building {bid}",
            reason,
            offers,
            effective_level=_eff_level(planet, eff_buildings, bid),
            researches=state.researches,
        )
        actions.append(_apply_queue_cap_if_full(act, queue_full))

    for rtask in research_tasks:
        rid = rtask.research_id
        assert rid is not None
        reason = (
            f"Tutorial {state.tutorial.step}/{state.tutorial.total_steps}: "
            f"{rtask.description} (progress {rtask.current}/{rtask.target})"
        )
        actions.append(
            _make_research_action(
                "P2",
                planet,
                state,
                rid,
                reason,
                research_offers,
                research_queue_busy,
                allow_live=config.allow_research,
            )
        )

    return actions


def _tutorial_fight_task(state: GameState) -> Optional[object]:
    """Return active incomplete tutorial combat milestone task, if any."""
    if state.tutorial is None or state.tutorial.can_complete:
        return None
    for task in state.tutorial.tasks:
        if task.completed or task.category != "milestone":
            continue
        desc = task.description.lower()
        if any(word in desc for word in ("fight", "attack", "combat", "battle")):
            return task
    return None


def _plan_tutorial_fleet_milestones(
    state: GameState,
    active: PlanetState,
    config: StrategyConfig,
) -> list[PlannedAction]:
    """Emit ship build and non-combat milestone actions for later tutorial steps (e.g. Tutorial 6/11)."""
    if state.tutorial is None:
        return []
    tut = state.tutorial
    out: list[PlannedAction] = []
    ship_offers = _ship_offer_map(active)
    for task in tut.tasks:
        if task.completed:
            continue
        if task.category == "ship" and task.building_id is not None:
            sid = int(task.building_id)
            offer = ship_offers.get(sid)
            if offer is not None:
                ship_name = offer.name
                cost = offer.cost
                can_exec = (
                    config.allow_shipyard
                    and offer.can_build
                    and active.resources.can_afford(cost)
                )
            else:
                slug = SHIP_SLUG_BY_KEY.get(f"s{sid}")
                info = game_data.SHIPS.get(slug or "") if slug else None
                ship_name = task.building_name or str((info or {}).get("name") or f"Ship s{sid}")
                cost = _resources_from_dict((info or {}).get("base_cost", {}))
                can_exec = False
            out.append(
                PlannedAction(
                    priority="P2",
                    kind="BUILD_SHIP",
                    planet_id=active.planet_id,
                    planet_coords=active.coords,
                    target_id=sid,
                    target_name=ship_name,
                    target_level=max(1, task.target - task.current) if offer is None else 1,
                    cost=cost,
                    missing=active.resources.missing(cost),
                    reason=f"Tutorial {tut.step}/{tut.total_steps}: {task.description}",
                    executable_now=can_exec,
                )
            )
        elif task.category == "milestone":
            desc_l = task.description.lower()
            if any(word in desc_l for word in ("fight", "attack", "combat", "battle")):
                # Handled at P2 as SCAN_GALAXY / ATTACK by `_plan_combat_and_raid_actions`
                continue
            out.append(
                PlannedAction(
                    priority="P3",
                    kind="TUTORIAL_TASK",
                    planet_id=active.planet_id,
                    planet_coords=active.coords,
                    target_name=task.description,
                    reason=f"Tutorial {tut.step}/{tut.total_steps}: {task.description}",
                    executable_now=False,
                )
            )
    return out


_COMBAT_AND_RAID_SHIP_KEYS = ("s4", "s5", "s6", "s7", "s13", "s14", "s15", "s16", "s1", "s2", "s3")


def _parse_gal_sys_pos(coords: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in str(coords or "").split(":")]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return int(parts[0]), int(parts[1]), int(parts[2])
    return 1, 1, 1


def _plan_combat_and_raid_actions(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    *,
    scanned_systems: Optional[Mapping[str, float]] = None,
    now_ts: Optional[float] = None,
) -> list[PlannedAction]:
    """Plan Galaxy exploration sweep, Spy probe scouting, and scored Attack targets."""
    if state.tutorial is not None and state.tutorial.can_complete:
        return []

    ref_now = float(now_ts if now_ts is not None else time.time())
    tut_fight = _tutorial_fight_task(state)
    tutorial_fight_pending = tut_fight is not None

    def _planet_attack_ships(planet: PlanetState) -> dict[str, int]:
        return {
            k: int(planet.ships.get(k, 0) or 0)
            for k in _COMBAT_AND_RAID_SHIP_KEYS
            if int(planet.ships.get(k, 0) or 0) > 0
        }

    origin = active
    attack_ships = _planet_attack_ships(active)
    if not attack_ships:
        for candidate in state.planets:
            if candidate.planet_type == "raid":
                continue
            cand_ships = _planet_attack_ships(candidate)
            if cand_ships:
                origin = candidate
                attack_ships = cand_ships
                break

    has_attack_fleet = bool(attack_ships)
    probe_planet = (
        active
        if int(active.ships.get("s10", 0) or 0) > 0
        else next(
            (
                p
                for p in state.planets
                if p.planet_type != "raid" and int(p.ships.get("s10", 0) or 0) > 0
            ),
            None,
        )
    )
    has_probes = probe_planet is not None
    raid_targets = getattr(state, "raid_targets", ()) or ()

    if (
        not raid_targets
        and not tutorial_fight_pending
        and not cfg.allow_galaxy_scan
        and not cfg.allow_raids
        and not has_attack_fleet
        and not has_probes
    ):
        return []

    scanned_map: dict[str, float] = {}
    for raw_sys, raw_ts in (scanned_systems or {}).items():
        m_sys = re.search(r"(\d{1,2})\s*:\s*(\d{1,3})", str(raw_sys))
        if m_sys:
            skey = f"{int(m_sys.group(1)):02d}:{int(m_sys.group(2)):03d}"
            scanned_map[skey] = max(scanned_map.get(skey, 0.0), float(raw_ts or 0.0))

    own_coords = {p.coords for p in state.planets}
    scored_attacks: list[tuple[tuple[int, float, float], PlannedAction]] = []
    spy_actions: list[tuple[tuple[int, float], PlannedAction]] = []

    for raw in raid_targets:
        coords = str(raw.get("coords") or raw.get("target_coords") or "").strip()
        if not coords:
            continue
        t_gal, t_sys, pos = _parse_gal_sys_pos(coords)
        raw_scanned_ts = float(raw.get("last_scanned_ts") or 0.0)
        if raw_scanned_ts > 0.0:
            skey = f"{t_gal:02d}:{t_sys:03d}"
            scanned_map[skey] = max(scanned_map.get(skey, 0.0), raw_scanned_ts)
        if coords in own_coords:
            continue

        is_spacia = bool(raw.get("is_spacia", False))
        status_color = str(raw.get("status_color") or ("purple" if is_spacia else "yellow")).lower()
        if status_color in ("free", "blue", "green"):
            continue

        owner = str(raw.get("owner") or "")
        pname = str(raw.get("planet_name") or "")
        slot = GalaxySlot(
            coords=coords,
            position=pos,
            name=pname,
            owner=owner,
            status_color=status_color,
            is_spacia=is_spacia,
        )
        res_map = raw.get("resources") if isinstance(raw.get("resources"), dict) else raw
        res = Resources(
            pig_iron=int(res_map.get("pig_iron", 0) or 0),
            metal=int(res_map.get("metal", 0) or 0),
            kryptonite=int(res_map.get("kryptonite", 0) or 0),
            spice=int(res_map.get("spice", 0) or 0),
        )
        spied_ts = float(raw.get("last_spied_ts") or 0.0)
        has_intel = (
            bool(raw.get("has_spy_intel"))
            or spied_ts > 0.0
            or (not res.is_zero())
            or bool(raw.get("fleet"))
            or bool(raw.get("defense"))
        )
        stale_intel = spied_ts > 0.0 and (ref_now - spied_ts) > (4 * 3600.0)
        eligible_target = (
            is_spacia
            or status_color == "yellow"
            or (cfg.allow_pvp_in_az and status_color == "light_red")
        )
        tag = "Spacia" if is_spacia else ("Inactive" if status_color == "yellow" else status_color.title())
        target_label = f"{coords} ({owner or pname or tag})"

        if has_probes and not tutorial_fight_pending and eligible_target and (not has_intel or stale_intel):
            assert probe_planet is not None
            spy_actions.append(
                (
                    (0 if (status_color == "yellow" or is_spacia) else 1, -raw_scanned_ts),
                    PlannedAction(
                        priority="P2",
                        kind="SPY",
                        planet_id=probe_planet.planet_id,
                        planet_coords=probe_planet.coords,
                        target_id=10,
                        target_level=1,
                        target_name=target_label,
                        reason=(
                            f"Send Spy probe (s10) to scout {tag} {coords}"
                            + (" (refresh stale intel)" if stale_intel else " before raiding")
                        ),
                        executable_now=bool(cfg.allow_galaxy_scan),
                    ),
                )
            )
            if not has_intel:
                continue

        report = SpyReport(
            target_coords=coords,
            owner=owner,
            resources=res,
            fleet={str(k): int(v) for k, v in (raw.get("fleet") or {}).items()},
            defense={str(k): int(v) for k, v in (raw.get("defense") or {}).items()},
            is_spacia=is_spacia,
        )
        sim_fleet = attack_ships if has_attack_fleet else {"s1": 1}
        min_loot = 0.0 if (res.is_zero() or tutorial_fight_pending) else cfg.raid_min_loot_spice_eq
        rec = score_raid_target(
            origin.coords,
            slot,
            report,
            sim_fleet,
            attacker_researches=state.researches,
            safety_margin=cfg.combat_safety_margin,
            min_loot_spice_eq=min_loot,
            allow_pvp_in_az=cfg.allow_pvp_in_az,
        )
        if rec is None:
            continue

        can_exec = bool(cfg.allow_raids and has_attack_fleet and rec.combat.safe_to_attack)
        loot_note = (
            f"~{int(round(rec.loot_spice_equivalent)):,} SpEq plunder"
            if rec.loot_spice_equivalent > 0
            else "scouted inactive target"
        )
        gates: list[str] = []
        if not has_attack_fleet:
            gates.append("need stationed attack/transport ships")
        if not cfg.allow_raids:
            gates.append("allow_raids=false in Config")
        if not rec.combat.safe_to_attack:
            gates.append(rec.combat.reason)
        gate_suffix = f" [Hold: {'; '.join(gates)}]" if gates else " [Safe >=1.4x]"
        tut_prefix = "Tutorial fight: " if tutorial_fight_pending else ""

        scored_attacks.append(
            (
                (0 if can_exec else 1, -rec.loot_spice_equivalent, rec.distance_score),
                PlannedAction(
                    priority="P2",
                    kind="ATTACK",
                    planet_id=origin.planet_id,
                    planet_coords=origin.coords,
                    target_name=target_label,
                    reason=f"{tut_prefix}{tag} raid {loot_note} (ratio {rec.combat.power_ratio if rec.combat.power_ratio != float('inf') else 'INF'}x){gate_suffix}",
                    executable_now=can_exec,
                ),
            )
        )

    if scored_attacks:
        scored_attacks.sort(key=lambda item: item[0])
        out_actions = [act for _, act in scored_attacks[:3]]
        if spy_actions:
            spy_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in spy_actions[:2])
        return out_actions

    if spy_actions:
        spy_actions.sort(key=lambda item: item[0])
        return [act for _, act in spy_actions[:3]]

    # Outward spiral exploration sweep across neighbor solar systems with 30m cooldown
    scan_cooldown_s = 1800.0
    base_planets = [origin]
    if cfg.allow_multi_planet:
        for p in state.planets:
            if p.planet_id != origin.planet_id and p.planet_type != "raid":
                base_planets.append(p)
    colonies = [(p, *_parse_gal_sys_pos(p.coords)[:2]) for p in base_planets]
    offsets = [0] + [off for d in range(1, 16) for off in (d, -d)]

    chosen: Optional[tuple[PlanetState, int, int]] = None
    for off in offsets:
        for col_p, col_gal, col_sys in colonies:
            cand_sys = col_sys + off
            if not (1 <= cand_sys <= 400):
                continue
            skey = f"{col_gal:02d}:{cand_sys:03d}"
            prev_ts = scanned_map.get(skey)
            if prev_ts is not None and (ref_now - prev_ts) < scan_cooldown_s:
                continue
            chosen = (col_p, col_gal, cand_sys)
            break
        if chosen is not None:
            break

    if chosen is None:
        return []

    scan_planet, gal_num, sys_num = chosen
    tut_hint = f" for {tut_fight.description}" if tut_fight is not None else ""
    scan_note = "" if cfg.allow_galaxy_scan else " (click Scan Galaxy in Combat or enable allow_galaxy_scan in Config)"
    return [
        PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=scan_planet.planet_id,
            planet_coords=scan_planet.coords,
            target_name=f"Galaxy [{gal_num:02d}:{sys_num:03d}]",
            reason=f"Scan system {gal_num:02d}:{sys_num:03d} for yellow inactive / Spacia raid targets{tut_hint}{scan_note}",
            executable_now=bool(cfg.allow_galaxy_scan),
        )
    ]


def _mine_payback_hours(
    planet: PlanetState,
    eff_buildings: dict[str, int],
    building_id: int,
    mining_tech_level: int,
    offers: dict[int, BuildingOffer],
) -> tuple[float, float, int]:
    """Return `(payback_hours, delta_spice_per_hour, next_level)` at Merchant parity."""
    cur_lvl = _eff_level(planet, eff_buildings, building_id)
    nxt_lvl = cur_lvl + 1
    cur_prod = game_data.mine_production_per_hour(
        building_id,
        cur_lvl,
        temperature=planet.temperature,
        mining_tech_level=mining_tech_level,
    )
    nxt_prod = game_data.mine_production_per_hour(
        building_id,
        nxt_lvl,
        temperature=planet.temperature,
        mining_tech_level=mining_tech_level,
    )
    delta_prod = max(0.0, nxt_prod - cur_prod)
    res_key = _MINE_RES_KEY[building_id]
    delta_spice = delta_prod / game_data.MERCHANT_RATES_IN_SPICE[res_key]
    if delta_spice <= 0:
        return 999999.0, 0.0, nxt_lvl

    offer = offers.get(building_id)
    if offer is not None and offer.target_level == nxt_lvl and not offer.cost.is_zero():
        cost_spice = game_data.spice_equivalent(offer.cost)
    else:
        cost_spice = game_data.spice_equivalent(game_data.building_cost(building_id, nxt_lvl))

    payback_h = cost_spice / delta_spice if cost_spice > 0 else 0.0
    return payback_h, delta_spice, nxt_lvl


def _empire_raw_spice_eq_per_hour(state: GameState) -> float:
    """Sum hourly Pig iron + Kryptonite + Spice output across all planets in Spice equivalents."""
    mining_lvl = int(state.researches.get("r1", 0))
    total = 0.0
    for planet in state.planets:
        for bid in (PIG_IRON_MINE, KRYPTONITE_MINE, SPICE_MINE):
            lvl = planet.building_level(bid)
            if lvl <= 0:
                continue
            prod = game_data.mine_production_per_hour(
                bid,
                lvl,
                temperature=planet.temperature,
                mining_tech_level=mining_lvl,
            )
            total += prod / game_data.MERCHANT_RATES_IN_SPICE[_MINE_RES_KEY[bid]]
    return total


def _resolve_prereq_chain(
    category: str,
    slug: str,
    goal_label: str,
    eff_buildings: dict[str, int],
    researches: dict[str, int],
    visited: Optional[set[tuple[str, str]]] = None,
) -> list[dict]:
    """Walk `missing_prerequisites` DFS to surface immediately actionable unlock steps."""
    if visited is None:
        visited = set()
    if (category, slug) in visited:
        return []
    visited.add((category, slug))

    unmet = game_data.missing_prerequisites(
        category,
        slug,
        buildings=eff_buildings,
        researches=researches,
    )
    steps: list[dict] = []
    for req in unmet:
        sub = _resolve_prereq_chain(
            req["category"],
            req["slug"],
            goal_label,
            eff_buildings,
            researches,
            visited,
        )
        if sub:
            steps.extend(sub)
        else:
            steps.append({**req, "goal": goal_label})
    return steps


def _plan_unlock_and_advisory_actions(
    state: GameState,
    planet: PlanetState,
    eff_buildings: dict[str, int],
    offers: dict[int, BuildingOffer],
    research_offers: dict[str, ResearchOffer],
    ship_offers: dict[int, UnitOffer],
    queue_full: bool,
    research_queue_busy: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
) -> tuple[list[PlannedAction], list[PlannedAction]]:
    """Plan Research lab/Weapons factory unlocks, research ROI steps, and utility ship builds."""
    build_unlocks: list[PlannedAction] = []
    advisories: list[PlannedAction] = []

    pig = _eff_level(planet, eff_buildings, PIG_IRON_MINE)
    blast = _eff_level(planet, eff_buildings, BLAST_FURNACE)
    kryp = _eff_level(planet, eff_buildings, KRYPTONITE_MINE)
    dev = _eff_level(planet, eff_buildings, DEVELOPMENT_CENTER)
    lab = _eff_level(planet, eff_buildings, RESEARCH_LAB)
    wf = _eff_level(planet, eff_buildings, WEAPONS_FACTORY)

    base_ready = min(pig, blast, kryp) >= 3 or (config.merchant_mode and min(pig, kryp) >= 3)

    # Step toward Research lab (`b8`, requires Development center L2)
    if base_ready and lab == 0:
        unmet_lab = game_data.missing_prerequisites(
            "buildings",
            RESEARCH_LAB,
            buildings=eff_buildings,
            researches=state.researches,
        )
        if unmet_lab:
            for req in unmet_lab:
                if req["category"] == "buildings":
                    req_info = game_data.resolve_unit("buildings", req["slug"])
                    if req_info and req_info.get("id"):
                        bid = int(req_info["id"])
                        act = _make_build_action(
                            "P2",
                            planet,
                            bid,
                            str(req["name"]),
                            f"Prerequisite for Research lab ({req['current']}/{req['required']})",
                            offers,
                            effective_level=_eff_level(planet, eff_buildings, bid),
                            researches=state.researches,
                            allow_remote_colony_build=allow_remote_build,
                        )
                        build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))
        else:
            act = _make_build_action(
                "P2",
                planet,
                RESEARCH_LAB,
                "Research lab",
                "Unlock empire research and Mining/Energy/Engine technologies",
                offers,
                effective_level=lab,
                researches=state.researches,
                allow_remote_colony_build=allow_remote_build,
            )
            build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))

    # Step toward Weapons factory (`b9`, requires Development center L3 + Energy tech L1)
    if min(pig, kryp) >= 4 and lab >= 1 and wf == 0:
        unmet_wf = game_data.missing_prerequisites(
            "buildings",
            WEAPONS_FACTORY,
            buildings=eff_buildings,
            researches=state.researches,
        )
        if unmet_wf:
            for req in unmet_wf:
                if req["category"] == "buildings":
                    req_info = game_data.resolve_unit("buildings", req["slug"])
                    if req_info and req_info.get("id"):
                        bid = int(req_info["id"])
                        act = _make_build_action(
                            "P2",
                            planet,
                            bid,
                            str(req["name"]),
                            f"Prerequisite for Weapons factory ({req['current']}/{req['required']})",
                            offers,
                            effective_level=_eff_level(planet, eff_buildings, bid),
                            researches=state.researches,
                            allow_remote_colony_build=allow_remote_build,
                        )
                        build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))
                elif req["category"] == "research":
                    res_info = game_data.resolve_unit("research", req["slug"])
                    rkey = str((res_info or {}).get("id") or "r2")
                    advisories.append(
                        _make_research_action(
                            "P3",
                            planet,
                            state,
                            rkey,
                            f"Unlock prerequisite for Weapons factory ({req['current']}/{req['required']})",
                            research_offers,
                            research_queue_busy,
                            allow_live=config.allow_research,
                        )
                    )
        else:
            act = _make_build_action(
                "P2",
                planet,
                WEAPONS_FACTORY,
                "Weapons factory",
                "Unlock fleet & solar cell construction",
                offers,
                effective_level=wf,
                researches=state.researches,
                allow_remote_colony_build=allow_remote_build,
            )
            build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))

    if base_ready and lab >= 1 and dev < max(2, min(pig, kryp) - 2):
        act = _make_build_action(
            "P2",
            planet,
            DEVELOPMENT_CENTER,
            "Development center",
            "Cut local building times and scale infrastructure",
            offers,
            effective_level=dev,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))

    # Cross-planet Mining tech (`r1`) ROI + Energy/Ship unlock steps once Research lab L1 exists
    if lab >= 1:
        mining_lvl = int(state.researches.get("r1", 0))
        r1_info = game_data.RESEARCH_BY_ID.get("r1")
        if r1_info and not game_data.missing_prerequisites(
            "research", "r1", buildings=eff_buildings, researches=state.researches
        ):
            empire_raw = _empire_raw_spice_eq_per_hour(state)
            delta_r1 = 0.02 * empire_raw
            r1_cost_d = r1_info.get("levels", {}).get(mining_lvl + 1, {}).get("cost", {})
            r1_cost_sp = game_data.spice_equivalent(r1_cost_d)
            if delta_r1 > 0 and r1_cost_sp > 0:
                pb = r1_cost_sp / delta_r1
                reason = (
                    f"+2% Pig/Kryp/Spice across {len(state.planets)} planet(s): "
                    f"+{delta_r1:.1f} Spice-eq/h ({pb:.1f}h payback)"
                )
            else:
                reason = "+2% Pig iron, Kryptonite & Spice output across all planets"
            advisories.append(
                _make_research_action(
                    "P3",
                    planet,
                    state,
                    "r1",
                    reason,
                    research_offers,
                    research_queue_busy,
                    allow_live=config.allow_research,
                )
            )
        elif int(state.researches.get("r2", 0)) < 4 and 2 not in {
            a.target_id for a in advisories if a.kind == "RESEARCH"
        }:
            advisories.append(
                _make_research_action(
                    "P3",
                    planet,
                    state,
                    "r2",
                    f"Global +1% energy & prerequisite L4 for Mining tech ({state.researches.get('r2', 0)}/4)",
                    research_offers,
                    research_queue_busy,
                    allow_live=config.allow_research,
                )
            )

        goal_ships = (
            ("small-transport", "colony-ship", "phoenix", "transmitter", "battleship", "stealth-bomber")
            if config.playstyle == "fleeter"
            else ("small-transport", "colony-ship")
        )
        planned_ship_names = {
            a.target_name for a in advisories if a.kind in ("BUILD_SHIP", "SHIP")
        }
        if wf >= 1 and config.allow_shipyard:
            for _, t_cnt in config.shipyard_targets.items():
                if t_cnt > 0:
                    planned_ship_names.add("Small transport")
                    break
        for ship_slug in goal_ships:
            ship_info = game_data.SHIPS.get(ship_slug)
            if not ship_info or str(ship_info["name"]) in planned_ship_names:
                continue
            steps = _resolve_prereq_chain(
                "ships",
                ship_slug,
                str(ship_info["name"]),
                eff_buildings,
                state.researches,
            )
            if not steps:
                ship_cost = _resources_from_dict(ship_info.get("base_cost", {}))
                advisories.append(
                    PlannedAction(
                        priority="P3",
                        kind="SHIP",
                        planet_id=planet.planet_id,
                        planet_coords=planet.coords,
                        target_name=str(ship_info["name"]),
                        cost=ship_cost,
                        missing=planet.resources.missing(ship_cost),
                        reason=f"{ship_info['name']} unlocked (trade/expedition/colonization ready)",
                        executable_now=False,
                    )
                )
            else:
                first_res = next((s for s in steps if s["category"] == "research"), None)
                if first_res:
                    res_info = game_data.resolve_unit("research", first_res["slug"])
                    rkey = str((res_info or {}).get("id") or "")
                    if rkey:
                        advisories.append(
                            _make_research_action(
                                "P3",
                                planet,
                                state,
                                rkey,
                                f"Tech step ({first_res['current']}/{first_res['required']}) toward {first_res['goal']}",
                                research_offers,
                                research_queue_busy,
                                allow_live=config.allow_research,
                            )
                        )

    # Utility & Colony Shipyard targets whenever Weapons factory is built (`wf >= 1`)
    if wf >= 1 and config.allow_shipyard:
        slug_to_skey = {slug: skey for skey, slug in SHIP_SLUG_BY_KEY.items()}
        tutorial_ship_ids: set[int] = set()
        if state.tutorial and not state.tutorial.can_complete:
            for t in state.tutorial.tasks:
                if not t.completed and t.category == "ship" and t.building_id is not None:
                    tutorial_ship_ids.add(int(t.building_id))

        waiting_ships: list[tuple[float, PlannedAction]] = []
        for ship_slug, target_cnt in config.shipyard_targets.items():
            if target_cnt <= 0:
                continue
            skey = slug_to_skey.get(ship_slug)
            if not skey or not skey[1:].isdigit():
                continue
            sid_num = int(skey[1:])
            offer = ship_offers.get(sid_num)
            if offer is None:
                unmet_ship = game_data.missing_prerequisites(
                    "ships",
                    ship_slug,
                    buildings=planet.buildings,
                    researches=state.researches,
                )
                if unmet_ship:
                    continue

            owned = int(planet.ships.get(skey, offer.owned if offer is not None else 0))
            need = target_cnt - owned
            if need <= 0:
                continue

            if offer is not None:
                ship_name = offer.name
                cost = offer.cost
            else:
                info = game_data.resolve_unit("ships", ship_slug) or {}
                ship_name = str(info.get("name") or ship_slug)
                cost = _resources_from_dict(info.get("base_cost", {}))

            missing = planet.resources.missing(cost)
            can_exec = (
                offer is not None
                and offer.can_build
                and planet.resources.can_afford(cost)
            )
            ship_act = PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=sid_num,
                target_name=ship_name,
                target_level=1,
                cost=cost,
                missing=missing,
                reason=f"Fleet build target ({owned}/{target_cnt} {ship_name})",
                executable_now=can_exec,
            )
            if can_exec:
                advisories.append(ship_act)
            elif sid_num not in tutorial_ship_ids:
                waiting_ships.append((game_data.spice_equivalent(missing), ship_act))

        waiting_ships.sort(key=lambda pair: pair[0])
        for _, wait_act in waiting_ships[:3]:
            advisories.append(wait_act)

    return build_unlocks, advisories


def _plan_economy_for_planet(
    state: GameState,
    planet: PlanetState,
    offers: dict[int, BuildingOffer],
    research_offers: dict[str, ResearchOffer],
    ship_offers: dict[int, UnitOffer],
    eff_buildings: dict[str, int],
    projected_net: int,
    queue_full: bool,
    research_queue_busy: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
) -> tuple[list[tuple[float, PlannedAction]], list[PlannedAction], list[PlannedAction]]:
    """Return `(scored_mines, power_or_unlock_actions, advisories)` for one planet."""
    pig_lvl = _eff_level(planet, eff_buildings, PIG_IRON_MINE)
    blast_lvl = _eff_level(planet, eff_buildings, BLAST_FURNACE)
    mining_tech = int(state.researches.get("r1", 0))

    pig_prod = game_data.mine_production_per_hour(
        PIG_IRON_MINE,
        pig_lvl,
        temperature=planet.temperature,
        mining_tech_level=mining_tech,
    )
    blast_used_next = game_data.blast_furnace_pig_iron_used(blast_lvl + 1)
    blast_pig_capped = pig_prod <= 0 or (blast_used_next > pig_prod * config.blast_pig_iron_max_share)

    scored_mines: list[tuple[float, int, int, str]] = []
    deferred_blast: Optional[tuple[float, int, int, str]] = None

    for bid in _MINE_IDS:
        if bid == BLAST_FURNACE and config.merchant_mode:
            continue
        payback_h, delta_sp, nxt_lvl = _mine_payback_hours(
            planet,
            eff_buildings,
            bid,
            mining_tech,
            offers,
        )
        name = _MINE_LABELS[bid]
        reason = f"Best mine ROI ({payback_h:.1f}h Spice payback, +{delta_sp:.1f} Spice-eq/h @ {planet.temperature}C)"
        if bid == BLAST_FURNACE:
            if blast_pig_capped:
                deferred_blast = (
                    payback_h,
                    bid,
                    nxt_lvl,
                    f"Blast furnace L{nxt_lvl} needs {blast_used_next:.0f} Pig iron/h vs {pig_prod:.0f}/h produced; expand Pig iron first",
                )
                continue
            reason = (
                f"Blast furnace ROI ({payback_h:.1f}h payback; uses {blast_used_next:.0f}/{pig_prod:.0f} Pig iron/h)"
            )
        scored_mines.append((payback_h, bid, nxt_lvl, reason))

    scored_mines.sort(key=lambda row: (row[0], row[1]))

    priority_builds: list[PlannedAction] = []
    if scored_mines:
        _, top_bid, top_nxt, _ = scored_mines[0]
        starve_delta = _energy_starvation_delta(top_bid, top_nxt, projected_net)
        if starve_delta < 0 and planet.energy.net >= 0:
            power_bid, power_name, power_note = _pick_best_power_building(
                planet,
                eff_buildings,
                state.researches,
            )
            # Hot planets with tight building fields prefer 0-field orbital Solar cells (`s9`) when available
            solar_offer = ship_offers.get(SOLAR_CELLS_ID)
            if (
                planet.temperature >= config.hot_planet_solar_temp_threshold
                and 0 < planet.fields_remaining <= config.min_free_fields_warning
                and solar_offer is not None
                and solar_offer.can_build
                and config.allow_shipyard
            ):
                priority_builds.append(
                    PlannedAction(
                        priority="P1",
                        kind="BUILD_SHIP",
                        planet_id=planet.planet_id,
                        planet_coords=planet.coords,
                        target_id=SOLAR_CELLS_ID,
                        target_name="Solar cells",
                        target_level=1,
                        cost=solar_offer.cost,
                        missing=planet.resources.missing(solar_offer.cost),
                        reason=(
                            f"0-field orbital power on {planet.temperature}C planet "
                            f"({planet.fields_remaining} fields left)"
                        ),
                        executable_now=planet.resources.can_afford(solar_offer.cost),
                    )
                )
            else:
                power_act = _make_build_action(
                    "P1",
                    planet,
                    power_bid,
                    power_name,
                    f"Proactive power ({power_note}) for {_MINE_LABELS[top_bid]} L{top_nxt} (needs {-starve_delta} energy, net {projected_net:+d})",
                    offers,
                    effective_level=_eff_level(planet, eff_buildings, power_bid),
                    researches=state.researches,
                    allow_remote_colony_build=allow_remote_build,
                )
                priority_builds.append(_apply_queue_cap_if_full(power_act, queue_full))

    unlocks, advisories = _plan_unlock_and_advisory_actions(
        state,
        planet,
        eff_buildings,
        offers,
        research_offers,
        ship_offers,
        queue_full,
        research_queue_busy,
        allow_remote_build,
        config,
    )
    priority_builds.extend(unlocks)

    mine_actions: list[tuple[float, PlannedAction]] = []
    for payback_h, bid, nxt_lvl, reason in scored_mines:
        act = _make_build_action(
            "P2",
            planet,
            bid,
            _MINE_LABELS[bid],
            reason,
            offers,
            effective_level=nxt_lvl - 1,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        mine_actions.append((payback_h, _apply_queue_cap_if_full(act, queue_full)))

    if deferred_blast is not None:
        pb, bid, nxt_lvl, reason = deferred_blast
        act = _make_build_action(
            "P3",
            planet,
            bid,
            _MINE_LABELS[bid],
            reason,
            offers,
            effective_level=nxt_lvl - 1,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        advisories.append(_apply_queue_cap_if_full(act, queue_full))

    return mine_actions, priority_builds, advisories


def _plan_blast_furnace_throttle(
    state: GameState,
    planet: PlanetState,
    existing_plan: list[PlannedAction],
    cfg: StrategyConfig,
    *,
    last_throttle_ts: Optional[float] = None,
    now_ts: Optional[float] = None,
    storage_warn_ratio: Optional[float] = None,
    _check_bounce: bool = True,
) -> Optional[PlannedAction]:
    """Pause Blast furnace (`0%`) to stop 2:1 Pig iron drain when Iron-starved, or resume (`100%`)."""
    if planet.planet_type in ("asteroid", "raid"):
        return None
    b2_lvl = planet.building_level(BLAST_FURNACE)
    if b2_lvl <= 0:
        return None

    rates = getattr(planet, "economy_rates", None) or {}
    cur_b2_pct = int(rates.get("resource2", 100))
    cur_b1_pct = int(rates.get("resource1", 100))

    ref_now = float(now_ts if now_ts is not None else time.time())
    if last_throttle_ts is not None and (ref_now - float(last_throttle_ts)) < 300.0:
        if not (planet.energy.net < 0 and cur_b2_pct > 0):
            return None

    mining_tech = int((state.researches or {}).get("r1", 0) or 0)
    speedup = float(state.speedup_factor or 1.0)
    if speedup <= 0:
        speedup = 1.0
    prod_ratio = 1.0
    if planet.energy.used > 0 and planet.energy.produced < planet.energy.used:
        prod_ratio = max(0.0, planet.energy.produced / planet.energy.used)
    factor = speedup * prod_ratio

    p_pig_gross = (
        game_data.mine_production_per_hour(
            PIG_IRON_MINE,
            planet.building_level(PIG_IRON_MINE),
            temperature=planet.temperature,
            mining_tech_level=mining_tech,
        )
        * factor
        * (max(0, cur_b1_pct) / 100.0)
    )
    bf_pig_used_full = game_data.blast_furnace_pig_iron_used(b2_lvl) * factor
    net_pig_at_100 = p_pig_gross - bf_pig_used_full

    p_acts = [
        a
        for a in existing_plan
        if a.planet_id == planet.planet_id
        and a.kind in ("WAIT_RESOURCES", "BUILD", "RESEARCH", "BUILD_SHIP")
    ]
    prio_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    p_acts.sort(key=lambda a: prio_order.get(a.priority, 9))

    top_blocker = next((a for a in p_acts if a.kind == "WAIT_RESOURCES"), None)
    top_needs_pig_only = (
        top_blocker is not None
        and top_blocker.missing.pig_iron > 0
        and top_blocker.missing.metal == 0
    )
    top_needs_metal = (
        top_blocker is not None
        and top_blocker.missing.metal > 0
        and (top_blocker.missing.pig_iron == 0 or planet.resources.pig_iron >= 2000)
    )
    # When Blast furnace is active (>0%), scan full planned queue so P1 power items
    # or 3 affordable builds never mask Metal demand; preserve p_acts[:3] when paused (0%)
    # so the Pig-iron resume deadband remains unchanged.
    metal_scope = p_acts if cur_b2_pct > 0 else p_acts[:3]
    any_needs_metal = top_needs_metal or any(a.missing.metal > 0 for a in metal_scope)
    warn_ratio = cfg.storage_warn_ratio if storage_warn_ratio is None else storage_warn_ratio
    metal_cap_pct = (planet.resources.metal / planet.storage.metal) if planet.storage.metal > 0 else 0.0

    b2_full_energy = abs(int(game_data.building_level_stats(BLAST_FURNACE, b2_lvl).get("energy", 0) or 0))
    candidate: Optional[PlannedAction] = None

    if cur_b2_pct > 0:
        if top_needs_pig_only or (
            not any_needs_metal
            and net_pig_at_100 < 0
            and planet.resources.pig_iron < max(500.0, bf_pig_used_full * 0.5)
        ):
            candidate = PlannedAction(
                priority="P1",
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=BLAST_FURNACE,
                target_name="Blast furnace 0%",
                target_level=0,
                reason=(
                    f"Pause Blast furnace (currently {cur_b2_pct}%) to save "
                    f"+{bf_pig_used_full:.0f} Pig iron/h while Pig iron is the bottleneck"
                ),
                executable_now=True,
            )
        elif not any_needs_metal and (
            metal_cap_pct >= warn_ratio
            or (net_pig_at_100 < 0 and planet.resources.pig_iron < bf_pig_used_full)
        ):
            candidate = PlannedAction(
                priority="P2",
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=BLAST_FURNACE,
                target_name="Blast furnace 0%",
                target_level=0,
                reason=(
                    f"Blast furnace drains {bf_pig_used_full:.0f} Pig iron/h; "
                    "pause to 0% to prioritize Pig iron stockpiling"
                ),
                executable_now=True,
            )
        else:
            return None
    else:
        # Currently paused (0%): apply energy-coupling lookahead and symmetric deadband before resuming 100%
        if metal_cap_pct >= warn_ratio * 0.85:
            return None
        if any(a.priority == "P1" and a.missing.pig_iron > 0 for a in p_acts):
            return None

        resume_energy_cost = int(round(b2_full_energy * max(0, 100 - cur_b2_pct) / 100.0))
        net_if_resumed = planet.energy.net - resume_energy_cost
        if net_if_resumed < 0:
            return None

        active = state.active_planet()
        is_active = active is not None and planet.planet_id == active.planet_id
        eff_buildings, projected_net = _effective_building_levels_and_energy(
            state,
            planet,
            is_active_planet=is_active,
        )
        projected_net_if_resumed = projected_net - resume_energy_cost
        queued_counts = _queued_building_counts(state) if is_active else {}
        queue_full = (
            sum(queued_counts.values()) >= (4 if planet.planet_type == "asteroid" else 5)
            if is_active
            else False
        )
        allow_remote = cfg.allow_multi_planet and not is_active

        has_p1_power = any(
            a.priority == "P1" and a.target_id in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID)
            for a in p_acts
        )
        would_trigger_p1_power = has_p1_power
        if not would_trigger_p1_power:
            for a in p_acts:
                if a.target_id is None or a.target_id in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID):
                    continue
                if a.kind not in ("BUILD", "WAIT_RESOURCES"):
                    continue
                nxt_l = int(a.target_level or (_eff_level(planet, eff_buildings, int(a.target_id)) + 1))
                if _energy_starvation_delta(int(a.target_id), nxt_l, projected_net_if_resumed) < 0:
                    would_trigger_p1_power = True
                    break
        if would_trigger_p1_power:
            solar_offer = _ship_offer_map(planet).get(SOLAR_CELLS_ID)
            use_solar_cells = (
                planet.temperature >= cfg.hot_planet_solar_temp_threshold
                and 0 < planet.fields_remaining <= cfg.min_free_fields_warning
                and solar_offer is not None
                and solar_offer.can_build
                and cfg.allow_shipyard
            )
            if use_solar_cells:
                assert solar_offer is not None
                miss_pw = planet.resources.missing(solar_offer.cost)
                can_exec_power = planet.resources.can_afford(solar_offer.cost)
            else:
                power_bid, _, _ = _pick_best_power_building(planet, eff_buildings, state.researches)
                power_lvl = _eff_level(planet, eff_buildings, power_bid) + 1
                power_cost = _resources_from_dict(game_data.building_cost(power_bid, power_lvl))
                power_offer = _offer_map(planet).get(power_bid)
                if power_offer is not None:
                    miss_pw = planet.resources.missing(power_offer.cost)
                    can_exec_power = (
                        (not queue_full)
                        and power_offer.can_build
                        and planet.resources.can_afford(power_offer.cost)
                    )
                else:
                    miss_pw = planet.resources.missing(power_cost)
                    prereq_ok = not game_data.missing_prerequisites(
                        "buildings",
                        power_bid,
                        buildings=planet.buildings,
                        researches=state.researches,
                    )
                    can_exec_power = (
                        (not queue_full)
                        and allow_remote
                        and (not power_cost.is_zero())
                        and planet.resources.can_afford(power_cost)
                        and prereq_ok
                    )
            if (
                has_p1_power
                or miss_pw.pig_iron > 0
                or (not miss_pw.is_zero() and miss_pw.metal == 0)
                or (not can_exec_power and miss_pw.metal == 0)
            ):
                return None

        if top_needs_metal or (not top_needs_pig_only and any_needs_metal and net_pig_at_100 >= 0):
            resume_pig_min_for_metal = max(2000.0, bf_pig_used_full * 0.6)
            if net_pig_at_100 >= 0 or planet.resources.pig_iron >= resume_pig_min_for_metal:
                candidate = PlannedAction(
                    priority="P1",
                    kind="THROTTLE_BUILDING",
                    planet_id=planet.planet_id,
                    planet_coords=planet.coords,
                    target_id=BLAST_FURNACE,
                    target_name="Blast furnace 100%",
                    target_level=100,
                    reason="Metal needed for next priority build/research: resume Blast furnace to 100%",
                    executable_now=True,
                )

        if candidate is None:
            resume_pig_threshold = max(5000.0, planet.resources.metal * 2.0, bf_pig_used_full * 1.5)
            if (
                not top_needs_pig_only
                and planet.resources.pig_iron >= resume_pig_threshold
                and net_pig_at_100 >= 0
            ):
                candidate = PlannedAction(
                    priority="P2",
                    kind="THROTTLE_BUILDING",
                    planet_id=planet.planet_id,
                    planet_coords=planet.coords,
                    target_id=BLAST_FURNACE,
                    target_name="Blast furnace 100%",
                    target_level=100,
                    reason="Pig iron reserves healthy: resume Blast furnace smelting to 100%",
                    executable_now=True,
                )

    if candidate is not None and _check_bounce:
        new_pct = int(candidate.target_level or 0)
        delta_e = int(round(b2_full_energy * (new_pct - cur_b2_pct) / 100.0))
        new_rates = dict(rates)
        new_rates["resource2"] = new_pct
        post_planet = replace(
            planet,
            economy_rates=new_rates,
            energy=EnergyBalance(planet.energy.produced, max(0, planet.energy.used + delta_e)),
        )
        post_state = replace(
            state,
            planets=tuple(
                post_planet if p.planet_id == planet.planet_id else p for p in state.planets
            ),
        )
        post_plan, _ = _build_pre_throttle_plan(
            post_state,
            warn_ratio=warn_ratio,
            cfg=cfg,
            include_combat=False,
        )
        bounce = _plan_blast_furnace_throttle(
            post_state,
            post_planet,
            post_plan,
            cfg,
            last_throttle_ts=None,
            now_ts=now_ts,
            storage_warn_ratio=warn_ratio,
            _check_bounce=False,
        )
        if bounce is not None:
            return None

    return candidate


def _build_pre_throttle_plan(
    state: GameState,
    *,
    warn_ratio: float,
    cfg: StrategyConfig,
    scanned_systems: Optional[Mapping[str, float]] = None,
    now_ts: Optional[float] = None,
    include_combat: bool = True,
) -> tuple[list[PlannedAction], list[PlanetState]]:
    plan: list[PlannedAction] = []
    active = state.active_planet()
    if active is None:
        return plan, []

    # P0: Survival / incoming hostile attacks + optimal Escape Flight packing
    for alert in state.fleet_alerts:
        if alert.hostile:
            plan.append(
                PlannedAction(
                    priority="P0",
                    kind="ALERT_HOSTILE",
                    planet_id=active.planet_id,
                    planet_coords=active.coords,
                    target_name="Escape flight / Fleet Save",
                    reason=f"Hostile fleet detected: {alert.summary}",
                    executable_now=False,
                )
            )
            other_coords = next(
                (p.coords for p in state.planets if p.planet_id != active.planet_id),
                active.coords,
            )
            escape = plan_escape_flight(
                active,
                target_coords=other_coords,
                fuel_reserve_spice=cfg.fleet_save_fuel_reserve_spice,
            )
            if escape.feasible:
                plan.append(
                    PlannedAction(
                        priority="P0",
                        kind="ESCAPE_FLIGHT",
                        planet_id=active.planet_id,
                        planet_coords=active.coords,
                        target_name=f"Save -> {escape.target_coords}",
                        cost=escape.cargo,
                        reason=escape.reason,
                        executable_now=cfg.allow_fleetsave,
                    )
                )

    queued_counts = _queued_building_counts(state)
    total_b_queued = sum(queued_counts.values())
    active_queue_full = total_b_queued >= (4 if active.planet_type == "asteroid" else 5)
    research_offers = _research_offer_map(state)
    research_queue_busy = (
        any(q.category == "research" for q in state.active_queues)
        or any(o.active for o in state.research_offers)
    )

    planets_to_check = list(state.planets) if cfg.allow_multi_planet else [active]
    # Keep active planet first for tie-breaks
    planets_to_check.sort(key=lambda p: 0 if p.planet_id == active.planet_id else 1)

    for planet in planets_to_check:
        is_active = planet.planet_id == active.planet_id
        offers = _offer_map(planet)
        eff_buildings, projected_net = _effective_building_levels_and_energy(
            state,
            planet,
            is_active_planet=is_active,
        )
        queue_full = active_queue_full if is_active else False
        allow_remote = cfg.allow_multi_planet and not is_active

        # P1: Negative energy guard per planet
        if planet.energy.net < 0:
            power_bid, power_name, _ = _pick_best_power_building(
                planet,
                eff_buildings,
                state.researches,
            )
            plan.append(
                _apply_queue_cap_if_full(
                    _make_build_action(
                        "P1",
                        planet,
                        power_bid,
                        power_name,
                        f"Negative energy ({planet.energy.produced}/{planet.energy.used}); mines throttled",
                        offers,
                        effective_level=_eff_level(planet, eff_buildings, power_bid),
                        researches=state.researches,
                        allow_remote_colony_build=allow_remote,
                    ),
                    queue_full,
                )
            )

        # P1: Warehouse overflow guard per planet
        for res_key, ratio in planet.storage_fill_ratios().items():
            if ratio >= warn_ratio:
                wh_id, wh_name = _WAREHOUSE_BY_RESOURCE[res_key]
                plan.append(
                    _apply_queue_cap_if_full(
                        _make_build_action(
                            "P1",
                            planet,
                            wh_id,
                            wh_name,
                            f"{res_key.replace('_', ' ').title()} storage at {ratio * 100:.0f}% (5% cap risk)",
                            offers,
                            effective_level=_eff_level(planet, eff_buildings, wh_id),
                            researches=state.researches,
                            allow_remote_colony_build=allow_remote,
                        ),
                        queue_full,
                    )
                )

    # P2: Active planet Tutorial Claim + Tutorial Tasks
    active_offers = _offer_map(active)
    active_eff, active_proj_net = _effective_building_levels_and_energy(
        state,
        active,
        is_active_planet=True,
    )
    if state.tutorial and state.tutorial.can_complete:
        plan.append(
            PlannedAction(
                priority="P2",
                kind="CLAIM_TUTORIAL",
                planet_id=active.planet_id,
                planet_coords=active.coords,
                target_name=f"Tutorial {state.tutorial.step}/{state.tutorial.total_steps} Reward",
                reason=f"All tasks complete; claim {state.tutorial.reward_text or 'tutorial reward'}",
                executable_now=True,
            )
        )

    tutorial_actions = _plan_tutorial_actions(
        state,
        active,
        active_offers,
        research_offers,
        queued_counts,
        active_eff,
        active_proj_net,
        active_queue_full,
        research_queue_busy,
        cfg,
    )
    if tutorial_actions:
        plan.extend(tutorial_actions)
    elif not (state.tutorial and state.tutorial.can_complete):
        all_mines: list[tuple[float, PlannedAction]] = []
        all_advisories: list[PlannedAction] = []
        for planet in planets_to_check:
            if planet.planet_type == "raid":
                continue
            is_active = planet.planet_id == active.planet_id
            eff_b, proj_net = _effective_building_levels_and_energy(
                state,
                planet,
                is_active_planet=is_active,
            )
            mines, extra_builds, adv = _plan_economy_for_planet(
                state,
                planet,
                _offer_map(planet),
                research_offers if is_active else {},
                _ship_offer_map(planet),
                eff_b,
                proj_net,
                active_queue_full if is_active else False,
                research_queue_busy,
                cfg.allow_multi_planet and not is_active,
                cfg,
            )
            all_mines.extend(mines)
            plan.extend(extra_builds)
            all_advisories.extend(adv)

        # Sort cross-planet mines by global best payback hours!
        all_mines.sort(key=lambda pair: pair[0])
        plan.extend(act for _, act in all_mines)
        plan.extend(all_advisories)

        if state.tutorial is not None and not state.tutorial.can_complete:
            plan.extend(_plan_tutorial_fleet_milestones(state, active, cfg))

        if include_combat:
            plan.extend(
                _plan_combat_and_raid_actions(
                    state,
                    active,
                    cfg,
                    scanned_systems=scanned_systems,
                    now_ts=now_ts,
                )
            )

    return plan, planets_to_check


def plan_actions(
    state: GameState,
    *,
    storage_warn_ratio: Optional[float] = None,
    config: Optional[StrategyConfig] = None,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[int, float]] = None,
    now_ts: Optional[float] = None,
) -> list[PlannedAction]:
    """Generate a priority-sorted action plan across the empire."""
    cfg = config or StrategyConfig()
    warn_ratio = cfg.storage_warn_ratio if storage_warn_ratio is None else storage_warn_ratio

    plan, planets_to_check = _build_pre_throttle_plan(
        state,
        warn_ratio=warn_ratio,
        cfg=cfg,
        scanned_systems=scanned_systems,
        now_ts=now_ts,
        include_combat=True,
    )
    if not planets_to_check:
        return plan

    # Bucket: Blast furnace (b2) 0% / 100% auto-throttling to protect Pig iron flow
    if cfg.auto_throttle_blast_furnace and not cfg.merchant_mode:
        cooldown_map = throttle_cooldowns or {}
        for planet in planets_to_check:
            throttle_act = _plan_blast_furnace_throttle(
                state,
                planet,
                plan,
                cfg,
                last_throttle_ts=cooldown_map.get(planet.planet_id),
                now_ts=now_ts,
                storage_warn_ratio=warn_ratio,
            )
            if throttle_act is not None:
                plan.append(throttle_act)

    # Sort strictly by priority band, executable actions first within each band
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    plan.sort(
        key=lambda a: (
            priority_rank.get(a.priority, 9),
            0 if a.executable_now else 1,
        )
    )
    deduped: list[PlannedAction] = []
    seen_keys: set[tuple[str, int, Optional[int], str]] = set()
    for act in plan:
        key = (act.kind, act.planet_id, act.target_id, act.target_name)
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(act)
    return deduped
