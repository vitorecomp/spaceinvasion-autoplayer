export type PageKey =
  | "dashboard"
  | "buildings"
  | "combat"
  | "espionage"
  | "colonies"
  | "attacks"
  | "recyclers"
  | "operations"
  | "action-graph"
  | "ranking"
  | "config"
  | "logs";

export const PAGE_TITLES: Record<PageKey, string> = {
  dashboard: "DASHBOARD // OVERVIEW",
  buildings: "BUILDINGS // LEVEL ETAS",
  combat: "COMBAT & FLEET // 1:1 SIM",
  espionage: "ESPIONAGE // INTEL & ARCHIVE",
  colonies: "COLONIES // 300+ GRADING",
  attacks: "COMBAT // ATTACK RESULTS",
  recyclers: "RECYCLERS // RUBBLE & INBOX",
  operations: "OPERATIONS // PRIORITY QUEUE",
  "action-graph": "ACTION GRAPH // SEQUENCE & DAG",
  ranking: "RANKING // EVOLUTION & AZ",
  config: "STRATEGY // CONFIG BUCKETS",
  logs: "TERMINAL // LIVE LOGS",
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
  detail?: string;
  [key: string]: any;
}

export interface PlannedActionData {
  priority?: string;
  kind?: string;
  planet_id?: number;
  planet_coords?: string;
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
  planner_mode?: "priority" | "ga_nn" | string;
  playstyle?: "fleeter" | "hybrid" | "miner" | string;
  merchant_mode?: boolean;
  auto_throttle_blast_furnace?: boolean;
  allow_research?: boolean;
  allow_shipyard?: boolean;
  allow_multi_planet?: boolean;
  allow_fleetsave?: boolean;
  allow_galaxy_scan?: boolean;
  allow_raids?: boolean;
  allow_pvp_in_az?: boolean;
  allow_colonize?: boolean;
  allow_transmitter?: boolean;
  forbid_planetary_solar?: boolean;
  storage_warn_ratio?: number;
  blast_pig_iron_max_share?: number;
  combat_safety_margin?: number;
  raid_min_loot_spice_eq?: number;
  fleet_save_fuel_reserve_spice?: number;
  action_batch_interval_seconds?: number;
  max_actions_per_hour?: number;
  hot_planet_solar_temp_threshold?: number;
  min_free_fields_warning?: number;
  min_colony_fields?: number;
  max_planets?: number;
  galaxy_sweep_interval_seconds?: number;
  galaxy_sweep_pages?: number;
  galaxy_scan_click_delay_seconds?: number;
  galaxy_scan_click_jitter_seconds?: number;
  galaxy_scan_explore_first?: boolean;
  galaxy_scan_near_weight?: number;
  galaxy_scan_attack_weight?: number;
  reserved_spy_slots?: number;
  spy_dispatch_planet_id?: number;
  ga_population_size?: number;
  ga_generations?: number;
  ga_horizon_steps?: number;
  ga_mutation_rate?: number;
  ga_nn_weight?: number;
  ga_seed?: number | null;
  shipyard_targets?: Record<string, number>;
  [key: string]: any;
}

export interface NotificationItem {
  id: number;
  notification_code?: string;
  status?: "unread" | "read" | "dismissed" | string;
  severity?: "error" | "warning" | "info" | string;
  category?: string;
  title?: string;
  message?: string;
  help_prompt?: string;
  action_kind?: string;
  planet_coords?: string;
  target_name?: string;
  occurrence_count?: number;
  created_ts?: number;
  updated_ts?: number;
  details?: Record<string, any>;
  [key: string]: any;
}

export interface NotificationsState {
  unread_count: number;
  items: NotificationItem[];
}

/** One append-only SQLite `colony_attempts` row (V011): a tried colonization target. */
export interface ColonyAttemptItem {
  id: number;
  ts?: number | null;
  /** `dispatched` | `settled` | `kept` | `deleted_undersized` | `failed` (case-insensitive). */
  event_type?: string;
  target_coords?: string;
  origin_coords?: string;
  planet_id?: number | null;
  fields_rolled?: number | null;
  min_fields_target?: number | null;
  expected_min_fields?: number | null;
  expected_max_fields?: number | null;
  temperature?: number | null;
  grade?: string;
  score?: number | null;
  status_message?: string;
  details?: Record<string, any> | null;
  [key: string]: any;
}

/** Per-coordinate rollup from `get_colony_attempts_summary().by_coords`. */
export interface ColonyAttemptCoordStats {
  attempts_count?: number;
  deleted_count?: number;
  last_event_type?: string;
  last_fields_rolled?: number | null;
  last_ts?: number | null;
  [key: string]: any;
}

export interface ColonyAttemptsSummary {
  total_events?: number;
  unique_coords_tried?: number;
  dispatched_count?: number;
  deleted_undersized_count?: number;
  kept_count?: number;
  failed_count?: number;
  by_coords?: Record<string, ColonyAttemptCoordStats>;
  [key: string]: any;
}

export interface UniverseResearchMaxRow {
  research_id: number;
  key?: string;
  research_key?: string;
  slug: string;
  name: string;
  own_level: number;
  universe_max_level: number;
  spied_max_level?: number;
  server_record_level?: number | null;
  catalog_max_level?: number | null;
  gap_to_max?: number;
  holder?: string;
  holder_coords?: string;
  updated_ts?: number;
  [key: string]: any;
}

export interface DashboardPayload {
  ok?: boolean;
  code_version?: string | null;
  css_version?: string | null;
  server_boot_id?: string | null;
  planner_mode?: string;
  health?: {
    ok?: boolean;
    in_game?: boolean;
    debug_port_open?: boolean;
    debug_port?: number;
    rate_limited?: boolean;
    retry_after?: number;
    action_batch_interval_seconds?: number;
    remaining_hourly_actions?: number;
    [key: string]: any;
  };
  bot_control?: {
    paused?: boolean;
    mode_override?: string | null;
    captcha_blocked?: boolean;
    captcha_message?: string | null;
    [key: string]: any;
  };
  state?: {
    universe_id?: number;
    user_id?: number;
    nickname?: string;
    timestamp?: number;
    speedup_factor?: number;
    active_planet_id?: number;
    planets?: PlanetData[];
    researches?: Record<string, number>;
    research_names?: Record<string, string>;
    active_queues?: QueueJobData[];
    fleet_alerts?: Array<{ hostile?: boolean; [key: string]: any }>;
    tutorial?: {
      step?: number;
      total_steps?: number;
      reward_text?: string;
      can_complete?: boolean;
      tasks?: Array<{
        building_name?: string;
        description?: string;
        current?: number;
        target?: number;
      }>;
    } | null;
    [key: string]: any;
  };
  config?: StrategyConfigData;
  plan?: PlannedActionData[];
  manual_queue?: Array<Record<string, any>>;
  buildings_eta?: Record<string, any> | null;
  combat?: Record<string, any> | null;
  ranking?: Record<string, any> | null;
  action_graph?: Record<string, any> | null;
  optimizer_summary?: Record<string, any> | null;
  logs?: {
    commander?: string;
    game_api?: string;
    [key: string]: any;
  };
  db_summary?: Record<string, any> | null;
  galaxy_sweep?: {
    cursor_galaxy?: number;
    cursor_system?: number;
    scan_phase?: string;
    selection_mode?: string;
    explored_systems?: number;
    unscanned_systems?: number;
    total_systems?: number;
    coverage_pct?: number;
    universe_explored_systems?: number;
    universe_total_systems?: number;
    universe_coverage_pct?: number;
    scan_galaxies?: number[];
    in_progress?: boolean;
    [key: string]: any;
  } | null;
  universe_research_max?: UniverseResearchMaxRow[];
  notifications?: NotificationsState;
  [key: string]: any;
}

export type FeedbackKind = "ok" | "warn" | "error" | "info";

export interface FeedbackState {
  message: string;
  kind: FeedbackKind;
  hidden: boolean;
}
