"""Verification suite for Phase 4.4 canonical settings schema and Phase 4.5 TS payload codegen."""

from __future__ import annotations

import ast
import dataclasses
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from backend.core.config import StrategyConfig
from backend.core.settings import (
    ALL_SETTING_SPECS,
    REMOVED_GA_NN_FIELDS,
    SETTING_GROUPS,
    SETTING_SPEC_BY_KEY,
    VALID_PLANNER_MODES,
    SettingsValidationError,
    export_frontend_settings_schema,
    generate_settings_reference_markdown,
    normalize_loaded_settings,
    normalize_planner_mode,
    validate_config_patch,
)
from backend.runtime.api.payloads import PAYLOAD_MODELS
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers_validation import validate_config_patch as _validate_config_patch

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_SCRIPT = REPO_ROOT / "scripts" / "generate_ts_types.py"
FRONTEND_DIR = REPO_ROOT / "frontend"
MAIN_NODE_MODULES = Path(
    "/usr/local/google/home/vieiravitor/workspace/opensource/spaceinvasion-autoplayer/frontend/node_modules"
)


def _parse_frontend_config_schema_items() -> dict[str, dict[str, object]]:
    """Extract non-removed items from `GENERATED_SETTINGS_SCHEMA` consumed by `configSchema.ts`."""
    schema_ts = (FRONTEND_DIR / "src" / "features" / "config" / "configSchema.ts").read_text(encoding="utf-8")
    assert "GENERATED_SETTINGS_SCHEMA.groups.map(toConfigGroupSpec)" in schema_ts

    gen_ts = (FRONTEND_DIR / "src" / "types" / "generated.ts").read_text(encoding="utf-8")
    marker = "export const GENERATED_SETTINGS_SCHEMA: SettingsSchemaPayload = "
    start = gen_ts.index(marker) + len(marker)
    end = gen_ts.index("\n};", start) + 2
    payload = json.loads(gen_ts[start:end])

    items: dict[str, dict[str, object]] = {}
    for grp in payload.get("groups") or []:
        for raw in grp.get("items") or []:
            key = str(raw["key"])
            if key in REMOVED_GA_NN_FIELDS:
                continue
            entry: dict[str, object] = {
                "key": key,
                "control": raw["control"],
                "defaultValue": raw["defaultValue"],
            }
            for num_field in ("min", "max", "step"):
                if num_field in raw and raw[num_field] is not None:
                    entry[num_field] = raw[num_field]
            items[key] = entry
    return items


def test_canonical_settings_cover_strategy_config_and_frontend_schema_1_to_1() -> None:
    cfg_defaults = StrategyConfig()
    cfg_fields = {f.name for f in dataclasses.fields(StrategyConfig)}
    expected_keys = cfg_fields - REMOVED_GA_NN_FIELDS

    assert set(SETTING_SPEC_BY_KEY.keys()) == expected_keys
    assert len(ALL_SETTING_SPECS) == len(expected_keys)
    assert REMOVED_GA_NN_FIELDS == {"ga_horizon_steps", "ga_nn_weight", "ga_seed"}

    for key in sorted(expected_keys):
        spec = SETTING_SPEC_BY_KEY[key]
        cfg_val = getattr(cfg_defaults, key)
        spec_val = spec.default_json_value()
        if isinstance(cfg_val, float):
            assert spec_val == pytest.approx(cfg_val), f"Default mismatch for {key}"
        else:
            assert spec_val == cfg_val, f"Default mismatch for {key}: {spec_val!r} != {cfg_val!r}"

    # Verify 1:1 parity with surviving frontend/src/features/config/configSchema.ts items
    ts_items = _parse_frontend_config_schema_items()
    exported = export_frontend_settings_schema()
    groups = exported["groups"]
    assert isinstance(groups, list)
    assert [g["id"] for g in groups] == [grp.id for grp in SETTING_GROUPS]

    exported_by_key: dict[str, dict[str, object]] = {}
    for grp in groups:
        for item in grp["items"]:
            exported_by_key[str(item["key"])] = item

    assert set(exported_by_key.keys()) == set(ts_items.keys())
    for key, ts_item in ts_items.items():
        py_item = exported_by_key[key]
        assert py_item["control"] == ts_item["control"], f"Control mismatch for {key}"
        assert py_item["defaultValue"] == ts_item["defaultValue"], f"Default mismatch for {key}"
        for bound_field in ("min", "max", "step"):
            if bound_field in ts_item:
                assert py_item.get(bound_field) == pytest.approx(ts_item[bound_field]), (
                    f"Bound {bound_field} mismatch for {key}"
                )

    assert exported["planner_modes"] == list(VALID_PLANNER_MODES)
    assert exported["removed_keys"] == sorted(REMOVED_GA_NN_FIELDS)


def test_validate_config_patch_rejects_malformed_inputs_matching_legacy_api() -> None:
    # Shared rejection matrix tested against both canonical validator and legacy _validate_config_patch
    shared_bad_patches: list[dict[str, object]] = [
        {"max_planets": "١٢"},  # Arabic-Indic Unicode digits
        {"max_planets": "²"},  # Unicode superscript digit
        {"max_planets": True},  # bool-as-int
        {"max_planets": False},
        {"max_planets": 0},  # below min 1
        {"max_planets": 51},  # above max 50
        {"max_planets": 11.5},  # non-integer float
        {"storage_warn_ratio": float("nan")},
        {"storage_warn_ratio": float("inf")},
        {"storage_warn_ratio": -math.inf},
        {"storage_warn_ratio": "nan"},
        {"storage_warn_ratio": "Infinity"},
        {"storage_warn_ratio": "0.5_0"},  # underscore float string
        {"storage_warn_ratio": True},
        {"storage_warn_ratio": 0.05},  # below min 0.1
        {"storage_warn_ratio": 1.0},  # above max 0.99
        {"allow_raids": "true"},  # strict_bools=True rejects string
        {"allow_raids": 1},
        {"planner_mode": ""},
        {"planner_mode": "invalid_mode"},
        {"playstyle": ""},
        {"shipyard_targets": "not-a-dict"},
        {"shipyard_targets": {"": 5}},
        {"shipyard_targets": {"spy-probe": "١٠"}},
        {"shipyard_targets": {"spy-probe": -1}},
        {"fleet_research_targets": {"r3": 101}},
        {"spy_dispatch_planet_id": -1},
        {"spy_dispatch_planet_id": True},
    ]

    for patch in shared_bad_patches:
        with pytest.raises(SettingsValidationError) as exc_info:
            validate_config_patch(patch, strict_bools=True)
        payload = exc_info.value.to_error_payload()
        assert payload["ok"] is False
        assert payload["status_code"] == 400
        with pytest.raises(ApiRequestError):
            _validate_config_patch(patch, strict_bools=True)

    # Canonical validator additionally rejects unknown keys, removed ga_nn fields, and planner_mode='ga_nn'
    for removed_key in sorted(REMOVED_GA_NN_FIELDS):
        with pytest.raises(SettingsValidationError):
            validate_config_patch({removed_key: 1})
    with pytest.raises(SettingsValidationError):
        validate_config_patch({"nonexistent_setting_xyz": 42})
    with pytest.raises(SettingsValidationError):
        validate_config_patch({"planner_mode": "ga_nn"})
    with pytest.raises(SettingsValidationError):
        validate_config_patch({"playstyle": "pirate"})

    # Valid patch coerces cleanly
    good = validate_config_patch(
        {
            "planner_mode": "  AI_PLANNER ",
            "playstyle": "HYBRID",
            "max_planets": "11",
            "storage_warn_ratio": "0.85",
            "allow_raids": True,
            "spy_dispatch_planet_id": "",
            "shipyard_targets": {"spy-probe": 25},
            "fleet_research_targets": {"r4": 8},
        }
    )
    assert good == {
        "planner_mode": "ai_planner",
        "playstyle": "hybrid",
        "max_planets": 11,
        "storage_warn_ratio": 0.85,
        "allow_raids": True,
        "spy_dispatch_planet_id": 0,
        "shipyard_targets": {"spy-probe": 25},
        "fleet_research_targets": {"r4": 8},
    }

    # Load-time normalization maps legacy ga_nn -> priority and drops removed ga_nn keys
    assert normalize_planner_mode("ga_nn", strict=False) == "priority"
    loaded = normalize_loaded_settings(
        {
            "planner_mode": "ga_nn",
            "ga_horizon_steps": 12,
            "ga_nn_weight": 0.5,
            "ga_seed": 42,
            "max_planets": 15,
        }
    )
    assert loaded["planner_mode"] == "priority"
    assert loaded["max_planets"] == 15
    for removed_key in REMOVED_GA_NN_FIELDS:
        assert removed_key not in loaded


def test_settings_markdown_and_ts_codegen_cli_are_deterministic(tmp_path: Path) -> None:
    md_1 = generate_settings_reference_markdown()
    md_2 = generate_settings_reference_markdown()
    assert md_1 == md_2
    for grp in SETTING_GROUPS:
        assert f"## {grp.title}" in md_1
    for spec in ALL_SETTING_SPECS:
        assert f"`{spec.key}`" in md_1

    out_file = tmp_path / "generated_api.ts"
    res_write = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--output", str(out_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_write.returncode == 0, res_write.stderr
    generated_text = out_file.read_text(encoding="utf-8")

    # --check passes on identical output
    res_ok = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--check", str(out_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_ok.returncode == 0, res_ok.stderr

    # --check fails on drift
    out_file.write_text(generated_text + "\n// drifted\n", encoding="utf-8")
    res_drift = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--check", str(out_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_drift.returncode == 1
    assert "out of sync" in res_drift.stderr

    # Verify every export from existing frontend/src/types/api.ts exists in generated output
    existing_api_ts = (FRONTEND_DIR / "src" / "types" / "api.ts").read_text(encoding="utf-8")
    existing_exports = set(re.findall(r"^export\s+(?:type|interface|const)\s+(\w+)", existing_api_ts, re.M))
    generated_exports = set(re.findall(r"^export\s+(?:type|interface|const)\s+(\w+)", generated_text, re.M))
    missing = existing_exports - generated_exports
    assert not missing, f"Generated TS missing exports from api.ts: {sorted(missing)}"
    assert "GENERATED_SETTINGS_SCHEMA" in generated_exports
    assert len(PAYLOAD_MODELS) >= 50


def test_generated_ts_compiles_against_real_react_console_with_tsc(tmp_path: Path) -> None:
    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.exists():
        node_modules = MAIN_NODE_MODULES
    tsc_bin = node_modules / ".bin" / "tsc"
    if not tsc_bin.exists():
        pytest.skip("TypeScript compiler (frontend/node_modules/.bin/tsc) not available")

    tmp_frontend = tmp_path / "frontend"
    tmp_frontend.mkdir(parents=True)
    shutil.copy2(FRONTEND_DIR / "tsconfig.json", tmp_frontend / "tsconfig.json")
    shutil.copytree(FRONTEND_DIR / "src", tmp_frontend / "src")
    (tmp_frontend / "node_modules").symlink_to(node_modules)

    api_ts_target = tmp_frontend / "src" / "types" / "api.ts"
    gen_proc = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--output", str(api_ts_target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert gen_proc.returncode == 0, gen_proc.stderr

    tsc_proc = subprocess.run(
        [str(tsc_bin), "--noEmit", "--project", str(tmp_frontend / "tsconfig.json")],
        cwd=str(tmp_frontend),
        capture_output=True,
        text=True,
        check=False,
    )
    assert tsc_proc.returncode == 0, f"tsc --noEmit failed:\n{tsc_proc.stdout}\n{tsc_proc.stderr}"


def test_settings_and_codegen_modules_obey_layering_and_section4_standards() -> None:
    stdlib = sys.stdlib_module_names
    settings_dir = REPO_ROOT / "backend" / "core" / "settings"
    payloads_dir = REPO_ROOT / "backend" / "runtime" / "api" / "payloads"

    # core/settings must import ONLY stdlib + backend.core.settings
    for path in sorted(settings_dir.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in stdlib, f"{path.name} imports non-stdlib {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                allowed = top in stdlib or node.module.startswith("backend.core.settings")
                assert allowed, f"{path.name} imports forbidden module {node.module}"

    all_new_prod = [*sorted(settings_dir.glob("*.py")), *sorted(payloads_dir.glob("*.py")), GEN_SCRIPT]
    for path in all_new_prod:
        src = path.read_text(encoding="utf-8")
        loc = len(src.splitlines())
        assert loc <= 400, f"{path} exceeds 400 LOC ({loc})"
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.end_lineno:
                assert node.end_lineno - node.lineno + 1 <= 400, f"{path}:{node.name} exceeds 400 LOC"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                assert node.end_lineno - node.lineno + 1 <= 100, f"{path}:{node.name} exceeds 100 LOC"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", f"Forbidden print() in {path}"
            if isinstance(node, ast.ExceptHandler):
                typ = node.type
                assert typ is not None, f"Bare except in {path}"
                if isinstance(typ, ast.Name):
                    assert typ.id not in ("Exception", "BaseException"), f"Broad except in {path}"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr in ("environ", "getenv")), f"os.environ in {path}"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("backend.ui"), f"{path} imports ui/"
                assert not node.module.startswith("selenium"), f"{path} imports selenium"
