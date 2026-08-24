# AGENTS.md — Contributor & Agent Guide

Guide for coding agents working in `spaceinvasion-autoplayer`. Read [docs/README.md](docs/README.md), [docs/specification.md](docs/specification.md), and [docs/specifications/README.md](docs/specifications/README.md) for the full runtime architecture, SRS game mechanics, scaling tables, formulas, and page/click contracts.

## Repository Layout

- **`starter.sh` & `bin/` — 3-Tier Live Runtime & CLI Launchers**:
  - `starter.sh` / `bin/run_stack.py` — Root supervisor script (`./starter.sh`) and Python stack launcher supervising Chrome debug port, `bin/game_api.py --reload`, and `bin/commander.py --loop --reload` with automatic crash recovery and CAPTCHA exit-code-2 stop safety.
  - `bin/login.py` + `spaceinvasion/browser.py` / `spaceinvasion/auth.py` — BitMeUp login, `.env` credential profiles, persistent Chrome profile (`.browser_profile/`), and detached Chrome DevTools debug port (`127.0.0.1:9222`).
  - `bin/game_api.py` + `spaceinvasion/api_server.py` — Thread-locked local HTTP JSON bridge (`http://127.0.0.1:8787`) serving the Web Operator Console (`spaceinvasion/ui/`) and `GET /health`, `GET /ui-version`, `GET /state`, `GET /dashboard`, and `POST /command`.
  - `bin/commander.py` + `spaceinvasion/commander_client.py` — Pure-HTTP autonomous decision loop with zero Selenium imports.
  - `bin/advisor.py` / `bin/run_bot.py` — Read-only tactical advisor CLI and single-process runner.
- **`spaceinvasion/` — Core Python Package**:
  - `spaceinvasion/game_data.py` — Offline catalog scraped from the SpaceInvasion Classic Wiki (21 buildings, 16 researches, 15 ships, 11 defenses, Merchant parity `6:2.5:3:1`, temp curves, prerequisites).
  - `spaceinvasion/state.py` — Typed `GameState` / `PlanetState` / offer dataclasses and HTML/JSON parsers (`#json-container`, `internalBuildings`, `internalResearch`, `internalWeaponFactory`, `internalHome`, `F_Navi`).
  - `spaceinvasion/config.py` / `config.example.yaml` — Non-secret `StrategyConfig` (`config.yaml`, optional).
  - `spaceinvasion/strategy.py` / `spaceinvasion/optimizer.py` / `spaceinvasion/action_graph.py` — Priority-sorted `P0`–`P3` heuristic planner, GA + Neural Network (`ga_nn`) rollout optimizer, manual command queue with queue-position Metal production review, and Kahn DAG builder.
  - `spaceinvasion/executor.py` / `spaceinvasion/fleet.py` — Safe Selenium click/form executor, Escape flight packer, `1:1` zero-rapidfire combat simulator (`>=1.4x`), Bash tracker (`3/24h`), Galaxy/Spy/Inbox parsers, and `300+`-field colony grader.
  - `spaceinvasion/migrations.py` / `spaceinvasion/storage.py` — Versioned forward-only SQLite migration engine (`V001..V006`, SHA-256 checksums, online `.backup()` snapshots) and append-only WAL database (`state/spaceinvasion.db`).
  - `spaceinvasion/ui/` — Web Operator Console (`index.html`, `styles.css`, `app.js`).
- **`scripts/` — Maintenance & Admin Utilities**:
  - `scripts/db_migrate.py` — Database versioning, migration, online backup, and integrity verification CLI.
  - `scripts/sync_wiki_data.py` — Offline SpaceInvasion Classic Wiki catalog scraper.
- **`docs/` — Unified Documentation Hub** (`architecture.md`, `specification.md`, `manual.md`, `database-mer.md`, `project.md`, `specifications/`, `testing/`, `notes/`, `images/`).
- **`notebooks/` — Offline Analytics & GA+NN Experimentation** (`optimizer_experimentation.ipynb`, `full_planner.ipynb`).
- **`tests/` — Unified Test Suite** (`tests/unit/` unit tests + `tests/e2e/` 4-tier end-to-end & adversarial tests).

## Operational Rules for Agents

1. **Never bypass anti-bot or auth gates**: Abort on CAPTCHAs, Turnstile, or missing 2FA codes and let the user handle them in Chrome.
2. **Never race a running Chrome debug session**: When `bin/run_stack.py` or `bin/game_api.py` is running on `127.0.0.1:8787`, never open a second Selenium client directly against Chrome port `9222`. Query or navigate through `http://127.0.0.1:8787` so `GameApiBridge._lock` serializes browser actions.
3. **Keep hot-reload safe**: Always write/generate dependencies (`spaceinvasion/game_data.py`, `spaceinvasion/migrations.py`, `spaceinvasion/state.py`, `spaceinvasion/fleet.py`, `spaceinvasion/storage.py`) **before** editing `spaceinvasion/strategy.py`, `spaceinvasion/api_server.py`, or `bin/commander.py` so `--reload` subprocesses never boot on broken imports.
4. **Never leak secrets, session IDs, or scratch state**:
   - Never read, print, or commit `.env`, `.sessions/`, `.browser_profile/`, `.agents/`, `state/`, or `logs/`.
   - Keep `sid=<token>` redaction active in `spaceinvasion/logging_utils.py` (`redact_sensitive`).
   - Never execute `DELETE FROM` or `DROP TABLE` on `state/spaceinvasion.db`; keep all telemetry and queue ledgers strictly append-only.
5. **Preserve SpaceInvasion Classic invariants**:
   - No rapidfire in combat (`1:1` targeting); *Blast furnace* uses `2:1` Pig iron and is **not** boosted by *Mining technology*; **Escape flights** are invisible to Asteroid *Galaxy scanners*; Bash limit is **3/24h** on active planets and exempt on yellow inactives / *Spacia* (unless overridden by server bash-block banners).
   - In `_plan_tutorial_actions`, keep non-urgent *Fusion plant* upgrades in `bucket = 4` (after mines) whenever projected net energy stays comfortably positive.
   - Non-building advisories without live actionable DOM links/inputs must keep `executable_now=False`.

## Verification Commands

- **Run unit test suite**:
  ```bash
  .venv/bin/python3 -m unittest discover -s tests/unit -t . -v
  ```
- **Run end-to-end test suite**:
  ```bash
  .venv/bin/python3 -m unittest discover -s tests/e2e -t . -v
  ```
- **Inspect database migration status & integrity**:
  ```bash
  .venv/bin/python3 scripts/db_migrate.py status
  .venv/bin/python3 scripts/db_migrate.py verify --full-integrity
  ```
- **Inspect live stack safely**:
  ```bash
  curl -s http://127.0.0.1:8787/health
  curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"plan"}'
  ```
