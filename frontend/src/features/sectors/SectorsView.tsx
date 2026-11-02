import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import {
  useActivePage,
  useActivePlanet,
  useConsoleActions,
  useDashboard,
  useSectorsData,
  useUiPrefs,
} from "../../store";
import type {
  ScanQueueItem,
  ScannedSectorItem,
  ScannedSectorsSummary,
} from "../../types/api";
import { finiteNum } from "../../utils/formatters";
import { SectorsAllTable } from "./SectorsAllTable";
import { buildGalaxyStats } from "./sectorsHelpers";
import { SectorsKpiStrip } from "./SectorsKpiStrip";
import { SectorsQueueTable } from "./SectorsQueueTable";

export { formatSectorDistance, formatShortAge } from "./sectorsHelpers";

export interface SectorsViewProps {
  hidden?: boolean;
}

export const SectorsView: React.FC<SectorsViewProps> = ({ hidden }) => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const activePlanet = useActivePlanet();
  const sectorsData = useSectorsData();
  const uiPrefs = useUiPrefs();
  const {
    refreshSectorsView,
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
  } = useConsoleActions();

  const isHidden = hidden ?? activePage !== "sectors";
  const statusFilter = uiPrefs.sectorFilter || "all";
  const galaxyFilter = Math.max(0, Math.round(finiteNum(uiPrefs.sectorGalaxy, 0)));

  const summary: ScannedSectorsSummary =
    sectorsData?.summary && typeof sectorsData.summary === "object"
      ? (sectorsData.summary as ScannedSectorsSummary)
      : {};

  const [refreshPctInput, setRefreshPctInput] = useState<string>("0");
  const [discoveryPctInput, setDiscoveryPctInput] = useState<string>("100");
  const [sweepPagesInput, setSweepPagesInput] = useState<string>("100");
  const [allGalaxiesInput, setAllGalaxiesInput] = useState<boolean>(false);
  const [splitDirty, setSplitDirty] = useState<boolean>(false);
  const [previewPending, setPreviewPending] = useState<boolean>(false);
  const [savingSplit, setSavingSplit] = useState<boolean>(false);
  const [sweeping, setSweeping] = useState<boolean>(false);
  const [refreshingSector, setRefreshingSector] = useState<string | null>(null);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isPreviewPayload = Boolean(sectorsData?.preview);
  const savedRefreshPct = Math.max(
    0,
    Math.min(
      100,
      Math.round(
        finiteNum(
          summary.saved_refresh_pct ??
            (!isPreviewPayload ? summary.effective_refresh_pct : undefined) ??
            dashboard?.config?.galaxy_scan_refresh_pct ??
            (dashboard?.config?.galaxy_scan_explore_first === false ? 100 : 0),
          0
        )
      )
    )
  );
  const savedSweepPages = Math.max(
    1,
    Math.min(
      400,
      Math.round(
        finiteNum(
          summary.saved_sweep_pages ??
            (!isPreviewPayload ? summary.sweep_pages : undefined) ??
            dashboard?.config?.galaxy_sweep_pages,
          100
        )
      )
    )
  );
  const savedAllGalaxies = Boolean(
    summary.saved_scan_all_galaxies ??
      (!isPreviewPayload ? summary.scan_all_galaxies : undefined) ??
      dashboard?.config?.galaxy_scan_all_galaxies ??
      false
  );

  useEffect(() => {
    if (splitDirty || savingSplit) return;
    setRefreshPctInput(String(savedRefreshPct));
    setDiscoveryPctInput(String(100 - savedRefreshPct));
    setSweepPagesInput(String(savedSweepPages));
    setAllGalaxiesInput(savedAllGalaxies);
  }, [savedRefreshPct, savedSweepPages, savedAllGalaxies, splitDirty, savingSplit]);

  useEffect(() => {
    return () => {
      if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    };
  }, []);

  const parsedRefreshPct = Math.max(
    0,
    Math.min(100, Math.round(finiteNum(refreshPctInput, savedRefreshPct)))
  );
  const parsedDiscoveryPct = 100 - parsedRefreshPct;
  const parsedSweepPages = Math.max(
    1,
    Math.min(400, Math.round(finiteNum(sweepPagesInput, savedSweepPages)))
  );
  const previewRefreshReq = Math.round((parsedSweepPages * parsedRefreshPct) / 100);
  const previewDiscoveryReq = Math.max(0, parsedSweepPages - previewRefreshReq);
  const previewModeLabel =
    parsedRefreshPct <= 0
      ? "EXPLORATORY (100% DISCOVERY)"
      : parsedRefreshPct >= 100
        ? "REFRESH ONLY (100% REFRESH)"
        : `MIXED (${parsedRefreshPct}% REFRESH / ${parsedDiscoveryPct}% DISCOVERY)`;

  const scheduleQueuePreview = useCallback(
    (
      nextRefreshPct: number,
      nextSweepPages: number,
      nextAllGalaxies: boolean,
      immediate = false
    ) => {
      if (previewTimerRef.current) {
        clearTimeout(previewTimerRef.current);
        previewTimerRef.current = null;
      }
      const matchesSaved =
        nextRefreshPct === savedRefreshPct &&
        nextSweepPages === savedSweepPages &&
        nextAllGalaxies === savedAllGalaxies;
      setSplitDirty(!matchesSaved);
      const previewArg = matchesSaved
        ? null
        : {
            refresh_pct: nextRefreshPct,
            sweep_pages: nextSweepPages,
            all_galaxies: nextAllGalaxies,
          };
      const incUnscanned = galaxyFilter > 0 || statusFilter === "unscanned";
      const runPreview = () => {
        setPreviewPending(true);
        void refreshSectorsView(galaxyFilter, incUnscanned, previewArg).finally(() => {
          setPreviewPending(false);
        });
      };
      if (immediate) {
        runPreview();
      } else {
        setPreviewPending(true);
        previewTimerRef.current = setTimeout(() => {
          previewTimerRef.current = null;
          runPreview();
        }, 150);
      }
    },
    [
      galaxyFilter,
      refreshSectorsView,
      savedAllGalaxies,
      savedRefreshPct,
      savedSweepPages,
      statusFilter,
    ]
  );

  const handleRefreshPctChange = (raw: string) => {
    setRefreshPctInput(raw);
    setSplitDirty(true);
    const v = raw.trim() === "" ? null : finiteNum(raw, null);
    if (v !== null) {
      const clamped = Math.max(0, Math.min(100, Math.round(v)));
      setDiscoveryPctInput(String(100 - clamped));
      scheduleQueuePreview(clamped, parsedSweepPages, allGalaxiesInput, false);
    }
  };

  const handleDiscoveryPctChange = (raw: string) => {
    setDiscoveryPctInput(raw);
    setSplitDirty(true);
    const v = raw.trim() === "" ? null : finiteNum(raw, null);
    if (v !== null) {
      const clampedDisc = Math.max(0, Math.min(100, Math.round(v)));
      const nextRef = 100 - clampedDisc;
      setRefreshPctInput(String(nextRef));
      scheduleQueuePreview(nextRef, parsedSweepPages, allGalaxiesInput, false);
    }
  };

  const handleSweepPagesChange = (raw: string) => {
    setSweepPagesInput(raw);
    setSplitDirty(true);
    const v = raw.trim() === "" ? null : finiteNum(raw, null);
    if (v !== null && v >= 1) {
      const clampedPages = Math.max(1, Math.min(400, Math.round(v)));
      scheduleQueuePreview(parsedRefreshPct, clampedPages, allGalaxiesInput, false);
    }
  };

  const handleAllGalaxiesToggle = (checked: boolean) => {
    setAllGalaxiesInput(checked);
    scheduleQueuePreview(parsedRefreshPct, parsedSweepPages, checked, true);
  };

  const allSectors: ScannedSectorItem[] = useMemo(
    () =>
      Array.isArray(sectorsData?.sectors)
        ? (sectorsData!.sectors as ScannedSectorItem[]).filter((s) => s && typeof s === "object")
        : [],
    [sectorsData]
  );

  const scanQueue: ScanQueueItem[] = useMemo(
    () =>
      Array.isArray(sectorsData?.scan_queue)
        ? (sectorsData!.scan_queue as ScanQueueItem[]).filter((q) => q && typeof q === "object")
        : [],
    [sectorsData]
  );

  const galaxyStats = useMemo(
    () => buildGalaxyStats(sectorsData, allSectors, scanQueue),
    [allSectors, scanQueue, sectorsData]
  );

  const handleSelectGalaxy = (g: number) => {
    setUiPref("sectorGalaxy", g);
    refreshSectorsView(g, g > 0 || statusFilter === "unscanned");
  };

  const handleSelectStatusFilter = (key: string) => {
    setUiPref("sectorFilter", key);
    if (key === "unscanned" && !sectorsData?.include_unscanned) {
      refreshSectorsView(galaxyFilter, true);
    }
  };

  const handleSaveSplit = async () => {
    if (previewTimerRef.current) {
      clearTimeout(previewTimerRef.current);
      previewTimerRef.current = null;
    }
    const rVal = Math.max(0, Math.min(100, Math.round(finiteNum(refreshPctInput, savedRefreshPct))));
    const pVal = Math.max(1, Math.min(400, Math.round(finiteNum(sweepPagesInput, savedSweepPages))));
    setSavingSplit(true);
    try {
      await apiCommand({
        command: "update_config",
        config: {
          galaxy_scan_refresh_pct: rVal,
          galaxy_sweep_pages: pVal,
          galaxy_scan_explore_first: rVal < 100,
          galaxy_scan_all_galaxies: allGalaxiesInput,
        },
      });
      setRefreshPctInput(String(rVal));
      setDiscoveryPctInput(String(100 - rVal));
      setSweepPagesInput(String(pVal));
      setSplitDirty(false);
      setPreviewPending(false);
      const refReq = Math.round((pVal * rVal) / 100);
      const discReq = Math.max(0, pVal - refReq);
      const scopeLabel = allGalaxiesInput ? "All 14 Galaxies (G1-G14)" : "Owned Galaxies (+spillover)";
      showFeedback(
        `Sweep split saved: ${rVal}% Refresh (${refReq} req) / ${100 - rVal}% Discovery (${discReq} req) across ${pVal} req/sweep · Scope: ${scopeLabel}.`,
        "ok"
      );
      await refreshSectorsView(galaxyFilter, galaxyFilter > 0 || statusFilter === "unscanned", null);
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err: any) {
      showFeedback(`Failed saving sweep split: ${err.message}`, feedbackKindForError(err));
    } finally {
      setSavingSplit(false);
    }
  };

  const handleRunSweep = async () => {
    if (previewTimerRef.current) {
      clearTimeout(previewTimerRef.current);
      previewTimerRef.current = null;
    }
    const rVal = Math.max(0, Math.min(100, Math.round(finiteNum(refreshPctInput, savedRefreshPct))));
    const pVal = Math.max(1, Math.min(400, Math.round(finiteNum(sweepPagesInput, savedSweepPages))));
    setSweeping(true);
    try {
      if (splitDirty) {
        await apiCommand({
          command: "update_config",
          config: {
            galaxy_scan_refresh_pct: rVal,
            galaxy_sweep_pages: pVal,
            galaxy_scan_explore_first: rVal < 100,
            galaxy_scan_all_galaxies: allGalaxiesInput,
          },
        });
        setSplitDirty(false);
        setPreviewPending(false);
      }
      showFeedback(
        `Starting smart galaxy sweep (${pVal} pages · ${rVal}% Refresh / ${100 - rVal}% Discovery)...`,
        "ok"
      );
      const res = await apiCommand({
        command: "galaxy_sweep",
        pages: pVal,
        dry_run: false,
      });
      showFeedback(res.message || "Galaxy sweep completed.", res.ok === false ? "warn" : "ok");
      await refreshSectorsView(galaxyFilter, galaxyFilter > 0 || statusFilter === "unscanned", null);
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Galaxy sweep failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setSweeping(false);
    }
  };

  const handleScanSector = async (sectorLabel: string, galaxy: number, system: number) => {
    setRefreshingSector(sectorLabel);
    try {
      showFeedback(`Scanning galaxy sector [${sectorLabel}]...`, "ok");
      const res = await apiCommand({
        command: "scan_galaxy",
        galaxy,
        system,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Sector [${sectorLabel}] scanned.`,
        res.executed === false ? "warn" : "ok"
      );
      await loadDashboard({ refresh: false, force: true });
      await refreshSectorsView(galaxyFilter, galaxyFilter > 0 || statusFilter === "unscanned");
    } catch (err: any) {
      showFeedback(`Sector [${sectorLabel}] scan failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setRefreshingSector(null);
    }
  };

  const sweepMeta = dashboard?.galaxy_sweep;
  const galCovPct = Math.max(
    0,
    Math.min(100, finiteNum(summary.coverage_pct ?? sweepMeta?.coverage_pct, 0))
  );
  const galExplored = Math.round(
    finiteNum(summary.explored_systems ?? sweepMeta?.explored_systems, allSectors.length)
  );
  const galTotal = Math.max(
    1,
    Math.round(finiteNum(summary.total_systems ?? sweepMeta?.total_systems, 400))
  );
  const uniExplored = Math.round(
    finiteNum(
      summary.universe_explored_systems ?? sweepMeta?.universe_explored_systems,
      galExplored
    )
  );
  const uniTotal = Math.max(
    1,
    Math.round(finiteNum(summary.universe_total_systems ?? sweepMeta?.universe_total_systems, 5600))
  );
  const uniCovPct = Math.max(
    0,
    Math.min(
      100,
      finiteNum(
        summary.universe_coverage_pct ?? sweepMeta?.universe_coverage_pct,
        (uniExplored / uniTotal) * 100
      )
    )
  );
  const totalScannedAll = Math.round(
    finiteNum(summary.total_scanned_all_galaxies ?? summary.scanned_sectors, allSectors.length)
  );
  const queueDiscCount = Math.round(
    finiteNum(
      summary.queue_discovery_count,
      scanQueue.filter((q) => q.phase === "exploratory").length
    )
  );
  const queueRefCount = Math.round(
    finiteNum(
      summary.queue_refresh_count,
      scanQueue.filter((q) => q.phase !== "exploratory").length
    )
  );
  const originLabel = sectorsData?.origin_coords || activePlanet?.coords || "--";
  const queueScopeLabel =
    Array.isArray(summary.queue_galaxies) && summary.queue_galaxies.length >= 14
      ? "G1–G14"
      : Array.isArray(summary.queue_galaxies) && summary.queue_galaxies.length > 0
        ? summary.queue_galaxies.map((g) => `G${g}`).join(", ")
        : allGalaxiesInput
          ? "G1–G14"
          : "Owned";
  const summaryMatchesInputs =
    sectorsData?.summary != null &&
    Math.round(finiteNum(summary.effective_refresh_pct ?? summary.refresh_pct, -1)) ===
      parsedRefreshPct &&
    Math.round(finiteNum(summary.sweep_pages, -1)) === parsedSweepPages &&
    Boolean(summary.scan_all_galaxies) === allGalaxiesInput;
  const isShowingPreview = splitDirty || isPreviewPayload;
  const ownedFullNoAllGals =
    summaryMatchesInputs &&
    parsedDiscoveryPct > 0 &&
    queueDiscCount === 0 &&
    !allGalaxiesInput &&
    galCovPct >= 100;
  const splitReadoutText =
    !summaryMatchesInputs || previewPending
      ? `Updating queue: ${previewRefreshReq} Refresh · ${previewDiscoveryReq} Discovery (${previewModeLabel}) · Scope: ${allGalaxiesInput ? "G1–G14" : queueScopeLabel}`
      : ownedFullNoAllGals
        ? `${isShowingPreview ? "Preview (unsaved)" : "Actual queue"}: ${queueRefCount} Refresh · 0 Discovery (Scope ${queueScopeLabel} is 100% scanned — enable "All 14 Galaxies" to discover G1–G14)`
        : `${isShowingPreview ? "Preview (unsaved)" : "Actual queue"}: ${queueRefCount} Refresh · ${queueDiscCount} Discovery (${previewModeLabel}) · Scope: ${queueScopeLabel}`;

  return (
    <section
      id="view-sectors"
      className="page-view"
      aria-label="All-galaxy sectors, upcoming scan queue, and refresh vs discovery split"
      hidden={isHidden}
    >
      <SectorsKpiStrip
        summary={summary}
        totalScannedAll={totalScannedAll}
        galCovPct={galCovPct}
        galExplored={galExplored}
        galTotal={galTotal}
        uniCovPct={uniCovPct}
        uniExplored={uniExplored}
        uniTotal={uniTotal}
        splitCardProps={{
          refreshPctInput,
          discoveryPctInput,
          sweepPagesInput,
          allGalaxiesInput,
          parsedRefreshPct,
          parsedDiscoveryPct,
          parsedSweepPages,
          splitDirty,
          savingSplit,
          sweeping,
          splitReadoutText,
          onRefreshPctChange: handleRefreshPctChange,
          onDiscoveryPctChange: handleDiscoveryPctChange,
          onSweepPagesChange: handleSweepPagesChange,
          onAllGalaxiesToggle: handleAllGalaxiesToggle,
          onRefreshPctBlur: () => {
            if (refreshPctInput.trim() === "") {
              setRefreshPctInput(String(parsedRefreshPct));
              setDiscoveryPctInput(String(100 - parsedRefreshPct));
            }
          },
          onDiscoveryPctBlur: () => {
            if (discoveryPctInput.trim() === "") {
              setDiscoveryPctInput(String(parsedDiscoveryPct));
              setRefreshPctInput(String(parsedRefreshPct));
            }
          },
          onSweepPagesBlur: () => {
            if (sweepPagesInput.trim() === "") {
              setSweepPagesInput(String(parsedSweepPages));
            }
          },
          onSaveSplit: handleSaveSplit,
          onRunSweep: handleRunSweep,
        }}
      />

      <SectorsQueueTable
        scanQueue={scanQueue}
        queueDiscCount={queueDiscCount}
        queueRefCount={queueRefCount}
        queueScopeLabel={queueScopeLabel}
        previewPending={previewPending}
        isShowingPreview={isShowingPreview}
        refreshingSector={refreshingSector}
        onScanSector={handleScanSector}
      />

      <SectorsAllTable
        sectorsData={sectorsData}
        allSectors={allSectors}
        galaxyStats={galaxyStats}
        galaxyFilter={galaxyFilter}
        statusFilter={statusFilter}
        totalScannedAll={totalScannedAll}
        uniTotal={uniTotal}
        originLabel={originLabel}
        refreshingSector={refreshingSector}
        onSelectGalaxy={handleSelectGalaxy}
        onSelectStatusFilter={handleSelectStatusFilter}
        onRefreshDb={() =>
          refreshSectorsView(galaxyFilter, galaxyFilter > 0 || statusFilter === "unscanned")
        }
        onScanSector={handleScanSector}
      />
    </section>
  );
};

export default SectorsView;
