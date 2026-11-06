import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  resourcesTotal,
  unitDictTotal,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  finiteNum,
  fmtInt,
  formatResourcesInline,
  formatUnitDictShort,
} from "../../utils/formatters";
import {
  espionageDistanceSortKey,
  formatEspionageDistance,
  TARGET_FILTERS,
  type TargetFilterKey,
} from "./espionageHelpers";

export interface EspionageTargetsPaneProps {
  filteredTargets: Array<Record<string, any>>;
  totalTargetsCount: number;
  originCoords: string;
  targetFilter: TargetFilterKey;
  onTargetFilterChange: (next: TargetFilterKey) => void;
  espTab: "targets" | "reports";
  espSearch: string;
  hidden: boolean;
  onSpyTarget: (coords: string) => void;
  onSimTarget: (target: Record<string, any>) => void;
  onAttackTarget: (coords: string) => void;
}

export const EspionageTargetsPane: React.FC<EspionageTargetsPaneProps> = ({
  filteredTargets,
  totalTargetsCount,
  originCoords,
  targetFilter,
  onTargetFilterChange,
  espTab,
  espSearch,
  hidden,
  onSpyTarget,
  onSimTarget,
  onAttackTarget,
}) => {
  const targetSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "coords" | "distance" | "player" | "status" | "fields" | "resources" | "spiceEq" | "fleetDef"
    >
  >(
    () => ({
      coords: (t) => coordsSortKey(t.coords || t.target_coords),
      distance: (t) =>
        espionageDistanceSortKey(t, originCoords, String(t.coords || t.target_coords || "")),
      player: (t) => [t.planet_name, t.owner].filter(Boolean).join(" · ") || null,
      status: (t) => {
        const isSpacia = Boolean(t.is_spacia);
        const isInact = Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
        const tags = [
          isSpacia
            ? "SPACIA"
            : isInact
              ? "INACTIVE"
              : String(t.status_color || "ACTIVE").toUpperCase(),
        ];
        if (t.has_debris) tags.push("DEBRIS");
        if (t.dug_in) tags.push("DUG-IN");
        return tags.join(" · ");
      },
      fields: {
        accessor: (t) => {
          const uf = finiteNum(t.uninhabited_fields, null);
          return uf !== null && uf > 0 ? Math.round(uf) : null;
        },
        defaultDirection: "desc",
      },
      resources: {
        accessor: (t) => (t.has_spy_intel ? resourcesTotal(t.resources) : null),
        defaultDirection: "desc",
      },
      spiceEq: {
        accessor: (t) => (t.has_spy_intel ? finiteNum(t.spice_eq, null) : null),
        defaultDirection: "desc",
      },
      fleetDef: {
        accessor: (t) =>
          t.has_spy_intel
            ? (unitDictTotal(t.fleet) ?? 0) + (unitDictTotal(t.defense) ?? 0)
            : null,
        defaultDirection: "desc",
      },
    }),
    [originCoords]
  );

  const targetSort = useTableSort(filteredTargets, targetSortCols);

  const targetPagination = useTablePagination(
    targetSort.sortedRows,
    `${espTab}|${targetFilter}|${espSearch}|${targetSort.sortKey ?? ""}|${targetSort.sortDirection ?? ""}`,
    25
  );

  return (
    <div className="intel-pane" id="esp-pane-targets" hidden={hidden}>
      <div className="intel-filter-bar">
        <div className="filter-chips" role="group" aria-label="Espionage target filters">
          {TARGET_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`chip-btn${targetFilter === f.key ? " active" : ""}`}
              data-esp-filter={f.key}
              onClick={() => onTargetFilterChange(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Coords"
                sortKey="coords"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
              />
              <SortableTh
                label="Distance"
                sortKey="distance"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Player / Planet"
                sortKey="player"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
              />
              <SortableTh
                label="Status"
                sortKey="status"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
              />
              <SortableTh
                label="Fields"
                sortKey="fields"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Resources (Pig / Met / Kry / Spi)"
                sortKey="resources"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
              />
              <SortableTh
                label="Loot SpEq"
                sortKey="spiceEq"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Fleet / Defense"
                sortKey="fleetDef"
                activeKey={targetSort.sortKey}
                direction={targetSort.sortDirection}
                onSort={targetSort.requestSort}
              />
              <th className="col-right">Ops</th>
            </tr>
          </thead>
          <tbody id="esp-targets-tbody">
            {targetPagination.pagedRows.length === 0 ? (
              <tr>
                <td colSpan={9} className="empty-cell">
                  {totalTargetsCount > 0
                    ? "No espionage targets match the active filter or search query."
                    : "No espionage targets recorded yet. Run Scan Galaxy or Sync All Folders."}
                </td>
              </tr>
            ) : (
              targetPagination.pagedRows.map((t, idx) => {
                const coords = String(t.coords || t.target_coords || "—");
                const dist = formatEspionageDistance(t, originCoords, coords);
                const isSpacia = Boolean(t.is_spacia);
                const isInact =
                  Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
                const tags = [
                  isSpacia
                    ? "SPACIA"
                    : isInact
                      ? "INACTIVE"
                      : String(t.status_color || "ACTIVE").toUpperCase(),
                ];
                if (t.has_debris) tags.push("DEBRIS");
                if (t.dug_in) tags.push("DUG-IN");
                const uf = finiteNum(t.uninhabited_fields, null);
                const fStr = formatUnitDictShort(t.fleet, 3);
                const dStr = formatUnitDictShort(t.defense, 3);
                const fdTxt = t.has_spy_intel
                  ? fStr === "None" && dStr === "None"
                    ? "Clear (0)"
                    : `F:${fStr} / D:${dStr}`
                  : "—";

                return (
                  <tr key={`${coords}-${idx}`}>
                    <td className="tabular">{coords}</td>
                    <td className="col-right tabular" title={dist.title}>
                      {dist.label}
                    </td>
                    <td>
                      {[t.planet_name, t.owner].filter(Boolean).join(" · ") ||
                        "Uninhabited / Unknown"}
                    </td>
                    <td>
                      <span className={`status-word${isSpacia || isInact ? "" : " is-warn"}`}>
                        {tags.join(" · ")}
                      </span>
                    </td>
                    <td className="col-right tabular">
                      {uf !== null && uf > 0 ? String(Math.round(uf)) : "—"}
                    </td>
                    <td className="tabular">
                      {t.has_spy_intel ? formatResourcesInline(t.resources) : "Not spied yet"}
                    </td>
                    <td className="col-right tabular">
                      {t.has_spy_intel ? fmtInt(Math.round(finiteNum(t.spice_eq, 0))) : "—"}
                    </td>
                    <td className="tabular">{fdTxt}</td>
                    <td className="col-right">
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => onSpyTarget(coords)}
                        >
                          Spy
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => onSimTarget(t)}
                        >
                          Sim
                        </button>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={() => onAttackTarget(coords)}
                        >
                          Attack
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="esp-targets"
        totalItems={targetPagination.totalItems}
        page={targetPagination.page}
        pageSize={targetPagination.pageSize}
        itemLabel="targets"
        onPageChange={targetPagination.setPage}
        onPageSizeChange={targetPagination.setPageSize}
      />
    </div>
  );
};
