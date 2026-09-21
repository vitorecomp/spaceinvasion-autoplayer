import React, { useCallback, useMemo } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import {
  durationToSeconds,
  resourcesTotal,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  bottleneckTag,
  clampPct,
  finiteNum,
  fmtInt,
  formatResourcesInline,
  getSelectedBlastFurnacePct,
} from "../../utils/formatters";
import { BuildingInspector } from "./BuildingInspector";

const BUILDING_CATEGORY_TABS = [
  { id: "all", bcat: "buildings", label: "All 21" },
  { id: "mines", bcat: "mines", label: "Mines" },
  { id: "energy", bcat: "energy", label: "Energy" },
  { id: "infra", bcat: "infra", label: "Infra" },
  { id: "storage", bcat: "storage", label: "Storage" },
  { id: "special", bcat: "special", label: "Special" },
  { id: "defense", bcat: "defense", label: "Defense" },
] as const;

const VALID_BUILDING_CATEGORIES = new Set<string>(BUILDING_CATEGORY_TABS.map((t) => t.id));

export const BuildingsView: React.FC = () => {
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
    combatData,
    toggleBlastFurnaceRate,
    enqueueManualCommand,
  } = useConsole();

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

  const cmbData = useMemo(() => {
    if (combatData && activePlanet && combatData.planet_id === activePlanet.planet_id) {
      return combatData;
    }
    return dashboard?.combat || null;
  }, [activePlanet, combatData, dashboard?.combat]);

  const rawCategory = uiPrefs.bldCategory || "all";
  const normalizedCategory = rawCategory === "buildings" ? "all" : rawCategory;
  const bldCategory = VALID_BUILDING_CATEGORIES.has(normalizedCategory)
    ? normalizedCategory
    : "all";
  const isDefenseTab = bldCategory === "defense";

  const p = etaData?.planet || {};
  const econ = etaData?.planet_economy || {};
  const bldDiv = finiteNum(p.building_time_divisor, 1);
  const planetMetaText = etaData
    ? `[${p.coords || "--"}] · Dev L${finiteNum(p.dev_center_level, 0)} · Micro L${finiteNum(p.microsystem_level, 0)} · Div x${bldDiv}`
    : "[--] · Dev L0 · Micro L0 · Div x1";

  const econBfPct = finiteNum(econ.blast_furnace_pct, null);
  const bfPct =
    econBfPct !== null
      ? clampPct(Math.round(econBfPct))
      : getSelectedBlastFurnacePct(dashboard, selectedPlanetId, buildingsEtaData);

  const defenseRows = useMemo(() => {
    const blds = Array.isArray(etaData?.buildings) ? etaData!.buildings : [];
    const defBuildings = blds.filter(
      (b: any) => b?.building_id === 15 || b?.building_id === 16 || b?.category === "defense"
    );
    const turrets = Array.isArray(cmbData?.defenses)
      ? cmbData!.defenses.map((d: any) => ({
          key: d.key,
          unit_id: d.unit_id,
          name: d.name,
          category: "defense",
          is_defense_turret: true,
          current_level: finiteNum(d.owned, 0),
          next_level: finiteNum(d.owned, 0) + 1,
          cost: d.base_cost || {},
          missing: {},
          missing_total: 0,
          build_time_text: "Shipyard",
          wait_eta: "—",
          reach_eta: `${fmtInt(d.owned ?? 0)} owned`,
          wait_hours: 0,
          attack: d.attack,
          shield: d.shield,
          hull: d.hull,
          horizons: [],
        }))
      : [];
    return [...defBuildings, ...turrets];
  }, [cmbData, etaData]);

  const visibleRows = useMemo(() => {
    if (!etaData) return [];
    if (isDefenseTab) {
      return defenseRows;
    }
    const allBlds = Array.isArray(etaData.buildings) ? etaData.buildings : [];
    if (bldCategory === "all") {
      return allBlds;
    }
    return allBlds.filter((b: any) => b?.category === bldCategory);
  }, [bldCategory, defenseRows, etaData, isDefenseTab]);

  const allInspectableRows = useMemo(() => {
    if (!etaData) return [];
    return [
      ...(Array.isArray(etaData.buildings) ? etaData.buildings : []),
      ...defenseRows.filter((d) => d.is_defense_turret),
    ];
  }, [defenseRows, etaData]);

  const selectedUnitKey = uiPrefs.selectedUnitKey || "b1";
  const selectedRow = useMemo(() => {
    if (!etaData) return null;
    const hit = allInspectableRows.find((r: any) => r?.key === selectedUnitKey);
    if (hit) return hit;
    return visibleRows[0] || allInspectableRows[0] || null;
  }, [allInspectableRows, etaData, selectedUnitKey, visibleRows]);

  const unitSortCols = useMemo<
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
        accessor: (row) =>
          row.is_defense_turret ? null : finiteNum(row.next_level ?? row.target_level, null),
        defaultDirection: "desc",
      },
      missing: (row) =>
        row.is_defense_turret
          ? resourcesTotal(row.cost)
          : (finiteNum(row.missing_total, null) ?? resourcesTotal(row.missing) ?? 0),
      buildTime: (row) =>
        finiteNum(row.build_seconds ?? row.build_time_seconds, null) ??
        durationToSeconds(row.build_time_text),
      resReady: (row) => {
        if (row.is_defense_turret || row.wait_eta === "—") return null;
        const wh = finiteNum(row.wait_hours, null);
        if (wh !== null) return wh * 3600;
        return durationToSeconds(row.wait_eta);
      },
      reached: (row) => {
        if (row.is_defense_turret || row.reach_eta === "—") return null;
        const rh = finiteNum(row.reach_hours ?? row.total_hours, null);
        if (rh !== null) return rh * 3600;
        return durationToSeconds(row.reach_eta);
      },
    }),
    []
  );

  const unitSort = useTableSort(visibleRows, unitSortCols);

  const handleSyncEconomy = useCallback(async () => {
    try {
      await apiCommand({ command: "navigate", action: "internalEconomy", return_home: true });
      showFeedback("Synced live internalEconomy production percentages.", "ok");
      await loadDashboard({ refresh: true, force: true });
      await refreshBuildingsEtaForSelectedPlanet(false);
    } catch (err: any) {
      showFeedback(`Sync failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [apiCommand, loadDashboard, refreshBuildingsEtaForSelectedPlanet, showFeedback]);

  const handleRowLiveBuild = useCallback(
    async (ev: React.MouseEvent, row: Record<string, any>) => {
      ev.stopPropagation();
      try {
        const res = await apiCommand({
          command: "build",
          building_id: row.building_id,
          planet_id: p.planet_id,
          dry_run: false,
        });
        showFeedback(res.message || "Queued.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err: any) {
        showFeedback(`Failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, loadDashboard, p.planet_id, showFeedback]
  );

  const handleRowPinQueue = useCallback(
    (ev: React.MouseEvent, row: Record<string, any>) => {
      ev.stopPropagation();
      const unitId = row.building_id;
      const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
      if (unitId == null || nextLvl === null || nextLvl <= 0) return;
      void enqueueManualCommand({
        kind: "BUILD",
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

  const handleRowDemolishB6 = useCallback(
    async (ev: React.MouseEvent, row: Record<string, any>) => {
      ev.stopPropagation();
      try {
        const curLvl = finiteNum(row.current_level, 0);
        showFeedback(`Demolishing ${row.name || "Thermosolar plant"} L${curLvl}...`, "ok");
        const res = await apiCommand({
          command: "demolish_building",
          building_id: row.building_id,
          planet_id: p.planet_id,
          dry_run: false,
        });
        showFeedback(res.message || "Demolish queued.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err: any) {
        showFeedback(`Demolish failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, loadDashboard, p.planet_id, showFeedback]
  );

  return (
    <section
      id="view-buildings"
      className="page-view"
      aria-label="Buildings and reach ETAs"
      hidden={activePage !== "buildings"}
    >
      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">BLD</span>
            <h2>Buildings — Level Reach Estimates</h2>
            <span className="module-meta tabular" id="bld-planet-meta">
              {planetMetaText}
            </span>
          </div>

          <div className="filter-toolbar">
            <div
              className="tab-group"
              id="bld-category-filters"
              role="group"
              aria-label="Building category filters"
            >
              {BUILDING_CATEGORY_TABS.map((tab) => {
                const isActive = bldCategory === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    className={`chip-btn${isActive ? " active" : ""}`}
                    data-bld-cat={tab.id}
                    data-bcat={tab.bcat}
                    onClick={() => setUiPref("bldCategory", tab.id)}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>

            <div className="inline-actions">
              <button
                type="button"
                className="btn btn-warning btn-sm"
                id="btn-b2-pause"
                title="Set Blast furnace to 0% to boost Pig iron production"
                disabled={Boolean(etaData) && bfPct === 0}
                onClick={() => toggleBlastFurnaceRate(0)}
              >
                Blast Furnace 0% (+Pig)
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-b2-half"
                title="Set Blast furnace smelting to 50%"
                disabled={Boolean(etaData) && bfPct === 50}
                onClick={() => toggleBlastFurnaceRate(50)}
              >
                50%
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-b2-resume"
                title="Resume Blast furnace smelting to 100%"
                disabled={Boolean(etaData) && bfPct === 100}
                onClick={() => toggleBlastFurnaceRate(100)}
              >
                100% (Metal)
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-sync-economy"
                title="Read internalEconomy select rates from Chrome"
                onClick={handleSyncEconomy}
              >
                Sync %
              </button>
            </div>
          </div>
        </div>

        <div className="split-workspace">
          <div className="table-wrap table-scroll">
            <table id="bld-table">
              <thead>
                <tr>
                  <SortableTh
                    label="Unit"
                    sortKey="unit"
                    activeKey={unitSort.sortKey}
                    direction={unitSort.sortDirection}
                    onSort={unitSort.requestSort}
                  />
                  <SortableTh
                    label="Lvl"
                    sortKey="level"
                    activeKey={unitSort.sortKey}
                    direction={unitSort.sortDirection}
                    onSort={unitSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Next"
                    sortKey="next"
                    activeKey={unitSort.sortKey}
                    direction={unitSort.sortDirection}
                    onSort={unitSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Missing Resources"
                    sortKey="missing"
                    activeKey={unitSort.sortKey}
                    direction={unitSort.sortDirection}
                    onSort={unitSort.requestSort}
                  />
                  <SortableTh
                    label="Build Time"
                    sortKey="buildTime"
                    activeKey={unitSort.sortKey}
                    direction={unitSort.sortDirection}
                    onSort={unitSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Res Ready"
                    sortKey="resReady"
                    activeKey={unitSort.sortKey}
                    direction={unitSort.sortDirection}
                    onSort={unitSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Level Reached"
                    sortKey="reached"
                    activeKey={unitSort.sortKey}
                    direction={unitSort.sortDirection}
                    onSort={unitSort.requestSort}
                    align="right"
                  />
                  <th className="col-right">Control</th>
                </tr>
              </thead>
              <tbody id="eta-table-tbody">
                {!etaData ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      Waiting for building catalog...
                    </td>
                  </tr>
                ) : unitSort.sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      No items in selected category.
                    </td>
                  </tr>
                ) : (
                  unitSort.sortedRows.map((row: any, idx: number) => {
                    const rowKey = row.key != null ? String(row.key).trim() : "";
                    const isSelected = Boolean(selectedRow && selectedRow.key === row.key);
                    const curLvl = finiteNum(row.current_level, 0);
                    const rowPct = finiteNum(row.economy_rate_pct, null);
                    const clampedRowPct = rowPct !== null ? clampPct(Math.round(rowPct)) : null;
                    const missTotal = finiteNum(row.missing_total, 0);
                    const waitEtaText = row.wait_eta ?? "—";
                    const bnTag =
                      typeof row.bottleneck === "string" && row.bottleneck.trim()
                        ? bottleneckTag(row.bottleneck)
                        : "";
                    const reachEtaText = row.reach_eta ?? "—";
                    const rowWaitH = finiteNum(row.wait_hours, 0);
                    const isTurret = Boolean(row.is_defense_turret);
                    const canExec = isTurret ? false : Boolean(row.can_build_live);
                    const unitId = row.building_id;
                    const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
                    const safeName = String(row.name || "Unit").replace(/>/g, "→");

                    return (
                      <tr
                        key={rowKey || `unit-${idx}`}
                        className={`clickable-row${isSelected ? " row-selected" : ""}`}
                        onClick={() => {
                          if (rowKey) {
                            setUiPref("selectedUnitKey", rowKey);
                          }
                        }}
                      >
                        <td>
                          <div className="action-title">
                            {row.name || "Unit"}
                            {rowKey ? (
                              <span className="action-sub tabular">{` (${rowKey})`}</span>
                            ) : null}
                            {row.queued_count > 0 || row.active ? (
                              <span className="rate-chip">
                                {row.active ? "ACTIVE" : `Q x${row.queued_count}`}
                              </span>
                            ) : null}
                            {clampedRowPct !== null && curLvl > 0 ? (
                              <span
                                className={`rate-chip tabular${clampedRowPct < 100 ? " is-paused" : ""}`}
                              >
                                {`${clampedRowPct}%`}
                              </span>
                            ) : null}
                          </div>
                        </td>

                        <td className="col-right tabular">{String(curLvl)}</td>
                        <td className="col-right tabular">
                          {isTurret ? "—" : `L${row.next_level ?? row.target_level ?? "—"}`}
                        </td>

                        <td className="tabular">
                          {isTurret ? (
                            formatResourcesInline(row.cost)
                          ) : missTotal === 0 ? (
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
                                onClick={(ev) => handleRowLiveBuild(ev, row)}
                              >
                                Build
                              </button>
                            ) : Array.isArray(row.unmet_prerequisites) &&
                              row.unmet_prerequisites.length > 0 ? (
                              <span className="action-sub">Locked</span>
                            ) : null}

                            {!isTurret && unitId != null && nextLvl !== null && nextLvl > 0 ? (
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

                            {!isTurret && row.building_id === 6 && curLvl > 0 ? (
                              <button
                                type="button"
                                className="btn btn-secondary btn-sm btn-danger-ghost"
                                title="Demolish Thermosolar plant (b6)"
                                onClick={(ev) => handleRowDemolishB6(ev, row)}
                              >
                                Demolish
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
            mode="buildings"
          />
        </div>
      </article>
    </section>
  );
};

export default BuildingsView;
