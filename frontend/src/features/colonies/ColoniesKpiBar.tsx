import React from "react";
import { fmtInt } from "../../utils/formatters";

export interface ColoniesKpiBarProps {
  colonyData: Record<string, any> | null;
  gradeCounts: Record<string, number>;
  minFieldsTarget: number;
  triedCount: number;
  deletedCount: number;
  keptCount: number;
  totalEvents: number;
  minFieldsInput: string;
  allowColonizeInput: boolean;
  onMinFieldsChange: (val: string) => void;
  onAllowColonizeChange: (checked: boolean) => void;
  onSavePolicy: () => void;
  onScanGalaxy: () => void;
}

export const ColoniesKpiBar: React.FC<ColoniesKpiBarProps> = ({
  colonyData,
  gradeCounts: gc,
  minFieldsTarget,
  triedCount,
  deletedCount,
  keptCount,
  totalEvents,
  minFieldsInput,
  allowColonizeInput,
  onMinFieldsChange,
  onAllowColonizeChange,
  onSavePolicy,
  onScanGalaxy,
}) => {
  return (
    <div className="combat-top-cards col-top-cards">
      <div className="kpi-card">
        <span className="kpi-label tabular">Qualified 300+ Colonies</span>
        <strong className="kpi-value tabular" id="col-kpi-qualified">
          {`${fmtInt(colonyData?.qualified_count ?? 0)} Ready`}
        </strong>
        <span className="kpi-sub tabular" id="col-kpi-grades">
          {colonyData
            ? `S:${gc.S || 0} · A+:${gc["A+"] || 0} · A:${gc.A || 0} · B:${gc.B || 0} · C/D:${(gc.C || 0) + (gc.D || 0)}`
            : "S:0 · A+:0 · A:0 · B:0"}
        </span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label tabular">Scouted Free Slots</span>
        <strong className="kpi-value tabular" id="col-kpi-probed">
          {colonyData ? `${fmtInt(colonyData.probed_count ?? 0)} Spied` : "0 Probed"}
        </strong>
        <span className="kpi-sub tabular" id="col-kpi-unprobed">
          {`${fmtInt(colonyData?.unprobed_count ?? 0)} unprobed free slots`}
        </span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label tabular">Colonization Fleet</span>
        <strong className="kpi-value tabular" id="col-kpi-ships">
          {`${fmtInt(colonyData?.colony_ships ?? 0)} s8 · ${fmtInt(colonyData?.spy_probes ?? 0)} s10`}
        </strong>
        <span className="kpi-sub tabular" id="col-kpi-origin">
          {colonyData
            ? `Origin [${colonyData.origin_coords || "--"}] · ${fmtInt(colonyData.planet_count ?? 1)} colonies`
            : "Origin [--]"}
        </span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label tabular">{`Tried / Deleted (<${minFieldsTarget})`}</span>
        <strong className="kpi-value tabular" id="col-kpi-tried">
          {`${fmtInt(triedCount)} Tried · ${fmtInt(deletedCount)} Del`}
        </strong>
        <span className="kpi-sub tabular" id="col-kpi-tried-sub">
          {`${fmtInt(keptCount)} kept · ${fmtInt(totalEvents)} logged events`}
        </span>
      </div>
      <div className="kpi-card">
        <span className="kpi-label tabular">Colony Policy</span>
        <div className="colony-policy-controls">
          <label className="colony-inline-field tabular" htmlFor="col-min-fields">
            Min Fields
            <input
              type="number"
              id="col-min-fields"
              min={50}
              max={600}
              step={10}
              value={minFieldsInput}
              onChange={(e) => onMinFieldsChange(e.target.value)}
            />
          </label>
          <label className="toggle-chip colony-inline-toggle">
            <input
              type="checkbox"
              id="col-allow-colonize"
              checked={allowColonizeInput}
              onChange={(e) => onAllowColonizeChange(e.target.checked)}
            />{" "}
            Auto Colonize
          </label>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            id="btn-col-save"
            onClick={onSavePolicy}
          >
            Apply
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-col-scan"
            onClick={onScanGalaxy}
          >
            Scan Galaxy
          </button>
        </div>
      </div>
    </div>
  );
};
