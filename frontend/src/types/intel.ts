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

