import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type {
  GalaxyStatsItem,
  ScanQueueItem,
  ScannedSectorItem,
  ScannedSectorsSummary,
} from "../../types/api";
import { finiteNum, fmtInt, fmtShortDateTime } from "../../utils/formatters";

export interface SectorsViewProps {
  hidden?: boolean;
}

const ALL_GALAXIES: readonly number[] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14];

export function formatShortAge(ageSeconds: unknown): string {
  const sec = finiteNum(ageSeconds, null);
  if (sec === null || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  if (h < 24) return remM > 0 ? `${h}h ${remM}m ago` : `${h}h ago`;
  const d = Math.floor(h / 24);
  const remH = h % 24;
  return remH > 0 ? `${d}d ${remH}h ago` : `${d}d ago`;
}

export function formatSectorDistance(row: {
  system_distance?: number | null;
  galaxy_delta?: number | null;
  system_delta?: number | null;
  circular_distance?: number | null;
}): { label: string; title: string } {
  const sysDist = finiteNum(row.system_distance, null);
  const gDiff = finiteNum(row.galaxy_delta, null);
  const sDiff = finiteNum(row.system_delta, null);
  const circ = finiteNum(row.circular_distance, null);

  if (sysDist === null && gDiff === null && sDiff === null) {
    return { label: "—", title: "Distance unavailable (no active origin colony)" };
  }
  const gInt = Math.max(0, Math.round(gDiff ?? 0));
  const sInt = Math.max(0, Math.round(sDiff ?? Math.max(0, (sysDist ?? 0) - gInt * 400)));

  if (gInt === 0 && sInt === 0) {
    return { label: "Same sys", title: "Same solar system as active colony" };
  }
  if (gInt === 0) {
    const circNote = circ !== null && circ !== sInt ? ` (wrap ${Math.round(circ)} sys)` : "";
    return {
      label: `${fmtInt(sInt)} sys`,
      title: `Same galaxy · ${fmtInt(sInt)} systems away${circNote}`,
    };
  }
  return {
    label: `${gInt}G · ${fmtInt(sInt)} sys`,
    title: `${gInt} galaxy jump(s), ${fmtInt(sInt)} systems delta`,
  };
}

function isSectorRowScanned(sec: ScannedSectorItem): boolean {
  if (typeof sec.scanned === "boolean") return sec.scanned;
  return finiteNum(sec.last_scanned_ts, 0) > 0;
}

export const SectorsView: React.FC<SectorsViewProps> = ({ hidden }) => {
  const {
    dashboard,
    activePage,
    activePlanet,
    sectorsData,
    refreshSectorsView,
    showFeedback,
    apiCommand,
    loadDashboard,
    uiPrefs,
    setUiPref,
  } = useConsole();

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
  const [queuePhaseFilter, setQueuePhaseFilter] = useState<"all" | "exploratory" | "targeted_refresh">("all");
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

  const galaxyStats: GalaxyStatsItem[] = useMemo(() => {
    if (Array.isArray(sectorsData?.galaxy_stats) && sectorsData!.galaxy_stats.length > 0) {
      const byGal = new Map<number, GalaxyStatsItem>();
      (sectorsData!.galaxy_stats as GalaxyStatsItem[]).forEach((st) => {
        const gi = Math.round(finiteNum(st?.galaxy, 0));
        if (gi >= 1 && gi <= 14) byGal.set(gi, st);
      });
      return ALL_GALAXIES.map(
        (g) =>
          byGal.get(g) || {
            galaxy: g,
            is_owned: false,
            in_sweep_scope: false,
            scanned_count: 0,
            unscanned_count: 400,
            total_systems: 400,
            coverage_pct: 0,
            occupied_count: 0,
            debris_count: 0,
            target_count: 0,
            prime_free_count: 0,
            queued_count: 0,
          }
      );
    }

    const scannedByGal = new Map<number, number>();
    const queuedByGal = new Map<number, number>();
    allSectors.forEach((s) => {
      const gi = Math.round(finiteNum(s.galaxy, 0));
      if (gi >= 1 && gi <= 14 && isSectorRowScanned(s)) {
        scannedByGal.set(gi, (scannedByGal.get(gi) ?? 0) + 1);
      }
    });
    scanQueue.forEach((q) => {
      const gi = Math.round(finiteNum(q.galaxy, 0));
      if (gi >= 1 && gi <= 14) {
        queuedByGal.set(gi, (queuedByGal.get(gi) ?? 0) + 1);
      }
    });
    return ALL_GALAXIES.map((g) => {
      const sc = scannedByGal.get(g) ?? 0;
      return {
        galaxy: g,
        is_owned: false,
        in_sweep_scope: false,
        scanned_count: sc,
        unscanned_count: Math.max(0, 400 - sc),
        total_systems: 400,
        coverage_pct: Math.round((sc / 400) * 10000) / 100,
        occupied_count: 0,
        debris_count: 0,
        target_count: 0,
        prime_free_count: 0,
        queued_count: queuedByGal.get(g) ?? 0,
      };
    });
  }, [allSectors, scanQueue, sectorsData]);

  const queueGalBreakdown = useMemo(() => {
    const counts = new Map<number, number>();
    const order: number[] = [];
    scanQueue.forEach((q) => {
      const gi = Math.round(finiteNum(q.galaxy, 0));
      if (gi >= 1 && gi <= 14) {
        if (!counts.has(gi)) order.push(gi);
        counts.set(gi, (counts.get(gi) ?? 0) + 1);
      }
    });
    return order.map((g) => ({ galaxy: g, count: counts.get(g) ?? 0 }));
  }, [scanQueue]);

  const filteredQueue = useMemo(() => {
    return scanQueue.filter((q) => {
      if (queuePhaseFilter === "exploratory") return q.phase === "exploratory";
      if (queuePhaseFilter === "targeted_refresh") return q.phase !== "exploratory";
      return true;
    });
  }, [scanQueue, queuePhaseFilter]);

  const queueSortCols = useMemo<
    SortColumnMap<
      ScanQueueItem,
      "queuePos" | "phase" | "sector" | "distance" | "status" | "score" | "reason"
    >
  >(
    () => ({
      queuePos: (q) => finiteNum(q.queue_pos, 0),
      phase: (q) => (q.phase === "exploratory" ? "0_discovery" : "1_refresh"),
      sector: (q) => Math.round(finiteNum(q.galaxy, 0)) * 1000 + Math.round(finiteNum(q.system, 0)),
      distance: (q) => finiteNum(q.system_distance, null),
      status: (q) => (q.scanned ? finiteNum(q.age_seconds, 0) : Number.POSITIVE_INFINITY),
      score: {
        accessor: (q) => finiteNum(q.score, 0),
        defaultDirection: "desc",
      },
      reason: (q) => String(q.reason || ""),
    }),
    []
  );

  const queueSort = useTableSort(filteredQueue, queueSortCols);

  const filteredSectors = useMemo(() => {
    return allSectors.filter((s) => {
      if (galaxyFilter > 0 && Math.round(finiteNum(s.galaxy, 0)) !== galaxyFilter) {
        return false;
      }
      const scanned = isSectorRowScanned(s);
      if (statusFilter === "scanned") return scanned;
      if (statusFilter === "unscanned") return !scanned;
      if (statusFilter === "queued") return finiteNum(s.queue_pos, 0) > 0;
      if (statusFilter === "debris") return finiteNum(s.debris_count, 0) > 0;
      if (statusFilter === "targets") return finiteNum(s.target_count, 0) > 0;
      if (statusFilter === "colony") return finiteNum(s.prime_colony_free_count, 0) > 0;
      if (statusFilter === "stale") return scanned && finiteNum(s.age_seconds, 0) >= 3600;
      return true;
    });
  }, [allSectors, galaxyFilter, statusFilter]);

  const sectorSortCols = useMemo<
    SortColumnMap<
      ScannedSectorItem,
      | "sector"
      | "distance"
      | "occupied"
      | "debris"
      | "targets"
      | "free"
      | "raidsScore"
      | "lastRefresh"
    >
  >(
    () => ({
      sector: (s) => Math.round(finiteNum(s.galaxy, 0)) * 1000 + Math.round(finiteNum(s.system, 0)),
      distance: (s) => finiteNum(s.system_distance, null),
      occupied: {
        accessor: (s) => finiteNum(s.occupied_count, 0),
        defaultDirection: "desc",
      },
      debris: {
        accessor: (s) => finiteNum(s.debris_count, 0),
        defaultDirection: "desc",
      },
      targets: {
        accessor: (s) => finiteNum(s.target_count, 0),
        defaultDirection: "desc",
      },
      free: {
        accessor: (s) =>
          Math.round(finiteNum(s.prime_colony_free_count, 0)) * 100 +
          Math.round(finiteNum(s.free_count, 0)),
        defaultDirection: "desc",
      },
      raidsScore: {
        accessor: (s) => finiteNum(s.score, 0),
        defaultDirection: "desc",
      },
      lastRefresh: {
        accessor: (s) => finiteNum(s.last_scanned_ts, 0),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const sectorSort = useTableSort(filteredSectors, sectorSortCols);

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
      <div className="combat-top-cards sectors-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Scanned Sectors &amp; Coverage</span>
          <strong className="kpi-value tabular" id="sec-kpi-count">
            {`${fmtInt(summary.scanned_sectors ?? totalScannedAll)} Sectors`}
          </strong>
          <span className="kpi-sub tabular" id="sec-kpi-coverage">
            {`SCOPE ${galCovPct.toFixed(1)}% (${fmtInt(galExplored)}/${fmtInt(galTotal)}) · UNI ${uniCovPct.toFixed(1)}% (${fmtInt(uniExplored)}/${fmtInt(uniTotal)})`}
          </span>
        </div>

        <div className="kpi-card">
          <span className="kpi-label tabular">Occupied Planets &amp; Free Slots</span>
          <strong className="kpi-value tabular" id="sec-kpi-occupied">
            {`${fmtInt(summary.total_occupied ?? 0)} Occupied`}
          </strong>
          <span className="kpi-sub tabular" id="sec-kpi-free">
            {`Avg ${finiteNum(summary.avg_occupied, 0).toFixed(1)}/sys · ${fmtInt(summary.total_free ?? 0)} free (${fmtInt(summary.total_prime_free ?? 0)} prime 300+)`}
          </span>
        </div>

        <div className="kpi-card">
          <span className="kpi-label tabular">Debris Fields &amp; Raid Targets</span>
          <strong className="kpi-value tabular" id="sec-kpi-debris">
            {`${fmtInt(summary.total_debris_fields ?? 0)} Debris · ${fmtInt(summary.total_targets ?? 0)} Targets`}
          </strong>
          <span className="kpi-sub tabular" id="sec-kpi-targets-sub">
            {`${fmtInt(summary.sectors_with_debris ?? 0)} sectors w/ debris · ${fmtInt(summary.total_spacia ?? 0)} SPC · ${fmtInt(summary.total_inactives ?? 0)} INA`}
          </span>
        </div>

        <div className="kpi-card sector-split-card">
          <span className="kpi-label tabular">Sweep Request Allocation (Refresh vs Discovery)</span>
          <div className="sector-split-controls">
            <label className="sector-split-field tabular" htmlFor="sec-refresh-pct">
              Refresh %
              <input
                type="number"
                id="sec-refresh-pct"
                min={0}
                max={100}
                step={5}
                value={refreshPctInput}
                onChange={(e) => handleRefreshPctChange(e.target.value)}
                onBlur={() => {
                  if (refreshPctInput.trim() === "") {
                    setRefreshPctInput(String(parsedRefreshPct));
                    setDiscoveryPctInput(String(100 - parsedRefreshPct));
                  }
                }}
              />
            </label>
            <label className="sector-split-field tabular" htmlFor="sec-discovery-pct">
              Discovery %
              <input
                type="number"
                id="sec-discovery-pct"
                min={0}
                max={100}
                step={5}
                value={discoveryPctInput}
                onChange={(e) => handleDiscoveryPctChange(e.target.value)}
                onBlur={() => {
                  if (discoveryPctInput.trim() === "") {
                    setDiscoveryPctInput(String(parsedDiscoveryPct));
                    setRefreshPctInput(String(parsedRefreshPct));
                  }
                }}
              />
            </label>
            <label className="sector-split-field tabular" htmlFor="sec-sweep-pages">
              Req/Sweep
              <input
                type="number"
                id="sec-sweep-pages"
                min={1}
                max={400}
                step={10}
                value={sweepPagesInput}
                onChange={(e) => handleSweepPagesChange(e.target.value)}
                onBlur={() => {
                  if (sweepPagesInput.trim() === "") {
                    setSweepPagesInput(String(parsedSweepPages));
                  }
                }}
              />
            </label>
            <label
              className="sector-all-gals-toggle tabular"
              htmlFor="sec-all-galaxies"
              title="Expand smart sweep discovery and scan queue across all 14 galaxies (G1-G14)"
            >
              <input
                type="checkbox"
                id="sec-all-galaxies"
                checked={allGalaxiesInput}
                onChange={(e) => handleAllGalaxiesToggle(e.target.checked)}
              />
              All 14 Galaxies
            </label>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-sec-save-split"
              disabled={savingSplit}
              onClick={handleSaveSplit}
            >
              {savingSplit ? "Saving..." : splitDirty ? "Apply Split*" : "Apply Split"}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-sec-run-sweep"
              disabled={sweeping}
              onClick={handleRunSweep}
            >
              {sweeping ? "Sweeping..." : "Run Sweep"}
            </button>
          </div>
          <div
            className="sector-split-bar"
            role="img"
            aria-label={`Refresh ${parsedRefreshPct}% vs Discovery ${parsedDiscoveryPct}%`}
          >
            <span className="sector-split-refresh" style={{ width: `${parsedRefreshPct}%` }} />
            <span className="sector-split-discovery" style={{ width: `${parsedDiscoveryPct}%` }} />
          </div>
          <span className="kpi-sub tabular" id="sec-split-readout">
            {splitReadoutText}
          </span>
        </div>
      </div>

      <article className="hud-window sectors-queue-window" id="sectors-queue-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">QUE</span>
            <h2>Upcoming Scan Queue</h2>
            <span className="module-meta tabular" id="sec-queue-meta">
              {`${fmtInt(scanQueue.length)} QUEUED · ${fmtInt(queueDiscCount)} DISCOVERY · ${fmtInt(queueRefCount)} REFRESH · SCOPE ${queueScopeLabel}${previewPending ? " · UPDATING..." : isShowingPreview ? " · PREVIEW (UNSAVED)" : ""}`}
            </span>
          </div>
          <div className="filter-toolbar">
            {queueGalBreakdown.length > 0 ? (
              <div className="sector-queue-gal-pills tabular" aria-label="Queued sectors by galaxy">
                {queueGalBreakdown.map((gb) => (
                  <span key={gb.galaxy} className="sector-queue-badge">
                    {`G${gb.galaxy}: ${gb.count}`}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="filter-chips" role="group" aria-label="Queue phase filter">
              {[
                { key: "all" as const, label: `All (${scanQueue.length})` },
                { key: "exploratory" as const, label: `Discovery (${queueDiscCount})` },
                { key: "targeted_refresh" as const, label: `Refresh (${queueRefCount})` },
              ].map((qf) => (
                <button
                  key={qf.key}
                  type="button"
                  className={`chip-btn${queuePhaseFilter === qf.key ? " active" : ""}`}
                  data-sec-qfilter={qf.key}
                  onClick={() => setQueuePhaseFilter(qf.key)}
                >
                  {qf.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="table-scroll">
          <table className="data-table" id="sectors-queue-table">
            <thead>
              <tr>
                <SortableTh
                  label="#"
                  sortKey="queuePos"
                  activeKey={queueSort.sortKey}
                  direction={queueSort.sortDirection}
                  onSort={queueSort.requestSort}
                />
                <SortableTh
                  label="Phase"
                  sortKey="phase"
                  activeKey={queueSort.sortKey}
                  direction={queueSort.sortDirection}
                  onSort={queueSort.requestSort}
                />
                <SortableTh
                  label="Sector"
                  sortKey="sector"
                  activeKey={queueSort.sortKey}
                  direction={queueSort.sortDirection}
                  onSort={queueSort.requestSort}
                />
                <SortableTh
                  label="Distance"
                  sortKey="distance"
                  activeKey={queueSort.sortKey}
                  direction={queueSort.sortDirection}
                  onSort={queueSort.requestSort}
                />
                <SortableTh
                  label="Current Intel"
                  sortKey="status"
                  activeKey={queueSort.sortKey}
                  direction={queueSort.sortDirection}
                  onSort={queueSort.requestSort}
                />
                <SortableTh
                  label="Score"
                  sortKey="score"
                  activeKey={queueSort.sortKey}
                  direction={queueSort.sortDirection}
                  onSort={queueSort.requestSort}
                />
                <SortableTh
                  label="Smart Scanner Reason"
                  sortKey="reason"
                  activeKey={queueSort.sortKey}
                  direction={queueSort.sortDirection}
                  onSort={queueSort.requestSort}
                />
                <th>Ops</th>
              </tr>
            </thead>
            <tbody id="sectors-queue-tbody">
              {queueSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={8} className="empty-state">
                    {scanQueue.length > 0
                      ? "No queued sectors match the selected phase filter."
                      : "Click Refresh DB to load the smart scanner queue."}
                  </td>
                </tr>
              ) : (
                queueSort.sortedRows.map((qItem) => {
                  const isDisc = qItem.phase === "exploratory";
                  const distFmt = formatSectorDistance(qItem);
                  const isRowBusy = refreshingSector === qItem.sector;
                  const scans = Math.max(0, Math.round(finiteNum(qItem.scan_count, 0)));
                  const occ = Math.max(0, Math.round(finiteNum(qItem.occupied_count, 0)));
                  const deb = Math.max(0, Math.round(finiteNum(qItem.debris_count, 0)));
                  const tgt = Math.max(0, Math.round(finiteNum(qItem.target_count, 0)));

                  return (
                    <tr key={`${qItem.queue_pos}-${qItem.sector}`}>
                      <td className="tabular">
                        <strong>{`#${qItem.queue_pos}`}</strong>
                      </td>
                      <td>
                        <span
                          className={`sector-phase-pill ${isDisc ? "is-discovery" : "is-refresh"}`}
                        >
                          {isDisc ? "DISCOVERY" : "REFRESH"}
                        </span>
                      </td>
                      <td className="tabular">
                        <strong>{qItem.sector}</strong>
                      </td>
                      <td className="tabular" title={distFmt.title}>
                        {distFmt.label}
                      </td>
                      <td className="tabular">
                        {qItem.scanned ? (
                          <>
                            <span>{`${formatShortAge(qItem.age_seconds)} · x${Math.max(1, scans)}`}</span>
                            <span className="sector-subline">
                              {`${occ}/16 occ · ${deb} deb · ${tgt} tgt`}
                            </span>
                          </>
                        ) : (
                          <span className="text-muted">Unscanned</span>
                        )}
                      </td>
                      <td className="tabular">
                        <strong>{finiteNum(qItem.score, 0).toFixed(1)}</strong>
                      </td>
                      <td className="sector-reason-cell">{qItem.reason || "—"}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          disabled={isRowBusy}
                          onClick={() => handleScanSector(qItem.sector, qItem.galaxy, qItem.system)}
                        >
                          {isRowBusy ? "Scanning..." : "Scan Now"}
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </article>

      <article className="hud-window page-fill sectors-all-window" id="sectors-all-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">SEC</span>
            <h2>All-Galaxy Sectors (G1–G14)</h2>
            <span className="module-meta tabular" id="sec-meta-count">
              {`${fmtInt(filteredSectors.length)} / ${fmtInt(allSectors.length)} SHOWN · ${fmtInt(totalScannedAll)} / ${fmtInt(uniTotal)} SCANNED · ORIGIN [${originLabel}]`}
            </span>
          </div>
          <div className="filter-toolbar">
            <div className="filter-chips sector-gal-chips" role="group" aria-label="Galaxy filter">
              <button
                type="button"
                className={`chip-btn${galaxyFilter === 0 ? " active" : ""}`}
                data-sec-gal="0"
                onClick={() => handleSelectGalaxy(0)}
              >
                {`All G (${fmtInt(totalScannedAll)})`}
              </button>
              {galaxyStats.map((gs) => {
                const cls = [
                  "chip-btn",
                  "sector-gal-chip",
                  galaxyFilter === gs.galaxy ? "active" : "",
                  gs.is_owned ? "is-owned" : "",
                  gs.scanned_count === 0 ? "is-empty" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                const qSuffix = gs.queued_count > 0 ? ` · Q${gs.queued_count}` : "";
                return (
                  <button
                    key={gs.galaxy}
                    type="button"
                    className={cls}
                    data-sec-gal={String(gs.galaxy)}
                    title={`Galaxy ${gs.galaxy}: ${gs.scanned_count}/400 scanned (${gs.coverage_pct.toFixed(1)}%), ${gs.queued_count} queued`}
                    onClick={() => handleSelectGalaxy(gs.galaxy)}
                  >
                    {`G${gs.galaxy} (${gs.scanned_count}${qSuffix})`}
                  </button>
                );
              })}
            </div>
            <div className="filter-chips" role="group" aria-label="Sector status filter">
              {[
                { key: "all", label: "All Sectors" },
                { key: "scanned", label: "Scanned" },
                { key: "unscanned", label: "Unscanned" },
                { key: "queued", label: "Queued" },
                { key: "debris", label: "Has Debris" },
                { key: "targets", label: "Has Targets" },
                { key: "colony", label: "300+ Slots" },
                { key: "stale", label: "Stale (1h+)" },
              ].map((f) => (
                <button
                  key={f.key}
                  type="button"
                  className={`chip-btn${statusFilter === f.key ? " active" : ""}`}
                  data-sec-filter={f.key}
                  onClick={() => handleSelectStatusFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-sec-refresh"
              onClick={() =>
                refreshSectorsView(galaxyFilter, galaxyFilter > 0 || statusFilter === "unscanned")
              }
            >
              Refresh DB
            </button>
          </div>
        </div>

        <div className="table-scroll">
          <table className="data-table" id="sectors-table">
            <thead>
              <tr>
                <SortableTh
                  label="Sector"
                  sortKey="sector"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <SortableTh
                  label="Distance"
                  sortKey="distance"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <SortableTh
                  label="Occupied"
                  sortKey="occupied"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <SortableTh
                  label="Debris"
                  sortKey="debris"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <SortableTh
                  label="Targets"
                  sortKey="targets"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <SortableTh
                  label="Free / 300+"
                  sortKey="free"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <SortableTh
                  label="Raids / Score"
                  sortKey="raidsScore"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <SortableTh
                  label="Last Refresh"
                  sortKey="lastRefresh"
                  activeKey={sectorSort.sortKey}
                  direction={sectorSort.sortDirection}
                  onSort={sectorSort.requestSort}
                />
                <th>Ops</th>
              </tr>
            </thead>
            <tbody id="sectors-tbody">
              {sectorSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="empty-state">
                    {sectorsData
                      ? "No sectors match the current galaxy or status filter."
                      : "Click Refresh DB or run a Galaxy Sweep to populate sectors."}
                  </td>
                </tr>
              ) : (
                sectorSort.sortedRows.map((sec) => {
                  const scanned = isSectorRowScanned(sec);
                  const occ = Math.max(0, Math.round(finiteNum(sec.occupied_count, 0)));
                  const maxSlots = Math.max(1, Math.round(finiteNum(sec.max_slots, 16)));
                  const occPct = Math.min(100, Math.round((occ / maxSlots) * 100));
                  const occFillCls =
                    occ >= maxSlots
                      ? "sector-occ-fill is-full"
                      : occ >= 11
                        ? "sector-occ-fill is-dense"
                        : "sector-occ-fill";
                  const debris = Math.max(0, Math.round(finiteNum(sec.debris_count, 0)));
                  const spacia = Math.max(0, Math.round(finiteNum(sec.spacia_count, 0)));
                  const inact = Math.max(0, Math.round(finiteNum(sec.inactive_count, 0)));
                  const targets = Math.max(0, Math.round(finiteNum(sec.target_count, 0)));
                  const free = Math.max(0, Math.round(finiteNum(sec.free_count, 0)));
                  const prime = Math.max(0, Math.round(finiteNum(sec.prime_colony_free_count, 0)));
                  const dRaids = Math.max(0, Math.round(finiteNum(sec.direct_raid_count, 0)));
                  const affRaids = Math.max(0, Math.round(finiteNum(sec.owner_affinity_raid_count, 0)));
                  const score = finiteNum(sec.score, 0);
                  const scans = Math.max(1, Math.round(finiteNum(sec.scan_count, 1)));
                  const distFmt = formatSectorDistance(sec);
                  const freqOwners = Array.isArray(sec.frequent_owners) ? sec.frequent_owners : [];
                  const qPos = finiteNum(sec.queue_pos, null);
                  const qIsRefresh = sec.queue_phase && sec.queue_phase !== "exploratory";
                  const isRowBusy = refreshingSector === sec.sector;

                  return (
                    <tr key={sec.sector} className={scanned ? undefined : "sector-row-unscanned"}>
                      <td className="tabular">
                        <strong>{sec.sector}</strong>
                        {qPos !== null && qPos > 0 ? (
                          <span
                            className={`sector-queue-badge${qIsRefresh ? " is-refresh" : ""}`}
                            title={`Queued #${Math.round(qPos)} in upcoming smart sweep (${qIsRefresh ? "Refresh" : "Discovery"})`}
                          >
                            {`#${Math.round(qPos)}`}
                          </span>
                        ) : null}
                      </td>
                      <td className="tabular" title={distFmt.title}>
                        {distFmt.label}
                      </td>
                      <td
                        className="tabular"
                        title={
                          scanned
                            ? `${occ} occupied out of ${maxSlots} slots (${Math.max(0, Math.round(finiteNum(sec.active_occupied_count, 0)))} active)`
                            : "Sector has not been scanned yet"
                        }
                      >
                        {scanned ? (
                          <span className="sector-occ-cell">
                            <span>{`${occ} / ${maxSlots}`}</span>
                            <span className="sector-occ-meter" aria-hidden="true">
                              <span className={occFillCls} style={{ width: `${occPct}%` }} />
                            </span>
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td className="tabular">
                        {!scanned ? (
                          <span className="text-muted">—</span>
                        ) : debris > 0 ? (
                          <span className="outcome-badge is-draw">{`${debris} Debris`}</span>
                        ) : (
                          <span className="text-muted">0</span>
                        )}
                      </td>
                      <td className="tabular">
                        {!scanned ? (
                          <span className="text-muted">—</span>
                        ) : targets > 0 ? (
                          <>
                            {spacia > 0 ? (
                              <span className="outcome-badge is-win">{`${spacia} SPC`}</span>
                            ) : null}{" "}
                            {inact > 0 ? (
                              <span className="outcome-badge is-pending">{`${inact} INA`}</span>
                            ) : null}
                            {spacia === 0 && inact === 0 ? (
                              <span className="outcome-badge is-pending">{`${targets} TGT`}</span>
                            ) : null}
                          </>
                        ) : (
                          <span className="text-muted">0</span>
                        )}
                      </td>
                      <td className="tabular">
                        {scanned ? (
                          <>
                            <span>{`${free} free`}</span>
                            {prime > 0 ? (
                              <span className="sector-subline">{`${prime} prime 300+`}</span>
                            ) : null}
                          </>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td className="tabular" title={sec.reason || ""}>
                        <strong>{score.toFixed(1)}</strong>
                        {dRaids > 0 || affRaids > 0 ? (
                          <span className="sector-subline">
                            {`${dRaids} raids${affRaids > 0 ? ` · ${affRaids} aff` : ""}`}
                          </span>
                        ) : null}
                        {freqOwners.length > 0 ? (
                          <span className="sector-subline">{freqOwners.slice(0, 2).join(", ")}</span>
                        ) : null}
                      </td>
                      <td className="tabular">
                        {scanned ? (
                          <>
                            <span>{fmtShortDateTime(sec.last_scanned_ts)}</span>
                            <span className="sector-subline">
                              {`${formatShortAge(sec.age_seconds)} · x${scans}`}
                            </span>
                          </>
                        ) : (
                          <span className="sector-phase-pill is-discovery">Unscanned</span>
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          disabled={isRowBusy}
                          onClick={() => handleScanSector(sec.sector, sec.galaxy, sec.system)}
                        >
                          {isRowBusy ? "Scanning..." : scanned ? "Refresh" : "Scan"}
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
};

export default SectorsView;
