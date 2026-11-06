import { DashboardPayload, NotificationsState, PageKey, PlannerJobsStatusPayload } from "../types/api";
import { CONSOLE_STORE_SCHEMA_VERSION, ConsoleStoreState, UiPrefs } from "./types";

export interface PersistedConsoleEnvelope {
  v: number;
  savedAt: number;
  activePage: PageKey;
  selectedPlanetId: number | null;
  uiPrefs: UiPrefs;
  dashboard?: DashboardPayload | null;
  buildingsEtaData?: Record<string, any> | null;
  combatData?: Record<string, any> | null;
  actionGraphData?: Record<string, any> | null;
  plannerJobsStatus?: PlannerJobsStatusPayload | null;
  sectorsData?: Record<string, any> | null;
  espData?: Record<string, any> | null;
  colonyData?: Record<string, any> | null;
  atkData?: Record<string, any> | null;
  recData?: Record<string, any> | null;
  lastOptimizerSummary?: Record<string, any> | null;
  notifications?: NotificationsState;
  apiOnline: boolean;
  lastHydratedAt: number | null;
}

export function isPlainObject(value: unknown): value is Record<string, any> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function trimLogValue(val: unknown, maxChars: number, maxEntries: number): unknown {
  if (typeof val === "string") {
    return val.length > maxChars ? val.slice(-maxChars) : val;
  }
  if (Array.isArray(val)) {
    return val.length > maxEntries ? val.slice(-maxEntries) : val;
  }
  return val;
}

function compactDashboard(
  dash: DashboardPayload | null,
  tier: number,
  hasSeparateActionGraph: boolean,
  hasSeparateCombat: boolean
): DashboardPayload | null {
  if (!dash || !isPlainObject(dash)) return null;
  if (tier <= 0) return dash;

  if (tier === 1) {
    const next: DashboardPayload = { ...dash };
    if (isPlainObject(next.logs)) {
      const trimmedLogs: Record<string, any> = {};
      for (const [k, v] of Object.entries(next.logs)) {
        trimmedLogs[k] = trimLogValue(v, 16_000, 150);
      }
      next.logs = trimmedLogs;
    }
    if (hasSeparateActionGraph && "action_graph" in next) {
      delete next.action_graph;
    }
    return next;
  }

  if (tier === 2) {
    const next: DashboardPayload = { ...dash };
    if ("logs" in next) next.logs = {};
    if (hasSeparateActionGraph && "action_graph" in next) delete next.action_graph;
    if (hasSeparateCombat && "combat" in next) delete next.combat;
    if (Array.isArray(next.plan) && next.plan.length > 80) {
      next.plan = next.plan.slice(0, 80);
    }
    if (isPlainObject(next.db_summary)) {
      const dbCopy: Record<string, any> = { ...next.db_summary };
      for (const [k, v] of Object.entries(dbCopy)) {
        if (Array.isArray(v) && v.length > 80) dbCopy[k] = v.slice(0, 80);
      }
      next.db_summary = dbCopy;
    }
    return next;
  }

  const minimal: DashboardPayload = {
    ok: dash.ok,
    code_version: dash.code_version,
    css_version: dash.css_version,
    server_boot_id: dash.server_boot_id,
    planner_mode: dash.planner_mode,
    effective_planner_mode: dash.effective_planner_mode,
    fallback_reason: dash.fallback_reason,
    colonies_owned: dash.colonies_owned,
    max_planets: dash.max_planets,
    live_ai_unlocked: dash.live_ai_unlocked,
    health: dash.health,
    bot_control: dash.bot_control,
    state: dash.state,
    config: dash.config,
    plan: Array.isArray(dash.plan) ? dash.plan.slice(0, 40) : dash.plan,
    manual_queue: Array.isArray(dash.manual_queue)
      ? dash.manual_queue.slice(0, 40)
      : dash.manual_queue,
    galaxy_sweep: dash.galaxy_sweep,
    planet_economies: dash.planet_economies,
    optimizer_summary: dash.optimizer_summary,
  };
  if (dash.current_hourly_production !== undefined) {
    minimal.current_hourly_production = dash.current_hourly_production;
  }
  if (dash.loot_recycle_summary !== undefined) {
    minimal.loot_recycle_summary = dash.loot_recycle_summary;
  }
  return minimal;
}

function compactArrayRecord(
  data: Record<string, any> | null,
  arrayLimits: Record<string, number>,
  trimEmailBody = false
): Record<string, any> | null {
  if (!data || !isPlainObject(data)) return null;
  let mutated = false;
  const copy: Record<string, any> = { ...data };
  for (const [key, limit] of Object.entries(arrayLimits)) {
    const val = copy[key];
    if (Array.isArray(val)) {
      let arr = val;
      if (arr.length > limit) {
        arr = arr.slice(0, limit);
        mutated = true;
      }
      if (trimEmailBody && key === "emails") {
        arr = arr.map((item) => {
          if (isPlainObject(item) && typeof item.body === "string" && item.body.length > 2000) {
            mutated = true;
            return { ...item, body: item.body.slice(0, 2000) };
          }
          return item;
        });
      }
      copy[key] = arr;
    }
  }
  return mutated ? copy : data;
}

export function buildPersistedPayload(
  state: ConsoleStoreState,
  tier: 0 | 1 | 2 | 3 | 4
): PersistedConsoleEnvelope {
  const persistedPrefs: UiPrefs = { ...state.uiPrefs, sidebarOpen: false };
  const base: PersistedConsoleEnvelope = {
    v: CONSOLE_STORE_SCHEMA_VERSION,
    savedAt: Date.now(),
    activePage: state.activePage,
    selectedPlanetId: state.selectedPlanetId,
    uiPrefs: persistedPrefs,
    apiOnline: state.apiOnline,
    lastHydratedAt: state.lastHydratedAt,
  };

  if (tier === 4) return base;

  if (tier === 3) {
    return {
      ...base,
      dashboard: compactDashboard(state.dashboard, 3, false, false),
      notifications: {
        unread_count: state.notifications.unread_count,
        items: state.notifications.items.slice(0, 10),
      },
      plannerJobsStatus: state.plannerJobsStatus,
      lastOptimizerSummary: state.lastOptimizerSummary,
    };
  }

  if (tier === 2) {
    const page = state.activePage as string;
    return {
      ...base,
      dashboard: compactDashboard(
        state.dashboard,
        2,
        Boolean(state.actionGraphData),
        Boolean(state.combatData)
      ),
      buildingsEtaData:
        page === "buildings" || page === "research" ? state.buildingsEtaData : null,
      combatData: page === "combat" ? state.combatData : null,
      actionGraphData:
        page === "action-graph"
          ? compactArrayRecord(state.actionGraphData, { nodes: 150, edges: 220 })
          : null,
      plannerJobsStatus: state.plannerJobsStatus,
      sectorsData:
        page === "sectors"
          ? compactArrayRecord(state.sectorsData, { sectors: 150, scan_queue: 80 })
          : null,
      espData:
        page === "espionage"
          ? compactArrayRecord(state.espData, { targets: 80, reports: 60 })
          : null,
      colonyData:
        page === "colonies"
          ? compactArrayRecord(state.colonyData, { candidates: 100, colony_attempts: 60 })
          : null,
      atkData:
        page === "attacks"
          ? compactArrayRecord(state.atkData, { battles: 60, blocks: 60, reports: 60, items: 60 })
          : null,
      recData:
        page === "recyclers"
          ? compactArrayRecord(
              state.recData,
              { harvests: 60, debris: 60, emails: 60, items: 60 },
              true
            )
          : null,
      lastOptimizerSummary: state.lastOptimizerSummary,
      notifications: {
        unread_count: state.notifications.unread_count,
        items: state.notifications.items.slice(0, 20),
      },
    };
  }

  if (tier === 1) {
    return {
      ...base,
      dashboard: compactDashboard(
        state.dashboard,
        1,
        Boolean(state.actionGraphData),
        Boolean(state.combatData)
      ),
      buildingsEtaData: state.buildingsEtaData,
      combatData: state.combatData,
      actionGraphData: compactArrayRecord(state.actionGraphData, { nodes: 250, edges: 400 }),
      plannerJobsStatus: state.plannerJobsStatus,
      sectorsData: compactArrayRecord(state.sectorsData, { sectors: 500, scan_queue: 180 }),
      espData: compactArrayRecord(state.espData, { targets: 150, reports: 150 }),
      colonyData: compactArrayRecord(state.colonyData, { candidates: 250, colony_attempts: 120 }),
      atkData: compactArrayRecord(state.atkData, {
        battles: 150,
        blocks: 150,
        reports: 150,
        items: 150,
      }),
      recData: compactArrayRecord(
        state.recData,
        { harvests: 150, debris: 150, emails: 150, items: 150 },
        true
      ),
      lastOptimizerSummary: state.lastOptimizerSummary,
      notifications: {
        unread_count: state.notifications.unread_count,
        items: state.notifications.items.slice(0, 40),
      },
    };
  }

  return {
    ...base,
    dashboard: state.dashboard,
    buildingsEtaData: state.buildingsEtaData,
    combatData: state.combatData,
    actionGraphData: state.actionGraphData,
    plannerJobsStatus: state.plannerJobsStatus,
    sectorsData: state.sectorsData,
    espData: state.espData,
    colonyData: state.colonyData,
    atkData: state.atkData,
    recData: state.recData,
    lastOptimizerSummary: state.lastOptimizerSummary,
    notifications: state.notifications,
  };
}
