export type PageKey =
  | "dashboard"
  | "buildings"
  | "combat"
  | "sectors"
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
  sectors: "GALAXY // SCANNED SECTORS",
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
  origin_coords?: string;
  detail?: string;
  cargo?: ResourceBundle | null;
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
  planner_mode?: "priority" | "ai_planner" | "ga_nn" | string;
  playstyle?: "fleeter" | "hybrid" | "miner" | string;
  merchant_mode?: boolean;
  auto_throttle_blast_furnace?: boolean;
  allow_research?: boolean;
  allow_fleet_research?: boolean;
  allow_shipyard?: boolean;
  allow_multi_planet?: boolean;
  allow_fleetsave?: boolean;
  allow_galaxy_scan?: boolean;
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
  galaxy_scan_refresh_pct?: number;
  galaxy_scan_all_galaxies?: boolean;
  galaxy_scan_near_weight?: number;
  galaxy_scan_attack_weight?: number;
  reserved_spy_slots?: number;
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

export type ActionGraphTabKey =
  | "heuristics"
  | "config-optimizer"
  | "ai-planner"
  | "fleet-planner";

export interface PlannerJobState {
  job_id?: string | null;
  kind?: string;
  status?: "idle" | "running" | "completed" | "error" | string;
  phase?: string;
  progress_pct?: number;
  elapsed_ms?: number;
  started_ts?: number | null;
  finished_ts?: number | null;
  error?: string | null;
  trigger_source?: string;
  [key: string]: any;
}

export interface FieldConflictCandidate {
  building_key: string;
  building_id?: number;
  name?: string;
  level?: number;
  energy_delta?: number;
  safe_to_demolish?: boolean;
  [key: string]: any;
}

export interface FieldConflictItem {
  conflict_key: string;
  planet_id: number;
  planet_coords?: string;
  planet_name?: string;
  blocked_building_key?: string;
  blocked_building_name?: string;
  blocked_target_level?: number;
  candidate_building_key?: string;
  candidate_level?: number;
  candidates?: FieldConflictCandidate[];
  status?: "pending" | "resolved" | string;
  decision?: string;
  created_ts?: number;
  resolved_ts?: number | null;
  detail?: Record<string, any>;
  [key: string]: any;
}

export interface PlannerJobsStatusPayload {
  ok?: boolean;
  config_optimizer_job?: PlannerJobState | null;
  ai_planner_job?: PlannerJobState | null;
  colonies_owned?: number;
  max_planets?: number;
  live_ai_unlocked?: boolean;
  active_planner_mode?: string;
  effective_planner_mode?: string;
  fallback_reason?: string | null;
  pending_field_conflicts?: FieldConflictItem[];
  [key: string]: any;
}

export interface ConfigOptimizerPlanetRecord {
  slot?: number;
  slot_index?: number;
  planet_id?: number;
  coords?: string;
  name?: string;
  planet_status?: string;
  is_placeholder?: boolean;
  is_central_planet?: boolean;
  pays_for_research?: boolean;
  temperature?: number;
  temperature_c?: number;
  fields_max?: number;
  fields_used?: number;
  fields_used_target?: number;
  fields_free?: number;
  built_floors?: Record<string, number>;
  reserved_floors?: Record<string, number>;
  detected_specialization?: string;
  locked_specialization?: string;
  previous_specialization?: string | null;
  specialization_changed?: boolean;
  production?: Record<string, any>;
  warehouses?: Array<Record<string, any>> | Record<string, any>;
  buildings?: Array<Record<string, any>> | Record<string, any>;
  [key: string]: any;
}

export interface ConfigOptimizerResultData {
  schema_version?: number;
  generated_at?: string;
  elapsed_ms?: number;
  meta_parameters?: Record<string, any>;
  inputs?: Record<string, any>;
  planned_research_levels?: Record<string, number> | Array<Record<string, any>>;
  summary?: Record<string, any>;
  search?: Record<string, any>;
  limits?: Array<Record<string, any>>;
  planets?: ConfigOptimizerPlanetRecord[];
  [key: string]: any;
}

export interface ConfigOptimizerGetPayload {
  ok?: boolean;
  active?: {
    run_id?: number;
    updated_ts?: number;
    params?: Record<string, any>;
    reserved_floors?: Record<string, Record<string, number>>;
    result?: ConfigOptimizerResultData;
    [key: string]: any;
  } | null;
  job?: PlannerJobState | null;
  runs?: Array<Record<string, any>>;
  reserved_floors?: Record<string, Record<string, number>>;
  planet_specializations?: Record<string, string>;
  specialization_options?: string[];
  default_params?: Record<string, any>;
  colonies_owned?: number;
  max_planets?: number;
  [key: string]: any;
}

export interface AiPlannerStepItem {
  seq?: number;
  order?: number;
  start_sec?: number;
  duration_sec?: number;
  finish_sec?: number;
  start?: string;
  duration_text?: string;
  week?: number;
  kind?: string;
  planet_id?: number;
  planet_coords?: string;
  planet_name?: string;
  entity_key?: string;
  entity_name?: string;
  target_level?: number;
  cost?: ResourceBundle;
  status?: string;
  needs_transport?: boolean;
  transport_request_at_sec?: number | null;
  transport_request_text?: string;
  transport_travel_sec?: number;
  transport_dispatch_delay_sec?: number;
  transport_lead_sec?: number;
  transport_sources?: Array<Record<string, any>>;
  [key: string]: any;
}

export interface AiPlannerGetPayload {
  ok?: boolean;
  has_plan?: boolean;
  active_summary?: Record<string, any> | null;
  meta_parameters?: Record<string, any> | null;
  weekly_breakdown?: Array<Record<string, any>>;
  attack_telemetry?: Record<string, any> | null;
  in_progress?: Record<string, any> | null;
  transport_matrix?: Record<string, any> | null;
  graph?: Record<string, any> | null;
  steps_page?: {
    items?: AiPlannerStepItem[];
    total?: number;
    page?: number;
    page_size?: number;
  };
  job?: PlannerJobState | null;
  runs?: Array<Record<string, any>>;
  colonies_owned?: number;
  max_planets?: number;
  live_ai_unlocked?: boolean;
  pending_field_conflicts?: FieldConflictItem[];
  default_params?: Record<string, any>;
  [key: string]: any;
}

export interface FleetPlannerGetPayload {
  ok?: boolean;
  graph?: Record<string, any> | null;
  slot_budgets?: Array<Record<string, any>> | Record<string, any>;
  attack_telemetry?: Record<string, any> | null;
  spare_budget?: Record<string, any> | null;
  config?: StrategyConfigData;
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

export interface GalaxyStatsItem {
  galaxy: number;
  is_owned?: boolean;
  in_sweep_scope?: boolean;
  scanned_count: number;
  unscanned_count: number;
  total_systems: number;
  coverage_pct: number;
  occupied_count: number;
  debris_count: number;
  target_count: number;
  prime_free_count: number;
  queued_count: number;
  [key: string]: any;
}

export interface ScanQueueItem {
  queue_pos: number;
  sector: string;
  galaxy: number;
  system: number;
  phase: "exploratory" | "targeted_refresh" | string;
  scanned: boolean;
  last_scanned_ts: number;
  age_seconds: number | null;
  scan_count: number;
  occupied_count: number;
  debris_count: number;
  target_count: number;
  free_count: number;
  prime_colony_free_count: number;
  direct_raid_count: number;
  owner_affinity_raid_count: number;
  frequent_owners?: string[];
  system_distance: number | null;
  circular_distance?: number | null;
  galaxy_delta: number | null;
  system_delta: number | null;
  score: number;
  importance?: number;
  reason?: string;
  [key: string]: any;
}

export interface ScannedSectorItem {
  sector: string;
  galaxy: number;
  system: number;
  scanned?: boolean;
  queue_pos?: number | null;
  queue_phase?: string | null;
  occupied_count: number;
  max_slots?: number;
  active_occupied_count?: number;
  free_count: number;
  prime_colony_free_count: number;
  debris_count: number;
  target_count: number;
  inactive_count: number;
  spacia_count: number;
  scan_count: number;
  last_scanned_ts: number;
  age_seconds: number | null;
  system_distance: number | null;
  circular_distance?: number | null;
  galaxy_delta: number | null;
  system_delta: number | null;
  direct_raid_count: number;
  direct_raid_24h_count?: number;
  direct_loot_spice_eq?: number;
  owner_affinity_raid_count: number;
  frequent_owners?: string[];
  score: number;
  importance?: number;
  reason?: string;
  [key: string]: any;
}

export interface ScannedSectorsSummary {
  scanned_sectors?: number;
  unscanned_sectors_in_view?: number;
  unscanned_total_all_galaxies?: number;
  total_scanned_all_galaxies?: number;
  explored_systems?: number;
  unscanned_systems?: number;
  total_systems?: number;
  coverage_pct?: number;
  universe_explored_systems?: number;
  universe_total_systems?: number;
  universe_coverage_pct?: number;
  total_occupied?: number;
  avg_occupied?: number;
  total_free?: number;
  total_prime_free?: number;
  total_debris_fields?: number;
  sectors_with_debris?: number;
  total_targets?: number;
  total_spacia?: number;
  total_inactives?: number;
  stale_over_1h_count?: number;
  total_raids?: number;
  sweep_pages?: number;
  sweep_interval_seconds?: number;
  allow_galaxy_scan?: boolean;
  explore_first?: boolean;
  scan_all_galaxies?: boolean;
  refresh_pct?: number;
  discovery_pct?: number;
  effective_refresh_pct?: number;
  effective_discovery_pct?: number;
  saved_refresh_pct?: number;
  saved_sweep_pages?: number;
  saved_scan_all_galaxies?: boolean;
  planned_refresh_pages?: number;
  planned_discovery_pages?: number;
  next_batch_refresh_count?: number;
  next_batch_discovery_count?: number;
  queue_count?: number;
  queue_discovery_count?: number;
  queue_refresh_count?: number;
  queue_galaxies?: number[];
  scan_phase?: string;
  [key: string]: any;
}

export interface ScannedSectorsPayload {
  ok?: boolean;
  command?: string;
  preview?: boolean;
  origin_coords?: string;
  galaxy_filter?: number;
  include_unscanned?: boolean;
  galaxies_available?: number[];
  galaxies_with_scans?: number[];
  galaxy_stats?: GalaxyStatsItem[];
  scan_queue?: ScanQueueItem[];
  summary?: ScannedSectorsSummary;
  sectors?: ScannedSectorItem[];
  [key: string]: any;
}

export interface DashboardPayload {
  ok?: boolean;
  code_version?: string | null;
  css_version?: string | null;
  server_boot_id?: string | null;
  planner_mode?: string;
  effective_planner_mode?: string;
  fallback_reason?: string | null;
  colonies_owned?: number;
  max_planets?: number;
  live_ai_unlocked?: boolean;
  pending_field_conflicts?: FieldConflictItem[];
  planner_jobs_status?: PlannerJobsStatusPayload | null;
  planner_status?: PlannerJobsStatusPayload | null;
  ai_planner_summary?: Record<string, any> | null;
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
  planet_economies?: Record<string, Record<string, any>> | null;
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
    refresh_pct?: number;
    discovery_pct?: number;
    exploratory_count?: number;
    refresh_count?: number;
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
