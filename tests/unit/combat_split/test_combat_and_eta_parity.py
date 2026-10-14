"""1:1 numerical, structural, and layering parity tests for Phase 2.5/3 combat & ETA modules."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sys

import pytest

from spaceinvasion.combat import (
    cargo,
    colonies,
    fleet as legacy_fleet,
    flight,
    points,
    ranking as legacy_ranking,
    simulator,
)
from spaceinvasion.core.state import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    UnitOffer,
)
from spaceinvasion.planner import eta

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_rich_state() -> GameState:
    home = PlanetState(
        planet_id=101,
        name="Homeworld",
        coords="02:180:08",
        planet_type="planet",
        temperature=28,
        fields_used=142,
        fields_max=320,
        resources=Resources(pig_iron=85_000, metal=42_000, kryptonite=36_000, spice=18_500),
        storage=Resources(pig_iron=500_000, metal=500_000, kryptonite=500_000, spice=500_000),
        energy=EnergyBalance(produced=4_500, used=4_200),
        buildings={
            "b1": 18,
            "b2": 14,
            "b3": 15,
            "b4": 12,
            "b5": 14,
            "b6": 8,
            "b7": 10,
            "b8": 9,
            "b9": 8,
            "b10": 6,
            "b11": 7,
            "b12": 7,
            "b13": 6,
            "b14": 6,
            "b17": 2,
            "b19": 0,
        },
        ships={"s1": 18, "s2": 10, "s3": 2, "s4": 40, "s6": 12, "s9": 15, "s10": 14, "s11": 8, "s14": 5},
        defense={"d1": 30, "d2": 15, "d5": 6, "d10": 1},
        economy_rates={"resource1": 100, "resource2": 80, "resource3": 100, "resource4": 100},
        building_offers=(
            BuildingOffer(
                building_id=1,
                name="Pig iron mine",
                current_level=18,
                target_level=19,
                cost=Resources(pig_iron=90_000, metal=35_000, kryptonite=10_000, spice=0),
                can_build=False,
                status_text="Not enough resources",
            ),
        ),
        ship_offers=(
            UnitOffer(
                unit_type="ship",
                unit_id=4,
                name="Small fighter",
                owned=40,
                cost=Resources(pig_iron=3_000, metal=1_000, kryptonite=0, spice=0),
                can_build=True,
                status_text="Ready",
            ),
        ),
    )
    colony = PlanetState(
        planet_id=202,
        name="Cold Spice",
        coords="02:195:15",
        planet_type="planet",
        temperature=-42,
        fields_used=88,
        fields_max=305,
        resources=Resources(pig_iron=12_000, metal=9_000, kryptonite=7_500, spice=24_000),
        storage=Resources(pig_iron=250_000, metal=250_000, kryptonite=250_000, spice=250_000),
        energy=EnergyBalance(produced=1_400, used=1_750),
        buildings={"b1": 14, "b2": 10, "b3": 12, "b4": 15, "b5": 10, "b7": 6, "b8": 4, "b9": 5, "b10": 4},
        ships={"s1": 6, "s10": 5},
        defense={"d1": 10},
        economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
    )
    return GameState(
        universe_id=7,
        user_id=999,
        nickname="Commander",
        timestamp=1_700_000_000.0,
        speedup_factor=2.0,
        active_planet_id=101,
        account_points=128_450,
        account_rank=184,
        planets=(home, colony),
        researches={"r1": 12, "r2": 10, "r4": 8, "r5": 7, "r6": 5, "r7": 8, "r8": 9, "r9": 8, "r10": 7},
        active_queues=(
            QueueJob(category="building", name="Pig iron mine", building_id=1, target_level=19, remaining_seconds=900),
        ),
        research_offers=(
            ResearchOffer(
                research_key="r1",
                research_id=1,
                name="Mining technology",
                current_level=12,
                target_level=13,
                cost=Resources(pig_iron=0, metal=40_000, kryptonite=30_000, spice=15_000),
                can_research=True,
                status_text="Ready",
                active=False,
            ),
        ),
    )


def test_cargo_and_escape_flight_parity() -> None:
    ships = {"s1": 12, "s2": 5, "s3": 2, "s4": 25, "s11": 4, "battleship": 3, "s9": 10}
    for spec_lvl in (0, 3, 10):
        assert cargo.fleet_cargo_capacity(ships, miner_spec_level=spec_lvl) == legacy_fleet.fleet_cargo_capacity(
            ships, miner_spec_level=spec_lvl
        )

    state = _make_rich_state()
    home = state.planets[0]
    for fuel_res in (500, 15_000, 999_999):
        new_plan = cargo.plan_escape_flight(home, target_coords="02:195:15", fuel_reserve_spice=fuel_res)
        old_plan = legacy_fleet.plan_escape_flight(home, target_coords="02:195:15", fuel_reserve_spice=fuel_res)
        assert new_plan.__dict__ == old_plan.__dict__

    # Transport ship selection and cargo packing parity
    stationed = {"s1": 20, "s2": 6, "s3": 1, "s4": 10}
    for units in (0, 5_000, 42_000, 310_000, 999_999):
        assert cargo.select_transport_ships(stationed, units) == legacy_fleet.select_transport_ships(stationed, units)

    need = Resources(pig_iron=50_000, metal=25_000, kryptonite=15_000, spice=8_000)
    surplus = Resources(pig_iron=80_000, metal=40_000, kryptonite=20_000, spice=12_000)
    dest_free = Resources(pig_iron=60_000, metal=35_000, kryptonite=18_000, spice=10_000)
    for cap in (0, 20_000, 100_000, 250_000):
        for fill in (False, True):
            assert cargo.pack_transport_cargo(
                need, surplus, dest_free, cap, fill_to_capacity=fill
            ) == legacy_fleet.pack_transport_cargo(need, surplus, dest_free, cap, fill_to_capacity=fill)

    for raw in (
        {"debris_total": 45_000},
        {"debris": {"pig_iron": 1000, "metal": 2000, "kryptonite": 3000, "spice": 400}},
        {"debris_available": {"pig_iron": 500, "metal": 700}},
        {"debris_pig_iron": 900, "debris_metal": 1200, "debris_kryptonite": 300, "debris_spice": 50},
    ):
        assert cargo.extract_target_debris_resources(raw) == legacy_fleet.extract_target_debris_resources(raw)


def test_flight_timing_bash_tracker_and_mission_banner_parity() -> None:
    researches = {"r4": 8, "r5": 7, "r6": 5}
    for raw_c in ("2:380:4", "02:380:04", "[1/12/9]", "bad"):
        assert flight.normalize_coords(raw_c) == legacy_fleet.normalize_coords(raw_c)

    for a, b in (("01:100:04", "01:100:09"), ("01:100:04", "01:150:08"), ("01:100:04", "03:010:02")):
        assert flight.parse_coords_distance(a, b) == legacy_fleet._parse_coords_distance(a, b)
        assert flight.flight_distance_units(a, b) == legacy_fleet._flight_distance_units(a, b)
        fleet_mix = {"s1": 10, "s4": 25, "s14": 4, "s9": 99}
        assert flight.estimate_fleet_flight_seconds(
            a, b, fleet_mix, researches=researches, round_trip=True
        ) == legacy_fleet.estimate_fleet_flight_seconds(a, b, fleet_mix, researches=researches, round_trip=True)

    for sec in (0, 45, 95, 3600, 5400, 90_000):
        assert flight.format_flight_duration(sec) == legacy_fleet.format_flight_duration(sec)
        assert flight.estimate_resource_per_hour(120_000, sec) == legacy_fleet.estimate_resource_per_hour(120_000, sec)

    spec_str = "[15x s11, 6x s4, 10x s9]"
    assert flight.parse_station_ships_spec(spec_str) == legacy_fleet.parse_station_ships_spec(spec_str)
    assert flight.format_station_ships_spec({"s11": 15, "s4": 6, "s9": 10}) == legacy_fleet.format_station_ships_spec(
        {"s11": 15, "s4": 6, "s9": 10}
    )

    # BashTracker 3/24h active cap, yellow/Spacia exemption, and server block override
    bt_new = flight.BashTracker()
    bt_old = legacy_fleet.BashTracker()
    base_ts = 1_700_000_000.0
    for offset in (0.0, 100.0, 200.0):
        bt_new.record_attack("2:380:4", timestamp=base_ts + offset)
        bt_old.record_attack("2:380:4", timestamp=base_ts + offset)
    assert bt_new.can_attack("02:380:04", timestamp=base_ts + 300.0) is False
    assert bt_new.can_attack("02:380:04", timestamp=base_ts + 300.0) == bt_old.can_attack(
        "02:380:04", timestamp=base_ts + 300.0
    )
    assert bt_new.can_attack("02:380:04", is_inactive=True, timestamp=base_ts + 300.0) is True
    assert bt_new.can_attack("02:380:04", is_spacia=True, timestamp=base_ts + 300.0) is True
    bt_new.record_block("02:380:04", base_ts + 7200.0, reason="Attack (forbidden): Bash")
    bt_old.record_block("02:380:04", base_ts + 7200.0, reason="Attack (forbidden): Bash")
    assert bt_new.can_attack("02:380:04", is_spacia=True, timestamp=base_ts + 300.0) is False
    assert bt_new.can_attack("02:380:04", is_spacia=True, timestamp=base_ts + 300.0) == bt_old.can_attack(
        "02:380:04", is_spacia=True, timestamp=base_ts + 300.0
    )

    from spaceinvasion.parsing.fleet_page import parse_send_fleet_mission_block

    banners = [
        ("<div>Attack (forbidden): You attacked this planet too often!</div><p>Reset in 12:30:00</p>", "ATTACK"),
        ("<p>This player can't be attacked! Target is in vacation mode.</p>", "ATTACK"),
        ("<div>Transport (forbidden): Inactive target</div>", "ATTACK"),
        ("<td>No rubble field at coordinates</td>", "RECYCLE"),
    ]
    for html, mk in banners:
        assert parse_send_fleet_mission_block(html, mk) == legacy_fleet.parse_send_fleet_mission_block(html, mk)


def test_combat_simulator_raid_scoring_and_colony_grading_parity() -> None:
    state = _make_rich_state()
    attacker_ships = {"s4": 40, "s6": 12, "s13": 4, "s14": 6, "s2": 10}
    report = legacy_fleet.SpyReport(
        target_coords="02:188:07",
        owner="Spacia",
        resources=Resources(pig_iron=80_000, metal=40_000, kryptonite=30_000, spice=15_000),
        fleet={"s4": 10, "s5": 3},
        defense={"d1": 20, "d2": 8, "d5": 4},
        dug_in=False,
        is_spacia=True,
    )
    for dug_in in (False, True):
        rep = legacy_fleet.SpyReport(**{**report.__dict__, "dug_in": dug_in})
        new_est = simulator.simulate_raid_outcome(
            attacker_ships, rep, attacker_researches=state.researches, defender_researches={"r8": 4, "r9": 3, "r10": 5}
        )
        old_est = legacy_fleet.simulate_raid_outcome(
            attacker_ships, rep, attacker_researches=state.researches, defender_researches={"r8": 4, "r9": 3, "r10": 5}
        )
        assert new_est.__dict__ == old_est.__dict__

    slot = legacy_fleet.GalaxySlot(
        coords="02:188:07", position=7, owner="Spacia", status_color="yellow", is_spacia=True
    )
    new_rec = simulator.score_raid_target(
        "02:180:08", slot, report, attacker_ships, attacker_researches=state.researches
    )
    old_rec = legacy_fleet.score_raid_target(
        "02:180:08", slot, report, attacker_ships, attacker_researches=state.researches
    )
    assert new_rec is not None and old_rec is not None
    assert new_rec.combat.__dict__ == old_rec.combat.__dict__
    assert new_rec.distance_score == pytest.approx(old_rec.distance_score)
    assert new_rec.resources_per_hour == pytest.approx(old_rec.resources_per_hour)

    assert simulator.simulate_combat_matchup(
        state,
        attacker_ships=attacker_ships,
        defender_ships={"s4": 12},
        defender_defense={"d1": 25, "d2": 10},
        defender_researches={"r8": 5, "r9": 5, "r10": 4},
        target_resources={"pig_iron": 60_000, "metal": 30_000, "kryptonite": 20_000, "spice": 10_000},
    ) == legacy_ranking.simulate_combat_matchup(
        state,
        attacker_ships=attacker_ships,
        defender_ships={"s4": 12},
        defender_defense={"d1": 25, "d2": 10},
        defender_researches={"r8": 5, "r9": 5, "r10": 4},
        target_resources={"pig_iron": 60_000, "metal": 30_000, "kryptonite": 20_000, "spice": 10_000},
    )

    # Colony grading parity across confirmed, range-ecology, and unprobed slots
    grade_cases: list[dict[str, object]] = [
        {"coords": "02:180:09", "origin_coords": "02:180:08", "uninhabited_fields": 330, "spied": True},
        {"coords": "02:190:10", "origin_coords": "02:180:08", "uninhabited_fields": 304, "spied": True},
        {"coords": "02:190:05", "origin_coords": "02:180:08", "uninhabited_fields": 240, "spied": True},
        {"coords": "02:190:02", "origin_coords": "02:180:08", "uninhabited_fields": 150, "spied": True},
        {
            "coords": "02:182:08",
            "origin_coords": "02:180:08",
            "colonize_min_fields": 150,
            "colonize_max_fields": 355,
            "temperature": 24,
        },
        {
            "coords": "02:182:14",
            "origin_coords": "02:180:08",
            "colonize_min_fields": 120,
            "colonize_max_fields": 260,
            "temperature": -45,
        },
        {"coords": "02:185:09", "origin_coords": "02:180:08"},
        {"coords": "02:185:15", "origin_coords": "02:180:08"},
        {"coords": "02:185:02", "origin_coords": "02:180:08"},
    ]
    for kwargs in grade_cases:
        assert colonies.grade_colony_candidate(**kwargs) == legacy_fleet.grade_colony_candidate(**kwargs)  # type: ignore[arg-type]

    cand_slots = (
        legacy_fleet.GalaxySlot(coords="02:180:02", position=2, status_color="free"),
        legacy_fleet.GalaxySlot(coords="02:180:09", position=9, status_color="free"),
        legacy_fleet.GalaxySlot(coords="02:180:15", position=15, status_color="free"),
        legacy_fleet.GalaxySlot(coords="02:180:10", position=10, status_color="yellow"),
    )
    for goal in ("large_fields", "cold_spice"):
        picks = colonies.pick_colony_candidates(cand_slots, goal=goal)
        assert len(picks) == 3
        assert all(p.coords != "02:180:10" for p in picks)


def test_points_ranking_economy_and_eta_parity() -> None:
    state = _make_rich_state()
    for pts in (0, 999, 1_000, 10_000, 128_450, 999_999, 6_000_000_000):
        assert points.attack_zone_for_points(pts) == legacy_ranking.attack_zone_for_points(pts)

    assert points.estimate_account_points(state) == legacy_ranking.estimate_account_points(state)

    history = [
        {"ts": 1_700_000_000.0, "server_points": 120_000, "estimated_points": 119_500.0, "server_rank": 205},
        {"ts": 1_700_014_400.0, "server_points": 128_450, "estimated_points": 128_000.0, "server_rank": 184},
    ]
    assert points.build_ranking_evolution(state, history=history) == legacy_ranking.build_ranking_evolution(
        state, history=history
    )
    assert points.build_combat_overview(state) == legacy_ranking.build_combat_overview(state)

    # Economy & Blast furnace invariant parity
    for planet in state.planets:
        assert eta.estimate_planet_hourly_economy(state, planet) == legacy_ranking.estimate_planet_hourly_economy(
            state, planet
        )
    assert eta.estimate_hourly_economy(state) == legacy_ranking.estimate_hourly_economy(state)

    # Full b1..b21 and r1..r15 ETA table parity
    for planet in state.planets:
        assert eta.build_buildings_eta(state, planet, horizon_count=4) == legacy_ranking.build_buildings_eta(
            state, planet, horizon_count=4
        )

    @dataclass(frozen=True)
    class _DummyAction:
        kind: str
        planet_id: int
        target_id: object
        target_name: str
        target_level: int | None
        cost: Resources
        wait_hours: float | None = None
        wait_seconds: float | None = None
        wait_eta: str | None = None
        duration_seconds: float | None = None
        duration_text: str | None = None
        total_hours: float | None = None
        total_seconds: float | None = None
        total_eta: str | None = None
        eta_bottleneck: str | None = None

    sample_actions: list[object] = [
        {
            "kind": "BUILD",
            "planet_id": 101,
            "target_id": 1,
            "target_level": 19,
            "cost": {"pig_iron": 90_000, "metal": 35_000, "kryptonite": 10_000, "spice": 0},
        },
        {"kind": "DEMOLISH_BUILDING", "planet_id": 101, "target_id": "b6", "target_level": 7},
        {
            "kind": "RESEARCH",
            "planet_id": 101,
            "target_id": "r1",
            "target_level": 13,
            "cost": {"pig_iron": 0, "metal": 40_000, "kryptonite": 30_000, "spice": 15_000},
        },
        {
            "kind": "BUILD_SHIP",
            "planet_id": 101,
            "target_id": "s4",
            "quantity": 10,
            "cost": {"pig_iron": 30_000, "metal": 10_000},
        },
        {
            "kind": "BUILD_DEFENSE",
            "planet_id": 101,
            "target_id": "d2",
            "quantity": 5,
            "cost": {"pig_iron": 15_000, "metal": 5_000},
        },
        {"kind": "ATTACK", "planet_id": 101},
        _DummyAction(
            kind="BUILD",
            planet_id=202,
            target_id=4,
            target_name="Spice mine",
            target_level=16,
            cost=Resources(pig_iron=40_000, metal=20_000, kryptonite=15_000, spice=5_000),
        ),
    ]
    for act in sample_actions:
        assert eta.compute_action_eta(state, act) == legacy_ranking.compute_action_eta(state, act)
    assert eta.attach_action_etas(state, sample_actions) == legacy_ranking.attach_action_etas(state, sample_actions)


def test_combat_and_eta_modules_obey_layering_and_section4_standards() -> None:
    combat_files = [
        REPO_ROOT / "spaceinvasion" / "combat" / name
        for name in (
            "cargo.py",
            "flight.py",
            "simulator.py",
            "colonies.py",
            "points.py",
            "_points_helpers.py",
            "_combat_overview.py",
        )
    ]
    planner_files = [
        REPO_ROOT / "spaceinvasion" / "planner" / name for name in ("eta.py", "_eta_helpers.py", "_eta_tables.py")
    ]
    forbidden_global = ("sqlite3", "psycopg", "sqlalchemy", "bs4", "selenium")
    forbidden_mods = (
        "spaceinvasion.db",
        "spaceinvasion.session",
        "spaceinvasion.runtime",
        "spaceinvasion.ui",
        "spaceinvasion.combat.fleet",
        "spaceinvasion.combat.ranking",
    )

    for path in [*combat_files, *planner_files]:
        src = path.read_text(encoding="utf-8")
        loc = len(src.splitlines())
        assert loc <= 400, f"{path.relative_to(REPO_ROOT)} exceeds 400 LOC ({loc})"
        tree = ast.parse(src)
        is_combat = path.parent.name == "combat"
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.end_lineno:
                assert node.end_lineno - node.lineno + 1 <= 400, f"{path.name}:{node.name} exceeds 400 LOC"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                fn_len = node.end_lineno - node.lineno + 1
                assert fn_len <= 100, f"{path.name}:{node.name} exceeds 100 LOC ({fn_len})"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", f"Forbidden print() in {path.name}"
            if isinstance(node, ast.ExceptHandler):
                typ = node.type
                assert typ is not None, f"Bare except in {path.name}"
                if isinstance(typ, ast.Name):
                    assert typ.id not in ("Exception", "BaseException"), f"Broad except in {path.name}"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr in ("environ", "getenv")), f"os.environ in {path.name}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden_global, f"{path.name} imports forbidden {alias.name}"
                    assert top in sys.stdlib_module_names, f"{path.name} imports non-stdlib {alias.name}"
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in forbidden_global, f"{path.name} imports forbidden {node.module}"
                for bad in forbidden_mods:
                    assert not node.module.startswith(bad), f"{path.name} imports forbidden {node.module}"
                if is_combat:
                    assert not node.module.startswith("spaceinvasion.planner"), f"{path.name} imports planner/"
