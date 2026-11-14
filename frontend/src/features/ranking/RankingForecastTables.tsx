import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import {
  durationToSeconds,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { finiteNum, fmtInt } from "../../utils/formatters";

function fmtSignedDeltaInt(val: number): string {
  const rounded = Math.round(val);
  const clean = rounded === 0 ? 0 : rounded;
  return `${clean >= 0 ? "+" : ""}${fmtInt(clean)}`;
}

export interface RankingForecastTablesProps {
  horizons: Array<Record<string, any>>;
  milestones: Array<Record<string, any>>;
}

export const RankingForecastTables: React.FC<RankingForecastTablesProps> = ({
  horizons,
  milestones,
}) => {
  const horizonSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "horizon" | "steady" | "steadyAz" | "reinvested" | "reinvestedAz"
    >
  >(
    () => ({
      horizon: (row) => durationToSeconds(row.label) ?? (String(row.label || "").trim() || null),
      steady: {
        accessor: (row) => finiteNum(row.steady_points, null),
        defaultDirection: "desc",
      },
      steadyAz: (row) => String(row.steady_az || "").trim() || null,
      reinvested: {
        accessor: (row) => finiteNum(row.compound_points, null),
        defaultDirection: "desc",
      },
      reinvestedAz: (row) => String(row.compound_az || "").trim() || null,
    }),
    []
  );

  const milestoneSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "milestone" | "remaining" | "steadyEta" | "compoundEta"
    >
  >(
    () => ({
      milestone: (m) => finiteNum(m.target_points, null) ?? (String(m.label || "").trim() || null),
      remaining: (m) => finiteNum(m.missing_points, null),
      steadyEta: (m) => durationToSeconds(m.steady_eta),
      compoundEta: (m) => durationToSeconds(m.compound_eta),
    }),
    []
  );

  const horizonSort = useTableSort(horizons, horizonSortCols);
  const milestoneSort = useTableSort(milestones, milestoneSortCols);

  return (
    <div className="ranking-tables-stack">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Horizon"
                sortKey="horizon"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
              />
              <SortableTh
                label="Steady"
                sortKey="steady"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
                align="right"
              />
              <SortableTh
                label="AZ"
                sortKey="steadyAz"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Reinvested"
                sortKey="reinvested"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
                align="right"
              />
              <SortableTh
                label="AZ"
                sortKey="reinvestedAz"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody id="rnk-horizons-tbody">
            {horizonSort.sortedRows.length === 0 ? (
              <tr>
                <td colSpan={5} className="empty-cell">
                  Waiting for ranking forecast...
                </td>
              </tr>
            ) : (
              horizonSort.sortedRows.map((row, idx) => {
                const sp = finiteNum(row.steady_points, null);
                const sd = finiteNum(row.steady_delta, null);
                const cp = finiteNum(row.compound_points, null);
                const cd = finiteNum(row.compound_delta, null);
                return (
                  <tr key={row.label || idx}>
                    <td>{String(row.label || "—")}</td>
                    <td className="col-right tabular">
                      {sp !== null && sd !== null
                        ? `${fmtInt(Math.round(sp))} (${fmtSignedDeltaInt(sd)})`
                        : "—"}
                    </td>
                    <td className="col-right tabular">{String(row.steady_az || "—")}</td>
                    <td className="col-right tabular">
                      {cp !== null && cd !== null
                        ? `${fmtInt(Math.round(cp))} (${fmtSignedDeltaInt(cd)})`
                        : "—"}
                    </td>
                    <td className="col-right tabular">{String(row.compound_az || "—")}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Next Milestone"
                sortKey="milestone"
                activeKey={milestoneSort.sortKey}
                direction={milestoneSort.sortDirection}
                onSort={milestoneSort.requestSort}
              />
              <SortableTh
                label="Remaining"
                sortKey="remaining"
                activeKey={milestoneSort.sortKey}
                direction={milestoneSort.sortDirection}
                onSort={milestoneSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Steady ETA"
                sortKey="steadyEta"
                activeKey={milestoneSort.sortKey}
                direction={milestoneSort.sortDirection}
                onSort={milestoneSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Compound ETA"
                sortKey="compoundEta"
                activeKey={milestoneSort.sortKey}
                direction={milestoneSort.sortDirection}
                onSort={milestoneSort.requestSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody id="rnk-milestones-tbody">
            {milestoneSort.sortedRows.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty-cell">
                  Calculating milestones...
                </td>
              </tr>
            ) : (
              milestoneSort.sortedRows.map((m, idx) => {
                const tgtPts = finiteNum(m.target_points, null);
                const missPts = finiteNum(m.missing_points, null);
                return (
                  <tr key={m.label || idx}>
                    <td>{`${m.label} (${tgtPts !== null ? fmtInt(tgtPts) : "—"})`}</td>
                    <td className="col-right tabular">
                      {missPts !== null ? `+${fmtInt(Math.round(missPts))}` : "—"}
                    </td>
                    <td className="col-right tabular">{String(m.steady_eta || "—")}</td>
                    <td className="col-right tabular">{String(m.compound_eta || "—")}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
