# Project: SpaceInvasion Operator Control UI — Operational Action Graph & Parameter Simulation (`run2`)

## Architecture
- **Action Graph & Config Override Engine (`spaceinvasion/action_graph.py`, `spaceinvasion/optimizer.py`)**:
  - Pure, Selenium-free module `spaceinvasion/action_graph.py` depending only on `config.py`, `game_data.py`, `state.py`, `strategy.py`, `optimizer.py`, and `ranking.py`.
  - `merge_strategy_config_overrides(base, overrides, *, planner_mode_override=None) -> tuple[StrategyConfig, list[str]]`: constructs an ephemeral `StrategyConfig` in memory (shallow-merging `shipyard_targets`) and computes `changed_keys` relative to active `base` without mutating `base` or touching disk.
  - `build_action_graph(state, actions, *, effective_config, active_config=None, simulated=False, optimizer_summary=None, planet_filter=None) -> dict[str, Any]`: builds a deterministic, acyclic multi-lane DAG (`lanes`, `nodes` with topological column layers and Wait/Total ETAs, directed `edges` across `priority_gate`, `level_chain`, `prerequisite`, `energy_support`, `throttle_control`, `resource_feed`, `loot_feed`, `storage_cap`, `queue_order`, and `ga_step_order`/`execution_order`, `summary`, `active_config`, `effective_config`, `changed_keys`, `is_dirty`, `simulated`, `mode`, `ga_summary`).
  - `spaceinvasion/optimizer.py`: extends `OptimizerRunSummary.to_dict()` with `best_simulation.step_outcomes` and adds `record_global_summary: bool = True` to `run_optimizer` (and `_RUN_OPTIMIZER_KWARGS`) so isolated `"ga_nn"` simulations never pollute `_LAST_OPTIMIZER_SUMMARY`.
- **Thread-Locked HTTP JSON Bridge (`spaceinvasion/api_server.py`, `game_api.py`)**:
  - `POST /command {"command": "action_graph", ...}`: validates `planner_mode` and `config` overrides via hardened `_validate_config_patch`, runs `_plan_with_summary(..., record_summary=not is_simulated)` against cached `self._last_state` when `refresh=False` (`0` browser page views, rate-limit exempt, zero mutation of `self.strategy_config`, `self.config_path`, executors, `self._last_optimizer_summary`, or `_LAST_OPTIMIZER_SUMMARY`), and returns `{"ok": True, "command": "action_graph", "action_graph": graph}`.
  - `POST /command {"command": "update_config", "config": ..., "include_action_graph": True}`: validates, shallow-merges `shipyard_targets`, updates active `self.strategy_config` in memory, persists to `self.config_path` (`config.yaml`) via `save_strategy_config`, syncs `_live_executor` and `_dry_executor`, and optionally returns the freshly persisted `action_graph` (`simulated=False`, `is_dirty=False`).
  - `GET /dashboard`: attaches active `action_graph` from the already-computed `(actions, opt_summary)` in `< 1ms` (`dashboard(refresh=False)` remains well under `< 25.0ms`).
  - `game_api.py`: registers `Path("spaceinvasion/action_graph.py")` in `WATCH_PATHS` after module creation.
- **Operator Control UI (`spaceinvasion/ui/index.html`, `spaceinvasion/ui/styles.css`, `spaceinvasion/ui/app.js`)**:
  - Adds `[data-page="action-graph"]` sidebar navigation link (`DAG` / `Action Graph` / `#nav-graph-badge`), quick-jump `[data-goto-page="action-graph"]` buttons, and `<section id="view-action-graph" class="page-view ag-view" hidden>`.
  - Two direct sibling panes inside `section#view-action-graph.page-view`:
    1. `<article id="ag-main-window">`: mode toggle (`"priority"` vs `"ga_nn"`), live vs preview status pill, summary KPI strip (strictly avoiding the word `"generation"` inside `#ag-main-window` to preserve `findGaNnHudPanel()`), edge legend & filter chips, deterministic coordinate-positioned `<button class="ag-node-card">` cards over dynamically `createElementNS`-mounted `<svg id="ag-edges-svg">` Bezier edges (preserving static single-SVG invariant), and node detail inspector.
    2. `<aside id="ag-control-panel">`: right-side parameter simulation & apply panel (`Mode & Tuning` + `Automation & Limits` subtabs covering all planner/GA/economy/automation fields), isolated preview simulation (`Simulate Graph` + auto-recalc on mode/input change, immune to 5s `/dashboard` polling), `Reset to Active`, and explicit `Apply to Active Config` (`update_config`).
  - 100% English strings, `>= 0.86rem` font-size floor, `impeccable audit` `20.0/20`, full compatibility with both Node `vm` harnesses (`tests/test_ui.py` and `e2e_tests/harness.py`).

## Feature Inventory
Every Survey feature assigned a milestone (none unassigned).

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Action Graph DAG & Optimizer Isolation Engine | `spaceinvasion/action_graph.py` (`merge_strategy_config_overrides`, `build_action_graph` with multi-lane topological nodes & typed causal/sequence edges) + `optimizer.py` (`step_outcomes` in `to_dict()`, `record_global_summary=False` isolation) | M1 | survey_1, survey_3 |
| F2 | Isolated `action_graph` Simulation & Explicit `update_config` API | `api_server.py` `action_graph` command (0 page views when `refresh=False`, 0 memory/disk/summary mutation), hardened `_validate_config_patch`, `shipyard_targets` shallow merge & `include_action_graph` on `update_config`, `<1ms` `/dashboard` integration, `game_api.py` `WATCH_PATHS` | M1 | survey_1, survey_3 |
| F3 | Operational Action Graph Tab, Canvas & Mode Switcher | `ui/{index.html,styles.css,app.js}` `[data-page="action-graph"]` view, `"priority"` <-> `"ga_nn"` switcher, dynamic `<svg id="ag-edges-svg">` Bezier edges + HTML `<button>` node cards, KPI strip, edge filter, node inspector, empty state, `#gann-calc-hud` refcount integration | M1 | survey_2, survey_3 |
| F4 | Right-Side Parameter Simulation & Explicit Apply Panel | Right-side `<aside id="ag-control-panel">` with mode/GA/heuristic/automation controls, isolated preview recalculation immune to 5s `/dashboard` polling, `Reset to Active`, and explicit `Apply to Active Config` persistence | M1 | survey_1, survey_2, survey_3 |
| F5 | UI Craft, Node-VM Harness Invariants & English-Only Compliance | `impeccable` `20.0/20` (`>=0.86rem`, existing `1100px`/`680px`/`prefers-reduced-motion` blocks), static single-SVG & `findGaNnHudPanel()` invariants, dual Node-VM DOM compatibility, 100% English UI/code/logs/API | M1 | survey_2, survey_3, follow-up |
| F6 | Automated Unit, UI & E2E Verification + R3 Live/Git Safety | Comprehensive tests in `tests/test_action_graph.py`, `tests/test_api_and_commander.py`, `tests/test_ui.py`, and `e2e_tests/test_action_graph_e2e.py`; `100%` pass across all `tests/` (`395+`) and `e2e_tests/` (`181+`); zero `:9222` access; clean git index with zero forbidden files staged/committed | M1 | survey_1, survey_2, survey_3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | `M1-action-graph-and-simulation-panel` | Single-cycle (2B) full delivery of F1–F6: backend Action Graph engine, isolated simulation & apply API, Operator Control UI Action Graph screen + right-side parameter panel, unit/UI/E2E tests, and R3 safety verification | none | DONE (`428/428` `tests/` PASS, `185/185` `e2e_tests/` PASS, `9.36–14.32ms` 5P warm `/dashboard`, `20.0/20` Impeccable, Gate 4 `5/5` PASS: `auditor_ag_r4` CLEAN + `reviewer_ag_r4_1/2` & `challenger_ag_r4_1/2` APPROVE) |

## Interface Contracts

### `action_graph.py` ↔ `optimizer.py` ↔ `api_server.py` ↔ `ui/app.js`
- `POST /command` with `{"command": "action_graph", "planner_mode": Optional[str], "config": Optional[dict], "simulated": Optional[bool], "planet_id": Optional[int], "refresh": bool = False}`:
  - Validates `planner_mode` (`"priority"` | `"ga_nn"`) and `config` fields strictly (HTTP `400` on invalid types/ranges/non-finite numbers).
  - Returns `{"ok": True, "command": "action_graph", "action_graph": { "mode", "simulated", "state_available", "is_dirty", "changed_keys", "active_config", "effective_config", "lanes", "nodes", "edges", "summary", "ga_summary" }}`.
  - **Strict Isolation**: When `simulated=True` (default whenever `planner_mode` or `config` override is provided), `bridge.strategy_config`, `bridge.config_path`, `bridge._live_executor`, `bridge._dry_executor`, `bridge._last_optimizer_summary`, and `optimizer._LAST_OPTIMIZER_SUMMARY` MUST NOT be mutated, and `refresh=False` MUST NOT open or navigate any browser page.
- `POST /command` with `{"command": "update_config", "config": dict, "include_action_graph": Optional[bool]}`:
  - Validates `config`, shallow-merges partial `shipyard_targets`, updates `bridge.strategy_config`, writes `bridge.config_path` via `save_strategy_config`, syncs `_live_executor` & `_dry_executor`, and when `include_action_graph=True` includes `"action_graph"` (`simulated=False`, `is_dirty=False`).
- **UI DOM & Harness Contracts**:
  - Static `spaceinvasion/ui/index.html` keeps `non_chart_svgs == 1` (`#gann-radial-svg` only); `<svg id="ag-edges-svg">` is created dynamically via `document.createElementNS`.
  - `<article id="ag-main-window">` and `<aside id="ag-control-panel">` are direct children of `<section id="view-action-graph" class="page-view ag-view" hidden>`, and `#ag-main-window` never contains the substring `"generation"`.
  - All JSON serialization in Python production files passes `allow_nan=False`.
  - `spaceinvasion/config.py` maintains zero internal `spaceinvasion.*` imports.
  - All UI copy, error messages, code identifiers, comments, and logs are 100% English.

## Code Layout
- **Hot-Reload Safe Write Order (`AGENTS.md` Rule 3)**:
  1. `spaceinvasion/action_graph.py` (new)
  2. `spaceinvasion/optimizer.py`
  3. `spaceinvasion/api_server.py`
  4. `game_api.py`
  5. `spaceinvasion/ui/index.html`, `spaceinvasion/ui/styles.css`, `spaceinvasion/ui/app.js`
  6. `tests/test_action_graph.py` (new), `tests/test_api_and_commander.py`, `tests/test_ui.py`, `e2e_tests/test_action_graph_e2e.py` (new)
- **Repository Hygiene (R3)**:
  - Never read `.env`, `.sessions/`, or `.browser_profile/`.
  - Never connect directly to Chrome port `127.0.0.1:9222`.
  - Never mutate workspace-root `config.yaml`, `state/`, or `logs/` from tests (always pass `TemporaryDirectory` paths + `port=0`).
  - Never stage or commit `state/`, `logs/`, `config.yaml`, `.env`, `.sessions/`, `.browser_profile/`, `PROJECT.md`, or `.agents/`.
