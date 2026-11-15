# Strategy Settings Reference

Generated from `backend.core.settings`. Every surviving `StrategyConfig` field is listed below.

Removed legacy `ga_nn` keys (ignored on load, rejected on strict API patches): `ga_horizon_steps`, `ga_nn_weight`, `ga_seed`.

## Planner Engine & Empire Doctrine

Core planning engine selection, playstyle doctrine weighting, multi-planet scheduling scope, and colony expansion field thresholds.

| Setting | Type | Default | Bounds / Choices | Summary |
| :--- | :--- | :--- | :--- | :--- |
| `planner_mode` | `enum` | `priority` | `priority` \| `ai_planner` | Active autonomous planner engine (rule-based P0-P3 vs full-horizon AI-Planner). |
| `ai_adaptive_execution` | `bool` | `false` | `true` \| `false` | Enable Plan v2 adaptive smelting, remote lab starts, and drift re-tuning. |
| `ai_resource_spare_pct` | `float` | `10.0` | `0.0` .. `100.0` % | Soft spare resource share allocated to Fleet Planner hulls and tech unlocks. |
| `playstyle` | `enum` | `fleeter` | `fleeter` \| `hybrid` \| `miner` | Doctrine weighting for research ordering and shipyard hull quotas. |
| `allow_multi_planet` | `bool` | `true` | `true` \| `false` | Schedule buildings, power, warehouses, and fleets across all owned colonies. |
| `max_planets` | `int` | `11` | `1` .. `50` planets | Upper cap on normal colonies before halting autonomous colonization. |
| `min_colony_fields` | `int` | `300` | `50` .. `600` fields | Minimum buildable field count required by the colony grader and COLONIZE selector. |
| `min_free_fields_warning` | `int` | `20` | `1` .. `1000` fields | Free-field buffer below which a non-executable colony space advisory fires. |

- **`planner_mode` (Planner Mode)**: Selects between the deterministic rule-based P0–P3 heuristic pipeline (priority) and the full-horizon sequential rollout optimizer (ai_planner, auto-unlocks for live bot execution at 11/11 colonies). Legacy 'ga_nn' profiles normalize to 'priority' on load.
- **`ai_adaptive_execution` (Adaptive AI-Planner Execution)**: Enables Plan v2 adaptive execution: per-colony Blast furnace (b2) 2:1 smelting control, immediate remote Research lab starts across colonies, in-flight ship hull ownership accounting, plan-sized transport fleet scaling up to 25% of hourly Metal and Kryptonite income, and drift-triggered background path re-tuning. Boolean toggle.
- **`ai_resource_spare_pct` (AI-Planner Spare Resource Budget (%))**: Percentage of surplus planetary resources (after reserving the next waiting P1 AI-Planner build or research step) that Fleet Planner may spend on shipyard hulls and fleet unlock research. Accepts floats from 0.0 to 100.0.
- **`playstyle` (Playstyle Doctrine)**: Weights P2 research ordering and shipyard hull quotas toward offensive warships and combat/drive technologies (fleeter), compound mine ROI payback (miner), or a balanced empire progression (hybrid). Applies whenever research or shipyard automation is enabled.
- **`allow_multi_planet` (Multi-Planet Empire Planning)**: Evaluates construction queues, energy balance, warehouse caps, and fleet dispatch across every owned colony and Asteroid instead of restricting planning to the currently active planet. Respects SpaceInvasion Classic slot rules for normal planets, solar-only Asteroids (1 build slot), and isolated [RK] raid colonies.
- **`max_planets` (Max Empire Planets Cap)**: Caps total normal planets (owned colonies plus outbound COLONIZE flights) before halting autonomous Colony ship (s8) dispatches; SpaceInvasion Classic permits 1 homeworld plus up to 10 normal colonies (11 total, excluding Asteroids and [RK] raid colonies). Accepts integers from 1 to 50.
- **`min_colony_fields` (Minimum Colony Fields Target)**: Minimum buildable planet size required by the colony grader and P2 COLONIZE selector, favoring Prime Mid-Orbit positions 7–12 capable of rolling 300+ fields so late-game mines and Fusion plants never stall on slot exhaustion. Accepts integers from 50 to 600 fields.
- **`min_free_fields_warning` (Low Free Fields Advisory Threshold)**: Raises a P3 non-executable colony space advisory (executable_now=False) and steers power upgrades toward compact Fusion plant levels once remaining buildable slots (fields_max - fields_used) drop at or below this buffer. Accepts integers from 1 to 1000 fields.

## Economy, Power & Storage Automation

Blast furnace (b2) 2:1 Pig iron throttling, planetary power generation policy, warehouse overflow guards, research/shipyard queues, and free IDT spins.

| Setting | Type | Default | Bounds / Choices | Summary |
| :--- | :--- | :--- | :--- | :--- |
| `auto_throttle_blast_furnace` | `bool` | `true` | `true` \| `false` | Automatically toggle Blast furnace (b2) 0%/100% to avoid Pig iron starvation. |
| `blast_pig_iron_max_share` | `float` | `0.7` | `0.1` .. `1.0` | Maximum share of gross Pig iron output that Blast furnace (b2) may smelt. |
| `merchant_mode` | `bool` | `false` | `true` \| `false` | Score mine payback with NPC Merchant ratios (6:2.5:3:1) and skip Blast furnace. |
| `forbid_planetary_solar` | `bool` | `false` | `true` \| `false` | Rely exclusively on Fusion plant (b5) on normal planets (Asteroids exempt). |
| `hot_planet_solar_temp_threshold` | `int` | `40` | `-273` .. `1000` °C | Minimum planet temperature before emergency power considers orbital Solar cells. |
| `storage_warn_ratio` | `float` | `0.85` | `0.1` .. `0.99` | Warehouse fill fraction triggering P1 storage upgrades (b11-b14). |
| `allow_research` | `bool` | `true` | `true` \| `false` | Enable P2 account-wide RESEARCH starts from the highest Research lab. |
| `allow_fleet_research` | `bool` | `true` | `true` \| `false` | Spend spare Fleet Planner budget on ship unlock prerequisites and r3-r15 targets. |
| `allow_shipyard` | `bool` | `true` | `true` \| `false` | Enable P2 Weapons factory (b9) ship builds toward shipyard_targets quotas. |
| `allow_transmitter` | `bool` | `false` | `true` \| `false` | Claim the free daily IDT spin once per Europe/Berlin day (never spends Urplasma). |
| `shipyard_targets` | `int_map` | `11 entries` | `0` .. `1000000000` ships | Per-ship-slug target hull counts maintained by autonomous shipyard queues. |
| `fleet_research_targets` | `int_map` | `{}` | `0` .. `100` level | Per-research-key target levels pursued using Fleet Planner spare resources. |

- **`auto_throttle_blast_furnace` (Auto-Throttle Blast Furnace (b2))**: Automatically throttles Blast furnace (b2) to 0% via P1 THROTTLE_BUILDING when 2:1 Pig iron smelting starves frontier Pig iron upgrades or when Metal storage is 98%+ full while Pig iron storage has headroom, then restores 100% when Metal is needed. In SpaceInvasion Classic, b2 consumes 2 Pig iron per 1 Metal and is not boosted by Mining technology (r1).
- **`blast_pig_iron_max_share` (Max Blast Furnace Pig Iron Share)**: Maximum share of gross Pig iron mine (b1) hourly output that Blast furnace (b2) may consume through its 2:1 smelting ratio before the planner holds further b2 levels or pauses smelting to preserve positive net Pig iron income. Accepts float ratios from 0.10 to 1.00.
- **`merchant_mode` (Merchant Exchange Mode (Skip b2))**: Evaluates mine payback ROI using NPC Merchant trade ratios (6 : 2.5 : 3 : 1 Pig iron / Metal / Kryptonite / Spice) instead of standard player ratios (4 : 2 : 2 : 1) and skips Blast furnace (b2) construction when Metal is sourced via trader swaps. Boolean toggle.
- **`forbid_planetary_solar` (Forbid Solar Power on Normal Planets)**: Forbids Thermosolar plant (b6) and combat-destructible orbital Solar cells (s9) on normal planets—relying exclusively on Fusion plant (b5) scaled by Energy technology (r2) and scheduling P1 step-down DEMOLISH_BUILDING of existing b6 levels when spare energy covers the loss. Asteroids remain exempt because SpaceInvasion Classic restricts Asteroids to solar power only.
- **`hot_planet_solar_temp_threshold` (Hot Planet Solar Temperature Threshold (°C))**: Minimum colony temperature in °C required before emergency power recovery considers orbital Solar cells (s9) on tight-field normal planets when forbid_planetary_solar is disabled; hot inner-orbit slots boost solar yield whereas cold outer slots boost Spice mine (b4) output. Accepts integers from -273 to 1000 °C.
- **`storage_warn_ratio` (Warehouse Overflow Warning Ratio)**: Schedules P1 warehouse upgrades (b11–b14: Pig iron, Metal, Kryptonite, or Spice warehouse) as soon as stored stock divided by warehouse capacity reaches this threshold, preventing SpaceInvasion mines from halting extraction at 100% cap. Accepts float ratios from 0.10 to 0.99.
- **`allow_research` (Autonomous Research Queue)**: Queues P2 account-wide RESEARCH upgrades (r1–r15) from the colony with the highest Research lab (b8) level whenever the global research slot is idle; research duration scales with (b8 + 1) * speedup and is unaffected by Microsystem (b17). Boolean toggle.
- **`allow_fleet_research` (Fleet & Ship Unlock Research (Spare Budget))**: Spends Fleet Planner soft spare resources (ai_resource_spare_pct, after reserving the next waiting P1 AI-Planner step) on P2 RESEARCH upgrades to auto-unlock recursive prerequisites for configured shipyard_targets hulls and reach fleet_research_targets levels (r3–r15). Holds the Research lab whenever AI-Planner has a ready or next-waiting P1 research step; requires allow_research enabled.
- **`allow_shipyard` (Autonomous Shipyard Construction)**: Enables P2 BUILD_SHIP orders at Weapons factory (b9) to maintain configured shipyard_targets quotas (Spy probe s10, Transports, Recycler s11, Colony ship s8, and combat hulls), where per-unit build time divides by b9 * 2^b17 * speedup. Boolean toggle.
- **`allow_transmitter` (Daily Free Interdimensional Transmitter Spin)**: Schedules P2 ACTIVATE_TRANSMITTER once per Europe/Berlin server day to claim the free daily Interdimensional Transmitter spin, strictly guarded by _freeSpins >= 1 and idtSrc == 1 so zero paid Urplasma is ever spent. Boolean toggle.
- **`shipyard_targets` (Shipyard Hull Quotas)**: Mapping of canonical hyphenated ship slugs (for example 'spy-probe', 'recycler', 'colony-ship', 'battleship') to desired empire hull counts. Accepts integer values from 0 to 1,000,000,000 per ship slug.
- **`fleet_research_targets` (Fleet Research Target Levels)**: Mapping of research keys (r3–r15) to desired minimum levels pursued when allow_fleet_research is enabled. Setting a key to 0 removes its target. Accepts integer levels from 0 to 100.

## Galaxy Scanner & Sweep Pacing

Spiral solar-system recon, the smart 2-phase scanner (exploratory 1-400 coverage, then a weighted refresh by proximity and attack history), multi-page 2D Galaxy si.navigate sweeps, delay and jitter between clicks, and free-slot colonization.

| Setting | Type | Default | Bounds / Choices | Summary |
| :--- | :--- | :--- | :--- | :--- |
| `allow_galaxy_scan` | `bool` | `false` | `true` \| `false` | Enable spiral SCAN_GALAXY recon and background 2D Galaxy sweeps. |
| `galaxy_scan_block_other_actions` | `bool` | `true` | `true` \| `false` | Keep Chrome exclusively on internalGalaxy during multi-page sweeps (P0 SAVE exempt). |
| `galaxy_sweep_pages` | `int` | `100` | `1` .. `400` pages | Number of solar-system pages visited per Galaxy sweep cycle. |
| `galaxy_sweep_interval_seconds` | `int` | `270` | `30` .. `86400` s | Maximum seconds between automatic background Galaxy sweeps. |
| `galaxy_scan_click_delay_seconds` | `float` | `1.5` | `0.2` .. `60.0` s | Base delay in seconds between consecutive Galaxy page navigation clicks. |
| `galaxy_scan_click_jitter_seconds` | `float` | `0.6` | `0.0` .. `30.0` s | Uniform +/- jitter offset around the base Galaxy click delay. |
| `galaxy_scan_explore_first` | `bool` | `true` | `true` \| `false` | Discover all unscanned systems in owned galaxies before weighted refresh. |
| `galaxy_scan_refresh_pct` | `int` | `0` | `0` .. `100` % | Percentage of each sweep batch devoted to refreshing already-scanned sectors. |
| `galaxy_scan_all_galaxies` | `bool` | `false` | `true` \| `false` | Expand smart galaxy sweeps beyond owned galaxies across all 14 galaxies. |
| `galaxy_scan_near_weight` | `float` | `2.0` | `0.0` .. `20.0` | Phase 2 targeted-refresh multiplier on circular proximity to your closest colony. |
| `galaxy_scan_attack_weight` | `float` | `3.0` | `0.0` .. `20.0` | Phase 2 targeted-refresh multiplier on raid-history affinity per sector and owner. |
| `allow_colonize` | `bool` | `false` | `true` \| `false` | Launch Colony ships (s8) on verified 300+-field candidates up to max_planets. |

- **`allow_galaxy_scan` (Autonomous Galaxy Scanner & Background Sweep)**: Enables P2 outward spiral SCAN_GALAXY system recon (1800s per-system cooldown) and background randomized 2D Galaxy sweeps that index yellow inactive farms, Spacia NPC planets, orbital rubble fields, and free colony slots. Boolean toggle.
- **`galaxy_scan_block_other_actions` (Block Other Browser Actions During Galaxy Scan)**: Keeps Chrome exclusively on internalGalaxy during multi-page Galaxy sweeps so step_galaxy_system arrow chains are never interrupted by Commander ticks, economy syncs, or manual live page jumps. Defers autonomous ticks until the sweep finishes, warns on manual live browser actions mid-scan, and still lets Pause, disabling Galaxy Scan, or P0 emergency Escape Flights (SAVE) interrupt immediately. Boolean toggle.
- **`galaxy_sweep_pages` (Galaxy Sweep Pages per Cycle)**: Number of solar system pages (gal:sun) visited per randomized 2D Galaxy sweep cycle (and default page count for the Sweep 100 Pages action button) to keep sector intelligence fresh across neighboring systems. Accepts integers from 1 to 400 pages.
- **`galaxy_sweep_interval_seconds` (Max Background Galaxy Sweep Interval (s))**: Maximum interval in seconds between automatic background multi-page Galaxy sweep runs when allow_galaxy_scan is enabled, jittered so sector recon refreshes regularly without starving build or fleet ticks. Accepts integers from 30 to 86400 seconds.
- **`galaxy_scan_click_delay_seconds` (Time Between Galaxy Scan Clicks (s))**: Base delay in seconds waited between consecutive step_galaxy_system / si.navigate page clicks during multi-page Galaxy sweeps to stay safely under SpaceInvasion's 30-page-view rate-limit cooldown. Accepts floats from 0.2 to 60.0 seconds.
- **`galaxy_scan_click_jitter_seconds` (Random Jitter Between Galaxy Scan Pages (s))**: Uniform random +/-jitter offset in seconds added around galaxy_scan_click_delay_seconds on each Galaxy sweep page step (floored at 0.2s total wait) so page navigation cadence stays organic rather than fixed-interval. Accepts floats from 0.0 to 30.0 seconds.
- **`galaxy_scan_explore_first` (Smart Scan Phase 1: Explore Unscanned Systems First)**: Phase 1 (exploratory): Galaxy sweeps and SCAN_GALAXY visit every never-scanned solar system (1–400) in each galaxy where you own a non-raid colony before they revisit any system. Systems are ordered outward from your nearest colony so si.navigate steps stay contiguous. Once coverage reaches 100%, or right away when this is off, sweeps switch to Phase 2: weighted refresh by proximity, attack history, and staleness. Boolean toggle.
- **`galaxy_scan_refresh_pct` (Smart Scan Refresh vs Discovery Split (%))**: Percentage of each galaxy sweep batch allocated to refreshing already-scanned sectors (by proximity, raid affinity, debris/targets, and staleness), while the remaining (100 - refresh)% discovers never-scanned systems. Unused quota spills automatically to the other pool. Accepts integers from 0 to 100.
- **`galaxy_scan_all_galaxies` (Smart Scan Scope: Discover Across All 14 Galaxies (G1–G14))**: When enabled, smart galaxy sweeps and the Sectors Scan Queue expand beyond galaxies where you already own a colony to discover and refresh all 14 galaxies (G1–G14) outward from your colonies (nearest neighbor galaxies first). When disabled, sweeps stay inside owned galaxies unless a mixed Refresh/Discovery split (1–99%) has already finished 100% of owned galaxies. Boolean toggle.
- **`galaxy_scan_near_weight` (Smart Scan Phase 2: Near-Sector Proximity Weight)**: Phase 2 (targeted_refresh) multiplier on the proximity score of each (galaxy, system) sector. Proximity uses the circular system distance to your closest non-raid colony, min(|s - own_s|, 400 - |s - own_s|), with a penalty for other galaxies. Higher values refresh nearby sectors more often; 0.0 ignores distance. Accepts floats from 0.0 to 20.0.
- **`galaxy_scan_attack_weight` (Smart Scan Phase 2: Frequent-Attack Player & Sector Weight)**: Phase 2 (targeted_refresh) multiplier on attack-history affinity from raid_history. Combines direct raids and loot in a GG:SSS sector with per-owner frequency, adding debris, yellow inactive, Spacia, and 300+ free-slot value multiplied by scan staleness. Higher values keep frequent farm targets fresh; 0.0 ignores attack history. Accepts floats from 0.0 to 20.0.
- **`allow_colonize` (Autonomous Free-Slot Colonization)**: Probes non-discarded free slots (Prime Mid-Orbit 7–12 first) until qualified 300+-field candidates cover max_planets minus allocated colonies, then launches stationed Colony ships (s8) on verified 300+-field candidates when 1 + b10 fleet slots permit. Boolean toggle.

## Espionage, Raiding & Fleet Safety

Reserved spy probe flight seats, spy origin colony selection, zero-rapidfire 1:1 combat simulator gates, 3/24h Bash tracking, and P0 SAVE Escape flights.

| Setting | Type | Default | Bounds / Choices | Summary |
| :--- | :--- | :--- | :--- | :--- |
| `allow_raids` | `bool` | `false` | `true` \| `false` | Enable P1 SPY scouting and P2 ATTACK/RECYCLE waves against farms and debris. |
| `allow_transport` | `bool` | `false` | `true` \| `false` | Dispatch P1 TRANSPORT flights strictly between owned planets for shortages. |
| `reserved_spy_slots` | `int` | `1` | `0` .. `10` slots | Fleet base (1 + b10) concurrent flight slots held exclusively for SPY missions. |
| `spy_reserved_slots_only` | `bool` | `false` | `true` \| `false` | Cap SPY launches per planet to reserved_spy_slots without spilling into extra seats. |
| `spy_dispatch_planet_id` | `planet_id` | `0` | `0` (auto) or colony `planet_id` | Lock SPY probe launches to one owned colony planet_id (0 = auto-select). |
| `allow_pvp_in_az` | `bool` | `false` | `true` \| `false` | Permit SPY/ATTACK against active players in your AZ with 3/24h Bash enforcement. |
| `combat_safety_margin` | `float` | `1.4` | `1.0` .. `1000.0` x | Minimum attacker-to-defender effective power ratio in the 1:1 combat simulator. |
| `raid_min_loot_spice_eq` | `float` | `1500.0` | `0.0` .. `1000000000000.0` SpEq | Minimum expected 50% raid plunder in Spice-equivalent units. |
| `attack_min_res_per_hour` | `float` | `0.0` | `0.0` .. `1000000000000.0` res/h | Minimum expected raid plunder per round-trip flight hour (0 disables filter). |
| `recycle_min_res_per_hour` | `float` | `0.0` | `0.0` .. `1000000000000.0` res/h | Minimum expected debris recovery per round-trip Recycler flight hour (0 disables). |
| `allow_fleetsave` | `bool` | `false` | `true` \| `false` | Launch immediate P0 SAVE Escape flights when incoming hostile attacks are detected. |
| `fleet_save_fuel_reserve_spice` | `int` | `500` | `0` .. `1000000000000` Spice | Planetary Spice held back as propellant before packing ESCAPE_FLIGHT cargo. |

- **`allow_raids` (Autonomous Espionage & Inactive/Spacia Raids)**: Enables P1 SPY scouting (s10 on inhabited planets and Spacia, refreshing intel older than 4h) and P2 ATTACK/RECYCLE harvest waves against yellow inactives, Spacia NPC planets, and rubble fields ranked by total resources per round-trip flight time (res/h), bounded by 1 + b10 concurrent fleet slots. Boolean toggle.
- **`allow_transport` (Autonomous Inter-Planet Resource Transport)**: Dispatches P1 TRANSPORT flights (s3/s2/s1) strictly between your own planets to supply P1/P2 resource shortfalls and bootstrap young colonies from surplus planets (respecting Spice fuel reserves and 85% recipient storage caps). Runs at Fleet Priority #2: after SPY (#1) and ahead of COLONIZE (#3) and ATTACK/RECYCLE (#4). Boolean toggle.
- **`reserved_spy_slots` (Reserved Spy Fleet Slots)**: Number of Fleet base (1 + b10) concurrent flight seats held exclusively for SPY probe missions (#1) whenever ready spy targets and stationed s10 probes exist, enforcing the 4-tier fleet priority SPY (#1) → TRANSPORT (#2) → COLONIZE (#3) → ATTACK & RECYCLE (#4). Accepts integers from 0 to 10.
- **`spy_reserved_slots_only` (Restrict Spy Probes to Reserved Slot(s) Only)**: When enabled, SPY probe launches on each planet are strictly capped to the Reserved Spy Fleet Slots count (reserved_spy_slots) and will never spill into extra unreserved fleet slots—keeping remaining Fleet base (1 + b10) slots free for TRANSPORT, COLONIZE, ATTACK, and RECYCLE flights. Boolean toggle.
- **`spy_dispatch_planet_id` (Designated Spy Dispatch Colony)**: Locks all SPY probe launches to a specific owned non-raid colony planet_id (holding SPY and prioritizing s10 construction there if its probe stock hits zero), or set 0 to automatically select the active or closest colony carrying stationed Spy probes (s10). Accepts 0 (Auto) or an owned colony ID.
- **`allow_pvp_in_az` (Allow Active-Player PvP Raids Inside Attack Zone)**: Permits P2 SPY and ATTACK missions against active (light_red) players within your SpaceInvasion Attack Zone (AZ) tier, strictly enforcing the 3 attacks per 24 hours Bash rule per target planet and 6-hour cooldowns on newbie, vacation, or out-of-attackzone targets. Boolean toggle.
- **`combat_safety_margin` (1:1 Combat Simulator Safety Margin)**: Minimum attacker-to-defender effective power ratio required in SpaceInvasion Classic's zero-rapidfire 1:1 combat simulator (incorporating Weapons r11, Shield r12, and Defense r13 tech multipliers) before authorizing any ATTACK wave. Accepts floats from 1.0 to 1000.0.
- **`raid_min_loot_spice_eq` (Minimum Raid Plunder Threshold (Spice Eq))**: Minimum expected 50% raid plunder converted into Spice-equivalent units (4 : 2 : 2 : 1 standard value) required before committing cargo hulls, Spice flight fuel, and a 1 + b10 fleet slot to an ATTACK mission. Accepts floats of 0 or higher.
- **`attack_min_res_per_hour` (Min Attack Yield (res/h))**: Minimum expected total plunder throughput per round-trip flight hour (Pig iron + Metal + Kryptonite + Spice divided by round-trip hours) required from a candidate origin planet before triggering an ATTACK mission. Origins below this threshold are skipped so closer colonies can qualify; set to 0 to disable. Tutorial fight missions remain exempt.
- **`recycle_min_res_per_hour` (Min Recycle Yield (res/h))**: Minimum expected debris recovery throughput per round-trip Recycler flight hour (expected collectible rubble divided by round-trip hours) required from a candidate origin planet before triggering a RECYCLE mission. Distant origins below this threshold are skipped so closer Recycler hubs can qualify; set to 0 to disable. Tutorial recycle missions remain exempt.
- **`allow_fleetsave` (P0 Automatic Hostile Escape Flight (SAVE))**: Launches an immediate P0 ESCAPE_FLIGHT (SAVE mission) whenever an incoming hostile fleet targets a colony, evacuating stationed ships and packing cargo in Spice → Kryptonite → Metal → Pig iron priority order; SpaceInvasion Classic Escape flights are invisible to Asteroid Galaxy scanners. Boolean toggle.
- **`fleet_save_fuel_reserve_spice` (Escape Flight Spice Fuel Reserve)**: Quantity of planetary Spice held back on the origin colony before loading ESCAPE_FLIGHT cargo holds so stationed fleets always retain sufficient propellant to launch the SAVE flight and follow-up missions. Accepts integers of 0 or higher.

## Runtime Pacing & Evolutionary Search

Autonomous Commander tick cadence, rolling hourly DOM mutation budget, page-view rate ceiling, and evolutionary search hyperparameters.

| Setting | Type | Default | Bounds / Choices | Summary |
| :--- | :--- | :--- | :--- | :--- |
| `action_batch_interval_seconds` | `int` | `120` | `5` .. `86400` s | Minimum pause in seconds between autonomous Commander tick batches. |
| `max_actions_per_hour` | `int` | `60` | `1` .. `100000` ops/hr | Rolling 60-minute cap on live browser mutations executed by GameApiBridge. |
| `page_view_limit_per_minute` | `int` | `60` | `6` .. `600` views/min | Rolling 60-second local browser page-view budget enforced by PageViewLimiter. |
| `ga_population_size` | `int` | `24` | `4` .. `200` genomes | Candidate chromosomes maintained per evolutionary search generation. |
| `ga_generations` | `int` | `12` | `1` .. `200` cycles | Number of selection, crossover, and mutation cycles per evolutionary search pass. |
| `ga_mutation_rate` | `float` | `0.25` | `0.0` .. `1.0` | Per-gene mutation probability during evolutionary offspring reproduction. |

- **`action_batch_interval_seconds` (Minimum Batch & Snapshot Refresh Interval (s))**: Minimum pause in seconds between autonomous Commander execution batches and full Chrome DOM snapshot refreshes when no active building or fleet timer completes sooner, pacing navigation under SpaceInvasion page-view limits. Accepts integers from 5 to 86400 seconds.
- **`max_actions_per_hour` (Max Live Actions per Rolling Hour)**: Rolling 60-minute action cap limiting total live browser mutations (BUILD, RESEARCH, BUILD_SHIP, THROTTLE_BUILDING, and fleet dispatches) executed by GameApiBridge to prevent runaway click loops. Accepts integers from 1 to 100000 actions/hour.
- **`page_view_limit_per_minute` (Max Page Views per Rolling Minute)**: Rolling 60-second local browser page-view budget enforced by PageViewLimiter across Commander ticks and interactive commands (reserving up to 6 slots for interactive bursts and scaling inter-page pacing delay proportionally). BitMeUp's game server enforces its own server-side throttle around 30–35 views/min with a 45s cooldown if exceeded. Accepts integers from 6 to 600 views/minute.
- **`ga_population_size` (Evolutionary Population Size)**: Number of candidate chromosomes maintained per evolutionary generation in offline/batch planner search; larger populations explore broader mine, Fusion power, and tech orders at higher CPU cost. Accepts integers from 4 to 200.
- **`ga_generations` (Evolutionary Generations)**: Number of tournament-selection, crossover, and mutation cycles executed per evolutionary search pass to converge on lower-wait, higher-throughput build plans. Accepts integers from 1 to 200.
- **`ga_mutation_rate` (Evolutionary Genome Mutation Rate)**: Per-gene mutation probability applied during evolutionary offspring reproduction to diversify build-order search and escape local optima such as warehouse or power bottlenecks. Accepts floats from 0.00 to 1.00.
