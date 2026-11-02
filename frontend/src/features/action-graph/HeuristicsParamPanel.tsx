import React from "react";
import type { ActionGraphTabKey } from "../../types/api";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import { AG_BOOL_FIELDS, AG_NUM_DEFAULTS } from "./heuristicsDraft";
import { getStrategyParamDoc } from "./paramDocs";
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

const CORE_THRESHOLD_FIELDS = [
  ["storage_warn_ratio", 0.1, 0.99, 0.05],
  ["blast_pig_iron_max_share", 0.1, 1.0, 0.05],
  ["combat_safety_margin", 1.0, 1000.0, 0.1],
  ["raid_min_loot_spice_eq", 0, undefined, 100],
  ["attack_min_res_per_hour", 0, undefined, 100],
  ["recycle_min_res_per_hour", 0, undefined, 100],
  ["hot_planet_solar_temp_threshold", -273, 1000, 5],
  ["min_free_fields_warning", 1, 1000, 1],
  ["min_colony_fields", 50, 600, 10],
  ["max_planets", 1, 50, 1],
] as const;

const SCOPE_PACING_FIELDS = [
  ["fleet_save_fuel_reserve_spice", 0, undefined, 50],
  ["action_batch_interval_seconds", 5, 86400, 5],
  ["galaxy_scan_click_delay_seconds", 0.2, 60, 0.1],
  ["galaxy_scan_click_jitter_seconds", 0, 30, 0.1],
  ["max_actions_per_hour", 1, 100000, 1],
  ["reserved_spy_slots", 0, 10, 1],
] as const;

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
  const draftModeNorm = normalizeUiPlannerMode(draftValues.planner_mode);
  const knownPlaystyles = ["fleeter", "hybrid", "miner"];
  const curPlaystyle = String(draftValues.playstyle || "fleeter");

  return (
    <aside
      className="hud-window ag-control-panel"
      id="ag-control-panel"
      aria-label="Planner parameter simulation and apply controls"
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">PRM</span>
          <h2>Heuristic Parameters</h2>
        </div>
        <span
          className={`ag-dirty-badge tabular ${agSimDirty ? "is-dirty" : "is-synced"}`}
          id="ag-dirty-badge"
        >
          {agSimDirty ? "MODIFIED (PREVIEW)" : "ACTIVE SYNCED"}
        </span>
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
              <ActionGraphParamRow
                doc={getStrategyParamDoc("planner_mode")}
                htmlFor="ag-cfg-planner_mode"
                layout="stack"
              >
                <select
                  id="ag-cfg-planner_mode"
                  value={draftModeNorm}
                  onChange={(e) => onModeSelectChange(normalizeUiPlannerMode(e.target.value))}
                >
                  <option value="priority">priority — Rule-Based Heuristic</option>
                  <option value="ai_planner">ai_planner — AI-Planner (W-Week NPV)</option>
                </select>
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={getStrategyParamDoc("playstyle")}
                htmlFor="ag-cfg-playstyle"
                layout="stack"
              >
                <select
                  id="ag-cfg-playstyle"
                  value={curPlaystyle}
                  onChange={(e) => {
                    onDraftChange("playstyle", e.target.value);
                    onImmediateSim();
                  }}
                >
                  <option value="fleeter">fleeter — Combat &amp; Techs</option>
                  <option value="hybrid">hybrid — Balanced</option>
                  <option value="miner">miner — Economy ROI</option>
                  {!knownPlaystyles.includes(curPlaystyle) && curPlaystyle ? (
                    <option value={curPlaystyle}>{curPlaystyle}</option>
                  ) : null}
                </select>
              </ActionGraphParamRow>
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
              {CORE_THRESHOLD_FIELDS.map(([key, minV, maxV, stepV]) => (
                <ActionGraphParamRow
                  key={key}
                  doc={getStrategyParamDoc(key)}
                  htmlFor={`ag-cfg-${key}`}
                  layout="inline"
                >
                  <input
                    type="number"
                    id={`ag-cfg-${key}`}
                    min={minV}
                    max={maxV}
                    step={stepV}
                    value={String(draftValues[key] ?? AG_NUM_DEFAULTS[key])}
                    onChange={(e) => {
                      onDraftChange(key, e.target.value);
                      onDebouncedSim();
                    }}
                  />
                </ActionGraphParamRow>
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
              {SCOPE_PACING_FIELDS.map(([key, minV, maxV, stepV]) => (
                <ActionGraphParamRow
                  key={key}
                  doc={getStrategyParamDoc(key)}
                  htmlFor={`ag-cfg-${key}`}
                  layout="inline"
                >
                  <input
                    type="number"
                    id={`ag-cfg-${key}`}
                    min={minV}
                    max={maxV}
                    step={stepV}
                    value={String(draftValues[key] ?? AG_NUM_DEFAULTS[key])}
                    onChange={(e) => {
                      onDraftChange(key, e.target.value);
                      onDebouncedSim();
                    }}
                  />
                </ActionGraphParamRow>
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
                <ActionGraphParamRow
                  key={bKey}
                  doc={getStrategyParamDoc(bKey)}
                  htmlFor={`ag-cfg-${bKey}`}
                  layout="toggle"
                >
                  <input
                    type="checkbox"
                    className="config-checkbox"
                    id={`ag-cfg-${bKey}`}
                    checked={Boolean(draftValues[bKey])}
                    onChange={(e) => {
                      onDraftChange(bKey, e.target.checked);
                      onImmediateSim();
                    }}
                  />
                </ActionGraphParamRow>
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
