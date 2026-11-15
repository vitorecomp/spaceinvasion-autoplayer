# SpaceInvasion Autoplayer — System Architecture Guide

This document describes the modular runtime architecture, package dependency boundaries, Podman container deployment, and safety invariants of `spaceinvasion-autoplayer`.

---

## 1. Runtime Architecture (Podman + PostgreSQL 16)

`spaceinvasion-autoplayer` runs exclusively via `podman-compose up -d` (`podman-compose.yml`), supervising five containers on a shared PostgreSQL 16 instance:

| Component | Details |
|---|---|
| **Launch** | `podman-compose up -d --build` (`podman-compose.yml`) |
| **Containers** | Five containers: `postgres`, `browser` (Chrome + noVNC `:7900`), `game_api` (`:8787`), `commander`, `frontend` (Vite `:5173`) |
| **Database** | PostgreSQL 16 (`postgres` container, persisted in `./data/postgres`) |
| **Operator Console** | Built into `/opt/spaceinvasion-ui` in `docker/Containerfile.backend` (`SPACEINVASION_UI_DIR=/opt/spaceinvasion-ui`, unaffected by `./backend` bind mount) + live Vite dev server on `:5173` |

```mermaid
flowchart TB
  subgraph Tier3["Tier 3 — Clients (Zero Selenium Imports)"]
    CMD["bin/commander.py (--loop --reload)"]
    UI["React Operator Console (:8787 / Vite :5173)"]
    CLI["curl / Operator Tools"]
  end

  subgraph Tier2["Tier 2 — HTTP API & Published Snapshot Bridge (:8787)"]
    API["backend/runtime/api/ (CommandRegistry + 60 @command handlers)"]
    SNAP["Published Immutable Snapshot (Lock-Free Reads)"]
    BRIDGE["backend/runtime/bridge/ (Serialized Browser Lock)"]
    PLAN["backend/planner/ (heuristics, ai, config_opt, graph, eta)"]
    DB["backend/db/ (SQLAlchemy 2.0 ORM + Alembic)"]
  end

  subgraph Tier1["Tier 1 — Stateful Infrastructure"]
    PG["PostgreSQL 16 (40 Tables: 19 Hot + 21 History)"]
    CHROME["Chrome DevTools (:9222 / noVNC :7900)"]
  end

  CMD -->|HTTP JSON| API
  UI -->|HTTP JSON| API
  CLI -->|HTTP JSON| API
  API -->|Read-Only Commands & GET /health /state /dashboard| SNAP
  API -->|needs_browser=True| BRIDGE
  BRIDGE --> PLAN
  BRIDGE --> DB
  API --> DB
  DB --> PG
  BRIDGE -->|GameBrowserClient + ActionExecutor| CHROME
```

---

## 2. Package Architecture & Layering Rules

Dependencies flow strictly downward; the static layering tests enforce these boundaries automatically:

```text
bin/ (container entrypoints: game_api.py, commander.py, login.py)
  └── backend/runtime/ (api/, bridge/, commander/, reloader.py)
        ├── backend/session/ (auth.py, browser.py, limiter.py, browser_client/, exec_ops/)
        ├── backend/planner/ (heuristics/, ai/, config_opt/, graph/, eta.py)
        ├── backend/db/      (models/, repos/, queries/, dto/, session.py, store.py, migrations/)
        ├── backend/combat/  (simulator, cargo, flight, raid, bash, colony, scan, points)
        ├── backend/parsing/ (pure HTML/DOM parsers: overview, buildings, research, galaxy, spy, messages, fleet)
        └── backend/core/    (model.py, catalog/, settings/, redaction.py, coercion.py, coords.py)
```

Key architectural invariants:
- **`backend/core/`** depends only on the Python standard library. It owns `model.py` dataclasses, the generated game catalog (`catalog/`), and the single-source 50-field settings schema (`settings/`).
- **`backend/parsing/`** and **`backend/combat/`** are pure domain logic with zero Selenium, HTTP, or database imports.
- **`backend/session/`** owns browser/frame navigation (`browser_client/`), token-bucket pacing (`limiter.py`), and DOM action execution (`exec_ops/`), with zero `planner`, `combat`, `db`, or `runtime` imports.
- **`backend/db/`** is the sole owner of SQLAlchemy 2.0 and `psycopg` imports. ORM objects never cross out of `db/`; repositories return immutable DTOs from `db/dto/`.
- **`backend/planner/`** provides two autonomous engines (`priority` deterministic P0–P3 heuristics in `planner/heuristics/` and full-horizon `ai_planner` in `planner/ai/`), backed by `planner/config_opt/` (long-term planet/warehouse target optimizer) and `planner/graph/` (Kahn dependency DAG builder).

---

## 3. Concurrency, Locking & Lock-Free Read Path

- **Serialized Browser Lock**: Chrome `:9222` is strictly single-writer. `CommandRegistry` inspects each command's declarative `needs_browser` flag and acquires the browser lock **only** when `needs_browser=True`.
- **Lock-Free Published Snapshots**: After every state change, config patch, or queue mutation, the bridge publishes a fresh immutable snapshot. `GET /health`, `GET /bot_status`, `GET /state`, `GET /dashboard`, and all 33 read-only/DB `POST /command` handlers read directly from the published snapshot or PostgreSQL without ever waiting on an in-flight Selenium page load.
- **Page-View Rate Limiter**: `backend/session/limiter.py` enforces per-minute page-view budgets with reserved interactive capacity so background galaxy sweeps yield cleanly to live commander actions.

---

## 4. Operator Console (`frontend/`)

- Modular React + TypeScript + Vite application (`frontend/src/`) split by domain feature (`dashboard`, `operations`, `buildings`, `research`, `action-graph`, `combat`, `fleet-control`, `sectors`, `espionage`, `colonies`, `attacks`, `recyclers`, `ranking`, `config`, `logs`).
- **Single-source settings UI**: Settings panels render from the schema exported by `backend.core.settings`, eliminating duplicated field bounds and help strings.
- **Typed API payloads**: TypeScript payload contracts are generated from Python payload definitions (`backend/runtime/api/payloads/`).
- **Bundle builds**:
  - **Podman build**: `docker/Containerfile.backend` runs a Node 22 build stage (`npm run build` into `/app/backend/ui`) and copies it into `/opt/spaceinvasion-ui` (`SPACEINVASION_UI_DIR=/opt/spaceinvasion-ui`).
  - **Local UI build**: `npm --prefix frontend run build` writes the static bundle into git-ignored `backend/ui/`.

---

## 5. Hot-Reload & Security Guardrails

- **Hot-reload write order**: Edit leaf packages (`core/`, `parsing/`, `db/`, `combat/`) before editing `planner/`, `session/`, `runtime/`, or `bin/` so `--reload` subprocesses never restart on broken imports.
- **Anti-bot safety (CAPTCHA / Turnstile / 2FA)**: `PageGuardChecker` raises `AntiBotChallengeError` immediately on any challenge page. Automation pauses cleanly, persists a `captcha_gate` UI notification, and waits passively (`GET /bot_status`, zero browser calls) until the operator resolves the gate in noVNC (`:7900`) and clicks Resume.
- **Secret & session redaction**: `backend/core/redaction.py` strips `sid=<token>`, `PHPSESSID`, and credential tokens across logs, diagnostics, and persisted DB text. `.env`, `.sessions/`, `.browser_profile/`, `data/`, and `logs/` remain strictly untracked.
