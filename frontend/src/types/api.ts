export * from "./core";
export * from "./planner";
export * from "./intel";
import type {
  BuildingResourceRequestItem,
  HourlyProductionPoint,
  LootRecycleBucket,
  LootRecycleSummary,
  PlannedActionData,
  PlanetData,
  QueueJobData,
  StrategyConfigData,
} from "./core";
import type { ConfigOptimizerResultData, FieldConflictItem, PlannerJobsStatusPayload } from "./planner";
import type { NotificationsState, UniverseResearchMaxRow } from "./intel";

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
  building_resource_requests?: BuildingResourceRequestItem[];
  custom_planet_names?: Record<string, string>;
  hourly_production_history?: HourlyProductionPoint[];
  current_hourly_production?: HourlyProductionPoint | null;
  loot_recycle_history?: LootRecycleBucket[];
  loot_recycle_summary?: LootRecycleSummary | null;
  manual_queue?: Array<Record<string, any>>;
  buildings_eta?: Record<string, any> | null;
  planet_economies?: Record<string, Record<string, any>> | null;
  combat?: Record<string, any> | null;
  ranking?: Record<string, any> | null;
  action_graph?: Record<string, any> | null;
  optimizer_summary?: Record<string, any> | null;
  config_optimizer_goals?: {
    run_id?: number | null;
    updated_ts?: number;
    by_planet_id?: Record<
      string,
      {
        planet_id?: number;
        coords?: string;
        specialization?: string;
        goals?: Record<string, number>;
      }
    >;
    by_coords?: Record<
      string,
      {
        planet_id?: number;
        coords?: string;
        specialization?: string;
        goals?: Record<string, number>;
      }
    >;
  } | null;
  novnc_url?: string | null;
  db_summary?: Record<string, any> | null;
  db_backend?: {
    backend?: "postgres" | "sqlite" | string;
    target?: string;
    selection_source?: string;
    container_mode?: boolean;
    postgres_target?: string;
    sqlite_target?: string;
    sqlite_exists?: boolean;
    sqlite_size_bytes?: number;
    postgres_reachable?: boolean;
    postgres_latency_ms?: number;
    postgres_server?: string;
    podman?: {
      runtime?: string;
      container_name?: string;
      container_running?: boolean;
      container_status?: string;
    };
    [key: string]: any;
  } | null;
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
