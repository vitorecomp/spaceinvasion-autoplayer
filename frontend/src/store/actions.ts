import type React from "react";
import {
  apiCommand as rawApiCommand,
  apiGet as rawApiGet,
  feedbackKindForError,
  isRateLimitError,
  isSweepBlockedError,
  safeFiniteNumber,
} from "../api/httpClient";
import {
  DashboardPayload,
  FeedbackKind,
  NotificationsState,
  PageKey,
} from "../types/api";
import { buildClientFallbackActionGraph } from "./fallbackActionGraph";
import { buildDashboardPollUrl, mergeDashboardDeltaResponse } from "./dashboardDelta";
import { normalizePageKey } from "./persistence";
import { createViewRefreshActions } from "./refreshActions";
import { ConsoleActions, ConsoleStore, UiPrefs } from "./types";

const HOT_RELOAD_GUARD_KEY = "si_hot_reload_guard";
const HOT_RELOAD_GUARD_TTL_MS = 15_000;

function isViteDevDocument(): boolean {
  if (typeof document === "undefined") return false;
  const hasViteMain = Boolean(
    document.querySelector('script[type="module"][src*="/src/main"]')
  );
  const hasBuiltBundle = Boolean(document.querySelector('script[src*="/app.js"]'));
  return hasViteMain && !hasBuiltBundle;
}

function shouldSuppressHotReloadLoop(targetSig: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    const raw = window.sessionStorage?.getItem(HOT_RELOAD_GUARD_KEY);
    if (!raw) return false;
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      parsed.sig === targetSig &&
      Date.now() - Number(parsed.ts || 0) < HOT_RELOAD_GUARD_TTL_MS
    ) {
      return true;
    }
  } catch {
    // Ignore sessionStorage errors
  }
  return false;
}

function recordHotReloadGuard(targetSig: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage?.setItem(
      HOT_RELOAD_GUARD_KEY,
      JSON.stringify({ sig: targetSig, ts: Date.now() })
    );
  } catch {
    // Ignore sessionStorage errors
  }
}

export interface CreateConsoleActionsOptions {
  shouldPersist: boolean;
  disableInitialFetch: boolean;
}

export function createConsoleActions(
  store: ConsoleStore,
  opts: CreateConsoleActionsOptions
): ConsoleActions {
  let feedbackTimer: ReturnType<typeof setTimeout> | null = null;
  const uiVersions: {
    code_version: string | null;
    css_version: string | null;
    server_boot_id: string | null;
  } = {
    code_version: null,
    css_version: null,
    server_boot_id: null,
  };
  const seenHelpSigs = new Set<string>();

  const setDashboard: React.Dispatch<React.SetStateAction<DashboardPayload | null>> = (next) => {
    store.setDashboard(next);
  };

  const showFeedback = (
    message: string,
    kind: FeedbackKind = "ok",
    feedbackOpts: { autoHideMs?: number } = {}
  ) => {
    if (feedbackTimer) {
      clearTimeout(feedbackTimer);
      feedbackTimer = null;
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
    store.setRuntime({ feedback: { message, kind, hidden: false } });
    const autoHideMs = feedbackOpts.autoHideMs ?? 0;
    if (autoHideMs > 0) {
      feedbackTimer = setTimeout(() => {
        if (store.getSnapshot().apiOnline) {
          if (typeof document !== "undefined") {
            const banner = document.getElementById("feedback-banner");
            if (banner) banner.hidden = true;
          }
          store.setRuntime((prev) => ({
            ...prev,
            feedback: prev.feedback ? { ...prev.feedback, hidden: true } : null,
          }));
        }
      }, autoHideMs);
    }
  };

  const refreshers = createViewRefreshActions(store, showFeedback, (loadOpts) =>
    loadDashboard(loadOpts)
  );

  const setApiStatus = (online: boolean, note = "") => {
    const snap = store.getSnapshot();
    const rt = store.getRuntimeSnapshot();
    const wasOnline = snap.apiOnline;
    store.setApiOnline(online);
    const port = snap.dashboard?.health?.debug_port
      ? "8787"
      : (typeof window !== "undefined" && window.location.port) || "8787";

    if (online) {
      const recovered = !wasOnline && rt.reconnectAttempts > 0;
      store.setRuntime({ reconnectAttempts: 0 });
      if (recovered) {
        showFeedback(`Reconnected to Game API :${port}.`, "ok", { autoHideMs: 3200 });
      }
    } else {
      store.setRuntime({ reconnectAttempts: rt.reconnectAttempts + 1 });
      showFeedback(
        `API offline (${note || "connection lost"}) — auto-reconnecting every 1.2s...`,
        "warn"
      );
    }
  };

  const syncNotificationsPayload = (notifPayload: Record<string, any> | null | undefined) => {
    if (!notifPayload || typeof notifPayload !== "object") return;
    const items = Array.isArray(notifPayload.items) ? notifPayload.items : [];
    const unreadCnt = Math.max(0, Math.round(safeFiniteNumber(notifPayload.unread_count, 0) ?? 0));
    const next: NotificationsState = { unread_count: unreadCnt, items };
    store.setNotifications(next);

    const rt = store.getRuntimeSnapshot();
    const newestUnread = items.find(
      (n: any) => n && n.status === "unread" && (safeFiniteNumber(n.id, 0) ?? 0) > 0
    );
    if (newestUnread) {
      const sig = `${newestUnread.id}:${safeFiniteNumber(newestUnread.occurrence_count, 1) ?? 1}`;
      if (!seenHelpSigs.has(sig) && !rt.notifCenterOpen) {
        seenHelpSigs.add(sig);
        store.setRuntime({ activeHelpNotif: newestUnread });
      }
    } else if (rt.activeHelpNotif !== null) {
      store.setRuntime({ activeHelpNotif: null });
    }
  };

  const beginGaCalc = () => {
    store.setRuntime((prev) => ({ ...prev, gaCalcCount: prev.gaCalcCount + 1 }));
    if (typeof document !== "undefined") {
      const hud = document.getElementById("gann-calc-hud");
      if (hud) hud.classList.add("is-calculating");
    }
  };

  const endGaCalc = () => {
    const next = store.setRuntime((prev) => ({
      ...prev,
      gaCalcCount: Math.max(0, prev.gaCalcCount - 1),
    }));
    if (typeof document !== "undefined" && next.gaCalcCount === 0) {
      const hud = document.getElementById("gann-calc-hud");
      if (hud) hud.classList.remove("is-calculating");
    }
  };

  const setConfigDirty = (dirty: boolean) => {
    store.setRuntime({ configDirty: Boolean(dirty) });
  };

  const setConfigSaving: React.Dispatch<React.SetStateAction<number>> = (next) => {
    store.setRuntime((prev) => ({
      ...prev,
      configSaving: Math.max(0, typeof next === "function" ? next(prev.configSaving) : next),
    }));
  };

  const setAgSimDirty = (dirty: boolean) => {
    store.setRuntime({ agSimDirty: Boolean(dirty) });
  };

  const setAgSimRunning: React.Dispatch<React.SetStateAction<number>> = (next) => {
    store.setRuntime((prev) => ({
      ...prev,
      agSimRunning: Math.max(0, typeof next === "function" ? next(prev.agSimRunning) : next),
    }));
  };

  const bumpDashboardSeq = () =>
    store.setRuntime((prev) => ({ ...prev, dashboardSeq: prev.dashboardSeq + 1 })).dashboardSeq;

  const bumpAgSimSeq = () =>
    store.setRuntime((prev) => ({ ...prev, agSimSeq: prev.agSimSeq + 1 })).agSimSeq;

  const bumpConfigHydration = () => {
    store.setRuntime((prev) => ({ ...prev, configHydrationTick: prev.configHydrationTick + 1 }));
  };

  const setSimPopulatedOnce = (value: boolean) => {
    store.setRuntime({ simPopulatedOnce: Boolean(value) });
  };

  const setNowEpochSec = (epochSec: number) => {
    store.setRuntime({ nowEpochSec: epochSec });
  };

  const applyVersionSnapshot = (vData: Record<string, any> | null | undefined): boolean => {
    if (!vData) return false;
    if (uiVersions.code_version == null) {
      uiVersions.code_version = vData.code_version || null;
      uiVersions.css_version = vData.css_version || null;
      uiVersions.server_boot_id = vData.server_boot_id || null;
      return false;
    }
    const viteDev = isViteDevDocument();
    const codeChanged =
      !viteDev &&
      Boolean(vData.code_version && vData.code_version !== uiVersions.code_version);
    const bootChanged = Boolean(
      vData.server_boot_id &&
        uiVersions.server_boot_id &&
        vData.server_boot_id !== uiVersions.server_boot_id
    );
    if (store.getSnapshot().dashboard?.bot_control?.captcha_blocked) return false;
    if (codeChanged || bootChanged) {
      const targetSig = `${vData.server_boot_id || uiVersions.server_boot_id || ""}|${vData.code_version || uiVersions.code_version || ""}`;
      if (shouldSuppressHotReloadLoop(targetSig)) {
        uiVersions.code_version = vData.code_version || uiVersions.code_version;
        uiVersions.css_version = vData.css_version || uiVersions.css_version;
        uiVersions.server_boot_id = vData.server_boot_id || uiVersions.server_boot_id;
        return false;
      }
      recordHotReloadGuard(targetSig);
      store.flushPersistence(true);
      showFeedback(
        codeChanged
          ? "UI update detected — hot-reloading console..."
          : "Game API restarted — hot-reloading console...",
        "ok"
      );
      if (typeof window !== "undefined") setTimeout(() => window.location.reload(), 120);
      return true;
    }
    if (!viteDev && vData.css_version && vData.css_version !== uiVersions.css_version) {
      uiVersions.css_version = vData.css_version;
      if (typeof document !== "undefined") {
        const link = document.getElementById("ui-stylesheet") as HTMLLinkElement | null;
        if (link) link.href = `/styles.css?v=${encodeURIComponent(vData.css_version)}`;
      }
      showFeedback("Hot-reloaded styles.css live.", "ok", { autoHideMs: 2600 });
    } else if (viteDev) {
      uiVersions.code_version = vData.code_version || uiVersions.code_version;
      uiVersions.css_version = vData.css_version || uiVersions.css_version;
      uiVersions.server_boot_id = vData.server_boot_id || uiVersions.server_boot_id;
    }
    return false;
  };

  const loadDashboard = async ({
    refresh = false,
    preserveConfig = true,
    force = false,
  }: {
    refresh?: boolean;
    preserveConfig?: boolean;
    force?: boolean;
  } = {}): Promise<boolean> => {
    const rtBefore = store.getRuntimeSnapshot();
    if (!refresh && !force && rtBefore.inFlightDashboardCount > 0) return false;
    const reqSeq = rtBefore.dashboardSeq + 1;
    store.setRuntime((prev) => ({
      ...prev,
      dashboardSeq: reqSeq,
      inFlightDashboardCount: prev.inFlightDashboardCount + 1,
    }));
    try {
      const prevBeforeFetch = store.getSnapshot().dashboard;
      const rawDash = await rawApiGet<DashboardPayload>(
        buildDashboardPollUrl(refresh, prevBeforeFetch),
        15000
      );
      if (reqSeq !== store.getRuntimeSnapshot().dashboardSeq) return false;
      const prevSnap = store.getSnapshot();
      const dash = mergeDashboardDeltaResponse(prevSnap.dashboard ?? prevBeforeFetch, rawDash);
      if (applyVersionSnapshot(dash)) return false;
      setApiStatus(true);

      const rtNow = store.getRuntimeSnapshot();
      const preserveUnsavedCfg =
        (rtNow.configDirty || rtNow.configSaving > 0) && preserveConfig;
      const nextDash: DashboardPayload =
        preserveUnsavedCfg && prevSnap.dashboard?.config
          ? {
              ...dash,
              config: prevSnap.dashboard.config,
              planner_mode: prevSnap.dashboard.planner_mode || dash.planner_mode,
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
        !rtNow.agSimDirty && rtNow.agSimRunning === 0 && rtNow.configSaving === 0;
      const nextActionGraph = shouldPatchActionGraph
        ? nextDash.action_graph || buildClientFallbackActionGraph(nextDash)
        : null;

      store.setState(
        (prev) => ({
          ...prev,
          dashboard: nextDash,
          uiPrefs:
            prev.uiPrefs.modeOverride === modeVal
              ? prev.uiPrefs
              : { ...prev.uiPrefs, modeOverride: modeVal },
          lastOptimizerSummary: nextDash.optimizer_summary || prev.lastOptimizerSummary,
          plannerJobsStatus: incomingJobsStatus || prev.plannerJobsStatus,
          buildingsEtaData:
            nextDash.buildings_eta &&
            (!prev.selectedPlanetId ||
              prev.selectedPlanetId === nextDash.buildings_eta.planet?.planet_id)
              ? nextDash.buildings_eta
              : prev.buildingsEtaData,
          combatData:
            nextDash.combat &&
            (!prev.selectedPlanetId || prev.selectedPlanetId === nextDash.combat.planet_id)
              ? nextDash.combat
              : prev.combatData,
          actionGraphData: shouldPatchActionGraph ? nextActionGraph : prev.actionGraphData,
          lastHydratedAt: Date.now(),
        }),
        { persist: true }
      );

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

      const afterSnap = store.getSnapshot();
      const curPage = afterSnap.activePage;
      if (curPage === "sectors") {
        void refreshers.refreshSectorsView(undefined, undefined, undefined, { background: true });
      } else if (curPage === "espionage") {
        void refreshers.refreshEspionageView();
      } else if (curPage === "colonies") {
        void refreshers.refreshColoniesView({ background: true });
      } else if (curPage === "attacks") {
        void refreshers.refreshAttacksView();
      } else if (curPage === "recyclers") {
        void refreshers.refreshRecyclersView();
      } else if (curPage === "buildings" || (curPage as string) === "research") {
        if (
          !nextDash.buildings_eta ||
          (afterSnap.selectedPlanetId &&
            afterSnap.selectedPlanetId !== nextDash.buildings_eta.planet?.planet_id)
        ) {
          void refreshers.refreshBuildingsEtaForSelectedPlanet(false);
        }
      } else if (curPage === "combat") {
        if (
          !nextDash.combat ||
          (afterSnap.selectedPlanetId && afterSnap.selectedPlanetId !== nextDash.combat.planet_id)
        ) {
          void refreshers.refreshCombatForSelectedPlanet({ refresh: false });
        }
      }

      store.setRuntime((prev) => {
        const updateConfigTick = !prev.configDirty && prev.configSaving === 0;
        if (!updateConfigTick && !shouldPatchActionGraph) return prev;
        return {
          ...prev,
          configDirty: updateConfigTick ? false : prev.configDirty,
          configHydrationTick: updateConfigTick
            ? prev.configHydrationTick + 1
            : prev.configHydrationTick,
          agHydrationTick: shouldPatchActionGraph
            ? prev.agHydrationTick + 1
            : prev.agHydrationTick,
        };
      });
      return true;
    } catch (err: any) {
      if (reqSeq !== store.getRuntimeSnapshot().dashboardSeq) return false;
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
      store.setRuntime((prev) => ({
        ...prev,
        inFlightDashboardCount: Math.max(0, prev.inFlightDashboardCount - 1),
      }));
    }
  };

  const checkApiHealthAndHotReload = async () => {
    if (store.getRuntimeSnapshot().inFlightVersionCheck) return;
    store.setRuntime({ inFlightVersionCheck: true });
    try {
      const vData = await rawApiGet("/ui-version", 2500);
      if (applyVersionSnapshot(vData)) return;
      const wasOffline = !store.getSnapshot().apiOnline;
      setApiStatus(true);
      if (wasOffline) await loadDashboard({ refresh: false, preserveConfig: true });
    } catch (err: any) {
      const msg = err?.name === "AbortError" ? "timeout" : err?.message || "unreachable";
      setApiStatus(false, msg);
    } finally {
      store.setRuntime({ inFlightVersionCheck: false });
    }
  };

  const setActivePage = (rawKey: PageKey | string) => {
    const target = normalizePageKey(rawKey);
    store.setState(
      (prev) => {
        const nextPrefs = prev.uiPrefs.sidebarOpen
          ? { ...prev.uiPrefs, sidebarOpen: false }
          : prev.uiPrefs;
        if (prev.activePage === target && nextPrefs === prev.uiPrefs) return prev;
        return { ...prev, activePage: target, uiPrefs: nextPrefs };
      },
      { persist: true }
    );
    if (
      target === "dashboard" ||
      target === "operations" ||
      (target as string) === "fleet-control"
    ) {
      setNowEpochSec(Math.floor(Date.now() / 1000));
    }
    if (
      opts.shouldPersist &&
      typeof window !== "undefined" &&
      window.location.hash !== `#${target}`
    ) {
      window.history.replaceState(null, "", `#${target}`);
    }

    if (target === "buildings" || (target as string) === "research") {
      void refreshers.refreshBuildingsEtaForSelectedPlanet();
    } else if (target === "combat") {
      void refreshers.refreshCombatForSelectedPlanet({ refresh: false });
    } else if ((target as string) === "fleet-control") {
      if (!opts.disableInitialFetch) void loadDashboard({ refresh: false, preserveConfig: true });
    } else if (target === "sectors") {
      void refreshers.refreshSectorsView();
    } else if (target === "espionage") {
      void refreshers.refreshEspionageView();
    } else if (target === "colonies") {
      void refreshers.refreshColoniesView();
    } else if (target === "attacks") {
      void refreshers.refreshAttacksView();
    } else if (target === "recyclers") {
      void refreshers.refreshRecyclersView();
    } else if (target === "action-graph") {
      const snap = store.getSnapshot();
      if (!snap.actionGraphData) {
        if (snap.dashboard?.action_graph) store.setActionGraphData(snap.dashboard.action_graph);
        else if (snap.dashboard) {
          store.setActionGraphData(buildClientFallbackActionGraph(snap.dashboard));
        }
      }
    }
  };

  const setSelectedPlanetId = (planetId: number | null) => {
    const pid = planetId == null ? null : safeFiniteNumber(planetId, null);
    store.setRuntime({ simPopulatedOnce: false });
    store.setSelectedPlanetId(pid);
    const snap = store.getSnapshot();
    if (snap.dashboard) {
      void refreshers.refreshBuildingsEtaForSelectedPlanet();
      if (snap.activePage === "combat") void refreshers.refreshCombatForSelectedPlanet({ refresh: false });
      else if (snap.activePage === "sectors") void refreshers.refreshSectorsView();
      else if (snap.activePage === "colonies") void refreshers.refreshColoniesView();
    }
  };

  const setUiPref = <K extends keyof UiPrefs>(key: K, value: UiPrefs[K]) => {
    store.setUiPref(key, value);
  };

  const refreshNotificationCenter = async (filterOverride?: string) => {
    const statusFilter = filterOverride ?? store.getSnapshot().uiPrefs.notifFilter ?? "active";
    try {
      const res = await rawApiCommand({
        command: "notifications_list",
        status: statusFilter,
        limit: 60,
      });
      if (Array.isArray(res?.items)) {
        store.setNotifications({
          unread_count: Math.max(0, Math.round(safeFiniteNumber(res.unread_count, 0) ?? 0)),
          items: res.items,
        });
      }
    } catch {
      // Offline fallback keeps cached items
    }
  };

  const openNotificationCenter = async () => {
    store.setRuntime({ notifCenterOpen: true, nowEpochSec: Math.floor(Date.now() / 1000) });
    await refreshNotificationCenter();
  };

  const closeNotificationCenter = () => store.setRuntime({ notifCenterOpen: false });
  const closeHelpPopupOnly = () => store.setRuntime({ activeHelpNotif: null });

  const ackActiveHelpPopup = async (targetStatus: "read" | "dismissed") => {
    const activeHelp = store.getRuntimeSnapshot().activeHelpNotif;
    const nid = activeHelp ? safeFiniteNumber(activeHelp.id, null) : null;
    closeHelpPopupOnly();
    if (!nid || nid <= 0) return;
    try {
      await rawApiCommand({ command: "notification_ack", notification_id: nid, status: targetStatus });
    } catch (err: any) {
      showFeedback(`Failed updating notification: ${err.message}`, feedbackKindForError(err));
    }
  };

  const cleanup = () => {
    if (feedbackTimer) {
      clearTimeout(feedbackTimer);
      feedbackTimer = null;
    }
  };

  return {
    getSnapshot: () => store.getSnapshot(),
    getRuntime: () => store.getRuntimeSnapshot(),
    setDashboard,
    setActivePage,
    setSelectedPlanetId,
    setUiPref,
    setApiStatus,
    beginGaCalc,
    endGaCalc,
    showFeedback,
    apiGet: rawApiGet,
    apiCommand: rawApiCommand,
    loadDashboard,
    checkApiHealthAndHotReload,
    bumpDashboardSeq,
    bumpAgSimSeq,
    bumpConfigHydration,
    setSimPopulatedOnce,
    setNowEpochSec,
    setBuildingsEtaData: (d) => store.setBuildingsEtaData(d),
    setCombatData: (d) => store.setCombatData(d),
    setSectorsData: (d) => store.setSectorsData(d),
    setEspData: (d) => store.setEspData(d),
    setColonyData: (d) => store.setColonyData(d),
    setAtkData: (d) => store.setAtkData(d),
    setRecData: (d) => store.setRecData(d),
    setActionGraphData: (d) => store.setActionGraphData(d),
    setPlannerJobsStatus: (s) => store.setPlannerJobsStatus(s),
    setLastOptimizerSummary: (s) => store.setLastOptimizerSummary(s),
    ...refreshers,
    setAgSimDirty,
    setAgSimRunning,
    setConfigDirty,
    setConfigSaving,
    syncNotificationsPayload,
    openNotificationCenter,
    closeNotificationCenter,
    refreshNotificationCenter,
    ackActiveHelpPopup,
    closeHelpPopupOnly,
    cleanup,
  };
}
