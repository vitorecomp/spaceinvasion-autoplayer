# SpaceInvasion Classic Autoplayer

A local, safety-first automation engine, universe observatory, and web operator console for **SpaceInvasion Classic** (`https://spaceinvasion.bitmeup.com/?locale=en`).

![SpaceInvasion Operator Console Dashboard](docs/images/control-ui.png)

## What This Repository Is

`spaceinvasion-autoplayer` automates and analyzes SpaceInvasion Classic gameplay through a decoupled 3-tier architecture running exclusively on **Podman Compose** and **PostgreSQL 16**:

- **Dual Strategy & Planning Engines**:
  - **Deterministic Heuristic Planner (`priority`)**: `P0`–`P3` rule-based priority queue covering `SAVE` escape flights, energy recovery, manual queue reservations, `2:1` Blast furnace auto-throttling, tutorial progression, mine ROI, smart galaxy scanning, espionage, `1:1` combat-simulated raids (`>= 1.4×` margin, `3/24h` bash tracking), debris recycling, and colony grading (`300+` fields).
  - **Full-Horizon AI-Planner & Config Optimizer (`ai_planner` / `config_optimizer`)**: Multi-colony genetic-algorithm empire target optimizer and discounted-reward build-order planner with live adaptive execution and an interactive Kahn topological **Action Graph (`DAG`)**.
- **Append-Only PostgreSQL 16 Observatory**: 40 typed tables (19 Hot + 21 History) managed via **SQLAlchemy 2.0 ORM** and **Alembic** (`0001`–`0004`, with native `JSONB`, foreign keys, `CHECK` constraints, and partial/composite/BRIN indexes) storing universe scans, spy intel, battle/recycle logs, hourly production, loot history, and strategy settings.
- **Lock-Free HTTP Bridge & Declarative API**: 60 `@command` endpoints served from a serialized Chrome controller with lock-free published snapshots (`< 10 ms` `/health`, `~2.5 KB` versioned `/dashboard` deltas) and strict anti-bot/CAPTCHA auto-pause guardrails (never bypasses CAPTCHAs or 2FA).
- **Modular React + TypeScript Operator Console**: 14 live views (Dashboard, Buildings & ETAs, Research & ETAs, Fleet Build, Fleet Control, Scanned Sectors, Espionage Intel, Possible Colonies, Attack Results, Recyclers & Inbox, Operations Queue, Action Graph, Ranking Evolution, and Strategy Config) backed by generated TypeScript types from the Python schema.

```text
[Tier 3] bin/commander.py (--reload) / React Operator Console (:8787 or Vite :5173) / curl
             │  HTTP JSON (GET /health, /bot_status, /state, /dashboard; POST /command)
             ▼
[Tier 2] bin/game_api.py @ 127.0.0.1:8787 (--reload) ──► PostgreSQL 16 (40 tables, Alembic)
             │  Serialized browser lock (GameBrowserClient + ActionExecutor)
             ▼
[Tier 1] Persistent Chromium @ browser:9222 (noVNC @ 127.0.0.1:7900)
```

---

## How to Run (Podman Only)

Podman Compose (`podman-compose.yml`) is the sole supported runtime mode.

### Prerequisites

- **Podman** and **podman-compose** installed on Linux.
- A SpaceInvasion Classic (BitMeUp) account.

### 1. Configure Credentials

```bash
cp .env.example .env
```

Edit `.env` with your BitMeUp login credentials (`SPACEINVASION_EMAIL`, `SPACEINVASION_PASSWORD`, `SPACEINVASION_UNIVERSE`). Runtime strategy settings live in PostgreSQL (`strategy_config_store`) and are edited directly from the **Strategy Config** tab in the console.

### 2. Start the 5-Container Stack

```bash
podman-compose up -d --build
```

| Service | Container | Local Endpoint | Purpose |
|---|---|---|---|
| **Operator Console & API** | `spaceinvasion_api` | `http://127.0.0.1:8787/` | HTTP JSON API + built React console (`/opt/spaceinvasion-ui`) |
| **Frontend Dev (Vite HMR)** | `spaceinvasion_frontend` | `http://127.0.0.1:5173/` | Live-reloading React dev console proxying `/command` & `/dashboard` |
| **Game Browser (noVNC)** | `spaceinvasion_browser` | `http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale` | Persistent Chromium (`:9222` internal) for login & manual CAPTCHA resolution |
| **Commander Loop** | `spaceinvasion_commander` | *(internal HTTP client)* | Autonomous planning & execution loop (`--live --loop --reload`) |
| **PostgreSQL 16** | `spaceinvasion_postgres` | `127.0.0.1:5433` | Append-only database persisted in `./data/postgres` (`pg_stat_statements` enabled) |

### Useful Operations

```bash
# Check container health & follow logs
podman-compose ps
podman-compose logs -f game_api commander

# Verify API health & database schema status
curl -s http://127.0.0.1:8787/health
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"db_verify"}'

# Rebuild only backend containers without restarting browser or PostgreSQL
podman-compose up -d --no-deps --build game_api commander

# Stop the stack (preserves ./data/postgres and ./data/browser_profile)
podman-compose down
```

### Local Development & Quality Gate

All Python runtime and development dependencies are defined in [pyproject.toml](pyproject.toml):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
npm --prefix frontend install

# Run full quality gate (ruff, mypy --strict, AST standards, TS/docs sync, hermetic PG pytest, Vitest, tsc)
podman unshare unshare -n sh -c 'ip link set lo up; exec .venv/bin/python3 scripts/check.py --check-coverage'

# Run offline Jupyter notebook parity tests on demand
podman unshare unshare -n sh -c 'ip link set lo up; exec .venv/bin/pytest -v -m notebooks'
```

---

## Repository Structure

```text
spaceinvasion-autoplayer/
├── podman-compose.yml         # 5-container Podman stack (postgres, browser, game_api, commander, frontend)
├── pyproject.toml             # Python dependencies, ruff, mypy --strict, pytest & coverage config
├── AGENTS.md                  # Contributor & coding-agent operational guardrails
├── docker/                    # Containerfiles (backend builds React UI -> /opt/spaceinvasion-ui), browser entrypoint & PG init
├── bin/                       # Container CLI entrypoints (game_api.py, commander.py, login.py)
├── backend/                   # Modular Python package (strict downward import DAG, <= 400 LOC/module):
│   ├── core/                  #   env.py, redaction.py, coercion.py, coords.py, model.py, catalog/, settings/ (50 fields)
│   ├── parsing/               #   Pure HTML/DOM parsers (overview, buildings, research, galaxy, spy, inbox, fleet, transmitter)
│   ├── combat/                #   1:1 simulator, Escape flight packer, 3/24h Bash tracker, colony grader, smart scanner, points
│   ├── planner/               #   heuristics/, ai/, config_optimizer/, graph/ (Kahn DAG), eta.py
│   ├── session/               #   auth.py, browser.py, limiter.py, client/ (Selenium), executor/ (action ops)
│   ├── db/                    #   SQLAlchemy 2.0 ORM (models/, repos/, queries/, dto/, session.py, store.py) & Alembic migrations/
│   ├── runtime/               #   api/ (60 @command handlers & payloads), bridge/ (lock-free snapshots), commander/, reloader.py
│   └── ui/                    #   Static icons (bell.svg, favicon.svg, favicon.ico); compiled bundle built on demand
├── frontend/                  # Modular React + TypeScript + Vite Operator Console:
│   ├── src/store/             #   Granular store slices, selectors, versioned /dashboard delta merge & reload guard
│   ├── src/types/             #   Generated TypeScript API & settings schema types (from backend Python definitions)
│   ├── src/features/          #   14 feature views (dashboard, buildings, research, combat, fleet-control, sectors, etc.)
│   ├── src/components/        #   Shared layout shell, HUD sparklines, sortable tables, pagination & modals
│   └── scripts/prerender.mjs  #   Zero-flicker SSR shell prerenderer
├── scripts/                   # Quality gates & generators (check.py, check_test_hygiene.py, generate_docs.py,
│                              #   generate_ts_types.py, db_migrate.py, enable_pg_stats.py, sync_wiki_data.py)
├── docs/                      # Complete technical documentation hub (see Documentation section below)
├── notebooks/                 # Offline strategy & optimizer notebooks (full_planner.ipynb, config_optimizer.ipynb)
└── tests/                     # Layered hermetic test suite (disposable --network=none Unix-socket PostgreSQL 16):
    ├── invariants/            #   Core SpaceInvasion Classic rules, sid redaction & architecture layer DAG
    ├── unit/                  #   Package-mirrored unit tests (core, parsing, combat, planner, db, session, runtime, tooling, notebooks)
    ├── integration/           #   HTTP bridge, static UI dir & repository contract tests
    ├── e2e/                   #   Fake-game multi-tick workflow & adversarial tests
    └── goldens/               #   Deterministic parser, planner, graph, API & anonymized live-state regression goldens
```

---

## Documentation

Start at the unified documentation portal in **[docs/README.md](docs/README.md)**:

| Document | Contents |
|---|---|
| **[docs/README.md](docs/README.md)** | Full documentation portal and reading order |
| **[docs/architecture.md](docs/architecture.md)** | 3-tier Podman runtime, strict package DAG, lock-free snapshots, Kahn Action Graph, and safety boundaries |
| **[docs/database-mer.md](docs/database-mer.md)** | 40-table PostgreSQL 16 MER diagram, SQLAlchemy 2.0 ORM architecture, and Alembic revisions (`0001`–`0004`) |
| **[docs/manual.md](docs/manual.md)** | Operator setup, daily workflows, `pg_dump` backups, Alembic migrations, and troubleshooting |
| **[docs/api-commands.md](docs/api-commands.md)** | *(Generated)* All 60 `POST /command` endpoints, parameter schemas, and lock requirements |
| **[docs/settings-reference.md](docs/settings-reference.md)** | *(Generated)* All 50 `StrategyConfig` fields, defaults, bounds, and descriptions |
| **[docs/heuristic-strategy.md](docs/heuristic-strategy.md)** | Deterministic `P0`–`P3` strategy ruleset, Blast furnace `2:1` throttling, and fleet dispatch rules |
| **[docs/specification.md](docs/specification.md)** & **[docs/specifications/](docs/specifications/README.md)** | SpaceInvasion Classic game mechanics SRS, economy scaling formulas, and DOM click contracts |
| **[docs/notes/](docs/notes/)** | Engineering diagnoses and adaptive AI-Planner validation notes |
| **[AGENTS.md](AGENTS.md)** | Operational rules and verification commands for contributors and coding agents |
