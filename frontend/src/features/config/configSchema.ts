export type ConfigControlKind = "select" | "number" | "text-seed" | "checkbox" | "planet-select";

export interface SelectOptionSpec {
  value: string;
  label: string;
}

export interface ConfigItemSpec {
  key: string;
  id: string;
  title: string;
  boundsLabel: string;
  description: string;
  control: ConfigControlKind;
  defaultValue: string | number | boolean | null;
  options?: SelectOptionSpec[];
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
}

export interface ConfigGroupSpec {
  id: string;
  headingId: string;
  title: string;
  subtitle: string;
  items: ConfigItemSpec[];
}

export const CONFIG_GROUPS: ConfigGroupSpec[] = [
  {
    id: "doctrine",
    headingId: "cfg-grp-doctrine",
    title: "Planner Engine & Empire Doctrine",
    subtitle:
      "Core planning engine selection, playstyle doctrine weighting, multi-planet scheduling scope, and colony expansion field thresholds.",
    items: [
      {
        key: "planner_mode",
        id: "cfg-planner_mode",
        title: "Planner Mode",
        boundsLabel: "default: priority · priority | ga_nn",
        description:
          "Selects between the deterministic rule-based P0–P3 heuristic pipeline (priority) and evolutionary chromosome search blended with the 46-feature value network (ga_nn). Saved immediately to SQLite so both Game API and Commander ticks share the active planner.",
        control: "select",
        defaultValue: "priority",
        options: [
          { value: "priority", label: "priority — Rule-Based Heuristic" },
          { value: "ga_nn", label: "ga_nn — GA + Neural Network" },
        ],
      },
      {
        key: "playstyle",
        id: "cfg-playstyle",
        title: "Playstyle Doctrine",
        boundsLabel: "default: fleeter · fleeter | hybrid | miner",
        description:
          "Weights P2 research ordering and shipyard hull quotas toward offensive warships and combat/drive technologies (fleeter), compound mine ROI payback (miner), or a balanced empire progression (hybrid). Applies whenever research or shipyard automation is enabled.",
        control: "select",
        defaultValue: "fleeter",
        options: [
          { value: "fleeter", label: "fleeter — Combat & Techs" },
          { value: "hybrid", label: "hybrid — Balanced" },
          { value: "miner", label: "miner — Economy ROI" },
        ],
      },
      {
        key: "allow_multi_planet",
        id: "cfg-allow_multi_planet",
        title: "Multi-Planet Empire Planning",
        boundsLabel: "default: true · boolean",
        description:
          "Evaluates construction queues, energy balance, warehouse caps, and fleet dispatch across every owned colony and Asteroid instead of restricting planning to the currently active planet. Respects SpaceInvasion Classic slot rules for normal planets, solar-only Asteroids (1 build slot), and isolated [RK] raid colonies.",
        control: "checkbox",
        defaultValue: true,
      },
      {
        key: "max_planets",
        id: "cfg-max_planets",
        title: "Max Empire Planets Cap",
        boundsLabel: "default: 11 · 1–50 planets",
        description:
          "Caps total normal planets (owned colonies plus outbound COLONIZE flights) before halting autonomous Colony ship (s8) dispatches; SpaceInvasion Classic permits 1 homeworld plus up to 10 normal colonies (11 total, excluding Asteroids and [RK] raid colonies). Accepts integers from 1 to 50.",
        control: "number",
        defaultValue: 11,
        min: 1,
        max: 50,
        step: 1,
      },
      {
        key: "min_colony_fields",
        id: "cfg-min_colony_fields",
        title: "Minimum Colony Fields Target",
        boundsLabel: "default: 300 · 50–600 fields",
        description:
          "Minimum buildable planet size required by the colony grader and P2 COLONIZE selector, favoring Prime Mid-Orbit positions 7–12 capable of rolling 300+ fields so late-game mines and Fusion plants never stall on slot exhaustion. Accepts integers from 50 to 600 fields.",
        control: "number",
        defaultValue: 300,
        min: 50,
        max: 600,
        step: 10,
      },
      {
        key: "min_free_fields_warning",
        id: "cfg-min_free_fields_warning",
        title: "Low Free Fields Advisory Threshold",
        boundsLabel: "default: 20 · 1–1000 fields",
        description:
          "Raises a P3 non-executable colony space advisory (executable_now=False) and steers power upgrades toward compact Fusion plant levels once remaining buildable slots (fields_max - fields_used) drop at or below this buffer. Accepts integers from 1 to 1000 fields.",
        control: "number",
        defaultValue: 20,
        min: 1,
        max: 1000,
        step: 1,
      },
    ],
  },
  {
    id: "economy",
    headingId: "cfg-grp-economy",
    title: "Economy, Power & Storage Automation",
    subtitle:
      "Blast furnace (b2) 2:1 Pig iron throttling, planetary power generation policy, warehouse overflow guards, research/shipyard queues, and free IDT spins.",
    items: [
      {
        key: "auto_throttle_blast_furnace",
        id: "cfg-auto_throttle_blast_furnace",
        title: "Auto-Throttle Blast Furnace (b2)",
        boundsLabel: "default: true · boolean",
        description:
          "Automatically throttles Blast furnace (b2) to 0% via P1 THROTTLE_BUILDING when 2:1 Pig iron smelting starves frontier Pig iron upgrades or when Metal storage is 98%+ full while Pig iron storage has headroom, then restores 100% when Metal is needed. In SpaceInvasion Classic, b2 consumes 2 Pig iron per 1 Metal and is not boosted by Mining technology (r1).",
        control: "checkbox",
        defaultValue: true,
      },
      {
        key: "blast_pig_iron_max_share",
        id: "cfg-blast_pig_iron_max_share",
        title: "Max Blast Furnace Pig Iron Share",
        boundsLabel: "default: 0.70 · 0.10–1.00",
        description:
          "Maximum share of gross Pig iron mine (b1) hourly output that Blast furnace (b2) may consume through its 2:1 smelting ratio before the planner holds further b2 levels or pauses smelting to preserve positive net Pig iron income. Accepts float ratios from 0.10 to 1.00.",
        control: "number",
        defaultValue: 0.7,
        min: 0.1,
        max: 1.0,
        step: 0.05,
      },
      {
        key: "merchant_mode",
        id: "cfg-merchant_mode",
        title: "Merchant Exchange Mode (Skip b2)",
        boundsLabel: "default: false · boolean",
        description:
          "Evaluates mine payback ROI using NPC Merchant trade ratios (6 : 2.5 : 3 : 1 Pig iron / Metal / Kryptonite / Spice) instead of standard player ratios (4 : 2 : 2 : 1) and skips Blast furnace (b2) construction when Metal is sourced via trader swaps. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
      {
        key: "forbid_planetary_solar",
        id: "cfg-forbid_planetary_solar",
        title: "Forbid Solar Power on Normal Planets",
        boundsLabel: "default: false · boolean",
        description:
          "Forbids Thermosolar plant (b6) and combat-destructible orbital Solar cells (s9) on normal planets—relying exclusively on Fusion plant (b5) scaled by Energy technology (r2) and scheduling P1 step-down DEMOLISH_BUILDING of existing b6 levels when spare energy covers the loss. Asteroids remain exempt because SpaceInvasion Classic restricts Asteroids to solar power only.",
        control: "checkbox",
        defaultValue: false,
      },
      {
        key: "hot_planet_solar_temp_threshold",
        id: "cfg-hot_planet_solar_temp_threshold",
        title: "Hot Planet Solar Temperature Threshold (°C)",
        boundsLabel: "default: 40 · -273 to 1000 °C",
        description:
          "Minimum colony temperature in °C required before emergency power recovery considers orbital Solar cells (s9) on tight-field normal planets when forbid_planetary_solar is disabled; hot inner-orbit slots boost solar yield whereas cold outer slots boost Spice mine (b4) output. Accepts integers from -273 to 1000 °C.",
        control: "number",
        defaultValue: 40,
        min: -273,
        max: 1000,
        step: 5,
      },
      {
        key: "storage_warn_ratio",
        id: "cfg-storage_warn_ratio",
        title: "Warehouse Overflow Warning Ratio",
        boundsLabel: "default: 0.85 · 0.10–0.99",
        description:
          "Schedules P1 warehouse upgrades (b11–b14: Pig iron, Metal, Kryptonite, or Spice warehouse) as soon as stored stock divided by warehouse capacity reaches this threshold, preventing SpaceInvasion mines from halting extraction at 100% cap. Accepts float ratios from 0.10 to 0.99.",
        control: "number",
        defaultValue: 0.85,
        min: 0.1,
        max: 0.99,
        step: 0.05,
      },
      {
        key: "allow_research",
        id: "cfg-allow_research",
        title: "Autonomous Research Queue",
        boundsLabel: "default: true · boolean",
        description:
          "Queues P2 account-wide RESEARCH upgrades (r1–r15) from the colony with the highest Research lab (b8) level whenever the global research slot is idle; research duration scales with (b8 + 1) * speedup and is unaffected by Microsystem (b17). Boolean toggle.",
        control: "checkbox",
        defaultValue: true,
      },
      {
        key: "allow_shipyard",
        id: "cfg-allow_shipyard",
        title: "Autonomous Shipyard Construction",
        boundsLabel: "default: true · boolean",
        description:
          "Enables P2 BUILD_SHIP orders at Weapons factory (b9) to maintain configured shipyard_targets quotas (Spy probe s10, Transports, Recycler s11, Colony ship s8, and combat hulls), where per-unit build time divides by b9 * 2^b17 * speedup. Boolean toggle.",
        control: "checkbox",
        defaultValue: true,
      },
      {
        key: "allow_transmitter",
        id: "cfg-allow_transmitter",
        title: "Daily Free Interdimensional Transmitter Spin",
        boundsLabel: "default: false · boolean",
        description:
          "Schedules P2 ACTIVATE_TRANSMITTER once per Europe/Berlin server day to claim the free daily Interdimensional Transmitter spin, strictly guarded by _freeSpins >= 1 and idtSrc == 1 so zero paid Urplasma is ever spent. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
    ],
  },
  {
    id: "galaxy",
    headingId: "cfg-grp-galaxy",
    title: "Galaxy Scanner & Sweep Pacing",
    subtitle:
      "Spiral solar-system recon, the smart 2-phase scanner (exploratory 1–400 coverage, then a weighted refresh by proximity and attack history), multi-page 2D Galaxy si.navigate sweeps, delay and jitter between clicks, and free-slot colonization.",
    items: [
      {
        key: "allow_galaxy_scan",
        id: "cfg-allow_galaxy_scan",
        title: "Autonomous Galaxy Scanner & Background Sweep",
        boundsLabel: "default: false · boolean",
        description:
          "Enables P2 outward spiral SCAN_GALAXY system recon (1800s per-system cooldown) and background randomized 2D Galaxy sweeps that index yellow inactive farms, Spacia NPC planets, orbital rubble fields, and free colony slots in SQLite. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
      {
        key: "galaxy_sweep_pages",
        id: "cfg-galaxy_sweep_pages",
        title: "Galaxy Sweep Pages per Cycle",
        boundsLabel: "default: 100 · 1–400 pages",
        description:
          "Number of solar system pages (gal:sun) visited per randomized 2D Galaxy sweep cycle (and default page count for the Sweep 100 Pages action button) to keep sector intelligence fresh across neighboring systems. Accepts integers from 1 to 400 pages.",
        control: "number",
        defaultValue: 100,
        min: 1,
        max: 400,
        step: 10,
      },
      {
        key: "galaxy_sweep_interval_seconds",
        id: "cfg-galaxy_sweep_interval_seconds",
        title: "Max Background Galaxy Sweep Interval (s)",
        boundsLabel: "default: 270 · 30–86400 s",
        description:
          "Maximum interval in seconds between automatic background multi-page Galaxy sweep runs when allow_galaxy_scan is enabled, jittered so sector recon refreshes regularly without starving build or fleet ticks. Accepts integers from 30 to 86400 seconds.",
        control: "number",
        defaultValue: 270,
        min: 30,
        max: 86400,
        step: 15,
      },
      {
        key: "galaxy_scan_click_delay_seconds",
        id: "cfg-galaxy_scan_click_delay_seconds",
        title: "Time Between Galaxy Scan Clicks (s)",
        boundsLabel: "default: 1.5 · 0.2–60.0 s",
        description:
          "Base delay in seconds waited between consecutive step_galaxy_system / si.navigate page clicks during multi-page Galaxy sweeps to stay safely under SpaceInvasion's 30-page-view rate-limit cooldown. Accepts floats from 0.2 to 60.0 seconds.",
        control: "number",
        defaultValue: 1.5,
        min: 0.2,
        max: 60,
        step: 0.1,
      },
      {
        key: "galaxy_scan_click_jitter_seconds",
        id: "cfg-galaxy_scan_click_jitter_seconds",
        title: "Random Jitter Between Galaxy Scan Pages (s)",
        boundsLabel: "default: 0.6 · 0.0–30.0 s",
        description:
          "Uniform random +/-jitter offset in seconds added around galaxy_scan_click_delay_seconds on each Galaxy sweep page step (floored at 0.2s total wait) so page navigation cadence stays organic rather than fixed-interval. Accepts floats from 0.0 to 30.0 seconds.",
        control: "number",
        defaultValue: 0.6,
        min: 0,
        max: 30,
        step: 0.1,
      },
      {
        key: "galaxy_scan_explore_first",
        id: "cfg-galaxy_scan_explore_first",
        title: "Smart Scan Phase 1: Explore Unscanned Systems First",
        boundsLabel: "default: true · boolean",
        description:
          "Phase 1 (exploratory): Galaxy sweeps and SCAN_GALAXY visit every never-scanned solar system (1–400) in each galaxy where you own a non-raid colony before they revisit any system. Systems are ordered outward from your nearest colony so si.navigate steps stay contiguous. Once coverage reaches 100% (explored_systems == total_systems), or right away when this is off, sweeps switch to Phase 2: a weighted refresh that ranks sectors by proximity, attack history, and staleness. Boolean toggle.",
        control: "checkbox",
        defaultValue: true,
      },
      {
        key: "galaxy_scan_refresh_pct",
        id: "cfg-galaxy_scan_refresh_pct",
        title: "Smart Scan Refresh vs Discovery Split (%)",
        boundsLabel: "default: 0% · 0–100%",
        description:
          "Percentage of each galaxy sweep batch allocated to refreshing already-scanned sectors (by proximity, raid affinity, debris/targets, and staleness), while the remaining (100 − refresh)% discovers never-scanned systems. Set 0% for pure discovery first, 100% for refresh-only, or an intermediate split (for example 30% refresh / 70% discovery) to keep nearby farm sectors fresh while expanding universe coverage. Unused quota spills automatically to the other pool. Accepts integers from 0 to 100.",
        control: "number",
        defaultValue: 0,
        min: 0,
        max: 100,
        step: 5,
      },
      {
        key: "galaxy_scan_all_galaxies",
        id: "cfg-galaxy_scan_all_galaxies",
        title: "Smart Scan Scope: Discover Across All 14 Galaxies (G1–G14)",
        boundsLabel: "default: false · boolean",
        description:
          "When enabled, smart galaxy sweeps and the Sectors Scan Queue expand beyond galaxies where you already own a colony to discover and refresh all 14 galaxies (G1–G14) outward from your colonies (nearest neighbor galaxies first). When disabled, sweeps stay inside owned galaxies unless a mixed Refresh/Discovery split (1–99%) has already finished 100% of owned galaxies. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
      {
        key: "galaxy_scan_near_weight",
        id: "cfg-galaxy_scan_near_weight",
        title: "Smart Scan Phase 2: Near-Sector Proximity Weight",
        boundsLabel: "default: 2.0 · 0.0–20.0",
        description:
          "Phase 2 (targeted_refresh) multiplier on the proximity score of each (galaxy, system) sector. Proximity uses the circular system distance to your closest non-raid colony, min(|s − own_s|, 400 − |s − own_s|), with a penalty for other galaxies. Higher values refresh nearby sectors (cheap raids, short Spy probe flights) more often; 0.0 ignores distance. Accepts floats from 0.0 to 20.0.",
        control: "number",
        defaultValue: 2.0,
        min: 0,
        max: 20,
        step: 0.5,
      },
      {
        key: "galaxy_scan_attack_weight",
        id: "cfg-galaxy_scan_attack_weight",
        title: "Smart Scan Phase 2: Frequent-Attack Player & Sector Weight",
        boundsLabel: "default: 3.0 · 0.0–20.0",
        description:
          "Phase 2 (targeted_refresh) multiplier on attack-history affinity, read from SQLite raid_history. It combines direct raids and loot in a GG:SSS sector with per-owner frequency: every sector where a player you attack often owns a planet gets boosted. Debris, yellow inactive, Spacia, and 300+ free-slot value add a secondary score, and the total is multiplied by how stale the scan is. Higher values keep frequent farm targets fresh; 0.0 ignores attack history. Accepts floats from 0.0 to 20.0.",
        control: "number",
        defaultValue: 3.0,
        min: 0,
        max: 20,
        step: 0.5,
      },
      {
        key: "allow_colonize",
        id: "cfg-allow_colonize",
        title: "Autonomous Free-Slot Colonization",
        boundsLabel: "default: false · boolean",
        description:
          "Probes non-discarded free slots (Prime Mid-Orbit 7–12 first) until qualified 300+-field candidates cover max_planets minus allocated colonies, then launches stationed Colony ships (s8) on verified 300+-field candidates when 1 + b10 fleet slots permit. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
    ],
  },
  {
    id: "combat",
    headingId: "cfg-grp-combat",
    title: "Espionage, Raiding & Fleet Safety",
    subtitle:
      "Reserved spy probe flight seats, spy origin colony selection, zero-rapidfire 1:1 combat simulator gates, 3/24h Bash tracking, and P0 SAVE Escape flights.",
    items: [
      {
        key: "allow_raids",
        id: "cfg-allow_raids",
        title: "Autonomous Espionage & Inactive/Spacia Raids",
        boundsLabel: "default: false · boolean",
        description:
          "Enables closest-first P2 SPY scouting (s10 on inhabited planets and Spacia, refreshing intel older than 4h) and ATTACK plunder waves against yellow inactives and Spacia NPC planets (exempt from the 3/24h Bash cap unless server-blocked), bounded by 1 + b10 concurrent fleet slots. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
      {
        key: "reserved_spy_slots",
        id: "cfg-reserved_spy_slots",
        title: "Reserved Spy Fleet Slots",
        boundsLabel: "default: 1 · 0–10 slots",
        description:
          "Number of Fleet base (1 + b10) concurrent flight seats held exclusively for SPY probe missions whenever ready spy targets and stationed s10 probes exist, holding non-SPY ATTACK, RECYCLE, and COLONIZE dispatches at max_fleet_slots - eff_res so espionage never stalls behind slow raids. Accepts integers from 0 to 10.",
        control: "number",
        defaultValue: 1,
        min: 0,
        max: 10,
        step: 1,
      },
      {
        key: "spy_dispatch_planet_id",
        id: "cfg-spy_dispatch_planet_id",
        title: "Designated Spy Dispatch Colony",
        boundsLabel: "default: 0 (Auto) · colony planet_id",
        description:
          "Locks all SPY probe launches to a specific owned non-raid colony planet_id (holding SPY and prioritizing s10 construction there if its probe stock hits zero), or set 0 to automatically select the active or closest colony carrying stationed Spy probes (s10). Accepts 0 (Auto) or an owned colony ID.",
        control: "planet-select",
        defaultValue: 0,
      },
      {
        key: "allow_pvp_in_az",
        id: "cfg-allow_pvp_in_az",
        title: "Allow Active-Player PvP Raids Inside Attack Zone",
        boundsLabel: "default: false · boolean",
        description:
          "Permits P2 SPY and ATTACK missions against active (light_red) players within your SpaceInvasion Attack Zone (AZ) tier, strictly enforcing the 3 attacks per 24 hours Bash rule per target planet and 6-hour cooldowns on newbie, vacation, or out-of-attackzone targets. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
      {
        key: "combat_safety_margin",
        id: "cfg-combat_safety_margin",
        title: "1:1 Combat Simulator Safety Margin",
        boundsLabel: "default: 1.4 · 1.0–1000.0x",
        description:
          "Minimum attacker-to-defender effective power ratio required in SpaceInvasion Classic's zero-rapidfire 1:1 combat simulator (incorporating Weapons r11, Shield r12, and Defense r13 tech multipliers) before authorizing any ATTACK wave. Accepts floats from 1.0 to 1000.0.",
        control: "number",
        defaultValue: 1.4,
        min: 1.0,
        max: 1000.0,
        step: 0.1,
      },
      {
        key: "raid_min_loot_spice_eq",
        id: "cfg-raid_min_loot_spice_eq",
        title: "Minimum Raid Plunder Threshold (Spice Eq)",
        boundsLabel: "default: 1500 · >= 0 SpEq",
        description:
          "Minimum expected 50% raid plunder converted into Spice-equivalent units (4 : 2 : 2 : 1 standard value) required before committing cargo hulls, Spice flight fuel, and a 1 + b10 fleet slot to an ATTACK mission. Accepts floats of 0 or higher.",
        control: "number",
        defaultValue: 1500,
        min: 0,
        step: 100,
      },
      {
        key: "allow_fleetsave",
        id: "cfg-allow_fleetsave",
        title: "P0 Automatic Hostile Escape Flight (SAVE)",
        boundsLabel: "default: false · boolean",
        description:
          "Launches an immediate P0 ESCAPE_FLIGHT (SAVE mission) whenever an incoming hostile fleet targets a colony, evacuating stationed ships and packing cargo in Spice → Kryptonite → Metal → Pig iron priority order; SpaceInvasion Classic Escape flights are invisible to Asteroid Galaxy scanners. Boolean toggle.",
        control: "checkbox",
        defaultValue: false,
      },
      {
        key: "fleet_save_fuel_reserve_spice",
        id: "cfg-fleet_save_fuel_reserve_spice",
        title: "Escape Flight Spice Fuel Reserve",
        boundsLabel: "default: 500 · >= 0 Spice",
        description:
          "Quantity of planetary Spice held back on the origin colony before loading ESCAPE_FLIGHT cargo holds so stationed fleets always retain sufficient propellant to launch the SAVE flight and follow-up missions. Accepts integers of 0 or higher.",
        control: "number",
        defaultValue: 500,
        min: 0,
        step: 50,
      },
    ],
  },
  {
    id: "runtime",
    headingId: "cfg-grp-runtime",
    title: "Runtime Pacing & GA + Neural Network Optimizer",
    subtitle:
      "Autonomous Commander tick cadence, rolling hourly DOM mutation budget, and evolutionary ga_nn sequence search hyperparameters.",
    items: [
      {
        key: "action_batch_interval_seconds",
        id: "cfg-action_batch_interval_seconds",
        title: "Minimum Batch & Snapshot Refresh Interval (s)",
        boundsLabel: "default: 120 · 5–86400 s",
        description:
          "Minimum pause in seconds between autonomous Commander execution batches and full Chrome DOM snapshot refreshes when no active building or fleet timer completes sooner, pacing navigation under SpaceInvasion page-view limits. Accepts integers from 5 to 86400 seconds.",
        control: "number",
        defaultValue: 120,
        min: 5,
        max: 86400,
        step: 5,
      },
      {
        key: "max_actions_per_hour",
        id: "cfg-max_actions_per_hour",
        title: "Max Live Actions per Rolling Hour",
        boundsLabel: "default: 60 · 1–100000 ops/hr",
        description:
          "Rolling 60-minute action cap limiting total live browser mutations (BUILD, RESEARCH, BUILD_SHIP, THROTTLE_BUILDING, and fleet dispatches) executed by GameApiBridge to prevent runaway click loops. Accepts integers from 1 to 100000 actions/hour.",
        control: "number",
        defaultValue: 60,
        min: 1,
        max: 100000,
        step: 1,
      },
      {
        key: "ga_population_size",
        id: "cfg-ga_population_size",
        title: "GA Population Size",
        boundsLabel: "default: 24 · 4–200 genomes",
        description:
          "Number of candidate action-sequence chromosomes maintained per evolutionary generation when planner_mode is ga_nn; larger populations explore broader mine, Fusion power, and tech build orders at higher CPU cost. Accepts integers from 4 to 200.",
        control: "number",
        defaultValue: 24,
        min: 4,
        max: 200,
        step: 1,
      },
      {
        key: "ga_generations",
        id: "cfg-ga_generations",
        title: "GA Evolution Generations",
        boundsLabel: "default: 12 · 1–200 cycles",
        description:
          "Number of tournament-selection, crossover, and mutation cycles executed per ga_nn planning pass to converge on lower-wait, higher-SpiceEq multi-step build sequences. Accepts integers from 1 to 200.",
        control: "number",
        defaultValue: 12,
        min: 1,
        max: 200,
        step: 1,
      },
      {
        key: "ga_horizon_steps",
        id: "cfg-ga_horizon_steps",
        title: "GA Rollout Horizon Steps",
        boundsLabel: "default: 6 · 1–24 steps",
        description:
          "Number of sequential actions simulated per chromosome through SpaceInvasion resource accumulation, Blast furnace 2:1 smelting, and Development center / Microsystem build-time formulas during ga_nn rollout scoring. Accepts integers from 1 to 24 steps.",
        control: "number",
        defaultValue: 6,
        min: 1,
        max: 24,
        step: 1,
      },
      {
        key: "ga_mutation_rate",
        id: "cfg-ga_mutation_rate",
        title: "GA Genome Mutation Rate",
        boundsLabel: "default: 0.25 · 0.00–1.00",
        description:
          "Per-gene mutation probability applied during ga_nn offspring reproduction to diversify build-order search and escape local optima such as warehouse or power bottlenecks. Accepts floats from 0.00 to 1.00.",
        control: "number",
        defaultValue: 0.25,
        min: 0,
        max: 1,
        step: 0.05,
      },
      {
        key: "ga_nn_weight",
        id: "cfg-ga_nn_weight",
        title: "46D Neural Value Blending Weight",
        boundsLabel: "default: 0.35 · 0.00–1.00",
        description:
          "Weight blending the 46-dimensional MLP value-network terminal state evaluation with deterministic Spice-equivalent production fitness during ga_nn scoring (0.0 pure rollout math, 1.0 full neural guidance). Accepts floats from 0.00 to 1.00.",
        control: "number",
        defaultValue: 0.35,
        min: 0,
        max: 1,
        step: 0.05,
      },
      {
        key: "ga_seed",
        id: "cfg-ga_seed",
        title: "GA Deterministic PRNG Seed",
        boundsLabel: "default: Auto · 32-bit int or blank",
        description:
          "Optional integer seed pinning ga_nn random initialization, crossover, and mutation for deterministic, repeatable rollout plans; leave blank for fresh stochastic search on each pass. Accepts signed 32-bit integers or blank.",
        control: "text-seed",
        defaultValue: "",
        placeholder: "Auto",
      },
    ],
  },
];

export const CONFIG_BOOL_KEYS: string[] = CONFIG_GROUPS.flatMap((g) =>
  g.items.filter((i) => i.control === "checkbox").map((i) => i.key)
);

export const CONFIG_NUMBER_DEFAULTS: Record<string, number> = Object.fromEntries(
  CONFIG_GROUPS.flatMap((g) =>
    g.items
      .filter((i) => i.control === "number")
      .map((i) => [i.key, Number(i.defaultValue)])
  )
);
