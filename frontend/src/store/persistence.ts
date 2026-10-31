import { safeFiniteNumber } from "../api/httpClient";
import {
  DashboardPayload,
  NotificationsState,
  PAGE_TITLES,
  PageKey,
  PlannerJobsStatusPayload,
} from "../types/api";
import {
  CONSOLE_STORE_SCHEMA_VERSION,
  CONSOLE_STORE_STORAGE_KEY,
  ConsoleRuntimeState,
  ConsoleStoreState,
  DEFAULT_UI_PREFS,
  LEGACY_SESSION_STATE_KEY,
  SOFT_MAX_PERSIST_CHARS,
  UiPrefs,
  VALID_AG_TABS,
} from "./types";

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

export function createDefaultRuntimeState(): ConsoleRuntimeState {
  return {
    reconnectAttempts: 0,
    nowEpochSec: typeof window === "undefined" ? 0 : Math.floor(Date.now() / 1000),
    gaCalcCount: 0,
    feedback: null,
    agSimDirty: false,
    agSimRunning: 0,
    agSimSeq: 0,
    configDirty: false,
    configSaving: 0,
    configHydrationTick: 0,
    agHydrationTick: 0,
    notifCenterOpen: false,
    activeHelpNotif: null,
    dashboardSeq: 0,
    inFlightDashboardCount: 0,
    inFlightVersionCheck: false,
    simPopulatedOnce: false,
  };
}

export const SERVER_RUNTIME_SNAPSHOT: ConsoleRuntimeState = Object.freeze({
  reconnectAttempts: 0,
  nowEpochSec: 0,
  gaCalcCount: 0,
  feedback: null,
  agSimDirty: false,
  agSimRunning: 0,
  agSimSeq: 0,
  configDirty: false,
  configSaving: 0,
  configHydrationTick: 0,
  agHydrationTick: 0,
  notifCenterOpen: false,
  activeHelpNotif: null,
  dashboardSeq: 0,
  inFlightDashboardCount: 0,
  inFlightVersionCheck: false,
  simPopulatedOnce: false,
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
  if (typeof window === "undefined") return next;

  let loadedFromStoreKey = false;
  const local = safeGetStorage("local");
  const session = safeGetStorage("session");

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
      if (savedPid !== null) next.selectedPlanetId = savedPid;
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
      if (tier < 4 && json.length > maxChars) continue;
      try {
        local.setItem(storageKey, json);
        return { ok: true, storage: "local", tier };
      } catch (err) {
        if (isQuotaExceededError(err)) continue;
        break;
      }
    }
  }

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
      if (tier < 4 && json.length > maxChars) continue;
      try {
        session.setItem(storageKey, json);
        return { ok: true, storage: "session", tier };
      } catch (err) {
        if (isQuotaExceededError(err)) continue;
        break;
      }
    }
  }

  return { ok: false, storage: "memory", tier: -1 };
}
