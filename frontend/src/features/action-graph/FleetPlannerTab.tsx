import React, { useCallback, useEffect, useMemo, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { useActivePage, useApiOnline, useConsoleActions, useDashboard } from "../../store";
import type {
  FleetPlannerGetPayload,
  PlannedActionData,
  PlanetData,
  QueueJobData,
} from "../../types/api";
import { fmtCompact, fmtInt } from "../../utils/formatters";
import { DagCanvas } from "./DagCanvas";
import {
  FleetDraftState,
  FleetSlotBudgetRow,
  buildClientFallbackFleetGraph,
  buildConfigPatchFromDraft,
  buildFleetDraftFromConfig,
  computeFleetSlotRows,
  computeOngoingFlights,
} from "./fleetPlannerHelpers";
import { FleetParamTab, FleetPlannerParamPanel } from "./FleetPlannerParamPanel";
import { FleetPlannerTables } from "./FleetPlannerTables";
import { formatDurationSecondsCompact, normalizeUiPlannerMode } from "./plannerHelpers";

export interface FleetPlannerTabProps {
  hidden?: boolean;
}

export const FleetPlannerTab: React.FC<FleetPlannerTabProps> = ({ hidden = false }) => {
  const apiOnline = useApiOnline();
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const {
    setDashboard,
    setConfigDirty,
    setConfigSaving,
    bumpDashboardSeq,
    apiCommand,
    loadDashboard,
    showFeedback,
    bumpConfigHydration,
  } = useConsoleActions();

  const [fleetData, setFleetData] = useState<FleetPlannerGetPayload | null>(null);
  const [draft, setDraft] = useState<FleetDraftState>(() =>
    buildFleetDraftFromConfig(dashboard?.config)
  );
  const [draftDirty, setDraftDirty] = useState<boolean>(false);
  const [paramGroupTab, setParamGroupTab] = useState<FleetParamTab>("combat");
  const [edgeFilter, setEdgeFilter] = useState<"all" | "deps">("all");
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [isSimulating, setIsSimulating] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<string>(
    "Fleet & attack controls stay synchronized with global Config."
  );

  useEffect(() => {
    if (!draftDirty && dashboard?.config) {
      setDraft(buildFleetDraftFromConfig(dashboard.config));
    }
  }, [dashboard?.config, draftDirty]);

  const fetchFleetPlanner = useCallback(
    async ({
      previewConfig = null,
      silent = false,
    }: { previewConfig?: Record<string, any> | null; silent?: boolean } = {}) => {
      try {
        const payload: Record<string, any> = { command: "fleet_planner_get" };
        if (previewConfig) payload.config = previewConfig;
        const data = (await apiCommand(payload, { timeoutMs: 20000 })) as FleetPlannerGetPayload;
        if (data && data.ok) {
          setFleetData(data);
          if (!previewConfig && !draftDirty && data.config) {
            setDraft(buildFleetDraftFromConfig(data.config));
          }
          if (!silent) {
            setStatusMessage(
              previewConfig
                ? "Simulated fleet plan with draft parameters."
                : "Fleet planner refreshed."
            );
          }
        }
      } catch (err) {
        if (!silent) {
          showFeedback(
            `Fleet planner load failed: ${(err as Error).message}`,
            feedbackKindForError(err)
          );
        }
      }
    },
    [apiCommand, draftDirty, showFeedback]
  );

  useEffect(() => {
    if (hidden || activePage !== "action-graph") return;
    void fetchFleetPlanner({ silent: true });
  }, [hidden, activePage, fetchFleetPlanner]);

  const customPlanetNames = useMemo<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const src of [
      (dashboard as Record<string, any> | null | undefined)?.custom_planet_names,
      (fleetData as Record<string, any> | null | undefined)?.custom_planet_names,
    ]) {
      if (!src || typeof src !== "object") continue;
      for (const [k, v] of Object.entries(src as Record<string, unknown>)) {
        const clean = String(v || "").trim();
        if (clean) out[String(k)] = clean;
      }
    }
    return out;
  }, [dashboard, fleetData]);

  const slotRows: FleetSlotBudgetRow[] = useMemo(
    () => computeFleetSlotRows(fleetData, dashboard, customPlanetNames),
    [fleetData, dashboard, customPlanetNames]
  );

  const ongoingFlights = useMemo(
    () => computeOngoingFlights(dashboard, customPlanetNames),
    [dashboard, customPlanetNames]
  );

  const activeGraph = useMemo(() => {
    if (fleetData?.graph && typeof fleetData.graph === "object" && Array.isArray(fleetData.graph.nodes)) {
      return fleetData.graph;
    }
    const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets)
      ? dashboard!.state!.planets!
      : [];
    const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
      ? dashboard!.state!.active_queues!
      : [];
    const plan: PlannedActionData[] = Array.isArray(dashboard?.plan) ? dashboard!.plan! : [];
    return buildClientFallbackFleetGraph(planets, activeQueues, plan, slotRows);
  }, [fleetData?.graph, dashboard, slotRows]);

  const totalSlotsUsed = slotRows.reduce((acc, r) => acc + r.slots_used, 0);
  const totalSlotsMax = slotRows.reduce((acc, r) => acc + r.slots_max, 0);
  const totalDockedShips = slotRows.reduce((acc, r) => acc + r.docked_ships_total, 0);
  const activeMissionsCount = Array.isArray(activeGraph?.nodes) ? activeGraph.nodes.length : 0;

  const telemetry =
    fleetData?.attack_telemetry || dashboard?.ai_planner_summary?.attack_telemetry || {};
  const sparePct =
    safeFiniteNumber(
      draft.numbers.ai_resource_spare_pct,
      safeFiniteNumber(
        fleetData?.spare_budget?.spare_percent ?? fleetData?.spare_budget?.ai_resource_spare_pct,
        10
      ) ?? 10
    ) ?? 10;
  const effMode = normalizeUiPlannerMode(
    dashboard?.effective_planner_mode || dashboard?.config?.planner_mode || dashboard?.planner_mode
  );
  const lootPerHour = safeFiniteNumber(
    telemetry?.hourly_loot_spice_eq ??
      telemetry?.hourly_loot?.spice_eq ??
      telemetry?.median_loot_spice_eq_per_hour ??
      telemetry?.median_loot_per_hour ??
      telemetry?.median_loot?.spice_eq,
    null
  );
  const medianRtSec = safeFiniteNumber(
    telemetry?.median_round_trip_seconds ?? telemetry?.median_roundtrip_seconds,
    null
  );

  const handleBoolChange = (key: string, checked: boolean) => {
    setDraft((prev) => ({ ...prev, booleans: { ...prev.booleans, [key]: checked } }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const handleNumChange = (key: string, value: string) => {
    setDraft((prev) => ({ ...prev, numbers: { ...prev.numbers, [key]: value } }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const handleShipyardTargetChange = (slug: string, value: string) => {
    setDraft((prev) => ({
      ...prev,
      shipyard_targets: { ...prev.shipyard_targets, [slug]: value },
    }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const handleFleetResearchTargetChange = (rKey: string, value: string) => {
    setDraft((prev) => ({
      ...prev,
      fleet_research_targets: { ...prev.fleet_research_targets, [rKey]: value },
    }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const liveResearches: Record<string, number> =
    dashboard?.state?.researches && typeof dashboard.state.researches === "object"
      ? (dashboard.state.researches as Record<string, number>)
      : {};

  const handleSimulate = async () => {
    if (isSimulating) return;
    setIsSimulating(true);
    setStatusMessage("Simulating fleet & attack plan preview...");
    try {
      await fetchFleetPlanner({ previewConfig: buildConfigPatchFromDraft(draft), silent: false });
    } finally {
      setIsSimulating(false);
    }
  };

  const handleReset = () => {
    setDraft(buildFleetDraftFromConfig(dashboard?.config));
    setDraftDirty(false);
    setConfigDirty(false);
    setStatusMessage("Fleet controls synced back to active Config.");
    void fetchFleetPlanner({ silent: true });
  };

  const handleApplyToConfig = async () => {
    if (isApplying) return;
    setIsApplying(true);
    setConfigSaving((c) => c + 1);
    setStatusMessage("Applying fleet & logistics settings to active Config...");
    const patch = buildConfigPatchFromDraft(draft);
    try {
      const res = await apiCommand({ command: "update_config", config: patch });
      const updatedCfg = res?.config || { ...(dashboard?.config || {}), ...patch };
      setDashboard((prev) => ({ ...(prev || {}), config: updatedCfg }));
      setDraft(buildFleetDraftFromConfig(updatedCfg));
      setDraftDirty(false);
      setConfigDirty(false);
      bumpDashboardSeq();
      bumpConfigHydration();
      setStatusMessage("Saved to Config and synced bidirectionally.");
      showFeedback("Fleet planner settings applied to Config.", "ok");
      await Promise.all([
        fetchFleetPlanner({ silent: true }),
        loadDashboard({ refresh: false, preserveConfig: false, force: true }),
      ]);
    } catch (err) {
      setStatusMessage(`Save failed: ${(err as Error).message}`);
      showFeedback(
        `Failed to apply fleet config: ${(err as Error).message}`,
        feedbackKindForError(err)
      );
    } finally {
      setConfigSaving((c) => Math.max(0, c - 1));
      setIsApplying(false);
    }
  };

  return (
    <div className="ag-tab-split" id="ag-panel-fleet-planner" hidden={hidden}>
      <article className="hud-window ag-main-window" id="ag-flt-main-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">FLT</span>
            <h2>Fleet, Raids &amp; Logistics Planner</h2>
            <span
              id="flt-sync-badge"
              className={`ag-status-pill tabular ${draftDirty ? "is-sim" : "is-live"}`}
              role="status"
            >
              {draftDirty ? "PREVIEW EDITED" : "CONFIG SYNCED"}
            </span>
          </div>
          <div className="inline-actions">
            <button
              className="btn btn-secondary btn-sm"
              id="btn-flt-refresh"
              type="button"
              disabled={!apiOnline}
              onClick={() => void fetchFleetPlanner({ silent: false })}
            >
              Refresh Fleet DAG
            </button>
          </div>
        </div>

        <div className="ag-kpi-strip">
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Empire Fleet Slots</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-slots">
              {fmtInt(totalSlotsUsed)} / {fmtInt(totalSlotsMax)}
            </strong>
            <span className="ag-kpi-sub tabular">
              {slotRows.length} active planets · {fmtInt(totalDockedShips)} docked ships
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Active &amp; Planned Flights</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-missions">
              {fmtInt(activeMissionsCount)}
            </strong>
            <span className="ag-kpi-sub tabular">
              Raids: {draft.booleans.allow_raids ? "ON" : "OFF"} · Shipyard:{" "}
              {draft.booleans.allow_shipyard ? "ON" : "OFF"} · Tech:{" "}
              {draft.booleans.allow_fleet_research ? "ON" : "OFF"}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Soft Spare % Budget</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-budget">
              {fmtInt(sparePct)}%
            </strong>
            <span className="ag-kpi-sub tabular">
              Feeds {effMode === "ai_planner" ? "AI-Planner" : "Heuristic"} reserve budget
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Median Attack Telemetry</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-loot">
              {lootPerHour !== null ? `${fmtCompact(lootPerHour)}/h` : "—"}
            </strong>
            <span className="ag-kpi-sub tabular">
              Median RT: {formatDurationSecondsCompact(medianRtSec)} · Reserve:{" "}
              {fmtInt(safeFiniteNumber(draft.numbers.fleet_save_fuel_reserve_spice, 500) ?? 500)}{" "}
              Spice
            </span>
          </div>
        </div>

        <DagCanvas
          graph={activeGraph}
          idPrefix="ag-flt"
          markerPrefix="ag-flt"
          edgeFilter={edgeFilter}
          onEdgeFilterChange={setEdgeFilter}
          emptyMessage="No active flights, raids, recyclers, or shipyard builds in the current queue."
        />

        <FleetPlannerTables ongoingFlights={ongoingFlights} slotRows={slotRows} />
      </article>

      <FleetPlannerParamPanel
        draft={draft}
        draftDirty={draftDirty}
        paramGroupTab={paramGroupTab}
        onSelectParamGroupTab={setParamGroupTab}
        liveResearches={liveResearches}
        statusMessage={statusMessage}
        apiOnline={apiOnline}
        isSimulating={isSimulating}
        isApplying={isApplying}
        onBoolChange={handleBoolChange}
        onNumChange={handleNumChange}
        onShipyardTargetChange={handleShipyardTargetChange}
        onFleetResearchTargetChange={handleFleetResearchTargetChange}
        onSimulate={() => void handleSimulate()}
        onReset={handleReset}
        onApply={() => void handleApplyToConfig()}
      />
    </div>
  );
};

export default FleetPlannerTab;
