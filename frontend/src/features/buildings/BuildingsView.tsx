import React, { useCallback, useMemo } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import {
  useActivePage,
  useActivePlanet,
  useBuildingsEtaData,
  useCombatData,
  useConsoleActions,
  useDashboard,
  useSelectedPlanetId,
  useUiPrefs,
} from "../../store";
import {
  clampPct,
  finiteNum,
  getSelectedBlastFurnacePct,
} from "../../utils/formatters";
import { BuildingInspector } from "./BuildingInspector";
import { BuildingsEtaTable } from "./BuildingsEtaTable";
import {
  BUILDING_CATEGORY_TABS,
  VALID_BUILDING_CATEGORIES,
  buildDefenseRows,
  formatSpecializationLabel,
  resolvePlanetGoalEntry,
} from "./buildingViewHelpers";

export const BuildingsView: React.FC = () => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const selectedPlanetId = useSelectedPlanetId();
  const activePlanet = useActivePlanet();
  const uiPrefs = useUiPrefs();
  const buildingsEtaData = useBuildingsEtaData();
  const combatData = useCombatData();
  const {
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
    refreshBuildingsEtaForSelectedPlanet,
    toggleBlastFurnaceRate,
    enqueueManualCommand,
  } = useConsoleActions();

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
  const coGoals =
    (etaData as any)?.config_optimizer_goals ?? dashboard?.config_optimizer_goals ?? null;
  const planetGoalEntry = useMemo(
    () =>
      resolvePlanetGoalEntry(
        coGoals,
        p.planet_id ?? activePlanet?.planet_id,
        p.coords || activePlanet?.coords
      ),
    [activePlanet?.coords, activePlanet?.planet_id, coGoals, p.coords, p.planet_id]
  );
  const planetGoalsMap: Record<string, number> | null =
    planetGoalEntry?.goals && typeof planetGoalEntry.goals === "object"
      ? planetGoalEntry.goals
      : null;
  const planetGoalSpec = formatSpecializationLabel(planetGoalEntry?.specialization);
  const getGoalLevelForRow = useCallback(
    (row: Record<string, any> | null | undefined): number | null => {
      if (!row || row.is_defense_turret || !planetGoalsMap) return null;
      const rk = String(row.key || (row.building_id != null ? `b${row.building_id}` : ""))
        .trim()
        .toLowerCase();
      if (!rk || !(rk in planetGoalsMap)) return null;
      return finiteNum(planetGoalsMap[rk], null);
    },
    [planetGoalsMap]
  );
  const bldCustomName = useMemo(() => {
    const pid = p.planet_id ?? activePlanet?.planet_id;
    const customMap = dashboard?.custom_planet_names;
    const fromMap =
      pid != null && customMap && typeof customMap === "object" ? customMap[String(pid)] : "";
    return String(fromMap || p.custom_name || activePlanet?.custom_name || "").trim();
  }, [
    activePlanet?.custom_name,
    activePlanet?.planet_id,
    dashboard?.custom_planet_names,
    p.custom_name,
    p.planet_id,
  ]);
  const planetMetaText = etaData
    ? `${bldCustomName ? `${bldCustomName} ` : ""}[${p.coords || "--"}] · Dev L${finiteNum(p.dev_center_level, 0)} · Micro L${finiteNum(p.microsystem_level, 0)} · Div x${bldDiv}${planetGoalSpec ? ` · Goal: ${planetGoalSpec}` : ""}`
    : "[--] · Dev L0 · Micro L0 · Div x1";

  const econBfPct = finiteNum(econ.blast_furnace_pct, null);
  const bfPct =
    econBfPct !== null
      ? clampPct(Math.round(econBfPct))
      : getSelectedBlastFurnacePct(dashboard, selectedPlanetId, buildingsEtaData);

  const defenseRows = useMemo(() => buildDefenseRows(etaData, cmbData), [cmbData, etaData]);

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

  const handleSelectUnit = useCallback(
    (rowKey: string) => {
      setUiPref("selectedUnitKey", rowKey);
    },
    [setUiPref]
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
          <BuildingsEtaTable
            hasEtaData={Boolean(etaData)}
            visibleRows={visibleRows}
            selectedRow={selectedRow}
            getGoalLevelForRow={getGoalLevelForRow}
            onSelectUnit={handleSelectUnit}
            onLiveBuild={handleRowLiveBuild}
            onPinQueue={handleRowPinQueue}
            onDemolishB6={handleRowDemolishB6}
          />

          <BuildingInspector
            etaData={etaData}
            selectedRow={selectedRow}
            bfPct={bfPct}
            mode="buildings"
            goalLevel={getGoalLevelForRow(selectedRow)}
            goalSpecialization={planetGoalSpec || null}
          />
        </div>
      </article>
    </section>
  );
};

export default BuildingsView;
