"""Parity, invariant, and layering tests for `spaceinvasion.core.catalog` vs `game_data`."""

from __future__ import annotations

import ast
import math
from pathlib import Path
import sys

import pytest

from spaceinvasion.combat.fleet import DEFENSE_SLUG_BY_KEY as FLEET_DEFENSE_SLUGS
from spaceinvasion.combat.fleet import SHIP_SLUG_BY_KEY as FLEET_SHIP_SLUGS
from spaceinvasion.core import catalog, game_data
from spaceinvasion.core.state import Resources

CATALOG_DIR = Path(__file__).resolve().parents[3] / "spaceinvasion" / "core" / "catalog"


def test_catalog_data_tables_and_constants_match_game_data_exactly() -> None:
    assert catalog.WIKI_SYNCED_AT_UTC == game_data.WIKI_SYNCED_AT_UTC
    assert catalog.WIKI_SOURCE_URL == game_data.WIKI_SOURCE_URL
    assert catalog.MINING_TECH_BONUS_PER_LEVEL == game_data.MINING_TECH_BONUS_PER_LEVEL
    assert catalog.MERCHANT_RATES_IN_SPICE == game_data.MERCHANT_RATES_IN_SPICE
    assert catalog.MAX_JSON_SAFE_INT == game_data.MAX_JSON_SAFE_INT

    assert catalog.BUILDINGS == game_data.BUILDINGS
    assert catalog.RESEARCHES == game_data.RESEARCHES
    assert catalog.SHIPS == game_data.SHIPS
    assert catalog.DEFENSES == game_data.DEFENSES
    assert catalog.BUILDING_BY_ID == game_data.BUILDING_BY_ID
    assert catalog.BUILDING_LEVELS == game_data.BUILDING_LEVELS
    assert catalog.RESEARCH_BY_ID == game_data.RESEARCH_BY_ID
    assert catalog.RESEARCH_LEVELS == game_data.RESEARCH_LEVELS


def test_ship_and_defense_slug_invariants() -> None:
    assert catalog.SHIP_SLUG_BY_KEY == FLEET_SHIP_SLUGS
    assert catalog.DEFENSE_SLUG_BY_KEY == FLEET_DEFENSE_SLUGS
    for skey, slug in catalog.SHIP_SLUG_BY_KEY.items():
        assert slug in catalog.SHIPS
        assert catalog.resolve_ship_slug(skey) == slug
        assert catalog.resolve_ship_slug(slug) == slug
        resolved = catalog.resolve_unit("ships", skey)
        assert resolved is not None
        assert resolved["slug"] == slug
    assert catalog.resolve_ship_slug("s16") == "imperial-starbase"
    assert catalog.resolve_ship_slug("Imperial Starbase") == "imperial-starbase"
    assert catalog.resolve_ship_slug("") is None


def test_building_level_curves_and_economy_formulas_match_game_data() -> None:
    for bid in range(1, 7):
        assert bid in catalog.BUILDING_BY_ID
        for lvl in (0, 1, 5, 12, 25):
            assert catalog.building_level_stats(bid, lvl) == game_data.building_level_stats(bid, lvl)
            assert catalog.building_cost(bid, lvl) == game_data.building_cost(bid, lvl)
            assert catalog.mine_energy_needed(bid, lvl) == game_data.mine_energy_needed(bid, lvl)
            assert catalog.building_energy_delta(bid, lvl) == game_data.building_energy_delta(bid, lvl)

    # Mine production parity including temperature curve & Blast furnace Mining-tech invariant
    for bid in (1, 2, 3, 4):
        for temp in (-60, 0, 45):
            for tech in (0, 10, 25):
                got = catalog.mine_production_per_hour(bid, 15, temperature=temp, mining_tech_level=tech)
                exp = game_data.mine_production_per_hour(bid, 15, temperature=temp, mining_tech_level=tech)
                assert got == pytest.approx(exp)

    # SpaceInvasion Classic invariant: Blast furnace (b2) is NOT boosted by Mining technology
    assert catalog.mine_production_per_hour(2, 15, mining_tech_level=0) == catalog.mine_production_per_hour(
        2, 15, mining_tech_level=30
    )
    assert catalog.blast_furnace_pig_iron_used(15) == game_data.blast_furnace_pig_iron_used(15)
    assert catalog.blast_furnace_pig_iron_used(15) == pytest.approx(2.0 * catalog.mine_production_per_hour(2, 15))

    temp_curve = {-40: 120, 0: 100, 40: 80}
    for temp_val in (-100, -40, -15.5, 0, 20, 40, 120):
        assert catalog.interpolate_temp_value(temp_curve, temp_val) == pytest.approx(
            game_data.interpolate_temp_value(temp_curve, temp_val)
        )


def test_lookup_and_prerequisite_parity() -> None:
    identifiers: list[object] = [
        1,
        "b1",
        "pig-iron-mine",
        "Pig Iron Mine",
        "blast-furnace",
        "r1",
        "defence-systems",
        "particle-analyser",
        "imperial-starbase",
        b"b2",
        None,
        True,
        "nonexistent-unit",
    ]
    for ident in identifiers:
        assert catalog.resolve_unit(None, ident) == game_data.resolve_unit(None, ident)  # type: ignore[arg-type]
        assert catalog.resolve_unit("buildings", ident) == game_data.resolve_unit("buildings", ident)  # type: ignore[arg-type]

    for raw in ("Defence-Systems", "particle_analyser", "  Blast-Furnace  "):
        assert catalog.normalize_unit_key(raw) == game_data.normalize_unit_key(raw)

    buildings = {"b1": 10, "b7": 8, "b8": 4, "b9": 6}
    researches = {"r1": 8, "r2": 4, "r3": 6}
    for cat_name, unit_id in (
        ("buildings", "microsystem-accelerator"),
        ("research", "warp-engine"),
        ("ships", "imperial-starbase"),
        ("defense", "heavy-graviton-cannon"),
    ):
        assert catalog.missing_prerequisites(
            cat_name, unit_id, buildings=buildings, researches=researches
        ) == game_data.missing_prerequisites(cat_name, unit_id, buildings=buildings, researches=researches)


def test_coercion_and_json_sanitization_parity() -> None:
    samples: list[object] = [
        None,
        True,
        False,
        0,
        42,
        -19,
        "123",
        " 45.7 ",
        "١٢٣",
        "nan",
        "inf",
        float("nan"),
        float("inf"),
        10**250,
        b"77",
        {"bad": 1},
        [1, 2],
    ]
    for val in samples:
        assert catalog.safe_int(val, -1) == game_data.safe_int(val, -1)
        assert catalog.safe_optional_int(val, None) == game_data.safe_optional_int(val, None)
        assert catalog.safe_finite_float(val, -2.5) == pytest.approx(game_data.safe_finite_float(val, -2.5))
        assert catalog.cap_json_int(val) == game_data.cap_json_int(val)

    for num, den in ((100, 4), (10**400, 10**395), (10**500, 1), (-10, 2), (5, 0), (True, 2)):
        assert catalog.safe_big_ratio(num, den) == pytest.approx(game_data.safe_big_ratio(num, den))
        assert catalog.safe_big_ratio(num, den, allow_negative=True) == pytest.approx(
            game_data.safe_big_ratio(num, den, allow_negative=True)
        )

    for raw, scale in ((100, 1.5), (10**350, 0.25), (0, 2.0), (-5, 1.0), (50, float("nan"))):
        assert catalog.safe_scale_round_int(raw, scale) == game_data.safe_scale_round_int(raw, scale)

    for v in (0, 12345, -98765, 10**4000):
        assert catalog.fmt_compact_int(v, signed=True, grouped=True) == game_data.fmt_compact_int(
            v, signed=True, grouped=True
        )

    # Spice equivalent with both Mapping and Resources dataclass
    res_dict = {"pig_iron": 600, "metal": 250, "kryptonite": 300, "spice": 100}
    res_obj = Resources(pig_iron=600, metal=250, kryptonite=300, spice=100)
    assert catalog.spice_equivalent(res_dict) == pytest.approx(game_data.spice_equivalent(res_dict))
    assert catalog.spice_equivalent(res_obj) == pytest.approx(game_data.spice_equivalent(res_obj))

    # Cycle-safe JSON tree sanitization parity
    cyclic: dict[object, object] = {
        1: [1, math.nan, math.inf, 10**250, b"ok"],
        ("a", 2): KeyError("secret"),
    }
    cyclic["self"] = cyclic
    assert catalog.sanitize_json_tree(cyclic) == game_data.sanitize_json_tree(cyclic)
    assert catalog.safe_key((1, "x", frozenset({"b", "a"}))) == game_data.safe_key((1, "x", frozenset({"b", "a"})))


def test_catalog_imports_stdlib_only_and_meets_size_standards() -> None:
    stdlib = sys.stdlib_module_names
    for path in sorted(CATALOG_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in stdlib, f"{path.name} imports non-stdlib module {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                allowed = (
                    top in stdlib
                    or node.module.startswith("spaceinvasion.core.catalog")
                    or node.module == "spaceinvasion.core.coercion"
                )
                assert allowed, f"{path.name} imports forbidden module {node.module}"
        line_count = len(src.splitlines())
        assert line_count <= 400, f"Catalog module {path.name} exceeds 400 LOC ({line_count})"
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                fn_len = node.end_lineno - node.lineno + 1
                assert fn_len <= 100, f"{path.name}:{node.name} exceeds 100 LOC ({fn_len})"
