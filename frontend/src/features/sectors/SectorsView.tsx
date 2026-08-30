import React, { useEffect, useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type { ScannedSectorItem, ScannedSectorsSummary } from "../../types/api";
import { finiteNum, fmtInt, fmtShortDateTime } from "../../utils/formatters";

export interface SectorsViewProps {
  hidden?: boolean;
}

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

export function formatSectorDistance(row: ScannedSectorItem): { label: string; title: string } {
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
  const [sweepPagesInput, setSweepPagesInput] = useState<string>("100");
  const [savingSplit, setSavingSplit] = useState<boolean>(false);
  const [sweeping, setSweeping] = useState<boolean>(false);
  const [refreshingSector, setRefreshingSector] = useState<string | null>(null);

  const cfgRefreshPct =
    summary.effective_refresh_pct ??
    dashboard?.config?.galaxy_scan_refresh_pct ??
    (dashboard?.config?.galaxy_scan_explore_first === false ? 100 : 0);
  const cfgSweepPages = summary.sweep_pages ?? dashboard?.config?.galaxy_sweep_pages ?? 100;

  useEffect(() => {
    const rVal = Math.max(0, Math.min(100, Math.round(finiteNum(cfgRefreshPct, 0))));
    setRefreshPctInput(String(rVal));
  }, [cfgRefreshPct]);

  useEffect(() => {
    const pVal = Math.max(1, Math.min(400, Math.round(finiteNum(cfgSweepPages, 100))));
    setSweepPagesInput(String(pVal));
  }, [cfgSweepPages]);

  const parsedRefreshPct = Math.max(
    0,
    Math.min(100, Math.round(finiteNum(refreshPctInput, 0)))
  );
  const parsedDiscoveryPct = 100 - parsedRefreshPct;
  const parsedSweepPages = Math.max(
    1,
    Math.min(400, Math.round(finiteNum(sweepPagesInput, 100)))
  );
  const previewRefreshReq = Math.round((parsedSweepPages * parsedRefreshPct) / 100);
  const previewDiscoveryReq = Math.max(0, parsedSweepPages - previewRefreshReq);
  const previewModeLabel =
    parsedRefreshPct <= 0
      ? "EXPLORATORY (100% DISCOVERY)"
      : parsedRefreshPct >= 100
        ? "REFRESH ONLY (100% REFRESH)"
        : `MIXED (${parsedRefreshPct}% REFRESH / ${parsedDiscoveryPct}% DISCOVERY)`;

  const allSectors: ScannedSectorItem[] = useMemo(
    () =>
      Array.isArray(sectorsData?.sectors)
        ? (sectorsData!.sectors as ScannedSectorItem[]).filter((s) => s && typeof s === "object")
        : [],
    [sectorsData]
  );

  const galaxiesAvailable: number[] = useMemo(() => {
    if (Array.isArray(sectorsData?.galaxies_available) && sectorsData!.galaxies_available.length > 0) {
      return sectorsData!.galaxies_available
        .map((g: unknown) => Math.round(finiteNum(g, 0)))
        .filter((g: number) => g >= 1 && g <= 14);
    }
    const set = new Set<number>();
    allSectors.forEach((s) => {
      const gi = Math.round(finiteNum(s.galaxy, 0));
      if (gi >= 1 && gi <= 14) set.add(gi);
    });
    return Array.from(set).sort((a, b) => a - b);
  }, [allSectors, sectorsData]);

  const filteredSectors = useMemo(() => {
    return allSectors.filter((s) => {
      if (galaxyFilter > 0 && Math.round(finiteNum(s.galaxy, 0)) !== galaxyFilter) {
        return false;
      }
      if (statusFilter === "debris") return finiteNum(s.debris_count, 0) > 0;
      if (statusFilter === "targets") return finiteNum(s.target_count, 0) > 0;
      if (statusFilter === "colony") return finiteNum(s.prime_colony_free_count, 0) > 0;
      if (statusFilter === "stale") return finiteNum(s.age_seconds, 0) >= 3600;
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

  const handleSaveSplit = async () => {
    const rVal = Math.max(0, Math.min(100, Math.round(finiteNum(refreshPctInput, 0))));
    const pVal = Math.max(1, Math.min(400, Math.round(finiteNum(sweepPagesInput, 100))));
    setSavingSplit(true);
    try {
      await apiCommand({
        command: "update_config",
        config: {
          galaxy_scan_refresh_pct: rVal,
          galaxy_sweep_pages: pVal,
          galaxy_scan_explore_first: rVal < 100,
        },
      });
      const refReq = Math.round((pVal * rVal) / 100);
      const discReq = Math.max(0, pVal - refReq);
      showFeedback(
        `Sweep split saved: ${rVal}% Refresh (${refReq} req) / ${100 - rVal}% Discovery (${discReq} req) across ${pVal} req/sweep.`,
        "ok"
      );
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
      await refreshSectorsView(0);
    } catch (err: any) {
      showFeedback(`Failed saving sweep split: ${err.message}`, feedbackKindForError(err));
    } finally {
      setSavingSplit(false);
    }
  };

  const handleRunSweep = async () => {
    const pVal = Math.max(1, Math.min(400, Math.round(finiteNum(sweepPagesInput, 100))));
    setSweeping(true);
    try {
      showFeedback(
        `Starting smart galaxy sweep (${pVal} pages · ${parsedRefreshPct}% Refresh / ${parsedDiscoveryPct}% Discovery)...`,
        "ok"
      );
      const res = await apiCommand({
        command: "galaxy_sweep",
        pages: pVal,
        dry_run: false,
      });
      showFeedback(res.message || "Galaxy sweep completed.", res.ok === false ? "warn" : "ok");
      await loadDashboard({ refresh: false, force: true });
      await refreshSectorsView(0);
    } catch (err: any) {
      showFeedback(`Galaxy sweep failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setSweeping(false);
    }
  };

  const handleRefreshSingleSector = async (item: ScannedSectorItem) => {
    setRefreshingSector(item.sector);
    try {
      showFeedback(`Refreshing galaxy sector [${item.sector}]...`, "ok");
      const res = await apiCommand({
        command: "scan_galaxy",
        galaxy: item.galaxy,
        system: item.system,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Sector [${item.sector}] refreshed.`,
        res.executed === false ? "warn" : "ok"
      );
      await loadDashboard({ refresh: false, force: true });
      await refreshSectorsView(0);
    } catch (err: any) {
      showFeedback(`Sector [${item.sector}] refresh failed: ${err.message}`, feedbackKindForError(err));
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
  const originLabel = sectorsData?.origin_coords || activePlanet?.coords || "--";

  return (
    <section
      id="view-sectors"
      className="page-view"
      aria-label="Scanned galaxy sectors and refresh vs discovery split"
      hidden={isHidden}
    >
      <div className="combat-top-cards sectors-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Scanned Sectors &amp; Coverage</span>
          <strong className="kpi-value tabular" id="sec-kpi-count">
            {`${fmtInt(summary.scanned_sectors ?? allSectors.length)} Sectors`}
          </strong>
          <span className="kpi-sub tabular" id="sec-kpi-coverage">
            {`GAL ${galCovPct.toFixed(1)}% (${fmtInt(galExplored)}/${fmtInt(galTotal)}) · UNI ${uniCovPct.toFixed(1)}% (${fmtInt(uniExplored)}/${fmtInt(uniTotal)})`}
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
                onChange={(e) => setRefreshPctInput(e.target.value)}
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
                value={String(parsedDiscoveryPct)}
                onChange={(e) => {
                  const v = finiteNum(e.target.value, null);
                  if (v === null) {
                    setRefreshPctInput("100");
                  } else {
                    const clampedDisc = Math.max(0, Math.min(100, Math.round(v)));
                    setRefreshPctInput(String(100 - clampedDisc));
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
                onChange={(e) => setSweepPagesInput(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-sec-save-split"
              disabled={savingSplit}
              onClick={handleSaveSplit}
            >
              {savingSplit ? "Saving..." : "Apply Split"}
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
            {`Next batch: ${previewRefreshReq} Refresh · ${previewDiscoveryReq} Discovery (${previewModeLabel})`}
          </span>
        </div>
      </div>

      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">SEC</span>
            <h2>Scanned Galaxy Sectors</h2>
            <span className="module-meta tabular" id="sec-meta-count">
              {`${fmtInt(filteredSectors.length)} / ${fmtInt(allSectors.length)} SECTORS · ORIGIN [${originLabel}]`}
            </span>
          </div>
          <div className="filter-toolbar">
            {galaxiesAvailable.length > 0 ? (
              <div className="filter-chips" role="group" aria-label="Galaxy filter">
                <button
                  type="button"
                  className={`chip-btn${galaxyFilter === 0 ? " active" : ""}`}
                  data-sec-gal="0"
                  onClick={() => setUiPref("sectorGalaxy", 0)}
                >
                  All G
                </button>
                {galaxiesAvailable.map((g) => (
                  <button
                    key={g}
                    type="button"
                    className={`chip-btn${galaxyFilter === g ? " active" : ""}`}
                    data-sec-gal={String(g)}
                    onClick={() => setUiPref("sectorGalaxy", g)}
                  >
                    {`G${g}`}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="filter-chips" role="group" aria-label="Sector status filter">
              {[
                { key: "all", label: "All Sectors" },
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
                  onClick={() => setUiPref("sectorFilter", f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-sec-refresh"
              onClick={() => refreshSectorsView(0)}
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
                      ? "No scanned sectors match the current filter."
                      : "Click Refresh DB or run a Galaxy Sweep to populate scanned sectors."}
                  </td>
                </tr>
              ) : (
                sectorSort.sortedRows.map((sec) => {
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
                  const isRowBusy = refreshingSector === sec.sector;

                  return (
                    <tr key={sec.sector}>
                      <td className="tabular">
                        <strong>{sec.sector}</strong>
                      </td>
                      <td className="tabular" title={distFmt.title}>
                        {distFmt.label}
                      </td>
                      <td
                        className="tabular"
                        title={`${occ} occupied out of ${maxSlots} slots (${Math.max(0, Math.round(finiteNum(sec.active_occupied_count, 0)))} active)`}
                      >
                        <span className="sector-occ-cell">
                          <span>{`${occ} / ${maxSlots}`}</span>
                          <span className="sector-occ-meter" aria-hidden="true">
                            <span className={occFillCls} style={{ width: `${occPct}%` }} />
                          </span>
                        </span>
                      </td>
                      <td className="tabular">
                        {debris > 0 ? (
                          <span className="outcome-badge is-draw">{`${debris} Debris`}</span>
                        ) : (
                          <span className="text-muted">0</span>
                        )}
                      </td>
                      <td className="tabular">
                        {targets > 0 ? (
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
                        <span>{`${free} free`}</span>
                        {prime > 0 ? (
                          <span className="sector-subline">{`${prime} prime 300+`}</span>
                        ) : null}
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
                        <span>{fmtShortDateTime(sec.last_scanned_ts)}</span>
                        <span className="sector-subline">
                          {`${formatShortAge(sec.age_seconds)} · x${scans}`}
                        </span>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          disabled={isRowBusy}
                          onClick={() => handleRefreshSingleSector(sec)}
                        >
                          {isRowBusy ? "Scanning..." : "Refresh"}
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
