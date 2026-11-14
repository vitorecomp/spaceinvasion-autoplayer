import {
  DashboardPayload,
  HourlyProductionPoint,
  LootRecycleBucket,
} from "../types/api";
import { isPlainObject } from "./compaction";

const MAX_HOURLY_PRODUCTION_POINTS = 1440;
const MAX_LOOT_RECYCLE_BUCKETS = 168;

const UI_META_KEYS = [
  "server_boot_id",
  "ui_version",
  "code_version",
  "css_version",
  "planner_mode",
  "effective_planner_mode",
  "fallback_reason",
  "live_ai_unlocked",
  "novnc_url",
] as const;

export function buildDashboardPollUrl(
  refresh: boolean,
  prevDashboard: DashboardPayload | null
): string {
  if (refresh) {
    return "/dashboard?refresh=1";
  }
  const params = new URLSearchParams({ refresh: "0" });
  const versions =
    prevDashboard && isPlainObject(prevDashboard.section_versions)
      ? prevDashboard.section_versions
      : {};
  params.set("versions", JSON.stringify(versions));
  if (
    prevDashboard &&
    typeof prevDashboard.latest_minute_bucket === "number" &&
    Number.isFinite(prevDashboard.latest_minute_bucket)
  ) {
    params.set("since_minute_bucket", String(Math.trunc(prevDashboard.latest_minute_bucket)));
  }
  if (
    prevDashboard &&
    typeof prevDashboard.latest_loot_hour === "number" &&
    Number.isFinite(prevDashboard.latest_loot_hour)
  ) {
    params.set("since_loot_hour", String(Math.trunc(prevDashboard.latest_loot_hour)));
  }
  return `/dashboard?${params.toString()}`;
}

function mergeSeriesByBucket<T extends Record<string, any>>(
  prevRows: readonly T[] | undefined,
  deltaRows: readonly T[] | undefined,
  bucketKey: string,
  maxItems: number
): T[] {
  const byBucket = new Map<number, T>();
  for (const row of prevRows || []) {
    if (!row || typeof row !== "object") continue;
    const rawBucket = Number(row[bucketKey]);
    if (Number.isFinite(rawBucket)) {
      byBucket.set(rawBucket, row);
    }
  }
  for (const row of deltaRows || []) {
    if (!row || typeof row !== "object") continue;
    const rawBucket = Number(row[bucketKey]);
    if (Number.isFinite(rawBucket)) {
      byBucket.set(rawBucket, row);
    }
  }
  const sorted = Array.from(byBucket.entries())
    .sort((a, b) => a[0] - b[0])
    .map((entry) => entry[1]);
  return sorted.length > maxItems ? sorted.slice(-maxItems) : sorted;
}

export function mergeDashboardDeltaResponse(
  prevDashboard: DashboardPayload | null,
  incoming: DashboardPayload
): DashboardPayload {
  const isDeltaEnvelope =
    incoming.delta === true ||
    isPlainObject(incoming.section_versions) ||
    Array.isArray(incoming.unchanged_sections);

  if (!isDeltaEnvelope) {
    return incoming;
  }

  const unchangedSet = new Set<string>(
    Array.isArray(incoming.unchanged_sections)
      ? incoming.unchanged_sections.map((k) => String(k))
      : []
  );

  const merged: DashboardPayload =
    incoming.delta === true && prevDashboard && isPlainObject(prevDashboard)
      ? { ...prevDashboard, ...incoming }
      : { ...incoming };

  if (prevDashboard && isPlainObject(prevDashboard)) {
    for (const key of unchangedSet) {
      if (!(key in incoming) && key in prevDashboard) {
        (merged as Record<string, any>)[key] = (prevDashboard as Record<string, any>)[key];
      }
    }
  }

  if (isPlainObject(merged.ui_meta)) {
    for (const metaKey of UI_META_KEYS) {
      if (metaKey in merged.ui_meta && merged.ui_meta[metaKey] !== undefined) {
        (merged as Record<string, any>)[metaKey] = merged.ui_meta[metaKey];
      }
    }
  }

  if (
    merged.db_backend == null &&
    isPlainObject(merged.health) &&
    isPlainObject((merged.health as Record<string, any>).db_backend)
  ) {
    merged.db_backend = (merged.health as Record<string, any>).db_backend;
  }

  if (incoming.hourly_production_mode === "delta") {
    merged.hourly_production_history = mergeSeriesByBucket<HourlyProductionPoint>(
      prevDashboard?.hourly_production_history,
      incoming.hourly_production_history,
      "minute_bucket",
      MAX_HOURLY_PRODUCTION_POINTS
    );
  }

  const incomingLootRows = Array.isArray(incoming.loot_recycle_history)
    ? incoming.loot_recycle_history
    : Array.isArray(incoming.loot_recycle_hourly)
      ? incoming.loot_recycle_hourly
      : undefined;

  if (incoming.loot_recycle_mode === "delta") {
    merged.loot_recycle_history = mergeSeriesByBucket<LootRecycleBucket>(
      prevDashboard?.loot_recycle_history,
      incomingLootRows,
      "hour_bucket",
      MAX_LOOT_RECYCLE_BUCKETS
    );
  } else if (incomingLootRows !== undefined) {
    merged.loot_recycle_history = incomingLootRows;
  }

  return merged;
}
