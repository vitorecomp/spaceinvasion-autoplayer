# AGENTS.md — Contributor & Agent Guide

Guide for coding agents working in `spaceinvasion-autoplayer`. Read [SPEC.md](SPEC.md) for the runtime architecture and [specifications/README.md](specifications/README.md) for the full SRS game mechanics, scaling tables, formulas, and page/click contracts.

## Repository Layout

- **3-Tier Live Runtime**:
  - `login.py` / `spaceinvasion/browser.py` / `spaceinvasion/auth.py` — BitMeUp login, `.env` credential profiles, persistent Chrome profile (`.browser_profile/`), and detached Chrome DevTools debug port (`127.0.0.1:9222`).
  - `game_api.py` / `spaceinvasion/api_server.py` — Thread-locked local HTTP JSON bridge (`http://127.0.0.1:8787`) exposing `GET /health`, `GET /ui-version`, `GET /state`, `GET /dashboard`, and `POST /command` (`plan`, `build`, `research`, `build_ship`, `claim_tutorial`, `throttle_building`, `buildings_eta`, `combat_overview`, `simulate_combat`, `ranking_evolution`, `db_summary`, `fleetsave_plan`, `navigate`, `menu`, `config`, `ensure_session`).
  - `commander.py` / `spaceinvasion/commander_client.py` — Pure-HTTP decision loop with zero Selenium imports.
  - `run_stack.py` — Starts/monitors Chrome debug port, `game_api.py --reload`, and `commander.py --loop --reload`.
- **Core Game Logic**:
  - `scripts/sync_wiki_data.py` -> `spaceinvasion/game_data.py` — Offline catalog scraped from the SpaceInvasion Classic Wiki (21 buildings, 16 researches, 15 ships, 11 defenses, Merchant parity `6:2.5:3:1`, temp curves, prerequisites).
  - `spaceinvasion/state.py` — Typed `GameState` / `PlanetState` / offer dataclasses and HTML/JSON parsers (`#json-container`, `internalBuildings`, `internalResearch`, `internalWeaponFactory`, `internalHome`, `F_Navi`).
  - `spaceinvasion/config.py` / `config.example.yaml` — Non-secret `StrategyConfig` (`config.yaml`, optional).
  - `spaceinvasion/strategy.py` — Priority-sorted `P0`–`P3` decision planner (`plan_actions`).
  - `spaceinvasion/executor.py` — Safe Selenium click/form executor with hourly rate cap, stale-element auto-reload retry, and dry-run defaults.
  - `spaceinvasion/fleet.py` — M5–M6 Escape flight packer, 1:1 zero-rapidfire combat simulator (`>=1.4x`), Bash tracker (`3/24h`), Galaxy/Spy parsers, Spacia scorer, and colony slot selector.
  - `spaceinvasion/storage.py` — Thread-safe SQLite database (`state/spaceinvasion.db`, WAL mode) persisting rank snapshots, colony snapshots, `internalEconomy` sliders, tutorial steps, executed actions, spy reports, and 24h raid history.
  - `spaceinvasion/logging_utils.py` / `spaceinvasion/reloader.py` — Structured `logs/*.jsonl` + human-readable `logs/*.log` (`ACTUAL` / `NEXT`) and subprocess hot-reloader.

## Operational Rules for Agents

1. **Never bypass anti-bot or auth gates**: Abort on CAPTCHAs, Turnstile, or missing 2FA codes and let the user handle them in Chrome.
2. **Never race a running Chrome debug session**: When `run_stack.py` or `game_api.py` is running on `127.0.0.1:8787`, never open a second Selenium client directly against Chrome port `9222`. Query or navigate through `http://127.0.0.1:8787` so `GameApiBridge._lock` serializes browser actions.
3. **Keep hot-reload safe**: Always write/generate dependencies (`spaceinvasion/game_data.py`, `spaceinvasion/state.py`, `spaceinvasion/fleet.py`) **before** editing `spaceinvasion/strategy.py`, `spaceinvasion/api_server.py`, or `commander.py` so `--reload` subprocesses never boot on broken imports.
4. **Never leak secrets or session IDs**:
   - Never read, print, or commit `.env`, `.sessions/`, or `.browser_profile/`.
   - Keep `sid=<token>` redaction active in `spaceinvasion/logging_utils.py` (`redact_sensitive`) whenever writing or checking `logs/`.
5. **Preserve SpaceInvasion Classic invariants**:
   - No rapidfire in combat (`1:1` targeting); *Blast furnace* uses `2:1` Pig iron and is **not** boosted by *Mining technology*; **Escape flights** are invisible to Asteroid *Galaxy scanners*; Bash limit is **3/24h** on active planets and exempt on yellow inactives / *Spacia*.
   - In `_plan_tutorial_actions`, keep non-urgent *Fusion plant* upgrades in `bucket = 4` (after mines) whenever projected net energy stays comfortably positive so excess Fusion levels are not rushed ahead of mines.
   - Non-building advisories without live actionable DOM links/inputs must keep `executable_now=False`.

## Verification Commands

- **Run full test suite** (must pass completely after any edit):
  ```bash
  .venv/bin/python3 -m unittest discover -s tests -v
  ```
- **Refresh offline wiki catalog**:
  ```bash
  .venv/bin/python3 scripts/sync_wiki_data.py
  ```
- **Inspect live stack safely**:
  ```bash
  curl -s http://127.0.0.1:8787/health
  curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"plan"}'
  ```
