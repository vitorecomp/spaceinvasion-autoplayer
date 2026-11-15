# AGENTS.md — Contributor & Agent Guide

Guide for coding agents working in `spaceinvasion-autoplayer`. Read [docs/README.md](docs/README.md), [docs/architecture.md](docs/architecture.md), [docs/specification.md](docs/specification.md), and [docs/specifications/README.md](docs/specifications/README.md) for the full runtime architecture, SRS game mechanics, scaling tables, formulas, and page/click contracts.

## Repository Layout

- **Podman Container Runtime (`podman-compose.yml`, `docker/`, PostgreSQL 16 Only)**:
  - Five containers (`postgres`, `browser` with noVNC `:7900`, `game_api` `:8787`, `commander`, and `frontend` Vite `:5173`), launched via `podman-compose up -d --build`. `docker/Containerfile.backend` builds the React console into `/opt/spaceinvasion-ui` (`SPACEINVASION_UI_DIR=/opt/spaceinvasion-ui`) so bind-mounting `./backend` never hides the built bundle.
  - `bin/` holds container entrypoints (`bin/game_api.py --reload`, `bin/commander.py --loop --reload`, `bin/login.py`).
- **`backend/` — Modular Python Package**:
  - `backend/core/` — Stdlib foundations (`redaction.py`, `coercion.py`, `coords.py`), canonical typed domain models (`model.py`), generated SpaceInvasion Classic catalog (`catalog/`), and single-source settings schema (`settings/` — 50 `StrategyConfig` fields with strict validation & doc/UI schema export).
  - `backend/parsing/` — Pure HTML/DOM parsers for SpaceInvasion Classic pages (`overview`, `buildings`, `research`, `galaxy`, `spy`, `messages`, `fleet_page`, `transmitter`).
  - `backend/combat/` — Zero-rapidfire `1:1` combat simulator (`>=1.4x`), Escape flight packer, Bash tracker (`3/24h`), colony grader (`300+` fields), scan selector, and points calculator.
  - `backend/planner/` — Deterministic `P0`–`P3` rule-based heuristics (`heuristics/`), full-horizon AI-Planner (`ai/`), Config Optimizer (`config_opt/`), Kahn dependency Action Graph DAG (`graph/`), and building ETA calculator (`eta.py`).
  - `backend/session/` — BitMeUp auth (`auth.py`), Chrome `:9222` launcher (`browser.py`), token-bucket page-view limiter (`limiter.py`), modular Selenium client (`browser_client/`), and action execution collaborators (`exec_ops/`).
  - `backend/db/` — PostgreSQL-only persistence built on SQLAlchemy 2.0 ORM (`models/`, `repos/`, `queries/`, `dto/`, `session.py`, `store.py`) and Alembic migrations (`migrations/`, covering all **40 PostgreSQL tables**: 19 Hot + 21 History).
  - `backend/runtime/` — Local HTTP JSON bridge (`api/` command registry, validation, handlers, TypeScript payload codegen; `bridge/` lock-free published snapshot + browser-serialized actions), pure-HTTP commander loop (`commander/`), and subprocess hot-reloader (`reloader.py`).
- **`frontend/` — Modular React + TypeScript + Vite Operator Console**:
  - `frontend/src/` (`store/`, `api/`, `hooks/`, `components/`, `features/`, `styles/`) + `frontend/scripts/prerender.mjs`, built via `npm --prefix frontend run build` into `backend/ui/` (or `/opt/spaceinvasion-ui` in Podman).
- **`scripts/` — Maintenance, Quality Gates & Generators**:
  - `scripts/check.py` — Unified `ruff`, `mypy`, and AST engineering-standards check command.
  - `scripts/generate_docs.py` — Deterministic `--write` / `--check` generator for `docs/api-commands.md` and `docs/settings-reference.md`.
  - `scripts/enable_pg_stats.py` — Enable/report `pg_stat_statements` on PostgreSQL.
  - `scripts/sync_wiki_data.py` — Offline SpaceInvasion Classic Wiki catalog scraper.
- **`docs/` — Unified Documentation Hub**: `architecture.md`, `database-mer.md`, `manual.md`, `api-commands.md` (generated), `settings-reference.md` (generated), `heuristic-strategy.md`, `specification.md`, `specifications/`, `notes/`, `images/`.
- **`notebooks/` — Offline Analytics Notebooks**: `full_planner.ipynb` and `config_optimizer.ipynb` (kept untouched; tests tagged `@pytest.mark.notebooks`).
- **`tests/` — Layered Test Suite**: `tests/invariants/` (tagged `@pytest.mark.invariants`, never deleted), `tests/unit/` (Hermetic + disposable Unix-socket PostgreSQL 16 via `tests/pg_harness.py`), and `tests/e2e/`.

## Operational Rules for Agents

1. **Never bypass anti-bot or auth gates**: Never attempt to solve or click through CAPTCHAs, Turnstile, or missing 2FA codes; auto-pause automation, surface a persistent UI alert (`captcha_gate`), and wait passively (`GET /bot_status`, zero browser calls) until the operator resolves it in Chrome and clicks Resume.
2. **Never race a running Chrome debug session**: When `game_api` is running on `127.0.0.1:8787`, never open a second Selenium client directly against Chrome port `9222`. Query or navigate through `http://127.0.0.1:8787` so the bridge lock serializes browser actions.
3. **Never connect tests to live ports**: Tests must run hermetic (`unshare -r -n` / `podman unshare unshare -n`) against disposable Unix-socket PostgreSQL 16 (`tests/pg_harness.py`) and never touch live ports `8787`, `5433`, `9222`, `7900`, or `5173`.
4. **Keep hot-reload safe**: Always write leaf dependencies (`core/`, `parsing/`, `db/`, `combat/`) **before** editing `planner/`, `session/`, `runtime/`, or `bin/` so `--reload` subprocesses never boot on broken imports.
5. **Never leak secrets, session IDs, or scratch state**:
   - Never read, print, or commit `.env`, `.sessions/`, `.browser_profile/`, `.agents/`, `data/`, or `logs/`.
   - Keep `sid=<token>` redaction active in `backend/core/redaction.py`.
   - Never execute `DELETE FROM` or `DROP TABLE` on PostgreSQL game tables; keep all telemetry, history, and audit ledgers strictly append-only.
6. **Preserve SpaceInvasion Classic invariants**:
   - No rapidfire in combat (`1:1` targeting); *Blast furnace* uses `2:1` Pig iron and is **not** boosted by *Mining technology*; **Escape flights** are invisible to Asteroid *Galaxy scanners*; Bash limit is **3/24h** on active planets and exempt on yellow inactives / *Spacia* (unless overridden by server bash-block banners).
   - Keep non-urgent *Fusion plant* upgrades in `bucket = 4` (after mines) whenever projected net energy stays comfortably positive.
   - Non-building advisories without live actionable DOM links/inputs must keep `executable_now=False`.
7. **Keep generated references synchronized**: Whenever settings (`backend/core/settings/`) or API commands (`backend/runtime/api/`) change, run `.venv/bin/python3 scripts/generate_docs.py --write`.

## Verification Commands

```bash
# Static quality gates (ruff format + lint + mypy + AST standards)
.venv/bin/python3 scripts/check.py --lint-only

# Verify generated docs are up to date
.venv/bin/python3 scripts/generate_docs.py --check

# Build & test React operator console
npm --prefix frontend run build
npm --prefix frontend test

# Run core game invariants & unit tests (hermetic, no live ports)
.venv/bin/pytest -v tests/invariants/ tests/unit/

# Inspect live stack safely (read-only)
curl -s http://127.0.0.1:8787/health
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"plan"}'
```
