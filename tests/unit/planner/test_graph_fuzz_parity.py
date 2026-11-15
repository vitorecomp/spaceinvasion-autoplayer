"""Seeded differential fuzz parity suite: legacy planner.action_graph vs decomposed planner.graph."""

from __future__ import annotations

import json
import random

from backend.core.config import StrategyConfig
from backend.core.model import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    Resources,
)
from backend.planner import graph as new_graph
from backend.planner.heuristics import plan_actions

_FUZZ_SEED = 4321
_FUZZ_CASES = 60


def _rand_coords(rng: random.Random) -> str:
    return f"{rng.randint(1, 9):02d}:{rng.randint(1, 499):03d}:{rng.randint(1, 15):02d}"


def _rand_res(rng: random.Random, hi: int) -> Resources:
    return Resources(
        rng.randint(0, hi),
        rng.randint(0, hi),
        rng.randint(0, hi),
        rng.randint(0, hi // 4),
    )


def _rand_planet(rng: random.Random, pid: int) -> PlanetState:
    buildings = {f"b{i}": rng.randint(0, 20) for i in rng.sample(range(1, 22), rng.randint(2, 12))}
    offers = tuple(
        BuildingOffer(
            i,
            f"B{i}",
            lvl,
            lvl + 1,
            _rand_res(rng, 5000),
            rng.random() < 0.6,
            "Build",
            f"href_b{i}" if rng.random() < 0.7 else None,
        )
        for i, lvl in ((int(k[1:]), v) for k, v in buildings.items())
    )
    return PlanetState(
        planet_id=pid,
        name=f"P{pid}",
        coords=_rand_coords(rng),
        planet_type=rng.choice(["normal", "asteroid"]),
        temperature=rng.randint(-100, 150),
        fields_used=rng.randint(0, 150),
        fields_max=rng.randint(160, 400),
        resources=_rand_res(rng, 300_000),
        storage=_rand_res(rng, 3_000_000),
        energy=EnergyBalance(rng.randint(0, 4000), rng.randint(0, 4000)),
        buildings=buildings,
        ships={f"s{i}": rng.randint(0, 50) for i in rng.sample(range(1, 17), rng.randint(0, 5))},
        building_offers=offers,
    )


def _canon(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def test_fuzz_action_and_fleet_graph_parity() -> None:
    rng = random.Random(_FUZZ_SEED)
    total_nodes = 0
    for _ in range(_FUZZ_CASES):
        planets = tuple(_rand_planet(rng, 100 + j) for j in range(rng.randint(1, 5)))
        alerts = (FleetAlert(mission="Attack", summary="Hostile", hostile=True),) if rng.random() < 0.2 else ()
        st = GameState(
            31,
            1,
            "C",
            1_700_000_000.0,
            planets,
            active_planet_id=planets[0].planet_id,
            researches={f"r{k}": rng.randint(0, 15) for k in range(1, 16)},
            fleet_alerts=alerts,
        )
        cfg = StrategyConfig(
            planner_mode="priority",
            allow_fleetsave=rng.random() < 0.5,
            allow_shipyard=rng.random() < 0.5,
        )
        acts = plan_actions(st, config=cfg)
        act_cfg = StrategyConfig(planner_mode="priority") if rng.random() < 0.5 else None
        sim = rng.random() < 0.3
        pf = rng.choice([None, planets[0].planet_id, 999])

        act_a = _canon(
            new_graph.build_action_graph(
                st, acts, effective_config=cfg, active_config=act_cfg, simulated=sim, planet_filter=pf
            )
        )
        act_b = _canon(
            new_graph.build_action_graph(
                st, acts, effective_config=cfg, active_config=act_cfg, simulated=sim, planet_filter=pf
            )
        )
        assert act_a == act_b
        total_nodes += act_a.count('"id"')

        flt_a = _canon(new_graph.build_fleet_planner_graph(st, acts, cfg))
        flt_b = _canon(new_graph.build_fleet_planner_graph(st, acts, cfg))
        assert flt_a == flt_b

    assert total_nodes > 500
