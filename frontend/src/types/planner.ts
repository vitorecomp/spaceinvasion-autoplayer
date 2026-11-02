import type { ResourceBundle, StrategyConfigData } from "./core";

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

export interface YearlyShipsWeekPoint {
  week: number;
  year: number;
  week_in_year: number;
  ships_per_week: number;
  resource_cap_per_week: number;
  shipyard_cap_per_week: number;
  bottleneck: string;
  completion_pct: number;
}

export interface YearlyShipsSummaryRow {
  year: number;
  label: string;
  start_ships_per_week: number;
  avg_ships_per_week: number;
  end_ships_per_week: number;
  peak_ships_per_week: number;
  delta_ships_per_week: number;
  total_ships_produced: number;
  cumulative_ships_produced: number;
  bottleneck: string;
  completion_pct: number;
}

export interface ConfigOptimizerYearlyProjection {
  target_ship_key: string;
  target_ship: string;
  horizon_years: number;
  total_weeks: number;
  current_ships_per_week: number;
  target_ships_per_week: number;
  target_ships_per_month: number;
  total_upgrade_levels: number;
  weeks_to_90_pct?: number | null;
  weeks_to_full_target?: number | null;
  years_to_full_target?: number | null;
  years: YearlyShipsSummaryRow[];
  weeks: YearlyShipsWeekPoint[];
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
  custom_planet_names?: Record<string, string>;
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

export interface AiPlannerSmeltingPlanet {
  planet_index?: number;
  planet_id: number;
  coords: string;
  name: string;
  b2_level: number;
  initial_pct: number;
  initial_enabled: boolean;
  rate_known?: boolean;
}

export interface AiPlannerSmeltingToggleTarget {
  planet_id: number;
  coords: string;
  name: string;
  b2_level: number;
  from_pct: number;
  to_pct: number;
}

export interface AiPlannerSmeltingEvent {
  seq: number;
  time_hours: number;
  time_sec: number;
  time_text: string;
  week: number;
  action: "enable" | "disable" | string;
  enabled: boolean;
  target_pct: number;
  from_mode: string;
  from_enabled: boolean;
  is_initial: boolean;
  affected_planets?: AiPlannerSmeltingToggleTarget[];
  step_label?: string | null;
  step_planet_coords?: string | null;
  reason?: string;
}

export interface AiPlannerSmeltingSpan {
  start_hours: number;
  end_hours: number;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  start_text: string;
  end_text: string;
  duration_text: string;
}

export interface AiPlannerSmeltingSummary {
  initial_live_mode?: "on" | "off" | "mixed" | "none" | string;
  initial_live_any_enabled?: boolean;
  initial_live_all_enabled?: boolean;
  initial_live_enabled_count?: number;
  initial_live_disabled_count?: number;
  total_b2_planets?: number;
  initial_target_enabled?: boolean;
  initial_target_pct?: number;
  initial_toggle_needed?: boolean;
  initial_planets_needing_toggle?: AiPlannerSmeltingToggleTarget[];
  metal_smelted?: number;
  smelting_hours_total?: number;
  smelting_time_text?: string;
  smelting_toggles_count?: number;
  smelting_spans_count?: number;
  smelting_spans?: AiPlannerSmeltingSpan[];
  smelting_events?: AiPlannerSmeltingEvent[];
  planets?: AiPlannerSmeltingPlanet[];
}

export interface AiPlannerColonyDiag {
  planet_id: number;
  coords: string;
  name: string;
  state: string;
  reason: string;
  next_label?: string | null;
  idle_with_affordable_target_step: boolean;
  b2_level: number;
  b2_pct: number;
  energy_net: number;
  b2_energy_margin_at_100: number;
}

export interface AiPlannerAdaptiveStatus {
  enabled: boolean;
  active_builders: number;
  total_colonies: number;
  colonies: AiPlannerColonyDiag[];
  counts: Record<string, number>;
  idle_with_affordable_count: number;
  ready_not_started_count: number;
  window_24h?: {
    bucket_count: number;
    colony_samples: number;
    idle_with_affordable_share_pct: number;
    ready_not_started_share_pct: number;
    state_shares_pct: Record<string, number>;
  } | null;
  transport_fleet?: {
    owned_parked_cargo: number;
    in_flight_cargo: number;
    total_fleet_cargo: number;
    target_fleet_cargo: number;
    planned_cargo_per_hour_24h: number;
    realized_utilization: number;
    delivered_cargo_24h?: number;
    delivered_cargo_per_hour?: number;
    delivered_cargo_per_hour_24h?: number;
    delivered_flights_24h?: number;
    delivered_vs_planned_pct?: number | null;
  } | null;
  auto_replan?: {
    cooldown_seconds: number;
    last_auto_replan_ts: number | null;
    last_trigger_reason: string;
    drift_ready: boolean;
  } | null;
}

export interface AiPlannerGetPayload {
  ok?: boolean;
  has_plan?: boolean;
  active_summary?: Record<string, any> | null;
  meta_parameters?: Record<string, any> | null;
  weekly_breakdown?: Array<Record<string, any>>;
  attack_telemetry?: Record<string, any> | null;
  in_progress?: Record<string, any> | null;
  smelting?: AiPlannerSmeltingSummary | null;
  live_blast_furnace?: {
    mode?: string;
    any_enabled?: boolean;
    all_enabled?: boolean;
    enabled_count?: number;
    disabled_count?: number;
    total_b2_planets?: number;
    planets?: AiPlannerSmeltingPlanet[];
  } | null;
  transport_matrix?: Record<string, any> | null;
  transport_demand?: Record<string, any> | null;
  adaptive_status?: AiPlannerAdaptiveStatus | null;
  search?: Record<string, any> | null;
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

