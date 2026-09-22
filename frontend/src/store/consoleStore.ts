import type React from "react";
import { useSyncExternalStore } from "react";
import { safeFiniteNumber } from "../api/httpClient";
import {
  ActionGraphTabKey,
  DashboardPayload,
  NotificationsState,
  PAGE_TITLES,
  PageKey,
  PlannerJobsStatusPayload,
} from "../types/api";

export const CONSOLE_STORE_STORAGE_KEY = "si_console_store_v1";
export const LEGACY_SESSION_STATE_KEY = "si_console_ui_state";
export const CONSOLE_STORE_SCHEMA_VERSION = 1;
export const PERSIST_DEBOUNCE_MS = 120;
export const SOFT_MAX_PERSIST_CHARS = 1_800_000;

export const VALID_AG_TABS: readonly ActionGraphTabKey[] = [
  "heuristics",
  "config-optimizer",
  "ai-planner",
  "fleet-planner",
];

export interface UiPrefs {
  modeOverride: "auto" | "live" | "dry-run" | string;
  bldCategory: string;
  selectedUnitKey: string;
  resTab: string;
  selectedResearchKey: string;
  combatSubTab: "ships" | "defense" | "targets" | "escape" | string;
  queueFilter: string;
  dashQueuePlanetScope: string;
  opsPlanetScope: string;
  opsQueueLayout: "table" | "parallel";
  activeLogSource: "commander" | "game_api" | "sqlite" | string;
  agActiveTab: ActionGraphTabKey;
  agEdgeFilter: "all" | "deps";
  agParamTab: "core" | "scope";
  sectorFilter: string;
  sectorGalaxy: number;
  espTab: "targets" | "reports";
  colonyFilter: string;
  atkTab: "battles" | "blocks";
  recTab: "harvests" | "debris" | "emails";
  emailCatFilter: string;
  notifFilter: "active" | "unread" | "all";
  sidebarOpen: boolean;
  [key: string]: any;
}

export const DEFAULT_UI_PREFS: UiPrefs = Object.freeze({
  modeOverride: "auto",
  bldCategory: "all",
  selectedUnitKey: "b1",
  resTab: "all",
  selectedResearchKey: "r1",
  combatSubTab: "ships",
  queueFilter: "all",
  dashQueuePlanetScope: "all",
  opsPlanetScope: "all",
  opsQueueLayout: "table",
  activeLogSource: "commander",
  agActiveTab: "heuristics",
  agEdgeFilter: "all",
  agParamTab: "core",
  sectorFilter: "all",
  sectorGalaxy: 0,
  espTab: "targets",
  colonyFilter: "all",
  atkTab: "battles",
  recTab: "harvests",
  emailCatFilter: "all",
  notifFilter: "active",
  sidebarOpen: false,
}) as UiPrefs;

export interface ConsoleStoreState {
  activePage: PageKey;
  selectedPlanetId: number | null;
  uiPrefs: UiPrefs;
  dashboard: DashboardPayload | null;
  buildingsEtaData: Record<string, any> | null;
  combatData: Record<string, any> | null;
  actionGraphData: Record<string, any> | null;
  plannerJobsStatus: PlannerJobsStatusPayload | null;
  sectorsData: Record<string, any> | null;
  espData: Record<string, any> | null;
  colonyData: Record<string, any> | null;
  atkData: Record<string, any> | null;
  recData: Record<string, any> | null;
  lastOptimizerSummary: Record<string, any> | null;
  notifications: NotificationsState;
  apiOnline: boolean;
  lastHydratedAt: number | null;
  isHydratedFromCache: boolean;
}

interface PersistedConsoleEnvelope {
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

const EMPTY_NOTIFICATIONS: NotificationsState = Object.freeze({
  unread_count: 0,
  items: Object.freeze([]) as unknown as NotificationsState["items"],
});

export function createDefaultConsoleStoreState(): ConsoleStoreState {
  return {
    activePage: "dashboard",
    selectedPlanetId: null,
    uiPrefs: { ...DEFAULT_UI_PREFS },
    dashboard: null,
    buildingsEtaData: null,
    combatData: null,
    actionGraphData: null,
    plannerJobsStatus: null,
    sectorsData: null,
    espData: null,
    colonyData: null,
    atkData: null,
    recData: null,
    lastOptimizerSummary: null,
    notifications: { unread_count: 0, items: [] },
    apiOnline: false,
    lastHydratedAt: null,
    isHydratedFromCache: false,
  };
}

export const SERVER_CONSOLE_SNAPSHOT: ConsoleStoreState = Object.freeze({
  activePage: "dashboard",
  selectedPlanetId: null,
  uiPrefs: DEFAULT_UI_PREFS,
  dashboard: null,
  buildingsEtaData: null,
  combatData: null,
  actionGraphData: null,
  plannerJobsStatus: null,
  sectorsData: null,
  espData: null,
  colonyData: null,
  atkData: null,
  recData: null,
  lastOptimizerSummary: null,
  notifications: EMPTY_NOTIFICATIONS,
  apiOnline: false,
  lastHydratedAt: null,
  isHydratedFromCache: false,
});

export function isSupportedPageKey(key: unknown): key is PageKey {
  if (typeof key !== "string" || !key) return false;
  return Boolean(
    PAGE_TITLES[key as PageKey] || key === "research" || key === "fleet-control"
  );
}

export function normalizePageKey(rawKey: unknown): PageKey {
  const str = typeof rawKey === "string" ? rawKey.trim() : "";
  const norm = str === "graph" ? "action-graph" : str;
  return isSupportedPageKey(norm) ? (norm as PageKey) : "dashboard";
}

export function sanitizePersistedBldCategory(raw: unknown): string {
  const val = typeof raw === "string" ? raw.trim() : "";
  if (!val || val === "research" || val === "universe_research") {
    return "all";
  }
  return val;
}

function isPlainObject(value: unknown): value is Record<string, any> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function isQuotaExceededError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as { name?: string; code?: number; message?: string };
  if (
    e.name === "QuotaExceededError" ||
    e.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
    e.code === 22 ||
    e.code === 1014
  ) {
    return true;
  }
  const msg = String(e.message || "").toLowerCase();
  return msg.includes("quota") || msg.includes("exceeded") || msg.includes("storage full");
}

function sanitizeUiPrefs(raw: unknown): UiPrefs {
  const prefs: UiPrefs = { ...DEFAULT_UI_PREFS };
  if (!isPlainObject(raw)) return prefs;

  for (const [k, v] of Object.entries(raw)) {
    if (v !== undefined) {
      prefs[k] = v;
    }
  }

  prefs.modeOverride =
    typeof raw.modeOverride === "string" && raw.modeOverride
      ? raw.modeOverride
      : DEFAULT_UI_PREFS.modeOverride;
  prefs.bldCategory = sanitizePersistedBldCategory(raw.bldCategory);
  prefs.selectedUnitKey =
    typeof raw.selectedUnitKey === "string" && raw.selectedUnitKey
      ? raw.selectedUnitKey
      : DEFAULT_UI_PREFS.selectedUnitKey;
  prefs.resTab =
    typeof raw.resTab === "string" && raw.resTab ? raw.resTab : DEFAULT_UI_PREFS.resTab;
  prefs.selectedResearchKey =
    typeof raw.selectedResearchKey === "string" && raw.selectedResearchKey
      ? raw.selectedResearchKey
      : DEFAULT_UI_PREFS.selectedResearchKey;
  prefs.combatSubTab =
    typeof raw.combatSubTab === "string" && raw.combatSubTab
      ? raw.combatSubTab
      : DEFAULT_UI_PREFS.combatSubTab;
  prefs.queueFilter =
    typeof raw.queueFilter === "string" && raw.queueFilter
      ? raw.queueFilter
      : DEFAULT_UI_PREFS.queueFilter;
  prefs.dashQueuePlanetScope =
    typeof raw.dashQueuePlanetScope === "string" && raw.dashQueuePlanetScope
      ? raw.dashQueuePlanetScope
      : DEFAULT_UI_PREFS.dashQueuePlanetScope;
  prefs.opsPlanetScope =
    typeof raw.opsPlanetScope === "string" && raw.opsPlanetScope
      ? raw.opsPlanetScope
      : DEFAULT_UI_PREFS.opsPlanetScope;
  prefs.opsQueueLayout =
    raw.opsQueueLayout === "parallel" || raw.opsQueueLayout === "table"
      ? raw.opsQueueLayout
      : DEFAULT_UI_PREFS.opsQueueLayout;
  prefs.activeLogSource =
    typeof raw.activeLogSource === "string" && raw.activeLogSource
      ? raw.activeLogSource
      : DEFAULT_UI_PREFS.activeLogSource;
  prefs.agActiveTab = VALID_AG_TABS.includes(raw.agActiveTab)
    ? raw.agActiveTab
    : DEFAULT_UI_PREFS.agActiveTab;
  prefs.agEdgeFilter =
    raw.agEdgeFilter === "deps" || raw.agEdgeFilter === "all"
      ? raw.agEdgeFilter
      : DEFAULT_UI_PREFS.agEdgeFilter;
  prefs.agParamTab =
    raw.agParamTab === "scope" || raw.agParamTab === "core"
      ? raw.agParamTab
      : DEFAULT_UI_PREFS.agParamTab;
  prefs.sectorFilter =
    typeof raw.sectorFilter === "string" && raw.sectorFilter
      ? raw.sectorFilter
      : DEFAULT_UI_PREFS.sectorFilter;
  prefs.sectorGalaxy = Math.max(
    0,
    Math.round(safeFiniteNumber(raw.sectorGalaxy, DEFAULT_UI_PREFS.sectorGalaxy) ?? 0)
  );
  prefs.espTab =
    raw.espTab === "reports" || raw.espTab === "targets" ? raw.espTab : DEFAULT_UI_PREFS.espTab;
  prefs.colonyFilter =
    typeof raw.colonyFilter === "string" && raw.colonyFilter
      ? raw.colonyFilter
      : DEFAULT_UI_PREFS.colonyFilter;
  prefs.atkTab =
    raw.atkTab === "blocks" || raw.atkTab === "battles" ? raw.atkTab : DEFAULT_UI_PREFS.atkTab;
  prefs.recTab =
    raw.recTab === "debris" || raw.recTab === "emails" || raw.recTab === "harvests"
      ? raw.recTab
      : DEFAULT_UI_PREFS.recTab;
  prefs.emailCatFilter =
    typeof raw.emailCatFilter === "string" && raw.emailCatFilter
      ? raw.emailCatFilter
      : DEFAULT_UI_PREFS.emailCatFilter;
  prefs.notifFilter =
    raw.notifFilter === "unread" || raw.notifFilter === "all" || raw.notifFilter === "active"
      ? raw.notifFilter
      : DEFAULT_UI_PREFS.notifFilter;
  // Mobile drawer should always start closed on page reload
  prefs.sidebarOpen = false;
  return prefs;
}

function sanitizeNotifications(raw: unknown): NotificationsState {
  if (!isPlainObject(raw)) {
    return { unread_count: 0, items: [] };
  }
  const items = Array.isArray(raw.items) ? raw.items : [];
  const unreadCount = Math.max(0, Math.round(safeFiniteNumber(raw.unread_count, 0) ?? 0));
  return { unread_count: unreadCount, items };
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

  if (tier <= 0) {
    return dash;
  }

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
    if ("logs" in next) {
      next.logs = {};
    }
    if (hasSeparateActionGraph && "action_graph" in next) {
      delete next.action_graph;
    }
    if (hasSeparateCombat && "combat" in next) {
      delete next.combat;
    }
    if (Array.isArray(next.plan) && next.plan.length > 80) {
      next.plan = next.plan.slice(0, 80);
    }
    if (isPlainObject(next.db_summary)) {
      const dbCopy: Record<string, any> = { ...next.db_summary };
      for (const [k, v] of Object.entries(dbCopy)) {
        if (Array.isArray(v) && v.length > 80) {
          dbCopy[k] = v.slice(0, 80);
        }
      }
      next.db_summary = dbCopy;
    }
    return next;
  }

  // Tier 3: core dashboard only
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
    manual_queue: Array.isArray(dash.manual_queue) ? dash.manual_queue.slice(0, 40) : dash.manual_queue,
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

  if (tier === 4) {
    return base;
  }

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

  // Tier 0: full fidelity
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

function safeGetStorage(kind: "local" | "session"): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

export function readPersistedConsoleState(
  storageKey = CONSOLE_STORE_STORAGE_KEY,
  legacyKey = LEGACY_SESSION_STATE_KEY
): ConsoleStoreState {
  const next = createDefaultConsoleStoreState();
  if (typeof window === "undefined") {
    return next;
  }

  let loadedFromStoreKey = false;
  const local = safeGetStorage("local");
  const session = safeGetStorage("session");

  // 1. Try primary localStorage key (fallback to sessionStorage copy if localStorage was blocked)
  for (const storage of [local, session]) {
    if (!storage) continue;
    try {
      const raw = storage.getItem(storageKey);
      if (!raw) continue;
      const saved = JSON.parse(raw);
      if (!isPlainObject(saved)) continue;

      if (saved.activePage !== undefined) {
        next.activePage = normalizePageKey(saved.activePage);
      }
      const savedPid = safeFiniteNumber(saved.selectedPlanetId, null);
      if (savedPid !== null) {
        next.selectedPlanetId = savedPid;
      }
      next.uiPrefs = sanitizeUiPrefs(saved.uiPrefs);

      if (isPlainObject(saved.dashboard)) {
        next.dashboard = saved.dashboard as DashboardPayload;
      }
      if (isPlainObject(saved.buildingsEtaData)) {
        next.buildingsEtaData = saved.buildingsEtaData;
      } else if (
        isPlainObject(next.dashboard?.buildings_eta) &&
        (!next.selectedPlanetId ||
          next.selectedPlanetId === next.dashboard?.buildings_eta?.planet?.planet_id)
      ) {
        next.buildingsEtaData = next.dashboard!.buildings_eta!;
      }

      if (isPlainObject(saved.combatData)) {
        next.combatData = saved.combatData;
      } else if (
        isPlainObject(next.dashboard?.combat) &&
        (!next.selectedPlanetId || next.selectedPlanetId === next.dashboard?.combat?.planet_id)
      ) {
        next.combatData = next.dashboard!.combat!;
      }

      if (isPlainObject(saved.actionGraphData)) {
        next.actionGraphData = saved.actionGraphData;
      } else if (isPlainObject(next.dashboard?.action_graph)) {
        next.actionGraphData = next.dashboard!.action_graph!;
      }

      if (isPlainObject(saved.plannerJobsStatus)) {
        next.plannerJobsStatus = saved.plannerJobsStatus as PlannerJobsStatusPayload;
      } else if (isPlainObject(next.dashboard?.planner_jobs_status)) {
        next.plannerJobsStatus = next.dashboard!.planner_jobs_status as PlannerJobsStatusPayload;
      } else if (isPlainObject(next.dashboard?.planner_status)) {
        next.plannerJobsStatus = next.dashboard!.planner_status as PlannerJobsStatusPayload;
      }

      if (isPlainObject(saved.sectorsData)) next.sectorsData = saved.sectorsData;
      if (isPlainObject(saved.espData)) next.espData = saved.espData;
      if (isPlainObject(saved.colonyData)) next.colonyData = saved.colonyData;
      if (isPlainObject(saved.atkData)) next.atkData = saved.atkData;
      if (isPlainObject(saved.recData)) next.recData = saved.recData;

      if (isPlainObject(saved.lastOptimizerSummary)) {
        next.lastOptimizerSummary = saved.lastOptimizerSummary;
      } else if (isPlainObject(next.dashboard?.optimizer_summary)) {
        next.lastOptimizerSummary = next.dashboard!.optimizer_summary!;
      }

      next.notifications = sanitizeNotifications(saved.notifications);
      next.apiOnline = Boolean(saved.apiOnline);
      next.lastHydratedAt =
        safeFiniteNumber(saved.lastHydratedAt, null) ?? safeFiniteNumber(saved.savedAt, null);

      const hasCachedData = Boolean(
        next.dashboard ||
          next.buildingsEtaData ||
          next.combatData ||
          next.actionGraphData ||
          next.plannerJobsStatus ||
          next.sectorsData ||
          next.espData ||
          next.colonyData ||
          next.atkData ||
          next.recData ||
          next.notifications.items.length > 0
      );
      next.isHydratedFromCache = hasCachedData;
      loadedFromStoreKey = true;
      break;
    } catch {
      // Ignore corrupt or blocked storage entries
    }
  }

  // 2. Backwards-compatible migration fallback from legacy sessionStorage key
  if (!loadedFromStoreKey && session) {
    try {
      const legacyRaw = session.getItem(legacyKey);
      if (legacyRaw) {
        const legacy = JSON.parse(legacyRaw);
        if (isPlainObject(legacy)) {
          const savedPid = safeFiniteNumber(legacy.selectedPlanetId, null);
          if (savedPid !== null) next.selectedPlanetId = savedPid;
          next.uiPrefs = sanitizeUiPrefs(legacy);
          if (legacy.activePage !== undefined) {
            next.activePage = normalizePageKey(legacy.activePage);
          }
        }
      }
    } catch {
      // Ignore storage restrictions
    }
  }

  // 3. URL hash takes precedence if explicitly pointing to a supported page
  try {
    const rawHash = (window.location.hash || "").replace(/^#/, "");
    const normHash = rawHash === "graph" ? "action-graph" : rawHash;
    if (isSupportedPageKey(normHash)) {
      next.activePage = normHash as PageKey;
    }
  } catch {
    // Ignore hash read errors
  }

  return next;
}

export function persistConsoleStateToStorage(
  state: ConsoleStoreState,
  opts?: {
    storageKey?: string;
    maxChars?: number;
  }
): { ok: boolean; storage: "local" | "session" | "memory"; tier: number } {
  if (typeof window === "undefined") {
    return { ok: false, storage: "memory", tier: -1 };
  }
  const storageKey = opts?.storageKey || CONSOLE_STORE_STORAGE_KEY;
  const maxChars = opts?.maxChars ?? SOFT_MAX_PERSIST_CHARS;

  const tiers: Array<0 | 1 | 2 | 3 | 4> = [0, 1, 2, 3, 4];
  const local = safeGetStorage("local");

  if (local) {
    for (const tier of tiers) {
      let json: string;
      try {
        json = JSON.stringify(buildPersistedPayload(state, tier));
      } catch {
        continue;
      }
      if (tier < 4 && json.length > maxChars) {
        continue;
      }
      try {
        local.setItem(storageKey, json);
        return { ok: true, storage: "local", tier };
      } catch (err) {
        if (isQuotaExceededError(err)) {
          continue;
        }
        // Non-quota error (e.g. SecurityError / disabled localStorage) -> fallback to sessionStorage
        break;
      }
    }
  }

  // Fallback to sessionStorage with slimmed payload
  const session = safeGetStorage("session");
  if (session) {
    const fallbackTiers: Array<2 | 3 | 4> = [2, 3, 4];
    for (const tier of fallbackTiers) {
      let json: string;
      try {
        json = JSON.stringify(buildPersistedPayload(state, tier));
      } catch {
        continue;
      }
      if (tier < 4 && json.length > maxChars) {
        continue;
      }
      try {
        session.setItem(storageKey, json);
        return { ok: true, storage: "session", tier };
      } catch (err) {
        if (isQuotaExceededError(err)) {
          continue;
        }
        break;
      }
    }
  }

  return { ok: false, storage: "memory", tier: -1 };
}

export interface CreateConsoleStoreOptions {
  persist?: boolean;
  hydrateOnInit?: boolean;
  bindUnloadEvents?: boolean;
  debounceMs?: number;
  maxPersistChars?: number;
  storageKey?: string;
  legacyKey?: string;
}

export interface ResetConsoleStoreOptions {
  clearStorage?: boolean;
}

export interface ConsoleStore {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => ConsoleStoreState;
  getServerSnapshot: () => ConsoleStoreState;
  hydrateIfNeeded: () => ConsoleStoreState;
  rehydrateFromStorage: () => ConsoleStoreState;
  setState: (
    updater:
      | Partial<ConsoleStoreState>
      | ((prev: ConsoleStoreState) => ConsoleStoreState),
    opts?: { persist?: boolean; touchHydratedAt?: boolean }
  ) => ConsoleStoreState;
  setDashboard: (next: React.SetStateAction<DashboardPayload | null>) => void;
  setActivePage: (page: PageKey | string) => PageKey;
  setSelectedPlanetId: (planetId: number | null) => void;
  setUiPref: <K extends keyof UiPrefs>(key: K, value: UiPrefs[K]) => void;
  setUiPrefs: (next: React.SetStateAction<UiPrefs>) => void;
  setBuildingsEtaData: (data: Record<string, any> | null) => void;
  setCombatData: (data: Record<string, any> | null) => void;
  setActionGraphData: (data: Record<string, any> | null) => void;
  setPlannerJobsStatus: (status: PlannerJobsStatusPayload | null) => void;
  setSectorsData: (data: Record<string, any> | null) => void;
  setEspData: (data: Record<string, any> | null) => void;
  setColonyData: (data: Record<string, any> | null) => void;
  setAtkData: (data: Record<string, any> | null) => void;
  setRecData: (data: Record<string, any> | null) => void;
  setLastOptimizerSummary: (summary: Record<string, any> | null) => void;
  setNotifications: (notifications: NotificationsState) => void;
  setApiOnline: (online: boolean) => void;
  flushPersistence: (force?: boolean) => boolean;
  hasPendingPersist: () => boolean;
  reset: (opts?: ResetConsoleStoreOptions) => void;
  destroy: () => void;
}

type StateChangeHook = (state: ConsoleStoreState) => void;

export function createConsoleStore(options: CreateConsoleStoreOptions = {}): ConsoleStore {
  const persistEnabled = options.persist ?? false;
  const debounceMs = options.debounceMs ?? PERSIST_DEBOUNCE_MS;
  const maxPersistChars = options.maxPersistChars ?? SOFT_MAX_PERSIST_CHARS;
  const storageKey = options.storageKey || CONSOLE_STORE_STORAGE_KEY;
  const legacyKey = options.legacyKey || LEGACY_SESSION_STATE_KEY;

  const listeners = new Set<() => void>();
  let snapshot: ConsoleStoreState = createDefaultConsoleStoreState();
  let needsHydration = Boolean(persistEnabled);
  let pendingPersist = false;
  let persistTimer: ReturnType<typeof setTimeout> | null = null;
  let unloadBound = false;

  const emit = () => {
    listeners.forEach((listener) => {
      listener();
    });
  };

  const clearPersistTimer = () => {
    if (persistTimer !== null) {
      clearTimeout(persistTimer);
      persistTimer = null;
    }
  };

  const flushPersistence = (force = false): boolean => {
    clearPersistTimer();
    if (!persistEnabled || typeof window === "undefined") {
      pendingPersist = false;
      return false;
    }
    if (!pendingPersist && !force) {
      return false;
    }
    pendingPersist = false;
    const res = persistConsoleStateToStorage(snapshot, {
      storageKey,
      maxChars: maxPersistChars,
    });
    return res.ok;
  };

  const schedulePersist = () => {
    if (!persistEnabled || typeof window === "undefined") return;
    pendingPersist = true;
    clearPersistTimer();
    if (debounceMs <= 0) {
      flushPersistence(true);
      return;
    }
    persistTimer = setTimeout(() => {
      persistTimer = null;
      flushPersistence(false);
    }, debounceMs);
  };

  const onUnload = () => {
    flushPersistence(false);
  };

  const onVisibilityChange = () => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      flushPersistence(false);
    }
  };

  const bindUnload = () => {
    if (!persistEnabled || unloadBound || typeof window === "undefined") return;
    unloadBound = true;
    window.addEventListener("pagehide", onUnload);
    window.addEventListener("beforeunload", onUnload);
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
  };

  const unbindUnload = () => {
    if (!unloadBound || typeof window === "undefined") return;
    unloadBound = false;
    window.removeEventListener("pagehide", onUnload);
    window.removeEventListener("beforeunload", onUnload);
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  };

  const hydrateSilent = (): ConsoleStoreState => {
    needsHydration = false;
    if (!persistEnabled || typeof window === "undefined") {
      return snapshot;
    }
    snapshot = readPersistedConsoleState(storageKey, legacyKey);
    return snapshot;
  };

  if (options.hydrateOnInit && persistEnabled && typeof window !== "undefined") {
    hydrateSilent();
  }
  if (options.bindUnloadEvents && persistEnabled && typeof window !== "undefined") {
    bindUnload();
  }

  const hydrateIfNeeded = (): ConsoleStoreState => {
    if (!needsHydration) return snapshot;
    return hydrateSilent();
  };

  const rehydrateFromStorage = (): ConsoleStoreState => {
    const prev = snapshot;
    hydrateSilent();
    if (snapshot !== prev) {
      emit();
    }
    return snapshot;
  };

  const setState: ConsoleStore["setState"] = (updater, opts) => {
    const prev = snapshot;
    let next: ConsoleStoreState;
    if (typeof updater === "function") {
      next = updater(prev);
    } else {
      let changed = false;
      for (const key of Object.keys(updater) as Array<keyof ConsoleStoreState>) {
        if (!Object.is(prev[key], updater[key])) {
          changed = true;
          break;
        }
      }
      if (!changed && !opts?.touchHydratedAt) {
        return prev;
      }
      next = {
        ...prev,
        ...updater,
      };
    }

    if (opts?.touchHydratedAt) {
      const now = Date.now();
      if (next === prev) {
        next = { ...prev, lastHydratedAt: now };
      } else {
        next = { ...next, lastHydratedAt: now };
      }
    }

    if (Object.is(next, prev)) {
      return prev;
    }

    snapshot = next;
    needsHydration = false;
    if (opts?.persist !== false) {
      schedulePersist();
    }
    emit();
    return snapshot;
  };

  const setDashboard: ConsoleStore["setDashboard"] = (next) => {
    setState(
      (prev) => {
        const resolved = typeof next === "function" ? next(prev.dashboard) : next;
        if (Object.is(prev.dashboard, resolved)) return prev;
        return {
          ...prev,
          dashboard: resolved,
          lastHydratedAt: resolved ? Date.now() : prev.lastHydratedAt,
        };
      },
      { persist: true }
    );
  };

  const setActivePage: ConsoleStore["setActivePage"] = (rawKey) => {
    const target = normalizePageKey(rawKey);
    setState({ activePage: target }, { persist: true });
    return target;
  };

  const setSelectedPlanetId: ConsoleStore["setSelectedPlanetId"] = (planetId) => {
    const pid = planetId == null ? null : safeFiniteNumber(planetId, null);
    setState({ selectedPlanetId: pid }, { persist: true });
  };

  const setUiPref: ConsoleStore["setUiPref"] = (key, value) => {
    setState(
      (prev) => {
        if (Object.is(prev.uiPrefs[key], value)) return prev;
        return {
          ...prev,
          uiPrefs: {
            ...prev.uiPrefs,
            [key]: value,
          },
        };
      },
      { persist: key !== "sidebarOpen" }
    );
  };

  const setUiPrefs: ConsoleStore["setUiPrefs"] = (next) => {
    setState(
      (prev) => {
        const resolved = typeof next === "function" ? next(prev.uiPrefs) : next;
        if (Object.is(prev.uiPrefs, resolved)) return prev;
        return { ...prev, uiPrefs: resolved };
      },
      { persist: true }
    );
  };

  const setBuildingsEtaData: ConsoleStore["setBuildingsEtaData"] = (data) => {
    setState({ buildingsEtaData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setCombatData: ConsoleStore["setCombatData"] = (data) => {
    setState({ combatData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setActionGraphData: ConsoleStore["setActionGraphData"] = (data) => {
    setState({ actionGraphData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setPlannerJobsStatus: ConsoleStore["setPlannerJobsStatus"] = (status) => {
    setState({ plannerJobsStatus: status }, { persist: true, touchHydratedAt: Boolean(status) });
  };

  const setSectorsData: ConsoleStore["setSectorsData"] = (data) => {
    setState({ sectorsData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setEspData: ConsoleStore["setEspData"] = (data) => {
    setState({ espData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setColonyData: ConsoleStore["setColonyData"] = (data) => {
    setState({ colonyData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setAtkData: ConsoleStore["setAtkData"] = (data) => {
    setState({ atkData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setRecData: ConsoleStore["setRecData"] = (data) => {
    setState({ recData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setLastOptimizerSummary: ConsoleStore["setLastOptimizerSummary"] = (summary) => {
    setState(
      { lastOptimizerSummary: summary },
      { persist: true, touchHydratedAt: Boolean(summary) }
    );
  };

  const setNotifications: ConsoleStore["setNotifications"] = (notifications) => {
    setState(
      { notifications },
      { persist: true, touchHydratedAt: notifications.items.length > 0 }
    );
  };

  const setApiOnline: ConsoleStore["setApiOnline"] = (online) => {
    setState({ apiOnline: Boolean(online) }, { persist: true });
  };

  const reset: ConsoleStore["reset"] = (opts) => {
    clearPersistTimer();
    pendingPersist = false;
    if (opts?.clearStorage && typeof window !== "undefined") {
      try {
        window.localStorage?.removeItem(storageKey);
      } catch {
        // Ignore storage errors
      }
      try {
        window.sessionStorage?.removeItem(storageKey);
        window.sessionStorage?.removeItem(legacyKey);
      } catch {
        // Ignore storage errors
      }
    }
    snapshot = createDefaultConsoleStoreState();
    needsHydration = Boolean(persistEnabled);
    emit();
  };

  const destroy = () => {
    clearPersistTimer();
    pendingPersist = false;
    unbindUnload();
    listeners.clear();
  };

  return {
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    getServerSnapshot: () => SERVER_CONSOLE_SNAPSHOT,
    hydrateIfNeeded,
    rehydrateFromStorage,
    setState,
    setDashboard,
    setActivePage,
    setSelectedPlanetId,
    setUiPref,
    setUiPrefs,
    setBuildingsEtaData,
    setCombatData,
    setActionGraphData,
    setPlannerJobsStatus,
    setSectorsData,
    setEspData,
    setColonyData,
    setAtkData,
    setRecData,
    setLastOptimizerSummary,
    setNotifications,
    setApiOnline,
    flushPersistence,
    hasPendingPersist: () => pendingPersist,
    reset,
    destroy,
  };
}

let allowPersistWhenInitialFetchDisabled = false;
const resetHooks = new Set<StateChangeHook>();

export function setConsoleStorePersistenceForTests(enabled: boolean): void {
  allowPersistWhenInitialFetchDisabled = Boolean(enabled);
}

export function getConsoleStorePersistenceForTests(): boolean {
  return allowPersistWhenInitialFetchDisabled;
}

export function registerConsoleStoreResetHook(hook: StateChangeHook): () => void {
  resetHooks.add(hook);
  return () => {
    resetHooks.delete(hook);
  };
}

export const consoleStore: ConsoleStore = createConsoleStore({
  persist: true,
  hydrateOnInit: true,
  bindUnloadEvents: true,
});

export function useConsoleStore(): ConsoleStoreState {
  return useSyncExternalStore(
    consoleStore.subscribe,
    consoleStore.getSnapshot,
    consoleStore.getServerSnapshot
  );
}

export function flushConsoleStore(force = false): boolean {
  return consoleStore.flushPersistence(force);
}

export function rehydrateConsoleStore(): ConsoleStoreState {
  const next = consoleStore.rehydrateFromStorage();
  resetHooks.forEach((hook) => hook(next));
  return next;
}

export function resetConsoleStore(opts?: ResetConsoleStoreOptions): void {
  consoleStore.reset(opts);
  const next = consoleStore.getSnapshot();
  resetHooks.forEach((hook) => hook(next));
}
