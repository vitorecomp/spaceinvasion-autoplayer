"""Fleet inventory, tutorial state, and colony-cap context assembly for fleet mission planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import time

from spaceinvasion.combat.flight import BashTracker, normalize_coords
from spaceinvasion.combat.simulator import CombatReport
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    GameState,
    PlanetState,
    Resources,
    TutorialTask,
    count_in_flight_fleets_for_planet,
    count_in_flight_spy_fleets_for_planet,
    extract_in_flight_colonize_coords,
    extract_in_flight_recycle_coords,
    is_in_flight_fleet_job,
    is_undersized_fresh_colony,
)
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._fleet_bash import (
    _count_qualified_colony_coords,
    _hydrate_bash_tracker,
    _parse_scanned_systems,
)
from spaceinvasion.planner.heuristics._tutorial_tasks import (
    _tutorial_colonize_task,
    _tutorial_escape_task,
    _tutorial_fight_task,
    _tutorial_recycle_task,
    _tutorial_spy_task,
)
from spaceinvasion.planner.heuristics._types import (
    _COMBAT_AND_RAID_SHIP_KEYS,
    FLEET_BASE,
)


@dataclass(frozen=True)
class _TargetSlot:
    """Lightweight galaxy slot satisfying `GalaxySlotLike` without importing `combat.fleet`."""

    coords: str
    position: int
    name: str = ""
    owner: str = ""
    status_color: str = "free"
    is_spacia: bool = False


@dataclass(frozen=True)
class _DebrisEntry:
    norm_coords: str
    owner: str
    pname: str
    known_debris: int


@dataclass(frozen=True)
class _AttackEntry:
    norm_coords: str
    slot: _TargetSlot
    report: CombatReport
    tag: str
    target_label: str
    res: Resources


@dataclass
class _S8PoolEntry:
    planet: PlanetState
    count: int


@dataclass(frozen=True)
class _ShipPools:
    attack_planets: list[tuple[PlanetState, dict[str, int]]]
    origin: PlanetState
    attack_ships: dict[str, int]
    has_attack_fleet: bool
    designated_spy_planet: PlanetState | None
    probe_planet: PlanetState | None
    has_probes: bool
    designated_missing_probes: bool
    can_plan_spy: bool
    recycler_planets: list[PlanetState]
    recycler_planet: PlanetState | None
    has_recyclers: bool
    colony_planet: PlanetState | None
    has_colony_ship: bool
    s8_pool: list[tuple[PlanetState, int]]
    total_stationed_s8: int
    queued_s8: int


@dataclass(frozen=True)
class _FleetContext:
    state: GameState
    ref_now: float
    tut_fight: TutorialTask | None
    tut_spy: TutorialTask | None
    tut_escape: TutorialTask | None
    tut_recycle: TutorialTask | None
    tut_colonize: TutorialTask | None
    min_colony_fields: int
    undersized_pids: set[int]
    in_flight_col_coords: set[str]
    in_flight_recycle_coords: set[str]
    needed_colonies: int
    open_colony_slots: int
    qualified_candidates_count: int
    want_colony_probes: bool
    want_colonize: bool
    res_spy_cfg_pre: int
    pools: _ShipPools
    raid_targets: Sequence[object]
    scanned_map: dict[str, float]
    bash_tracker: BashTracker
    latest_battle_by_sys: dict[str, float]
    stats_sources: list[Mapping[str, object]]
    own_coords: set[str]
    planet_by_id: dict[int, PlanetState]
    used_slots_by_pid: dict[int, int]
    used_spy_slots_by_pid: dict[int, int]
    own_fleet_in_flight: bool

    @property
    def tutorial_fight_pending(self) -> bool:
        return self.tut_fight is not None

    @property
    def tutorial_spy_pending(self) -> bool:
        return self.tut_spy is not None

    @property
    def tutorial_escape_pending(self) -> bool:
        return self.tut_escape is not None

    @property
    def tutorial_recycle_pending(self) -> bool:
        return self.tut_recycle is not None

    @property
    def tutorial_colonize_pending(self) -> bool:
        return self.tut_colonize is not None

    def max_slots_for(self, p_obj: PlanetState) -> int:
        return 1 + max(0, p_obj.building_level(FLEET_BASE))

    def used_slots_for(self, p_obj: PlanetState) -> int:
        return self.used_slots_by_pid.get(p_obj.planet_id, count_in_flight_fleets_for_planet(self.state, p_obj))

    def used_spy_slots_for(self, p_obj: PlanetState) -> int:
        return self.used_spy_slots_by_pid.get(p_obj.planet_id, count_in_flight_spy_fleets_for_planet(self.state, p_obj))


def _planet_attack_ships(planet: PlanetState) -> dict[str, int]:
    return {
        k: max(0, _safe_strategy_int(planet.ships.get(k, 0), 0))
        for k in _COMBAT_AND_RAID_SHIP_KEYS
        if _safe_strategy_int(planet.ships.get(k, 0), 0) > 0
    }


def _select_probe_planet(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
) -> tuple[PlanetState | None, PlanetState | None, bool, bool]:
    req_spy_pid = max(0, _safe_strategy_int(getattr(cfg, "spy_dispatch_planet_id", 0), 0))
    designated = (
        next((p for p in state.planets if p.planet_id == req_spy_pid and _norm_planet_type(p) != "raid"), None)
        if req_spy_pid > 0
        else None
    )
    if designated is not None:
        has_probes = _safe_strategy_int(designated.ships.get("s10", 0), 0) > 0
        return designated, designated, has_probes, not has_probes
    probe_planet = (
        active
        if _norm_planet_type(active) != "raid" and _safe_strategy_int(active.ships.get("s10", 0), 0) > 0
        else next(
            (
                p
                for p in state.planets
                if _norm_planet_type(p) != "raid" and _safe_strategy_int(p.ships.get("s10", 0), 0) > 0
            ),
            None,
        )
    )
    return None, probe_planet, probe_planet is not None, False


def _collect_ship_pools(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    undersized_pids: set[int],
) -> _ShipPools:
    attack_planets: list[tuple[PlanetState, dict[str, int]]] = []
    seen_atk_pids: set[int] = set()
    for candidate in [active, *state.planets]:
        if (
            candidate.planet_id in seen_atk_pids
            or _norm_planet_type(candidate) == "raid"
            or (candidate.planet_id in undersized_pids and candidate.planet_id != active.planet_id)
        ):
            continue
        cand_ships = _planet_attack_ships(candidate)
        if cand_ships:
            seen_atk_pids.add(candidate.planet_id)
            attack_planets.append((candidate, cand_ships))
    origin, attack_ships = attack_planets[0] if attack_planets else (active, {})
    designated_spy_planet, probe_planet, has_probes, designated_missing_probes = _select_probe_planet(
        state, active, cfg
    )

    recycler_planets: list[PlanetState] = []
    seen_rec_pids: set[int] = set()
    for candidate in [active, *state.planets]:
        if (
            candidate.planet_id in seen_rec_pids
            or _norm_planet_type(candidate) == "raid"
            or (candidate.planet_id in undersized_pids and candidate.planet_id != active.planet_id)
        ):
            continue
        if _safe_strategy_int(candidate.ships.get("s11", 0), 0) > 0:
            seen_rec_pids.add(candidate.planet_id)
            recycler_planets.append(candidate)

    colony_planet = (
        active
        if _norm_planet_type(active) != "raid" and _safe_strategy_int(active.ships.get("s8", 0), 0) > 0
        else next(
            (
                p
                for p in state.planets
                if _norm_planet_type(p) != "raid" and _safe_strategy_int(p.ships.get("s8", 0), 0) > 0
            ),
            None,
        )
    )
    s8_pool: list[tuple[PlanetState, int]] = []
    seen_s8: set[int] = set()
    for p_s8 in ([colony_planet] if colony_planet is not None else []) + list(state.planets):
        if p_s8 is None or _norm_planet_type(p_s8) == "raid" or p_s8.planet_id in undersized_pids:
            continue
        if p_s8.planet_id in seen_s8:
            continue
        cnt_s8 = max(0, _safe_strategy_int(p_s8.ships.get("s8", 0), 0))
        if cnt_s8 > 0:
            seen_s8.add(p_s8.planet_id)
            s8_pool.append((p_s8, cnt_s8))
    queued_s8 = sum(
        1
        for q in (state.active_queues or ())
        if _safe_strategy_str(getattr(q, "category", "")).lower() in ("ship", "shipyard")
        and "colony ship" in _safe_strategy_str(getattr(q, "name", "")).lower()
    )
    return _ShipPools(
        attack_planets=attack_planets,
        origin=origin,
        attack_ships=attack_ships,
        has_attack_fleet=bool(attack_planets),
        designated_spy_planet=designated_spy_planet,
        probe_planet=probe_planet,
        has_probes=has_probes,
        designated_missing_probes=designated_missing_probes,
        can_plan_spy=probe_planet is not None,
        recycler_planets=recycler_planets,
        recycler_planet=recycler_planets[0] if recycler_planets else None,
        has_recyclers=bool(recycler_planets),
        colony_planet=colony_planet,
        has_colony_ship=colony_planet is not None,
        s8_pool=s8_pool,
        total_stationed_s8=sum(cnt for _, cnt in s8_pool),
        queued_s8=queued_s8,
    )


def _build_fleet_context(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    *,
    scanned_systems: Mapping[str, float] | None,
    now_ts: float | None,
    raid_stats: Mapping[str, object] | None,
) -> _FleetContext | None:
    """Assemble fleet inventory, colony counts, and Bash state; return `None` if nothing can run."""
    ref_now = _safe_strategy_float(now_ts, time.time()) if now_ts is not None else time.time()
    tut_fight = _tutorial_fight_task(state)
    tut_spy = _tutorial_spy_task(state)
    tut_escape = _tutorial_escape_task(state)
    tut_recycle = _tutorial_recycle_task(state)
    tut_colonize = _tutorial_colonize_task(state)

    max_planets_cap = max(1, _safe_strategy_int(getattr(cfg, "max_planets", 11), 11))
    min_colony_fields = max(50, _safe_strategy_int(getattr(cfg, "min_colony_fields", 300), 300))
    undersized_colonies = [p for p in state.planets if is_undersized_fresh_colony(p, state, min_colony_fields)]
    undersized_pids = {p.planet_id for p in undersized_colonies}
    raw_allocated_normal = sum(1 for p in state.planets if _norm_planet_type(p) == "normal")
    allocated_normal = max(1, raw_allocated_normal - len(undersized_colonies)) if raw_allocated_normal > 0 else 0
    in_flight_col_coords = extract_in_flight_colonize_coords(state)
    in_flight_recycle_coords = extract_in_flight_recycle_coords(state)
    needed_colonies = max(0, max_planets_cap - allocated_normal)
    open_colony_slots = max(0, needed_colonies - len(in_flight_col_coords))

    pools = _collect_ship_pools(state, active, cfg, undersized_pids)
    raid_targets = getattr(state, "raid_targets", ()) or ()
    own_coords = {normalize_coords(p.coords) for p in state.planets}
    qualified_count = _count_qualified_colony_coords(raid_targets, own_coords, min_colony_fields)
    want_colony_probes = bool(
        (cfg.allow_galaxy_scan or cfg.allow_colonize or tut_colonize is not None)
        and allocated_normal < max_planets_cap
        and qualified_count < needed_colonies
    )
    want_colonize = bool((cfg.allow_colonize or tut_colonize is not None) and open_colony_slots > 0)

    if (
        not raid_targets
        and tut_fight is None
        and tut_spy is None
        and tut_recycle is None
        and not want_colonize
        and not want_colony_probes
        and not cfg.allow_galaxy_scan
        and not cfg.allow_raids
        and not getattr(cfg, "allow_transport", False)
        and not pools.has_attack_fleet
        and not pools.can_plan_spy
        and not pools.has_recyclers
    ):
        return None

    scanned_map = _parse_scanned_systems(scanned_systems)
    bash_tracker, latest_battle_by_sys, stats_sources = _hydrate_bash_tracker(state, raid_stats, ref_now)
    used_fleet_slots = sum(1 for q in (state.active_queues or ()) if is_in_flight_fleet_job(q))
    return _FleetContext(
        state=state,
        ref_now=ref_now,
        tut_fight=tut_fight,
        tut_spy=tut_spy,
        tut_escape=tut_escape,
        tut_recycle=tut_recycle,
        tut_colonize=tut_colonize,
        min_colony_fields=min_colony_fields,
        undersized_pids=undersized_pids,
        in_flight_col_coords=in_flight_col_coords,
        in_flight_recycle_coords=in_flight_recycle_coords,
        needed_colonies=needed_colonies,
        open_colony_slots=open_colony_slots,
        qualified_candidates_count=qualified_count,
        want_colony_probes=want_colony_probes,
        want_colonize=want_colonize,
        res_spy_cfg_pre=max(0, _safe_strategy_int(getattr(cfg, "reserved_spy_slots", 1), 1)),
        pools=pools,
        raid_targets=raid_targets,
        scanned_map=scanned_map,
        bash_tracker=bash_tracker,
        latest_battle_by_sys=latest_battle_by_sys,
        stats_sources=stats_sources,
        own_coords=own_coords,
        planet_by_id={p.planet_id: p for p in state.planets},
        used_slots_by_pid={p.planet_id: count_in_flight_fleets_for_planet(state, p) for p in state.planets},
        used_spy_slots_by_pid={p.planet_id: count_in_flight_spy_fleets_for_planet(state, p) for p in state.planets},
        own_fleet_in_flight=used_fleet_slots > 0,
    )
