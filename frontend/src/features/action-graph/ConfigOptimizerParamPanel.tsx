import React from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import type { PlannerJobState } from "../../types/api";
import { SchemaSettingField } from "../config/SchemaSettingField";
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

type CoNumberKey =
  | "production_month_days"
  | "research_percent_of_universe_max"
  | "warehouse_hours_of_production"
  | "min_warehouse_level"
  | "central_planet_slot"
  | "central_planet_min_warehouse_level"
  | "specialization_switch_threshold_pct"
  | "ga_population_size"
  | "ga_generations"
  | "ga_random_seed";

const CO_OBJECTIVE_NUMBERS: ReadonlyArray<
  readonly [CoNumberKey, number | undefined, number | undefined, number]
> = [
  ["production_month_days", 1, 365, 1],
  ["research_percent_of_universe_max", 0, 200, 5],
  ["warehouse_hours_of_production", 0, 720, 1],
  ["min_warehouse_level", 0, 60, 1],
  ["central_planet_slot", 1, 50, 1],
  ["central_planet_min_warehouse_level", 0, 50, 1],
];

const CO_GA_NUMBERS: ReadonlyArray<
  readonly [CoNumberKey, number | undefined, number | undefined, number]
> = [
  ["ga_population_size", 4, 1000, 10],
  ["ga_generations", 1, 1000, 10],
  ["ga_random_seed", undefined, undefined, 1],
];

const TARGET_SHIP_SELECT_OPTIONS = SHIP_OPTIONS.map((s) => ({
  value: s.key,
  label: `${s.key} — ${s.name}`,
}));

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
  const renderNumberField = ([key, minV, maxV, stepV]: readonly [
    CoNumberKey,
    number | undefined,
    number | undefined,
    number,
  ]) => (
    <SchemaSettingField
      key={key}
      variant="panel"
      idPrefix="co-param"
      doc={CONFIG_OPTIMIZER_PARAM_DOCS[key]}
      control="number"
      min={minV}
      max={maxV}
      step={stepV}
      value={draft[key]}
      onChange={(val) => onPatchDraft((prev) => ({ ...prev, [key]: String(val) }))}
    />
  );

  const [showHelp, setShowHelp] = React.useState(false);

  return (
    <aside
      className={`hud-window ag-control-panel${showHelp ? "" : " ag-help-collapsed"}`}
      id="ag-co-control-panel"
      aria-label="Config optimizer parameters"
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">CFG</span>
          <h2>Optimizer Parameters</h2>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className={`btn btn-secondary btn-sm ag-help-toggle${showHelp ? " active" : ""}`}
            id="btn-co-help-toggle"
            aria-pressed={showHelp}
            onClick={() => setShowHelp((v) => !v)}
          >
            {showHelp ? "Hide Help" : "Show Help"}
          </button>
          <span
            className={`ag-dirty-badge tabular ${draftDirty ? "is-dirty" : "is-synced"}`}
            id="co-dirty-badge"
          >
            {draftDirty ? "MODIFIED" : "SYNCED"}
          </span>
        </div>
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
              <SchemaSettingField
                variant="panel"
                idPrefix="co-param"
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.target_ship_key}
                control="select"
                options={TARGET_SHIP_SELECT_OPTIONS}
                value={draft.target_ship_key}
                onChange={(val) =>
                  onPatchDraft((prev) => ({ ...prev, target_ship_key: String(val) }))
                }
              />
              {CO_OBJECTIVE_NUMBERS.map(renderNumberField)}
              <SchemaSettingField
                variant="panel"
                idPrefix="co-param"
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.allow_demolition}
                control="checkbox"
                value={draft.allow_demolition}
                onChange={(checked) =>
                  onPatchDraft((prev) => ({ ...prev, allow_demolition: Boolean(checked) }))
                }
              />
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Specialization Stability</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.stability}</p>
            </div>
            <div className="ag-param-list">
              <SchemaSettingField
                variant="panel"
                idPrefix="co-param"
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.preserve_previous_specializations}
                control="checkbox"
                value={draft.preserve_previous_specializations}
                onChange={(checked) =>
                  onPatchDraft((prev) => ({
                    ...prev,
                    preserve_previous_specializations: Boolean(checked),
                  }))
                }
              />
              {renderNumberField(["specialization_switch_threshold_pct", 0, 25, 0.1])}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Min Fleet per Planet</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.minFleet}</p>
            </div>
            <div className="ag-param-list">
              {(["s2", "s10", "s11"] as const).map((sk) => (
                <SchemaSettingField
                  key={sk}
                  variant="panel"
                  id={`co-param-min_fleet-${sk}`}
                  doc={CONFIG_OPTIMIZER_MIN_FLEET_DOCS[sk]}
                  control="number"
                  min={0}
                  max={10000}
                  step={1}
                  value={draft.min_fleet_per_planet[sk] ?? "10"}
                  onChange={(val) =>
                    onPatchDraft((prev) => ({
                      ...prev,
                      min_fleet_per_planet: {
                        ...prev.min_fleet_per_planet,
                        [sk]: String(val),
                      },
                    }))
                  }
                />
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Colony Slot Preview</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.previewScope}</p>
            </div>
            <div className="ag-param-list">
              <SchemaSettingField
                variant="panel"
                idPrefix="co-param"
                doc={CONFIG_OPTIMIZER_PARAM_DOCS.pad_generic_colonies}
                control="checkbox"
                titleOverride={`Pad < ${gate.maxPlanets}/${gate.maxPlanets} Missing Slots (${gate.coloniesOwned}/${gate.maxPlanets} Owned)`}
                value={draft.pad_generic_colonies}
                onChange={(checked) =>
                  onPatchDraft((prev) => ({ ...prev, pad_generic_colonies: Boolean(checked) }))
                }
              />
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
                <SchemaSettingField
                  key={bk}
                  variant="panel"
                  id={`co-param-opt_max-${bk}`}
                  doc={CONFIG_OPTIMIZER_OPT_MAX_DOCS[bk]}
                  control="number"
                  min={1}
                  max={100}
                  step={1}
                  value={draft.optimized_building_max_levels[bk] ?? "100"}
                  onChange={(val) =>
                    onPatchDraft((prev) => ({
                      ...prev,
                      optimized_building_max_levels: {
                        ...prev.optimized_building_max_levels,
                        [bk]: String(val),
                      },
                    }))
                  }
                />
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
                <SchemaSettingField
                  key={bk}
                  variant="panel"
                  id={`co-param-misc-${bk}`}
                  doc={getConfigOptimizerMiscDoc(bk)}
                  control="number"
                  min={0}
                  max={100}
                  step={1}
                  value={draft.misc_building_levels[bk] ?? "0"}
                  onChange={(val) =>
                    onPatchDraft((prev) => ({
                      ...prev,
                      misc_building_levels: {
                        ...prev.misc_building_levels,
                        [bk]: String(val),
                      },
                    }))
                  }
                />
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>GA Search Settings</h3>
              <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.gaSearch}</p>
            </div>
            <div className="ag-param-list">{CO_GA_NUMBERS.map(renderNumberField)}</div>
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
