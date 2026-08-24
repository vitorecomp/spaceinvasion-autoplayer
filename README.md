# SpaceInvasion Classic Autoplayer

Autonomous strategy engine (`priority` + evolutionary `ga_nn`), topological Action Graph (`DAG`), append-only SQLite universe observatory (`V001..V006` migrations), local HTTP API, and Web Operator Console for **SpaceInvasion Classic** (`https://spaceinvasion.bitmeup.com/?locale=en`).

See [docs/README.md](docs/README.md) for the full documentation portal, [docs/architecture.md](docs/architecture.md) for the 3-tier runtime design, [docs/database-mer.md](docs/database-mer.md) for the 22-table SQLite MER & migration architecture, [docs/specifications/README.md](docs/specifications/README.md) for the game-mechanics SRS, and [AGENTS.md](AGENTS.md) for contributor/agent rules.

## Repository Structure

```text
spaceinvasion-autoplayer/
├── starter.sh             # Supervised live stack launcher with crash auto-restart & CAPTCHA stop guard
├── bin/                   # 3-tier runtime launchers (run_stack.py, game_api.py, commander.py, login.py)
├── spaceinvasion/         # Core Python package (state, strategy, optimizer, migrations, storage, fleet, executor, ui/)
├── scripts/               # Maintenance & DB utilities (db_migrate.py, sync_wiki_data.py)
├── docs/                  # Unified documentation hub (architecture, MER, manual, specifications/, testing/, notes/)
├── notebooks/             # Offline analytics & GA+NN experimentation notebooks
├── tests/                 # Unified test suite (tests/unit/ + tests/e2e/)
├── config.example.yaml    # Non-secret StrategyConfig template (copy to config.yaml)
├── .env.example           # Credential profile template (copy to .env)
└── requirements.txt       # Python dependencies
```

## Architecture (Hot-Reloadable 3-Tier Stack)

```text
[Tier 3] bin/commander.py (--reload) / Web Operator Console / curl
             │  HTTP JSON (GET /state, GET /dashboard, POST /command)
             ▼
[Tier 2] bin/game_api.py @ 127.0.0.1:8787 (--reload) + SQLite WAL (state/spaceinvasion.db)
             │  Thread-locked GameBrowserClient + ActionExecutor
             ▼
[Tier 1] Persistent Chrome @ 127.0.0.1:9222 (--detach)
```

## Quickstart

1. **Install dependencies into `.venv/`**:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

2. **Configure credentials in git-ignored `.env`** (see [.env.example](.env.example)):

```env
SPACEINVASION_EMAIL=you@example.com
SPACEINVASION_PASSWORD=your-password
SPACEINVASION_UNIVERSE=RETRO
```

3. **Optional strategy configuration** via [config.example.yaml](config.example.yaml) copied to `config.yaml`.

4. **Launch the full hot-reloadable stack**:

```bash
./starter.sh                              # Supervised live stack with crash auto-restart
.venv/bin/python3 bin/run_stack.py        # Dry-run commander
.venv/bin/python3 bin/run_stack.py --live # Live execution in visible Chrome
```

Or launch individual tiers directly:

```bash
.venv/bin/python3 bin/login.py --debug-port 9222 --detach
.venv/bin/python3 bin/game_api.py --reload
.venv/bin/python3 bin/commander.py --loop --reload --live
```

Open the **Web Operator Console** at `http://127.0.0.1:8787/`:

![SpaceInvasion Control UI](docs/images/control-ui.png)

## Database Versioning & Migrations

All telemetry, espionage reports, battle/recycle reports, galaxy history, and manual command queues are persisted append-only in `state/spaceinvasion.db` (`V001..V006`):

```bash
.venv/bin/python3 scripts/db_migrate.py status
.venv/bin/python3 scripts/db_migrate.py migrate --all --backup
.venv/bin/python3 scripts/db_migrate.py verify --full-integrity
.venv/bin/python3 scripts/db_migrate.py backup --label manual_snapshot
```

## Verification

```bash
# Fast unit & component suite
.venv/bin/python3 -m unittest discover -s tests/unit -t . -v

# Full 4-tier E2E & adversarial suite
.venv/bin/python3 -m unittest discover -s tests/e2e -t . -v
```
