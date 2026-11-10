# SpaceInvasion Autoplayer — Operator & System Manual

Operational handbook for installing, running, monitoring, backing up, and verifying `spaceinvasion-autoplayer` in both **Docker/Podman** and **Host** modes.

---

## 1. Prerequisites & Credentials

- **PostgreSQL 16** (managed automatically via `docker-compose.yml` / Podman in both Docker and Host modes).
- **Python 3.12+** (`.venv/` for Host mode and local verification).
- **Node.js 22+** (only required in Host mode for `npm --prefix frontend run build` and Vitest; built inside `docker/Containerfile.backend` in Docker mode).
- **Credentials (`.env`)**: Copy `.env.example` to git-ignored `.env`:

```env
SPACEINVASION_EMAIL=you@example.com
SPACEINVASION_PASSWORD=your-password
SPACEINVASION_UNIVERSE=RETRO
```

Never commit `.env`, `.sessions/`, `.browser_profile/`, `data/`, `state/`, or `logs/`.

---

## 2. Running the Stack

### Mode A: Docker / Podman Compose (5 Containers)

```bash
podman-compose up -d --build
podman-compose ps
```

Services & local ports (`127.0.0.1` only):
- **`postgres`** (`127.0.0.1:5433`): PostgreSQL 16 with `pg_stat_statements` enabled and `./data/postgres` persistent volume.
- **`browser`** (`127.0.0.1:7900`): Persistent Chromium + noVNC (`http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale`) for manual login/CAPTCHA resolution.
- **`game_api`** (`127.0.0.1:8787`): HTTP JSON API (`bin/game_api.py --reload`) serving the built Operator Console from `/opt/spaceinvasion-ui` (`SPACEINVASION_UI_DIR=/opt/spaceinvasion-ui`).
- **`commander`**: Pure-HTTP autonomous loop (`bin/commander.py --live --loop --reload`).
- **`frontend`** (`127.0.0.1:5173`): Optional live Vite dev server proxying API calls to `game_api`.

### Mode B: Host Mode (`./starter.sh` + PostgreSQL 16)

```bash
./starter.sh                 # Live supervised stack (auto-builds stale UI, ensures PG16, auto-restarts crashes)
./starter.sh --dry-run       # Dry-run commander (no in-game clicks)
./starter.sh --check-ui-build # Rebuild spaceinvasion/ui/ only if missing or stale, then exit
```

Individual host-mode entrypoints:
- `bin/login.py --debug-port 9222 --detach` — Opens persistent host Chrome profile and logs into BitMeUp.
- `bin/game_api.py --reload` — Runs the local HTTP API bridge on `127.0.0.1:8787`.
- `bin/commander.py --loop --reload [--live]` — Runs the pure-HTTP autonomous decision loop.
- `bin/run_stack.py [--live]` — Supervises Chrome, `game_api.py`, and `commander.py`.

**CAPTCHA / Anti-Bot Stop Behavior**: If Cloudflare Turnstile or a BitMeUp challenge appears, automation pauses immediately, persists a `captcha_gate` notification in the Operator Console, and exits `starter.sh` with code `2` (never auto-restarting into an anti-bot gate). Resolve the challenge manually in Chrome (or noVNC `:7900`), then click **Resume** in the console or re-run `./starter.sh`.

---

## 3. Configuration & HTTP API

- **Strategy Settings**: Persisted in PostgreSQL `strategy_config_store` and editable live in the Web Operator Console. See the generated [Strategy Settings Reference](./settings-reference.md) for all 50 canonical fields, bounds, and defaults.
- **HTTP Endpoints**:
  - `GET /health` — Lightweight bridge health & DB status (lock-free).
  - `GET /bot_status` — Pause state, mode override, and `captcha_gate` alert status (lock-free).
  - `GET /ui-version` — Compiled UI bundle hash/version.
  - `GET /state` — Published empire state snapshot (lock-free).
  - `GET /dashboard` — Consolidated Operator Console payload (lock-free).
  - `POST /command` — Executes any of the 60 declarative commands documented in the generated [API Command Reference](./api-commands.md).

Quick read-only checks:

```bash
curl -s http://127.0.0.1:8787/health
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"plan"}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"db_verify"}'
```

---

## 4. Database Backups & Alembic Migrations

- **Manual `pg_dump` backup**:

```bash
podman exec spaceinvasion_postgres pg_dump -U spaceinvasion -Fc spaceinvasion > data/backups/manual_$(date -u +%Y%m%dT%H%M%SZ).dump
```

- **Programmatic Alembic upgrades** (`spaceinvasion.db.alembic_migrations.upgrade_database`) require a `backup_fn` callback (creating a pre-upgrade `pg_dump` snapshot) and append SHA-256 checksums + timings to `schema_migration_events`.
- **Query performance inspection**:

```bash
.venv/bin/python3 scripts/enable_pg_stats.py --report --limit 40
```

---

## 5. Quality Gates & Verification

```bash
# Static quality gates (ruff format + lint + mypy + AST standards)
.venv/bin/python3 scripts/check.py --lint-only

# Regenerate / verify docs/api-commands.md and docs/settings-reference.md
.venv/bin/python3 scripts/generate_docs.py --check
.venv/bin/python3 scripts/generate_docs.py --write

# Frontend TypeScript build & Vitest unit suite
npm --prefix frontend run build
npm --prefix frontend test

# Python tests (hermetic, never touching live ports 8787/5433/9222/7900/5173)
.venv/bin/pytest -v tests/invariants/ tests/unit/
.venv/bin/pytest -v tests/e2e/
```
