# E2E Test Suite Ready

## Test Runner
- Command: `.venv/bin/python3 -m unittest discover -s e2e_tests -v`
- Expected: all `152` tests pass with exit code `0` (`0` failures, `0` errors, `0` skips, `0` `ResourceWarning`s) once M3, M4, M4b, and M5 Phase 1 complete.
- Sequential Tier Commands (for M5 Phase 1 tier-by-tier progression):
  - Harness Self-Check (`7` tests): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_harness*.py" -v`
  - Tier 1 — Feature Coverage (`60` tests): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier1*.py" -v`
  - Tier 2 — Boundary & Corner Cases (`64` tests): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier2*.py" -v`
  - Tier 3 — Cross-Feature Pairwise (`13` tests): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier3*.py" -v`
  - Tier 4 — Real-World Application Scenarios (`8` tests): `.venv/bin/python3 -m unittest discover -s e2e_tests -p "test_tier4*.py" -v`
- Pre-M3/M4/M4b Verified Baseline (Wave 5 Gate PASS at `2026-11-15T06:50:00Z`):
  - `test_harness*.py`: `7/7` PASS (`0` `ResourceWarning`s)
  - `test_tier*_r2.py`: `42/42` PASS (`18/18` Tier 1 R2 + `24/24` Tier 2 R2, `0` `ResourceWarning`s, incl. M2's live `4`-view same-planet `ATTACK` path)
  - Full `discover -s e2e_tests -v`: `152` collected (`87` passing, `65` failing strictly on unimplemented M3/M4/M4b deliverables, `0` errors, `0` skips, `0` `ResourceWarning`s)
  - Isolated M3+M4 conforming simulation sweep against real `spaceinvasion/` (`reviewer_e2e_10`): `test_tier1_r3` `19/19 OK`, `test_tier2_r3` `19/19 OK`, `test_tier3_pairwise` `13/13 OK`, `test_tier4_workflows` excl. `T4-08` `7/7 OK`, `test_tier2_r1` excl. F20 `20/20 OK`, `test_tier1_r1` excl. F20 `20/21` (sole expected failure is tracked M1/M3 optimizer raid-seeding item `test_r1_a3_t4` per `PROJECT.md § Non-Blocking M5 Tier 5 Hardening & Downstream Alignment Items`).
- Isolation & Safety Invariants:
  - Runs strictly against `E2ETestHarness` (`tempfile.TemporaryDirectory()`, ephemeral HTTP `127.0.0.1:0`, `FakeClock`, `FakeSpaceInvasionServer`, `FakeBrowserShell`, Node `v24.21.0` `vm` real `app.js` harness).
  - Never reads `.env`, `.sessions/`, or `.browser_profile/`, and never connects to live Chrome `:9222` or live Game API `:8787`.

## Coverage Summary
| Tier | Count | Description |
|------|------:|-------------|
| 0. Harness Self-Check | 7 | `e2e_tests/test_harness_self.py` (`test_self_01`–`test_self_07`, isolation, DOM/CSS, `FakeClock`, `executor_max_limiter_wait=None`/`8.0` rollover & fail-fast across restarts, `__enter__` rollback) |
| 1. Feature Coverage | 60 | `5–7` isolated core tests per area across `10` user-facing feature areas (`test_tier1_r1.py`: 23, `test_tier1_r2.py`: 18, `test_tier1_r3.py`: 19) |
| 2. Boundary & Corner | 64 | `5–8` boundary, edge, invalid-input, and failure-mode tests per area (`test_tier2_r1.py`: 21, `test_tier2_r2.py`: 24, `test_tier2_r3.py`: 19) |
| 3. Cross-Feature | 13 | `e2e_tests/test_tier3_pairwise.py` (`PW-01`–`PW-13` pairwise interactions across R1 × R2 × R3 + Full History + UI) |
| 4. Real-World Application | 8 | `e2e_tests/test_tier4_workflows.py` (`T4-01`–`T4-08` end-to-end player workflows) |
| **Total** | **152** | **`145` across `10` requirement areas + `7` harness self-tests (`0` skips, `0` external `._` couplings in `test_tier*.py`)** |

## Feature Checklist

### 1. Requirement Areas (`TEST_INFRA.md` — R1, R2, R3, F19, F20, F21)
| Feature Area | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---------|:------:|:------:|:------:|:------:|
| **R1.1** Selectable `planner_mode`, Config Roundtrip, API & `"priority"` Regression | 6 | 6 | ✓ (`PW-01,03,04,07,10`) | ✓ (`T4-01,02,06,07`) |
| **R1.2** Multi-Step Simulation Game Rules, Prerequisites, Queue & Planet Limits | 6 | 6 | ✓ (`PW-03,05,10,12`) | ✓ (`T4-02,03,06`) |
| **R1.3** GA + NN Empire Growth Optimization, Composite Score & Determinism | 6 | 6 | ✓ (`PW-01,02,05,11`) | ✓ (`T4-02,06`) |
| **R1.4** Full Attack/Telemetry History Optimization, DB+`data/attack_history.json` Sync & Notebook | 5 | 5 | ✓ (`PW-13`) | ✓ (`T4-08`) |
| **R2.1** Universe / Galaxy Opening, Multi-System Nav & System Parsing | 6 | 7 | ✓ (`PW-08,09`) | ✓ (`T4-04,05`) |
| **R2.2** Page-View Rate-Limit Prevention, Cooldown Enforcement & Commander Backoff | 6 | 7 | ✓ (`PW-03,06,07,12`) | ✓ (`T4-03,05`) |
| **R2.3** Exploration Sweep, Spy Intelligence, Attack Dispatch & Bash / `1.4x` Guards | 6 | 8 | ✓ (`PW-02,08,09,13`) | ✓ (`T4-04,07,08`) |
| **R3.1** Action & Queue ETA Math (`wait`, `duration`, `total`, `bottleneck`, blocked) | 6 | 6 | ✓ (`PW-01,03,05,11,12`) | ✓ (`T4-02,03`) |
| **R3.2** Planner & HTTP API ETA Payloads (`plan_actions`, `/command plan`, `/dashboard`) | 6 | 6 | ✓ (`PW-01,02,06,07,12,13`) | ✓ (`T4-01,02,05,07,08`) |
| **R3.3** Operator Control UI Queue ETA, `#cfg-planner_mode`, GA+NN Calc Animation HUD & Rate-Limit Feedback | 7 | 7 | ✓ (`PW-04,05,06,08,11`) | ✓ (`T4-01,02,03,05,07`) |

### 2. Project Feature Inventory Coverage (`PROJECT.md` F1–F21)
| ID | Feature (`PROJECT.md`) | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---|---|---|---|---|
| F1 | ML Dependency & `.venv` Compatibility | ✓ (`R1.1`, `R1.3`) | ✓ | ✓ | ✓ |
| F2 | Multi-Step Forward Empire Simulator | ✓ (`R1.2`, `R1.4`) | ✓ | ✓ | ✓ (`T4-02,03,06`) |
| F3 | Neural Network Action/State Evaluator | ✓ (`R1.3`, `R1.4`) | ✓ | ✓ | ✓ (`T4-02,08`) |
| F4 | Genetic Algorithm Action Optimizer | ✓ (`R1.3`, `R1.4`) | ✓ | ✓ | ✓ (`T4-02,06,08`) |
| F5 | Selectable `planner_mode` Config | ✓ (`R1.1`) | ✓ | ✓ | ✓ (`T4-01,02,06,07`) |
| F6 | Hot-Reload `WATCH_PATHS` Wiring | ✓ (`R1.1`) | ✓ | ✓ | ✓ (`T4-07`) |
| F7 | R2 Root-Cause Diagnosis Document (`docs/r2-galaxy-diagnosis.md`) | ✓ (`R2.2`) | ✓ | — | — |
| F8 | Shared `PageViewLimiter` & Budget | ✓ (`R2.2`) | ✓ | ✓ (`PW-03,07,12`) | ✓ (`T4-04,05`) |
| F9 | Rate-Limit Cooldown & Burst Reduction | ✓ (`R2.2`) | ✓ | ✓ (`PW-06,07`) | ✓ (`T4-03,05`) |
| F10 | Deterministic `open_galaxy` & Frame Wait | ✓ (`R2.1`) | ✓ | ✓ (`PW-08,09`) | ✓ (`T4-04,05`) |
| F11 | Reliable Exploration, Spy & Attack Dispatch | ✓ (`R2.3`) | ✓ | ✓ (`PW-02,08,09`) | ✓ (`T4-04,07`) |
| F12 | R2 Client/Executor/Fleet Coverage | ✓ (`R2.1–R2.3`) | ✓ | ✓ | ✓ |
| F13 | Action & Queue ETA Engine (`ranking.py`) | ✓ (`R3.1`) | ✓ | ✓ (`PW-01,03,05,11,12`) | ✓ (`T4-02,03`) |
| F14 | `PlannedAction` ETA & `plan_actions` Routing | ✓ (`R1.1`, `R3.2`) | ✓ | ✓ (`PW-01,02,13`) | ✓ (`T4-01,02,06,08`) |
| F15 | API `planner_mode`, ETA & Config Sync | ✓ (`R1.1`, `R1.4`, `R3.2`) | ✓ | ✓ (`PW-02,06,07,13`) | ✓ (`T4-01,02,05,07,08`) |
| F16 | Operator Control UI Queue ETA & Mode UI | ✓ (`R3.3`) | ✓ | ✓ (`PW-04,05,06,08,11`) | ✓ (`T4-01,02,03,05,07`) |
| F17 | Requirement-Driven Opaque-Box E2E Suite | ✓ (`test_harness_self`, `T1–T4`) | ✓ | ✓ | ✓ |
| F18 | Final Milestone Verification Target | ✓ (all 152 tests) | ✓ | ✓ | ✓ |
| F19 | Full History DB + Tracked `data/attack_history.json` | ✓ (`R1.4`) | ✓ (`R1.4`, `R2.3`) | ✓ (`PW-13`) | ✓ (`T4-08`) |
| F20 | Interactive Experimentation Python Notebook (`notebooks/optimizer_experimentation.ipynb`) | ✓ (`R1.4`) | ✓ (`R1.4`) | — | ✓ (`T4-08`) |
| F21 | Layered Radial-Ring GA+NN Calculation HUD (`prefers-reduced-motion`) | ✓ (`R3.3` `T1_R3C_07`) | ✓ (`R3.3` `T2_R3C_07`) | — | ✓ (`T4-02,07`) |
