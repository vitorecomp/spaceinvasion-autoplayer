import React from "react";
import type { PlannerJobState } from "../../types/api";
import { fmtInt, fmtShortDateTime } from "../../utils/formatters";
import { formatJobBadgeText, shipNameForKey } from "./plannerHelpers";

export interface ConfigOptimizerHeaderKpisProps {
  activeJob: PlannerJobState | null;
  isJobRunning: boolean;
  hasActiveResult: boolean;
  subView: "planets" | "research";
  planetCount: number;
  researchCount: number;
  onChangeSubView: (view: "planets" | "research") => void;
  onOpenYearlyGraph: () => void;
  onRefresh: () => void;
  shipKey: string;
  shipsPerMonth: number | null;
  wholeShips: number | null;
  limitFactor: string;
  investmentMonths: number | null;
  coloniesOwned: number;
  maxPlanets: number;
  totalFieldsUsed: number | null;
  totalFieldsMax: number | null;
  whFields: number | null;
  totalDemolishWhenFull: number;
  activeAllowDemolition: boolean;
  elapsedMs: number | null;
  runTs: number | string | null | undefined;
}

export const ConfigOptimizerHeaderKpis: React.FC<ConfigOptimizerHeaderKpisProps> = ({
  activeJob,
  isJobRunning,
  hasActiveResult,
  subView,
  planetCount,
  researchCount,
  onChangeSubView,
  onOpenYearlyGraph,
  onRefresh,
  shipKey,
  shipsPerMonth,
  wholeShips,
  limitFactor,
  investmentMonths,
  coloniesOwned,
  maxPlanets,
  totalFieldsUsed,
  totalFieldsMax,
  whFields,
  totalDemolishWhenFull,
  activeAllowDemolition,
  elapsedMs,
  runTs,
}) => {
  return (
    <>
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">OPT</span>
          <h2>Config Optimization — Monthly Target &amp; Hard Built Floors</h2>
          <span
            className={`ag-status-pill tabular ${isJobRunning ? "is-sim" : hasActiveResult ? "is-live" : ""}`}
            id="co-job-status-pill"
            role="status"
          >
            {formatJobBadgeText(activeJob)}
          </span>
        </div>

        <div className="inline-actions">
          <div className="tab-group" role="tablist" aria-label="Config optimizer tables">
            <button
              type="button"
              role="tab"
              aria-selected={subView === "planets"}
              className={`tab-btn${subView === "planets" ? " active" : ""}`}
              id="co-subtab-planets"
              onClick={() => onChangeSubView("planets")}
            >
              {`Planets (${planetCount})`}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={subView === "research"}
              className={`tab-btn${subView === "research" ? " active" : ""}`}
              id="co-subtab-research"
              onClick={() => onChangeSubView("research")}
            >
              {`Research (${researchCount})`}
            </button>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-co-yearly-graph"
            disabled={!hasActiveResult}
            title="Open year-by-year graph of Ships / Week produced toward target configuration"
            onClick={onOpenYearlyGraph}
          >
            Ships/Week Graph
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-co-refresh"
            onClick={onRefresh}
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="ag-kpi-strip" aria-label="Config optimization summary metrics">
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">Target Ships / Month</span>
          <strong className="ag-kpi-val tabular" id="co-kpi-ships">
            {shipsPerMonth !== null
              ? `${shipsPerMonth.toFixed(2)} ${shipKey}/mo`
              : `— ${shipKey}/mo`}
          </strong>
          <span className="ag-kpi-sub tabular">
            {wholeShips !== null
              ? `${fmtInt(Math.round(wholeShips))} whole · ${shipNameForKey(shipKey)}`
              : shipNameForKey(shipKey)}
          </span>
        </div>
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">Limiting Factor</span>
          <strong className="ag-kpi-val tabular" id="co-kpi-limit">
            {limitFactor}
          </strong>
          <span className="ag-kpi-sub tabular">
            {investmentMonths != null
              ? `Payback ~${Number(investmentMonths).toFixed(1)} mo`
              : `Colonies ${coloniesOwned}/${maxPlanets}`}
          </span>
        </div>
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">Total &amp; Warehouse Fields</span>
          <strong className="ag-kpi-val tabular" id="co-kpi-fields">
            {totalFieldsUsed !== null
              ? `${fmtInt(Math.round(totalFieldsUsed))} / ${totalFieldsMax !== null ? fmtInt(Math.round(totalFieldsMax)) : "—"}`
              : "— / —"}
          </strong>
          <span className="ag-kpi-sub tabular">
            {whFields !== null
              ? `Warehouse fields: ${fmtInt(Math.round(whFields))}${totalDemolishWhenFull > 0 ? ` · Demolish @ full: ${fmtInt(totalDemolishWhenFull)}` : ""}`
              : activeAllowDemolition
                ? "Demolition enabled when out of fields"
                : "Hard built floors enforced"}
          </span>
        </div>
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">Last Run &amp; Elapsed</span>
          <strong className="ag-kpi-val tabular" id="co-kpi-elapsed">
            {elapsedMs !== null ? `${Math.round(elapsedMs)} ms` : "Not Run"}
          </strong>
          <span className="ag-kpi-sub tabular">
            {typeof runTs === "number"
              ? fmtShortDateTime(runTs)
              : typeof runTs === "string" && runTs
                ? runTs.slice(0, 19).replace("T", " ")
                : "Click Run Config Optimizer"}
          </span>
        </div>
      </div>
    </>
  );
};
