import React, { useCallback, useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import {
  useActivePage,
  useActivePlanet,
  useBuildingsEtaData,
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
import { BuildingInspector } from "../buildings/BuildingInspector";
import { ResearchEtaTable } from "./ResearchEtaTable";
import { UniverseResearchPane } from "./UniverseResearchPane";

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
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const selectedPlanetId = useSelectedPlanetId();
  const activePlanet = useActivePlanet();
  const uiPrefs = useUiPrefs();
  const buildingsEtaData = useBuildingsEtaData();
  const {
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
    refreshBuildingsEtaForSelectedPlanet,
    enqueueManualCommand,
  } = useConsoleActions();

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
  const resCustomName = useMemo(() => {
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
    ? `${resCustomName ? `${resCustomName} ` : ""}[${p.coords || "--"}] · Lab L${finiteNum(p.research_lab_level, 0)} · Micro L${finiteNum(p.microsystem_level, 0)} · Div x${resDiv}`
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

  const handleSelectResearch = useCallback(
    (rowKey: string) => {
      setPrefAny("selectedResearchKey", rowKey);
    },
    [setPrefAny]
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
          <ResearchEtaTable
            hasEtaData={Boolean(etaData)}
            visibleRows={visibleRows}
            selectedRow={selectedRow}
            onSelectResearch={handleSelectResearch}
            onLiveResearch={handleRowLiveResearch}
            onPinQueue={handleRowPinQueue}
          />

          <BuildingInspector
            etaData={etaData}
            selectedRow={selectedRow}
            bfPct={bfPct}
            mode="research"
            universeMaxRow={selectedUniverseMaxRow}
          />
        </div>

        {/* Universe Max Research Levels Sub-Tab */}
        <UniverseResearchPane
          hidden={!isUniverseResearchTab}
          universeMaxRows={universeMaxRows}
          syncingUniverseMax={syncingUniverseMax}
          onRefreshUniverseResearch={handleRefreshUniverseResearch}
        />
      </article>
    </section>
  );
};

export default ResearchView;
