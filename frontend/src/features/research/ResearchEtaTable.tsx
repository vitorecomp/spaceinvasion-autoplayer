import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import {
  durationToSeconds,
  resourcesTotal,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { bottleneckTag, finiteNum, formatResourcesInline } from "../../utils/formatters";

export interface ResearchEtaTableProps {
  hasEtaData: boolean;
  visibleRows: Array<Record<string, any>>;
  selectedRow: Record<string, any> | null;
  onSelectResearch: (rowKey: string) => void;
  onLiveResearch: (ev: React.MouseEvent, row: Record<string, any>) => void;
  onPinQueue: (ev: React.MouseEvent, row: Record<string, any>) => void;
}

export const ResearchEtaTable: React.FC<ResearchEtaTableProps> = ({
  hasEtaData,
  visibleRows,
  selectedRow,
  onSelectResearch,
  onLiveResearch,
  onPinQueue,
}) => {
  const researchSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "unit" | "level" | "next" | "missing" | "buildTime" | "resReady" | "reached"
    >
  >(
    () => ({
      unit: (row) => String(row.name || row.key || ""),
      level: {
        accessor: (row) => finiteNum(row.current_level, 0),
        defaultDirection: "desc",
      },
      next: {
        accessor: (row) => finiteNum(row.next_level ?? row.target_level, null),
        defaultDirection: "desc",
      },
      missing: (row) => finiteNum(row.missing_total, null) ?? resourcesTotal(row.missing) ?? 0,
      buildTime: (row) =>
        finiteNum(row.build_seconds ?? row.build_time_seconds, null) ??
        durationToSeconds(row.build_time_text),
      resReady: (row) => {
        if (row.wait_eta === "—") return null;
        const wh = finiteNum(row.wait_hours, null);
        if (wh !== null) return wh * 3600;
        return durationToSeconds(row.wait_eta);
      },
      reached: (row) => {
        if (row.reach_eta === "—") return null;
        const rh = finiteNum(row.reach_hours ?? row.total_hours, null);
        if (rh !== null) return rh * 3600;
        return durationToSeconds(row.reach_eta);
      },
    }),
    []
  );

  const researchSort = useTableSort(visibleRows, researchSortCols);

  return (
    <div className="table-wrap table-scroll">
      <table id="res-table">
        <thead>
          <tr>
            <SortableTh
              label="Technology"
              sortKey="unit"
              activeKey={researchSort.sortKey}
              direction={researchSort.sortDirection}
              onSort={researchSort.requestSort}
            />
            <SortableTh
              label="Lvl"
              sortKey="level"
              activeKey={researchSort.sortKey}
              direction={researchSort.sortDirection}
              onSort={researchSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Next"
              sortKey="next"
              activeKey={researchSort.sortKey}
              direction={researchSort.sortDirection}
              onSort={researchSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Missing Resources"
              sortKey="missing"
              activeKey={researchSort.sortKey}
              direction={researchSort.sortDirection}
              onSort={researchSort.requestSort}
            />
            <SortableTh
              label="Research Time"
              sortKey="buildTime"
              activeKey={researchSort.sortKey}
              direction={researchSort.sortDirection}
              onSort={researchSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Res Ready"
              sortKey="resReady"
              activeKey={researchSort.sortKey}
              direction={researchSort.sortDirection}
              onSort={researchSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Level Reached"
              sortKey="reached"
              activeKey={researchSort.sortKey}
              direction={researchSort.sortDirection}
              onSort={researchSort.requestSort}
              align="right"
            />
            <th className="col-right">Control</th>
          </tr>
        </thead>
        <tbody id="res-eta-tbody">
          {!hasEtaData ? (
            <tr>
              <td colSpan={8} className="empty-cell">
                Waiting for research catalog...
              </td>
            </tr>
          ) : researchSort.sortedRows.length === 0 ? (
            <tr>
              <td colSpan={8} className="empty-cell">
                No research technologies in selected filter.
              </td>
            </tr>
          ) : (
            researchSort.sortedRows.map((row: any, idx: number) => {
              const rowKey = row.key != null ? String(row.key).trim() : "";
              const isSelected = Boolean(selectedRow && selectedRow.key === row.key);
              const curLvl = finiteNum(row.current_level, 0);
              const missTotal = finiteNum(row.missing_total, 0);
              const waitEtaText = row.wait_eta ?? "—";
              const bnTag =
                typeof row.bottleneck === "string" && row.bottleneck.trim()
                  ? bottleneckTag(row.bottleneck)
                  : "";
              const reachEtaText = row.reach_eta ?? "—";
              const rowWaitH = finiteNum(row.wait_hours, 0);
              const canExec = Boolean(row.can_research_live);
              const unitId = row.research_id;
              const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
              const safeName = String(row.name || "Research").replace(/>/g, "→");

              return (
                <tr
                  key={rowKey || `research-${idx}`}
                  className={`clickable-row${isSelected ? " row-selected" : ""}`}
                  onClick={() => {
                    if (rowKey) {
                      onSelectResearch(rowKey);
                    }
                  }}
                >
                  <td>
                    <div className="action-title">
                      {row.name || "Research"}
                      {rowKey ? (
                        <span className="action-sub tabular">{` (${rowKey})`}</span>
                      ) : null}
                      {row.queued_count > 0 || row.active ? (
                        <span className="rate-chip">
                          {row.active ? "ACTIVE" : `Q x${row.queued_count}`}
                        </span>
                      ) : null}
                    </div>
                  </td>

                  <td className="col-right tabular">{String(curLvl)}</td>
                  <td className="col-right tabular">
                    {`L${row.next_level ?? row.target_level ?? "—"}`}
                  </td>

                  <td className="tabular">
                    {missTotal === 0 ? (
                      <span className="status-word">Affordable</span>
                    ) : (
                      formatResourcesInline(row.missing)
                    )}
                  </td>

                  <td className="col-right tabular">{row.build_time_text ?? "—"}</td>
                  <td className="col-right tabular">
                    {bnTag ? `${waitEtaText} (${bnTag})` : waitEtaText}
                  </td>

                  <td className="col-right tabular">
                    <span
                      className={`eta-pill${reachEtaText === "—" ? " is-blocked" : rowWaitH > 0 ? " is-wait" : ""}`}
                    >
                      {reachEtaText}
                    </span>
                  </td>

                  <td className="col-right">
                    <div className="row-actions-wrap">
                      {canExec ? (
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={(ev) => onLiveResearch(ev, row)}
                        >
                          Research
                        </button>
                      ) : Array.isArray(row.unmet_prerequisites) &&
                        row.unmet_prerequisites.length > 0 ? (
                        <span className="action-sub">Locked</span>
                      ) : null}

                      {unitId != null && nextLvl !== null && nextLvl > 0 ? (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          title={`Add ${safeName} L${nextLvl} to P1 priority queue`}
                          aria-label={`Add ${safeName} L${nextLvl} to P1 priority queue`}
                          onClick={(ev) => onPinQueue(ev, row)}
                        >
                          + Queue
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
};
