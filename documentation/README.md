# SpaceInvasion Autoplayer — Documentation Portal

Welcome to the technical documentation suite for **`spaceinvasion-autoplayer`**, a local, safety-first 3-tier automation, evolutionary optimization (`ga_nn`), topological Action Graph (`DAG`), and telemetry study platform for **SpaceInvasion Classic (BitMeUp)**.

---

## Core Documentation

| Document | Scope |
|---|---|
| **[Architecture Guide](./architecture.md)** | 3-tier runtime topology, concurrency/locking model, heuristic (`priority`) vs. evolutionary (`ga_nn`) planners, Kahn Action Graph DAG engine, Web Operator Console, hot-reload order, and anti-bot/security guardrails. |
| **[System & Operator Documentation](./documentation.md)** | Installation, CLI tools (`run_stack.sh`, `run_stack.py`, `game_api.py`, `commander.py`, `login.py`), all `30` `StrategyConfig` knobs, complete HTTP API & `40` `POST /command` reference, notebooks, and verification suites. |
| **[Database MER (Entity-Relationship Model)](./database-mer.md)** | Complete Entity-Relationship Diagram (`erDiagram`), append-only data retention model, logical foreign-key relationships, column-by-column dictionary across all **20 SQLite tables**, JSON payload schemas, and **16 secondary indexes**. |

---

## Related Specifications & Notebooks

- **Game Mechanics SRS (`specifications/`)**:
  - [01 — UI Pages & Click Contracts](../specifications/01-ui-pages-and-clicks.md)
  - [02 — Economy & Scaling Formulas](../specifications/02-economy-and-formulas.md)
  - [03 — Buildings & Research Catalog](../specifications/03-buildings-and-research.md)
  - [04 — Ships, Defense & 1:1 Combat](../specifications/04-ships-defense-and-combat.md)
  - [05 — Fleet Missions & Progression](../specifications/05-fleet-missions-and-progression.md)
- **Interactive Strategy & Optimizer Notebooks (`notebooks/`)**:
  - [optimizer_experimentation.ipynb](../notebooks/optimizer_experimentation.ipynb) — 25-cell laboratory covering read-only telemetry, 46D state encoding, `NeuralValueNetwork` training, `ga_nn` evolution, Action Graph DAGs, solar policy ablations, and hyperparameter sweeps.
  - [full_planner.ipynb](../notebooks/full_planner.ipynb) — Full-game strategy simulation notebook modeling empirical attack loot (`mean`/`std`), mission flight & inter-attack times (`mean`/`std`), and attack success rates (`W/N` + Laplace prior).
