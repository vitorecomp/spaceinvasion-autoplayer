import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import {
  durationToSeconds,
  resourcesTotal,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  bottleneckTag,
  clampPct,
  finiteNum,
  formatResourcesInline,
} from "../../utils/formatters";

export interface BuildingsEtaTableProps {
  hasEtaData: boolean;
  visibleRows: Array<Record<string, any>>;
  selectedRow: Record<string, any> | null;
  getGoalLevelForRow: (row: Record<string, any> | null | undefined) => number | null;
  onSelectUnit: (rowKey: string) => void;
  onLiveBuild: (ev: React.MouseEvent, row: Record<string, any>) => void;
  onPinQueue: (ev: React.MouseEvent, row: Record<string, any>) => void;
  onDemolishB6: (ev: React.MouseEvent, row: Record<string, any>) => void;
}

export const BuildingsEtaTable: React.FC<BuildingsEtaTableProps> = ({
  hasEtaData,
  visibleRows,
  selectedRow,
  getGoalLevelForRow,
  onSelectUnit,
  onLiveBuild,
  onPinQueue,
  onDemolishB6,
}) => {
  const unitSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "unit" | "level" | "goal" | "next" | "missing" | "buildTime" | "resReady" | "reached"
    >
  >(
    () => ({
      unit: (row) => String(row.name || row.key || ""),
      level: {
        accessor: (row) => finiteNum(row.current_level, 0),
        defaultDirection: "desc",
      },
      goal: {
        accessor: (row) => getGoalLevelForRow(row),
        defaultDirection: "desc",
      },
      next: {
        accessor: (row) =>
          row.is_defense_turret ? null : finiteNum(row.next_level ?? row.target_level, null),
        defaultDirection: "desc",
      },
      missing: (row) =>
        row.is_defense_turret
          ? resourcesTotal(row.cost)
          : (finiteNum(row.missing_total, null) ?? resourcesTotal(row.missing) ?? 0),
      buildTime: (row) =>
        finiteNum(row.build_seconds ?? row.build_time_seconds, null) ??
        durationToSeconds(row.build_time_text),
      resReady: (row) => {
        if (row.is_defense_turret || row.wait_eta === "—") return null;
        const wh = finiteNum(row.wait_hours, null);
        if (wh !== null) return wh * 3600;
        return durationToSeconds(row.wait_eta);
      },
      reached: (row) => {
        if (row.is_defense_turret || row.reach_eta === "—") return null;
        const rh = finiteNum(row.reach_hours ?? row.total_hours, null);
        if (rh !== null) return rh * 3600;
        return durationToSeconds(row.reach_eta);
      },
    }),
    [getGoalLevelForRow]
  );

  const unitSort = useTableSort(visibleRows, unitSortCols);

  return (
    <div className="table-wrap table-scroll">
      <table id="bld-table">
        <thead>
          <tr>
            <SortableTh
              label="Unit"
              sortKey="unit"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
            />
            <SortableTh
              label="Lvl"
              sortKey="level"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Goal"
              sortKey="goal"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
              align="right"
              title="Target building level defined by the active Config Optimizer run"
            />
            <SortableTh
              label="Next"
              sortKey="next"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Missing Resources"
              sortKey="missing"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
            />
            <SortableTh
              label="Build Time"
              sortKey="buildTime"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Res Ready"
              sortKey="resReady"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
              align="right"
            />
            <SortableTh
              label="Level Reached"
              sortKey="reached"
              activeKey={unitSort.sortKey}
              direction={unitSort.sortDirection}
              onSort={unitSort.requestSort}
              align="right"
            />
            <th className="col-right">Control</th>
          </tr>
        </thead>
        <tbody id="eta-table-tbody">
          {!hasEtaData ? (
            <tr>
              <td colSpan={9} className="empty-cell">
                Waiting for building catalog...
              </td>
            </tr>
          ) : unitSort.sortedRows.length === 0 ? (
            <tr>
              <td colSpan={9} className="empty-cell">
                No items in selected category.
              </td>
            </tr>
          ) : (
            unitSort.sortedRows.map((row: any, idx: number) => {
              const rowKey = row.key != null ? String(row.key).trim() : "";
              const isSelected = Boolean(selectedRow && selectedRow.key === row.key);
              const curLvl = finiteNum(row.current_level, 0);
              const goalLvl = getGoalLevelForRow(row);
              const rowPct = finiteNum(row.economy_rate_pct, null);
              const clampedRowPct = rowPct !== null ? clampPct(Math.round(rowPct)) : null;
              const missTotal = finiteNum(row.missing_total, 0);
              const waitEtaText = row.wait_eta ?? "—";
              const bnTag =
                typeof row.bottleneck === "string" && row.bottleneck.trim()
                  ? bottleneckTag(row.bottleneck)
                  : "";
              const reachEtaText = row.reach_eta ?? "—";
              const rowWaitH = finiteNum(row.wait_hours, 0);
              const isTurret = Boolean(row.is_defense_turret);
              const canExec = isTurret ? false : Boolean(row.can_build_live);
              const unitId = row.building_id;
              const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
              const safeName = String(row.name || "Unit").replace(/>/g, "→");

              return (
                <tr
                  key={rowKey || `unit-${idx}`}
                  className={`clickable-row${isSelected ? " row-selected" : ""}`}
                  onClick={() => {
                    if (rowKey) {
                      onSelectUnit(rowKey);
                    }
                  }}
                >
                  <td>
                    <div className="action-title">
                      {row.name || "Unit"}
                      {rowKey ? (
                        <span className="action-sub tabular">{` (${rowKey})`}</span>
                      ) : null}
                      {row.queued_count > 0 || row.active ? (
                        <span className="rate-chip">
                          {row.active ? "ACTIVE" : `Q x${row.queued_count}`}
                        </span>
                      ) : null}
                      {clampedRowPct !== null && curLvl > 0 ? (
                        <span
                          className={`rate-chip tabular${clampedRowPct < 100 ? " is-paused" : ""}`}
                        >
                          {`${clampedRowPct}%`}
                        </span>
                      ) : null}
                    </div>
                  </td>

                  <td className="col-right tabular">{String(curLvl)}</td>
                  <td className="col-right tabular" data-col="goal">
                    {isTurret || goalLvl === null ? (
                      "—"
                    ) : goalLvl === 0 && curLvl === 0 ? (
                      <span
                        className="action-sub"
                        title="Config Optimizer target: L0 (not targeted on this planet)"
                      >
                        L0
                      </span>
                    ) : curLvl >= goalLvl ? (
                      <span
                        className="status-word"
                        title={`Config Optimizer goal reached (L${goalLvl})`}
                      >
                        {`L${goalLvl}`}
                      </span>
                    ) : (
                      <span
                        title={`Config Optimizer goal: L${goalLvl} (+${goalLvl - curLvl} remaining)`}
                      >
                        {`L${goalLvl}`}
                        <span className="action-sub">{` +${goalLvl - curLvl}`}</span>
                      </span>
                    )}
                  </td>
                  <td className="col-right tabular">
                    {isTurret ? "—" : `L${row.next_level ?? row.target_level ?? "—"}`}
                  </td>

                  <td className="tabular">
                    {isTurret ? (
                      formatResourcesInline(row.cost)
                    ) : missTotal === 0 ? (
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
                          onClick={(ev) => onLiveBuild(ev, row)}
                        >
                          Build
                        </button>
                      ) : Array.isArray(row.unmet_prerequisites) &&
                        row.unmet_prerequisites.length > 0 ? (
                        <span className="action-sub">Locked</span>
                      ) : null}

                      {!isTurret && unitId != null && nextLvl !== null && nextLvl > 0 ? (
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

                      {!isTurret && row.building_id === 6 && curLvl > 0 ? (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm btn-danger-ghost"
                          title="Demolish Thermosolar plant (b6)"
                          onClick={(ev) => onDemolishB6(ev, row)}
                        >
                          Demolish
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
