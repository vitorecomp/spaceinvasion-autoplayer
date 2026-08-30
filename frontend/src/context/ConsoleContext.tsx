import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { flushSync } from "react-dom";

const useClientSyncEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;
import {
  apiCommand as rawApiCommand,
  apiGet as rawApiGet,
  feedbackKindForError,
  isRateLimitError,
  parseResponseJson,
  registerHttpBridgeCallbacks,
  safeFiniteNumber,
} from "../api/httpClient";
import {
  DashboardPayload,
  FeedbackKind,
  FeedbackState,
  NotificationsState,
  PAGE_TITLES,
  PageKey,
  PlanetData,
} from "../types/api";

const SESSION_STATE_KEY = "si_console_ui_state";

export interface UiPrefs {
  modeOverride: "auto" | "live" | "dry-run" | string;
  bldCategory: string;
  selectedUnitKey: string;
  combatSubTab: "ships" | "defense" | "targets" | "escape" | string;
  queueFilter: string;
  activeLogSource: "commander" | "game_api" | "sqlite" | string;
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

const DEFAULT_UI_PREFS: UiPrefs = {
  modeOverride: "auto",
  bldCategory: "all",
  selectedUnitKey: "b1",
  combatSubTab: "ships",
  queueFilter: "all",
  activeLogSource: "commander",
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
};

export interface LegacyWindowState {
  dashboard: DashboardPayload | null;
  selectedPlanetId: number | null;
  modeOverride: string;
  activePage: PageKey;
  bldCategory: string;
  selectedUnitKey: string;
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

if (typeof globalThis !== "undefined") {
  const g = globalThis as any;
  g.state = sharedLegacyState;
  g.el = (id: string) => (typeof document !== "undefined" ? document.getElementById(id) : null);
  g.apiGet = rawApiGet;
  g.apiCommand = rawApiCommand;
  g.parseResponseJson = parseResponseJson;
  g.feedbackKindForError = feedbackKindForError;
  if (typeof window !== "undefined") {
    (window as any).state = sharedLegacyState;
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

export function buildClientFallbackActionGraph(dash: DashboardPayload | null | undefined): Record<string, any> {
  const cfg = dash?.config || {};
  const mode =
    String(cfg.planner_mode || dash?.planner_mode || "priority").toLowerCase() === "ga_nn"
      ? "ga_nn"
      : "priority";
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
      critical_path_eta: nodes.length > 0 ? String(nodes[nodes.length - 1].total_eta || "Ready") : "Ready",
      primary_bottleneck: null,
      by_priority: { P0: 0, P1: 0, P2: nodes.length, P3: 0 },
      by_edge_type: {},
    },
    ga_summary: dash?.optimizer_summary || null,
  };
}

export interface ConsoleContextValue {
  dashboard: DashboardPayload | null;
  setDashboard: React.Dispatch<React.SetStateAction<DashboardPayload | null>>;
  apiOnline: boolean;
  reconnectAttempts: number;
  chromeOnline: boolean;
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
  refreshSectorsView: (galaxyOverride?: number) => Promise<void>;
  espData: Record<string, any> | null;
  setEspData: (data: Record<string, any> | null) => void;
  refreshEspionageView: () => Promise<void>;
  colonyData: Record<string, any> | null;
  setColonyData: (data: Record<string, any> | null) => void;
  refreshColoniesView: () => Promise<void>;
  atkData: Record<string, any> | null;
  setAtkData: (data: Record<string, any> | null) => void;
  refreshAttacksView: () => Promise<void>;
  recData: Record<string, any> | null;
  setRecData: (data: Record<string, any> | null) => void;
  refreshRecyclersView: (categoryOverride?: string) => Promise<void>;
  actionGraphData: Record<string, any> | null;
  setActionGraphData: (data: Record<string, any> | null) => void;
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

function readInitialPersistedState(): {
  activePage: PageKey;
  selectedPlanetId: number | null;
  prefs: UiPrefs;
} {
  let activePage: PageKey = "dashboard";
  let selectedPlanetId: number | null = null;
  const prefs: UiPrefs = { ...DEFAULT_UI_PREFS };

  if (typeof window === "undefined") {
    return { activePage, selectedPlanetId, prefs };
  }

  try {
    const raw = sessionStorage.getItem(SESSION_STATE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        const savedPid = safeFiniteNumber(saved.selectedPlanetId, null);
        if (savedPid !== null) selectedPlanetId = savedPid;
        if (saved.bldCategory) prefs.bldCategory = String(saved.bldCategory);
        if (saved.selectedUnitKey) prefs.selectedUnitKey = String(saved.selectedUnitKey);
        if (saved.combatSubTab) prefs.combatSubTab = String(saved.combatSubTab);
        if (saved.queueFilter) prefs.queueFilter = String(saved.queueFilter);
        if (saved.activeLogSource) prefs.activeLogSource = String(saved.activeLogSource);
        if (saved.agEdgeFilter === "all" || saved.agEdgeFilter === "deps") {
          prefs.agEdgeFilter = saved.agEdgeFilter;
        }
        if (saved.agParamTab === "core" || saved.agParamTab === "scope") {
          prefs.agParamTab = saved.agParamTab;
        }
        if (saved.activePage && PAGE_TITLES[saved.activePage as PageKey]) {
          activePage = saved.activePage as PageKey;
        }
      }
    }
  } catch {
    // Ignore storage restrictions
  }

  const rawHash = (window.location.hash || "").replace(/^#/, "");
  const normHash = rawHash === "graph" ? "action-graph" : rawHash;
  if (normHash && PAGE_TITLES[normHash as PageKey]) {
    activePage = normHash as PageKey;
  }

  return { activePage, selectedPlanetId, prefs };
}

export interface ConsoleProviderProps {
  children: React.ReactNode;
  disableInitialFetch?: boolean;
}

export const ConsoleProvider: React.FC<ConsoleProviderProps> = ({
  children,
  disableInitialFetch = false,
}) => {
  const initial = useMemo(() => readInitialPersistedState(), []);

  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null);
  const [apiOnline, setApiOnlineState] = useState<boolean>(false);
  const [reconnectAttempts, setReconnectAttempts] = useState<number>(0);
  const [activePage, setActivePageState] = useState<PageKey>(initial.activePage);
  const [selectedPlanetId, setSelectedPlanetIdState] = useState<number | null>(
    initial.selectedPlanetId
  );
  const [nowEpochSec, setNowEpochSec] = useState<number>(() =>
    typeof window === "undefined" ? 0 : Math.floor(Date.now() / 1000)
  );
  const [gaCalcCount, setGaCalcCount] = useState<number>(0);
  const [feedback, setFeedback] = useState<FeedbackState | null>(null);
  const [uiPrefs, setUiPrefs] = useState<UiPrefs>(initial.prefs);

  const [buildingsEtaData, setBuildingsEtaDataState] = useState<Record<string, any> | null>(null);
  const [combatData, setCombatDataState] = useState<Record<string, any> | null>(null);
  const [sectorsData, setSectorsDataState] = useState<Record<string, any> | null>(null);
  const [espData, setEspDataState] = useState<Record<string, any> | null>(null);
  const [colonyData, setColonyDataState] = useState<Record<string, any> | null>(null);
  const [atkData, setAtkDataState] = useState<Record<string, any> | null>(null);
  const [recData, setRecDataState] = useState<Record<string, any> | null>(null);
  const [actionGraphData, setActionGraphDataState] = useState<Record<string, any> | null>(null);

  const [agSimDirty, setAgSimDirtyState] = useState<boolean>(false);
  const [agSimRunning, setAgSimRunning] = useState<number>(0);
  const [configDirty, setConfigDirtyState] = useState<boolean>(false);
  const [configSaving, setConfigSaving] = useState<number>(0);
  const [configHydrationTick, setConfigHydrationTick] = useState<number>(0);
  const [agHydrationTick, setAgHydrationTick] = useState<number>(0);
  const [lastOptimizerSummary, setLastOptimizerSummaryState] = useState<Record<string, any> | null>(
    null
  );

  const [notifications, setNotifications] = useState<NotificationsState>({
    unread_count: 0,
    items: [],
  });
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

  // Keep sharedLegacyState synchronized at all times for existing E2E & Node tests
  sharedLegacyState.dashboard = dashboard;
  sharedLegacyState.apiOnline = apiOnline;
  sharedLegacyState.reconnectAttempts = reconnectAttempts;
  sharedLegacyState.activePage = activePage;
  sharedLegacyState.selectedPlanetId = selectedPlanetId;
  sharedLegacyState.modeOverride = uiPrefs.modeOverride;
  sharedLegacyState.bldCategory = uiPrefs.bldCategory;
  sharedLegacyState.selectedUnitKey = uiPrefs.selectedUnitKey;
  sharedLegacyState.combatSubTab = uiPrefs.combatSubTab;
  sharedLegacyState.queueFilter = uiPrefs.queueFilter;
  sharedLegacyState.activeLogSource = uiPrefs.activeLogSource;
  sharedLegacyState.agEdgeFilter = uiPrefs.agEdgeFilter;
  sharedLegacyState.agParamTab = uiPrefs.agParamTab;
  sharedLegacyState.sectorFilter = uiPrefs.sectorFilter;
  sharedLegacyState.sectorGalaxy = uiPrefs.sectorGalaxy;
  sharedLegacyState.espTab = uiPrefs.espTab;
  sharedLegacyState.colonyFilter = uiPrefs.colonyFilter;
  sharedLegacyState.atkTab = uiPrefs.atkTab;
  sharedLegacyState.recTab = uiPrefs.recTab;
  sharedLegacyState.emailCatFilter = uiPrefs.emailCatFilter;
  sharedLegacyState.notifFilter = uiPrefs.notifFilter;
  sharedLegacyState.buildingsEtaData = buildingsEtaData;
  sharedLegacyState.combatData = combatData;
  sharedLegacyState.sectorsData = sectorsData;
  sharedLegacyState.espData = espData;
  sharedLegacyState.colonyData = colonyData;
  sharedLegacyState.atkData = atkData;
  sharedLegacyState.recData = recData;
  sharedLegacyState.actionGraphData = actionGraphData;
  sharedLegacyState.agSimDirty = agSimDirty;
  sharedLegacyState.agSimRunning = agSimRunning;
  sharedLegacyState.gaCalcCount = gaCalcCount;
  sharedLegacyState.lastOptimizerSummary = lastOptimizerSummary;
  sharedLegacyState.notifications = notifications;
  sharedLegacyState.activeHelpNotifId = activeHelpNotif ? safeFiniteNumber(activeHelpNotif.id, null) : null;

  const persistUiPrefs = useCallback(
    (nextPage: PageKey, nextPid: number | null, nextPrefs: UiPrefs) => {
      if (typeof window === "undefined") return;
      try {
        sessionStorage.setItem(
          SESSION_STATE_KEY,
          JSON.stringify({
            activePage: nextPage,
            selectedPlanetId: nextPid,
            bldCategory: nextPrefs.bldCategory,
            selectedUnitKey: nextPrefs.selectedUnitKey,
            combatSubTab: nextPrefs.combatSubTab,
            queueFilter: nextPrefs.queueFilter,
            activeLogSource: nextPrefs.activeLogSource,
            agEdgeFilter: nextPrefs.agEdgeFilter,
            agParamTab: nextPrefs.agParamTab,
          })
        );
      } catch {
        // Ignore storage restrictions
      }
    },
    []
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
      setApiOnlineState(online);
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
    [showFeedback]
  );

  const syncNotificationsPayload = useCallback(
    (notifPayload: Record<string, any> | null | undefined) => {
      if (!notifPayload || typeof notifPayload !== "object") return;
      const items = Array.isArray(notifPayload.items) ? notifPayload.items : [];
      const unreadCnt = Math.max(0, Math.round(safeFiniteNumber(notifPayload.unread_count, 0) ?? 0));
      const next: NotificationsState = { unread_count: unreadCnt, items };
      sharedLegacyState.notifications = next;
      setNotifications(next);

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
    []
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

  const setBuildingsEtaData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.buildingsEtaData = data;
    setBuildingsEtaDataState(data);
  }, []);

  const setCombatData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.combatData = data;
    setCombatDataState(data);
  }, []);

  const setSectorsData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.sectorsData = data;
    setSectorsDataState(data);
  }, []);

  const setEspData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.espData = data;
    setEspDataState(data);
  }, []);

  const setColonyData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.colonyData = data;
    setColonyDataState(data);
  }, []);

  const setAtkData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.atkData = data;
    setAtkDataState(data);
  }, []);

  const setRecData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.recData = data;
    setRecDataState(data);
  }, []);

  const setActionGraphData = useCallback((data: Record<string, any> | null) => {
    sharedLegacyState.actionGraphData = data;
    setActionGraphDataState(data);
  }, []);

  const setLastOptimizerSummary = useCallback((summary: Record<string, any> | null) => {
    sharedLegacyState.lastOptimizerSummary = summary;
    setLastOptimizerSummaryState(summary);
  }, []);

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
        persistUiPrefs(
          sharedLegacyState.activePage,
          sharedLegacyState.selectedPlanetId,
          uiPrefsRef.current
        );
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
    [persistUiPrefs, showFeedback]
  );

  const refreshBuildingsEtaForSelectedPlanet = useCallback(async (forceRefresh = false) => {
    const planet = selectActivePlanet(sharedLegacyState.dashboard, sharedLegacyState.selectedPlanetId);
    if (!planet) return;
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
      // Non-fatal during offline fallback
    }
  }, [setBuildingsEtaData]);

  const refreshCombatForSelectedPlanet = useCallback(
    async ({ refresh = false }: { refresh?: boolean } = {}): Promise<boolean> => {
      const planet = selectActivePlanet(
        sharedLegacyState.dashboard,
        sharedLegacyState.selectedPlanetId
      );
      if (!planet) return false;
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
      }
    },
    [setCombatData, showFeedback]
  );

  const refreshSectorsView = useCallback(
    async (galaxyOverride?: number) => {
      try {
        const gal =
          galaxyOverride !== undefined
            ? galaxyOverride
            : Math.max(0, Math.round(safeFiniteNumber(sharedLegacyState.sectorGalaxy, 0) ?? 0));
        const planet = selectActivePlanet(
          sharedLegacyState.dashboard,
          sharedLegacyState.selectedPlanetId
        );
        const payload: Record<string, any> = {
          command: "scanned_sectors",
          limit: 5600,
        };
        if (gal > 0) payload.galaxy = gal;
        if (planet?.planet_id) payload.planet_id = planet.planet_id;
        const data = await rawApiCommand(payload);
        setSectorsData(data);
      } catch (err: any) {
        showFeedback(`Scanned sectors load failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [setSectorsData, showFeedback]
  );

  const refreshEspionageView = useCallback(async () => {
    try {
      const data = await rawApiCommand({ command: "espionage_intel", limit: 200 });
      setEspData(data);
    } catch (err: any) {
      showFeedback(`Espionage data load failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [setEspData, showFeedback]);

  const refreshColoniesView = useCallback(async () => {
    try {
      const data = await rawApiCommand({ command: "colony_candidates", limit: 200 });
      setColonyData(data);
    } catch (err: any) {
      showFeedback(`Colony grading load failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [setColonyData, showFeedback]);

  const refreshAttacksView = useCallback(async () => {
    try {
      const data = await rawApiCommand({ command: "attack_results", limit: 200 });
      setAtkData(data);
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
        setRecData(data);
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

        sharedLegacyState.dashboard = nextDash;
        setDashboard(nextDash);

        if (nextDash.optimizer_summary) {
          setLastOptimizerSummary(nextDash.optimizer_summary);
        }

        const modeVal = nextDash.bot_control?.mode_override || "auto";
        setUiPrefs((prev) =>
          prev.modeOverride === modeVal ? prev : { ...prev, modeOverride: modeVal }
        );

        if (nextDash.buildings_eta) {
          if (
            !sharedLegacyState.selectedPlanetId ||
            sharedLegacyState.selectedPlanetId === nextDash.buildings_eta.planet?.planet_id
          ) {
            setBuildingsEtaData(nextDash.buildings_eta);
          }
        }
        if (nextDash.combat) {
          if (
            !sharedLegacyState.selectedPlanetId ||
            sharedLegacyState.selectedPlanetId === nextDash.combat.planet_id
          ) {
            setCombatData(nextDash.combat);
          }
        }
        if (nextDash.notifications && typeof nextDash.notifications === "object") {
          syncNotificationsPayload(nextDash.notifications);
        }

        const curPage = sharedLegacyState.activePage;
        if (curPage === "sectors") {
          refreshSectorsView();
        } else if (curPage === "espionage") {
          refreshEspionageView();
        } else if (curPage === "colonies") {
          refreshColoniesView();
        } else if (curPage === "attacks") {
          refreshAttacksView();
        } else if (curPage === "recyclers") {
          refreshRecyclersView();
        }

        if (!sharedLegacyState.configDirty && sharedLegacyState.configSaving === 0) {
          sharedLegacyState.configDirty = false;
          setConfigDirtyState(false);
          setConfigHydrationTick((t) => t + 1);
        }

        if (
          !sharedLegacyState.agSimDirty &&
          sharedLegacyState.agSimRunning === 0 &&
          sharedLegacyState.configSaving === 0
        ) {
          const ag = nextDash.action_graph || buildClientFallbackActionGraph(nextDash);
          setActionGraphData(ag);
          setAgHydrationTick((t) => t + 1);
        }

        return true;
      } catch (err: any) {
        if (reqSeq !== sharedLegacyState.dashboardSeq) return false;
        if (isRateLimitError(err)) {
          setApiStatus(true);
          showFeedback(`SpaceInvasion rate-limit cooldown active: ${err.message}`, "warn");
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
      applyVersionSnapshot,
      refreshAttacksView,
      refreshColoniesView,
      refreshEspionageView,
      refreshRecyclersView,
      refreshSectorsView,
      setActionGraphData,
      setApiStatus,
      setBuildingsEtaData,
      setCombatData,
      setLastOptimizerSummary,
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
      const normKey = rawKey === "graph" ? "action-graph" : rawKey;
      const target: PageKey = PAGE_TITLES[normKey as PageKey]
        ? (normKey as PageKey)
        : "dashboard";
      sharedLegacyState.activePage = target;
      setActivePageState(target);
      setUiPrefs((prev) => {
        const next = prev.sidebarOpen ? { ...prev, sidebarOpen: false } : prev;
        persistUiPrefs(target, sharedLegacyState.selectedPlanetId, next);
        return next;
      });
      if (typeof window !== "undefined" && window.location.hash !== `#${target}`) {
        window.history.replaceState(null, "", `#${target}`);
      }

      if (target === "buildings") {
        refreshBuildingsEtaForSelectedPlanet();
      } else if (target === "combat") {
        refreshCombatForSelectedPlanet({ refresh: false });
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
      persistUiPrefs,
      refreshAttacksView,
      refreshBuildingsEtaForSelectedPlanet,
      refreshColoniesView,
      refreshCombatForSelectedPlanet,
      refreshEspionageView,
      refreshRecyclersView,
      refreshSectorsView,
      setActionGraphData,
    ]
  );

  const setSelectedPlanetId = useCallback(
    (planetId: number | null) => {
      const pid = planetId == null ? null : safeFiniteNumber(planetId, null);
      sharedLegacyState.selectedPlanetId = pid;
      sharedLegacyState.simPopulatedOnce = false;
      setSelectedPlanetIdState(pid);
      persistUiPrefs(sharedLegacyState.activePage, pid, uiPrefsRef.current);
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
      persistUiPrefs,
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
      setUiPrefs((prev) => {
        const next = { ...prev, [key]: value };
        persistUiPrefs(sharedLegacyState.activePage, sharedLegacyState.selectedPlanetId, next);
        return next;
      });
    },
    [persistUiPrefs]
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
          setNotifications({
            unread_count: Math.max(0, Math.round(safeFiniteNumber(res.unread_count, 0) ?? 0)),
            items: res.items,
          });
          sharedLegacyState.notifications = {
            unread_count: Math.max(0, Math.round(safeFiniteNumber(res.unread_count, 0) ?? 0)),
            items: res.items,
          };
        }
      } catch {
        // Offline fallback keeps cached items
      }
    },
    []
  );

  const openNotificationCenter = useCallback(async () => {
    setNotifCenterOpen(true);
    notifCenterOpenRef.current = true;
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
    g.renderGaNnHud = applySyntheticDash;
    g.renderRanking = applySyntheticDash;
  }, [
    loadDashboard,
    setActivePage,
    setBuildingsEtaData,
    setLastOptimizerSummary,
    showFeedback,
  ]);

  // Boot fetch + live polling timers + Escape modal handler
  useClientSyncEffect(() => {
    if (disableInitialFetch || typeof window === "undefined") return;

    checkApiHealthAndHotReload();
    loadDashboard({ refresh: false, preserveConfig: false });

    const tickTimer = setInterval(() => {
      if (document.visibilityState === "visible") {
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
      lastOptimizerSummary,
      loadDashboard,
      notifCenterOpen,
      notifications,
      nowEpochSec,
      openNotificationCenter,
      recData,
      reconnectAttempts,
      refreshAttacksView,
      refreshBuildingsEtaForSelectedPlanet,
      refreshColoniesView,
      refreshCombatForSelectedPlanet,
      refreshEspionageView,
      refreshNotificationCenter,
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
      setEspData,
      setLastOptimizerSummary,
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
