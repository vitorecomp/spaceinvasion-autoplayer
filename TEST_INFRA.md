# E2E Test Infra: spaceinvasion-autoplayer

## Test Philosophy
- **Opaque-box, requirement-driven**: Derived strictly from `.agents/teamwork/ORIGINAL_REQUEST.md`, `PROJECT.md § Feature Inventory & § Interface Contracts`, `SPEC.md`, and `specifications/01–05`, with zero dependency on private internal helper functions or unverified implementation details.
- **Methodology**: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial Testing + Real-World Application Workload Testing across 4 sequential tiers:
  1. **Tier 1 (Feature Coverage)**: `>= 5` isolated core tests per requirement feature area (`5–7` per area across `10` areas = `60` tests).
  2. **Tier 2 (Boundary & Corner Cases)**: `>= 5` edge, boundary, negative-input, and failure-mode tests per area (`64` tests across `10` areas).
  3. **Tier 3 (Cross-Feature Combinations)**: Pairwise combinations spanning R1 × R2 × R3 + Telemetry/History (`13` tests).
  4. **Tier 4 (Real-World Application Scenarios)**: Realistic multi-step end-to-end player workflows (`8` tests).
- **Strict No-Weakening & Parallel-Track Resilience Rule**:
  - E2E tests are authored in parallel with Implementation Milestones M1–M4b and MUST strictly assert the exact R1/R2/R3 + follow-up acceptance criteria (never weakened, mocked away, or skipped via `@unittest.skip` to pass against unimplemented stubs).
  - Module top-level imports in `e2e_tests/` MUST import only pre-existing stable modules (`spaceinvasion.{config,state,strategy,game_data,ranking,fleet,api_server,commander_client,storage,client,executor}`, `commander`, stdlib) so `.venv/bin/python3 -m unittest discover -s e2e_tests` ALWAYS collects all tests cleanly with zero import/discovery crashes during M1–M4b.
  - New M1–M4b contracts (`spaceinvasion.optimizer`, `StrategyConfig.planner_mode`, `ga_*`, `PageViewLimiter`, `compute_action_eta`, `PlannedAction` ETA fields, `GameDatabase.get_raid_stats`, `data/attack_history.json`, `notebooks/optimizer_experimentation.ipynb`) are imported/checked inside test bodies (e.g., `strategy_config_from_dict(...)` with immediate `assertTrue(hasattr(cfg, "planner_mode"))`, local `try: from spaceinvasion.optimizer import optimize_plan_actions except ImportError: self.fail(...)`) so unimplemented features fail loudly inside individual test cases rather than crashing discovery.
- **Operational Safety & Isolation**:
  - NEVER read `.env`, `.sessions/`, or `.browser_profile/`.
  - NEVER connect Selenium to Chrome DevTools port `9222` or HTTP requests to live port `8787`.
  - Every HTTP E2E test runs in an isolated `tempfile.TemporaryDirectory()` (`config.yaml`, `state/spaceinvasion.db`, `logs/`, dummy `.env`) with `is_debug_port_open` patched, `create_api_server(bridge, host="127.0.0.1", port=0)` (ephemeral port), `FakeBrowserShell`, and `FakeSpaceInvasionServer`.

## Feature Inventory
Mapped across the 10 user-facing feature areas covering **R1**, **R2**, **R3**, **F19 (`data/attack_history.json`)**, **F20 (`notebooks/optimizer_experimentation.ipynb`)**, and **F21 (GA+NN Calculation Animation HUD)** (`PROJECT.md § Feature Inventory` F1–F21):

| # | Feature Area | Source (Requirement & PROJECT.md) | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---|---|---|---|---|---|
| 1 | **R1.1** Selectable `planner_mode`, Config Roundtrip, API & `"priority"` Regression | `ORIGINAL_REQUEST § R1`, `PROJECT.md` F1, F5, F6, F15 | 6 | 6 | PW-01, PW-03, PW-04, PW-07, PW-10 | T4-01, T4-02, T4-06, T4-07 |
| 2 | **R1.2** Multi-Step Simulation Game Rules, Prerequisites, Queue & Planet Limits | `ORIGINAL_REQUEST § R1`, `specifications/02–05`, `PROJECT.md` F2 | 6 | 6 | PW-03, PW-05, PW-10, PW-12 | T4-02, T4-03, T4-06 |
| 3 | **R1.3** GA + NN Empire Growth Optimization, Composite Score & Determinism | `ORIGINAL_REQUEST § R1`, `PROJECT.md` F3, F4 | 6 | 6 | PW-01, PW-02, PW-05, PW-11 | T4-02, T4-06 |
| 4 | **R1.4** Full Attack/Telemetry History Optimization, DB+`data/attack_history.json` Sync & Notebook | `ORIGINAL_REQUEST § Follow-ups`, `PROJECT.md` F2–F4, F14–F15, F19, F20 | 5 | 5 *(3 in `r1`, 2 in `r2`)* | PW-13 | T4-08 |
| 5 | **R2.1** Universe / Galaxy Opening, Multi-System Nav & System Parsing | `ORIGINAL_REQUEST § R2`, `specifications/01`, `PROJECT.md` F10 | 6 | 7 | PW-08, PW-09 | T4-04, T4-05 |
| 6 | **R2.2** Page-View Rate-Limit Prevention, Cooldown Enforcement & Commander Backoff | `ORIGINAL_REQUEST § R2`, `REQ-UI-006`, `PROJECT.md` F7–F9 | 6 | 7 | PW-03, PW-06, PW-07, PW-12 | T4-03, T4-05 |
| 7 | **R2.3** Exploration Sweep, Spy Intelligence, Attack Dispatch & Bash / `1.4x` Guards | `ORIGINAL_REQUEST § R2`, `specifications/01,04,05`, `PROJECT.md` F11–F12, F19 | 6 | 8 *(+2 F19)* | PW-02, PW-08, PW-09, PW-13 | T4-04, T4-07, T4-08 |
| 8 | **R3.1** Action & Queue ETA Math (`wait`, `duration`, `total`, `bottleneck`, blocked) | `ORIGINAL_REQUEST § R3`, `specifications/02–03`, `PROJECT.md` F13 | 6 | 6 | PW-01, PW-03, PW-05, PW-11, PW-12 | T4-02, T4-03 |
| 9 | **R3.2** Planner & HTTP API ETA Payloads (`plan_actions`, `/command plan`, `/dashboard`) | `ORIGINAL_REQUEST § R3`, `PROJECT.md` F14–F15 | 6 | 6 | PW-01, PW-02, PW-06, PW-07, PW-12, PW-13 | T4-01, T4-02, T4-05, T4-07, T4-08 |
| 10 | **R3.3** Operator Control UI Queue ETA, `#cfg-planner_mode`, GA+NN Calc Animation HUD & Rate-Limit Feedback | `ORIGINAL_REQUEST § R3 & § Follow-up F21`, `PROJECT.md` F16, F21 | 7 | 7 | PW-04, PW-05, PW-06, PW-08, PW-11 | T4-01, T4-02, T4-03, T4-05, T4-07 |
| **Total** | **10 Feature Areas (+ 7 Harness Self-Tests = 152 Tests)** | | **60** | **64** | **13** | **8** |

## Test Architecture

### Test Runner & Invocation
- **Location**: `e2e_tests/` at repository root (strictly isolated from `tests/`).
- **Full Suite Command**:
  ```bash
  .venv/bin/python3 -m unittest discover -s e2e_tests -v
  ```
- **Sequential Tier Commands (for M5 Phase 1 progression)**:
  - Harness Self-Check: `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_harness*.py" -v`
  - Tier 1 (Feature Coverage): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier1*.py" -v`
  - Tier 2 (Boundary & Corner): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier2*.py" -v`
  - Tier 3 (Cross-Feature Pairwise): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier3*.py" -v`
  - Tier 4 (Real-World Application): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier4*.py" -v`
- **Pass/Fail Semantics**: Standard Python `unittest` exit code `0` when all selected tests pass; non-zero on any failure/error. Tests targeting unimplemented M1–M4 features fail cleanly with descriptive `AssertionError` / `self.fail(...)` messages until implemented, while harness self-tests and baseline-contract tests pass immediately.

### Harness & Test Case Design (`e2e_tests/harness.py` & `e2e_tests/fixtures.py`)
1. **`FakeClock`**: Virtual clock driving `FakeSpaceInvasionServer`'s rolling 60-second page-view window and `commander` sleep capture (never globally patching stdlib `time.monotonic` while `ThreadingHTTPServer` runs, avoiding `selectors` timeout corruption).
2. **`FakeSpaceInvasionServer`**: Stateful simulated BitMeUp server modeling:
   - Multi-planet resources, storage, energy, buildings, ships, defenses, researches, tutorial progress, hostile alerts, galaxy systems `[G:S]` with slots `1..16`, spy reports (`internalMessages&type=spio`), launched fleet audit log, and `page_view_log`.
   - Separate `_planets_snapshot_json` so `F_planets` remains stale after actions until explicit `location.reload()` / `location.replace()` (`REQ-UI-004`).
   - Configurable rolling 60-second page-view throttle returning `"You've made more than 30 page views at last minute..."`, optional redirect (`redirect_on_rate_limit=True`), frame-targeted throttling (`F_planets` or `F_Hauptframe`), and anti-bot/session-expired fault injection.
   - Configurable DOM dialects: `galaxy_layout` (`"classic_table"`, `"icon_table"`, `"modern_div"`, `"empty"`), `sid_mode` (`"prefixed"`, `"raw"`, `"empty"`), `p_galaxy_mode` (`"in_frame"`, `"popup_noop"`), and `fleet_form_dialect` (`"standard"`, `"named_fleet"`).
3. **`FakeBrowserShell` (`FakeDriver`, `FakeWebElement`, `FakeSwitchTo`)**:
   - 3-iframe shell (`F_Navi`, `F_planets`, `F_Hauptframe`) backed by `bs4.BeautifulSoup` compatible with `selenium.webdriver.support.ui.Select` and `ActionExecutor`.
   - Per-frame `gen_id` and `window_vars` cleared on navigation, plus `async_nav_polls` to deterministically reproduce stale-DOM / same-action `internalGalaxy -> internalGalaxy` and `internalFleet -> internalSendFleet` transition races.
   - Fault toggles for `ElementClickInterceptedException` (recovered by JS `arguments[0].click()`) and `StaleElementReferenceException`.
4. **`E2ETestHarness`**: Context manager creating isolated `TemporaryDirectory()` (`config.yaml`, `state/spaceinvasion.db`, `logs/`, dummy `.env`), patching `spaceinvasion.api_server.is_debug_port_open`, booting `GameApiBridge` + `create_api_server(..., host="127.0.0.1", port=0)`, and exposing `GameApiClient` (`self.api`) and `self.base_url`.
5. **Operator Control UI Real-JS Runner (`run_ui_in_node`)**:
   - Fetches real `GET /` HTML and `GET /app.js` over loopback HTTP from `E2ETestHarness`.
   - Parses `GET /` with Python `html.parser` for static DOM & `<thead>`/`colSpan` integrity, then runs the real `app.js` inside `node` (`v24.21.0`) `vm` with a lightweight DOM tree (`removeAttribute`, `classList`, `dataset`, `querySelectorAll`, `AbortController`, tracked `setInterval`) and real `fetch` wrapper hitting the ephemeral `ThreadingHTTPServer`.
6. **R1 Fixture Builders & Independent Sequence Evaluator (`e2e_tests/fixtures.py`)**:
   - Uses exact `state.py` constructors (`UnitOffer` for ships/defenses, `BuildingOffer`, `ResearchOffer`, `PlanetState`, `GameState`).
   - Independent test-side forward evaluator (`evaluate_sequence_score`) grounded in `game_data`, `ranking`, and `fleet` (points `1/1000 res`, hourly `SpiceEq`/net income, raid value, `2:1` Blast furnace, energy throttle, prerequisites, field & queue rules) — never importing internal helpers from `optimizer.py`.

### Directory Layout
```text
e2e_tests/
├── __init__.py
├── harness.py               # FakeClock, FakeSpaceInvasionServer, FakeBrowserShell, E2ETestHarness, Node UI runner
├── fixtures.py              # R1/R2/R3 domain state/DOM builders + independent sequence evaluator
├── test_harness_self.py     # Harness, isolation, FakeDriver/Select, FakeServer throttle & Node UI self-tests (7 tests)
├── test_tier1_r1.py         # Tier 1 R1 + F19/F20 feature coverage (23 tests: 18 R1 + 5 R1.4 history/file/notebook)
├── test_tier1_r2.py         # Tier 1 R2 feature coverage (18 tests)
├── test_tier1_r3.py         # Tier 1 R3 + F21 UI GA+NN animation feature coverage (19 tests)
├── test_tier2_r1.py         # Tier 2 R1 + R1.4 (3) boundary & corner tests (21 tests)
├── test_tier2_r2.py         # Tier 2 R2 + F19 (2) boundary & corner tests (24 tests)
├── test_tier2_r3.py         # Tier 2 R3 + F21 UI boundary & corner tests (19 tests)
├── test_tier3_pairwise.py   # Tier 3 pairwise combination suite (13 tests)
└── test_tier4_workflows.py  # Tier 4 real-world application workflows (8 tests)
```

## Real-World Application Scenarios (Tier 4)
| # | ID | Scenario | Features Exercised | Complexity |
|---|---|---|---|---|
| 1 | `T4-01` | Fresh Session Recovery -> Tutorial Progression -> Claim -> Research Unlock | R1.1, R3.2, R3.3, Session/Tutorial | Medium |
| 2 | `T4-02` | Multi-Planet Mid-Game Optimization (`priority` <-> `ga_nn` via Operator Control UI) | R1.1, R1.2, R1.3, R3.1, R3.2, R3.3 | High |
| 3 | `T4-03` | Blast Furnace Starvation, `0%` Pause, Pig Iron Recovery & Hysteresis Resume (`100%`) | R1.2, R2.2, R3.1, R3.3 | High |
| 4 | `T4-04` | Multi-System Universe Sweep, Live Espionage (`s10`), Combat Sim, Raid Dispatch & Bash Cap | R2.1, R2.3, R3.2 | High |
| 5 | `T4-05` | Per-Minute Page-View Throttle Storm & Cooldown Recovery Under Commander + UI | R2.1, R2.2, R3.2, R3.3 | High |
| 6 | `T4-06` | Hostile Incoming Attack Survival (`P0` Escape Flight) During `ga_nn` Mode | R1.1, R1.2, R1.3, R3.1, R3.2 | Medium |
| 7 | `T4-07` | API Server Restart & SQLite WAL / `config.yaml` / UI Hot-Reload Continuity | R1.1, R2.3, R3.2, R3.3 | Medium |
| 8 | `T4-08` | Complete DB + `attack_history.json` Telemetry, History-Driven `ga_nn` Optimization & Notebook Execution | R1.4, R2.3, R3.2 | High |

## Coverage Thresholds
- **Tier 1**: `>= 5` per feature area (`5–7` implemented across each of 10 areas = `60` tests)
- **Tier 2**: `>= 5` per feature area (`5–8` implemented across each of 10 areas = `64` tests)
- **Tier 3**: Pairwise coverage of major R1 × R2 × R3 + Telemetry/History interactions (`13` tests, exceeding `N = 10`)
- **Tier 4**: `>= 5` realistic application scenarios (`8` workflows, exceeding `max(5, 10 // 2) = 5`)
