import React from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import { SchemaSettingField } from "../config/SchemaSettingField";
import type { FleetDraftState } from "./fleetPlannerHelpers";
import {
  FLEET_PLANNER_GROUP_SUBTITLES,
  FLEET_RESEARCH_OPTIONS,
  getFleetResearchDoc,
  getShipyardHullDoc,
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

const COMBAT_NUM_KEYS = [
  "combat_safety_margin",
  "raid_min_loot_spice_eq",
  "fleet_save_fuel_reserve_spice",
  "ai_resource_spare_pct",
] as const;

const LOGISTICS_NUM_KEYS = ["min_colony_fields", "max_planets"] as const;

const SCANNER_NUM_KEYS = [
  "galaxy_sweep_interval_seconds",
  "galaxy_sweep_pages",
  "galaxy_scan_click_delay_seconds",
  "galaxy_scan_click_jitter_seconds",
  "galaxy_scan_refresh_pct",
  "reserved_spy_slots",
  "galaxy_scan_near_weight",
  "galaxy_scan_attack_weight",
  "spy_dispatch_planet_id",
] as const;

const HARVEST_NUM_KEYS = ["attack_min_res_per_hour", "recycle_min_res_per_hour"] as const;

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
    <SchemaSettingField
      key={bKey}
      variant="panel"
      idPrefix="flt-cfg"
      settingKey={bKey}
      value={Boolean(draft.booleans[bKey])}
      onChange={(checked) => onBoolChange(bKey, Boolean(checked))}
    />
  );

  const renderNumberRow = (key: string) => (
    <SchemaSettingField
      key={key}
      variant="panel"
      idPrefix="flt-cfg"
      settingKey={key}
      control="number"
      value={draft.numbers[key]}
      onChange={(val) => onNumChange(key, String(val))}
    />
  );

  const [showHelp, setShowHelp] = React.useState(false);

  return (
    <aside
      className={`hud-window ag-control-panel${showHelp ? "" : " ag-help-collapsed"}`}
      id="ag-flt-control-panel"
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">CTL</span>
          <h2>Fleet &amp; Tactical Settings</h2>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className={`btn btn-secondary btn-sm ag-help-toggle${showHelp ? " active" : ""}`}
            id="btn-flt-help-toggle"
            aria-pressed={showHelp}
            onClick={() => setShowHelp((v) => !v)}
          >
            {showHelp ? "Hide Help" : "Show Help"}
          </button>
          <span
            className={`ag-status-pill tabular ${draftDirty ? "is-sim" : "is-live"}`}
            id="flt-panel-pill"
          >
            {draftDirty ? "EDITED" : "SYNCED"}
          </span>
        </div>
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
              {COMBAT_NUM_KEYS.map(renderNumberRow)}
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
              {(["allow_colonize", "allow_transport", "allow_transmitter"] as const).map(
                renderToggleRow
              )}
              {LOGISTICS_NUM_KEYS.map(renderNumberRow)}
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
              {SCANNER_NUM_KEYS.map(renderNumberRow)}
            </div>
          </div>
        </div>

        <div className="ag-param-pane" hidden={paramGroupTab !== "shipyard"}>
          <div className="ag-param-group" id="flt-harvest-rph-group">
            <div className="ag-param-group-head">
              <h3>Attack &amp; Recycle Min Yield (res/h)</h3>
              <p className="ag-param-group-sub">
                {FLEET_PLANNER_GROUP_SUBTITLES.harvestThresholds}
              </p>
            </div>
            <div className="ag-param-list">{HARVEST_NUM_KEYS.map(renderNumberRow)}</div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Fleet &amp; Ship Research (Spare Budget)</h3>
              <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.fleetResearch}</p>
            </div>
            <div className="ag-param-list">
              {renderToggleRow("allow_fleet_research")}
              {FLEET_RESEARCH_OPTIONS.map((opt) => (
                <SchemaSettingField
                  key={`flt-res-${opt.key}`}
                  variant="panel"
                  id={`flt-research-${opt.key}`}
                  doc={getFleetResearchDoc(
                    opt.key,
                    safeFiniteNumber(liveResearches[opt.key], null)
                  )}
                  control="number"
                  min={0}
                  max={100}
                  step={1}
                  value={draft.fleet_research_targets[opt.key] ?? "0"}
                  onChange={(val) => onFleetResearchTargetChange(opt.key, String(val))}
                />
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
                <SchemaSettingField
                  key={`flt-ship-${slug}`}
                  variant="panel"
                  id={`flt-shipyard-${slug}`}
                  doc={getShipyardHullDoc(slug)}
                  control="number"
                  min={0}
                  max={9999999}
                  step={1}
                  value={draft.shipyard_targets[slug] ?? "0"}
                  onChange={(val) => onShipyardTargetChange(slug, String(val))}
                />
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
