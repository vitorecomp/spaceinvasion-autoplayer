import React from "react";
import type { ScannedSectorsSummary } from "../../types/api";
import { finiteNum, fmtInt } from "../../utils/formatters";
import {
  SectorsSplitPolicyCard,
  type SectorsSplitPolicyCardProps,
} from "./SectorsSplitPolicyCard";

export interface SectorsKpiStripProps {
  summary: ScannedSectorsSummary;
  totalScannedAll: number;
  galCovPct: number;
  galExplored: number;
  galTotal: number;
  uniCovPct: number;
  uniExplored: number;
  uniTotal: number;
  splitCardProps: SectorsSplitPolicyCardProps;
}

export const SectorsKpiStrip: React.FC<SectorsKpiStripProps> = ({
  summary,
  totalScannedAll,
  galCovPct,
  galExplored,
  galTotal,
  uniCovPct,
  uniExplored,
  uniTotal,
  splitCardProps,
}) => {
  return (
    <div className="combat-top-cards sectors-top-cards">
      <div className="kpi-card">
        <span className="kpi-label tabular">Scanned Sectors &amp; Coverage</span>
        <strong className="kpi-value tabular" id="sec-kpi-count">
          {`${fmtInt(summary.scanned_sectors ?? totalScannedAll)} Sectors`}
        </strong>
        <span className="kpi-sub tabular" id="sec-kpi-coverage">
          {`SCOPE ${galCovPct.toFixed(1)}% (${fmtInt(galExplored)}/${fmtInt(galTotal)}) · UNI ${uniCovPct.toFixed(1)}% (${fmtInt(uniExplored)}/${fmtInt(uniTotal)})`}
        </span>
      </div>

      <div className="kpi-card">
        <span className="kpi-label tabular">Occupied Planets &amp; Free Slots</span>
        <strong className="kpi-value tabular" id="sec-kpi-occupied">
          {`${fmtInt(summary.total_occupied ?? 0)} Occupied`}
        </strong>
        <span className="kpi-sub tabular" id="sec-kpi-free">
          {`Avg ${finiteNum(summary.avg_occupied, 0).toFixed(1)}/sys · ${fmtInt(summary.total_free ?? 0)} free (${fmtInt(summary.total_prime_free ?? 0)} prime 300+)`}
        </span>
      </div>

      <div className="kpi-card">
        <span className="kpi-label tabular">Debris Fields &amp; Raid Targets</span>
        <strong className="kpi-value tabular" id="sec-kpi-debris">
          {`${fmtInt(summary.total_debris_fields ?? 0)} Debris · ${fmtInt(summary.total_targets ?? 0)} Targets`}
        </strong>
        <span className="kpi-sub tabular" id="sec-kpi-targets-sub">
          {`${fmtInt(summary.sectors_with_debris ?? 0)} sectors w/ debris · ${fmtInt(summary.total_spacia ?? 0)} SPC · ${fmtInt(summary.total_inactives ?? 0)} INA`}
        </span>
      </div>

      <SectorsSplitPolicyCard {...splitCardProps} />
    </div>
  );
};
