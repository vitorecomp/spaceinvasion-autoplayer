# SpaceInvasion Autoplayer — Documentation Portal

Welcome to the unified technical documentation suite for **`spaceinvasion-autoplayer`**, a local, safety-first 3-tier automation, evolutionary optimization (`ga_nn`), topological Action Graph (`DAG`), and append-only SQLite telemetry platform for **SpaceInvasion Classic (BitMeUp)**.

---

## Core Documentation

| Document | Scope |
|---|---|
| **[Architecture Guide](./architecture.md)** | 3-tier runtime topology, concurrency/locking model, heuristic (`priority`) vs. evolutionary (`ga_nn`) planners, Kahn Action Graph DAG engine, Web Operator Console, hot-reload order, and anti-bot/security guardrails. |
| **[Heuristic Strategy Specification](./heuristic-strategy.md)** | Complete deterministic `P0`–`P3` heuristic (`priority`) planner ruleset: `SAVE` escape flights, energy recovery, manual queue Metal frontier & `2:1` `Blast furnace` auto-throttling, `b6` solar tear-down, tutorial solver, mine ROI math, free daily Transmitter guard, `SPY`/`COLONIZE`/`ATTACK`/`RECYCLE`/`SCAN_GALAXY` dispatch rules, and configuration group cross-reference. |
| **[System Specification](./specification.md)** | Core runtime specification, API contracts, safety boundaries, and component design. |
| **[Operator & System Manual](./manual.md)** | Installation, CLI launchers (`./starter.sh`, `bin/run_stack.py`, `bin/game_api.py`, `bin/commander.py`, `bin/login.py`), `StrategyConfig` reference, HTTP API & `POST /command` catalog, and verification suites. |
| **[Database MER & Migrations](./database-mer.md)** | Entity-Relationship Diagram (`erDiagram`), append-only retention model, column-by-column dictionary across all **27 SQLite tables**, **28 secondary indexes**, and forward-only `V001..V009` schema migrations (`scripts/db_migrate.py`). |
| **[Project Overview & Roadmap](./project.md)** | Milestone history, capability matrix, and current operational status. |

---

## Specifications, Testing & Engineering Notes

- **Game Mechanics SRS (`docs/specifications/`)**:
  - [Specifications Index](./specifications/README.md)
  - [01 — UI Pages & Click Contracts](./specifications/01-ui-pages-and-clicks.md)
  - [02 — Economy & Scaling Formulas](./specifications/02-economy-and-formulas.md)
  - [03 — Buildings & Research Catalog](./specifications/03-buildings-and-research.md)
  - [04 — Ships, Defense & 1:1 Combat](./specifications/04-ships-defense-and-combat.md)
  - [05 — Fleet Missions & Progression](./specifications/05-fleet-missions-and-progression.md)
- **Testing & Quality (`docs/testing/`)**:
  - [Test Infrastructure Guide](./testing/test-infra.md)
  - [E2E Test Readiness Matrix](./testing/test-ready.md)
- **Engineering Notes (`docs/notes/`)**:
  - [R2 2D Galaxy & Rate-Limit Diagnosis](./notes/r2-galaxy-diagnosis.md)
  - [Adaptive AI-Planner Execution (Plan v2 and v3): Intent, What Was Built & How to Validate](./notes/adaptive-execution-plan-v2.md) — baseline numbers and the read-only check `scripts/validate_adaptive_execution.py`.
- **Interactive Strategy & Optimizer Notebooks (`notebooks/`)**:
  - [optimizer_experimentation.ipynb](../notebooks/optimizer_experimentation.ipynb) — Offline laboratory covering SQLite telemetry, 46D state encoding, `NeuralValueNetwork` training, `ga_nn` evolution, Action Graph DAGs, solar policy ablations, and hyperparameter sweeps.
  - [full_planner.ipynb](../notebooks/full_planner.ipynb) — Full-game strategy simulation notebook modeling empirical attack loot (`mean`/`std`), mission flight & inter-attack times (`mean`/`std`), and attack success rates (`W/N` + Laplace prior), plus game-parameter tables for every colony, the technology requirement graph, the planet roster, and the highest research levels known in the universe. Its last section searches for the `W`-week path toward the configuration saved by `config_optimizer.ipynb` that maximizes net weekly-discounted resource production (deducting per-resource spare % for ships/raids and scaling median-attack income dynamically with `Fleet base` `b10` slots) and saves those `W` weeks of actions — with a header listing fleet slots, spare %, and the max level reached of each factor — as `notebooks/metadata/full_planner_path.txt`. Its static descriptions and planner settings are JSON files in [notebooks/metadata/](../notebooks/metadata/).
  - [config_optimizer.ipynb](../notebooks/config_optimizer.ipynb) — Genetic-algorithm planner for the empire configuration that builds the most Imperial starbases per month: meta parameters to tune, warehouses sized by the model, and the build time of every last level. It saves its result as `notebooks/metadata/config_optimizer_result.json`.
