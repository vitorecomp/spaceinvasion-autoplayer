import React from "react";

export interface SectorsSplitPolicyCardProps {
  refreshPctInput: string;
  discoveryPctInput: string;
  sweepPagesInput: string;
  allGalaxiesInput: boolean;
  parsedRefreshPct: number;
  parsedDiscoveryPct: number;
  parsedSweepPages: number;
  splitDirty: boolean;
  savingSplit: boolean;
  sweeping: boolean;
  splitReadoutText: string;
  onRefreshPctChange: (raw: string) => void;
  onDiscoveryPctChange: (raw: string) => void;
  onSweepPagesChange: (raw: string) => void;
  onAllGalaxiesToggle: (checked: boolean) => void;
  onRefreshPctBlur: () => void;
  onDiscoveryPctBlur: () => void;
  onSweepPagesBlur: () => void;
  onSaveSplit: () => void;
  onRunSweep: () => void;
}

export const SectorsSplitPolicyCard: React.FC<SectorsSplitPolicyCardProps> = ({
  refreshPctInput,
  discoveryPctInput,
  sweepPagesInput,
  allGalaxiesInput,
  parsedRefreshPct,
  parsedDiscoveryPct,
  splitDirty,
  savingSplit,
  sweeping,
  splitReadoutText,
  onRefreshPctChange,
  onDiscoveryPctChange,
  onSweepPagesChange,
  onAllGalaxiesToggle,
  onRefreshPctBlur,
  onDiscoveryPctBlur,
  onSweepPagesBlur,
  onSaveSplit,
  onRunSweep,
}) => {
  return (
    <div className="kpi-card sector-split-card">
      <span className="kpi-label tabular">Sweep Request Allocation (Refresh vs Discovery)</span>
      <div className="sector-split-controls">
        <label className="sector-split-field tabular" htmlFor="sec-refresh-pct">
          Refresh %
          <input
            type="number"
            id="sec-refresh-pct"
            min={0}
            max={100}
            step={5}
            value={refreshPctInput}
            onChange={(e) => onRefreshPctChange(e.target.value)}
            onBlur={onRefreshPctBlur}
          />
        </label>
        <label className="sector-split-field tabular" htmlFor="sec-discovery-pct">
          Discovery %
          <input
            type="number"
            id="sec-discovery-pct"
            min={0}
            max={100}
            step={5}
            value={discoveryPctInput}
            onChange={(e) => onDiscoveryPctChange(e.target.value)}
            onBlur={onDiscoveryPctBlur}
          />
        </label>
        <label className="sector-split-field tabular" htmlFor="sec-sweep-pages">
          Req/Sweep
          <input
            type="number"
            id="sec-sweep-pages"
            min={1}
            max={400}
            step={10}
            value={sweepPagesInput}
            onChange={(e) => onSweepPagesChange(e.target.value)}
            onBlur={onSweepPagesBlur}
          />
        </label>
        <label
          className="sector-all-gals-toggle tabular"
          htmlFor="sec-all-galaxies"
          title="Expand smart sweep discovery and scan queue across all 14 galaxies (G1-G14)"
        >
          <input
            type="checkbox"
            id="sec-all-galaxies"
            checked={allGalaxiesInput}
            onChange={(e) => onAllGalaxiesToggle(e.target.checked)}
          />
          All 14 Galaxies
        </label>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          id="btn-sec-save-split"
          disabled={savingSplit}
          onClick={onSaveSplit}
        >
          {savingSplit ? "Saving..." : splitDirty ? "Apply Split*" : "Apply Split"}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id="btn-sec-run-sweep"
          disabled={sweeping}
          onClick={onRunSweep}
        >
          {sweeping ? "Sweeping..." : "Run Sweep"}
        </button>
      </div>
      <div
        className="sector-split-bar"
        role="img"
        aria-label={`Refresh ${parsedRefreshPct}% vs Discovery ${parsedDiscoveryPct}%`}
      >
        <span className="sector-split-refresh" style={{ width: `${parsedRefreshPct}%` }} />
        <span className="sector-split-discovery" style={{ width: `${parsedDiscoveryPct}%` }} />
      </div>
      <span className="kpi-sub tabular" id="sec-split-readout">
        {splitReadoutText}
      </span>
    </div>
  );
};
