import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { LoadingIndicator } from "../../components/LoadingIndicator";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  useActivePage,
  useActivePlanet,
  useApiStatus,
  useConsoleActions,
  useDashboard,
  useEspData,
  useUiPrefs,
} from "../../store";
import {
  coordsSortKey,
  resourcesTotal,
  timestampSortKey,
  unitDictTotal,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  finiteNum,
  fmtInt,
  fmtShortDateTime,
  formatResourcesInline,
  formatUnitDictShort,
} from "../../utils/formatters";
import { dispatchPopulateSimEvent } from "../combat/simEvents";

export interface EspionageViewProps {
  hidden?: boolean;
}

interface ParsedCoords {
  g: number;
  s: number;
  p: number;
}

function parseCoordsTriple(raw: unknown): ParsedCoords | null {
  const txt = String(raw ?? "").trim();
  const m = txt.match(/(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})/);
  if (!m) return null;
  const g = Number.parseInt(m[1], 10);
  const s = Number.parseInt(m[2], 10);
  const p = Number.parseInt(m[3], 10);
  if (!Number.isFinite(g) || !Number.isFinite(s) || !Number.isFinite(p)) return null;
  return { g, s, p };
}

export function espionageDistanceSortKey(
  row: Record<string, any>,
  originCoords: string,
  targetCoords: string
): number | null {
  const oParsed = parseCoordsTriple(originCoords);
  const tParsed = parseCoordsTriple(targetCoords);

  let gDiff = finiteNum(row?.galaxy_delta, null);
  let sDiff = finiteNum(row?.system_delta, null);
  let pDiff = finiteNum(row?.position_delta, null);
  let sysDist = finiteNum(row?.system_distance, null);
  const flightDist = finiteNum(row?.distance, null);

  if (oParsed && tParsed) {
    if (gDiff === null) gDiff = Math.abs(oParsed.g - tParsed.g);
    if (sDiff === null) sDiff = Math.abs(oParsed.s - tParsed.s);
    if (pDiff === null) pDiff = Math.abs(oParsed.p - tParsed.p);
    if (sysDist === null) sysDist = gDiff * 400 + sDiff;
  }

  if (sysDist === null && gDiff === null && sDiff === null) {
    return flightDist !== null && flightDist >= 0 ? flightDist : null;
  }

  const gInt = Math.max(0, Math.round(gDiff ?? 0));
  const sInt = Math.max(0, Math.round(sDiff ?? Math.max(0, (sysDist ?? 0) - gInt * 400)));
  const pInt = Math.max(0, Math.round(pDiff ?? 0));
  return (gInt * 400 + sInt) * 100 + pInt;
}

export function formatEspionageDistance(
  row: Record<string, any>,
  originCoords: string,
  targetCoords: string
): { label: string; title: string } {
  const oParsed = parseCoordsTriple(originCoords);
  const tParsed = parseCoordsTriple(targetCoords);

  let gDiff = finiteNum(row?.galaxy_delta, null);
  let sDiff = finiteNum(row?.system_delta, null);
  let pDiff = finiteNum(row?.position_delta, null);
  let sysDist = finiteNum(row?.system_distance, null);
  let flightDist = finiteNum(row?.distance, null);

  if (oParsed && tParsed) {
    if (gDiff === null) gDiff = Math.abs(oParsed.g - tParsed.g);
    if (sDiff === null) sDiff = Math.abs(oParsed.s - tParsed.s);
    if (pDiff === null) pDiff = Math.abs(oParsed.p - tParsed.p);
    if (sysDist === null) sysDist = gDiff * 400 + sDiff;
    if (flightDist === null) {
      if (gDiff > 0) flightDist = 20000 * gDiff;
      else if (sDiff > 0) flightDist = 2700 + 95 * sDiff;
      else flightDist = 1000 + 5 * pDiff;
    }
  }

  if (sysDist === null && gDiff === null && sDiff === null) {
    return { label: "—", title: "Distance unavailable (missing origin or target coordinates)" };
  }

  const gInt = Math.max(0, Math.round(gDiff ?? 0));
  const sInt = Math.max(0, Math.round(sDiff ?? Math.max(0, (sysDist ?? 0) - gInt * 400)));
  const pInt = Math.max(0, Math.round(pDiff ?? 0));
  const distStr =
    flightDist !== null && flightDist >= 0 ? ` · Flight dist ${fmtInt(Math.round(flightDist))}` : "";
  const fromStr = originCoords ? `From ${originCoords} · ` : "";

  if (gInt > 0) {
    const label = sInt > 0 ? `${gInt}G · ${sInt} sys` : `${gInt}G`;
    return {
      label,
      title: `${fromStr}Δ${gInt} gal, Δ${sInt} sys${distStr}`,
    };
  }
  if (sInt === 0) {
    const posStr = pInt > 0 ? ` (pos Δ${pInt})` : "";
    return {
      label: "Same sys",
      title: `${fromStr}Same system${posStr}${distStr}`,
    };
  }
  return {
    label: `${sInt} sys`,
    title: `${fromStr}${sInt} solar systems away${distStr}`,
  };
}

type TargetFilterKey = "all" | "spied" | "spacia" | "inactive" | "debris";

const TARGET_FILTERS: ReadonlyArray<{ key: TargetFilterKey; label: string }> = [
  { key: "all", label: "All Targets" },
  { key: "spied", label: "Spied Intel" },
  { key: "spacia", label: "Spacia" },
  { key: "inactive", label: "Inactive" },
  { key: "debris", label: "Has Debris" },
];

export const EspionageView: React.FC<EspionageViewProps> = ({ hidden }) => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const activePlanet = useActivePlanet();
  const espData = useEspData();
  const uiPrefs = useUiPrefs();
  const { apiOnline, reconnectAttempts } = useApiStatus();
  const {
    setActivePage,
    refreshEspionageView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
  } = useConsoleActions();

  const isHidden = hidden ?? activePage !== "espionage";
  const espTab = uiPrefs.espTab === "reports" ? "reports" : "targets";

  const [espSearch, setEspSearch] = useState<string>("");
  const [targetFilter, setTargetFilter] = useState<TargetFilterKey>("all");
  const [loadingCount, setLoadingCount] = useState<number>(0);
  const [loadingLabel, setLoadingLabel] = useState<string | null>(null);
  const mountedRef = useRef<boolean>(true);
  const activeReqCountRef = useRef<number>(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const runTrackedRequest = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    activeReqCountRef.current += 1;
    if (mountedRef.current) {
      setLoadingCount(activeReqCountRef.current);
      setLoadingLabel(label);
    }
    try {
      await fn();
    } finally {
      activeReqCountRef.current = Math.max(0, activeReqCountRef.current - 1);
      if (mountedRef.current) {
        setLoadingCount(activeReqCountRef.current);
        if (activeReqCountRef.current === 0) {
          setLoadingLabel(null);
        }
      }
    }
  }, []);

  const isInitialLoading = espData === null;
  const isRefreshing = loadingCount > 0;
  const showLoading = isInitialLoading || isRefreshing;

  const sum = espData?.summary || {};
  const originCoords = String(
    espData?.origin_coords || activePlanet?.coords || dashboard?.state?.planets?.[0]?.coords || ""
  ).trim();
  const targets: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(espData?.targets) ? espData!.targets : []),
    [espData]
  );
  const reports: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(espData?.reports) ? espData!.reports : []),
    [espData]
  );

  const filteredTargets = useMemo(() => {
    const needle = espSearch.trim().toLowerCase();
    return targets.filter((t) => {
      const isSpacia = Boolean(t.is_spacia);
      const isInact = Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
      if (targetFilter === "spied" && !t.has_spy_intel) return false;
      if (targetFilter === "spacia" && !isSpacia) return false;
      if (targetFilter === "inactive" && !isInact) return false;
      if (targetFilter === "debris" && !t.has_debris) return false;
      if (!needle) return true;
      const hay = [
        t.coords,
        t.target_coords,
        t.planet_name,
        t.owner,
        t.status_color,
        isSpacia ? "spacia" : "",
        isInact ? "inactive" : "",
        t.has_debris ? "debris" : "",
        t.dug_in ? "dug-in" : "",
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [targets, targetFilter, espSearch]);

  const filteredReports = useMemo(() => {
    const needle = espSearch.trim().toLowerCase();
    if (!needle) return reports;
    return reports.filter((r) => {
      const hay = [r.coords, r.target_coords, r.planet_name, r.owner, r.msg_date_text]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [reports, espSearch]);

  const targetSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "coords" | "distance" | "player" | "status" | "fields" | "resources" | "spiceEq" | "fleetDef"
    >
  >(
    () => ({
      coords: (t) => coordsSortKey(t.coords || t.target_coords),
      distance: (t) =>
        espionageDistanceSortKey(t, originCoords, String(t.coords || t.target_coords || "")),
      player: (t) => [t.planet_name, t.owner].filter(Boolean).join(" · ") || null,
      status: (t) => {
        const isSpacia = Boolean(t.is_spacia);
        const isInact = Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
        const tags = [
          isSpacia
            ? "SPACIA"
            : isInact
              ? "INACTIVE"
              : String(t.status_color || "ACTIVE").toUpperCase(),
        ];
        if (t.has_debris) tags.push("DEBRIS");
        if (t.dug_in) tags.push("DUG-IN");
        return tags.join(" · ");
      },
      fields: {
        accessor: (t) => {
          const uf = finiteNum(t.uninhabited_fields, null);
          return uf !== null && uf > 0 ? Math.round(uf) : null;
        },
        defaultDirection: "desc",
      },
      resources: {
        accessor: (t) => (t.has_spy_intel ? resourcesTotal(t.resources) : null),
        defaultDirection: "desc",
      },
      spiceEq: {
        accessor: (t) => (t.has_spy_intel ? finiteNum(t.spice_eq, null) : null),
        defaultDirection: "desc",
      },
      fleetDef: {
        accessor: (t) =>
          t.has_spy_intel
            ? (unitDictTotal(t.fleet) ?? 0) + (unitDictTotal(t.defense) ?? 0)
            : null,
        defaultDirection: "desc",
      },
    }),
    [originCoords]
  );

  const reportSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "recorded" | "coords" | "distance" | "owner" | "fields" | "resources" | "buildings" | "fleetDef"
    >
  >(
    () => ({
      recorded: {
        accessor: (r) => timestampSortKey(r.ts, r.msg_date_text),
        defaultDirection: "desc",
      },
      coords: (r) => coordsSortKey(r.coords || r.target_coords),
      distance: (r) =>
        espionageDistanceSortKey(r, originCoords, String(r.coords || r.target_coords || "")),
      owner: (r) =>
        [r.planet_name, r.owner].filter(Boolean).join(" · ") ||
        (r.uninhabited_fields ? "Uninhabited Slot" : null),
      fields: {
        accessor: (r) => {
          const uf = finiteNum(r.uninhabited_fields, null);
          return uf !== null && uf > 0 ? Math.round(uf) : null;
        },
        defaultDirection: "desc",
      },
      resources: {
        accessor: (r) =>
          finiteNum(r.spice_eq ?? r.loot_spice_eq, null) ?? resourcesTotal(r.resources),
        defaultDirection: "desc",
      },
      buildings: {
        accessor: (r) => {
          const b = unitDictTotal(r.buildings);
          const res = unitDictTotal(r.researches);
          return b === null && res === null ? null : (b ?? 0) + (res ?? 0);
        },
        defaultDirection: "desc",
      },
      fleetDef: {
        accessor: (r) => (unitDictTotal(r.fleet) ?? 0) + (unitDictTotal(r.defense) ?? 0),
        defaultDirection: "desc",
      },
    }),
    [originCoords]
  );

  const targetSort = useTableSort(filteredTargets, targetSortCols);
  const reportSort = useTableSort(filteredReports, reportSortCols);

  const targetPagination = useTablePagination(
    targetSort.sortedRows,
    `${espTab}|${targetFilter}|${espSearch}|${targetSort.sortKey ?? ""}|${targetSort.sortDirection ?? ""}`,
    25
  );
  const reportPagination = useTablePagination(
    reportSort.sortedRows,
    `${espTab}|${espSearch}|${reportSort.sortKey ?? ""}|${reportSort.sortDirection ?? ""}`,
    25
  );

  const activeTabHasRows =
    espTab === "reports" ? reports.length > 0 : targets.length > 0;
  const usePanelLoading = isInitialLoading || (isRefreshing && !activeTabHasRows);

  const bestSpEq = targets.reduce(
    (acc, t) => Math.max(acc, finiteNum(t?.spice_eq, 0)),
    0
  );
  const galSweep = dashboard?.galaxy_sweep || null;
  const galTotal = Math.max(0, Math.round(finiteNum(galSweep?.total_systems, 0)));
  const galExplored = Math.max(0, Math.round(finiteNum(galSweep?.explored_systems, 0)));
  const galPct =
    galSweep && galSweep.coverage_pct != null
      ? Math.max(0, Math.min(100, finiteNum(galSweep.coverage_pct, 0)))
      : galTotal > 0
        ? Math.max(0, Math.min(100, (galExplored / galTotal) * 100))
        : null;
  const uniTotal = Math.max(1, Math.round(finiteNum(galSweep?.universe_total_systems, 5600)));
  const uniExplored = Math.max(0, Math.round(finiteNum(galSweep?.universe_explored_systems, galExplored)));
  const uniPct =
    galSweep && galSweep.universe_coverage_pct != null
      ? Math.max(0, Math.min(100, finiteNum(galSweep.universe_coverage_pct, 0)))
      : galPct !== null
        ? Math.max(0, Math.min(100, (uniExplored / uniTotal) * 100))
        : null;
  const galPhaseText =
    String(galSweep?.scan_phase || "").toLowerCase() === "targeted_refresh"
      ? "Targeted Refresh"
      : "Exploratory";

  const handleRefreshEspionage = () => {
    void runTrackedRequest("Loading espionage intel...", () => refreshEspionageView());
  };

  const handleSyncInbox = () => {
    void runTrackedRequest("Syncing inbox folders & espionage intel...", () =>
      syncAllInboxFolders()
    );
  };

  const handleSpyTarget = async (coords: string) => {
    try {
      showFeedback(`Dispatching spy probe to ${coords}...`, "ok");
      const res = await apiCommand({
        command: "spy",
        target_coords: coords,
        quantity: 1,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Spy probe sent to ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await runTrackedRequest("Refreshing espionage intel...", () => refreshEspionageView());
    } catch (err: any) {
      showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleSimTarget = (target: Record<string, any>) => {
    const resMap = target.resources || {};
    const totalLoot =
      finiteNum(resMap.pig_iron, 0) +
      finiteNum(resMap.metal, 0) +
      finiteNum(resMap.kryptonite, 0) +
      finiteNum(resMap.spice, 0);
    setActivePage("combat");
    dispatchPopulateSimEvent({
      defense: target.defense || {},
      fleet: target.fleet || {},
      totalLoot,
    });
  };

  const handleAttackTarget = async (coords: string) => {
    try {
      showFeedback(`Dispatching raid to ${coords}...`, "ok");
      const res = await apiCommand({
        command: "attack",
        target_coords: coords,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Attack processed for ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await runTrackedRequest("Refreshing espionage intel...", async () => {
        await loadDashboard({ refresh: false, force: true });
        await refreshEspionageView();
      });
    } catch (err: any) {
      showFeedback(`Attack failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleNavigateMessages = async () => {
    try {
      await apiCommand({ command: "navigate", action: "internalMessages" });
      showFeedback("Chrome switched to internalMessages.", "ok");
    } catch (err: any) {
      showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-espionage"
      className="page-view"
      aria-label="Espionage targets and spy report archive"
      hidden={isHidden}
    >
      <div className="combat-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Spied Targets</span>
          <strong className="kpi-value tabular" id="esp-kpi-targets">
            {espData
              ? `${fmtInt(sum.spied_targets ?? 0)} / ${fmtInt(sum.total_targets ?? targets.length)}`
              : "0"}
          </strong>
          <span className="kpi-sub tabular" id="esp-kpi-reports">
            {`${fmtInt(sum.total_reports ?? reports.length)} archived spy reports`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Spacia &amp; Inactives</span>
          <strong className="kpi-value tabular" id="esp-kpi-spacia">
            {`${fmtInt(sum.spacia_count ?? 0)} Spacia`}
          </strong>
          <span className="kpi-sub tabular" id="esp-kpi-inactives">
            {`${fmtInt(sum.inactive_count ?? 0)} yellow inactive planets`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Debris &amp; Top Loot</span>
          <strong className="kpi-value tabular" id="esp-kpi-debris">
            {`${fmtInt(sum.debris_count ?? 0)} Debris`}
          </strong>
          <span className="kpi-sub tabular" id="esp-kpi-top-loot">
            {espData
              ? `Peak target: ${fmtInt(Math.round(bestSpEq))} SpEq`
              : "Best loot: 0 SpEq"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Espionage Sync</span>
          <div className="combat-jumps">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-esp-sync"
              data-testid="btn-sync-espionage"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleSyncInbox}
            >
              Sync All Folders
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-esp-refresh"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleRefreshEspionage}
            >
              Refresh DB
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm nav-jump"
              data-action="internalMessages"
              onClick={handleNavigateMessages}
            >
              Messages
            </button>
          </div>
        </div>
      </div>

      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">SPY</span>
            <h2>Espionage Intelligence &amp; Spy Report Archive</h2>
            <span className="module-meta tabular" id="esp-meta-count">
              {espData
                ? `${
                    filteredTargets.length !== targets.length
                      ? `${filteredTargets.length}/${targets.length}`
                      : String(targets.length)
                  } TARGETS · ${
                    filteredReports.length !== reports.length
                      ? `${filteredReports.length}/${reports.length}`
                      : String(reports.length)
                  } REPORTS${originCoords ? ` · FROM ${originCoords}` : ""}`
                : "LOADING..."}
            </span>
            <span
              className="gal-cov-inline tabular"
              id="esp-galaxy-coverage"
              title={
                galPct !== null
                  ? `Galaxy coverage: ${galExplored}/${galTotal} (${galPct.toFixed(1)}%) · Universe coverage: ${uniExplored}/${uniTotal} (${(uniPct ?? 0).toFixed(2)}%) · ${galPhaseText}`
                  : "Galaxy and universe exploration percentage"
              }
            >
              {galPct !== null
                ? `GAL ${galPct.toFixed(1)}% (${galExplored}/${galTotal}) · UNI ${(uniPct ?? 0).toFixed(1)}% (${fmtInt(uniExplored)}/${fmtInt(uniTotal)}) · ${galPhaseText}`
                : "GAL --% · UNI --%"}
            </span>
          </div>
          <div className="filter-toolbar">
            <input
              type="search"
              id="esp-search"
              className="table-search-input"
              placeholder="Search coords / player / status..."
              aria-label="Search espionage targets and reports"
              value={espSearch}
              onChange={(e) => setEspSearch(e.target.value)}
            />
            <div className="tab-group" role="tablist" aria-label="Espionage subtabs">
              <button
                type="button"
                className={`tab-btn${espTab === "targets" ? " active" : ""}`}
                data-esp-tab="targets"
                onClick={() => setUiPref("espTab", "targets")}
              >
                Spied Targets
              </button>
              <button
                type="button"
                className={`tab-btn${espTab === "reports" ? " active" : ""}`}
                data-esp-tab="reports"
                onClick={() => setUiPref("espTab", "reports")}
              >
                Historical Spy Reports
              </button>
            </div>
          </div>
        </div>

        <div className="intel-stage">
          {showLoading ? (
            <LoadingIndicator
              id="esp-loading-indicator"
              variant={usePanelLoading ? "panel" : "inline"}
              message={loadingLabel || "Loading espionage intel..."}
              detail={
                !apiOnline && reconnectAttempts > 0
                  ? "Waiting for Game API connection..."
                  : usePanelLoading
                    ? "Fetching spied targets and historical spy archive from SQLite"
                    : "Refreshing espionage targets and spy reports"
              }
            />
          ) : null}

          <div
            className="intel-pane"
            id="esp-pane-targets"
            hidden={usePanelLoading || espTab !== "targets"}
          >
          <div className="intel-filter-bar">
            <div className="filter-chips" role="group" aria-label="Espionage target filters">
              {TARGET_FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  className={`chip-btn${targetFilter === f.key ? " active" : ""}`}
                  data-esp-filter={f.key}
                  onClick={() => setTargetFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Coords"
                    sortKey="coords"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                  />
                  <SortableTh
                    label="Distance"
                    sortKey="distance"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Player / Planet"
                    sortKey="player"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                  />
                  <SortableTh
                    label="Status"
                    sortKey="status"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                  />
                  <SortableTh
                    label="Fields"
                    sortKey="fields"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Resources (Pig / Met / Kry / Spi)"
                    sortKey="resources"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                  />
                  <SortableTh
                    label="Loot SpEq"
                    sortKey="spiceEq"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Fleet / Defense"
                    sortKey="fleetDef"
                    activeKey={targetSort.sortKey}
                    direction={targetSort.sortDirection}
                    onSort={targetSort.requestSort}
                  />
                  <th className="col-right">Ops</th>
                </tr>
              </thead>
              <tbody id="esp-targets-tbody">
                {targetPagination.pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="empty-cell">
                      {targets.length > 0
                        ? "No espionage targets match the active filter or search query."
                        : "No espionage targets recorded yet. Run Scan Galaxy or Sync All Folders."}
                    </td>
                  </tr>
                ) : (
                  targetPagination.pagedRows.map((t, idx) => {
                    const coords = String(t.coords || t.target_coords || "—");
                    const dist = formatEspionageDistance(t, originCoords, coords);
                    const isSpacia = Boolean(t.is_spacia);
                    const isInact =
                      Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
                    const tags = [
                      isSpacia
                        ? "SPACIA"
                        : isInact
                          ? "INACTIVE"
                          : String(t.status_color || "ACTIVE").toUpperCase(),
                    ];
                    if (t.has_debris) tags.push("DEBRIS");
                    if (t.dug_in) tags.push("DUG-IN");
                    const uf = finiteNum(t.uninhabited_fields, null);
                    const fStr = formatUnitDictShort(t.fleet, 3);
                    const dStr = formatUnitDictShort(t.defense, 3);
                    const fdTxt = t.has_spy_intel
                      ? fStr === "None" && dStr === "None"
                        ? "Clear (0)"
                        : `F:${fStr} / D:${dStr}`
                      : "—";

                    return (
                      <tr key={`${coords}-${idx}`}>
                        <td className="tabular">{coords}</td>
                        <td className="col-right tabular" title={dist.title}>
                          {dist.label}
                        </td>
                        <td>
                          {[t.planet_name, t.owner].filter(Boolean).join(" · ") ||
                            "Uninhabited / Unknown"}
                        </td>
                        <td>
                          <span
                            className={`status-word${isSpacia || isInact ? "" : " is-warn"}`}
                          >
                            {tags.join(" · ")}
                          </span>
                        </td>
                        <td className="col-right tabular">
                          {uf !== null && uf > 0 ? String(Math.round(uf)) : "—"}
                        </td>
                        <td className="tabular">
                          {t.has_spy_intel ? formatResourcesInline(t.resources) : "Not spied yet"}
                        </td>
                        <td className="col-right tabular">
                          {t.has_spy_intel
                            ? fmtInt(Math.round(finiteNum(t.spice_eq, 0)))
                            : "—"}
                        </td>
                        <td className="tabular">{fdTxt}</td>
                        <td className="col-right">
                          <div className="row-actions">
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => handleSpyTarget(coords)}
                            >
                              Spy
                            </button>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => handleSimTarget(t)}
                            >
                              Sim
                            </button>
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              onClick={() => handleAttackTarget(coords)}
                            >
                              Attack
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <TablePagination
            idPrefix="esp-targets"
            totalItems={targetPagination.totalItems}
            page={targetPagination.page}
            pageSize={targetPagination.pageSize}
            itemLabel="targets"
            onPageChange={targetPagination.setPage}
            onPageSizeChange={targetPagination.setPageSize}
          />
        </div>

        <div
          className="intel-pane"
          id="esp-pane-reports"
          hidden={usePanelLoading || espTab !== "reports"}
        >
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Recorded"
                    sortKey="recorded"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                  />
                  <SortableTh
                    label="Coords"
                    sortKey="coords"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                  />
                  <SortableTh
                    label="Distance"
                    sortKey="distance"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Owner / Planet"
                    sortKey="owner"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                  />
                  <SortableTh
                    label="Fields"
                    sortKey="fields"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Resources & SpEq"
                    sortKey="resources"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                  />
                  <SortableTh
                    label="Buildings / Techs"
                    sortKey="buildings"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                  />
                  <SortableTh
                    label="Fleet / Defense"
                    sortKey="fleetDef"
                    activeKey={reportSort.sortKey}
                    direction={reportSort.sortDirection}
                    onSort={reportSort.requestSort}
                  />
                </tr>
              </thead>
              <tbody id="esp-reports-tbody">
                {reportPagination.pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      {reports.length > 0
                        ? "No historical spy reports match the active search query."
                        : "No historical spy reports stored yet."}
                    </td>
                  </tr>
                ) : (
                  reportPagination.pagedRows.map((r, idx) => {
                    const rCoords = String(r.coords || r.target_coords || "—");
                    const rDist = formatEspionageDistance(r, originCoords, rCoords);
                    const uf = finiteNum(r.uninhabited_fields, null);
                    const spEq = Math.round(finiteNum(r.spice_eq ?? r.loot_spice_eq, 0));
                    const bStr = formatUnitDictShort(r.buildings, 3);
                    const rStr = formatUnitDictShort(r.researches, 3);
                    const fStr = formatUnitDictShort(r.fleet, 3);
                    const dStr = formatUnitDictShort(r.defense, 3);
                    return (
                      <tr key={r.id ?? `${r.coords}-${idx}`}>
                        <td className="tabular">
                          {fmtShortDateTime(r.ts, r.msg_date_text)}
                        </td>
                        <td className="tabular">{rCoords}</td>
                        <td className="col-right tabular" title={rDist.title}>
                          {rDist.label}
                        </td>
                        <td>
                          {[r.planet_name, r.owner].filter(Boolean).join(" · ") ||
                            (r.uninhabited_fields ? "Uninhabited Slot" : "Unknown")}
                        </td>
                        <td className="col-right tabular">
                          {uf !== null && uf > 0 ? `${Math.round(uf)} fields` : "—"}
                        </td>
                        <td className="tabular">
                          {`${formatResourcesInline(r.resources)} (${fmtInt(spEq)} SpEq)`}
                        </td>
                        <td className="tabular">
                          {bStr === "None" && rStr === "None" ? "—" : `B:${bStr} · R:${rStr}`}
                        </td>
                        <td className="tabular">
                          {fStr === "None" && dStr === "None"
                            ? "Clear (0)"
                            : `F:${fStr} · D:${dStr}`}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <TablePagination
            idPrefix="esp-reports"
            totalItems={reportPagination.totalItems}
            page={reportPagination.page}
            pageSize={reportPagination.pageSize}
            itemLabel="reports"
            onPageChange={reportPagination.setPage}
            onPageSizeChange={reportPagination.setPageSize}
          />
        </div>
        </div>
      </article>
    </section>
  );
};

export default EspionageView;
