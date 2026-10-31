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
  PlannerJobsStatusPayload,
} from "../types/api";
import { buildClientFallbackActionGraph, selectActivePlanet } from "./fallbackActionGraph";
import { normalizePageKey } from "./persistence";
import {
  ConsoleActions,
  ConsoleStore,
  SectorQueuePreviewOverrides,
  UiPrefs,
} from "./types";

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
  let sectorsReqSeq = 0;
  let inFlightSectorsCount = 0;
  let coloniesReqSeq = 0;
  let inFlightColoniesCount = 0;
  let inFlightBuildingsEta: { key: string; promise: Promise<void> } | null = null;
  let inFlightCombat: { key: string; promise: Promise<boolean> } | null = null;
  let sectorPreview: SectorQueuePreviewOverrides | null = null;

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
      const nextAttempts = rt.reconnectAttempts + 1;
      store.setRuntime({ reconnectAttempts: nextAttempts });
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

  const bumpDashboardSeq = () => {
    const next = store.setRuntime((prev) => ({ ...prev, dashboardSeq: prev.dashboardSeq + 1 }));
    return next.dashboardSeq;
  };

  const bumpAgSimSeq = () => {
    const next = store.setRuntime((prev) => ({ ...prev, agSimSeq: prev.agSimSeq + 1 }));
    return next.agSimSeq;
  };

  const bumpConfigHydration = () => {
    store.setRuntime((prev) => ({
      ...prev,
      configHydrationTick: prev.configHydrationTick + 1,
    }));
  };

  const setSimPopulatedOnce = (value: boolean) => {
    store.setRuntime({ simPopulatedOnce: Boolean(value) });
  };

  const setNowEpochSec = (epochSec: number) => {
    store.setRuntime({ nowEpochSec: epochSec });
  };

  const setBuildingsEtaData = (data: Record<string, any> | null) => {
    store.setBuildingsEtaData(data);
  };

  const setCombatData = (data: Record<string, any> | null) => {
    store.setCombatData(data);
  };

  const setSectorsData = (data: Record<string, any> | null) => {
    store.setSectorsData(data);
  };

  const setEspData = (data: Record<string, any> | null) => {
    store.setEspData(data);
  };

  const setColonyData = (data: Record<string, any> | null) => {
    store.setColonyData(data);
  };

  const setAtkData = (data: Record<string, any> | null) => {
    store.setAtkData(data);
  };

  const setRecData = (data: Record<string, any> | null) => {
    store.setRecData(data);
  };

  const setActionGraphData = (data: Record<string, any> | null) => {
    store.setActionGraphData(data);
  };

  const setPlannerJobsStatus = (status: PlannerJobsStatusPayload | null) => {
    store.setPlannerJobsStatus(status);
  };

  const setLastOptimizerSummary = (summary: Record<string, any> | null) => {
    store.setLastOptimizerSummary(summary);
  };

  const refreshPlannerJobsStatus = async (): Promise<PlannerJobsStatusPayload | null> => {
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
  };

  const applyVersionSnapshot = (vData: Record<string, any> | null | undefined): boolean => {
    if (!vData) return false;
    if (uiVersions.code_version == null) {
      uiVersions.code_version = vData.code_version || null;
      uiVersions.css_version = vData.css_version || null;
      uiVersions.server_boot_id = vData.server_boot_id || null;
      return false;
    }
    const codeChanged = Boolean(
      vData.code_version && vData.code_version !== uiVersions.code_version
    );
    const bootChanged = Boolean(
      vData.server_boot_id &&
        uiVersions.server_boot_id &&
        vData.server_boot_id !== uiVersions.server_boot_id
    );
    if (store.getSnapshot().dashboard?.bot_control?.captcha_blocked) {
      return false;
    }
    if (codeChanged || bootChanged) {
      store.flushPersistence(true);
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
    if (vData.css_version && vData.css_version !== uiVersions.css_version) {
      uiVersions.css_version = vData.css_version;
      if (typeof document !== "undefined") {
        const link = document.getElementById("ui-stylesheet") as HTMLLinkElement | null;
        if (link) {
          link.href = `/styles.css?v=${encodeURIComponent(vData.css_version)}`;
        }
      }
      showFeedback("Hot-reloaded styles.css live.", "ok", { autoHideMs: 2600 });
    }
    return false;
  };

  const refreshBuildingsEtaForSelectedPlanet = async (forceRefresh = false) => {
    const snap = store.getSnapshot();
    const planet = selectActivePlanet(snap.dashboard, snap.selectedPlanetId);
    if (!planet) return;
    const reqKey = `${planet.planet_id}:${forceRefresh ? "1" : "0"}`;
    if (inFlightBuildingsEta?.key === reqKey) {
      return inFlightBuildingsEta.promise;
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
        // Non-fatal during offline fallback
      } finally {
        if (inFlightBuildingsEta?.promise === reqPromise) {
          inFlightBuildingsEta = null;
        }
      }
    })();
    inFlightBuildingsEta = { key: reqKey, promise: reqPromise };
    return reqPromise;
  };

  const refreshCombatForSelectedPlanet = async ({
    refresh = false,
  }: { refresh?: boolean } = {}): Promise<boolean> => {
    const snap = store.getSnapshot();
    const planet = selectActivePlanet(snap.dashboard, snap.selectedPlanetId);
    if (!planet) return false;
    const reqKey = `${planet.planet_id}:${refresh ? "1" : "0"}`;
    if (inFlightCombat?.key === reqKey) {
      return inFlightCombat.promise;
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
        if (inFlightCombat?.promise === reqPromise) {
          inFlightCombat = null;
        }
      }
    })();
    inFlightCombat = { key: reqKey, promise: reqPromise };
    return reqPromise;
  };

  const refreshSectorsView = async (
    galaxyOverride?: number,
    includeUnscannedOverride?: boolean,
    previewOverride?: SectorQueuePreviewOverrides | null,
    refreshOpts?: { background?: boolean }
  ) => {
    if (refreshOpts?.background && inFlightSectorsCount > 0) return;
    if (previewOverride !== undefined) {
      sectorPreview = previewOverride;
    }
    const reqSeq = ++sectorsReqSeq;
    inFlightSectorsCount += 1;
    try {
      const snap = store.getSnapshot();
      const gal =
        galaxyOverride !== undefined
          ? galaxyOverride
          : Math.max(0, Math.round(safeFiniteNumber(snap.uiPrefs.sectorGalaxy, 0) ?? 0));
      const curFilter = String(snap.uiPrefs.sectorFilter || "all");
      const incUnscanned =
        includeUnscannedOverride !== undefined
          ? includeUnscannedOverride
          : gal > 0 || curFilter === "unscanned";
      const planet = selectActivePlanet(snap.dashboard, snap.selectedPlanetId);
      const payload: Record<string, any> = {
        command: "scanned_sectors",
        limit: 5600,
        include_unscanned: incUnscanned,
      };
      if (gal > 0) payload.galaxy = gal;
      if (planet?.planet_id) payload.planet_id = planet.planet_id;
      if (sectorPreview) {
        if (typeof sectorPreview.refresh_pct === "number") {
          payload.preview_refresh_pct = sectorPreview.refresh_pct;
        }
        if (typeof sectorPreview.sweep_pages === "number") {
          payload.preview_sweep_pages = sectorPreview.sweep_pages;
        }
        if (typeof sectorPreview.all_galaxies === "boolean") {
          payload.preview_all_galaxies = sectorPreview.all_galaxies;
        }
      }
      const data = await rawApiCommand(payload);
      if (reqSeq !== sectorsReqSeq) return;
      if (
        data &&
        (Array.isArray(data.sectors) ||
          Array.isArray(data.scan_queue) ||
          (data.summary && typeof data.summary === "object"))
      ) {
        setSectorsData(data);
      }
    } catch (err: any) {
      if (reqSeq !== sectorsReqSeq) return;
      showFeedback(`Scanned sectors load failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      inFlightSectorsCount = Math.max(0, inFlightSectorsCount - 1);
    }
  };

  const refreshEspionageView = async () => {
    try {
      const data = await rawApiCommand({ command: "espionage_intel", limit: 200 });
      if (data && typeof data === "object") setEspData(data);
    } catch (err: any) {
      showFeedback(`Espionage data load failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const refreshColoniesView = async (refreshOpts?: { background?: boolean }) => {
    if (refreshOpts?.background && inFlightColoniesCount > 0) return;
    const reqSeq = ++coloniesReqSeq;
    inFlightColoniesCount += 1;
    try {
      const data = await rawApiCommand({ command: "colony_candidates", limit: 1000 });
      if (reqSeq !== coloniesReqSeq) return;
      if (data && typeof data === "object") setColonyData(data);
    } catch (err: any) {
      if (reqSeq !== coloniesReqSeq) return;
      showFeedback(`Colony grading load failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      inFlightColoniesCount = Math.max(0, inFlightColoniesCount - 1);
    }
  };

  const refreshAttacksView = async () => {
    try {
      const data = await rawApiCommand({ command: "attack_results", limit: 200 });
      if (data && typeof data === "object") setAtkData(data);
    } catch (err: any) {
      showFeedback(`Attack results load failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const refreshRecyclersView = async (categoryOverride?: string) => {
    try {
      const cat = categoryOverride ?? store.getSnapshot().uiPrefs.emailCatFilter ?? "all";
      const data = await rawApiCommand({
        command: "recycler_results",
        category: cat,
        limit: 200,
      });
      if (data && typeof data === "object") setRecData(data);
    } catch (err: any) {
      showFeedback(`Recycler and email load failed: ${err.message}`, feedbackKindForError(err));
    }
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
      const dash = await rawApiGet<DashboardPayload>(
        `/dashboard?refresh=${refresh ? 1 : 0}`,
        15000
      );
      if (reqSeq !== store.getRuntimeSnapshot().dashboardSeq) return false;
      if (applyVersionSnapshot(dash)) return false;
      setApiStatus(true);

      const rtNow = store.getRuntimeSnapshot();
      const prevSnap = store.getSnapshot();
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
        (prev) => {
          const nextPrefs =
            prev.uiPrefs.modeOverride === modeVal
              ? prev.uiPrefs
              : { ...prev.uiPrefs, modeOverride: modeVal };

          const nextBuildingsEta =
            nextDash.buildings_eta &&
            (!prev.selectedPlanetId ||
              prev.selectedPlanetId === nextDash.buildings_eta.planet?.planet_id)
              ? nextDash.buildings_eta
              : prev.buildingsEtaData;

          const nextCombat =
            nextDash.combat &&
            (!prev.selectedPlanetId || prev.selectedPlanetId === nextDash.combat.planet_id)
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
        void refreshSectorsView(undefined, undefined, undefined, { background: true });
      } else if (curPage === "espionage") {
        void refreshEspionageView();
      } else if (curPage === "colonies") {
        void refreshColoniesView({ background: true });
      } else if (curPage === "attacks") {
        void refreshAttacksView();
      } else if (curPage === "recyclers") {
        void refreshRecyclersView();
      } else if (curPage === "buildings" || (curPage as string) === "research") {
        if (
          !nextDash.buildings_eta ||
          (afterSnap.selectedPlanetId &&
            afterSnap.selectedPlanetId !== nextDash.buildings_eta.planet?.planet_id)
        ) {
          void refreshBuildingsEtaForSelectedPlanet(false);
        }
      } else if (curPage === "combat") {
        if (
          !nextDash.combat ||
          (afterSnap.selectedPlanetId &&
            afterSnap.selectedPlanetId !== nextDash.combat.planet_id)
        ) {
          void refreshCombatForSelectedPlanet({ refresh: false });
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
      if (wasOffline) {
        await loadDashboard({ refresh: false, preserveConfig: true });
      }
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
      void refreshBuildingsEtaForSelectedPlanet();
    } else if (target === "combat") {
      void refreshCombatForSelectedPlanet({ refresh: false });
    } else if ((target as string) === "fleet-control") {
      if (!opts.disableInitialFetch) {
        void loadDashboard({ refresh: false, preserveConfig: true });
      }
    } else if (target === "sectors") {
      void refreshSectorsView();
    } else if (target === "espionage") {
      void refreshEspionageView();
    } else if (target === "colonies") {
      void refreshColoniesView();
    } else if (target === "attacks") {
      void refreshAttacksView();
    } else if (target === "recyclers") {
      void refreshRecyclersView();
    } else if (target === "action-graph") {
      const snap = store.getSnapshot();
      if (!snap.actionGraphData) {
        if (snap.dashboard?.action_graph) {
          setActionGraphData(snap.dashboard.action_graph);
        } else if (snap.dashboard) {
          setActionGraphData(buildClientFallbackActionGraph(snap.dashboard));
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
      void refreshBuildingsEtaForSelectedPlanet();
      if (snap.activePage === "combat") {
        void refreshCombatForSelectedPlanet({ refresh: false });
      } else if (snap.activePage === "sectors") {
        void refreshSectorsView();
      } else if (snap.activePage === "colonies") {
        void refreshColoniesView();
      }
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
    store.setRuntime({
      notifCenterOpen: true,
      nowEpochSec: Math.floor(Date.now() / 1000),
    });
    await refreshNotificationCenter();
  };

  const closeNotificationCenter = () => {
    store.setRuntime({ notifCenterOpen: false });
  };

  const closeHelpPopupOnly = () => {
    store.setRuntime({ activeHelpNotif: null });
  };

  const ackActiveHelpPopup = async (targetStatus: "read" | "dismissed") => {
    const activeHelp = store.getRuntimeSnapshot().activeHelpNotif;
    const nid = activeHelp ? safeFiniteNumber(activeHelp.id, null) : null;
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
  };

  const toggleBlastFurnaceRate = async (targetPct: number) => {
    const snap = store.getSnapshot();
    const planet = selectActivePlanet(snap.dashboard, snap.selectedPlanetId);
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
  };

  const syncAllInboxFolders = async () => {
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
      const curPage = store.getSnapshot().activePage;
      if (curPage === "espionage") await refreshEspionageView();
      else if (curPage === "colonies") await refreshColoniesView();
      else if (curPage === "attacks") await refreshAttacksView();
      else if (curPage === "recyclers") await refreshRecyclersView();
    } catch (err: any) {
      showFeedback(`Inbox sync failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const enqueueManualCommand = async (
    payload: Record<string, any>
  ): Promise<Record<string, any> | null> => {
    try {
      const res = await rawApiCommand({ command: "queue_add", ...payload });
      showFeedback(res.message || "Command pinned to priority queue.", "ok", {
        autoHideMs: 4200,
      });
      await loadDashboard({ refresh: false, force: true });
      return res;
    } catch (err: any) {
      showFeedback(`Queue add failed: ${err.message}`, feedbackKindForError(err));
      return null;
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
    setBuildingsEtaData,
    refreshBuildingsEtaForSelectedPlanet,
    setCombatData,
    refreshCombatForSelectedPlanet,
    setSectorsData,
    refreshSectorsView,
    setEspData,
    refreshEspionageView,
    setColonyData,
    refreshColoniesView,
    setAtkData,
    refreshAttacksView,
    setRecData,
    refreshRecyclersView,
    setActionGraphData,
    setPlannerJobsStatus,
    refreshPlannerJobsStatus,
    setAgSimDirty,
    setAgSimRunning,
    setConfigDirty,
    setConfigSaving,
    setLastOptimizerSummary,
    syncNotificationsPayload,
    openNotificationCenter,
    closeNotificationCenter,
    refreshNotificationCenter,
    ackActiveHelpPopup,
    closeHelpPopupOnly,
    toggleBlastFurnaceRate,
    syncAllInboxFolders,
    enqueueManualCommand,
    cleanup,
  };
}
