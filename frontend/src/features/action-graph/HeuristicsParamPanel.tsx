import React from "react";
import type { ActionGraphTabKey } from "../../types/api";
import { SchemaSettingField } from "../config/SchemaSettingField";
import { AG_BOOL_FIELDS, AG_NUM_DEFAULTS } from "./heuristicsDraft";
import { ColonyGateInfo, normalizeUiPlannerMode } from "./plannerHelpers";

export interface HeuristicsParamPanelProps {
  agSimDirty: boolean;
  paramTab: "core" | "scope";
  draftValues: Record<string, any>;
  gate: ColonyGateInfo;
  onSwitchTab?: (tab: ActionGraphTabKey) => void;
  onSelectParamTab: (tab: "core" | "scope") => void;
  onDraftChange: (key: string, val: any) => void;
  onModeSelectChange: (mode: "priority" | "ai_planner") => void;
  onImmediateSim: () => void;
  onDebouncedSim: () => void;
  onSimulateExplicit: () => void;
  onResetSim: () => void;
  onApplySim: () => void;
}

const CORE_THRESHOLD_KEYS = [
  "storage_warn_ratio",
  "blast_pig_iron_max_share",
  "combat_safety_margin",
  "raid_min_loot_spice_eq",
  "attack_min_res_per_hour",
  "recycle_min_res_per_hour",
  "hot_planet_solar_temp_threshold",
  "min_free_fields_warning",
  "min_colony_fields",
  "max_planets",
] as const;

const SCOPE_PACING_KEYS = [
  "fleet_save_fuel_reserve_spice",
  "action_batch_interval_seconds",
  "galaxy_scan_click_delay_seconds",
  "galaxy_scan_click_jitter_seconds",
  "max_actions_per_hour",
  "reserved_spy_slots",
] as const;

const HEURISTICS_PLANNER_MODE_OPTIONS = [
  { value: "priority", label: "priority — Rule-Based Heuristic" },
  { value: "ai_planner", label: "ai_planner — AI-Planner (W-Week NPV)" },
];

export const HeuristicsParamPanel: React.FC<HeuristicsParamPanelProps> = ({
  agSimDirty,
  paramTab,
  draftValues,
  gate,
  onSwitchTab,
  onSelectParamTab,
  onDraftChange,
  onModeSelectChange,
  onImmediateSim,
  onDebouncedSim,
  onSimulateExplicit,
  onResetSim,
  onApplySim,
}) => {
  const [showHelp, setShowHelp] = React.useState(false);
  const draftModeNorm = normalizeUiPlannerMode(draftValues.planner_mode);
  const curPlaystyle = String(draftValues.playstyle || "fleeter");

  return (
    <aside
      className={`hud-window ag-control-panel${showHelp ? "" : " ag-help-collapsed"}`}
      id="ag-control-panel"
      aria-label="Planner parameter simulation and apply controls"
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">PRM</span>
          <h2>Heuristic Parameters</h2>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className={`btn btn-secondary btn-sm ag-help-toggle${showHelp ? " active" : ""}`}
            id="btn-ag-heuristics-help-toggle"
            aria-pressed={showHelp}
            onClick={() => setShowHelp((v) => !v)}
          >
            {showHelp ? "Hide Help" : "Show Help"}
          </button>
          <span
            className={`ag-dirty-badge tabular ${agSimDirty ? "is-dirty" : "is-synced"}`}
            id="ag-dirty-badge"
          >
            {agSimDirty ? "MODIFIED (PREVIEW)" : "ACTIVE SYNCED"}
          </span>
        </div>
      </div>

      <div className="ag-panel-tabs" role="tablist" aria-label="Parameter categories">
        <button
          type="button"
          className={`tab-btn${paramTab === "core" ? " active" : ""}`}
          role="tab"
          aria-selected={paramTab === "core" ? "true" : "false"}
          id="ag-ptab-btn-core"
          data-ag-ptab="core"
          onClick={() => onSelectParamTab("core")}
        >
          Mode &amp; Thresholds
        </button>
        <button
          type="button"
          className={`tab-btn${paramTab === "scope" ? " active" : ""}`}
          role="tab"
          aria-selected={paramTab === "scope" ? "true" : "false"}
          id="ag-ptab-btn-scope"
          data-ag-ptab="scope"
          onClick={() => onSelectParamTab("scope")}
        >
          Automation &amp; Limits
        </button>
      </div>

      <div className="ag-panel-body">
        <div className="ag-param-pane" id="ag-param-pane-core" hidden={paramTab !== "core"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Planner Mode &amp; Doctrine</h3>
              <p className="ag-param-group-sub">
                Choose the active planner engine and strategic doctrine used to preview and execute
                action sequences.
              </p>
            </div>
            <div className="ag-param-list">
              <SchemaSettingField
                variant="panel"
                idPrefix="ag-cfg"
                settingKey="planner_mode"
                value={draftModeNorm}
                options={HEURISTICS_PLANNER_MODE_OPTIONS}
                onChange={(val) => onModeSelectChange(normalizeUiPlannerMode(val))}
              />
              <SchemaSettingField
                variant="panel"
                idPrefix="ag-cfg"
                settingKey="playstyle"
                value={curPlaystyle}
                includeUnknownOption
                onChange={(val) => {
                  onDraftChange("playstyle", val);
                  onImmediateSim();
                }}
              />
            </div>
          </div>

          <div className="ag-ai-handoff-card" id="ag-ai-handoff-card">
            <div className="ag-ai-handoff-head">
              <span className="ag-handoff-title">AI-Planner Workspace</span>
              <span
                className={`ag-gate-chip tabular ${gate.liveAiUnlocked ? "is-unlocked" : "is-locked"}`}
              >
                {gate.liveAiUnlocked
                  ? `${gate.coloniesOwned}/${gate.maxPlanets} UNLOCKED`
                  : `${gate.coloniesOwned}/${gate.maxPlanets} PREVIEW ONLY`}
              </span>
            </div>
            <p className="ag-sim-note">
              {gate.liveAiUnlocked
                ? "All colonies founded. Configure monthly targets and W-week discounted builds in Tab 2 & Tab 3."
                : `Live AI-Planner unlocks at ${gate.maxPlanets}/${gate.maxPlanets} colonies. Heuristic mode guides early expansion.`}
            </p>
            {onSwitchTab ? (
              <div className="ag-handoff-actions">
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  id="btn-ag-goto-config-opt"
                  onClick={() => onSwitchTab("config-optimizer")}
                >
                  Config Optimization →
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  id="btn-ag-goto-ai-planner"
                  onClick={() => onSwitchTab("ai-planner")}
                >
                  Open AI-Planner →
                </button>
              </div>
            ) : null}
          </div>

          <div className="ag-param-group" id="ag-heuristic-params-group">
            <div className="ag-param-group-head">
              <h3>Economy &amp; Safety Thresholds</h3>
              <p className="ag-param-group-sub">
                Resource warehouse thresholds, Blast furnace throttling, combat margins, and colony
                field constraints.
              </p>
            </div>
            <div className="ag-param-list">
              {CORE_THRESHOLD_KEYS.map((key) => (
                <SchemaSettingField
                  key={key}
                  variant="panel"
                  idPrefix="ag-cfg"
                  settingKey={key}
                  value={draftValues[key] ?? AG_NUM_DEFAULTS[key]}
                  onChange={(val) => {
                    onDraftChange(key, val);
                    onDebouncedSim();
                  }}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="ag-param-pane" id="ag-param-pane-scope" hidden={paramTab !== "scope"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Pacing, Reserves &amp; Limits</h3>
              <p className="ag-param-group-sub">
                Batch refresh cadence, galaxy scanner humanization delays, hourly action rate limit,
                and spy/escape reserves.
              </p>
            </div>
            <div className="ag-param-list">
              {SCOPE_PACING_KEYS.map((key) => (
                <SchemaSettingField
                  key={key}
                  variant="panel"
                  idPrefix="ag-cfg"
                  settingKey={key}
                  value={draftValues[key] ?? AG_NUM_DEFAULTS[key]}
                  onChange={(val) => {
                    onDraftChange(key, val);
                    onDebouncedSim();
                  }}
                />
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Automation Capabilities</h3>
              <p className="ag-param-group-sub">
                Master switches controlling which autonomous build, research, scanning, combat, and
                logistics systems may run.
              </p>
            </div>
            <div className="ag-param-list">
              {AG_BOOL_FIELDS.map((bKey) => (
                <SchemaSettingField
                  key={bKey}
                  variant="panel"
                  idPrefix="ag-cfg"
                  settingKey={bKey}
                  value={Boolean(draftValues[bKey])}
                  onChange={(checked) => {
                    onDraftChange(bKey, checked);
                    onImmediateSim();
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="ag-panel-footer">
        <p className="ag-sim-note" id="ag-sim-note">
          {agSimDirty
            ? "Preview mode active: active StrategyConfig and config.yaml remain unchanged until applied."
            : "Edits simulate a preview graph without modifying active config.yaml until applied."}
        </p>
        <div className="ag-footer-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-ag-simulate"
            onClick={onSimulateExplicit}
          >
            Simulate Graph
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-ag-reset"
            onClick={onResetSim}
          >
            Reset to Active
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm btn-span"
            id="btn-ag-apply"
            onClick={onApplySim}
          >
            Apply to Active Config
          </button>
        </div>
      </div>
    </aside>
  );
};
