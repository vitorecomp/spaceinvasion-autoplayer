"""Synthetic game states, strategy configs and AI-Planner targets for the golden suite.

Everything here is invented: no name, coordinate or id comes from a live account.
The builders are self-contained on purpose (they do not reuse `tests/e2e/fixtures.py`)
so that reworking the e2e helpers cannot silently change a golden input.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.core import catalog as game_data
from backend.core.catalog import SHIP_SLUG_BY_KEY
from backend.core.config import StrategyConfig
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

GOLDEN_EPOCH = 1_700_000_000.0
GOLDEN_SEED = 20261120
UNIVERSE_ID = 31
USER_ID = 900001
NICKNAME = "GoldenPilot"
SID = "goldensid0001"

_RES_KEYS = ("pig_iron", "metal", "kryptonite", "spice")


def _res(raw: Mapping[str, Any]) -> Resources:
    return Resources(*(int(raw.get(k, 0) or 0) for k in _RES_KEYS))


def _research_cost(rkey: str, level: int) -> Resources:
    levels = (game_data.RESEARCH_BY_ID.get(rkey) or {}).get("levels") or {}
    return _res((levels.get(level) or {}).get("cost") or {})


def _building_offers(
    buildings: Mapping[str, int],
    researches: Mapping[str, int],
    resources: Resources,
    *,
    planet_type: str,
    fields_free: int,
) -> tuple[BuildingOffer, ...]:
    offers: list[BuildingOffer] = []
    for bid in sorted(game_data.BUILDING_BY_ID):
        info = game_data.BUILDING_BY_ID[bid]
        if planet_type == "asteroid" and not info.get("asteroid_ok", False):
            continue
        cur = int(buildings.get(f"b{bid}", 0))
        cost = _res(game_data.building_cost(bid, cur + 1))
        if cost.is_zero():
            continue
        unmet = game_data.missing_prerequisites(
            "buildings", bid, buildings=dict(buildings), researches=dict(researches)
        )
        can_build = (not unmet) and resources.can_afford(cost) and fields_free > 0
        offers.append(
            BuildingOffer(
                building_id=bid,
                name=str(info.get("name") or f"Building {bid}"),
                current_level=cur,
                target_level=cur + 1,
                cost=cost,
                can_build=can_build,
                status_text=f"Build level {cur + 1}" if can_build else "Not enough resources",
                build_href=(
                    f"indexInternal.es?action=internalBuildItem&buildingID={bid}&sid={SID}#{bid}" if can_build else None
                ),
            )
        )
    return tuple(offers)


def _ship_offers(ships: Mapping[str, int], resources: Resources, ship_ids: tuple[int, ...]) -> tuple[UnitOffer, ...]:
    offers: list[UnitOffer] = []
    for sid in ship_ids:
        slug = SHIP_SLUG_BY_KEY.get(f"s{sid}") or ""
        info = game_data.SHIPS.get(slug) or {}
        cost = _res(info.get("base_cost") or {})
        can_build = resources.can_afford(cost)
        offers.append(
            UnitOffer(
                unit_type="ship",
                unit_id=sid,
                name=str(info.get("name") or f"Ship s{sid}"),
                owned=int(ships.get(f"s{sid}", 0)),
                cost=cost,
                can_build=can_build,
                input_name=f"ship[{sid}]" if can_build else None,
                status_text="Buildable" if can_build else "Not enough resources",
            )
        )
    return tuple(offers)


def _research_offers(
    researches: Mapping[str, int],
    resources: Resources,
    keys: tuple[str, ...],
    *,
    active_key: Optional[str] = None,
) -> tuple[ResearchOffer, ...]:
    offers: list[ResearchOffer] = []
    for rkey in keys:
        cur = int(researches.get(rkey, 0))
        cost = _research_cost(rkey, cur + 1)
        is_active = rkey == active_key
        can = (not is_active) and active_key is None and (not cost.is_zero()) and resources.can_afford(cost)
        rid = int(rkey[1:])
        offers.append(
            ResearchOffer(
                research_id=rid,
                research_key=rkey,
                name=str((game_data.RESEARCH_BY_ID.get(rkey) or {}).get("name") or rkey),
                current_level=cur,
                target_level=cur + 1,
                cost=cost,
                can_research=can,
                status_text="In progress" if is_active else (f"Research level {cur + 1}" if can else "Unavailable"),
                research_href=(
                    f"indexInternal.es?action=internalBuildItem&researchID={rid}&sid={SID}" if can else None
                ),
                active=is_active,
            )
        )
    return tuple(offers)


def _planet(
    planet_id: int,
    name: str,
    coords: str,
    *,
    buildings: Mapping[str, int],
    resources: Resources,
    researches: Mapping[str, int],
    planet_type: str = "normal",
    temperature: int = 20,
    fields_max: int = 250,
    fields_used: Optional[int] = None,
    storage: Resources = Resources(400_000, 400_000, 400_000, 400_000),
    ships: Optional[Mapping[str, int]] = None,
    defense: Optional[Mapping[str, int]] = None,
    economy_rates: Optional[Mapping[str, int]] = None,
    ship_offer_ids: tuple[int, ...] = (),
) -> PlanetState:
    b_map = {str(k): int(v) for k, v in buildings.items()}
    used = sum(b_map.values()) if fields_used is None else int(fields_used)
    produced = 0
    demand = 0
    for bkey, lvl in b_map.items():
        energy = int(game_data.building_level_stats(int(bkey[1:]), lvl).get("energy", 0) or 0)
        if energy >= 0:
            produced += energy
        else:
            demand -= energy
    ship_map = {str(k): int(v) for k, v in (ships or {}).items()}
    return PlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type=planet_type,
        temperature=temperature,
        fields_used=used,
        fields_max=fields_max,
        resources=resources,
        storage=storage,
        energy=EnergyBalance(produced=produced, used=demand),
        buildings=b_map,
        building_names={k: str((game_data.BUILDING_BY_ID.get(int(k[1:])) or {}).get("name") or k) for k in b_map},
        ships=ship_map,
        defense={str(k): int(v) for k, v in (defense or {}).items()},
        building_offers=_building_offers(
            b_map, researches, resources, planet_type=planet_type, fields_free=fields_max - used
        ),
        ship_offers=_ship_offers(ship_map, resources, ship_offer_ids),
        economy_rates={str(k): int(v) for k, v in (economy_rates or {}).items()},
    )


def _state(
    planets: tuple[PlanetState, ...],
    researches: Mapping[str, int],
    **kwargs: Any,
) -> GameState:
    r_map = {str(k): int(v) for k, v in researches.items()}
    return GameState(
        universe_id=UNIVERSE_ID,
        user_id=USER_ID,
        nickname=NICKNAME,
        timestamp=GOLDEN_EPOCH,
        planets=planets,
        active_planet_id=planets[0].planet_id,
        researches=r_map,
        research_names={k: str((game_data.RESEARCH_BY_ID.get(k) or {}).get("name") or k) for k in r_map},
        specs={"miner": 0, "fleeter": 0},
        unread_messages={"total": 0},
        **kwargs,
    )


def early_homeworld() -> GameState:
    """One fresh planet inside the tutorial, newbie protection on."""
    researches: dict[str, int] = {}
    resources = Resources(6_500, 2_400, 3_100, 400)
    home = _planet(
        20001,
        "Golden Prime",
        "03:120:07",
        buildings={"b1": 2, "b3": 1, "b5": 2},
        resources=resources,
        researches=researches,
        temperature=24,
        fields_max=228,
        storage=Resources(100_000, 100_000, 100_000, 100_000),
        economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
    )
    tutorial = TutorialStatus(
        step=2,
        total_steps=11,
        tasks=(
            TutorialTask("Increase your Fusion plant level 2 / 3", "Fusion plant", 5, current=2, target=3),
            TutorialTask("Increase your Pig iron mine level 2 / 3", "Pig iron mine", 1, current=2, target=3),
            TutorialTask("Increase your Kryptonite mine level 1 / 3", "Kryptonite mine", 3, current=1, target=3),
        ),
        reward_text="5232 Pig iron, 900 Metal, 2758 Kryptonite, 200 Spice",
        can_complete=False,
    )
    return _state(
        (home,),
        researches,
        tutorial=tutorial,
        speedup_factor=5.0,
        newbie_protection=True,
        account_points=42,
        account_rank=1830,
    )


_MIDGAME_RAID_TARGETS: tuple[dict[str, Any], ...] = (
    {
        "target_coords": "03:120:03",
        "owner": "DormantOne",
        "planet_name": "Quiet Rock",
        "status_color": "yellow",
        "pig_iron": 64_000,
        "metal": 31_000,
        "kryptonite": 22_000,
        "spice": 9_000,
        "fleet": {},
        "defense": {"d1": 3},
        "is_spacia": False,
        "last_scanned_ts": GOLDEN_EPOCH - 900.0,
    },
    {
        "target_coords": "03:122:09",
        "owner": "Spacia",
        "planet_name": "Spacia Planet",
        "status_color": "yellow",
        "pig_iron": 120_000,
        "metal": 80_000,
        "kryptonite": 40_000,
        "spice": 15_000,
        "fleet": {"s4": 2},
        "defense": {"d1": 25, "d2": 6},
        "is_spacia": True,
        "has_debris": True,
        "last_scanned_ts": GOLDEN_EPOCH - 1800.0,
    },
    {
        "target_coords": "03:119:14",
        "owner": "WatchfulRival",
        "planet_name": "Bastion",
        "status_color": "light_red",
        "pig_iron": 12_000,
        "metal": 6_000,
        "kryptonite": 3_000,
        "spice": 1_000,
        "fleet": {"s5": 40},
        "defense": {"d1": 80, "d3": 10},
        "is_spacia": False,
        "last_scanned_ts": GOLDEN_EPOCH - 300.0,
    },
)


_LATEGAME_RAID_TARGETS: tuple[dict[str, Any], ...] = (
    {
        "target_coords": "03:121:12",
        "owner": "DormantOne",
        "planet_name": "Quiet Rock",
        "status_color": "yellow",
        "pig_iron": 940_000,
        "metal": 410_000,
        "kryptonite": 260_000,
        "spice": 120_000,
        "fleet": {},
        "defense": {"d1": 30},
        "is_spacia": False,
        "last_scanned_ts": GOLDEN_EPOCH - 600.0,
    },
)


def midgame_three_colonies() -> GameState:
    """Homeworld plus two colonies: queues, research offers, raid targets and a hostile alert."""
    researches = {"r1": 4, "r2": 5, "r3": 3, "r4": 4, "r5": 2, "r6": 3, "r7": 2}
    home_res = Resources(182_000, 96_000, 74_000, 31_000)
    home = _planet(
        20001,
        "Golden Prime",
        "03:120:07",
        buildings={"b1": 14, "b2": 10, "b3": 12, "b4": 9, "b5": 15, "b7": 8, "b8": 7, "b9": 5, "b10": 4},
        resources=home_res,
        researches=researches,
        temperature=24,
        fields_max=262,
        ships={"s1": 12, "s2": 4, "s4": 30, "s5": 6, "s10": 8, "s11": 3},
        defense={"d1": 12, "d2": 4},
        economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
        ship_offer_ids=(1, 2, 4, 8, 10, 11),
    )
    forge = _planet(
        20002,
        "Golden Forge",
        "03:120:11",
        buildings={"b1": 10, "b2": 7, "b3": 8, "b4": 6, "b5": 11, "b7": 5, "b9": 2, "b10": 2},
        resources=Resources(54_000, 18_500, 22_000, 6_400),
        researches=researches,
        temperature=-18,
        fields_max=301,
        ships={"s1": 3, "s10": 2},
        economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100, "resource5": 100},
        ship_offer_ids=(1, 10),
    )
    outpost = _planet(
        20003,
        "Golden Outpost",
        "03:121:04",
        buildings={"b1": 5, "b3": 4, "b4": 2, "b5": 6, "b7": 2},
        resources=Resources(9_800, 1_200, 5_100, 900),
        researches=researches,
        temperature=61,
        fields_max=188,
        storage=Resources(150_000, 150_000, 150_000, 150_000),
    )
    return _state(
        (home, forge, outpost),
        researches,
        research_offers=_research_offers(researches, home_res, ("r1", "r2", "r3", "r4", "r6"), active_key="r6"),
        active_queues=(
            QueueJob(
                "building",
                "Fusion plant",
                building_id=5,
                target_level=16,
                remaining_text="00:42:10",
                remaining_seconds=2530.0,
                finish_ts=GOLDEN_EPOCH + 2530.0,
            ),
            QueueJob(
                "research",
                "Spy technology",
                target_level=4,
                remaining_text="01:10:00",
                remaining_seconds=4200.0,
                finish_ts=GOLDEN_EPOCH + 4200.0,
            ),
        ),
        fleet_alerts=(FleetAlert("ATTACK", True, "Incoming attack on [03:120:07] in 00:12:00"),),
        speedup_factor=5.0,
        newbie_protection=False,
        account_points=18_450,
        account_rank=412,
        raid_targets=_MIDGAME_RAID_TARGETS,
    )


def _lategame_inner_planets(researches: Mapping[str, int]) -> tuple[PlanetState, ...]:
    big_storage = Resources(6_400_000, 6_400_000, 6_400_000, 6_400_000)
    capital_res = Resources(2_850_000, 1_420_000, 1_180_000, 640_000)
    capital = _planet(
        20001,
        "Golden Prime",
        "03:120:07",
        buildings={
            "b1": 26,
            "b2": 21,
            "b3": 23,
            "b4": 20,
            "b5": 27,
            "b7": 16,
            "b8": 14,
            "b9": 12,
            "b10": 9,
            "b11": 8,
            "b12": 8,
            "b13": 8,
            "b14": 8,
            "b17": 3,
        },
        resources=capital_res,
        researches=researches,
        temperature=24,
        fields_max=262,
        storage=big_storage,
        ships={"s1": 60, "s2": 45, "s4": 220, "s5": 90, "s6": 40, "s7": 18, "s10": 30, "s11": 24, "s8": 1},
        defense={"d1": 120, "d2": 60, "d3": 20},
        economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
        ship_offer_ids=(1, 2, 4, 5, 6, 7, 8, 10, 11),
    )
    forge = _planet(
        20002,
        "Golden Forge",
        "03:120:11",
        buildings={
            "b1": 24,
            "b2": 22,
            "b3": 20,
            "b4": 17,
            "b5": 25,
            "b7": 14,
            "b9": 8,
            "b10": 6,
            "b11": 7,
            "b12": 7,
            "b13": 7,
            "b14": 6,
        },
        resources=Resources(1_240_000, 310_000, 520_000, 190_000),
        researches=researches,
        temperature=-18,
        fields_max=301,
        storage=big_storage,
        ships={"s1": 20, "s2": 12, "s10": 6, "s11": 10},
        economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
        ship_offer_ids=(1, 2, 10, 11),
    )
    outpost = _planet(
        20003,
        "Golden Outpost",
        "03:121:04",
        buildings={
            "b1": 22,
            "b2": 16,
            "b3": 19,
            "b4": 21,
            "b5": 24,
            "b7": 12,
            "b9": 6,
            "b10": 5,
            "b11": 6,
            "b12": 6,
            "b13": 6,
            "b14": 7,
        },
        resources=Resources(760_000, 240_000, 410_000, 380_000),
        researches=researches,
        temperature=61,
        fields_max=188,
        fields_used=188,
        storage=big_storage,
        ships={"s1": 8, "s2": 6, "s11": 4},
        economy_rates={"resource1": 100, "resource2": 50, "resource3": 100, "resource4": 100, "resource5": 100},
    )
    return capital, forge, outpost


def _lategame_outer_planets(researches: Mapping[str, int]) -> tuple[PlanetState, ...]:
    reach = _planet(
        20004,
        "Golden Reach",
        "03:124:09",
        buildings={"b1": 18, "b2": 12, "b3": 15, "b4": 13, "b5": 19, "b7": 10, "b9": 4, "b10": 3, "b11": 4, "b12": 4},
        resources=Resources(310_000, 88_000, 140_000, 52_000),
        researches=researches,
        temperature=-52,
        fields_max=334,
        storage=Resources(1_600_000, 1_600_000, 1_600_000, 1_600_000),
        ships={"s1": 6, "s2": 2},
        economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
    )
    asteroid = _planet(
        20005,
        "Golden Shard",
        "03:120:07",
        buildings={"b7": 3, "b10": 2},
        resources=Resources(40_000, 20_000, 20_000, 30_000),
        researches=researches,
        planet_type="asteroid",
        temperature=-90,
        fields_max=12,
        storage=Resources(200_000, 200_000, 200_000, 200_000),
        ships={"s4": 40, "s10": 4},
    )
    return reach, asteroid


def lategame_five_colonies() -> GameState:
    """Five developed planets, one of them an asteroid and one out of free fields."""
    researches = {
        "r1": 11,
        "r2": 10,
        "r3": 9,
        "r4": 9,
        "r5": 8,
        "r6": 9,
        "r7": 7,
        "r8": 6,
        "r9": 5,
        "r10": 4,
        "r11": 3,
        "r12": 3,
        "r13": 2,
        "r14": 2,
        "r15": 1,
    }
    capital, forge, outpost = _lategame_inner_planets(researches)
    reach, asteroid = _lategame_outer_planets(researches)
    return _state(
        (capital, forge, outpost, reach, asteroid),
        researches,
        research_offers=_research_offers(researches, capital.resources, ("r1", "r2", "r5", "r8", "r12")),
        active_queues=(
            QueueJob(
                "fleet",
                "Transport [03:120:11] 00:18:30",
                remaining_text="00:18:30",
                remaining_seconds=1110.0,
                finish_ts=GOLDEN_EPOCH + 1110.0,
                origin_coords="03:120:07",
                cargo=Resources(200_000, 50_000, 0, 0),
            ),
        ),
        speedup_factor=5.0,
        newbie_protection=False,
        account_points=1_284_000,
        account_rank=37,
        raid_targets=_LATEGAME_RAID_TARGETS,
    )


STATES = {
    "early_homeworld": early_homeworld,
    "midgame_three_colonies": midgame_three_colonies,
    "lategame_five_colonies": lategame_five_colonies,
}


def default_config() -> StrategyConfig:
    return StrategyConfig(planner_mode="priority")


def full_auto_config() -> StrategyConfig:
    """Every automation switch on, so the plan covers fleet, raid, colony and transport branches."""
    return StrategyConfig(
        planner_mode="priority",
        playstyle="hybrid",
        allow_fleetsave=True,
        allow_galaxy_scan=True,
        allow_raids=True,
        allow_colonize=True,
        allow_transport=True,
        allow_transmitter=True,
        max_planets=6,
    )


CONFIGS = {"default": default_config, "full_auto": full_auto_config}

# Small fixed search budget: the GA still runs (two generations) but a case stays under a few seconds.
AI_PLANNER_PARAMS: dict[str, Any] = {
    "reward_weeks": 2,
    "ga_population_size": 4,
    "ga_generations": 2,
    "ga_random_seed": GOLDEN_SEED,
}

_AI_TARGET_BUMPS: dict[str, int] = {"b1": 3, "b2": 2, "b3": 3, "b4": 2, "b5": 3, "b7": 1}
# The path planner rejects a target whose warehouses cannot hold the cost of a planned level.
_WAREHOUSE_KEYS = ("b11", "b12", "b13", "b14")
_AI_TARGET_MIN_WAREHOUSE_LEVEL = 6


def ai_target_config(state: GameState) -> dict[str, Any]:
    """Hand-built Config Optimizer result: every colony grows its mines and power by a few levels."""
    planets: list[dict[str, Any]] = []
    slot = 0
    for planet in state.planets:
        if planet.planet_type != "normal":
            continue
        slot += 1
        targets = {k: int(v) for k, v in sorted(planet.buildings.items(), key=lambda kv: int(kv[0][1:]))}
        for bkey, bump in _AI_TARGET_BUMPS.items():
            targets[bkey] = int(targets.get(bkey, 0)) + bump
        for bkey in _WAREHOUSE_KEYS:
            targets[bkey] = max(int(targets.get(bkey, 0)) + 2, _AI_TARGET_MIN_WAREHOUSE_LEVEL)
        targets = dict(sorted(targets.items(), key=lambda kv: int(kv[0][1:])))
        planets.append(
            {
                "slot": slot,
                "slot_index": slot,
                "planet_id": planet.planet_id,
                "coords": planet.coords,
                "name": planet.name,
                "is_placeholder": False,
                "planet_status": "colonized",
                "is_central_planet": slot == 1,
                "pays_for_research": slot == 1,
                "temperature": planet.temperature,
                "temperature_c": planet.temperature,
                "fields_max": planet.fields_max,
                "buildings": [{"key": k, "target_level": v} for k, v in targets.items()],
            }
        )
    planned_research = {f"r{i}": int(state.researches.get(f"r{i}", 0)) for i in range(1, 16)}
    planned_research["r1"] += 1
    planned_research["r2"] += 1
    return {
        "schema_version": 1,
        "generated_at": "2026-11-20T00:00:00+00:00",
        "meta_parameters": {"min_fleet_per_planet": {"s2": 10, "s10": 10, "s11": 10}},
        "inputs": {"universe_speedup_factor": float(state.speedup_factor or 1.0)},
        "planned_research_levels": planned_research,
        "summary": {"target_ship": "Imperial starbase", "ships_per_month": 10.0},
        "planets": planets,
    }
