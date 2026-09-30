import { CONFIG_ITEM_BY_KEY } from "../config/configSchema";
import { BUILDING_NAMES, SHIP_OPTIONS } from "./plannerHelpers";

export interface ActionGraphParamDoc {
  title: string;
  paramKey: string;
  boundsLabel: string;
  description: string;
}

const EXTRA_STRATEGY_PARAM_DOCS: Record<string, ActionGraphParamDoc> = {
  ai_resource_spare_pct: {
    title: "AI Spare Resource Budget (%)",
    paramKey: "ai_resource_spare_pct",
    boundsLabel: "default: 10% · 0–90%",
    description:
      "Soft-reserve percentage of rolling hourly empire income set aside for Fleet Planner ship-unlock/combat research and autonomous shipyard hull production while AI-Planner guides buildings and economy research. Urgent 0-count replacements for Spy probes (s10) and transports (s1/s2) remain exempt so scouting and hauling never stall. Accepts floats from 0 to 90%.",
  },
};

export function getStrategyParamDoc(key: string): ActionGraphParamDoc {
  const fromConfig = CONFIG_ITEM_BY_KEY[key];
  if (fromConfig) {
    return {
      title: fromConfig.title,
      paramKey: fromConfig.key,
      boundsLabel: fromConfig.boundsLabel,
      description: fromConfig.description,
    };
  }
  const extra = EXTRA_STRATEGY_PARAM_DOCS[key];
  if (extra) return extra;
  return {
    title: key,
    paramKey: key,
    boundsLabel: "config parameter",
    description: `Configures the ${key} strategy setting persisted to StrategyConfig in SQLite.`,
  };
}

export const CONFIG_OPTIMIZER_GROUP_SUBTITLES = {
  objective:
    "Target hull objective, monthly window, research level scaling, and warehouse reserve rules governing steady-state planetary specialization.",
  stability:
    "Protects already-invested colonies (mines and warehouses) from flipping roles across small parameter tweaks. Combines automatic sticky warm-start with explicit per-planet role locks in the Planets table.",
  minFleet:
    "Mandatory per-planet docked utility hulls. Any missing ships add their Weapons factory (b9) prerequisites, hull cost, and warehouse capacity floor to every normal colony.",
  previewScope:
    "Preview full-empire optimization before all colony slots are founded by padding missing slots with generic 300-field placeholder planets.",
  optimizedCaps:
    "Upper level ceilings for the 7 buildings freely optimized by the GA and coordinate-ascent polish on each planet. Existing built levels and reserved floors always remain hard minimums.",
  miscLevels:
    "Fixed minimum building levels applied to every normal planet (slot 1 also applies Research lab b8 >= 12 by default). Warehouses b11–b14 act strictly as minimum floors raised automatically when production or build costs demand more capacity.",
  gaSearch:
    "Evolutionary search breadth and deterministic PRNG seed used to combine per-planet Pareto catalogues before dual coordinate-ascent polishing.",
} as const;

export const CONFIG_OPTIMIZER_PARAM_DOCS: Record<string, ActionGraphParamDoc> = {
  target_ship_key: {
    title: "Target Ship (Maximize / Month)",
    paramKey: "target_ship_key",
    boundsLabel: "default: s16 · hull key",
    description:
      "Ship hull whose sustained steady-state monthly production the optimizer maximizes across the pooled empire. Empire output is limited by the tightest bottleneck among each required resource (Pig iron, Metal, Kryptonite, Spice) and combined Weapons factory (b9) + Microsystem (b17) shipyard throughput.",
  },
  production_month_days: {
    title: "Production Month Length (Days)",
    paramKey: "production_month_days",
    boundsLabel: "default: 30 · 1–365 days",
    description:
      "Number of 24-hour days converted into the monthly ship production objective (hours_per_month = 24 * days). Scales both resource-limited and shipyard-limited ships/month proportionally for reporting and comparison. Accepts numbers from 1 to 365 days.",
  },
  research_percent_of_universe_max: {
    title: "Planned Research (% of Universe Max)",
    paramKey: "research_percent_of_universe_max",
    boundsLabel: "default: 100% · 0–200%",
    description:
      "Scales target research levels (r1–r15) as a percentage of SQLite universe_research_max (never dropping below your own current levels or ship/building prerequisites). Directly sets Mining tech (r1, +2%/lvl on b1/b3/b4) and Energy tech (r2, +1%/lvl on b5) used in steady-state output, plus research warehouse sizing on the Research lab planet. Accepts 0 to 200%.",
  },
  warehouse_hours_of_production: {
    title: "Warehouse Hours of Net Production",
    paramKey: "warehouse_hours_of_production",
    boundsLabel: "default: 24 · 0–720 h",
    description:
      "Minimum hours of net positive hourly output that each planet's warehouses (b11–b14) must hold, and that the central planet must hold for whole-empire net production (converged iteratively via search_in_rounds). Each warehouse is still raised further if needed to store the single most expensive planned upgrade, ship, or research cost. Accepts 0 to 720 hours.",
  },
  min_warehouse_level: {
    title: "Minimum Warehouse Level (All Resources)",
    paramKey: "min_warehouse_level",
    boundsLabel: "default: 0 · 0–60 lvl",
    description:
      "Hard minimum level enforced across all four resource warehouses—Pig iron (b11), Metal (b12), Kryptonite (b13), and Spice (b14)—on every colony in the roster (capacity 100,000 × 2^level per resource). Ensures specialist colonies still hold a baseline buffer of non-specialty resources; automatically raised higher whenever hourly output, upgrade costs, per-warehouse floors, or central_planet_min_warehouse_level require more storage. Accepts integers from 0 to 60.",
  },
  central_planet_slot: {
    title: "Central Hub Planet Slot",
    paramKey: "central_planet_slot",
    boundsLabel: "default: 1 · 1–50 slot",
    description:
      "1-based normal planet roster slot designated as the central hub (typically slot 1, your homeworld). Receives the central minimum warehouse floor and whole-empire production reserve requirement; mine and shipyard specializations remain emergent across planets based on temperature, free fields, and built floors. Accepts integers from 1 to 50.",
  },
  central_planet_min_warehouse_level: {
    title: "Central Planet Minimum Warehouse Level",
    paramKey: "central_planet_min_warehouse_level",
    boundsLabel: "default: 15 · 0–50 lvl",
    description:
      "Hard minimum level enforced on all four warehouses (b11–b14) of the designated central_planet_slot, ensuring a safe logistics buffer even before late-game upgrade costs dominate warehouse sizing. Accepts integers from 0 to 50.",
  },
  allow_demolition: {
    title: "Allow Demolishing Off-Role Mines (When Out of Fields)",
    paramKey: "allow_demolition",
    boundsLabel: "default: false · boolean",
    description:
      "When enabled, Config Optimizer allows target building levels below existing built levels on off-role mines and unreserved structures to reclaim colony fields (while always honoring explicit Reserved Floors). Downstream AI-Planner keeps existing mines running while free fields remain and triggers demolitions strictly when a colony runs out of fields (fields_used >= fields_max) and needs 1 field freed for the next upgrade.",
  },
  preserve_previous_specializations: {
    title: "Preserve Previous Planet Specializations",
    paramKey: "preserve_previous_specializations",
    boundsLabel: "default: true · boolean",
    description:
      "When enabled and an active Config Optimizer plan exists, warm-starts and polishes a sticky candidate that keeps every unlocked colony in its previous specialization (Metal, Kryptonite, Spice, Shipyard, or Spice + Shipyard) while re-optimizing building levels for the new parameters. Prevents small tweaks from flipping a Kryptonite colony (and its warehouses) into Metal unless a fresh global plan beats the sticky plan by more than specialization_switch_threshold_pct. Explicit per-planet locks in the Planets table are always enforced regardless of this toggle.",
  },
  specialization_switch_threshold_pct: {
    title: "Role Switch Min Improvement (%)",
    paramKey: "specialization_switch_threshold_pct",
    boundsLabel: "default: 0.5% · 0–25%",
    description:
      "Minimum percentage gain in monthly target ships required for an unlocked colony to switch away from its previous specialization when preserve_previous_specializations is active. Below this threshold (and on ties), the optimizer keeps existing colony roles and breaks remaining ties by lowest remaining building upgrade cost from current built levels. Set to 0% to allow any strictly better global swap on unlocked planets, or increase (e.g., 2–5%) for stricter role stability.",
  },
  pad_generic_colonies: {
    title: "Pad Missing Colony Slots with 300-Field Previews",
    paramKey: "pad_generic_colonies",
    boundsLabel: "default: false · boolean",
    description:
      "When enabled and your empire has fewer than max_planets normal colonies, appends generic 300-field (0°C) placeholder planets up to max_planets for full-empire steady-state preview runs. Does not alter live game state or bypass the all-colonies live AI-Planner execution gate.",
  },
  ga_population_size: {
    title: "GA Population Size",
    paramKey: "ga_population_size",
    boundsLabel: "default: 200 · 4–1000 genomes",
    description:
      "Number of candidate empire configurations (one Pareto catalogue design per planet) evolved per generation during the GA assignment stage before coordinate-ascent polish. Higher values improve global role allocation on heterogeneous rosters at modest CPU cost. Accepts integers from 4 to 1000.",
  },
  ga_generations: {
    title: "GA Evolution Generations",
    paramKey: "ga_generations",
    boundsLabel: "default: 150 · 1–1000 cycles",
    description:
      "Number of tournament selection, uniform crossover, and catalogue-index mutation cycles executed to assign planetary specializations before dual polishing (direct + balanced-score coordinate ascent). Accepts integers from 1 to 1000.",
  },
  ga_random_seed: {
    title: "GA Deterministic PRNG Seed",
    paramKey: "ga_random_seed",
    boundsLabel: "default: 20261117 · integer",
    description:
      "Integer seed initializing NumPy's random generator for reproducible GA search. Keeping the seed and inputs identical yields identical results; with preserve_previous_specializations enabled (or explicit per-planet role locks set), changing the seed will no longer randomly swap specializations across twin planets.",
  },
};

export const CONFIG_OPTIMIZER_MIN_FLEET_DOCS: Record<string, ActionGraphParamDoc> = {
  s2: {
    title: "Large Transport (s2)",
    paramKey: "min_fleet_per_planet.s2",
    boundsLabel: "default: 10 · 0–10000 ships",
    description:
      "Minimum Large transports (s2) stationed per normal colony for inter-planet hauling and Escape flight cargo evacuation. Counts missing hull cost toward fleet investment and enforces b9 prerequisites on each planet.",
  },
  s10: {
    title: "Spy Probe (s10)",
    paramKey: "min_fleet_per_planet.s10",
    boundsLabel: "default: 10 · 0–10000 ships",
    description:
      "Minimum Spy probes (s10) kept on every colony for local espionage scouting. Enforces required Weapons factory (b9) and Spy tech (r7) prerequisites and counts missing probes in fleet investment.",
  },
  s11: {
    title: "Recycler (s11)",
    paramKey: "min_fleet_per_planet.s11",
    boundsLabel: "default: 10 · 0–10000 ships",
    description:
      "Minimum Recyclers (s11) stationed per colony to harvest nearby orbital rubble fields. Enforces b9 prerequisites and includes missing hulls in warehouse capacity and fleet cost calculations.",
  },
};

export const CONFIG_OPTIMIZER_OPT_MAX_DOCS: Record<string, ActionGraphParamDoc> = {
  b1: {
    title: "Pig iron mine (b1)",
    paramKey: "optimized_building_max_levels.b1",
    boundsLabel: "default: 100 · 1–100 lvl",
    description:
      "Upper level cap for Pig iron mine (b1), boosted by Mining tech (r1, +2%/lvl). Feeds both raw Pig iron needs and the 2:1 smelting feedstock required by Blast furnace (b2) on Metal-specialized worlds.",
  },
  b2: {
    title: "Blast furnace (b2)",
    paramKey: "optimized_building_max_levels.b2",
    boundsLabel: "default: 100 · 1–100 lvl",
    description:
      "Upper level cap for Blast furnace (b2). Converts 2 Pig iron into 1 Metal per unit produced and is NOT boosted by Mining tech (r1); lowering this cap trades peak Metal output for dramatically shorter late-game build times.",
  },
  b3: {
    title: "Kryptonite mine (b3)",
    paramKey: "optimized_building_max_levels.b3",
    boundsLabel: "default: 100 · 1–100 lvl",
    description:
      "Upper level cap for Kryptonite mine (b3), boosted by Mining tech (r1). High-level b3 is energy-intensive and typically concentrated on 1–2 dedicated Kryptonite colonies once warehouse field costs bind.",
  },
  b4: {
    title: "Spice mine (b4)",
    paramKey: "optimized_building_max_levels.b4",
    boundsLabel: "default: 100 · 1–100 lvl",
    description:
      "Upper level cap for Spice mine (b4), boosted by Mining tech (r1) and colder planetary temperatures. Optimizer naturally places heavy b4 levels on your coldest colonies to supply Fusion plants (b5) and ship costs.",
  },
  b5: {
    title: "Fusion plant (b5)",
    paramKey: "optimized_building_max_levels.b5",
    boundsLabel: "default: 100 · 1–100 lvl",
    description:
      "Upper level cap for Fusion plant (b5). Primary compact planetary power source scaled by Energy tech (r2, +1%/lvl) while consuming Spice; keeps power ratio at 100% without wasting fields on Thermosolar.",
  },
  b9: {
    title: "Weapons factory (b9)",
    paramKey: "optimized_building_max_levels.b9",
    boundsLabel: "default: 100 · 1–100 lvl",
    description:
      "Upper level cap for Weapons factory (b9). Scales linear shipyard build speed (b9 * 2^b17 * speedup) and unlocks target hull construction on whichever planet(s) specialize as shipyards.",
  },
  b17: {
    title: "Microsystem accelerator (b17)",
    paramKey: "optimized_building_max_levels.b17",
    boundsLabel: "default: 100 · 1–100 lvl",
    description:
      "Upper level cap for Microsystem accelerator (b17). Halves building and shipyard construction time per level (2^b17 divisor; does not affect research). Catalog top level is L55; cap here bounds shipyard and build-speed investment.",
  },
};

const MISC_BUILDING_DETAIL: Record<string, { defaultVal: number; desc: string }> = {
  b6: {
    defaultVal: 0,
    desc: "Fixed minimum Thermosolar plant (b6) level per normal planet. Keep at 0 when relying on Fusion plant (b5) so valuable planetary fields are preserved for mines and warehouses.",
  },
  b7: {
    defaultVal: 12,
    desc: "Fixed minimum Development center (b7) level per planet. Accelerates construction linearly as (b7 + 1) * 2^b17 * speedup and satisfies b17 Microsystem accelerator prerequisites (requires b7 L10).",
  },
  b8: {
    defaultVal: 0,
    desc: "Fixed minimum Research lab (b8) level across all colonies (slot 1 also receives L12 by default via misc_building_levels_by_slot). The planet with the highest b8 automatically sizes its warehouses to pay for the costliest planned research.",
  },
  b10: {
    defaultVal: 5,
    desc: "Fixed minimum Fleet base (b10) level per planet. Each planet gets 1 + b10 concurrent fleet slots (raids, espionage, transport, recycling), directly scaling fleet throughput and AI-Planner attack slots.",
  },
  b11: {
    defaultVal: 0,
    desc: "Minimum floor for Pig iron warehouse (b11) on every planet. Leave at 0 to let the optimizer size b11 automatically from warehouse_hours_of_production, largest planned upgrade cost, and central_planet_min_warehouse_level.",
  },
  b12: {
    defaultVal: 0,
    desc: "Minimum floor for Metal warehouse (b12) on every planet. Automatically raised above this floor whenever hourly output, upgrade costs, or central hub reserves require larger Metal storage (100k * 2^L).",
  },
  b13: {
    defaultVal: 0,
    desc: "Minimum floor for Kryptonite warehouse (b13) on every planet. Automatically raised above this floor whenever Kryptonite production, upgrade/research costs, or central hub reserves demand higher capacity.",
  },
  b14: {
    defaultVal: 0,
    desc: "Minimum floor for Spice warehouse (b14) on every planet. Automatically raised above this floor whenever Spice output, upgrade/research costs, or central hub reserves require higher storage.",
  },
  b15: {
    defaultVal: 0,
    desc: "Fixed minimum Particle cannon (b15) planetary defense level per colony. Consumes planetary fields without adding resource output; leave at 0 unless you intentionally reserve defense slots.",
  },
  b16: {
    defaultVal: 0,
    desc: "Fixed minimum Particle shield (b16) planetary defense level per colony. Reserves planetary fields for dome protection; leave at 0 for pure economic optimization.",
  },
  b18: {
    defaultVal: 0,
    desc: "Fixed minimum Teleporter (b18) level per colony. Unlocks instantaneous fleet teleportation (requires Teleportation r14); set > 0 only on empires reserving fields for teleporter hubs.",
  },
  b19: {
    defaultVal: 0,
    desc: "Fixed minimum Galaxy scanner (b19) level. In SpaceInvasion Classic, Galaxy scanners belong on Asteroids; keep at 0 on normal planets.",
  },
  b20: {
    defaultVal: 0,
    desc: "Fixed minimum Tunnel system (b20) level per colony to shelter a slice of planetary resources from hostile raids. Keep at 0 when prioritizing maximum productive fields.",
  },
  b21: {
    defaultVal: 0,
    desc: "Fixed minimum Quantum Array (b21) level per colony. Leave at 0 unless reserving fields for late-game Quantum Array requirements.",
  },
};

export function getConfigOptimizerMiscDoc(bk: string): ActionGraphParamDoc {
  const name = BUILDING_NAMES[bk] || bk;
  const info = MISC_BUILDING_DETAIL[bk];
  return {
    title: `${name} (${bk})`,
    paramKey: `misc_building_levels.${bk}`,
    boundsLabel: `default: ${info?.defaultVal ?? 0} · 0–100 lvl`,
    description:
      info?.desc ||
      `Minimum fixed level enforced for ${name} (${bk}) on every normal colony (existing built levels and reserved floors remain hard minimums).`,
  };
}

export const AI_PLANNER_GROUP_SUBTITLES = {
  horizon:
    "Discounted weekly production horizon (NPV) that scores sequential upgrade trajectories from your live empire toward the active Config Optimization target.",
  sparePct:
    "Percentage of total hourly income (colony production plus simulated raid loot) held back for shipyard builds and hostile raid losses; weekly reward scores net kept production (1 − spare%).",
  telemetry:
    "Live SQLite raid loot & round-trip medians drive simulated hourly attack income (sum(1 + b10) slots × 3600 / median_RT). Leave overrides blank to auto-derive from live planets and raid history.",
  weights:
    "Baseline PathStrategy scoring hyperparameters seeded into the rollout simulator alongside the 8-gene evolutionary path optimizer.",
  gaSearch:
    "Outer real-coded 8-gene GA tuning (focus, critical_share, patience, speed_weight, and 4 log2 resource value multipliers) to maximize discounted W-week SpiceEq production.",
} as const;

export const AI_PLANNER_PARAM_DOCS: Record<string, ActionGraphParamDoc> = {
  reward_weeks: {
    title: "Reward Horizon (Weeks, W)",
    paramKey: "reward_weeks",
    boundsLabel: "default: 26 · 1–104 weeks",
    description:
      "Length of the planning horizon in weeks (168 hours per week) over which weekly net SpiceEq production is integrated and exported as sequential build/research actions. Shorter horizons prioritize fast-payback mines; longer horizons justify heavier b7/b17 accelerators and deep research. Accepts integers from 1 to 104 weeks.",
  },
  weekly_discount: {
    title: "Weekly Discount Factor (γ)",
    paramKey: "weekly_discount",
    boundsLabel: "default: 0.90 · 0.05–1.00",
    description:
      "Exponential time-preference multiplier applied to week w output as γ^(w−1) in discounted_reward. Lower values (e.g. 0.75–0.85) strongly favor immediate early-week income growth; 1.00 weights every week in the W-week window equally. Accepts floats from 0.05 to 1.00.",
  },
  transport_dispatch_delay_minutes: {
    title: "Fleet Dispatch Delay (min)",
    paramKey: "transport_dispatch_delay_minutes",
    boundsLabel: "default: 120 · 0–1440 min",
    description:
      "Minutes of Fleet Command dispatch lead time added on top of real inter-planet coordinate travel time (one-way flight using each colony's transport hulls and engine tech) when scheduling cross-planet resource transfers. AI-Planner requests transports early by dispatch delay plus travel time so cargo lands before a colony build or research queue finishes. Accepts 0 to 1440 minutes.",
  },
  fleet_command_center_level: {
    title: "Fleet Base (b10) Level Override",
    paramKey: "fleet_command_center_level",
    boundsLabel: "default: Auto (blank) · 0–50 lvl",
    description:
      "Optional fixed Fleet base (b10) level assumed per planet when computing simulated raid flight slots (1 + b10 per colony). Leave blank (Auto) to dynamically track each planet's live and upgraded b10 level throughout the rollout so b10 upgrades earn slot-loot NPV.",
  },
  attacks_per_day: {
    title: "Simulated Attacks / Day Override",
    paramKey: "attacks_per_day",
    boundsLabel: "default: Auto (blank) · >= 0/day",
    description:
      "Optional flat empire-wide daily raid count replacing slot-derived raid throughput (sum(1 + b10) × 86400 / median_round_trip_sec). Leave blank (Auto) to calculate raid cadence automatically from SQLite median round-trip time and active fleet slots.",
  },
  pad_generic_colonies: {
    title: "Pad Missing Colony Slots in Preview",
    paramKey: "pad_generic_colonies",
    boundsLabel: "default: false · boolean",
    description:
      "Includes generic 300-field preview planets up to max_planets when simulating a path before all colonies are founded. Only affects preview simulation; live AI-Planner bot execution still remains locked until all colonies are founded.",
  },
  scarcity_focus: {
    title: "Resource Scarcity Focus",
    paramKey: "scarcity_focus",
    boundsLabel: "default: 1.0 · 0.0–10.0",
    description:
      "Exponent amplifying shadow prices on bottleneck resources relative to remaining in-horizon upgrade demand (horizon_missing_resources). Higher values steer builds aggressively toward whichever resource currently starves upcoming upgrades; 0.0 uses flat Merchant exchange rates (6 : 2.5 : 3 : 1).",
  },
  critical_queue_share: {
    title: "Critical Queue Priority Share",
    paramKey: "critical_queue_share",
    boundsLabel: "default: 0.90 · 0.00–1.00",
    description:
      "Weighting bias allocating scarce empire income toward the highest-priority / bottleneck planet or global research queue before funding lower-impact parallel queues. Higher values concentrate resources to launch top-NPV steps sooner; lower values spread spending more evenly across colonies.",
  },
  patience: {
    title: "Upgrade Saving Patience",
    paramKey: "patience",
    boundsLabel: "default: 0.25 · 0.00–5.00",
    description:
      "Controls willingness to hold resources and wait for a higher-ROI upgrade rather than immediately spending on a cheaper, ready lower-value step. Higher values allow longer resource accumulation windows for major mine, b17, or research jumps; 0.0 spends greedily as soon as affordable.",
  },
  speed_weight: {
    title: "Build/Research Accelerator Speed Weight",
    paramKey: "speed_weight",
    boundsLabel: "default: 1.5 · 0.0–10.0",
    description:
      "Multiplier scaling the urgency of time-saving accelerators — Development center (b7), Microsystem accelerator (b17), and Research lab (b8) — across the remaining discounted horizon window. Raise to build accelerators earlier; lower to prioritize direct mine output first.",
  },
  ga_population_size: {
    title: "GA Strategy Population Size",
    paramKey: "ga_population_size",
    boundsLabel: "default: 10 · 4–200 genomes",
    description:
      "Number of 8-gene PathStrategy parameter vectors simulated over the full W-week rollout per generation. Larger populations explore wider combinations of scarcity weights and patience at linearly higher compute time. Accepts integers from 4 to 200.",
  },
  ga_generations: {
    title: "GA Evolution Generations",
    paramKey: "ga_generations",
    boundsLabel: "default: 6 · 1–100 cycles",
    description:
      "Number of evolutionary generations run to optimize the 8 PathStrategy genes maximizing net discounted SpiceEq reward (tie-broken by faster target completion). Accepts integers from 1 to 100.",
  },
  ga_random_seed: {
    title: "GA Deterministic PRNG Seed",
    paramKey: "ga_random_seed",
    boundsLabel: "default: 20261117 · integer",
    description:
      "Integer PRNG seed controlling 8-gene population initialization, crossover, and Gaussian mutation so repeated AI-Planner runs on the same snapshot produce identical schedules.",
  },
};

export const AI_PLANNER_SPARE_DOCS: Record<string, ActionGraphParamDoc> = {
  pig_iron: {
    title: "Pig Iron Spare %",
    paramKey: "resource_spare_percent.pig_iron",
    boundsLabel: "default: 10% · 0–100%",
    description:
      "Share of gross Pig iron hourly income (mines + raid loot) withheld from building/research spending to fund ship hulls and raid loss margins. Deducted before scoring weekly discounted reward.",
  },
  metal: {
    title: "Metal Spare %",
    paramKey: "resource_spare_percent.metal",
    boundsLabel: "default: 10% · 0–100%",
    description:
      "Share of gross Metal hourly income (Blast furnaces + raid loot) reserved for shipyard construction and combat replacements instead of building upgrades.",
  },
  kryptonite: {
    title: "Kryptonite Spare %",
    paramKey: "resource_spare_percent.kryptonite",
    boundsLabel: "default: 10% · 0–100%",
    description:
      "Share of gross Kryptonite hourly income reserved for probes, warships, and shields rather than building/research expansion.",
  },
  spice: {
    title: "Spice Spare %",
    paramKey: "resource_spare_percent.spice",
    boundsLabel: "default: 10% · 0–100%",
    description:
      "Share of gross Spice hourly income held back for flight propellant, combat ships, and Escape fuel buffers instead of economic upgrades.",
  },
};

export const FLEET_PLANNER_GROUP_SUBTITLES = {
  combat:
    "Zero-rapidfire 1:1 combat simulator gates, inactive/Spacia/PvP raid authorization, P0 Escape flight protection, and rolling spare-income shipyard budget (synced with Config).",
  logistics:
    "Free-slot 300+-field colonization caps, inter-planet resource transport, and daily free Interdimensional Transmitter automation (synced with Config).",
  scanner:
    "Smart 2-phase Galaxy scanner (contiguous arrow-first exploration when distance < 5, then proximity + attack-history weighted refresh), click cadence, and reserved spy slots (synced with Config).",
  shipyard:
    "Per-hull empire shipyard quotas constructed at Weapons factory (b9) whenever allow_shipyard is enabled and soft spare-income budget permits (synced with Config).",
  harvestThresholds:
    "Separate minimum round-trip resource-per-hour (res/h) yield gates required before triggering ATTACK raids or RECYCLE debris flights (0 = disabled; synced with Config).",
  fleetResearch:
    "Spends the same soft spare-income budget (ahead of non-urgent ship builds) to auto-unlock missing research prerequisites for any configured shipyard_targets > 0 and level explicit combat, drive, and stealth techs (r3–r15) whenever the global Research lab is free of P1 AI-Planner research.",
} as const;

const SHIP_HULL_ROLE_DESC: Record<string, string> = {
  "small-transport":
    "Fast early-game cargo hull (s1) for small inactive raids, rapid colony bootstrapping, and urgent 0-count logistics replacements.",
  "large-transport":
    "High-capacity cargo hull (s2) favored for bulk raid plunder hauling, inter-planet resource transport, and P0 Escape flight resource evacuation.",
  transmitter:
    "High-speed specialized courier/cargo hull (s3) used for rapid long-range inter-planet resource transfers once unlocked.",
  "small-fighter":
    "Inexpensive early combat fodder/raider (s4) in SpaceInvasion's zero-rapidfire 1:1 targeting model; absorbs defender fire cheaply during raids.",
  warship:
    "Light mid-tier combat vessel (s5) providing faster intercept speed and stronger firepower than Small fighters for early active/inactive raids.",
  starcruiser:
    "Versatile mid-game cruiser (s6) balancing speed, shield durability, and attack power for clean 1:1 raid sweeps.",
  phoenix:
    "Heavy assault hull (s7) offering strong weapon power and survivability against fortified inactive and Spacia defenses.",
  "colony-ship":
    "Required hull (s8) for P1/P2 COLONIZE flights onto verified 300+-field empty slots; planner scales builds up to remaining open colony slots.",
  "solar-cells":
    "Orbital energy unit (s9, destructible in combat). Reserved for Asteroids when forbid_planetary_solar is enabled; keep at 0 on normal planets.",
  "spy-probe":
    "Ultra-fast recon hull (s10) for P1 SPY scouting (#1 fleet priority) to refresh inactive, Spacia, and AZ target intelligence every 4 hours.",
  recycler:
    "Debris harvesting hull (s11) dispatched on P2 RECYCLE waves (tied with ATTACK by res/h) to recover orbital Pig iron, Metal, Kryptonite, and Spice rubble.",
  battleship:
    "Heavy capital warship (s13) delivering high single-target firepower and cargo capacity for major raid targets and fleet engagements.",
  "stealth-bomber":
    "First-strike anti-defense bomber (s14) ideal for cracking heavily turtled planets with minimal losses in 1:1 combat simulation.",
  destroyer:
    "Top-tier capital destroyer (s15) with heavy shields and attack strength against high-defense targets and opposing capital fleets.",
  "imperial-starbase":
    "End-game flagship capital hull (s16) with massive shields, weapons, and cargo; typically the long-term target maximized by Config Optimization.",
};

export function getShipyardHullDoc(slug: string): ActionGraphParamDoc {
  const meta = SHIP_OPTIONS.find((s) => s.slug === slug);
  const keyTag = meta ? `${meta.name} (${meta.key})` : slug;
  return {
    title: keyTag,
    paramKey: `shipyard_targets.${slug}`,
    boundsLabel: "default: 0 · 0–9,999,999 hulls",
    description:
      SHIP_HULL_ROLE_DESC[slug] ||
      `Target stationed hull count for ${keyTag} maintained automatically by Weapons factory (b9) when allow_shipyard is active.`,
  };
}

export interface FleetResearchMeta {
  key: string;
  name: string;
  desc: string;
}

export const FLEET_RESEARCH_OPTIONS: readonly FleetResearchMeta[] = [
  {
    key: "r3",
    name: "Engine research",
    desc: "Core propulsion prerequisite unlocking Rocket engine (r4 at L2), Diffusion engine (r5 at L6), and Warp engine (r6 at L8). Requires Research lab b8 L1.",
  },
  {
    key: "r4",
    name: "Rocket engine",
    desc: "Boosts early ship speed and unlocks Small fighter (L1), Spy probe (L3), Small transport & Warship (L4), Large transport (L6), and Colony ship (L8). Requires r3 L2.",
  },
  {
    key: "r5",
    name: "Diffusion engine",
    desc: "Mid-tier drive boosting cruiser/hauler speed and unlocking Recycler (L4), Starcruiser (L5), and Phoenix & Transmitter (L8). Requires r3 L6.",
  },
  {
    key: "r6",
    name: "Warp engine",
    desc: "Capital-ship FTL drive unlocking Battleship (L4), Stealth bomber (L6), Destroyer (L8), and Black hole research (L10). Requires r3 L8 and b8 L8.",
  },
  {
    key: "r7",
    name: "Spy technology",
    desc: "Improves espionage probe report depth & counter-espionage defense, and unlocks Spy probe (L2) and Stealth bomber (L8). Requires b8 L3.",
  },
  {
    key: "r8",
    name: "Weapons technology",
    desc: "Increases attack firepower of all combat hulls and defenses, and unlocks Warship (L2). Requires b8 L3.",
  },
  {
    key: "r9",
    name: "Shield technology",
    desc: "Strengthens fleet & defense shields, and unlocks Starcruiser (L4) and Particle analyzer (L5). Requires b8 L4 and Energy technology r2 L4.",
  },
  {
    key: "r10",
    name: "Defense systems",
    desc: "Reinforces hull durability & planetary defenses, and unlocks Phoenix (L5). Requires b8 L5.",
  },
  {
    key: "r11",
    name: "Targeting systems",
    desc: "Improves fleet weapon targeting effectiveness in combat simulations. Requires b8 L5 and Energy technology r2 L6.",
  },
  {
    key: "r12",
    name: "Particle analyzer",
    desc: "Advanced combat & shield efficiency research. Requires b8 L6 and Shield technology r9 L5.",
  },
  {
    key: "r13",
    name: "Graviton technology",
    desc: "High-tier energy-physics prerequisite required at L10 for Black hole research (r15). Requires b8 L2 and Energy technology r2 L2.",
  },
  {
    key: "r14",
    name: "Teleportation",
    desc: "Unlocks Teleporter (b18) for instant inter-colony fleet jumps. Requires Research lab b8 L10.",
  },
  {
    key: "r15",
    name: "Black hole research",
    desc: "End-game research unlocking Imperial starbase (s16 at L2). Requires Warp engine r6 L10 and Graviton technology r13 L10.",
  },
];

export function getFleetResearchDoc(
  rKey: string,
  currentLevel?: number | null
): ActionGraphParamDoc {
  const meta = FLEET_RESEARCH_OPTIONS.find((r) => r.key === rKey);
  const title = meta ? `${meta.name} (${meta.key})` : rKey;
  const curPrefix =
    typeof currentLevel === "number" && Number.isFinite(currentLevel)
      ? `Current L${Math.max(0, Math.round(currentLevel))} · `
      : "";
  return {
    title,
    paramKey: `fleet_research_targets.${rKey}`,
    boundsLabel: `${curPrefix}0 = auto-unlock only · 0–100 lvl`,
    description:
      meta?.desc ||
      `Target research level for ${title} funded automatically by Fleet Planner spare resources when allow_fleet_research is enabled.`,
  };
}
