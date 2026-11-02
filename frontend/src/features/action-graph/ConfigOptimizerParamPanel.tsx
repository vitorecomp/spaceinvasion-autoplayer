import React from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import type { PlannerJobState } from "../../types/api";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import {
  CONFIG_OPTIMIZER_GROUP_SUBTITLES,
  CONFIG_OPTIMIZER_MIN_FLEET_DOCS,
  CONFIG_OPTIMIZER_OPT_MAX_DOCS,
  CONFIG_OPTIMIZER_PARAM_DOCS,
  getConfigOptimizerMiscDoc,
} from "./paramDocs";
import {
  type ConfigOptimizerDraft,
  MISC_BUILDING_KEYS,
  OPTIMIZED_BUILDING_KEYS,
  SHIP_OPTIONS,
} from "./plannerHelpers";

export interface ConfigOptimizerParamPanelProps {
  draft: ConfigOptimizerDraft;
  draftDirty: boolean;
  paramSubTab: "core" | "buildings";
  onChangeParamSubTab: (tab: "core" | "buildings") => void;
  onPatchDraft: (updater: (prev: ConfigOptimizerDraft) => ConfigOptimizerDraft) => void;
  gate: { coloniesOwned: number; maxPlanets: number };
  activeJob: PlannerJobState | null;
  isJobRunning: boolean;
  isSubmitting: boolean;
  hasActiveResult: boolean;
  totalLevelsToDemolish: number;
  onResetDefaults: () => void;
  onOpenYearlyGraph: () => void;
  onRunOptimizer: () => void;
}

export const ConfigOptimizerParamPanel: React.FC<ConfigOptimizerParamPanelProps> = ({
  draft,
  draftDirty,
  paramSubTab,
  onChangeParamSubTab,
  onPatchDraft,
  gate,
  activeJob,
  isJobRunning,
  isSubmitting,
  hasActiveResult,
  totalLevelsToDemolish,
  onResetDefaults,
  onOpenYearlyGraph,
  onRunOptimizer,
}) => {
  return (
    <aside
      className="hud-window ag-control-panel"
      id="ag-co-control-panel"
      aria-label="Config optimizer parameters"
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">CFG</span>
          <h2>Optimizer Parameters</h2>
        </div>
        <span
          className={`ag-dirty-badge tabular ${draftDirty ? "is-dirty" : "is-synced"}`}
          id="co-dirty-badge"
        >
          {draftDirty ? "MODIFIED" : "SYNCED"}
        </span>
      </div>

      <div className="ag-panel-tabs" role="tablist" aria-label="Config optimizer parameter tabs">
        <button
          type="button"
          className={`tab-btn${paramSubTab === "core" ? " active" : ""}`}
          role="tab"
          aria-selected={paramSubTab === "core"}
          id="co-ptab-core"
          onClick={() => onChangeParamSubTab("core")}
        >
          Objective &amp; Fleet
        </button>
        <button
          type="button"
          className={`tab-btn${paramSubTab === "buildings" ? " active" : ""}`}
          role="tab"
          aria-selected={paramSubTab === "buildings"}
          id="co-ptab-buildings"
          onClick={() => onChangeParamSubTab("buildings")}
        >
          Buildings &amp; GA
        </button>
      </div>

      <div className="ag-panel-body">
        <div className="ag-param-pane" hidden={paramSubTab !== "core"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Objective &amp; Empire Constraints</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.objective}</p>
            </div>
            <div className="ag-param-list">
              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.target_ship_key}
                htmlFor="co-param-target_ship_key"
                layout="stack"
              >
                <select
                  id="co-param-target_ship_key"
                  value={draft.target_ship_key}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, target_ship_key: val }));
                  }}
                >
                  {SHIP_OPTIONS.map((s) => (
                    <option key={s.key} value={s.key}>
                      {`${s.key} — ${s.name}`}
                    </option>
                  ))}
                </select>
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.production_month_days}
                htmlFor="co-param-production_month_days"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-production_month_days"
                  min={1}
                  max={365}
                  step={1}
                  value={draft.production_month_days}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, production_month_days: val }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.research_percent_of_universe_max}
                htmlFor="co-param-research_percent_of_universe_max"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-research_percent_of_universe_max"
                  min={0}
                  max={200}
                  step={5}
                  value={draft.research_percent_of_universe_max}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, research_percent_of_universe_max: val }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.warehouse_hours_of_production}
                htmlFor="co-param-warehouse_hours_of_production"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-warehouse_hours_of_production"
                  min={0}
                  max={720}
                  step={1}
                  value={draft.warehouse_hours_of_production}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, warehouse_hours_of_production: val }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.min_warehouse_level}
                htmlFor="co-param-min_warehouse_level"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-min_warehouse_level"
                  min={0}
                  max={60}
                  step={1}
                  value={draft.min_warehouse_level}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, min_warehouse_level: val }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.central_planet_slot}
                htmlFor="co-param-central_planet_slot"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-central_planet_slot"
                  min={1}
                  max={50}
                  step={1}
                  value={draft.central_planet_slot}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, central_planet_slot: val }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.central_planet_min_warehouse_level}
                htmlFor="co-param-central_planet_min_warehouse_level"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-central_planet_min_warehouse_level"
                  min={0}
                  max={50}
                  step={1}
                  value={draft.central_planet_min_warehouse_level}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({
                      ...prev,
                      central_planet_min_warehouse_level: val,
                    }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.allow_demolition}
                htmlFor="co-param-allow_demolition"
                layout="toggle"
              >
                <input
                  type="checkbox"
                  className="config-checkbox"
                  id="co-param-allow_demolition"
                  checked={draft.allow_demolition}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    onPatchDraft((prev) => ({ ...prev, allow_demolition: checked }));
                  }}
                />
              </ActionGraphParamRow>
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Specialization Stability</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.stability}</p>
            </div>
            <div className="ag-param-list">
              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.preserve_previous_specializations}
                htmlFor="co-param-preserve_previous_specializations"
                layout="toggle"
              >
                <input
                  type="checkbox"
                  className="config-checkbox"
                  id="co-param-preserve_previous_specializations"
                  checked={draft.preserve_previous_specializations}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    onPatchDraft((prev) => ({
                      ...prev,
                      preserve_previous_specializations: checked,
                    }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.specialization_switch_threshold_pct}
                htmlFor="co-param-specialization_switch_threshold_pct"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-specialization_switch_threshold_pct"
                  min={0}
                  max={25}
                  step={0.1}
                  value={draft.specialization_switch_threshold_pct}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({
                      ...prev,
                      specialization_switch_threshold_pct: val,
                    }));
                  }}
                />
              </ActionGraphParamRow>
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Min Fleet per Planet</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.minFleet}</p>
            </div>
            <div className="ag-param-list">
              {(["s2", "s10", "s11"] as const).map((sk) => (
                <ActionGraphParamRow
                  key={sk}
                  doc={CONFIG_OPTIMIZER_MIN_FLEET_DOCS[sk]}
                  htmlFor={`co-param-min_fleet-${sk}`}
                  layout="inline"
                >
                  <input
                    type="number"
                    id={`co-param-min_fleet-${sk}`}
                    min={0}
                    max={10000}
                    step={1}
                    value={draft.min_fleet_per_planet[sk] ?? "10"}
                    onChange={(e) => {
                      const val = e.target.value;
                      onPatchDraft((prev) => ({
                        ...prev,
                        min_fleet_per_planet: {
                          ...prev.min_fleet_per_planet,
                          [sk]: val,
                        },
                      }));
                    }}
                  />
                </ActionGraphParamRow>
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Colony Slot Preview</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.previewScope}</p>
            </div>
            <div className="ag-param-list">
              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.pad_generic_colonies}
                htmlFor="co-param-pad_generic_colonies"
                layout="toggle"
                titleOverride={`Pad < ${gate.maxPlanets}/${gate.maxPlanets} Missing Slots (${gate.coloniesOwned}/${gate.maxPlanets} Owned)`}
              >
                <input
                  type="checkbox"
                  className="config-checkbox"
                  id="co-param-pad_generic_colonies"
                  checked={draft.pad_generic_colonies}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    onPatchDraft((prev) => ({ ...prev, pad_generic_colonies: checked }));
                  }}
                />
              </ActionGraphParamRow>
            </div>
          </div>
        </div>

        <div className="ag-param-pane" hidden={paramSubTab !== "buildings"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Optimized Building Max Caps</h3>
              <p className="ag-param-group-sub">
                {CONFIG_OPTIMIZER_GROUP_SUBTITLES.optimizedCaps}
              </p>
            </div>
            <div className="ag-param-list">
              {OPTIMIZED_BUILDING_KEYS.map((bk) => (
                <ActionGraphParamRow
                  key={bk}
                  doc={CONFIG_OPTIMIZER_OPT_MAX_DOCS[bk]}
                  htmlFor={`co-param-opt_max-${bk}`}
                  layout="inline"
                >
                  <input
                    type="number"
                    id={`co-param-opt_max-${bk}`}
                    min={1}
                    max={100}
                    step={1}
                    value={draft.optimized_building_max_levels[bk] ?? "100"}
                    onChange={(e) => {
                      const val = e.target.value;
                      onPatchDraft((prev) => ({
                        ...prev,
                        optimized_building_max_levels: {
                          ...prev.optimized_building_max_levels,
                          [bk]: val,
                        },
                      }));
                    }}
                  />
                </ActionGraphParamRow>
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Fixed / Minimum Misc Levels</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.miscLevels}</p>
            </div>
            <div className="ag-param-list">
              {MISC_BUILDING_KEYS.map((bk) => (
                <ActionGraphParamRow
                  key={bk}
                  doc={getConfigOptimizerMiscDoc(bk)}
                  htmlFor={`co-param-misc-${bk}`}
                  layout="inline"
                >
                  <input
                    type="number"
                    id={`co-param-misc-${bk}`}
                    min={0}
                    max={100}
                    step={1}
                    value={draft.misc_building_levels[bk] ?? "0"}
                    onChange={(e) => {
                      const val = e.target.value;
                      onPatchDraft((prev) => ({
                        ...prev,
                        misc_building_levels: {
                          ...prev.misc_building_levels,
                          [bk]: val,
                        },
                      }));
                    }}
                  />
                </ActionGraphParamRow>
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>GA Search Settings</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.gaSearch}</p>
            </div>
            <div className="ag-param-list">
              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.ga_population_size}
                htmlFor="co-param-ga_population_size"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-ga_population_size"
                  min={4}
                  max={1000}
                  step={10}
                  value={draft.ga_population_size}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, ga_population_size: val }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.ga_generations}
                htmlFor="co-param-ga_generations"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-ga_generations"
                  min={1}
                  max={1000}
                  step={10}
                  value={draft.ga_generations}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, ga_generations: val }));
                  }}
                />
              </ActionGraphParamRow>

              <ActionGraphParamRow
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.ga_random_seed}
                htmlFor="co-param-ga_random_seed"
                layout="inline"
              >
                <input
                  type="number"
                  id="co-param-ga_random_seed"
                  step={1}
                  value={draft.ga_random_seed}
                  onChange={(e) => {
                    const val = e.target.value;
                    onPatchDraft((prev) => ({ ...prev, ga_random_seed: val }));
                  }}
                />
              </ActionGraphParamRow>
            </div>
          </div>
        </div>
      </div>

      <div className="ag-panel-footer">
        {isJobRunning ? (
          <div className="ag-job-progress" id="co-job-progress">
            <div className="ag-job-progress-meta tabular">
              <span>{String(activeJob?.phase || "Running Config Optimizer...")}</span>
              <span>
                {`${Math.max(0, Math.min(100, Math.round(safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)))}%`}
              </span>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-mint"
                style={{
                  transform: `scaleX(${Math.max(0, Math.min(100, safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)) / 100})`,
                }}
              />
            </div>
          </div>
        ) : (
          <p className="ag-sim-note">
            {draft.allow_demolition
              ? `Allows off-role mine teardown (${totalLevelsToDemolish > 0 ? `${totalLevelsToDemolish} levels marked` : "honors reserved floors"}); AI-Planner triggers demolitions strictly when a planet runs out of fields.`
              : "Enforces all current built building levels and reserved floors as hard minimums; auto-triggers AI-Planner replan on completion."}
          </p>
        )}
        <div className="ag-footer-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-co-reset"
            onClick={onResetDefaults}
          >
            Reset Defaults
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-co-yearly-graph-footer"
            disabled={!hasActiveResult}
            onClick={onOpenYearlyGraph}
          >
            Ships/Week Graph
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm btn-span"
            id="btn-co-run"
            disabled={isSubmitting || isJobRunning}
            onClick={onRunOptimizer}
          >
            {isJobRunning ? "Running Config Optimizer..." : "Run Config Optimizer"}
          </button>
        </div>
      </div>
    </aside>
  );
};
