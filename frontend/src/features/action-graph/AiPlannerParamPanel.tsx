import React from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import type { PlannerJobState } from "../../types/api";
import { fmtInt } from "../../utils/formatters";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import {
  AI_PLANNER_GROUP_SUBTITLES,
  AI_PLANNER_PARAM_DOCS,
  AI_PLANNER_SPARE_DOCS,
} from "./paramDocs";
import {
  AiPlannerDraft,
  ColonyGateInfo,
  formatDurationSecondsCompact,
} from "./plannerHelpers";

export interface AiPlannerParamPanelProps {
  draft: AiPlannerDraft;
  draftDirty: boolean;
  paramSubTab: "horizon" | "weights";
  onSelectParamSubTab: (tab: "horizon" | "weights") => void;
  onUpdateDraft: (updater: (prev: AiPlannerDraft) => AiPlannerDraft) => void;
  telemetry: Record<string, any>;
  medianLoot: number | null;
  medianRtSec: number | null;
  gate: ColonyGateInfo;
  isJobRunning: boolean;
  activeJob: PlannerJobState | null;
  isSubmitting: boolean;
  isLiveAiActive: boolean;
  onRunAiPlanner: () => void;
  onActivateAiMode: () => void;
}

const HORIZON_FIELDS = [
  ["reward_weeks", 1, 104, 1],
  ["weekly_discount", 0.05, 1.0, 0.05],
  ["transport_dispatch_delay_minutes", 0, 1440, 5],
] as const;

const WEIGHT_FIELDS = [
  ["scarcity_focus", 0, 10, 0.1],
  ["critical_queue_share", 0, 1, 0.05],
  ["patience", 0, 5, 0.05],
  ["speed_weight", 0, 10, 0.1],
] as const;

const GA_FIELDS = [
  ["ga_population_size", 4, 200, 2],
  ["ga_generations", 1, 100, 1],
  ["ga_random_seed", undefined, undefined, 1],
] as const;

export const AiPlannerParamPanel: React.FC<AiPlannerParamPanelProps> = ({
  draft,
  draftDirty,
  paramSubTab,
  onSelectParamSubTab,
  onUpdateDraft,
  telemetry,
  medianLoot,
  medianRtSec,
  gate,
  isJobRunning,
  activeJob,
  isSubmitting,
  isLiveAiActive,
  onRunAiPlanner,
  onActivateAiMode,
}) => {
  const renderNumericField = (
    key: keyof Omit<AiPlannerDraft, "resource_spare_percent" | "pad_generic_colonies">,
    minV?: number,
    maxV?: number,
    stepV?: number,
    placeholder?: string
  ) => (
    <ActionGraphParamRow
      key={key}
      doc={AI_PLANNER_PARAM_DOCS[key]}
      htmlFor={`ai-param-${key}`}
      layout="inline"
    >
      <input
        type="number"
        id={`ai-param-${key}`}
        min={minV}
        max={maxV}
        step={stepV}
        placeholder={placeholder}
        value={draft[key]}
        onChange={(e) => onUpdateDraft((prev) => ({ ...prev, [key]: e.target.value }))}
      />
    </ActionGraphParamRow>
  );

  return (
    <aside
      className="hud-window ag-control-panel"
      id="ag-ai-control-panel"
      aria-label="AI-Planner parameters"
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">PTH</span>
          <h2>AI-Planner Parameters</h2>
        </div>
        <span
          className={`ag-dirty-badge tabular ${draftDirty ? "is-dirty" : "is-synced"}`}
          id="ai-dirty-badge"
        >
          {draftDirty ? "MODIFIED" : "SYNCED"}
        </span>
      </div>

      <div className="ag-panel-tabs" role="tablist" aria-label="AI-Planner parameter tabs">
        <button
          type="button"
          className={`tab-btn${paramSubTab === "horizon" ? " active" : ""}`}
          role="tab"
          aria-selected={paramSubTab === "horizon"}
          id="ai-ptab-horizon"
          onClick={() => onSelectParamSubTab("horizon")}
        >
          Horizon &amp; Spare %
        </button>
        <button
          type="button"
          className={`tab-btn${paramSubTab === "weights" ? " active" : ""}`}
          role="tab"
          aria-selected={paramSubTab === "weights"}
          id="ai-ptab-weights"
          onClick={() => onSelectParamSubTab("weights")}
        >
          Weights &amp; GA
        </button>
      </div>

      <div className="ag-panel-body">
        <div className="ag-param-pane" hidden={paramSubTab !== "horizon"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Reward Horizon &amp; Discounting</h3>
              <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.horizon}</p>
            </div>
            <div className="ag-param-list">
              {HORIZON_FIELDS.map(([key, minV, maxV, stepV]) =>
                renderNumericField(key, minV, maxV, stepV)
              )}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Per-Resource Spare % (Fleet &amp; Raids)</h3>
              <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.sparePct}</p>
            </div>
            <div className="ag-param-list">
              {(["pig_iron", "metal", "kryptonite", "spice"] as const).map((rk) => (
                <ActionGraphParamRow
                  key={rk}
                  doc={AI_PLANNER_SPARE_DOCS[rk]}
                  htmlFor={`ai-param-spare-${rk}`}
                  layout="inline"
                >
                  <input
                    type="number"
                    id={`ai-param-spare-${rk}`}
                    min={0}
                    max={100}
                    step={1}
                    value={draft.resource_spare_percent[rk]}
                    onChange={(e) =>
                      onUpdateDraft((prev) => ({
                        ...prev,
                        resource_spare_percent: {
                          ...prev.resource_spare_percent,
                          [rk]: e.target.value,
                        },
                      }))
                    }
                  />
                </ActionGraphParamRow>
              ))}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Attack Telemetry &amp; Optional Overrides</h3>
              <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.telemetry}</p>
            </div>
            <div className="ag-telemetry-box tabular" id="ai-sqlite-telemetry-box">
              <div>
                {`Median Loot: ${medianLoot !== null ? `${fmtInt(Math.round(medianLoot))} SpEq` : "Fallback defaults"}`}
              </div>
              <div>
                {`Median RT: ${formatDurationSecondsCompact(medianRtSec)} · Samples: ${fmtInt(Math.round(safeFiniteNumber(telemetry.sample_count, 0) ?? 0))}`}
              </div>
            </div>
            <div className="ag-param-list">
              {renderNumericField("fleet_command_center_level", 0, 50, 1, "Auto")}
              {renderNumericField("attacks_per_day", 0, undefined, 1, "Auto")}
              <ActionGraphParamRow
                doc={AI_PLANNER_PARAM_DOCS.pad_generic_colonies}
                htmlFor="ai-param-pad_generic_colonies"
                layout="toggle"
                titleOverride={`Pad < ${gate.maxPlanets}/${gate.maxPlanets} Missing Slots in Preview (${gate.coloniesOwned}/${gate.maxPlanets} Owned)`}
              >
                <input
                  type="checkbox"
                  className="config-checkbox"
                  id="ai-param-pad_generic_colonies"
                  checked={draft.pad_generic_colonies}
                  onChange={(e) =>
                    onUpdateDraft((prev) => ({ ...prev, pad_generic_colonies: e.target.checked }))
                  }
                />
              </ActionGraphParamRow>
            </div>
          </div>
        </div>

        <div className="ag-param-pane" hidden={paramSubTab !== "weights"}>
          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>Path Strategy Weights</h3>
              <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.weights}</p>
            </div>
            <div className="ag-param-list">
              {WEIGHT_FIELDS.map(([key, minV, maxV, stepV]) =>
                renderNumericField(key, minV, maxV, stepV)
              )}
            </div>
          </div>

          <div className="ag-param-group">
            <div className="ag-param-group-head">
              <h3>GA Path Search</h3>
              <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.gaSearch}</p>
            </div>
            <div className="ag-param-list">
              {GA_FIELDS.map(([key, minV, maxV, stepV]) =>
                renderNumericField(key, minV, maxV, stepV)
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="ag-panel-footer">
        {isJobRunning ? (
          <div className="ag-job-progress" id="ai-job-progress">
            <div className="ag-job-progress-meta tabular">
              <span>{String(activeJob?.phase || "Running AI-Planner...")}</span>
              <span>
                {`${Math.max(0, Math.min(100, Math.round(safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)))}%`}
              </span>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-cyan"
                style={{
                  transform: `scaleX(${Math.max(0, Math.min(100, safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)) / 100})`,
                }}
              />
            </div>
          </div>
        ) : (
          <p className="ag-sim-note" id="ai-activate-gate-note">
            {gate.liveAiUnlocked
              ? `Colony gate satisfied (${gate.coloniesOwned}/${gate.maxPlanets}). Ready for live bot execution.`
              : `Live execution locked until ${gate.maxPlanets}/${gate.maxPlanets} colonies are founded (${gate.coloniesOwned}/${gate.maxPlanets} owned).`}
          </p>
        )}
        <div className="ag-footer-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-ai-run"
            disabled={isSubmitting || isJobRunning}
            onClick={onRunAiPlanner}
          >
            {isJobRunning ? "Running AI-Planner..." : "Run AI-Planner"}
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm btn-span"
            id="btn-ai-activate"
            disabled={!gate.liveAiUnlocked}
            title={
              gate.liveAiUnlocked
                ? "Activate Live AI-Planner execution"
                : `Unlocks at ${gate.maxPlanets}/${gate.maxPlanets} founded colonies`
            }
            onClick={onActivateAiMode}
          >
            {isLiveAiActive
              ? "Live AI-Planner Mode Active"
              : gate.liveAiUnlocked
                ? "Activate Live AI-Planner Mode"
                : `Activate Live AI-Planner (${gate.coloniesOwned}/${gate.maxPlanets} Locked)`}
          </button>
        </div>
      </div>
    </aside>
  );
};
