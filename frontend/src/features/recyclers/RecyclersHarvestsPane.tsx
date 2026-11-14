import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { finiteNum, fmtInt, fmtShortDateTime } from "../../utils/formatters";

export interface RecyclersHarvestsPaneProps {
  filteredRecycles: Array<Record<string, any>>;
  totalRecyclesCount: number;
  recTab: "harvests" | "debris" | "emails";
  recSearch: string;
  hidden: boolean;
}

export const RecyclersHarvestsPane: React.FC<RecyclersHarvestsPaneProps> = ({
  filteredRecycles,
  totalRecyclesCount,
  recTab,
  recSearch,
  hidden,
}) => {
  const recycleSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "time" | "coords" | "recyclers" | "pigIron" | "metal" | "spiceEq" | "fieldBefore"
    >
  >(
    () => ({
      time: {
        accessor: (r) => timestampSortKey(r.ts, r.msg_date_text),
        defaultDirection: "desc",
      },
      coords: (r) => coordsSortKey(r.coords),
      recyclers: {
        accessor: (r) => {
          const c = finiteNum(r.recycler_count, null);
          return c !== null && c > 0 ? c : null;
        },
        defaultDirection: "desc",
      },
      pigIron: {
        accessor: (r) => finiteNum(r.harvested_pig_iron, 0),
        defaultDirection: "desc",
      },
      metal: {
        accessor: (r) => finiteNum(r.harvested_metal, 0),
        defaultDirection: "desc",
      },
      spiceEq: {
        accessor: (r) => finiteNum(r.spice_eq, 0),
        defaultDirection: "desc",
      },
      fieldBefore: {
        accessor: (r) => {
          const extra =
            finiteNum(r.harvested_kryptonite, 0) + finiteNum(r.harvested_spice, 0);
          if (extra > 0) return extra;
          const field = finiteNum(r.field_pig_iron, 0) + finiteNum(r.field_metal, 0);
          return field > 0 ? field : null;
        },
        defaultDirection: "desc",
      },
    }),
    []
  );

  const recycleSort = useTableSort(filteredRecycles, recycleSortCols);

  const recyclePagination = useTablePagination(
    recycleSort.sortedRows,
    `${recTab}|${recSearch}|${recycleSort.sortKey ?? ""}|${recycleSort.sortDirection ?? ""}`,
    25
  );

  return (
    <div className="intel-pane" id="rec-pane-harvests" hidden={hidden}>
      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Time"
                sortKey="time"
                activeKey={recycleSort.sortKey}
                direction={recycleSort.sortDirection}
                onSort={recycleSort.requestSort}
              />
              <SortableTh
                label="Debris Coords"
                sortKey="coords"
                activeKey={recycleSort.sortKey}
                direction={recycleSort.sortDirection}
                onSort={recycleSort.requestSort}
              />
              <SortableTh
                label="Recyclers / Cap"
                sortKey="recyclers"
                activeKey={recycleSort.sortKey}
                direction={recycleSort.sortDirection}
                onSort={recycleSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Pig Iron"
                sortKey="pigIron"
                activeKey={recycleSort.sortKey}
                direction={recycleSort.sortDirection}
                onSort={recycleSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Metal"
                sortKey="metal"
                activeKey={recycleSort.sortKey}
                direction={recycleSort.sortDirection}
                onSort={recycleSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Harvest SpEq"
                sortKey="spiceEq"
                activeKey={recycleSort.sortKey}
                direction={recycleSort.sortDirection}
                onSort={recycleSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Field Before"
                sortKey="fieldBefore"
                activeKey={recycleSort.sortKey}
                direction={recycleSort.sortDirection}
                onSort={recycleSort.requestSort}
              />
            </tr>
          </thead>
          <tbody id="rec-harvests-tbody">
            {recyclePagination.pagedRows.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-cell">
                  {totalRecyclesCount > 0
                    ? "No recycler harvests match the active search query."
                    : "Click Sync All Folders to archive fleet recycling reports."}
                </td>
              </tr>
            ) : (
              recyclePagination.pagedRows.map((r, idx) => {
                const rCnt = Math.round(finiteNum(r.recycler_count, 0));
                const rCap = Math.round(finiteNum(r.cargo_capacity, 0));
                const bPig = Math.round(finiteNum(r.field_pig_iron, 0));
                const bMet = Math.round(finiteNum(r.field_metal, 0));
                const hKry = Math.round(finiteNum(r.harvested_kryptonite, 0));
                const hSpi = Math.round(finiteNum(r.harvested_spice, 0));
                const befText =
                  hKry > 0 || hSpi > 0
                    ? `+${fmtInt(hKry)} Kry · +${fmtInt(hSpi)} Spi`
                    : bPig > 0 || bMet > 0
                      ? `${fmtInt(bPig)} Pig · ${fmtInt(bMet)} Met`
                      : "—";

                return (
                  <tr key={r.id ?? `${r.coords}-${idx}`}>
                    <td className="tabular">{fmtShortDateTime(r.ts, r.msg_date_text)}</td>
                    <td className="tabular">{String(r.coords || "—")}</td>
                    <td className="col-right tabular">
                      {rCnt > 0 ? `${fmtInt(rCnt)}x (${fmtInt(rCap)})` : "—"}
                    </td>
                    <td className="col-right tabular">{fmtInt(r.harvested_pig_iron ?? 0)}</td>
                    <td className="col-right tabular">{fmtInt(r.harvested_metal ?? 0)}</td>
                    <td className="col-right tabular">
                      {`${fmtInt(Math.round(finiteNum(r.spice_eq, 0)))} SpEq`}
                    </td>
                    <td className="tabular">{befText}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="rec-harvests"
        totalItems={recyclePagination.totalItems}
        page={recyclePagination.page}
        pageSize={recyclePagination.pageSize}
        itemLabel="harvests"
        onPageChange={recyclePagination.setPage}
        onPageSizeChange={recyclePagination.setPageSize}
      />
    </div>
  );
};
