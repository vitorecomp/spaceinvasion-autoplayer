# SpaceInvasion Autoplayer — Documentation Portal

Welcome to the unified technical documentation suite for **`spaceinvasion-autoplayer`**, a local, safety-first 3-tier automation, full-horizon AI-Planner & Config Optimizer, topological Action Graph (`DAG`), and append-only **PostgreSQL 16** telemetry platform for **SpaceInvasion Classic (BitMeUp)**.

---

## Core Documentation

| Document | Scope |
|---|---|
| **[Architecture Guide](./architecture.md)** | Both runtime modes (Docker/Podman & Host on PostgreSQL 16), modular package boundaries (`core`, `parsing`, `combat`, `planner`, `session`, `db`, `runtime`, `frontend`), lock-free published snapshot reads, Kahn Action Graph DAG, hot-reload order, and anti-bot/security guardrails. |
| **[Database MER & Migrations](./database-mer.md)** | Entity-Relationship Diagram (`erDiagram`), append-only retention model, complete dictionary across all **40 PostgreSQL tables** (19 Hot + 21 History), SQLAlchemy 2.0 ORM (`models/`, `repos/`, `queries/`, `session.py`, `DatabaseStore`), and Alembic migrations (`0001` baseline + Phase 3c `0002`–`0004`). |
| **[Operator & System Manual](./manual.md)** | Setup and daily operations for both Docker (`podman-compose up -d`) and Host (`./starter.sh`) modes, CLI entrypoints, frontend bundle builds, Alembic backup/migration workflow, quality gates (`scripts/check.py`, `scripts/generate_docs.py`), and troubleshooting. |
| **[API Command Reference](./api-commands.md)** | *(Generated)* Complete reference for all **60 `POST /command` endpoints** with browser-lock requirements, mutation flags, parameter schemas, and summaries. |
| **[Strategy Settings Reference](./settings-reference.md)** | *(Generated)* Complete reference for all **50 `StrategyConfig` fields** (types, defaults, bounds, choices, and descriptions) exported from `spaceinvasion.core.settings`. |
| **[Heuristic Strategy Specification](./heuristic-strategy.md)** | Complete deterministic `P0`–`P3` heuristic (`priority`) planner ruleset: `SAVE` escape flights, energy recovery, manual queue Metal frontier & `2:1` `Blast furnace` auto-throttling, `b6` solar tear-down, tutorial solver, mine ROI math, free daily Transmitter guard, and fleet dispatch rules. |
| **[System Specification](./specification.md)** | Core runtime specification, API contracts, safety boundaries, and component design. |

---

## Specifications, Notes & Offline Notebooks

- **Game Mechanics SRS (`docs/specifications/`)**:
  - [Specifications Index](./specifications/README.md)
  - [01 — UI Pages & Click Contracts](./specifications/01-ui-pages-and-clicks.md)
  - [02 — Economy & Scaling Formulas](./specifications/02-economy-and-formulas.md)
  - [03 — Buildings & Research Catalog](./specifications/03-buildings-and-research.md)
  - [04 — Ships, Defense & 1:1 Combat](./specifications/04-ships-defense-and-combat.md)
  - [05 — Fleet Missions & Progression](./specifications/05-fleet-missions-and-progression.md)
- **Engineering Notes (`docs/notes/`)**:
  - [R2 2D Galaxy & Rate-Limit Diagnosis](./notes/r2-galaxy-diagnosis.md)
  - [Adaptive AI-Planner Execution (Plan v2, v3 and v4)](./notes/adaptive-execution-plan-v2.md) — baseline numbers and `scripts/validate_adaptive_execution.py`.
- **Interactive Offline Notebooks (`notebooks/`)**:
  - [full_planner.ipynb](../notebooks/full_planner.ipynb) — Full-game strategy simulation notebook modeling empirical attack loot (`mean`/`std`), mission flight & inter-attack times, attack success rates, colony parameters, tech requirement graph, and `W`-week discounted-reward path search (`notebooks/metadata/full_planner_path.txt`).
  - [config_optimizer.ipynb](../notebooks/config_optimizer.ipynb) — Genetic-algorithm planner for the empire configuration maximizing Imperial starbases per month (`notebooks/metadata/config_optimizer_result.json`).
