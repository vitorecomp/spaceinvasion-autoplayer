# SpaceInvasion Classic Autoplayer — Master Specification

## 1. Purpose & Design Philosophy

Build an autonomous, player-faithful agent and assistant for **SpaceInvasion Classic** (`https://spaceinvasion.bitmeup.com/?locale=en`), hosted on the BitMeUp portal.

### Core Principles
1. **Browser-Native Execution**: Drive real Google Chrome via Selenium with a persistent user profile (`.browser_profile/<account>`) and localhost DevTools debugging port (`127.0.0.1:9222`) so a single live browser window stays attachable across manual inspection and automated ticks.
2. **Dry-Run First, Auditable Always**: Default to read-only inspection (`advisor.py`, `run_bot.py`); live DOM clicks require opt-in (`--live`) with JSONL audit logs (`logs/bot.jsonl`), rate caps, and post-action verification.
3. **Full-Lifecycle Strategy**: Support every stage of an account—from Tutorial 1–11 bootstrap through multi-planet economy, research, colonization, fleet saving, inactive/Spacia raiding, debris recycling, Synth/AI-Core management, and weekly Alien Black Hole waves.
4. **Strict Safety Boundaries**: Never attempt to bypass CAPTCHAs, Cloudflare challenges, or 2FA gates (halt and hand control to the user in the visible Chrome window). Store credentials exclusively in git-ignored `.env`.

### Non-Goals
- SpaceInvasion 2 (`spaceinvasion2.bitmeup.com`), which is a separate game.
- Packet tampering, rate-limit evasion, or anti-bot challenge solving.

---

## 2. SpaceInvasion Classic Domain Model (Full Lifecycle)

### 2.1 Resources, Energy & Economy Coupling
- **Four stored resources** (per planet/asteroid, offline-indexed in `spaceinvasion/game_data.py` via `scripts/sync_wiki_data.py`):
  - **Pig iron** (`b1` *Pig iron mine*) — construction material and raw feedstock consumed by Blast furnaces (`+2%/level` from *Mining technology*).
  - **Metal** (`b2` *Blast furnace*) — smelted from Pig iron at an exact **2:1** input/output ratio (`pig_iron_used = 2 * prod`; **not** boosted by *Mining technology*); primary material for ships, research, and advanced buildings.
  - **Kryptonite** (`b3` *Kryptonite mine*) — critical for research, ships, and defenses (`+2%/level` from *Mining technology*).
  - **Spice** (`b4` *Spice mine*) — fleet fuel, advanced units, Galaxy scans (`5,000`/scan), AI-Core runs, and Thermosolar fuel; yield scales inversely with planet temperature (colder planets produce more Spice, `+2%/level` from *Mining technology*).
  - **Merchant Value Parity**: Fixed conversion rate `Pig iron 6 : Metal 2.5 : Kryptonite 3 : Spice 1` used by the ROI planner to normalize build costs and hourly gains into Spice equivalents.
- **Warehouse Cap Penalty**: Reaching 100% capacity in *Pig iron / Metal / Kryptonite / Spice warehouse* (`b11`–`b14`; capacity scales `100,000 * 2^L`, e.g., L1 = `200,000`) drops that resource's output to **5%**.
- **Instantaneous Energy Balance**:
  - Supplied by *Fusion plant* (`b5`), *Thermosolar plant* (`b6`, burns Spice), orbital *Solar cells* (`s9`, scale with warmth, destroyable in combat), and *Energy technology* (`r2`, +1%/level global).
  - Negative net energy throttles **all** mines proportionally (`Production factor < 100%`).
  - Surplus energy charges *Particle cannon* (`b15`), *Particle shield* (`b16`), *Teleporter* (`b18`), and L40+ *Tunnel system* (`b20`) particle fog accumulators.
- **Utilization Controls (`internalEconomy`)**: Mines and power plants can be throttled 0–100% per planet (useful to pause Spice burn on Thermosolar plants or divert surplus energy into Teleporter charging).

### 2.2 Celestial Bodies & Roles
Each account expands across specialized celestial bodies:
- **Homeworld & Economy Colonies**: 230+ building fields (`fields_used / fields_max`), full building tree. Cold planets specialize in Spice; warm planets favor Solar cells.
- **Raid Colonies (`[RK]`)**: Unlocked by *The Fleeter* specialization. Cannot build economy structures (no mines, Blast furnace, Fusion plant, warehouses, Research lab, Tunnel system, or Quantum Array); act as forward fleet staging bases.
- **Asteroids**: Obtained via battles/events/IDT. Support only *Development center* (`b7`), *Weapons factory* (`b9`), *Fleet base* (`b10`), *Microsystem accelerator* (`b17`), *Galaxy scanner* (`b19`, exclusive to asteroids), and *Research lab* (`b8`, via Synth node). Max 1 building queue slot (vs. 5 on planets, 6 with Construction Commander).
- **Event Outposts (`[EVENT]`)**: Isolated alien-galaxy worlds during outpost events with separate wave skill trees and no transfers to/from main planets.

### 2.3 Tech Tree, Research & Ship Production
- **Building Accelerators**:
  - *Development center* (`b7`) cuts local building times and gates *Research lab* (`L2`), *Weapons factory* (`L3`), *Galaxy scanner* (`L8`), and *Microsystem accelerator* (`L12`).
  - *Microsystem accelerator* (`b17`) halves build times again per level.
- **Account-Wide Research (`r1`–`r15`)**:
  - Global queue: **1 active** research (+1 waiting slot with *Engineer* assistant). Paid upfront (70% refund on cancel). Speed scales with the *Research lab* (`b8`) level on the planet where started.
  - Core progression priorities: *Energy tech* (`r2`) → *Mining tech* (`r1`) → *Engine research* (`r3`) / *Rocket* (`r4`) / *Diffusion* (`r5`) / *Warp* (`r6`) → *Spy tech* (`r7`) → combat techs (*Weapons* `r8`, *Shield* `r9`, *Defence systems* `r10`, *Targeting* `r11`) → *Supercomputer* / *Graviton* (`r13`) / *Teleportation* (`r14`) / *Black hole research* (`r15`).
- **Weapons Factory (`internalWeaponFactory`) & Defense (`internalDefense`)**:
  - Ships built in capped time packages (up to 10 repeats per order; 70% refund on cancel).
  - Three engine groups (+10% speed per engine level; fleet travels at slowest ship's speed):
    - **Rocket**: `s1` Small transport, `s2` Large transport, `s4` Small fighter, `s5` Warship, `s8` Colony ship, `s10` Spy probe.
    - **Diffusion**: `s3` Transmitter, `s6` Starcruiser, `s7` Phoenix, `s11` Recycler.
    - **Warp**: `s13` Battleship, `s14` Stealth bomber (pre-round first strike on defenses), `s15` Destroyer, `s16` Imperial starbase.
    - **Outpost/Advanced Variants**: `s104` Small hunter, `s106` Titan Carrier, `s206` Hangar Ship, `s111` Collector Swarm, `s113` Void Shield, `s115` The Doombringer.
  - **Defense & Dig-in**: In supported universes (e.g., GeNeSIS 2527, Sirius, Infinity), planetary guns (`d1`–`d9`) can **Dig in** (1h minus 1 min/*Tunnel system* level) to avoid destruction (excluding shield domes `d10`–`d11`, and blocking new gun construction while dug in). *Particle shield* absorbs *Particle cannon* beams (`46% / 31% / 23%` target priority).

### 2.4 Combat, Attack Zones & Survival
- **Attack Zones (`AZ 1–20`) & Galaxy Colors**:
  - Standard universes allow PvP within `±2 AZ` (or `25%–400%` points on Sirius; Top-50 players are attackable by anyone with a 14-day retaliation window).
  - **Galaxy Map Colors**: `Light red` (in AZ), `Bright red` (Top 50 / retaliator), `Dark red` (out of range, too strong), `Blue` (too weak or Vacation Mode), `Yellow` (inactive >7 days or **Spacia** NPCs—attackable by anyone), `White` (Spectral Mode—unattackable), `Green/Turquoise` (Alliance/allies), `Purple` (Game Operators).
- **Combat Mechanics**:
  - Up to 6 rounds (7 with Synth node); shields absorb before armor; random **battle luck** requires combat simulator safety margins (`>=1.3x`–`1.5x` power buffer). Winner plunders up to 50% of planet resources (higher with Fleeter spec/Synth) limited by cargo hold. Destroyed units create debris fields.
- **Escape Flight (Fleet Save)**:
  - **Most critical survival mechanic**: fleets in flight cannot be attacked and are hidden from *Galaxy scanners*.
  - Load cargo by value density: **Spice → Metal → Kryptonite → Pig iron** (reserving Spice for flight fuel).
  - Supports **recall-dodge** (launch just before hostile impact, recall after impact passes) and *Astral fleet* restoration in Astral universes.
  - Vacation/Spectral Mode **does not** cancel attacks already in flight; survival monitoring stays active until threats clear.

### 2.5 Late-Game & Weekly Systems
- **Specialization (`internalSpecs`)**: Choice of **Miner**, **Fleeter**, **Researcher**, or **Alien** tiers unlocked by tutorial completion + point milestones (2M–50M) + 30d out of vacation mode.
- **Synth Network (`internalSynth`)**: 346-node web powered by up to 80 Synth points (`Quantum Array` up to 30, Assistants/VIP up to 12, Non-vacation `3`, Non-harassment `5`, etc.), upgraded from Gray → Orange using Synth energy and Cores generated by *Synth Former* and *AI-Core* (1d/3d/7d Spice programs).
- **Weekly Aliens & Black Holes (`internalAlien`)**: Friday–Friday waves unlocked at **10,000 tactical fleet strength**. Commit virtual wave power without risking real ships, claim daily reinforcement, attack White/Green/Purple/Red/Orange black holes matching required attack power + required ship types up to personal Spice and War Emblem wave caps.
- **Economy Extras**: *Interdimensional Transmitter* (`internalTransmitter`) free daily/event spins, *Bonus Inventory* multiple-production stacking (<60d cap), and *Merchant* (`6 : 2.5 : 3 : 1` swap rate + Recycling Yard).

---

## 3. Observed Technical Shell & Endpoints (Verified Live on RETRO)

- **Game Shell (`indexGame.es?sid=...`)**: Three-iframe layout:
  - `F_Navi` (`indexInternal.es?action=internalMenu`): navigation menu & unread message counts (`normal`, `alliance`, `spio`, `battle`, `fleet`, `admin`).
  - `F_planets` (`indexInternal.es?action=internalPlanetsOverview`): embeds full empire JSON inside `#json-container` (`planets`, `buildings`, `ships`, `defense`, `researches`, `specs`, `synth`, `dnTower`, `resources`). Does **not** auto-reload on every `F_Hauptframe` action; automation reloads `F_planets` explicitly when refreshing post-action telemetry.
  - `F_Hauptframe`: active screen (`internalHome`, `internalBuildings`, `internalEconomy`, `internalResearch`, `internalWeaponFactory`, `internalDefense`, `internalFleet`, `internalGalaxy`, `internalMessages`, `internalTransmitter`, `internalAlien`, `internalSynth`, `internalSpecs`). Auto-reloads automatically when a build countdown finishes (`00:00:00`), requiring `StaleElementReferenceException` retry handling.
- **Per-Minute Rate Limit**: SpaceInvasion enforces a page-view throttle (*"You've made more than page views at last minute"*). Reading `#json-container` from `F_planets` + pacing navigation (`0.9–1.8s+` jitter) avoids hitting it.
- **Key Action Surfaces**:
  - **Buildings**: `<div class="ecoshadow build-card b-<id>">`, dot-grouped numbers (`2.000` = `2000`), `<a class="bau" href="...internalBuildItem&buildingID=<id>">`, and `Construction queue ( N Elements )` (max 5 slots on planets).
  - **Tutorial Steps 1–11**: Inside `#tutorial.text` (toggled via `#tutorial.show`). Task counters (`cur / target`) are **relative deltas within the step**, not absolute building levels. Completion requires submitting `form[action*="internalHome"]` (`tutorialComplete=1`, *"Mission complete"*).
  - **Planet Switcher**: `select[name="planet"]` (`newPlanet=<planet_id>`).
  - **Shipyard / Defense**: POST `action=internalBuildItem&buildShip=1` / `buildDefense=1`.
  - **Fleet & Recall**: `internalFleet` (`action=internalSendFleet`); recall on `internalHome` via POST `recallFleet=<id>`.

---

## 4. System Architecture & Decision Priority Hierarchy

```text
+-----------------------------------------------------------------------+
|              Hot-Reloadable 3-Tier Runtime (run_stack.py)             |
|                                                                       |
|  [Tier 3] commander.py (--reload)  /  External Programs (curl, bots)  |
|             │  HTTP JSON: GET /state, POST /command                   |
|             ▼                                                         |
|  [Tier 2] game_api.py @ 127.0.0.1:8787 (--reload)                     |
|             │  Thread-locked GameBrowserClient + ActionExecutor       |
|             ▼                                                         |
|  [Tier 1] Persistent Chrome @ 127.0.0.1:9222 (--detach)               |
|             │  Survives API & Commander reloads without re-login      |
+-----------------------------------------------------------------------+
```

### Runtime Tiers & Hot-Reload Boundary
1. **Tier 1 — Persistent Chrome (`127.0.0.1:9222`)**: Launched/re-attached via `spaceinvasion.browser` with a persistent `.browser_profile/<account>` and `.env` login. Stays open when either Python service reloads.
2. **Tier 2 — Game HTTP API (`game_api.py` / `spaceinvasion.api_server` @ `127.0.0.1:8787`)**: Turns the live 3-iframe game into a serialized JSON API:
   - `GET /health` — Chrome debug-port status, `in_game` flag, active URL, remaining hourly budget.
   - `GET /state?include_buildings=1` — Refreshes `F_planets` and returns the full `GameState` JSON.
   - `POST /command` — Executes/simulates structured commands (`plan`, `build`, `claim_tutorial`, `navigate`, `ensure_session`) with `dry_run` support and a single browser mutex.
   - `--reload` restarts only `game_api.py` when driver/parser/API files change, leaving Chrome untouched.
3. **Tier 3 — Hot-Reloadable Commander (`commander.py` / `spaceinvasion.commander_client`) & External Clients**: Pure HTTP client with zero Selenium dependency. Evaluates `P0`–`P4` decision rules and dispatches JSON commands to `game_api.py`. `--reload` restarts only `commander.py` whenever `strategy.py` or commander features are updated.

### Priority Stack (Strict Order)
- **P0 — Survival & Threat Mitigation**:
  - Poll `internalHome` / `FleetActions` for incoming hostile flights (every 15–30s when fleets/enemies are active, or tighter as an arrival nears).
  - Trigger **Escape Flight** (`T - safety_margin`, e.g., 90–180s before impact) loading all stationed ships + `Spice → Metal → Kryptonite → Pig iron`, or schedule recall-dodge.
  - Spend un-savable excess resources into buildings/research/defenses before impact; fire urgent operator alert.
- **P1 — Empire Health Guards**:
  - **Energy Guard & Queue Lookahead**: Uses wiki energy deltas (`building_energy_delta`) and queued buildings (`projected_net`) so if `energy.net < 0`—or if the next recommended mine would push `projected_net < 0`—*Fusion plant* (`b5`) is promoted to P1/head-of-queue proactively before any mine.
  - **Storage Guard**: When any resource reaches `>= 85%` of storage, upgrade its warehouse or dispatch excess via Transport/spending to avoid the 5% cap penalty.
- **P2 — Core Progression (Tutorial → Wiki ROI Economy → Tech-Tree Unlocks)**:
  - **Tutorial Bootstrap (Steps 1–11)**: Complete relative step tasks (proactive energy & *Development center* prioritized) and claim `tutorialComplete=1` rewards automatically.
  - **Wiki-Driven Mine ROI & Blast-Furnace Coupling**: Scores mines by Merchant Spice-equivalent payback (`cost_spice / delta_spice_per_h`) using planet `temperature` and *Mining technology* (`r1`), while capping *Blast furnace* (`b2`) upgrades when `pig_iron_used` exceeds `70%` of *Pig iron mine* output.
  - **Tech-Tree Prerequisite Stepping & Global Research (`internalResearch`)**: Resolves `missing_prerequisites` from `game_data.py`, computes cross-planet *Mining technology* (`r1`) global ROI (`+2%` across all planets), steps through Tutorial 4+ research tasks and *Weapons factory* unlocks via `researchID=<id>` clicks.
  - **Multi-Planet Rotation, Thermosolar Crossover & Zero-Field Solar Cells**: Evaluates mine ROI and energy/storage guards across all colonies (`newPlanet=<id>`), adopts *Thermosolar plant* (`b6`) once cheaper per energy than high-level *Fusion plant*, prefers `0-field` orbital *Solar cells* (`s9`) on tight hot planets, and maintains baseline *Small transports*, *Spy probes*, *Colony ships*, and *Recyclers* via `internalWeaponFactory`.
- **P3 — Expansion, Farming & Combat Heuristics (`spaceinvasion/fleet.py`)**:
  - **Colonization Strategy (`> 230` Fields vs. Cold Spice)**: Scouts uninhabited coordinates with free *Spy probes* (`pick_colony_candidates`)—targeting `>= 230` field economy worlds in mid-outer positions (`7–12`, supporting staggered delete-and-recolonize loops) or cold edge positions (`13–16`) for dedicated *Spice* worlds.
  - **Spacia & Inactive Raiding (`zero rapidfire` + Bash Rules)**: Parses system status & spy reports (`parse_galaxy_system_html`, `parse_spy_report_html`), prioritizes overlooked distant *Spacia* worlds (`score_raid_target`), simulates **1:1 no-rapidfire** + *Stealth bomber* pre-round defense strikes with `>= 1.4x` battle-luck margin, and enforces **3 attacks/24h** per active planet (`BashTracker`, exempting yellow inactives/Spacia).
  - **Weekly Aliens / Black Holes**: Once `>= 10,000` tactical strength, assemble wave fleet, request daily reinforcement, and run profitable Black Hole attacks until weekly Spice/Emblem caps are reached.
- **P4 — Housekeeping & Boosts**:
  - Claim free *Interdimensional Transmitter* spins, keep *Synth Former* / *AI-Core* running when Spice reserves allow, extend *Bonus Inventory* production multipliers when `<60d`, and activate the 5 free `+3%` *Economy → Building Upgrade* slots on bottleneck mines/Fusion.

---

## 5. Configuration, Credentials & Safety Controls

- **`.env`** (`chmod 600`, git-ignored): stores credentials (`SPACEINVASION_EMAIL`, `SPACEINVASION_PASSWORD`, `SPACEINVASION_UNIVERSE`, optional `SPACEINVASION_2FA_CODE`, and named `--account` overrides). Never logged or committed.
- **`config.yaml` (`config.example.yaml`, `spaceinvasion/config.py`)**: non-secret strategy profile—`merchant_mode` (skips *Blast furnace* post-tutorial in favor of Pig iron -> Merchant swaps), `allow_research`, `allow_shipyard`, `allow_multi_planet`, `allow_fleetsave`, `allow_raids`, `allow_colonize`, `combat_safety_margin` (`1.4`), `shipyard_targets`, and `hot_planet_solar_temp_threshold`. Hot-reloaded automatically alongside Python modules.
- **Circuit Breakers & Error Recovery**:
  - **Anti-bot / 2FA**: Immediately halt and alert the operator to complete verification manually in the visible Chrome window.
  - **Per-minute throttle (`RateLimitError`)**: Exponential backoff (45s–90s) + increase loop interval jitter.
  - **Session expiry (`SID` invalid / kicked to portal)**: Re-run `login_with_browser(..., attach=True)` on the same Chrome debug port before resuming ticks.
  - **Vacation / Spectral Mode**: Auto-pause attacks/raids/scanning while keeping P0 Escape Flight checks active for already-en-route attacks.

---

## 6. Implementation Roadmap

| Milestone | Scope | Status | Verification Criteria |
|---|---|---|---|
| **M0** | Spec, `.env` config, BitMeUp HTTP + Selenium Chrome login, debug-port attach/detach (`login.py`, `auth.py`, `browser.py`) | **Done** | Unit tests pass; Chrome attaches/detaches on `127.0.0.1:9222` and enters RETRO |
| **M1** | Live browser client & state parsers (`client.py`, `state.py`) | **Done** | Reads refreshed `#json-container`, `internalBuildings`, `internalResearch`, `internalWeaponFactory`, `Construction queue`, `internalHome`, `F_Navi` |
| **M2** | Wiki catalog sync & ROI planner (`scripts/sync_wiki_data.py`, `game_data.py`, `strategy.py`, `advisor.py`) | **Done** | Offline catalog for 21 buildings, 16 researches, 15 ships, 11 defenses; emits `P0`–`P3` plans with energy lookahead, Thermosolar crossover, Merchant mode, cross-planet Mining Tech ROI, and tech-tree unlocks |
| **M3** | Safe live executor, persistent debug-port loop, local Game HTTP API & hot-reloadable Commander (`executor.py`, `game_api.py`, `commander.py`, `run_stack.py`) | **Done** | Live Tutorial progression in Chrome; HTTP `/health`, `/state`, `/command` on `127.0.0.1:8787`; API & Commander hot-reload cleanly with `logs/commander.log` action tracking |
| **M4** | Research execution + Weapons Factory ships + Multi-planet colony planning & `config.yaml` (`state.py`, `executor.py`, `strategy.py`, `config.py`) | **Done** | Parses & executes `RESEARCH` (`researchID`) & `BUILD_SHIP` (`buildShip=1`); cross-planet builds & colony rotation; hot-reloaded `config.yaml` profile |
| **M5** | Escape Flight packer (`spaceinvasion/fleet.py`), `fleetsave_plan` API, P0 alerts & guarded dispatch | **Done (Planner/API; Live dispatch gated)** | Packs `Spice -> Metal -> Kryptonite -> Pig iron` keeping fuel reserve; live dispatch strictly gated by `allow_fleetsave=true` + `internalFleet` form integration |
| **M6** | Galaxy scanner, Spy report parser, 1:1 Combat/Spacia scorer, Bash tracker (`3/24h`), Colony `>=230` field filter (`fleet.py`) | **Done (Core logic/Parsers; Live dispatch next)** | Unit-tested parsers, `>=1.4x` combat safety, Stealth bomber strike, Bash exemptions, and `>230` field vs cold Spice slot ranking; remaining UI click wiring next |
