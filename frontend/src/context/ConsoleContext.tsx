import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { flushSync } from "react-dom";
import {
  apiCommand as rawApiCommand,
  apiGet as rawApiGet,
  feedbackKindForError,
  isRateLimitError,
  isSweepBlockedError,
  parseResponseJson,
  registerHttpBridgeCallbacks,
  safeFiniteNumber,
} from "../api/httpClient";
import {
  ConsoleStore,
  ConsoleStoreState,
  DEFAULT_UI_PREFS,
  UiPrefs,
  consoleStore,
  createConsoleStore,
  flushConsoleStore,
  getConsoleStorePersistenceForTests,
  normalizePageKey,
  registerConsoleStoreResetHook,
  rehydrateConsoleStore,
  resetConsoleStore,
} from "../store/consoleStore";
import {
  ActionGraphTabKey,
  DashboardPayload,
  FeedbackKind,
  FeedbackState,
  NotificationsState,
  PageKey,
  PlanetData,
  PlannerJobsStatusPayload,
} from "../types/api";

export type { UiPrefs };
export {
  DEFAULT_UI_PREFS,
  consoleStore,
  flushConsoleStore,
  rehydrateConsoleStore,
  resetConsoleStore,
};

const useClientSyncEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

export interface LegacyWindowState {
  dashboard: DashboardPayload | null;
  selectedPlanetId: number | null;
  modeOverride: string;
  activePage: PageKey;
  bldCategory: string;
  selectedUnitKey: string;
  resTab: string;
  selectedResearchKey: string;
  buildingsEtaData: Record<string, any> | null;
  combatData: Record<string, any> | null;
  combatSubTab: string;
  queueFilter: string;
  activeLogSource: string;
  simPopulatedOnce: boolean;
  apiOnline: boolean;
  reconnectAttempts: number;
  configDirty: boolean;
  configSaving: number;
  gaCalcCount: number;
  readonly gaCalcRunning: boolean;
  lastOptimizerSummary: Record<string, any> | null;
  inFlightVersionCheck: boolean;
  inFlightDashboard: boolean;
  inFlightDashboardCount: number;
  dashboardSeq: number;
  actionGraphData: Record<string, any> | null;
  plannerJobsStatus: PlannerJobsStatusPayload | null;
  agActiveTab: ActionGraphTabKey;
  agSimDirty: boolean;
  agSimRunning: number;
  agSimSeq: number;
  agEdgeFilter: "all" | "deps";
  agParamTab: "core" | "scope";
  agSelectedNodeId: string | null;
  agSuppressNodeClick: boolean;
  sectorFilter: string;
  sectorGalaxy: number;
  sectorsData: Record<string, any> | null;
  espTab: string;
  espData: Record<string, any> | null;
  colonyFilter: string;
  colonyData: Record<string, any> | null;
  atkTab: string;
  atkData: Record<string, any> | null;
  recTab: string;
  emailCatFilter: string;
  recData: Record<string, any> | null;
  notifications: NotificationsState;
  notifFilter: string;
  activeHelpNotifId: number | null;
  uiPrefs?: UiPrefs;
  lastHydratedAt?: number | null;
  isHydratedFromCache?: boolean;
  [key: string]: any;
}

function createInitialLegacyWindowState(): LegacyWindowState {
  const st: any = {
    dashboard: null,
    selectedPlanetId: null,
    modeOverride: "auto",
    activePage: "dashboard",
    bldCategory: "all",
    selectedUnitKey: "b1",
    resTab: "all",
    selectedResearchKey: "r1",
    buildingsEtaData: null,
    combatData: null,
    combatSubTab: "ships",
    queueFilter: "all",
    activeLogSource: "commander",
    simPopulatedOnce: false,
    apiOnline: false,
    reconnectAttempts: 0,
    configDirty: false,
    configSaving: 0,
    gaCalcCount: 0,
    lastOptimizerSummary: null,
    inFlightVersionCheck: false,
    inFlightDashboard: false,
    inFlightDashboardCount: 0,
    dashboardSeq: 0,
    actionGraphData: null,
    plannerJobsStatus: null,
    agActiveTab: "heuristics",
    agSimDirty: false,
    agSimRunning: 0,
    agSimSeq: 0,
    agEdgeFilter: "all",
    agParamTab: "core",
    agSelectedNodeId: null,
    agSuppressNodeClick: false,
    sectorFilter: "all",
    sectorGalaxy: 0,
    sectorsData: null,
    espTab: "targets",
    espData: null,
    colonyFilter: "all",
    colonyData: null,
    atkTab: "battles",
    atkData: null,
    recTab: "harvests",
    emailCatFilter: "all",
    recData: null,
    notifications: { unread_count: 0, items: [] },
    notifFilter: "active",
    activeHelpNotifId: null,
    uiPrefs: { ...DEFAULT_UI_PREFS },
    lastHydratedAt: null,
    isHydratedFromCache: false,
  };
  Object.defineProperty(st, "gaCalcRunning", {
    enumerable: true,
    configurable: true,
    get() {
      return st.gaCalcCount > 0;
    },
    set(flag: boolean) {
      st.gaCalcCount = flag ? Math.max(1, st.gaCalcCount) : 0;
    },
  });
  return st as LegacyWindowState;
}

const sharedLegacyState: LegacyWindowState = createInitialLegacyWindowState();

function syncLegacyStateFromSnapshot(snap: ConsoleStoreState): void {
  sharedLegacyState.dashboard = snap.dashboard;
  sharedLegacyState.apiOnline = snap.apiOnline;
  sharedLegacyState.activePage = snap.activePage;
  sharedLegacyState.selectedPlanetId = snap.selectedPlanetId;
  sharedLegacyState.uiPrefs = snap.uiPrefs;
  sharedLegacyState.modeOverride = snap.uiPrefs.modeOverride;
  sharedLegacyState.bldCategory = snap.uiPrefs.bldCategory;
  sharedLegacyState.selectedUnitKey = snap.uiPrefs.selectedUnitKey;
  sharedLegacyState.resTab = snap.uiPrefs.resTab;
  sharedLegacyState.selectedResearchKey = snap.uiPrefs.selectedResearchKey;
  sharedLegacyState.combatSubTab = snap.uiPrefs.combatSubTab;
  sharedLegacyState.queueFilter = snap.uiPrefs.queueFilter;
  sharedLegacyState.activeLogSource = snap.uiPrefs.activeLogSource;
  sharedLegacyState.agActiveTab = snap.uiPrefs.agActiveTab;
  sharedLegacyState.agEdgeFilter = snap.uiPrefs.agEdgeFilter;
  sharedLegacyState.agParamTab = snap.uiPrefs.agParamTab;
  sharedLegacyState.sectorFilter = snap.uiPrefs.sectorFilter;
  sharedLegacyState.sectorGalaxy = snap.uiPrefs.sectorGalaxy;
  sharedLegacyState.espTab = snap.uiPrefs.espTab;
  sharedLegacyState.colonyFilter = snap.uiPrefs.colonyFilter;
  sharedLegacyState.atkTab = snap.uiPrefs.atkTab;
  sharedLegacyState.recTab = snap.uiPrefs.recTab;
  sharedLegacyState.emailCatFilter = snap.uiPrefs.emailCatFilter;
  sharedLegacyState.notifFilter = snap.uiPrefs.notifFilter;
  sharedLegacyState.buildingsEtaData = snap.buildingsEtaData;
  sharedLegacyState.combatData = snap.combatData;
  sharedLegacyState.sectorsData = snap.sectorsData;
  sharedLegacyState.espData = snap.espData;
  sharedLegacyState.colonyData = snap.colonyData;
  sharedLegacyState.atkData = snap.atkData;
  sharedLegacyState.recData = snap.recData;
  sharedLegacyState.actionGraphData = snap.actionGraphData;
  sharedLegacyState.plannerJobsStatus = snap.plannerJobsStatus;
  sharedLegacyState.lastOptimizerSummary = snap.lastOptimizerSummary;
  sharedLegacyState.notifications = snap.notifications;
  sharedLegacyState.lastHydratedAt = snap.lastHydratedAt;
  sharedLegacyState.isHydratedFromCache = snap.isHydratedFromCache;
}

function resetTransientLegacyFlags(): void {
  sharedLegacyState.simPopulatedOnce = false;
  sharedLegacyState.reconnectAttempts = 0;
  sharedLegacyState.configDirty = false;
  sharedLegacyState.configSaving = 0;
  sharedLegacyState.gaCalcCount = 0;
  sharedLegacyState.inFlightVersionCheck = false;
  sharedLegacyState.inFlightDashboard = false;
  sharedLegacyState.inFlightDashboardCount = 0;
  sharedLegacyState.dashboardSeq = 0;
  sharedLegacyState.agSimDirty = false;
  sharedLegacyState.agSimRunning = 0;
  sharedLegacyState.agSimSeq = 0;
  sharedLegacyState.agSelectedNodeId = null;
  sharedLegacyState.agSuppressNodeClick = false;
  sharedLegacyState.activeHelpNotifId = null;
}

// Synchronize initial browser snapshot & bind reset/rehydrate hooks onto sharedLegacyState
if (typeof window !== "undefined") {
  syncLegacyStateFromSnapshot(consoleStore.getSnapshot());
}

registerConsoleStoreResetHook((snap) => {
  resetTransientLegacyFlags();
  syncLegacyStateFromSnapshot(snap);
});

if (typeof globalThis !== "undefined") {
  const g = globalThis as any;
  g.state = sharedLegacyState;
  g.__SI_STATE__ = sharedLegacyState;
  g.el = (id: string) => (typeof document !== "undefined" ? document.getElementById(id) : null);
  g.apiGet = rawApiGet;
  g.apiCommand = rawApiCommand;
  g.parseResponseJson = parseResponseJson;
  g.feedbackKindForError = feedbackKindForError;
  if (typeof window !== "undefined") {
    (window as any).state = sharedLegacyState;
    (window as any).__SI_STATE__ = sharedLegacyState;
    (window as any).el = g.el;
  }
}

export function selectActivePlanet(
  dash: DashboardPayload | null | undefined,
  selectedPlanetId?: number | null
): PlanetData | null {
  const gs = dash?.state;
  if (!gs || !Array.isArray(gs.planets) || gs.planets.length === 0) return null;
  const targetId = selectedPlanetId || gs.active_planet_id;
  return gs.planets.find((p) => p.planet_id === targetId) || gs.planets[0] || null;
}

export function buildClientFallbackActionGraph(
  dash: DashboardPayload | null | undefined
): Record<string, any> {
  const cfg = dash?.config || {};
  const rawMode = String(
    dash?.effective_planner_mode || cfg.planner_mode || dash?.planner_mode || "priority"
  )
    .trim()
    .toLowerCase();
  const mode = rawMode === "ai_planner" ? "ai_planner" : "priority";
  const planList = Array.isArray(dash?.plan) ? dash!.plan! : [];
  const planets = Array.isArray(dash?.state?.planets) ? dash!.state!.planets! : [];
  const planetById = new Map<number, PlanetData>();
  planets.forEach((p) => {
    const pid = safeFiniteNumber(p?.planet_id, 0) ?? 0;
    if (pid > 0) planetById.set(pid, p);
  });

  const laneCounts = new Map<string, number>([["lane:global", 0]]);
  planets.forEach((p) => {
    const pid = safeFiniteNumber(p?.planet_id, 0) ?? 0;
    if (pid > 0) laneCounts.set(`lane:planet:${pid}`, 0);
  });

  let execRank = 0;
  const nodes = planList.map((act, idx) => {
    const kind = String(act?.kind || "BUILD").toUpperCase();
    const pid = Math.max(0, Math.round(safeFiniteNumber(act?.planet_id, 0) ?? 0));
    const pObj = planetById.get(pid) || null;
    const laneId =
      kind === "RESEARCH" || kind === "CLAIM_TUTORIAL" || pid <= 0
        ? "lane:global"
        : `lane:planet:${pid}`;
    laneCounts.set(laneId, (laneCounts.get(laneId) || 0) + 1);
    const execNow = Boolean(act?.executable_now);
    const missing = {
      pig_iron: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.pig_iron, 0) ?? 0)),
      metal: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.metal, 0) ?? 0)),
      kryptonite: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.kryptonite, 0) ?? 0)),
      spice: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.spice, 0) ?? 0)),
    };
    const hasMissing =
      missing.pig_iron > 0 || missing.metal > 0 || missing.kryptonite > 0 || missing.spice > 0;
    const reason = String(act?.reason || "");
    const status = execNow
      ? "ready"
      : kind === "ALERT_HOSTILE" || reason.toLowerCase().includes("[hold:")
        ? "blocked_gate"
        : hasMissing || kind === "WAIT_RESOURCES"
          ? "waiting_resources"
          : "queued";
    return {
      id: `act_${idx}`,
      order_index: idx,
      exec_rank: execNow ? execRank++ : null,
      topological_layer: idx,
      lane_id: laneId,
      priority: String(act?.priority || "P2"),
      kind,
      category: kind === "RESEARCH" ? "research" : "economy",
      status,
      planet_id: pid,
      planet_coords: String(act?.planet_coords || pObj?.coords || ""),
      planet_name: String(pObj?.name || (pid > 0 ? `Colony ${pid}` : "Empire")),
      target_id: safeFiniteNumber(act?.target_id, null),
      target_name: String(act?.target_name || kind),
      target_level: safeFiniteNumber(act?.target_level, null),
      reason,
      executable_now: execNow,
      cost: {
        pig_iron: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.pig_iron, 0) ?? 0)),
        metal: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.metal, 0) ?? 0)),
        kryptonite: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.kryptonite, 0) ?? 0)),
        spice: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.spice, 0) ?? 0)),
      },
      missing,
      wait_seconds: safeFiniteNumber(act?.wait_seconds, 0) ?? 0,
      wait_eta: String(act?.wait_eta || "Ready"),
      duration_seconds: Math.max(0, safeFiniteNumber(act?.duration_seconds, 0) ?? 0),
      duration_text: String(act?.duration_text || "Instant"),
      total_seconds: safeFiniteNumber(act?.total_seconds, 0) ?? 0,
      total_eta: String(act?.total_eta || "Ready"),
      eta_bottleneck: act?.eta_bottleneck ? String(act.eta_bottleneck) : null,
      ga_step: null,
      in_degree: idx > 0 ? 1 : 0,
      out_degree: idx + 1 < planList.length ? 1 : 0,
    };
  });

  const edges: Array<Record<string, any>> = [];
  for (let i = 0; i + 1 < nodes.length; i += 1) {
    const pType = nodes[i].lane_id === nodes[i + 1].lane_id ? "queue_order" : "execution_order";
    edges.push({
      id: `edge_act_${i}_act_${i + 1}`,
      source: `act_${i}`,
      target: `act_${i + 1}`,
      primary_type: pType,
      types: [pType],
      label: `Planned step #${i + 1} -> #${i + 2}`,
    });
  }

  const lanes: Array<Record<string, any>> = [];
  if (planets.length > 0 || nodes.length > 0) {
    lanes.push({
      id: "lane:global",
      scope: "global",
      label: "Empire & Global Research",
      planet_id: 0,
      coords: "",
      planet_type: "global",
      energy: 0,
      node_count: laneCounts.get("lane:global") || 0,
    });
    planets.forEach((p) => {
      const pid = Math.max(0, Math.round(safeFiniteNumber(p?.planet_id, 0) ?? 0));
      if (pid <= 0) return;
      const lid = `lane:planet:${pid}`;
      lanes.push({
        id: lid,
        scope: "planet",
        label: p?.coords
          ? `${p.name || `Colony ${pid}`} [${p.coords}]`
          : String(p?.name || `Colony ${pid}`),
        planet_id: pid,
        coords: String(p?.coords || ""),
        planet_type: String(p?.planet_type || "normal"),
        energy: Math.round(
          (safeFiniteNumber(p?.energy?.produced, 0) ?? 0) -
            (safeFiniteNumber(p?.energy?.used, 0) ?? 0)
        ),
        node_count: laneCounts.get(lid) || 0,
      });
    });
  }

  return {
    mode,
    simulated: false,
    state_available: Boolean(dash?.state),
    is_dirty: false,
    changed_keys: [],
    active_config: cfg,
    effective_config: cfg,
    lanes,
    nodes,
    edges,
    summary: {
      total_nodes: nodes.length,
      total_edges: edges.length,
      ready_count: nodes.filter((n) => n.status === "ready").length,
      waiting_count: nodes.filter((n) => n.status === "waiting_resources").length,
      blocked_count: nodes.filter((n) => n.status === "blocked_gate").length,
      queued_count: nodes.filter((n) => n.status === "queued").length,
      max_layer: Math.max(0, nodes.length - 1),
      critical_path_eta:
        nodes.length > 0 ? String(nodes[nodes.length - 1].total_eta || "Ready") : "Ready",
      primary_bottleneck: null,
      by_priority: { P0: 0, P1: 0, P2: nodes.length, P3: 0 },
      by_edge_type: {},
    },
    ga_summary: dash?.optimizer_summary || null,
  };
}

export interface SectorQueuePreviewOverrides {
  refresh_pct?: number;
  sweep_pages?: number;
  all_galaxies?: boolean;
}

export interface ConsoleContextValue {
  dashboard: DashboardPayload | null;
  setDashboard: React.Dispatch<React.SetStateAction<DashboardPayload | null>>;
  apiOnline: boolean;
  reconnectAttempts: number;
  chromeOnline: boolean;
  isHydratedFromCache?: boolean;
  lastHydratedAt?: number | null;
  activePage: PageKey;
  setActivePage: (page: PageKey | string) => void;
  selectedPlanetId: number | null;
  setSelectedPlanetId: (planetId: number | null) => void;
  activePlanet: PlanetData | null;
  nowEpochSec: number;
  gaCalculating: boolean;
  gaCalcCount: number;
  beginGaCalc: () => void;
  endGaCalc: () => void;
  feedback: FeedbackState | null;
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
  bumpDashboardSeq: () => number;
  uiPrefs: UiPrefs;
  setUiPref: <K extends keyof UiPrefs>(key: K, value: UiPrefs[K]) => void;
  buildingsEtaData: Record<string, any> | null;
  setBuildingsEtaData: (data: Record<string, any> | null) => void;
  refreshBuildingsEtaForSelectedPlanet: (forceRefresh?: boolean) => Promise<void>;
  combatData: Record<string, any> | null;
  setCombatData: (data: Record<string, any> | null) => void;
  refreshCombatForSelectedPlanet: (opts?: { refresh?: boolean }) => Promise<boolean>;
  sectorsData: Record<string, any> | null;
  setSectorsData: (data: Record<string, any> | null) => void;
  refreshSectorsView: (
    galaxyOverride?: number,
    includeUnscannedOverride?: boolean,
    previewOverride?: SectorQueuePreviewOverrides | null,
    opts?: { background?: boolean }
  ) => Promise<void>;
  espData: Record<string, any> | null;
  setEspData: (data: Record<string, any> | null) => void;
  refreshEspionageView: () => Promise<void>;
  colonyData: Record<string, any> | null;
  setColonyData: (data: Record<string, any> | null) => void;
  refreshColoniesView: (opts?: { background?: boolean }) => Promise<void>;
  atkData: Record<string, any> | null;
  setAtkData: (data: Record<string, any> | null) => void;
  refreshAttacksView: () => Promise<void>;
  recData: Record<string, any> | null;
  setRecData: (data: Record<string, any> | null) => void;
  refreshRecyclersView: (categoryOverride?: string) => Promise<void>;
  actionGraphData: Record<string, any> | null;
  setActionGraphData: (data: Record<string, any> | null) => void;
  plannerJobsStatus: PlannerJobsStatusPayload | null;
  setPlannerJobsStatus: (status: PlannerJobsStatusPayload | null) => void;
  refreshPlannerJobsStatus: () => Promise<PlannerJobsStatusPayload | null>;
  agSimDirty: boolean;
  setAgSimDirty: (dirty: boolean) => void;
  agSimRunning: number;
  setAgSimRunning: React.Dispatch<React.SetStateAction<number>>;
  configDirty: boolean;
  setConfigDirty: (dirty: boolean) => void;
  configSaving: number;
  setConfigSaving: React.Dispatch<React.SetStateAction<number>>;
  configHydrationTick: number;
  agHydrationTick: number;
  lastOptimizerSummary: Record<string, any> | null;
  setLastOptimizerSummary: (summary: Record<string, any> | null) => void;
  notifications: NotificationsState;
  syncNotificationsPayload: (payload: Record<string, any> | null | undefined) => void;
  notifCenterOpen: boolean;
  openNotificationCenter: () => Promise<void>;
  closeNotificationCenter: () => void;
  refreshNotificationCenter: (filterOverride?: string) => Promise<void>;
  activeHelpNotif: Record<string, any> | null;
  ackActiveHelpPopup: (targetStatus: "read" | "dismissed") => Promise<void>;
  closeHelpPopupOnly: () => void;
  toggleBlastFurnaceRate: (targetPct: number) => Promise<void>;
  syncAllInboxFolders: () => Promise<void>;
  enqueueManualCommand: (payload: Record<string, any>) => Promise<Record<string, any> | null>;
}

const ConsoleContext = createContext<ConsoleContextValue | null>(null);

export interface ConsoleProviderProps {
  children: React.ReactNode;
  disableInitialFetch?: boolean;
  enablePersistence?: boolean;
}

export const ConsoleProvider: React.FC<ConsoleProviderProps> = ({
  children,
  disableInitialFetch = false,
  enablePersistence,
}) => {
  const shouldPersist =
    enablePersistence !== undefined
      ? enablePersistence
      : !disableInitialFetch || getConsoleStorePersistenceForTests();

  // Select persistent browser singleton (synchronously hydrated before first render!)
  // or a clean isolated ephemeral store when disableInitialFetch={true} (SSR & unit tests).
  const activeStore: ConsoleStore = useMemo(() => {
    if (shouldPersist) {
      const hydrated = consoleStore.hydrateIfNeeded();
      if (typeof window !== "undefined") {
        syncLegacyStateFromSnapshot(hydrated);
      }
      return consoleStore;
    }
    const isolated = createConsoleStore({
      persist: false,
      hydrateOnInit: false,
      bindUnloadEvents: false,
    });
    if (typeof window !== "undefined") {
      resetTransientLegacyFlags();
      syncLegacyStateFromSnapshot(isolated.getSnapshot());
    }
    return isolated;
  }, [shouldPersist]);

  const snap = useSyncExternalStore(
    activeStore.subscribe,
    activeStore.getSnapshot,
    activeStore.getServerSnapshot
  );

  const {
    dashboard,
    apiOnline,
    activePage,
    selectedPlanetId,
    uiPrefs,
    buildingsEtaData,
    combatData,
    sectorsData,
    espData,
    colonyData,
    atkData,
    recData,
    actionGraphData,
    plannerJobsStatus,
    lastOptimizerSummary,
    notifications,
    lastHydratedAt,
    isHydratedFromCache,
  } = snap;

  // Ephemeral UI-only state (timers, transient modals, in-flight dirty flags)
  const [reconnectAttempts, setReconnectAttempts] = useState<number>(0);
  const [nowEpochSec, setNowEpochSec] = useState<number>(() =>
    typeof window === "undefined" ? 0 : Math.floor(Date.now() / 1000)
  );
  const [gaCalcCount, setGaCalcCount] = useState<number>(0);
  const [feedback, setFeedback] = useState<FeedbackState | null>(null);

  const [agSimDirty, setAgSimDirtyState] = useState<boolean>(false);
  const [agSimRunning, setAgSimRunning] = useState<number>(0);
  const [configDirty, setConfigDirtyState] = useState<boolean>(false);
  const [configSaving, setConfigSaving] = useState<number>(0);
  const [configHydrationTick, setConfigHydrationTick] = useState<number>(0);
  const [agHydrationTick, setAgHydrationTick] = useState<number>(0);

  const [notifCenterOpen, setNotifCenterOpen] = useState<boolean>(false);
  const [activeHelpNotif, setActiveHelpNotif] = useState<Record<string, any> | null>(null);

  const feedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const uiVersionsRef = useRef<{
    code_version: string | null;
    css_version: string | null;
    server_boot_id: string | null;
  }>({
    code_version: null,
    css_version: null,
    server_boot_id: null,
  });
  const seenHelpSigsRef = useRef<Set<string>>(new Set());
  const notifCenterOpenRef = useRef<boolean>(false);
  notifCenterOpenRef.current = notifCenterOpen;
  const uiPrefsRef = useRef<UiPrefs>(uiPrefs);
  uiPrefsRef.current = uiPrefs;
  const sectorsReqSeqRef = useRef<number>(0);
  const inFlightSectorsCountRef = useRef<number>(0);
  const coloniesReqSeqRef = useRef<number>(0);
  const inFlightColoniesCountRef = useRef<number>(0);
  const inFlightBuildingsEtaRef = useRef<{ key: string; promise: Promise<void> } | null>(null);
  const inFlightCombatRef = useRef<{ key: string; promise: Promise<boolean> } | null>(null);
  const sectorPreviewRef = useRef<SectorQueuePreviewOverrides | null>(null);

  // Keep sharedLegacyState (window.state & window.__SI_STATE__) synchronized at all times
  if (typeof window !== "undefined") {
    syncLegacyStateFromSnapshot(snap);
    sharedLegacyState.reconnectAttempts = reconnectAttempts;
    sharedLegacyState.agSimDirty = agSimDirty;
    sharedLegacyState.agSimRunning = agSimRunning;
    sharedLegacyState.gaCalcCount = gaCalcCount;
    sharedLegacyState.activeHelpNotifId = activeHelpNotif
      ? safeFiniteNumber(activeHelpNotif.id, null)
      : null;
  }

  // Flush any pending debounced store write when a persistent provider unmounts
  useEffect(() => {
    return () => {
      if (feedbackTimerRef.current) {
        clearTimeout(feedbackTimerRef.current);
        feedbackTimerRef.current = null;
      }
      if (shouldPersist) {
        activeStore.flushPersistence(false);
      }
    };
  }, [activeStore, shouldPersist]);

  const setDashboard = useCallback(
    (next: React.SetStateAction<DashboardPayload | null>) => {
      activeStore.setDashboard(next);
      sharedLegacyState.dashboard = activeStore.getSnapshot().dashboard;
    },
    [activeStore]
  );

  const showFeedback = useCallback(
    (message: string, kind: FeedbackKind = "ok", opts: { autoHideMs?: number } = {}) => {
      if (feedbackTimerRef.current) {
        clearTimeout(feedbackTimerRef.current);
        feedbackTimerRef.current = null;
      }
      if (typeof document !== "undefined") {
        const banner = document.getElementById("feedback-banner");
        if (banner) {
          const cls = kind === "error" ? "is-error" : kind === "warn" ? "is-warn" : "is-ok";
          banner.className = `feedback-banner ${cls}`;
          banner.hidden = false;
          banner.textContent = message;
        }
      }
      setFeedback({ message, kind, hidden: false });
      const autoHideMs = opts.autoHideMs ?? 0;
      if (autoHideMs > 0) {
        feedbackTimerRef.current = setTimeout(() => {
          if (sharedLegacyState.apiOnline) {
            if (typeof document !== "undefined") {
              const banner = document.getElementById("feedback-banner");
              if (banner) banner.hidden = true;
            }
            setFeedback((prev) => (prev ? { ...prev, hidden: true } : null));
          }
        }, autoHideMs);
      }
    },
    []
  );

  const setApiStatus = useCallback(
    (online: boolean, note = "") => {
      const wasOnline = sharedLegacyState.apiOnline;
      sharedLegacyState.apiOnline = online;
      activeStore.setApiOnline(online);
      const port = sharedLegacyState.dashboard?.health?.debug_port
        ? "8787"
        : (typeof window !== "undefined" && window.location.port) || "8787";

      if (online) {
        const recovered = !wasOnline && sharedLegacyState.reconnectAttempts > 0;
        sharedLegacyState.reconnectAttempts = 0;
        setReconnectAttempts(0);
        if (recovered) {
          showFeedback(`Reconnected to Game API :${port}.`, "ok", { autoHideMs: 3200 });
        }
      } else {
        sharedLegacyState.reconnectAttempts += 1;
        setReconnectAttempts(sharedLegacyState.reconnectAttempts);
        showFeedback(
          `API offline (${note || "connection lost"}) — auto-reconnecting every 1.2s...`,
          "warn"
        );
      }
    },
    [activeStore, showFeedback]
  );

  const syncNotificationsPayload = useCallback(
    (notifPayload: Record<string, any> | null | undefined) => {
      if (!notifPayload || typeof notifPayload !== "object") return;
      const items = Array.isArray(notifPayload.items) ? notifPayload.items : [];
      const unreadCnt = Math.max(0, Math.round(safeFiniteNumber(notifPayload.unread_count, 0) ?? 0));
      const next: NotificationsState = { unread_count: unreadCnt, items };
      sharedLegacyState.notifications = next;
      activeStore.setNotifications(next);

      const newestUnread = items.find(
        (n: any) => n && n.status === "unread" && (safeFiniteNumber(n.id, 0) ?? 0) > 0
      );
      if (newestUnread) {
        const sig = `${newestUnread.id}:${safeFiniteNumber(newestUnread.occurrence_count, 1) ?? 1}`;
        if (!seenHelpSigsRef.current.has(sig) && !notifCenterOpenRef.current) {
          seenHelpSigsRef.current.add(sig);
          sharedLegacyState.activeHelpNotifId = safeFiniteNumber(newestUnread.id, 0);
          setActiveHelpNotif(newestUnread);
        }
      } else if (sharedLegacyState.activeHelpNotifId !== null) {
        sharedLegacyState.activeHelpNotifId = null;
        setActiveHelpNotif(null);
      }
    },
    [activeStore]
  );

  useClientSyncEffect(() => {
    registerHttpBridgeCallbacks({
      onStatusChange: setApiStatus,
      onNotifications: syncNotificationsPayload,
    });
  }, [setApiStatus, syncNotificationsPayload]);

  const beginGaCalc = useCallback(() => {
    sharedLegacyState.gaCalcCount += 1;
    if (typeof document !== "undefined") {
      const hud = document.getElementById("gann-calc-hud");
      if (hud) hud.classList.add("is-calculating");
    }
    setGaCalcCount(sharedLegacyState.gaCalcCount);
  }, []);

  const endGaCalc = useCallback(() => {
    sharedLegacyState.gaCalcCount = Math.max(0, sharedLegacyState.gaCalcCount - 1);
    if (typeof document !== "undefined" && sharedLegacyState.gaCalcCount === 0) {
      const hud = document.getElementById("gann-calc-hud");
      if (hud) hud.classList.remove("is-calculating");
    }
    setGaCalcCount(sharedLegacyState.gaCalcCount);
  }, []);

  const setConfigDirty = useCallback((dirty: boolean) => {
    const flag = Boolean(dirty);
    sharedLegacyState.configDirty = flag;
    setConfigDirtyState(flag);
  }, []);

  const setAgSimDirty = useCallback((dirty: boolean) => {
    const flag = Boolean(dirty);
    sharedLegacyState.agSimDirty = flag;
    setAgSimDirtyState(flag);
  }, []);

  const setBuildingsEtaData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.buildingsEtaData = data;
      activeStore.setBuildingsEtaData(data);
    },
    [activeStore]
  );

  const setCombatData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.combatData = data;
      activeStore.setCombatData(data);
    },
    [activeStore]
  );

  const setSectorsData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.sectorsData = data;
      activeStore.setSectorsData(data);
    },
    [activeStore]
  );

  const setEspData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.espData = data;
      activeStore.setEspData(data);
    },
    [activeStore]
  );

  const setColonyData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.colonyData = data;
      activeStore.setColonyData(data);
    },
    [activeStore]
  );

  const setAtkData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.atkData = data;
      activeStore.setAtkData(data);
    },
    [activeStore]
  );

  const setRecData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.recData = data;
      activeStore.setRecData(data);
    },
    [activeStore]
  );

  const setActionGraphData = useCallback(
    (data: Record<string, any> | null) => {
      sharedLegacyState.actionGraphData = data;
      activeStore.setActionGraphData(data);
    },
    [activeStore]
  );

  const setPlannerJobsStatus = useCallback(
    (status: PlannerJobsStatusPayload | null) => {
      sharedLegacyState.plannerJobsStatus = status;
      activeStore.setPlannerJobsStatus(status);
    },
    [activeStore]
  );

  const refreshPlannerJobsStatus =
    useCallback(async (): Promise<PlannerJobsStatusPayload | null> => {
      try {
        const res = await rawApiCommand<PlannerJobsStatusPayload>({
          command: "planner_jobs_status",
        });
        if (res && typeof res === "object") {
          setPlannerJobsStatus(res);
          return res;
        }
      } catch {
        // Tolerate older servers or offline fallback
      }
      return null;
    }, [setPlannerJobsStatus]);

  const setLastOptimizerSummary = useCallback(
    (summary: Record<string, any> | null) => {
      sharedLegacyState.lastOptimizerSummary = summary;
      activeStore.setLastOptimizerSummary(summary);
    },
    [activeStore]
  );

  const bumpDashboardSeq = useCallback(() => {
    sharedLegacyState.dashboardSeq += 1;
    return sharedLegacyState.dashboardSeq;
  }, []);

  const applyVersionSnapshot = useCallback(
    (vData: Record<string, any> | null | undefined): boolean => {
      if (!vData) return false;
      const prev = uiVersionsRef.current;
      if (prev.code_version == null) {
        uiVersionsRef.current = {
          code_version: vData.code_version || null,
          css_version: vData.css_version || null,
          server_boot_id: vData.server_boot_id || null,
        };
        return false;
      }
      const codeChanged = Boolean(vData.code_version && vData.code_version !== prev.code_version);
      const bootChanged = Boolean(
        vData.server_boot_id &&
          prev.server_boot_id &&
          vData.server_boot_id !== prev.server_boot_id
      );
      if (sharedLegacyState.dashboard?.bot_control?.captcha_blocked) {
        return false;
      }
      if (codeChanged || bootChanged) {
        activeStore.flushPersistence(true);
        showFeedback(
          codeChanged
            ? "UI update detected — hot-reloading console..."
            : "Game API restarted — hot-reloading console...",
          "ok"
        );
        if (typeof window !== "undefined") {
          setTimeout(() => window.location.reload(), 120);
        }
        return true;
      }
      if (vData.css_version && vData.css_version !== prev.css_version) {
        uiVersionsRef.current.css_version = vData.css_version;
        if (typeof document !== "undefined") {
          const link = document.getElementById("ui-stylesheet") as HTMLLinkElement | null;
          if (link) {
            link.href = `/styles.css?v=${encodeURIComponent(vData.css_version)}`;
          }
        }
        showFeedback("Hot-reloaded styles.css live.", "ok", { autoHideMs: 2600 });
      }
      return false;
    },
    [activeStore, showFeedback]
  );

  const refreshBuildingsEtaForSelectedPlanet = useCallback(
    async (forceRefresh = false) => {
      const planet = selectActivePlanet(
        sharedLegacyState.dashboard,
        sharedLegacyState.selectedPlanetId
      );
      if (!planet) return;
      const reqKey = `${planet.planet_id}:${forceRefresh ? "1" : "0"}`;
      if (inFlightBuildingsEtaRef.current?.key === reqKey) {
        return inFlightBuildingsEtaRef.current.promise;
      }
      const reqPromise = (async () => {
        try {
          const res = await rawApiCommand({
            command: "buildings_eta",
            planet_id: planet.planet_id,
            refresh: forceRefresh,
          });
          if (res.buildings_eta) {
            setBuildingsEtaData(res.buildings_eta);
          }
        } catch {
          // Non-fatal during offline fallback — keep cached buildingsEtaData intact
        } finally {
          if (inFlightBuildingsEtaRef.current?.promise === reqPromise) {
            inFlightBuildingsEtaRef.current = null;
          }
        }
      })();
      inFlightBuildingsEtaRef.current = { key: reqKey, promise: reqPromise };
      return reqPromise;
    },
    [setBuildingsEtaData]
  );

  const refreshCombatForSelectedPlanet = useCallback(
    async ({ refresh = false }: { refresh?: boolean } = {}): Promise<boolean> => {
      const planet = selectActivePlanet(
        sharedLegacyState.dashboard,
        sharedLegacyState.selectedPlanetId
      );
      if (!planet) return false;
      const reqKey = `${planet.planet_id}:${refresh ? "1" : "0"}`;
      if (inFlightCombatRef.current?.key === reqKey) {
        return inFlightCombatRef.current.promise;
      }
      const reqPromise = (async (): Promise<boolean> => {
        try {
          const res = await rawApiCommand({
            command: "combat_overview",
            planet_id: planet.planet_id,
            refresh,
          });
          if (res.combat) {
            setCombatData(res.combat);
          }
          return true;
        } catch (err: any) {
          if (refresh || isRateLimitError(err)) {
            showFeedback(`Shipyard refresh failed: ${err.message}`, feedbackKindForError(err));
          }
          return false;
        } finally {
          if (inFlightCombatRef.current?.promise === reqPromise) {
            inFlightCombatRef.current = null;
          }
        }
      })();
      inFlightCombatRef.current = { key: reqKey, promise: reqPromise };
      return reqPromise;
    },
    [setCombatData, showFeedback]
  );

  const refreshSectorsView = useCallback(
    async (
      galaxyOverride?: number,
      includeUnscannedOverride?: boolean,
      previewOverride?: SectorQueuePreviewOverrides | null,
      opts?: { background?: boolean }
    ) => {
      if (opts?.background && inFlightSectorsCountRef.current > 0) {
        return;
      }
      if (previewOverride !== undefined) {
        sectorPreviewRef.current = previewOverride;
      }
      const reqSeq = ++sectorsReqSeqRef.current;
      inFlightSectorsCountRef.current += 1;
      try {
        const gal =
          galaxyOverride !== undefined
            ? galaxyOverride
            : Math.max(0, Math.round(safeFiniteNumber(sharedLegacyState.sectorGalaxy, 0) ?? 0));
        const curFilter = String(sharedLegacyState.sectorFilter || "all");
        const incUnscanned =
          includeUnscannedOverride !== undefined
            ? includeUnscannedOverride
            : gal > 0 || curFilter === "unscanned";
        const planet = selectActivePlanet(
          sharedLegacyState.dashboard,
          sharedLegacyState.selectedPlanetId
        );
        const payload: Record<string, any> = {
          command: "scanned_sectors",
          limit: 5600,
          include_unscanned: incUnscanned,
        };
        if (gal > 0) payload.galaxy = gal;
        if (planet?.planet_id) payload.planet_id = planet.planet_id;
        const activePreview = sectorPreviewRef.current;
        if (activePreview) {
          if (typeof activePreview.refresh_pct === "number") {
            payload.preview_refresh_pct = activePreview.refresh_pct;
          }
          if (typeof activePreview.sweep_pages === "number") {
            payload.preview_sweep_pages = activePreview.sweep_pages;
          }
          if (typeof activePreview.all_galaxies === "boolean") {
            payload.preview_all_galaxies = activePreview.all_galaxies;
          }
        }
        const data = await rawApiCommand(payload);
        if (reqSeq !== sectorsReqSeqRef.current) return;
        if (
          data &&
          (Array.isArray(data.sectors) ||
            Array.isArray(data.scan_queue) ||
            (data.summary && typeof data.summary === "object"))
        ) {
          setSectorsData(data);
        }
      } catch (err: any) {
        if (reqSeq !== sectorsReqSeqRef.current) return;
        showFeedback(`Scanned sectors load failed: ${err.message}`, feedbackKindForError(err));
      } finally {
        inFlightSectorsCountRef.current = Math.max(0, inFlightSectorsCountRef.current - 1);
      }
    },
    [setSectorsData, showFeedback]
  );

  const refreshEspionageView = useCallback(async () => {
    try {
      const data = await rawApiCommand({ command: "espionage_intel", limit: 200 });
      if (data && typeof data === "object") {
        setEspData(data);
      }
    } catch (err: any) {
      showFeedback(`Espionage data load failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [setEspData, showFeedback]);

  const refreshColoniesView = useCallback(
    async (opts?: { background?: boolean }) => {
      if (opts?.background && inFlightColoniesCountRef.current > 0) {
        return;
      }
      const reqSeq = ++coloniesReqSeqRef.current;
      inFlightColoniesCountRef.current += 1;
      try {
        const data = await rawApiCommand({ command: "colony_candidates", limit: 1000 });
        if (reqSeq !== coloniesReqSeqRef.current) return;
        if (data && typeof data === "object") {
          setColonyData(data);
        }
      } catch (err: any) {
        if (reqSeq !== coloniesReqSeqRef.current) return;
        showFeedback(`Colony grading load failed: ${err.message}`, feedbackKindForError(err));
      } finally {
        inFlightColoniesCountRef.current = Math.max(0, inFlightColoniesCountRef.current - 1);
      }
    },
    [setColonyData, showFeedback]
  );

  const refreshAttacksView = useCallback(async () => {
    try {
      const data = await rawApiCommand({ command: "attack_results", limit: 200 });
      if (data && typeof data === "object") {
        setAtkData(data);
      }
    } catch (err: any) {
      showFeedback(`Attack results load failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [setAtkData, showFeedback]);

  const refreshRecyclersView = useCallback(
    async (categoryOverride?: string) => {
      try {
        const cat = categoryOverride ?? sharedLegacyState.emailCatFilter ?? "all";
        const data = await rawApiCommand({
          command: "recycler_results",
          category: cat,
          limit: 200,
        });
        if (data && typeof data === "object") {
          setRecData(data);
        }
      } catch (err: any) {
        showFeedback(`Recycler and email load failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [setRecData, showFeedback]
  );

  const loadDashboard = useCallback(
    async ({
      refresh = false,
      preserveConfig = true,
      force = false,
    }: {
      refresh?: boolean;
      preserveConfig?: boolean;
      force?: boolean;
    } = {}): Promise<boolean> => {
      if (!refresh && !force && sharedLegacyState.inFlightDashboard) return false;
      const reqSeq = ++sharedLegacyState.dashboardSeq;
      sharedLegacyState.inFlightDashboardCount += 1;
      sharedLegacyState.inFlightDashboard = true;
      try {
        const dash = await rawApiGet<DashboardPayload>(
          `/dashboard?refresh=${refresh ? 1 : 0}`,
          15000
        );
        if (reqSeq !== sharedLegacyState.dashboardSeq) return false;
        if (applyVersionSnapshot(dash)) return false;
        setApiStatus(true);

        const preserveUnsavedCfg =
          (sharedLegacyState.configDirty || sharedLegacyState.configSaving > 0) && preserveConfig;
        const nextDash: DashboardPayload =
          preserveUnsavedCfg && sharedLegacyState.dashboard?.config
            ? {
                ...dash,
                config: sharedLegacyState.dashboard.config,
                planner_mode: sharedLegacyState.dashboard.planner_mode || dash.planner_mode,
              }
            : dash;

        const incomingJobsStatus =
          nextDash.planner_jobs_status && typeof nextDash.planner_jobs_status === "object"
            ? nextDash.planner_jobs_status
            : nextDash.planner_status && typeof nextDash.planner_status === "object"
              ? nextDash.planner_status
              : null;

        const modeVal = nextDash.bot_control?.mode_override || "auto";
        const shouldPatchActionGraph =
          !sharedLegacyState.agSimDirty &&
          sharedLegacyState.agSimRunning === 0 &&
          sharedLegacyState.configSaving === 0;
        const nextActionGraph = shouldPatchActionGraph
          ? nextDash.action_graph || buildClientFallbackActionGraph(nextDash)
          : null;

        // Patch store smoothly in-place (stale-while-revalidate: never null out cached tabs)
        const updatedSnap = activeStore.setState(
          (prev) => {
            const nextPrefs =
              prev.uiPrefs.modeOverride === modeVal
                ? prev.uiPrefs
                : { ...prev.uiPrefs, modeOverride: modeVal };

            const nextBuildingsEta =
              nextDash.buildings_eta &&
              (!sharedLegacyState.selectedPlanetId ||
                sharedLegacyState.selectedPlanetId === nextDash.buildings_eta.planet?.planet_id)
                ? nextDash.buildings_eta
                : prev.buildingsEtaData;

            const nextCombat =
              nextDash.combat &&
              (!sharedLegacyState.selectedPlanetId ||
                sharedLegacyState.selectedPlanetId === nextDash.combat.planet_id)
                ? nextDash.combat
                : prev.combatData;

            return {
              ...prev,
              dashboard: nextDash,
              uiPrefs: nextPrefs,
              lastOptimizerSummary: nextDash.optimizer_summary || prev.lastOptimizerSummary,
              plannerJobsStatus: incomingJobsStatus || prev.plannerJobsStatus,
              buildingsEtaData: nextBuildingsEta,
              combatData: nextCombat,
              actionGraphData: shouldPatchActionGraph ? nextActionGraph : prev.actionGraphData,
              lastHydratedAt: Date.now(),
            };
          },
          { persist: true }
        );
        syncLegacyStateFromSnapshot(updatedSnap);

        if (nextDash.notifications && typeof nextDash.notifications === "object") {
          syncNotificationsPayload(nextDash.notifications);
        }

        if (refresh && (nextDash as any).sweep_deferred_refresh) {
          showFeedback(
            String(
              (nextDash as any).sweep_message ||
                "Galaxy scan in progress — live browser refresh deferred until scan finishes."
            ),
            "warn",
            { autoHideMs: 4200 }
          );
        }

        const curPage = sharedLegacyState.activePage;
        if (curPage === "sectors") {
          refreshSectorsView(undefined, undefined, undefined, { background: true });
        } else if (curPage === "espionage") {
          refreshEspionageView();
        } else if (curPage === "colonies") {
          refreshColoniesView({ background: true });
        } else if (curPage === "attacks") {
          refreshAttacksView();
        } else if (curPage === "recyclers") {
          refreshRecyclersView();
        } else if (curPage === "buildings" || (curPage as string) === "research") {
          if (
            !nextDash.buildings_eta ||
            (sharedLegacyState.selectedPlanetId &&
              sharedLegacyState.selectedPlanetId !== nextDash.buildings_eta.planet?.planet_id)
          ) {
            refreshBuildingsEtaForSelectedPlanet(false);
          }
        } else if (curPage === "combat") {
          if (
            !nextDash.combat ||
            (sharedLegacyState.selectedPlanetId &&
              sharedLegacyState.selectedPlanetId !== nextDash.combat.planet_id)
          ) {
            refreshCombatForSelectedPlanet({ refresh: false });
          }
        }

        if (!sharedLegacyState.configDirty && sharedLegacyState.configSaving === 0) {
          sharedLegacyState.configDirty = false;
          setConfigDirtyState(false);
          setConfigHydrationTick((t) => t + 1);
        }

        if (shouldPatchActionGraph) {
          setAgHydrationTick((t) => t + 1);
        }

        return true;
      } catch (err: any) {
        if (reqSeq !== sharedLegacyState.dashboardSeq) return false;
        if (isRateLimitError(err)) {
          setApiStatus(true);
          showFeedback(`SpaceInvasion rate-limit cooldown active: ${err.message}`, "warn");
        } else if (isSweepBlockedError(err)) {
          setApiStatus(true);
          showFeedback(err.message, "warn", { autoHideMs: 4200 });
        } else {
          const msg = err?.name === "AbortError" ? "timeout" : err?.message || "unreachable";
          setApiStatus(false, msg);
        }
        return false;
      } finally {
        sharedLegacyState.inFlightDashboardCount = Math.max(
          0,
          sharedLegacyState.inFlightDashboardCount - 1
        );
        sharedLegacyState.inFlightDashboard = sharedLegacyState.inFlightDashboardCount > 0;
      }
    },
    [
      activeStore,
      applyVersionSnapshot,
      refreshAttacksView,
      refreshBuildingsEtaForSelectedPlanet,
      refreshColoniesView,
      refreshCombatForSelectedPlanet,
      refreshEspionageView,
      refreshRecyclersView,
      refreshSectorsView,
      setApiStatus,
      showFeedback,
      syncNotificationsPayload,
    ]
  );

  const checkApiHealthAndHotReload = useCallback(async () => {
    if (sharedLegacyState.inFlightVersionCheck) return;
    sharedLegacyState.inFlightVersionCheck = true;
    try {
      const vData = await rawApiGet("/ui-version", 2500);
      if (applyVersionSnapshot(vData)) return;
      const wasOffline = !sharedLegacyState.apiOnline;
      setApiStatus(true);
      if (wasOffline) {
        await loadDashboard({ refresh: false, preserveConfig: true });
      }
    } catch (err: any) {
      const msg = err?.name === "AbortError" ? "timeout" : err?.message || "unreachable";
      setApiStatus(false, msg);
    } finally {
      sharedLegacyState.inFlightVersionCheck = false;
    }
  }, [applyVersionSnapshot, loadDashboard, setApiStatus]);

  const setActivePage = useCallback(
    (rawKey: PageKey | string) => {
      const target = normalizePageKey(rawKey);
      const nextSnap = activeStore.setState(
        (prev) => {
          const nextPrefs = prev.uiPrefs.sidebarOpen
            ? { ...prev.uiPrefs, sidebarOpen: false }
            : prev.uiPrefs;
          if (prev.activePage === target && nextPrefs === prev.uiPrefs) {
            return prev;
          }
          return {
            ...prev,
            activePage: target,
            uiPrefs: nextPrefs,
          };
        },
        { persist: true }
      );
      syncLegacyStateFromSnapshot(nextSnap);
      if (
        target === "dashboard" ||
        target === "operations" ||
        (target as string) === "fleet-control"
      ) {
        setNowEpochSec(Math.floor(Date.now() / 1000));
      }
      if (shouldPersist && typeof window !== "undefined" && window.location.hash !== `#${target}`) {
        window.history.replaceState(null, "", `#${target}`);
      }

      if (target === "buildings" || (target as string) === "research") {
        refreshBuildingsEtaForSelectedPlanet();
      } else if (target === "combat") {
        refreshCombatForSelectedPlanet({ refresh: false });
      } else if ((target as string) === "fleet-control") {
        if (!disableInitialFetch) {
          loadDashboard({ refresh: false, preserveConfig: true });
        }
      } else if (target === "sectors") {
        refreshSectorsView();
      } else if (target === "espionage") {
        refreshEspionageView();
      } else if (target === "colonies") {
        refreshColoniesView();
      } else if (target === "attacks") {
        refreshAttacksView();
      } else if (target === "recyclers") {
        refreshRecyclersView();
      } else if (target === "action-graph") {
        if (!sharedLegacyState.actionGraphData) {
          if (sharedLegacyState.dashboard?.action_graph) {
            setActionGraphData(sharedLegacyState.dashboard.action_graph);
          } else if (sharedLegacyState.dashboard) {
            setActionGraphData(buildClientFallbackActionGraph(sharedLegacyState.dashboard));
          }
        }
      }
    },
    [
      activeStore,
      disableInitialFetch,
      loadDashboard,
      refreshAttacksView,
      refreshBuildingsEtaForSelectedPlanet,
      refreshColoniesView,
      refreshCombatForSelectedPlanet,
      refreshEspionageView,
      refreshRecyclersView,
      refreshSectorsView,
      setActionGraphData,
      shouldPersist,
    ]
  );

  const setSelectedPlanetId = useCallback(
    (planetId: number | null) => {
      const pid = planetId == null ? null : safeFiniteNumber(planetId, null);
      sharedLegacyState.selectedPlanetId = pid;
      sharedLegacyState.simPopulatedOnce = false;
      activeStore.setSelectedPlanetId(pid);
      if (sharedLegacyState.dashboard) {
        refreshBuildingsEtaForSelectedPlanet();
        if (sharedLegacyState.activePage === "combat") {
          refreshCombatForSelectedPlanet({ refresh: false });
        } else if (sharedLegacyState.activePage === "sectors") {
          refreshSectorsView();
        } else if (sharedLegacyState.activePage === "colonies") {
          refreshColoniesView();
        }
      }
    },
    [
      activeStore,
      refreshBuildingsEtaForSelectedPlanet,
      refreshColoniesView,
      refreshCombatForSelectedPlanet,
      refreshSectorsView,
    ]
  );

  const setUiPref = useCallback(
    <K extends keyof UiPrefs>(key: K, value: UiPrefs[K]) => {
      if (key in sharedLegacyState) {
        (sharedLegacyState as any)[key] = value;
      }
      activeStore.setUiPref(key, value);
      sharedLegacyState.uiPrefs = activeStore.getSnapshot().uiPrefs;
    },
    [activeStore]
  );

  const refreshNotificationCenter = useCallback(
    async (filterOverride?: string) => {
      const statusFilter = filterOverride ?? sharedLegacyState.notifFilter ?? "active";
      try {
        const res = await rawApiCommand({
          command: "notifications_list",
          status: statusFilter,
          limit: 60,
        });
        if (Array.isArray(res?.items)) {
          const next: NotificationsState = {
            unread_count: Math.max(0, Math.round(safeFiniteNumber(res.unread_count, 0) ?? 0)),
            items: res.items,
          };
          sharedLegacyState.notifications = next;
          activeStore.setNotifications(next);
        }
      } catch {
        // Offline fallback keeps cached items
      }
    },
    [activeStore]
  );

  const openNotificationCenter = useCallback(async () => {
    setNotifCenterOpen(true);
    notifCenterOpenRef.current = true;
    setNowEpochSec(Math.floor(Date.now() / 1000));
    await refreshNotificationCenter();
  }, [refreshNotificationCenter]);

  const closeNotificationCenter = useCallback(() => {
    setNotifCenterOpen(false);
    notifCenterOpenRef.current = false;
  }, []);

  const closeHelpPopupOnly = useCallback(() => {
    sharedLegacyState.activeHelpNotifId = null;
    setActiveHelpNotif(null);
  }, []);

  const ackActiveHelpPopup = useCallback(
    async (targetStatus: "read" | "dismissed") => {
      const nid = sharedLegacyState.activeHelpNotifId;
      closeHelpPopupOnly();
      if (!nid || nid <= 0) return;
      try {
        await rawApiCommand({
          command: "notification_ack",
          notification_id: nid,
          status: targetStatus,
        });
      } catch (err: any) {
        showFeedback(`Failed updating notification: ${err.message}`, feedbackKindForError(err));
      }
    },
    [closeHelpPopupOnly, showFeedback]
  );

  const toggleBlastFurnaceRate = useCallback(
    async (targetPct: number) => {
      const planet = selectActivePlanet(
        sharedLegacyState.dashboard,
        sharedLegacyState.selectedPlanetId
      );
      if (!planet) return;
      try {
        showFeedback(`Setting Blast furnace to ${targetPct}% on [${planet.coords}]...`, "ok");
        const res = await rawApiCommand({
          command: "throttle_building",
          building_id: 2,
          percentage: Number(targetPct),
          planet_id: planet.planet_id,
          dry_run: false,
        });
        showFeedback(res.message || `Blast furnace set to ${targetPct}%.`, "ok");
        await loadDashboard({ refresh: false, force: true });
        await refreshBuildingsEtaForSelectedPlanet(false);
      } catch (err: any) {
        showFeedback(`Throttle failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [loadDashboard, refreshBuildingsEtaForSelectedPlanet, showFeedback]
  );

  const syncAllInboxFolders = useCallback(async () => {
    try {
      showFeedback(
        "Syncing all in-game message folders (Espionage, Battle, Fleet, User, Alliance, System)...",
        "ok"
      );
      const res = await rawApiCommand({ command: "sync_messages" });
      const fc = res.folder_counts || {};
      const folderSummary = Object.entries(fc)
        .map(([k, v]: [string, any]) => `${k}:${safeFiniteNumber(v?.total, 0) ?? 0}`)
        .join(" · ");
      const archived = Math.round(safeFiniteNumber(res.archived_messages, 0) ?? 0);
      const spyCnt = Math.round(safeFiniteNumber(res.synced_reports, 0) ?? 0);
      const delNote = res.deleted_in_game ? "deleted in-game" : "stored in SQLite";
      showFeedback(
        `Inbox sync complete: ${archived} archived (${spyCnt} spy) — ${delNote}${folderSummary ? ` [${folderSummary}]` : ""}.`,
        "ok"
      );
      await loadDashboard({ refresh: false, force: true });
      const curPage = sharedLegacyState.activePage;
      if (curPage === "espionage") await refreshEspionageView();
      else if (curPage === "colonies") await refreshColoniesView();
      else if (curPage === "attacks") await refreshAttacksView();
      else if (curPage === "recyclers") await refreshRecyclersView();
    } catch (err: any) {
      showFeedback(`Inbox sync failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [
    loadDashboard,
    refreshAttacksView,
    refreshColoniesView,
    refreshEspionageView,
    refreshRecyclersView,
    showFeedback,
  ]);

  const enqueueManualCommand = useCallback(
    async (payload: Record<string, any>): Promise<Record<string, any> | null> => {
      try {
        const res = await rawApiCommand({
          command: "queue_add",
          ...payload,
        });
        showFeedback(res.message || "Command pinned to priority queue.", "ok", {
          autoHideMs: 4200,
        });
        await loadDashboard({ refresh: false, force: true });
        return res;
      } catch (err: any) {
        showFeedback(`Queue add failed: ${err.message}`, feedbackKindForError(err));
        return null;
      }
    },
    [loadDashboard, showFeedback]
  );

  // Expose imperative compatibility helpers onto globalThis / window for legacy unit tests
  useClientSyncEffect(() => {
    const g = globalThis as any;
    g.showFeedback = (msg: string, kind: FeedbackKind = "ok", opts?: { autoHideMs?: number }) => {
      flushSync(() => showFeedback(msg, kind, opts));
    };
    g.setActivePage = (page: string) => {
      flushSync(() => setActivePage(page));
    };
    g.beginGaCalc = () => {
      flushSync(() => beginGaCalc());
    };
    g.endGaCalc = () => {
      flushSync(() => endGaCalc());
    };
    g.loadDashboard = loadDashboard;
    const applySyntheticDash = (partialDash: DashboardPayload) => {
      flushSync(() => {
        setDashboard((prev) => {
          const merged: DashboardPayload = { ...(prev || {}), ...(partialDash || {}) };
          sharedLegacyState.dashboard = merged;
          return merged;
        });
        if (partialDash?.buildings_eta) {
          setBuildingsEtaData(partialDash.buildings_eta);
        }
        if (partialDash?.optimizer_summary) {
          setLastOptimizerSummary(partialDash.optimizer_summary);
        }
      });
    };
    g.renderTelemetry = applySyntheticDash;
    g.renderBuildingsView = applySyntheticDash;
    g.renderResearchView = applySyntheticDash;
    g.renderGaNnHud = applySyntheticDash;
    g.renderRanking = applySyntheticDash;
  }, [
    beginGaCalc,
    endGaCalc,
    loadDashboard,
    setActivePage,
    setBuildingsEtaData,
    setDashboard,
    setLastOptimizerSummary,
    showFeedback,
  ]);

  // Boot fetch + live polling timers + Escape modal handler
  useClientSyncEffect(() => {
    if (disableInitialFetch || typeof window === "undefined") return;

    checkApiHealthAndHotReload();
    loadDashboard({ refresh: false, preserveConfig: false });

    const tickTimer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      const curPage = sharedLegacyState.activePage;
      if (
        curPage === "dashboard" ||
        curPage === "operations" ||
        (curPage as string) === "fleet-control" ||
        notifCenterOpenRef.current
      ) {
        setNowEpochSec(Math.floor(Date.now() / 1000));
      }
    }, 1000);

    const versionTimer = setInterval(() => {
      if (!sharedLegacyState.dashboard?.bot_control?.captcha_blocked) {
        checkApiHealthAndHotReload();
      }
    }, 1200);

    const dashTimer = setInterval(() => {
      if (
        document.visibilityState === "visible" &&
        sharedLegacyState.apiOnline &&
        !sharedLegacyState.dashboard?.bot_control?.captcha_blocked
      ) {
        loadDashboard({ refresh: false, preserveConfig: true });
      }
    }, 5000);

    const onOnline = () => {
      if (!sharedLegacyState.dashboard?.bot_control?.captcha_blocked) {
        checkApiHealthAndHotReload();
      }
    };
    const onVis = () => {
      if (
        document.visibilityState === "visible" &&
        !sharedLegacyState.dashboard?.bot_control?.captcha_blocked
      ) {
        checkApiHealthAndHotReload();
        loadDashboard({ refresh: false, preserveConfig: true });
      }
    };
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev && ev.key === "Escape") {
        if (notifCenterOpenRef.current) {
          closeNotificationCenter();
        } else if (sharedLegacyState.activeHelpNotifId !== null) {
          closeHelpPopupOnly();
        }
      }
    };

    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVis);
    document.addEventListener("keydown", onKeyDown);

    return () => {
      clearInterval(tickTimer);
      clearInterval(versionTimer);
      clearInterval(dashTimer);
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVis);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [
    checkApiHealthAndHotReload,
    closeHelpPopupOnly,
    closeNotificationCenter,
    disableInitialFetch,
    loadDashboard,
  ]);

  const activePlanet = useMemo(
    () => selectActivePlanet(dashboard, selectedPlanetId),
    [dashboard, selectedPlanetId]
  );

  const chromeOnline = Boolean(dashboard?.health?.in_game);
  const gaCalculating = gaCalcCount > 0;

  const value = useMemo<ConsoleContextValue>(
    () => ({
      dashboard,
      setDashboard,
      apiOnline,
      reconnectAttempts,
      chromeOnline,
      isHydratedFromCache,
      lastHydratedAt,
      activePage,
      setActivePage,
      selectedPlanetId,
      setSelectedPlanetId,
      activePlanet,
      nowEpochSec,
      gaCalculating,
      gaCalcCount,
      beginGaCalc,
      endGaCalc,
      feedback,
      showFeedback,
      apiGet: rawApiGet,
      apiCommand: rawApiCommand,
      loadDashboard,
      bumpDashboardSeq,
      uiPrefs,
      setUiPref,
      buildingsEtaData,
      setBuildingsEtaData,
      refreshBuildingsEtaForSelectedPlanet,
      combatData,
      setCombatData,
      refreshCombatForSelectedPlanet,
      sectorsData,
      setSectorsData,
      refreshSectorsView,
      espData,
      setEspData,
      refreshEspionageView,
      colonyData,
      setColonyData,
      refreshColoniesView,
      atkData,
      setAtkData,
      refreshAttacksView,
      recData,
      setRecData,
      refreshRecyclersView,
      actionGraphData,
      setActionGraphData,
      plannerJobsStatus,
      setPlannerJobsStatus,
      refreshPlannerJobsStatus,
      agSimDirty,
      setAgSimDirty,
      agSimRunning,
      setAgSimRunning,
      configDirty,
      setConfigDirty,
      configSaving,
      setConfigSaving,
      configHydrationTick,
      agHydrationTick,
      lastOptimizerSummary,
      setLastOptimizerSummary,
      notifications,
      syncNotificationsPayload,
      notifCenterOpen,
      openNotificationCenter,
      closeNotificationCenter,
      refreshNotificationCenter,
      activeHelpNotif,
      ackActiveHelpPopup,
      closeHelpPopupOnly,
      toggleBlastFurnaceRate,
      syncAllInboxFolders,
      enqueueManualCommand,
    }),
    [
      ackActiveHelpPopup,
      actionGraphData,
      activeHelpNotif,
      activePage,
      activePlanet,
      agHydrationTick,
      agSimDirty,
      agSimRunning,
      apiOnline,
      atkData,
      beginGaCalc,
      buildingsEtaData,
      bumpDashboardSeq,
      chromeOnline,
      closeHelpPopupOnly,
      closeNotificationCenter,
      colonyData,
      combatData,
      configDirty,
      configHydrationTick,
      configSaving,
      dashboard,
      endGaCalc,
      enqueueManualCommand,
      espData,
      feedback,
      gaCalcCount,
      gaCalculating,
      isHydratedFromCache,
      lastHydratedAt,
      lastOptimizerSummary,
      loadDashboard,
      notifCenterOpen,
      notifications,
      nowEpochSec,
      openNotificationCenter,
      plannerJobsStatus,
      recData,
      reconnectAttempts,
      refreshAttacksView,
      refreshBuildingsEtaForSelectedPlanet,
      refreshColoniesView,
      refreshCombatForSelectedPlanet,
      refreshEspionageView,
      refreshNotificationCenter,
      refreshPlannerJobsStatus,
      refreshRecyclersView,
      refreshSectorsView,
      sectorsData,
      selectedPlanetId,
      setActionGraphData,
      setActivePage,
      setAgSimDirty,
      setAtkData,
      setBuildingsEtaData,
      setColonyData,
      setCombatData,
      setConfigDirty,
      setDashboard,
      setEspData,
      setLastOptimizerSummary,
      setPlannerJobsStatus,
      setRecData,
      setSectorsData,
      setSelectedPlanetId,
      setUiPref,
      showFeedback,
      syncAllInboxFolders,
      syncNotificationsPayload,
      toggleBlastFurnaceRate,
      uiPrefs,
    ]
  );

  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
};

export function useConsole(): ConsoleContextValue {
  const ctx = useContext(ConsoleContext);
  if (!ctx) {
    throw new Error("useConsole must be used inside <ConsoleProvider>");
  }
  return ctx;
}
