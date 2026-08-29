import React, { useCallback, useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import {
  bottleneckTag,
  clampPct,
  finiteNum,
  fmtInt,
  fmtShortDateTime,
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
  { id: "research", bcat: "research", label: "Research r1–15" },
  { id: "universe_research", bcat: "universe_research", label: "Universe Max Research" },
] as const;

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

  const [syncingUniverseMax, setSyncingUniverseMax] = useState(false);

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
  const bldCategory = rawCategory === "buildings" ? "all" : rawCategory;
  const isUniverseResearchTab = bldCategory === "universe_research";
  const isResearchTab = bldCategory === "research";
  const isDefenseTab = bldCategory === "defense";

  const p = etaData?.planet || {};
  const econ = etaData?.planet_economy || {};
  const bldDiv = finiteNum(p.building_time_divisor, 1);
  const planetMetaText = etaData
    ? `[${p.coords || "--"}] · Dev L${finiteNum(p.dev_center_level, 0)} · Lab L${finiteNum(p.research_lab_level, 0)} · Micro L${finiteNum(p.microsystem_level, 0)} · Div x${bldDiv}`
    : "[--] · Dev L0 · Lab L0 · x1";

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
    if (isResearchTab) {
      return Array.isArray(etaData.researches) ? etaData.researches : [];
    }
    if (isDefenseTab) {
      return defenseRows;
    }
    const allBlds = Array.isArray(etaData.buildings) ? etaData.buildings : [];
    if (bldCategory === "all") {
      return allBlds;
    }
    return allBlds.filter((b: any) => b?.category === bldCategory);
  }, [bldCategory, defenseRows, etaData, isDefenseTab, isResearchTab]);

  const allInspectableRows = useMemo(() => {
    if (!etaData) return [];
    return [
      ...(Array.isArray(etaData.buildings) ? etaData.buildings : []),
      ...(Array.isArray(etaData.researches) ? etaData.researches : []),
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

  const universeMaxRows: Array<Record<string, any>> = useMemo(
    () =>
      Array.isArray(dashboard?.universe_research_max) ? dashboard!.universe_research_max! : [],
    [dashboard?.universe_research_max]
  );

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

  const handleRowLiveBuild = useCallback(
    async (ev: React.MouseEvent, row: Record<string, any>, rowIsRes: boolean) => {
      ev.stopPropagation();
      const cmd = rowIsRes
        ? {
            command: "research",
            research_id: row.research_id,
            planet_id: p.planet_id,
            dry_run: false,
          }
        : {
            command: "build",
            building_id: row.building_id,
            planet_id: p.planet_id,
            dry_run: false,
          };
      try {
        const res = await apiCommand(cmd);
        showFeedback(res.message || "Queued.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err: any) {
        showFeedback(`Failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, loadDashboard, p.planet_id, showFeedback]
  );

  const handleRowPinQueue = useCallback(
    (ev: React.MouseEvent, row: Record<string, any>, rowIsRes: boolean) => {
      ev.stopPropagation();
      const unitId = rowIsRes ? row.research_id : row.building_id;
      const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
      if (unitId == null || nextLvl === null || nextLvl <= 0) return;
      void enqueueManualCommand({
        kind: rowIsRes ? "RESEARCH" : "BUILD",
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
            <h2>Buildings &amp; Research — Level Reach Estimates</h2>
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

        {/* Standard Buildings / Research / Defense Split Workspace */}
        <div className="split-workspace" hidden={isUniverseResearchTab}>
          <div className="table-wrap table-scroll">
            <table id="bld-table">
              <thead>
                <tr>
                  <th>Unit</th>
                  <th className="col-right">Lvl</th>
                  <th className="col-right">Next</th>
                  <th>Missing Resources</th>
                  <th className="col-right">Build Time</th>
                  <th className="col-right">Res Ready</th>
                  <th className="col-right">Level Reached</th>
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
                ) : visibleRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      No items in selected category.
                    </td>
                  </tr>
                ) : (
                  visibleRows.map((row: any, idx: number) => {
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
                    const rowIsRes = rowKey.startsWith("r") || isResearchTab;
                    const isTurret = Boolean(row.is_defense_turret);
                    const canExec = isTurret
                      ? false
                      : Boolean(rowIsRes ? row.can_research_live : row.can_build_live);
                    const unitId = rowIsRes ? row.research_id : row.building_id;
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
                                onClick={(ev) => handleRowLiveBuild(ev, row, rowIsRes)}
                              >
                                {rowIsRes ? "Research" : "Build"}
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
                                onClick={(ev) => handleRowPinQueue(ev, row, rowIsRes)}
                              >
                                + Queue
                              </button>
                            ) : null}

                            {!rowIsRes && !isTurret && row.building_id === 6 && curLvl > 0 ? (
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

          <BuildingInspector etaData={etaData} selectedRow={selectedRow} bfPct={bfPct} />
        </div>

        {/* Universe Max Research Levels Tab (#16 + #18) */}
        <div className="intel-pane" id="bld-pane-universe-research" hidden={!isUniverseResearchTab}>
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
                id="btn-bld-refresh-universe-research"
                disabled={syncingUniverseMax}
                onClick={handleRefreshUniverseResearch}
              >
                {syncingUniverseMax ? "Syncing..." : "Refresh Max Research"}
              </button>
            </div>
          </div>

          <div className="table-wrap table-scroll">
            <table id="bld-universe-research-table">
              <thead>
                <tr>
                  <th>Research</th>
                  <th className="col-right">Your Level</th>
                  <th className="col-right">Universe Max</th>
                  <th className="col-right">Delta / Gap</th>
                  <th className="col-right">Spied Max</th>
                  <th>Record Holder</th>
                  <th className="col-right">Catalog Cap</th>
                  <th className="col-right">Updated</th>
                </tr>
              </thead>
              <tbody id="bld-universe-research-tbody">
                {universeMaxRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      Click Refresh Max Research to compute and persist universe research records.
                    </td>
                  </tr>
                ) : (
                  universeMaxRows.map((r, idx) => {
                    const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
                    const uniMax = Math.max(ownLvl, Math.round(finiteNum(r.universe_max_level, ownLvl)));
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

export default BuildingsView;
