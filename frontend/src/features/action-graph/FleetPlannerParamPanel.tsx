import React from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import type { FleetDraftState } from "./fleetPlannerHelpers";
import {
  FLEET_PLANNER_GROUP_SUBTITLES,
  FLEET_RESEARCH_OPTIONS,
  getFleetResearchDoc,
  getShipyardHullDoc,
  getStrategyParamDoc,
} from "./paramDocs";

export type FleetParamTab = "combat" | "logistics" | "scanner" | "shipyard";

export interface FleetPlannerParamPanelProps {
  draft: FleetDraftState;
  draftDirty: boolean;
  paramGroupTab: FleetParamTab;
  onSelectParamGroupTab: (tab: FleetParamTab) => void;
  liveResearches: Record<string, number>;
  statusMessage: string;
  apiOnline: boolean;
  isSimulating: boolean;
  isApplying: boolean;
  onBoolChange: (key: string, checked: boolean) => void;
  onNumChange: (key: string, value: string) => void;
  onShipyardTargetChange: (slug: string, value: string) => void;
  onFleetResearchTargetChange: (rKey: string, value: string) => void;
  onSimulate: () => void;
  onReset: () => void;
  onApply: () => void;
}

const COMBAT_NUM_SPECS = [
  ["combat_safety_margin", 1, 10, 0.05],
  ["raid_min_loot_spice_eq", 0, 10000000, 100],
  ["fleet_save_fuel_reserve_spice", 0, 10000000, 50],
  ["ai_resource_spare_pct", 0, 90, 1],
] as const;

const LOGISTICS_NUM_SPECS = [
  ["min_colony_fields", 50, 500, 5],
  ["max_planets", 1, 20, 1],
] as const;

const SCANNER_NUM_SPECS = [
  ["galaxy_sweep_interval_seconds", 10, 86400, 10],
  ["galaxy_sweep_pages", 1, 400, 5],
  ["galaxy_scan_click_delay_seconds", 0.1, 30, 0.1],
  ["galaxy_scan_click_jitter_seconds", 0, 20, 0.1],
  ["galaxy_scan_refresh_pct", 0, 100, 1],
  ["reserved_spy_slots", 0, 20, 1],
  ["galaxy_scan_near_weight", 0, 10, 0.05],
  ["galaxy_scan_attack_weight", 0, 10, 0.05],
  ["spy_dispatch_planet_id", 0, undefined, 1],
] as const;

const HARVEST_NUM_SPECS = [
  ["attack_min_res_per_hour", 0, 1000000000000, 100],
  ["recycle_min_res_per_hour", 0, 1000000000000, 100],
] as const;

export const FleetPlannerParamPanel: React.FC<FleetPlannerParamPanelProps> = ({
  draft,
  draftDirty,
  paramGroupTab,
  onSelectParamGroupTab,
  liveResearches,
  statusMessage,
  apiOnline,
  isSimulating,
  isApplying,
  onBoolChange,
  onNumChange,
  onShipyardTargetChange,
  onFleetResearchTargetChange,
  onSimulate,
  onReset,
  onApply,
}) => {
  const renderToggleRow = (bKey: string) => (
    <ActionGraphParamRow
      key={bKey}
      doc={getStrategyParamDoc(bKey)}
      htmlFor={`flt-cfg-${bKey}`}
      layout="toggle"
    >
      <input
        id={`flt-cfg-${bKey}`}
        type="checkbox"
        className="config-checkbox"
        checked={Boolean(draft.booleans[bKey])}
        onChange={(e) => onBoolChange(bKey, e.target.checked)}
      />
    </ActionGraphParamRow>
  );

  const renderNumberRow = ([key, minV, maxV, stepV]: readonly [string, number, number | undefined, number]) => (
    <ActionGraphParamRow
      key={key}
      doc={getStrategyParamDoc(key)}
      htmlFor={`flt-cfg-${key}`}
      layout="inline"
    >
      <input
        id={`flt-cfg-${key}`}
        type="number"
        min={minV}
        max={maxV}
        step={stepV}
        value={draft.numbers[key]}
        onChange={(e) => onNumChange(key, e.target.value)}
      />
    </ActionGraphParamRow>
  );

  return (
    <aside className="hud-window ag-control-panel" id="ag-flt-control-panel">
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">CTL</span>
          <h2>Fleet &amp; Tactical Settings</h2>
        </div>
        <span
          className={`ag-status-pill tabular ${draftDirty ? "is-sim" : "is-live"}`}
          id="flt-panel-pill"
        >
          {draftDirty ? "EDITED" : "SYNCED"}
        </span>
      </div>

      <div className="ag-panel-tabs" role="tablist" aria-label="Fleet settings sections">
        {(
          [
            ["combat", "Combat"],
            ["logistics", "Logistics"],
            ["scanner", "Scanner"],
            ["shipyard", "Shipyard & Tech"],
          ] as const
        ).map(([tabKey, label]) => (
          <button
            key={tabKey}
            type="button"
            className={`tab-btn${paramGroupTab === tabKey ? " active" : ""}`}
            data-flt-ptab={tabKey}
            onClick={() => onSelectParamGroupTab(tabKey)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="ag-panel-body">
        <div className="ag-param-pane" hidden={paramGroupTab !== "combat"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Combat, Raids &amp; Escape Flights</h3>
              <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.combat}</p>
            </div>
            <div className="ag-param-list">
              {(["allow_raids", "allow_pvp_in_az", "allow_fleetsave"] as const).map(renderToggleRow)}
              {COMBAT_NUM_SPECS.map(renderNumberRow)}
            </div>
          </div>
        </div>

        <div className="ag-param-pane" hidden={paramGroupTab !== "logistics"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Colonization &amp; Inter-Planet Transport</h3>
              <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.logistics}</p>
            </div>
            <div className="ag-param-list">
              {(["allow_colonize", "allow_transport", "allow_transmitter"] as const).map(renderToggleRow)}
              {LOGISTICS_NUM_SPECS.map(renderNumberRow)}
            </div>
          </div>
        </div>

        <div className="ag-param-pane" hidden={paramGroupTab !== "scanner"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Smart Galaxy Scanner &amp; Spy Slots</h3>
              <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.scanner}</p>
            </div>
            <div className="ag-param-list">
              {(
                [
                  "allow_galaxy_scan",
                  "galaxy_scan_block_other_actions",
                  "galaxy_scan_explore_first",
                  "galaxy_scan_all_galaxies",
                  "spy_reserved_slots_only",
                ] as const
              ).map(renderToggleRow)}
              {SCANNER_NUM_SPECS.map(renderNumberRow)}
            </div>
          </div>
        </div>

        <div className="ag-param-pane" hidden={paramGroupTab !== "shipyard"}>
          <div className="ag-param-group" id="flt-harvest-rph-group">
            <div className="ag-param-group-head">
              <h3>Attack &amp; Recycle Min Yield (res/h)</h3>
              <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.harvestThresholds}</p>
            </div>
            <div className="ag-param-list">{HARVEST_NUM_SPECS.map(renderNumberRow)}</div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Fleet &amp; Ship Research (Spare Budget)</h3>
              <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.fleetResearch}</p>
            </div>
            <div className="ag-param-list">
              {renderToggleRow("allow_fleet_research")}
              {FLEET_RESEARCH_OPTIONS.map((opt) => (
                <ActionGraphParamRow
                  key={`flt-res-${opt.key}`}
                  doc={getFleetResearchDoc(
                    opt.key,
                    safeFiniteNumber(liveResearches[opt.key], null)
                  )}
                  htmlFor={`flt-research-${opt.key}`}
                  layout="inline"
                >
                  <input
                    id={`flt-research-${opt.key}`}
                    type="number"
                    min={0}
                    max={100}
                    step={1}
                    value={draft.fleet_research_targets[opt.key] ?? "0"}
                    onChange={(e) => onFleetResearchTargetChange(opt.key, e.target.value)}
                  />
                </ActionGraphParamRow>
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Shipyard Targets per Hull</h3>
              <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.shipyard}</p>
            </div>
            <div className="ag-param-list">
              {renderToggleRow("allow_shipyard")}
              {Object.keys(draft.shipyard_targets).map((slug) => (
                <ActionGraphParamRow
                  key={`flt-ship-${slug}`}
                  doc={getShipyardHullDoc(slug)}
                  htmlFor={`flt-shipyard-${slug}`}
                  layout="inline"
                >
                  <input
                    id={`flt-shipyard-${slug}`}
                    type="number"
                    min={0}
                    max={9999999}
                    step={1}
                    value={draft.shipyard_targets[slug] ?? "0"}
                    onChange={(e) => onShipyardTargetChange(slug, e.target.value)}
                  />
                </ActionGraphParamRow>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="ag-panel-footer">
        <p className="ag-sim-note" id="flt-status-text">
          {statusMessage}
        </p>
        <div className="ag-footer-actions">
          <button
            className="btn btn-secondary btn-sm"
            id="btn-flt-simulate"
            type="button"
            disabled={!apiOnline || isSimulating}
            onClick={onSimulate}
          >
            {isSimulating ? "Simulating..." : "Preview Simulate"}
          </button>
          <button
            className="btn btn-secondary btn-sm"
            id="btn-flt-reset"
            type="button"
            disabled={isApplying}
            onClick={onReset}
          >
            Reset
          </button>
          <button
            className="btn btn-primary btn-sm btn-span"
            id="btn-flt-apply"
            type="button"
            disabled={isApplying}
            onClick={onApply}
          >
            {isApplying ? "Saving..." : "Apply to Config"}
          </button>
        </div>
      </div>
    </aside>
  );
};
