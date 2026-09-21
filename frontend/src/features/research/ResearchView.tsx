import React, { useCallback, useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import {
  durationToSeconds,
  resourcesTotal,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  bottleneckTag,
  clampPct,
  finiteNum,
  fmtShortDateTime,
  formatResourcesInline,
  getSelectedBlastFurnacePct,
} from "../../utils/formatters";
import { BuildingInspector } from "../buildings/BuildingInspector";

export interface ResearchViewProps {
  hidden?: boolean;
}

const VALID_RESEARCH_TABS = new Set<string>([
  "all",
  "unlocked",
  "locked",
  "universe_research",
]);

export const ResearchView: React.FC<ResearchViewProps> = ({ hidden }) => {
  const {
    dashboard,
    activePage,
    selectedPlanetId,
    activePlanet,
    showFeedback,
    apiCommand,
    loadDashboard,
    uiPrefs,
    setUiPref,
    buildingsEtaData,
    refreshBuildingsEtaForSelectedPlanet,
    enqueueManualCommand,
  } = useConsole();

  const [refreshingEta, setRefreshingEta] = useState(false);
  const [syncingUniverseMax, setSyncingUniverseMax] = useState(false);

  const isHidden = hidden !== undefined ? hidden : (activePage as string) !== "research";

  const etaData = useMemo(() => {
    if (
      buildingsEtaData &&
      activePlanet &&
      buildingsEtaData.planet?.planet_id === activePlanet.planet_id
    ) {
      return buildingsEtaData;
    }
    return dashboard?.buildings_eta || null;
  }, [activePlanet, buildingsEtaData, dashboard?.buildings_eta]);

  const prefsAny = uiPrefs as Record<string, any>;
  const setPrefAny = setUiPref as (key: string, value: any) => void;

  const rawTab = String(prefsAny.resTab || "all");
  const resTab = VALID_RESEARCH_TABS.has(rawTab) ? rawTab : "all";
  const isUniverseResearchTab = resTab === "universe_research";

  const p = etaData?.planet || {};
  const econ = etaData?.planet_economy || {};
  const resDiv =
    finiteNum(p.research_time_divisor, null) ?? finiteNum(p.building_time_divisor, 1);
  const planetMetaText = etaData
    ? `[${p.coords || "--"}] · Lab L${finiteNum(p.research_lab_level, 0)} · Micro L${finiteNum(p.microsystem_level, 0)} · Div x${resDiv}`
    : "[--] · Lab L0 · Micro L0 · Div x1";

  const econBfPct = finiteNum(econ.blast_furnace_pct, null);
  const bfPct =
    econBfPct !== null
      ? clampPct(Math.round(econBfPct))
      : getSelectedBlastFurnacePct(dashboard, selectedPlanetId, buildingsEtaData);

  const allResearches = useMemo<Array<Record<string, any>>>(
    () => (Array.isArray(etaData?.researches) ? etaData!.researches : []),
    [etaData?.researches]
  );

  const visibleRows = useMemo(() => {
    if (!etaData) return [];
    if (resTab === "unlocked") {
      return allResearches.filter(
        (r) => !Array.isArray(r.unmet_prerequisites) || r.unmet_prerequisites.length === 0
      );
    }
    if (resTab === "locked") {
      return allResearches.filter(
        (r) => Array.isArray(r.unmet_prerequisites) && r.unmet_prerequisites.length > 0
      );
    }
    return allResearches;
  }, [allResearches, etaData, resTab]);

  const selectedResearchKey = String(prefsAny.selectedResearchKey || "r1");
  const selectedRow = useMemo(() => {
    if (!etaData) return null;
    const hit = allResearches.find((r) => r?.key === selectedResearchKey);
    if (hit) return hit;
    return visibleRows[0] || allResearches[0] || null;
  }, [allResearches, etaData, selectedResearchKey, visibleRows]);

  const universeMaxRows: Array<Record<string, any>> = useMemo(
    () =>
      Array.isArray(dashboard?.universe_research_max) ? dashboard!.universe_research_max! : [],
    [dashboard?.universe_research_max]
  );

  const selectedUniverseMaxRow = useMemo(() => {
    if (!selectedRow) return null;
    const selKey = String(selectedRow.key || "").trim();
    const selId = finiteNum(selectedRow.research_id, null);
    return (
      universeMaxRows.find((r) => {
        const rk = String(r.key || r.research_key || "").trim();
        if (selKey && rk === selKey) return true;
        if (selId !== null && finiteNum(r.research_id, null) === selId) return true;
        return false;
      }) || null
    );
  }, [selectedRow, universeMaxRows]);

  const researchTabs = useMemo(
    () => [
      {
        id: "all",
        label: `All ${allResearches.length > 0 ? allResearches.length : 15}`,
      },
      { id: "unlocked", label: "Unlocked" },
      { id: "locked", label: "Locked" },
      { id: "universe_research", label: "Universe Max Research" },
    ],
    [allResearches.length]
  );

  const researchSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "unit" | "level" | "next" | "missing" | "buildTime" | "resReady" | "reached"
    >
  >(
    () => ({
      unit: (row) => String(row.name || row.key || ""),
      level: {
        accessor: (row) => finiteNum(row.current_level, 0),
        defaultDirection: "desc",
      },
      next: {
        accessor: (row) => finiteNum(row.next_level ?? row.target_level, null),
        defaultDirection: "desc",
      },
      missing: (row) => finiteNum(row.missing_total, null) ?? resourcesTotal(row.missing) ?? 0,
      buildTime: (row) =>
        finiteNum(row.build_seconds ?? row.build_time_seconds, null) ??
        durationToSeconds(row.build_time_text),
      resReady: (row) => {
        if (row.wait_eta === "—") return null;
        const wh = finiteNum(row.wait_hours, null);
        if (wh !== null) return wh * 3600;
        return durationToSeconds(row.wait_eta);
      },
      reached: (row) => {
        if (row.reach_eta === "—") return null;
        const rh = finiteNum(row.reach_hours ?? row.total_hours, null);
        if (rh !== null) return rh * 3600;
        return durationToSeconds(row.reach_eta);
      },
    }),
    []
  );

  const uniMaxSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "research" | "ownLevel" | "uniMax" | "gap" | "spiedMax" | "holder" | "catalogCap" | "updated"
    >
  >(
    () => ({
      research: (r) => String(r.name || r.slug || r.key || ""),
      ownLevel: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.own_level, 0))),
        defaultDirection: "desc",
      },
      uniMax: {
        accessor: (r) => {
          const own = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
          return Math.max(own, Math.round(finiteNum(r.universe_max_level, own)));
        },
        defaultDirection: "desc",
      },
      gap: {
        accessor: (r) => {
          const own = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
          const uni = Math.max(own, Math.round(finiteNum(r.universe_max_level, own)));
          return Math.max(0, finiteNum(r.gap_to_max, uni - own));
        },
        defaultDirection: "desc",
      },
      spiedMax: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.spied_max_level, 0))),
        defaultDirection: "desc",
      },
      holder: (r) => {
        const own = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
        const uni = Math.max(own, Math.round(finiteNum(r.universe_max_level, own)));
        if (r.holder) {
          return `${r.holder}${r.holder_coords ? ` [${r.holder_coords}]` : ""}`;
        }
        return uni > 0 && own >= uni ? "You" : null;
      },
      catalogCap: {
        accessor: (r) => {
          const cap = finiteNum(r.catalog_max_level, null);
          return cap !== null && cap > 0 ? cap : null;
        },
        defaultDirection: "desc",
      },
      updated: {
        accessor: (r) => timestampSortKey(r.updated_ts),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const researchSort = useTableSort(visibleRows, researchSortCols);
  const uniMaxSort = useTableSort(universeMaxRows, uniMaxSortCols);

  const handleRefreshEta = useCallback(async () => {
    setRefreshingEta(true);
    try {
      await refreshBuildingsEtaForSelectedPlanet(true);
      showFeedback("Refreshed research reach estimates.", "ok");
    } catch (err: any) {
      showFeedback(`Research refresh failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setRefreshingEta(false);
    }
  }, [refreshBuildingsEtaForSelectedPlanet, showFeedback]);

  const handleRefreshUniverseResearch = useCallback(async () => {
    setSyncingUniverseMax(true);
    try {
      showFeedback("Refreshing Universe Max Research levels...", "ok");
      const res = await apiCommand({ command: "universe_research_max", sync: true });
      showFeedback(
        res.message || "Universe Max Research table refreshed and saved to SQLite.",
        "ok"
      );
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Universe research sync failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setSyncingUniverseMax(false);
    }
  }, [apiCommand, loadDashboard, showFeedback]);

  const handleRowLiveResearch = useCallback(
    async (ev: React.MouseEvent, row: Record<string, any>) => {
      ev.stopPropagation();
      try {
        const res = await apiCommand({
          command: "research",
          research_id: row.research_id,
          planet_id: p.planet_id,
          dry_run: false,
        });
        showFeedback(res.message || "Research queued.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err: any) {
        showFeedback(`Research failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, loadDashboard, p.planet_id, showFeedback]
  );

  const handleRowPinQueue = useCallback(
    (ev: React.MouseEvent, row: Record<string, any>) => {
      ev.stopPropagation();
      const unitId = row.research_id;
      const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
      if (unitId == null || nextLvl === null || nextLvl <= 0) return;
      void enqueueManualCommand({
        kind: "RESEARCH",
        priority: "P1",
        position: "top",
        planet_id: p.planet_id || 0,
        target_id: unitId,
        target_name: row.name || "",
        target_level: nextLvl,
        deduplicate_pending: true,
      });
    },
    [enqueueManualCommand, p.planet_id]
  );

  return (
    <section
      id="view-research"
      className="page-view"
      aria-label="Research and reach ETAs"
      hidden={isHidden}
    >
      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">RES</span>
            <h2>Research — Level Reach Estimates</h2>
            <span className="module-meta tabular" id="res-planet-meta">
              {planetMetaText}
            </span>
          </div>

          <div className="filter-toolbar">
            <div
              className="tab-group"
              id="res-category-filters"
              role="group"
              aria-label="Research view filters"
            >
              {researchTabs.map((tab) => {
                const isActive = resTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    className={`chip-btn${isActive ? " active" : ""}`}
                    data-res-tab={tab.id}
                    data-bcat={tab.id === "universe_research" ? "universe_research" : undefined}
                    onClick={() => setPrefAny("resTab", tab.id)}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>

            <div className="inline-actions">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-res-refresh-eta"
                title="Recompute research reach ETAs for active colony"
                disabled={refreshingEta}
                onClick={handleRefreshEta}
              >
                {refreshingEta ? "Refreshing..." : "Refresh ETAs"}
              </button>
            </div>
          </div>
        </div>

        {/* Research Technologies Split Workspace */}
        <div className="split-workspace" hidden={isUniverseResearchTab}>
          <div className="table-wrap table-scroll">
            <table id="res-table">
              <thead>
                <tr>
                  <SortableTh
                    label="Technology"
                    sortKey="unit"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                  />
                  <SortableTh
                    label="Lvl"
                    sortKey="level"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Next"
                    sortKey="next"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Missing Resources"
                    sortKey="missing"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                  />
                  <SortableTh
                    label="Research Time"
                    sortKey="buildTime"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Res Ready"
                    sortKey="resReady"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Level Reached"
                    sortKey="reached"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <th className="col-right">Control</th>
                </tr>
              </thead>
              <tbody id="res-eta-tbody">
                {!etaData ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      Waiting for research catalog...
                    </td>
                  </tr>
                ) : researchSort.sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      No research technologies in selected filter.
                    </td>
                  </tr>
                ) : (
                  researchSort.sortedRows.map((row: any, idx: number) => {
                    const rowKey = row.key != null ? String(row.key).trim() : "";
                    const isSelected = Boolean(selectedRow && selectedRow.key === row.key);
                    const curLvl = finiteNum(row.current_level, 0);
                    const missTotal = finiteNum(row.missing_total, 0);
                    const waitEtaText = row.wait_eta ?? "—";
                    const bnTag =
                      typeof row.bottleneck === "string" && row.bottleneck.trim()
                        ? bottleneckTag(row.bottleneck)
                        : "";
                    const reachEtaText = row.reach_eta ?? "—";
                    const rowWaitH = finiteNum(row.wait_hours, 0);
                    const canExec = Boolean(row.can_research_live);
                    const unitId = row.research_id;
                    const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
                    const safeName = String(row.name || "Research").replace(/>/g, "→");

                    return (
                      <tr
                        key={rowKey || `research-${idx}`}
                        className={`clickable-row${isSelected ? " row-selected" : ""}`}
                        onClick={() => {
                          if (rowKey) {
                            setPrefAny("selectedResearchKey", rowKey);
                          }
                        }}
                      >
                        <td>
                          <div className="action-title">
                            {row.name || "Research"}
                            {rowKey ? (
                              <span className="action-sub tabular">{` (${rowKey})`}</span>
                            ) : null}
                            {row.queued_count > 0 || row.active ? (
                              <span className="rate-chip">
                                {row.active ? "ACTIVE" : `Q x${row.queued_count}`}
                              </span>
                            ) : null}
                          </div>
                        </td>

                        <td className="col-right tabular">{String(curLvl)}</td>
                        <td className="col-right tabular">
                          {`L${row.next_level ?? row.target_level ?? "—"}`}
                        </td>

                        <td className="tabular">
                          {missTotal === 0 ? (
                            <span className="status-word">Affordable</span>
                          ) : (
                            formatResourcesInline(row.missing)
                          )}
                        </td>

                        <td className="col-right tabular">{row.build_time_text ?? "—"}</td>
                        <td className="col-right tabular">
                          {bnTag ? `${waitEtaText} (${bnTag})` : waitEtaText}
                        </td>

                        <td className="col-right tabular">
                          <span
                            className={`eta-pill${reachEtaText === "—" ? " is-blocked" : rowWaitH > 0 ? " is-wait" : ""}`}
                          >
                            {reachEtaText}
                          </span>
                        </td>

                        <td className="col-right">
                          <div className="row-actions-wrap">
                            {canExec ? (
                              <button
                                type="button"
                                className="btn btn-primary btn-sm"
                                onClick={(ev) => handleRowLiveResearch(ev, row)}
                              >
                                Research
                              </button>
                            ) : Array.isArray(row.unmet_prerequisites) &&
                              row.unmet_prerequisites.length > 0 ? (
                              <span className="action-sub">Locked</span>
                            ) : null}

                            {unitId != null && nextLvl !== null && nextLvl > 0 ? (
                              <button
                                type="button"
                                className="btn btn-secondary btn-sm"
                                title={`Add ${safeName} L${nextLvl} to P1 priority queue`}
                                aria-label={`Add ${safeName} L${nextLvl} to P1 priority queue`}
                                onClick={(ev) => handleRowPinQueue(ev, row)}
                              >
                                + Queue
                              </button>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <BuildingInspector
            etaData={etaData}
            selectedRow={selectedRow}
            bfPct={bfPct}
            mode="research"
            universeMaxRow={selectedUniverseMaxRow}
          />
        </div>

        {/* Universe Max Research Levels Sub-Tab */}
        <div
          className="intel-pane"
          id="res-pane-universe-research"
          hidden={!isUniverseResearchTab}
        >
          <div className="window-bar window-bar-wrap">
            <div className="window-title-group">
              <span className="module-code tabular">UNV</span>
              <h3>Universe Max Research Levels</h3>
              <span className="module-meta tabular">
                {`${universeMaxRows.length} TECHNOLOGIES`}
              </span>
            </div>
            <div className="inline-actions">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-res-refresh-universe-research"
                disabled={syncingUniverseMax}
                onClick={handleRefreshUniverseResearch}
              >
                {syncingUniverseMax ? "Syncing..." : "Refresh Max Research"}
              </button>
            </div>
          </div>

          <div className="table-wrap table-scroll">
            <table id="res-universe-research-table">
              <thead>
                <tr>
                  <SortableTh
                    label="Research"
                    sortKey="research"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                  />
                  <SortableTh
                    label="Your Level"
                    sortKey="ownLevel"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Universe Max"
                    sortKey="uniMax"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Delta / Gap"
                    sortKey="gap"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Spied Max"
                    sortKey="spiedMax"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Record Holder"
                    sortKey="holder"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                  />
                  <SortableTh
                    label="Catalog Cap"
                    sortKey="catalogCap"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Updated"
                    sortKey="updated"
                    activeKey={uniMaxSort.sortKey}
                    direction={uniMaxSort.sortDirection}
                    onSort={uniMaxSort.requestSort}
                    align="right"
                  />
                </tr>
              </thead>
              <tbody id="res-universe-research-tbody">
                {uniMaxSort.sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      Click Refresh Max Research to compute and persist universe research records.
                    </td>
                  </tr>
                ) : (
                  uniMaxSort.sortedRows.map((r, idx) => {
                    const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
                    const uniMax = Math.max(
                      ownLvl,
                      Math.round(finiteNum(r.universe_max_level, ownLvl))
                    );
                    const spiedMax = Math.max(0, Math.round(finiteNum(r.spied_max_level, 0)));
                    const catCap = finiteNum(r.catalog_max_level, null);
                    const gap = Math.max(0, finiteNum(r.gap_to_max, uniMax - ownLvl));
                    const isLeader = uniMax > 0 && ownLvl >= uniMax;
                    const holderStr = r.holder
                      ? `${r.holder}${r.holder_coords ? ` [${r.holder_coords}]` : ""}`
                      : isLeader
                        ? "You"
                        : "—";

                    return (
                      <tr key={r.key || r.slug || idx}>
                        <td>
                          <div className="action-title">
                            {r.name || r.slug || "Research"}
                            {r.key ? (
                              <span className="action-sub tabular">{` (${r.key})`}</span>
                            ) : null}
                          </div>
                        </td>
                        <td className="col-right tabular">{`L${ownLvl}`}</td>
                        <td className="col-right tabular">
                          <span className={`eta-pill${isLeader ? "" : gap > 0 ? " is-wait" : ""}`}>
                            {`L${uniMax}`}
                          </span>
                        </td>
                        <td className="col-right tabular">
                          {isLeader ? "Leader (0)" : gap > 0 ? `-${gap} Lvl` : "0"}
                        </td>
                        <td className="col-right tabular">{`L${spiedMax}`}</td>
                        <td>{holderStr}</td>
                        <td className="col-right tabular">
                          {catCap !== null && catCap > 0 ? `L${Math.round(catCap)}` : "—"}
                        </td>
                        <td className="col-right tabular">{fmtShortDateTime(r.updated_ts)}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </article>
    </section>
  );
};

export default ResearchView;
