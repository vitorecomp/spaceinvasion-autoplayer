# SpaceInvasion Classic Autoplayer

Autonomous strategy engine, local HTTP API, and Selenium browser player for **SpaceInvasion Classic** (`https://spaceinvasion.bitmeup.com/?locale=en`).

See [SPEC.md](SPEC.md) for the autoplayer architecture, [specifications/README.md](specifications/README.md) for the full SRS game-mechanics specification (`REQ-*`, tables, formulas, and page/click contracts), and [AGENTS.md](AGENTS.md) for contributor/agent guidelines.

## Architecture (Hot-Reloadable 3-Tier Stack)

```text
[Tier 3] commander.py (--reload) / curl / custom bots
            │  HTTP JSON (GET /state, POST /command)
            ▼
[Tier 2] game_api.py @ 127.0.0.1:8787 (--reload)
            │  Thread-locked GameBrowserClient + ActionExecutor
            ▼
[Tier 1] Persistent Chrome @ 127.0.0.1:9222 (--detach)
```

- **Tier 1 (Chrome on `127.0.0.1:9222`)**: Persistent login profile (`.browser_profile/`) stays open across API and Commander restarts.
- **Tier 2 (`game_api.py`)**: Exposes the live 3-iframe game (`F_Navi`, `F_planets`, `F_Hauptframe`) over localhost JSON HTTP and reloads cleanly when backend files change.
- **Tier 3 (`commander.py`)**: Pure HTTP client computing prioritized `P0`–`P3` decisions, dispatching builds/researches/ships, and logging `ACTUAL` + `NEXT` actions to [logs/](logs/).

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

3. **Optional strategy configuration** via [config.example.yaml](config.example.yaml) copied to `config.yaml`:

- `merchant_mode: false` — set `true` if using *Merchant* to convert Pig iron (`6 : 2.5`) instead of building higher *Blast furnaces*.
- `auto_throttle_blast_furnace: true` — automatically pause *Blast furnace* (`0%` in `internalEconomy`) when Pig iron is the bottleneck (`+2x` Pig iron savings) and resume (`100%`) once Metal is needed.
- `allow_research: true` / `allow_shipyard: true` / `allow_multi_planet: true` — enable research (`internalResearch`), baseline ship targets (`internalWeaponFactory`), and cross-colony switching.
- `allow_fleetsave: false` / `allow_raids: false` / `allow_colonize: false` — keep tactical fleet actions dry-run until explicitly enabled.

4. **Run the full hot-reloadable stack**:

```bash
.venv/bin/python3 run_stack.py           # Dry-run commander
.venv/bin/python3 run_stack.py --live    # Live execution in visible Chrome
```

Or launch services separately:

```bash
.venv/bin/python3 login.py --debug-port 9222 --detach
.venv/bin/python3 game_api.py --reload
.venv/bin/python3 commander.py --loop --reload --live
```

Then open the **Operator Control UI** at `http://127.0.0.1:8787/` (served directly by `game_api.py`). The console defaults to a focused **Dashboard** initial screen and uses a persistent sidebar to switch individual full-screen views without scrolling through one long page:

- **Dashboard (`DSH`)**: Sector resources, hourly net yields (`/h`), Blast furnace `0%`/`100%` toggle, active colonies, priority queue preview, and nearest building ETAs.
- **Buildings & ETAs (`BLD`)**: Reach-time forecasts (`L+1` to `L+4`) for all **21 buildings (`b1`–`b21`)** and **15 researches (`r1`–`r15`)**, bottleneck flags, Blast furnace `0%` what-if Pig-iron boost analysis, and live build triggers.
- **Combat & Fleet (`CMB`)**: Attack Zone bounds, `r8`/`r9`/`r10` combat bonuses, stationed ships/defenses, Weapons Factory quick orders, Escape Flight packer, and interactive `1:1` zero-rapidfire **Combat Simulator**.
- **Operations (`OPS`)**, **Ranking Evolution (`RNK`)**, **Strategy Config (`CFG`)**, and **Live Logs (`LOG`)**: Full `P0`–`P3` queue, AZ trajectory + milestones, `config.yaml` editor (`auto_throttle_blast_furnace`), live runtime logs, and an integrated **SQLite DB** inspector (`state/spaceinvasion.db`).

## Operator Control UI

![SpaceInvasion Control UI](docs/images/control-ui.png)

## Strategy Mechanics Implemented

- **Offline Wiki Catalog ([spaceinvasion/game_data.py](spaceinvasion/game_data.py))**: Scraped by [scripts/sync_wiki_data.py](scripts/sync_wiki_data.py) for all **21 buildings**, **16 researches**, **15 ships**, and **11 defenses**.
- **Proactive Energy & Zero Excess Spare Energy**: Projects post-queue net energy via `building_energy_delta`; promotes *Fusion plant* ahead of energy-starving mines (`P1` in economy), while deferring non-urgent tutorial Fusion levels behind mines (`bucket = 4`) when ample spare energy already exists. Adopts *Thermosolar plant* (`b6`) when cheaper per energy on cold high-Spice planets, and uses `0-field` orbital *Solar cells* (`s9`) on tight hot colonies.
- **Economy ROI & Blast Furnace Smelting Control (`b2 -> 0% / 100%`)**: Ranks mine upgrades by Merchant Spice-equivalent payback (`6 : 2.5 : 3 : 1`) adjusted for planet `temperature` and *Mining technology* (`+2%/level`, excluding *Blast furnace*), caps *Blast furnace* `pig_iron_used` at `<= 70%` of *Pig iron mine* output (or skips in `merchant_mode`), and automatically throttles *Blast furnace* (`0%` vs `100%`) via `THROTTLE_BUILDING` in `internalEconomy` when Pig iron is starving (`auto_throttle_blast_furnace: true`).
- **M5–M6 Fleet & Expansion Helpers ([spaceinvasion/fleet.py](spaceinvasion/fleet.py))**: Escape flight cargo packing (`Spice -> Metal -> Kryptonite -> Pig iron` retaining return fuel), `1:1` zero-rapidfire battle power & *Stealth bomber* first-strike evaluation (`>= 1.4x` margin), `BashTracker` (`3/24h` active cap, yellow inactive / *Spacia* exempt), distant *Spacia* target scoring, and `>= 230`-field mid-outer vs. cold-*Spice* colonization slot filtering.

## Useful Commands & HTTP API

Open `http://127.0.0.1:8787/` in any browser for the **Operator Control UI**, or script the HTTP bridge directly:

```bash
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8787/state
curl -s http://127.0.0.1:8787/bot_status
curl -s http://127.0.0.1:8787/dashboard
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"plan"}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"buildings_eta"}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"throttle_building","building_id":2,"percentage":0,"dry_run":true}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"simulate_combat","attacker_ships":{"s4":20,"s1":4},"defender_defense":{"d1":2}}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"ranking_evolution"}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"db_summary"}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"tick_once","dry_run":true}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"bot_control","paused":false,"mode_override":null}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"fleetsave_plan"}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"config"}'
curl -s -X POST http://127.0.0.1:8787/command -H 'Content-Type: application/json' -d '{"command":"update_config","config":{"auto_throttle_blast_furnace":true}}'
```

Refresh offline wiki tables:

```bash
.venv/bin/python3 scripts/sync_wiki_data.py
```

Run the test suite:

```bash
.venv/bin/python3 -m unittest discover -s tests -v
```
