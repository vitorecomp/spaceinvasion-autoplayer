"""Seeded fuzz invariant suite for ``spaceinvasion.combat.*`` and ``spaceinvasion.planner.eta``."""

from __future__ import annotations

import math
from pathlib import Path
import random

import pytest

from spaceinvasion.combat import cargo, colonies, flight, simulator
from spaceinvasion.core.model import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.parsing.fleet_page import parse_send_fleet_mission_block
from spaceinvasion.parsing.models import SpyReport
from spaceinvasion.planner import eta

_SHIPS: tuple[str, ...] = tuple(f"s{i}" for i in range(1, 17))
_DEFS: tuple[str, ...] = tuple(f"d{i}" for i in range(1, 10))
_FUZZ_ITERATIONS = 100
_FUZZ_SEED = 1234
_GOLDEN_PAGES_DIR = Path(__file__).resolve().parents[2] / "goldens" / "pages"


def _rand_fleet(rng: random.Random, pool: tuple[str, ...], *, k: int = 4, hi: int = 60) -> dict[str, int]:
    return {s: rng.randint(0, hi) for s in rng.sample(pool, rng.randint(0, k))}


def _rand_res(rng: random.Random, hi: int = 200_000) -> Resources:
    return Resources(rng.randint(0, hi), rng.randint(0, hi), rng.randint(0, hi), rng.randint(0, hi // 4))


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


def test_fuzz_combat_cargo_colonies_flight_and_eta_invariants() -> None:
    rng = random.Random(_FUZZ_SEED)
    for _ in range(_FUZZ_ITERATIONS):
        rep = SpyReport(
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
        raid = simulator.simulate_raid_outcome(att, rep, attacker_researches=ares, safety_margin=margin)
        assert raid.power_ratio >= 0.0

        pl = _rand_planet(rng)
        esc = cargo.plan_escape_flight(
            pl,
            target_coords=_rand_coords(rng),
            fuel_reserve_spice=rng.choice([0, 500, 5000]),
            save_solar_cells=rng.random() < 0.3,
        )
        assert esc.total_capacity >= 0 and esc.cargo.total() >= 0

        graded = colonies.grade_colony_candidate(
            coords=_rand_coords(rng),
            origin_coords=_rand_coords(rng),
            position=rng.choice([None, *range(1, 16)]),
            uninhabited_fields=rng.choice([None, 0, 150, 299, 300, 340, 400]),
            colonize_min_fields=rng.choice([None, 120, 250, 310]),
            colonize_max_fields=rng.choice([None, 200, 330, 420]),
            temperature=rng.choice([None, -50, 20, 90]),
            spied=rng.random() < 0.5,
            min_fields_target=rng.choice([250, 300, 330]),
            last_spied_ts=rng.random() * 1e9,
            last_scanned_ts=rng.random() * 1e9,
        )
        assert "grade" in graded and "score" in graded

        secs = flight.estimate_fleet_flight_seconds(
            _rand_coords(rng),
            rng.choice([_rand_coords(rng), "bad", "", "1:2:3"]),
            _rand_fleet(rng, _SHIPS, k=4, hi=50),
            researches=ares,
            round_trip=rng.random() < 0.5,
        )
        assert secs is None or (math.isfinite(secs) and secs >= 0.0)

        cap = rng.randint(0, 400_000)
        packed = cargo.pack_transport_cargo(
            _rand_res(rng), _rand_res(rng), rng.choice([None, _rand_res(rng)]), cap, fill_to_capacity=rng.random() < 0.5
        )
        assert packed.total() <= cap

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
        pecon = eta.estimate_planet_hourly_economy(st, planets[0])
        assert float(pecon["metal_per_hr"]) >= 0.0  # type: ignore[arg-type]
        tbl = eta.build_buildings_eta(st, planets[0])
        assert "buildings" in tbl


def test_fuzz_bash_tracker_sequences_invariants() -> None:
    rng = random.Random(_FUZZ_SEED + 1)
    for _ in range(_FUZZ_ITERATIONS):
        bt = flight.BashTracker()
        t0 = 1_700_000_000.0
        targets = [_rand_coords(rng) for _ in range(3)]
        for step in range(30):
            op = rng.choice(["att", "can", "blk"])
            tgt = rng.choice(targets)
            ts = t0 + step * rng.choice([60.0, 3600.0, 20000.0])
            if op == "att":
                bt.record_attack(tgt, timestamp=ts)
            elif op == "blk":
                bt.record_block(tgt, ts + rng.choice([60.0, 7200.0]), reason="r")
            else:
                res = bt.can_attack(
                    tgt,
                    is_inactive=rng.random() < 0.3,
                    is_spacia=rng.random() < 0.2,
                    in_declared_war=rng.random() < 0.2,
                    timestamp=ts,
                )
                assert isinstance(res, bool)


@pytest.mark.parametrize("kind", ("ATTACK", "SPY", "TRANSPORT", "STATION", "SAVE", "COLONIZE"))
def test_fuzz_mission_banner_parser_invariants(kind: str) -> None:
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
        out = parse_send_fleet_mission_block(html, kind)
        assert out is None or len(out) == 3
