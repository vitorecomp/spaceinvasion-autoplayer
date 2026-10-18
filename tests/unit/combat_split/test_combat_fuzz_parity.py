"""Seeded differential fuzz suite: legacy combat.fleet/ranking vs new combat/* and planner/eta."""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import math
from pathlib import Path
import random

import pytest

from spaceinvasion.combat import cargo, colonies, fleet as legacy_fleet, flight, ranking as legacy_ranking, simulator
from spaceinvasion.core.state import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.planner import eta

_SHIPS: tuple[str, ...] = tuple(f"s{i}" for i in range(1, 17))
_DEFS: tuple[str, ...] = tuple(f"d{i}" for i in range(1, 10))
_FUZZ_ITERATIONS = 100
_FUZZ_SEED = 1234
_GOLDEN_PAGES_DIR = Path(__file__).resolve().parents[2] / "goldens" / "pages"


def _norm(val: object) -> object:
    if dataclasses.is_dataclass(val) and not isinstance(val, type):
        return {k: _norm(v) for k, v in dataclasses.asdict(val).items()}
    if isinstance(val, dict):
        return {str(k): _norm(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_norm(v) for v in val]
    if isinstance(val, float):
        return None if math.isnan(val) else round(val, 9)
    return val


def _assert_same(label: str, legacy_fn: Callable[[], object], new_fn: Callable[[], object]) -> None:
    try:
        legacy_out: tuple[str, object] = ("ok", _norm(legacy_fn()))
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        legacy_out = ("exc", type(exc).__name__)
    try:
        new_out: tuple[str, object] = ("ok", _norm(new_fn()))
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        new_out = ("exc", type(exc).__name__)
    assert new_out == legacy_out, f"{label} mismatch: legacy={legacy_out!r} new={new_out!r}"


def _rand_fleet(rng: random.Random, pool: tuple[str, ...], *, k: int = 4, hi: int = 60) -> dict[str, int]:
    return {s: rng.randint(0, hi) for s in rng.sample(pool, rng.randint(0, k))}


def _rand_res(rng: random.Random, hi: int = 200_000) -> Resources:
    return Resources(
        rng.randint(0, hi),
        rng.randint(0, hi),
        rng.randint(0, hi),
        rng.randint(0, hi // 4),
    )


def _rand_coords(rng: random.Random) -> str:
    return f"{rng.randint(1, 9):02d}:{rng.randint(1, 499):03d}:{rng.randint(1, 15):02d}"


def _rand_planet(rng: random.Random, pid: int = 101) -> PlanetState:
    buildings = {f"b{i}": rng.randint(0, 25) for i in rng.sample(range(1, 22), rng.randint(0, 12))}
    return PlanetState(
        planet_id=pid,
        name="P",
        coords=_rand_coords(rng),
        planet_type=rng.choice(["normal", "asteroid", "moon"]),
        temperature=rng.randint(-120, 160),
        fields_used=rng.randint(0, 200),
        fields_max=rng.randint(100, 400),
        resources=_rand_res(rng),
        storage=_rand_res(rng, 2_000_000),
        energy=EnergyBalance(produced=rng.randint(0, 3000), used=rng.randint(0, 3000)),
        buildings=buildings,
        ships=_rand_fleet(rng, _SHIPS, k=6, hi=300),
        economy_rates={f"resource{i}": rng.choice([0, 10, 50, 100]) for i in range(1, 5)},
    )


def test_fuzz_combat_cargo_colonies_flight_and_eta_parity() -> None:
    rng = random.Random(_FUZZ_SEED)
    for _ in range(_FUZZ_ITERATIONS):
        rep = legacy_fleet.SpyReport(
            target_coords=_rand_coords(rng),
            owner=rng.choice(["", "x", "Spacia"]),
            resources=_rand_res(rng),
            fleet=_rand_fleet(rng, _SHIPS),
            defense=_rand_fleet(rng, _DEFS, k=5, hi=200),
            dug_in=rng.choice([None, True, False]),
            is_spacia=rng.random() < 0.2,
        )
        att = _rand_fleet(rng, _SHIPS, k=5, hi=120)
        ares = {f"r{i}": rng.randint(0, 15) for i in rng.sample(range(1, 16), 4)}
        margin = rng.choice([1.0, 1.4, 2.0])
        _assert_same(
            "simulate_raid_outcome",
            lambda: legacy_fleet.simulate_raid_outcome(att, rep, attacker_researches=ares, safety_margin=margin),
            lambda: simulator.simulate_raid_outcome(att, rep, attacker_researches=ares, safety_margin=margin),
        )

        pl = _rand_planet(rng)
        tc = _rand_coords(rng)
        fr = rng.choice([0, 500, 5000])
        ssc = rng.random() < 0.3
        _assert_same(
            "plan_escape_flight",
            lambda: legacy_fleet.plan_escape_flight(pl, target_coords=tc, fuel_reserve_spice=fr, save_solar_cells=ssc),
            lambda: cargo.plan_escape_flight(pl, target_coords=tc, fuel_reserve_spice=fr, save_solar_cells=ssc),
        )

        coords = _rand_coords(rng)
        origin = _rand_coords(rng)
        pos = rng.choice([None, *range(1, 16)])
        uf = rng.choice([None, 0, 150, 299, 300, 340, 400])
        cmin = rng.choice([None, 120, 250, 310])
        cmax = rng.choice([None, 200, 330, 420])
        temp = rng.choice([None, -50, 20, 90])
        spied = rng.random() < 0.5
        mft = rng.choice([250, 300, 330])
        lsp = rng.random() * 1e9
        lsc = rng.random() * 1e9
        _assert_same(
            "grade_colony_candidate",
            lambda: legacy_fleet.grade_colony_candidate(
                coords=coords,
                origin_coords=origin,
                position=pos,
                uninhabited_fields=uf,
                colonize_min_fields=cmin,
                colonize_max_fields=cmax,
                temperature=temp,
                spied=spied,
                min_fields_target=mft,
                last_spied_ts=lsp,
                last_scanned_ts=lsc,
            ),
            lambda: colonies.grade_colony_candidate(
                coords=coords,
                origin_coords=origin,
                position=pos,
                uninhabited_fields=uf,
                colonize_min_fields=cmin,
                colonize_max_fields=cmax,
                temperature=temp,
                spied=spied,
                min_fields_target=mft,
                last_spied_ts=lsp,
                last_scanned_ts=lsc,
            ),
        )

        o_c = _rand_coords(rng)
        t_c = rng.choice([_rand_coords(rng), "bad", "", "1:2:3"])
        fl = _rand_fleet(rng, _SHIPS, k=4, hi=50)
        rt = rng.random() < 0.5
        _assert_same(
            "estimate_fleet_flight_seconds",
            lambda: legacy_fleet.estimate_fleet_flight_seconds(o_c, t_c, fl, researches=ares, round_trip=rt),
            lambda: flight.estimate_fleet_flight_seconds(o_c, t_c, fl, researches=ares, round_trip=rt),
        )

        need = _rand_res(rng)
        sur = _rand_res(rng)
        dfs = rng.choice([None, _rand_res(rng)])
        cap = rng.randint(0, 400_000)
        fill = rng.random() < 0.5
        _assert_same(
            "pack_transport_cargo",
            lambda: legacy_fleet.pack_transport_cargo(need, sur, dfs, cap, fill_to_capacity=fill),
            lambda: cargo.pack_transport_cargo(need, sur, dfs, cap, fill_to_capacity=fill),
        )

        planets = tuple(_rand_planet(rng, 100 + i) for i in range(rng.randint(1, 4)))
        st = GameState(
            31,
            1,
            "C",
            1_700_000_000.0,
            planets,
            active_planet_id=planets[0].planet_id,
            researches={f"r{i}": rng.randint(0, 20) for i in range(1, 16)},
        )
        _assert_same(
            "estimate_planet_hourly_economy",
            lambda: legacy_ranking.estimate_planet_hourly_economy(st, planets[0]),
            lambda: eta.estimate_planet_hourly_economy(st, planets[0]),
        )
        _assert_same(
            "build_buildings_eta",
            lambda: legacy_ranking.build_buildings_eta(st, planets[0]),
            lambda: eta.build_buildings_eta(st, planets[0]),
        )


def test_fuzz_bash_tracker_sequences_parity() -> None:
    rng = random.Random(_FUZZ_SEED + 1)
    for _ in range(_FUZZ_ITERATIONS):
        legacy_bt = legacy_fleet.BashTracker()
        new_bt = flight.BashTracker()
        t0 = 1_700_000_000.0
        targets = [_rand_coords(rng) for _ in range(3)]
        for step in range(30):
            op = rng.choice(["att", "can", "blk"])
            tgt = rng.choice(targets)
            ts = t0 + step * rng.choice([60.0, 3600.0, 20000.0])
            if op == "att":
                legacy_bt.record_attack(tgt, timestamp=ts)
                new_bt.record_attack(tgt, timestamp=ts)
            elif op == "blk":
                until = ts + rng.choice([60.0, 7200.0])
                legacy_bt.record_block(tgt, until, reason="r")
                new_bt.record_block(tgt, until, reason="r")
            else:
                inact = rng.random() < 0.3
                spacia = rng.random() < 0.2
                war = rng.random() < 0.2
                _assert_same(
                    "BashTracker.can_attack",
                    lambda: legacy_bt.can_attack(
                        tgt, is_inactive=inact, is_spacia=spacia, in_declared_war=war, timestamp=ts
                    ),
                    lambda: new_bt.can_attack(
                        tgt, is_inactive=inact, is_spacia=spacia, in_declared_war=war, timestamp=ts
                    ),
                )


@pytest.mark.parametrize("kind", ("ATTACK", "SPY", "TRANSPORT", "STATION", "SAVE", "COLONIZE"))
def test_fuzz_mission_banner_parser_parity(kind: str) -> None:
    base = (_GOLDEN_PAGES_DIR / "send_fleet_blocked.html").read_text(encoding="utf-8")
    variants = (
        base,
        base.replace("Reset in", "reset in"),
        "<div>Attack (forbidden): You have attacked this planet too often! Attack-Bash-Regulations active. Reset in 04:30:00</div>",
        "<div class='x'>Spy <b>(forbidden)</b>: This user can't be attacked!</div>",
        "<p>Transport (forbidden): noob protection</p>",
        "<table><tr><td>Station (forbidden): Not your planet</td></tr></table>",
        "",
        "<div>nothing here</div>",
        "<div>Angriff (verboten): Bash-Schutz aktiv. Reset in 1:02:03</div>",
        "<div>Attack (forbidden):</div><div>Reset in 00:00:30</div>",
    )
    for html in variants:

        def _run_legacy(h: str = html) -> object:
            return legacy_fleet.parse_send_fleet_mission_block(h, kind)

        def _run_new(h: str = html) -> object:
            return flight.parse_send_fleet_mission_block(h, kind)

        _assert_same(f"parse_send_fleet_mission_block[{kind}]", _run_legacy, _run_new)
