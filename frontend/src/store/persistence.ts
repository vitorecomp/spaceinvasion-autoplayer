import { safeFiniteNumber } from "../api/httpClient";
import {
  DashboardPayload,
  NotificationsState,
  PAGE_TITLES,
  PageKey,
  PlannerJobsStatusPayload,
} from "../types/api";
import { buildPersistedPayload, isPlainObject } from "./compaction";
import {
  CONSOLE_STORE_STORAGE_KEY,
  ConsoleRuntimeState,
  ConsoleStoreState,
  DEFAULT_UI_PREFS,
  LEGACY_SESSION_STATE_KEY,
  SOFT_MAX_PERSIST_CHARS,
  UiPrefs,
  VALID_AG_TABS,
} from "./types";

export { buildPersistedPayload };

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
    if (v !== undefined) prefs[k] = v;
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
  if (!isPlainObject(raw)) return { unread_count: 0, items: [] };
  const items = Array.isArray(raw.items) ? raw.items : [];
  const unreadCount = Math.max(0, Math.round(safeFiniteNumber(raw.unread_count, 0) ?? 0));
  return { unread_count: unreadCount, items };
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

      if (saved.activePage !== undefined) next.activePage = normalizePageKey(saved.activePage);
      const savedPid = safeFiniteNumber(saved.selectedPlanetId, null);
      if (savedPid !== null) next.selectedPlanetId = savedPid;
      next.uiPrefs = sanitizeUiPrefs(saved.uiPrefs);

      if (isPlainObject(saved.dashboard)) next.dashboard = saved.dashboard as DashboardPayload;
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

      next.isHydratedFromCache = Boolean(
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
          if (legacy.activePage !== undefined) next.activePage = normalizePageKey(legacy.activePage);
        }
      }
    } catch {
      // Ignore storage restrictions
    }
  }

  try {
    const rawHash = (window.location.hash || "").replace(/^#/, "");
    const normHash = rawHash === "graph" ? "action-graph" : rawHash;
    if (isSupportedPageKey(normHash)) next.activePage = normHash as PageKey;
  } catch {
    // Ignore hash read errors
  }

  return next;
}

export function persistConsoleStateToStorage(
  state: ConsoleStoreState,
  opts?: { storageKey?: string; maxChars?: number }
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
