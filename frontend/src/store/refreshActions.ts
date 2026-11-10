import {
  apiCommand as rawApiCommand,
  feedbackKindForError,
  isRateLimitError,
  safeFiniteNumber,
} from "../api/httpClient";
import type { FeedbackKind, PlannerJobsStatusPayload } from "../types/api";
import { selectActivePlanet } from "./fallbackActionGraph";
import type { ConsoleStore, SectorQueuePreviewOverrides } from "./types";

export interface ViewRefreshActions {
  refreshPlannerJobsStatus: () => Promise<PlannerJobsStatusPayload | null>;
  refreshBuildingsEtaForSelectedPlanet: (forceRefresh?: boolean) => Promise<void>;
  refreshCombatForSelectedPlanet: (opts?: { refresh?: boolean }) => Promise<boolean>;
  refreshSectorsView: (
    galaxyOverride?: number,
    includeUnscannedOverride?: boolean,
    previewOverride?: SectorQueuePreviewOverrides | null,
    refreshOpts?: { background?: boolean }
  ) => Promise<void>;
  refreshEspionageView: () => Promise<void>;
  refreshColoniesView: (refreshOpts?: { background?: boolean }) => Promise<void>;
  refreshAttacksView: () => Promise<void>;
  refreshRecyclersView: (categoryOverride?: string) => Promise<void>;
  toggleBlastFurnaceRate: (targetPct: number) => Promise<void>;
  syncAllInboxFolders: () => Promise<void>;
  enqueueManualCommand: (payload: Record<string, any>) => Promise<Record<string, any> | null>;
}

export function createViewRefreshActions(
  store: ConsoleStore,
  showFeedback: (message: string, kind?: FeedbackKind, opts?: { autoHideMs?: number }) => void,
  loadDashboard: (opts?: {
    refresh?: boolean;
    preserveConfig?: boolean;
    force?: boolean;
  }) => Promise<boolean>
): ViewRefreshActions {
  let sectorsReqSeq = 0;
  let inFlightSectorsCount = 0;
  let coloniesReqSeq = 0;
  let inFlightColoniesCount = 0;
  let inFlightBuildingsEta: { key: string; promise: Promise<void> } | null = null;
  let inFlightCombat: { key: string; promise: Promise<boolean> } | null = null;
  let sectorPreview: SectorQueuePreviewOverrides | null = null;

  const refreshPlannerJobsStatus = async (): Promise<PlannerJobsStatusPayload | null> => {
    try {
      const res = await rawApiCommand<PlannerJobsStatusPayload>({
        command: "planner_jobs_status",
      });
      if (res && typeof res === "object") {
        store.setPlannerJobsStatus(res);
        return res;
      }
    } catch {
      // Tolerate older servers or offline fallback
    }
    return null;
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
        if (res.buildings_eta) store.setBuildingsEtaData(res.buildings_eta);
      } catch {
        // Non-fatal during offline fallback
      } finally {
        if (inFlightBuildingsEta?.promise === reqPromise) inFlightBuildingsEta = null;
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
        if (res.combat) store.setCombatData(res.combat);
        return true;
      } catch (err: any) {
        if (refresh || isRateLimitError(err)) {
          showFeedback(`Shipyard refresh failed: ${err.message}`, feedbackKindForError(err));
        }
        return false;
      } finally {
        if (inFlightCombat?.promise === reqPromise) inFlightCombat = null;
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
    if (previewOverride !== undefined) sectorPreview = previewOverride;
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
        store.setSectorsData(data);
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
      if (data && typeof data === "object") store.setEspData(data);
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
      if (data && typeof data === "object") store.setColonyData(data);
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
      if (data && typeof data === "object") store.setAtkData(data);
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
      if (data && typeof data === "object") store.setRecData(data);
    } catch (err: any) {
      showFeedback(`Recycler and email load failed: ${err.message}`, feedbackKindForError(err));
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
      showFeedback(res.message || "Command pinned to priority queue.", "ok", { autoHideMs: 4200 });
      await loadDashboard({ refresh: false, force: true });
      return res;
    } catch (err: any) {
      showFeedback(`Queue add failed: ${err.message}`, feedbackKindForError(err));
      return null;
    }
  };

  return {
    refreshPlannerJobsStatus,
    refreshBuildingsEtaForSelectedPlanet,
    refreshCombatForSelectedPlanet,
    refreshSectorsView,
    refreshEspionageView,
    refreshColoniesView,
    refreshAttacksView,
    refreshRecyclersView,
    toggleBlastFurnaceRate,
    syncAllInboxFolders,
    enqueueManualCommand,
  };
}
