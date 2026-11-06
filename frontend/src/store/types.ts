import type React from "react";
import {
  ActionGraphTabKey,
  DashboardPayload,
  FeedbackKind,
  FeedbackState,
  NotificationsState,
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

export interface ConsoleRuntimeState {
  reconnectAttempts: number;
  nowEpochSec: number;
  gaCalcCount: number;
  feedback: FeedbackState | null;
  agSimDirty: boolean;
  agSimRunning: number;
  agSimSeq: number;
  configDirty: boolean;
  configSaving: number;
  configHydrationTick: number;
  agHydrationTick: number;
  notifCenterOpen: boolean;
  activeHelpNotif: Record<string, any> | null;
  dashboardSeq: number;
  inFlightDashboardCount: number;
  inFlightVersionCheck: boolean;
  simPopulatedOnce: boolean;
}

export interface SectorQueuePreviewOverrides {
  refresh_pct?: number;
  sweep_pages?: number;
  all_galaxies?: boolean;
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
  subscribeRuntime: (listener: () => void) => () => void;
  getRuntimeSnapshot: () => ConsoleRuntimeState;
  getServerRuntimeSnapshot: () => ConsoleRuntimeState;
  setRuntime: (
    updater:
      | Partial<ConsoleRuntimeState>
      | ((prev: ConsoleRuntimeState) => ConsoleRuntimeState)
  ) => ConsoleRuntimeState;
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

export interface ConsoleActions {
  getSnapshot: () => ConsoleStoreState;
  getRuntime: () => ConsoleRuntimeState;
  setDashboard: React.Dispatch<React.SetStateAction<DashboardPayload | null>>;
  setActivePage: (page: PageKey | string) => void;
  setSelectedPlanetId: (planetId: number | null) => void;
  setUiPref: <K extends keyof UiPrefs>(key: K, value: UiPrefs[K]) => void;
  setApiStatus: (online: boolean, note?: string) => void;
  beginGaCalc: () => void;
  endGaCalc: () => void;
  showFeedback: (
    message: string,
    kind?: FeedbackKind,
    opts?: { autoHideMs?: number }
  ) => void;
  apiGet: <T = Record<string, any>>(url: string, timeoutMs?: number) => Promise<T>;
  apiCommand: <T = Record<string, any>>(
    payload: Record<string, any>,
    opts?: { timeoutMs?: number }
  ) => Promise<T>;
  loadDashboard: (opts?: {
    refresh?: boolean;
    preserveConfig?: boolean;
    force?: boolean;
  }) => Promise<boolean>;
  checkApiHealthAndHotReload: () => Promise<void>;
  bumpDashboardSeq: () => number;
  bumpAgSimSeq: () => number;
  bumpConfigHydration: () => void;
  setSimPopulatedOnce: (value: boolean) => void;
  setNowEpochSec: (epochSec: number) => void;
  setBuildingsEtaData: (data: Record<string, any> | null) => void;
  refreshBuildingsEtaForSelectedPlanet: (forceRefresh?: boolean) => Promise<void>;
  setCombatData: (data: Record<string, any> | null) => void;
  refreshCombatForSelectedPlanet: (opts?: { refresh?: boolean }) => Promise<boolean>;
  setSectorsData: (data: Record<string, any> | null) => void;
  refreshSectorsView: (
    galaxyOverride?: number,
    includeUnscannedOverride?: boolean,
    previewOverride?: SectorQueuePreviewOverrides | null,
    opts?: { background?: boolean }
  ) => Promise<void>;
  setEspData: (data: Record<string, any> | null) => void;
  refreshEspionageView: () => Promise<void>;
  setColonyData: (data: Record<string, any> | null) => void;
  refreshColoniesView: (opts?: { background?: boolean }) => Promise<void>;
  setAtkData: (data: Record<string, any> | null) => void;
  refreshAttacksView: () => Promise<void>;
  setRecData: (data: Record<string, any> | null) => void;
  refreshRecyclersView: (categoryOverride?: string) => Promise<void>;
  setActionGraphData: (data: Record<string, any> | null) => void;
  setPlannerJobsStatus: (status: PlannerJobsStatusPayload | null) => void;
  refreshPlannerJobsStatus: () => Promise<PlannerJobsStatusPayload | null>;
  setAgSimDirty: (dirty: boolean) => void;
  setAgSimRunning: React.Dispatch<React.SetStateAction<number>>;
  setConfigDirty: (dirty: boolean) => void;
  setConfigSaving: React.Dispatch<React.SetStateAction<number>>;
  setLastOptimizerSummary: (summary: Record<string, any> | null) => void;
  syncNotificationsPayload: (payload: Record<string, any> | null | undefined) => void;
  openNotificationCenter: () => Promise<void>;
  closeNotificationCenter: () => void;
  refreshNotificationCenter: (filterOverride?: string) => Promise<void>;
  ackActiveHelpPopup: (targetStatus: "read" | "dismissed") => Promise<void>;
  closeHelpPopupOnly: () => void;
  toggleBlastFurnaceRate: (targetPct: number) => Promise<void>;
  syncAllInboxFolders: () => Promise<void>;
  enqueueManualCommand: (payload: Record<string, any>) => Promise<Record<string, any> | null>;
  cleanup: () => void;
}
