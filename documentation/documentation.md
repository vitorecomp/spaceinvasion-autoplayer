# System Reference & Operator Documentation

Complete operational, configuration, API, and workflow guide for `spaceinvasion-autoplayer`.

---

## 1. Quick Start & CLI Entrypoints

### 1.1 Environment Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
cp config.example.yaml config.yaml
```

### 1.2 Authentication & Stack Supervision
| Command | Purpose |
|---|---|
| `.venv/bin/python3 login.py` | Launches detached Chrome (`127.0.0.1:9222`) and authenticates into BitMeUp / SpaceInvasion Classic (pausing cleanly if 2FA or CAPTCHA user input is required). |
| `./run_stack.sh` | **Recommended production launcher**: Runs `run_stack.py --live` under an auto-restarting bash supervisor (`5s -> 60s` exponential backoff on crashes; immediate halt on exit code `2` anti-bot/CAPTCHA). |
| `./run_stack.sh --dry-run` | Runs the supervised stack in dry-run observation mode (plans and logs actions without clicking in-game). |
| `.venv/bin/python3 run_stack.py --live` | Starts/verifies Chrome `:9222`, hot-reloading `game_api.py` (`127.0.0.1:8787`), and hot-reloading `commander.py --loop --live`. |
| `.venv/bin/python3 game_api.py --reload` | Runs only the HTTP JSON Bridge and Web Operator Console (`http://127.0.0.1:8787/`). |
| `.venv/bin/python3 commander.py --once` | Executes a single dry-run planning/inspection tick via HTTP (`--live` to execute). |
| `.venv/bin/python3 advisor.py` | Prints offline strategy, ETA, and economy diagnostics. |
| `.venv/bin/python3 scripts/sync_wiki_data.py` | Refreshes `spaceinvasion/game_data.py` from the SpaceInvasion Classic Wiki catalog. |

---

## 2. `StrategyConfig` Reference (`config.yaml`)

All 30 strategy and optimizer fields in `spaceinvasion/config.py` can be configured in `config.yaml` (seeded from `config.example.yaml`) or simulated/applied live from the **Action Graph** right-side panel (`#ag-control-panel`):

### 2.1 Planner & Evolutionary Optimizer (`ga_nn`)
| Key | Type | Default | Description |
|---|---|---|---|
| `planner_mode` | `str` | `"priority"` | Active decision engine: `"priority"` (heuristic `P0`–`P3`) or `"ga_nn"` (Genetic Algorithm + Neural Value Network). |
| `playstyle` | `str` | `"miner"` | Objective weighting profile: `"miner"`, `"hybrid"`, or `"fleeter"`. |
| `ga_population_size` | `int` | `24` | Candidate chromosomes per generation (`4..200`). |
| `ga_generations` | `int` | `12` | Evolutionary + online MLP training generations per tick (`1..100`). |
| `ga_horizon_steps` | `int` | `6` | Forward lookahead genes per chromosome (`1..20`). |
| `ga_mutation_rate` | `float` | `0.25` | Per-gene mutation probability (`0.0..1.0`). |
| `ga_nn_weight` | `float` | `0.35` | Blend weight for `NeuralValueNetwork` terminal evaluation (`0.0..1.0`). |
| `ga_seed` | `int \| null` | `null` | Fixed RNG seed for deterministic optimization (`null` for live entropy). |

### 2.2 Pacing, Safety & Automation Gates
| Key | Type | Default | Description |
|---|---|---|---|
| `action_batch_interval_seconds` | `int` | `120` | Minimum interval between automated batch wakeups (`5..86400`), paired with exact build/queue completion timers. |
| `max_actions_per_hour` | `int` | `60` | Safety ceiling on executed actions per rolling hour. |
| `auto_escape_flight` | `bool` | `true` | Automatically pack resources (`Spice -> Metal -> Kryptonite -> Pig iron`) and launch `ESCAPE_FLIGHT` on hostile `P0` incoming attacks. |
| `escape_fuel_reserve_spice` | `int` | `500` | Spice kept on colony to pay escape-flight fuel. |
| `auto_claim_tutorial` | `bool` | `true` | Claim completed tutorial steps when warehouse headroom prevents overflow. |
| `prioritize_tutorial` | `bool` | `true` | Prioritize tutorial milestone tasks (Steps `1`–`11`). |

### 2.3 Economy, Energy & Infrastructure Policy
| Key | Type | Default | Description |
|---|---|---|---|
| `forbid_planetary_solar` | `bool` | `true` | Never build `Thermosolar plant` (`b6`) or `Solar cells` (`s9`) on normal planets; demolish existing planetary `b6 -> L0` once `Fusion plant` (`b5`) covers energy. |
| `auto_throttle_blast_furnace` | `bool` | `true` | Automatically adjust `Blast furnace` (`b2`) slider in `internalEconomy` (including `0%` pause when energy is negative or when Metal storage is full while Pig iron has free space). |
| `merchant_mode` | `bool` | `false` | Optimize pure mine extraction assuming external `6:2.5:3:1` Merchant trades instead of local smelting. |
| `energy_buffer` | `int` | `10` | Target net energy reserve before scheduling mine upgrades. |
| `warehouse_fill_trigger` | `float` | `0.85` | Stockpile fill ratio (`0.10..1.00`) that triggers `P1` warehouse expansion (`b11`–`b14`). |
| `min_free_fields_warning` | `int` | `8` | Remaining building-field threshold triggering compact-infrastructure rules. |
| `allow_research` | `bool` | `true` | Permit automated `RESEARCH` actions (`r1`–`r16`). |
| `allow_shipyard` | `bool` | `true` | Permit automated `BUILD_SHIP` construction toward `shipyard_targets`. |
| `shipyard_targets` | `dict[str,int]` | `{...}` | Desired per-colony ship counts keyed by ship slug (e.g., `small-transport`, `small-fighter`, `spy-probe`, `recycler`, `colony-ship`). |

### 2.4 Reconnaissance, Raiding & Colonization
| Key | Type | Default | Description |
|---|---|---|---|
| `allow_galaxy_scan` | `bool` | `true` | Scan neighboring solar systems (`scan_radius_systems`) to discover yellow inactives, `Spacia`, debris fields, and open colony slots. |
| `scan_radius_systems` | `int` | `15` | +/- solar-system scan radius around each owned colony. |
| `allow_spy` | `bool` | `true` | Dispatch `Spy probe` (`s10`) recon missions to unspied/stale farm candidates. |
| `allow_raids` | `bool` | `false` | Dispatch `ATTACK` raids when `1:1` combat simulation predicts `>= min_combat_power_ratio` victory and positive loot. |
| `min_combat_power_ratio` | `float` | `1.4` | Minimum simulated attacker-to-defender combat power ratio required to launch a raid. |
| `min_raid_spice_value` | `float` | `2000.0` | Minimum expected plunder in `SpiceEq` required to justify a fleet slot. |
| `allow_colonize` | `bool` | `true` | Dispatch `Colony ship` (`s8`) missions to top-graded uninhabited slots (`>= min_colony_fields`). |
| `min_colony_fields` | `int` | `300` | Minimum estimated/spied planet field capacity required for automated colonization (`50..500`). |

---

## 3. HTTP JSON Bridge Reference (`http://127.0.0.1:8787`)

### 3.1 `GET` Endpoints
| Route | Query Params | Description |
|---|---|---|
| `GET /` | — | Serves the `SpaceInvasion Control` Web Operator Console (`spaceinvasion/ui/`). |
| `GET /health` | — | Bridge liveness, Chrome port `9222` status, in-game state, per-minute page-view counter (`24/min` budget), batch interval, and `bot_control` pause/CAPTCHA flags. |
| `GET /ui-version` | — | SHA-256 asset fingerprint of `index.html`, `styles.css`, and `app.js` for live UI reload detection. |
| `GET /state` | `refresh=0\|1`, `include_buildings=0\|1`, `planet_id=<int>` | Returns sanitized `GameState` JSON (colonies, resources, energy, queues, offers, fleet alerts, tutorial progress). |
| `GET /dashboard` | `refresh=0\|1`, `planet_id=<int>` | Consolidated operator payload: `state`, `plan`, memoized `action_graph`, `buildings_eta`, `combat_overview`, `ranking_evolution`, `manual_queue`, `db_summary`, `config`, and redacted log tails (`< 15ms` warm median). |

### 3.2 `POST /command` Actions (`Content-Type: application/json`)

| Domain | `command` Values | Purpose |
|---|---|---|
| **Planning & Action Graph** | `plan`, `action_graph`, `buildings_eta`, `ranking_evolution` | Generate heuristic or `ga_nn` action plans, run isolated `action_graph` parameter simulations (`simulated: true`, `refresh: false`, `config: {...}`), or inspect building/research ETAs. |
| **Colony Construction & Economy** | `build`, `demolish_building`, `research`, `build_ship`, `throttle_building`, `sync_economy`, `claim_tutorial` | Execute (or dry-run with `"dry_run": true`) building upgrades, `b6` solar demolitions, tech research, shipyard batches, `internalEconomy` percentage sliders, and tutorial claims. |
| **Fleet, Combat & Colonization** | `attack`, `spy`, `recycle`, `colonize`, `escape_flight`, `scan_galaxy`, `simulate_combat`, `combat_overview`, `fleetsave_plan` | Launch attacks, espionage probes, debris recyclers (`s11`), colony ships (`s8`), or `SAVE` escape flights; scan solar systems; run offline `1:1` combat simulations. |
| **Manual Operator Queue** | `queue_list`, `queue_add`, `queue_update`, `queue_cancel`, `queue_clear` | Enqueue manual actions (`P0`..`P3`), reorder (`top`, `up`, `down`), pin priorities, reserve resources, or cancel pending items. |
| **Intelligence & Archival Views** | `espionage_intel`, `colony_candidates`, `attack_results`, `recycler_results`, `game_messages`, `sync_messages`, `sync_spy_reports`, `db_summary` | Query archived SQLite spy reports, graded `300+` field colony candidates, battle plunder/loss history, rubble harvests, and archived in-game messages. |
| **Runtime, Session & Config** | `config`, `update_config`, `bot_control`, `tick_once`, `ensure_session`, `navigate`, `menu` | Read or persist `StrategyConfig` (`update_config` writes `config.yaml` + `config_history`), pause/resume automation, trigger a single tick, or navigate game frames safely under `_lock`. |

#### Example: Isolated Action Graph Simulation via `curl`
```bash
curl -s -X POST http://127.0.0.1:8787/command \
  -H 'Content-Type: application/json' \
  -d '{
    "command": "action_graph",
    "simulated": true,
    "refresh": false,
    "planner_mode": "ga_nn",
    "config": {
      "playstyle": "fleeter",
      "ga_population_size": 24,
      "ga_generations": 10,
      "ga_nn_weight": 0.40
    }
  }'
```

---

## 4. Experimentation & Strategy Notebooks

Both Jupyter notebooks in `notebooks/` run **100% read-only** (copying `state/spaceinvasion.db` + WAL sidecars into a temporary directory before querying and never opening browser connections):

1. **[notebooks/optimizer_experimentation.ipynb](../notebooks/optimizer_experimentation.ipynb)** (`25` cells across `8` chapters):
   - Walks step-by-step through `attack_history.json` coercion, 4-layer SQLite WAL inspection, midgame colony synthesis, controlled Farm (`90%` win) vs. Fluke Trap (`10%` win) raid telemetry, 46D state encoding, `NeuralValueNetwork` rollout training & `dV/dx_j` sensitivities, `ga_nn` convergence, Action Graph DAG layering, planetary solar demolition ablations, and hyperparameter sweeps.
2. **[notebooks/full_planner.ipynb](../notebooks/full_planner.ipynb)**:
   - Full-game strategy simulation notebook starting with dedicated statistical cells for **Attack Resource Mean & Std** (`pig_iron`, `metal`, `kryptonite`, `spice`, `spice_eq`), **Attack Time Mean & Std** (one-way flight, round-trip slot occupation, and inter-attack dispatch interval), and **Attack Success Rate** (`W/N`, binomial SE, Laplace-smoothed prior, and per-slot-hour `SpiceEq` yield).

---

## 5. Verification & Test Suites

Run the automated test suites before committing any change:

```bash
# 1. Unit, Action Graph, Watchdog, Notebook & Dual-Node-VM UI Suite
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -W error::ResourceWarning -m unittest discover -s tests -v

# 2. 5-Tier End-to-End Integration & Adversarial Suite
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -W error::ResourceWarning -m unittest discover -s e2e_tests -v
```
