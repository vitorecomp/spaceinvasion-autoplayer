export type PageKey =
  | "dashboard"
  | "buildings"
  | "research"
  | "combat"
  | "fleet-control"
  | "sectors"
  | "espionage"
  | "colonies"
  | "attacks"
  | "recyclers"
  | "operations"
  | "action-graph"
  | "ranking"
  | "config";

export const PAGE_TITLES: Record<PageKey, string> = {
  dashboard: "DASHBOARD // OVERVIEW",
  buildings: "BUILDINGS // LEVEL ETAS",
  research: "RESEARCH // LEVEL ETAS",
  combat: "FLEET BUILD // SHIPYARD & SIM",
  "fleet-control": "FLEET CONTROL // CURRENT FLIGHTS",
  sectors: "GALAXY // SCANNED SECTORS",
  espionage: "ESPIONAGE // INTEL & ARCHIVE",
  colonies: "COLONIES // 300+ GRADING",
  attacks: "COMBAT // ATTACK RESULTS",
  recyclers: "RECYCLERS // RUBBLE & INBOX",
  operations: "OPERATIONS // PRIORITY QUEUE",
  "action-graph": "ACTION GRAPH // SEQUENCE & DAG",
  ranking: "RANKING // EVOLUTION & AZ",
  config: "STRATEGY // CONFIG BUCKETS",
};

export interface ResourceBundle {
  pig_iron?: number;
  metal?: number;
  kryptonite?: number;
  spice?: number;
  [key: string]: any;
}

export interface PlanetData {
  planet_id: number;
  name?: string;
  game_name?: string;
  custom_name?: string;
  coords?: string;
  planet_type?: string;
  temperature?: number | null;
  fields_used?: number | null;
  fields_max?: number | null;
  resources?: ResourceBundle;
  storage?: ResourceBundle;
  energy?: {
    produced?: number | null;
    used?: number | null;
    net?: number | null;
  };
  buildings?: Record<string, number>;
  ships?: Record<string, number>;
  defense?: Record<string, number>;
  economy_rates?: Record<string, number>;
  [key: string]: any;
}

export interface QueueJobData {
  category?: string;
  name?: string;
  building_id?: number | null;
  research_id?: number | null;
  ship_id?: number | null;
  target_level?: number | null;
  quantity?: number | null;
  remaining_text?: string;
  remaining_seconds?: number | null;
  finish_ts?: number | null;
  queue_slot?: number | null;
  planet_id?: number | null;
  planet_coords?: string;
  origin_coords?: string;
  detail?: string;
  returning?: boolean;
  cargo?: ResourceBundle | null;
  expected_cargo?: ResourceBundle | null;
  delivered_cargo?: ResourceBundle | null;
  delivered?: boolean;
  [key: string]: any;
}

export interface BuildingResourceRequestItem {
  id?: string;
  planet_id?: number | null;
  planet_coords?: string;
  planet_name?: string;
  target_building?: string;
  target_level?: number | null;
  building_id?: number | null;
  label?: string;
  status?: "requested" | "planned" | "in_flight" | string;
  resources?: ResourceBundle | null;
  pig_iron?: number;
  metal?: number;
  kryptonite?: number;
  spice?: number;
  source_planet_id?: number | null;
  source_coords?: string;
  source_name?: string;
  eta_seconds?: number | null;
  finish_ts?: number | null;
  reason?: string;
  [key: string]: any;
}

export interface RenamePlanetResponse {
  ok?: boolean;
  command?: string;
  planet_id?: number;
  name?: string;
  game_name?: string;
  cleared?: boolean;
  custom_planet_names?: Record<string, string>;
  planet?: {
    planet_id?: number;
    coords?: string;
    name?: string;
    game_name?: string;
    custom_name?: string;
    [key: string]: any;
  };
  message?: string;
  [key: string]: any;
}

export interface HourlyProductionPoint {
  minute_bucket: number;
  recorded_at?: number;
  pig_iron_per_hour: number;
  metal_per_hour: number;
  kryptonite_per_hour: number;
  spice_per_hour: number;
  total_per_hour: number;
  speq_per_hour: number;
  planet_count?: number;
  active_builders?: number;
  [key: string]: any;
}

export interface LootRecycleBucket {
  hour_bucket: number;
  stolen_pig_iron: number;
  stolen_metal: number;
  stolen_kryptonite: number;
  stolen_spice: number;
  stolen_total: number;
  recycled_pig_iron: number;
  recycled_metal: number;
  recycled_kryptonite: number;
  recycled_spice: number;
  recycled_total: number;
  total?: number;
  attack_count?: number;
  recycle_count?: number;
  updated_at?: number;
  [key: string]: any;
}

export interface LootRecycleSummary {
  total_stolen: number;
  total_recycled: number;
  grand_total: number;
  attack_count?: number;
  recycle_count?: number;
  window_hours?: number;
  stolen_pig_iron?: number;
  stolen_metal?: number;
  stolen_kryptonite?: number;
  stolen_spice?: number;
  recycled_pig_iron?: number;
  recycled_metal?: number;
  recycled_kryptonite?: number;
  recycled_spice?: number;
  [key: string]: any;
}

export interface PlannedActionData {
  priority?: string;
  kind?: string;
  planet_id?: number;
  planet_coords?: string;
  planet_name?: string;
  target_id?: number | null;
  target_name?: string;
  target_level?: number | null;
  cost?: ResourceBundle;
  missing?: ResourceBundle;
  reason?: string;
  executable_now?: boolean;
  wait_hours?: number | null;
  wait_seconds?: number | null;
  wait_eta?: string | null;
  duration_seconds?: number | null;
  duration_text?: string | null;
  total_hours?: number | null;
  total_seconds?: number | null;
  total_eta?: string | null;
  eta_bottleneck?: string | null;
  manual_queue_id?: number | null;
  [key: string]: any;
}

export interface StrategyConfigData {
  planner_mode?: "priority" | "ai_planner" | "ga_nn" | string;
  ai_adaptive_execution?: boolean;
  playstyle?: "fleeter" | "hybrid" | "miner" | string;
  merchant_mode?: boolean;
  auto_throttle_blast_furnace?: boolean;
  allow_research?: boolean;
  allow_fleet_research?: boolean;
  allow_shipyard?: boolean;
  allow_multi_planet?: boolean;
  allow_fleetsave?: boolean;
  allow_galaxy_scan?: boolean;
  galaxy_scan_block_other_actions?: boolean;
  allow_raids?: boolean;
  allow_pvp_in_az?: boolean;
  allow_colonize?: boolean;
  allow_transport?: boolean;
  allow_transmitter?: boolean;
  forbid_planetary_solar?: boolean;
  storage_warn_ratio?: number;
  blast_pig_iron_max_share?: number;
  combat_safety_margin?: number;
  raid_min_loot_spice_eq?: number;
  attack_min_res_per_hour?: number;
  recycle_min_res_per_hour?: number;
  fleet_save_fuel_reserve_spice?: number;
  action_batch_interval_seconds?: number;
  max_actions_per_hour?: number;
  page_view_limit_per_minute?: number;
  hot_planet_solar_temp_threshold?: number;
  min_free_fields_warning?: number;
  min_colony_fields?: number;
  max_planets?: number;
  galaxy_sweep_interval_seconds?: number;
  galaxy_sweep_pages?: number;
  galaxy_scan_click_delay_seconds?: number;
  galaxy_scan_click_jitter_seconds?: number;
  galaxy_scan_explore_first?: boolean;
  galaxy_scan_refresh_pct?: number;
  galaxy_scan_all_galaxies?: boolean;
  galaxy_scan_near_weight?: number;
  galaxy_scan_attack_weight?: number;
  reserved_spy_slots?: number;
  spy_reserved_slots_only?: boolean;
  spy_dispatch_planet_id?: number;
  ai_resource_spare_pct?: number;
  ga_population_size?: number;
  ga_generations?: number;
  ga_horizon_steps?: number;
  ga_mutation_rate?: number;
  ga_nn_weight?: number;
  ga_seed?: number | null;
  shipyard_targets?: Record<string, number>;
  fleet_research_targets?: Record<string, number>;
  [key: string]: any;
}

