import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { fmtShortDateTime } from "../../utils/formatters";

export interface RecyclersDebrisPaneProps {
  filteredDebris: Array<Record<string, any>>;
  totalDebrisCount: number;
  recTab: "harvests" | "debris" | "emails";
  recSearch: string;
  hidden: boolean;
  onRecycleDebris: (coords: string) => void;
}

export const RecyclersDebrisPane: React.FC<RecyclersDebrisPaneProps> = ({
  filteredDebris,
  totalDebrisCount,
  recTab,
  recSearch,
  hidden,
  onRecycleDebris,
}) => {
  const debrisSortCols = useMemo<
    SortColumnMap<Record<string, any>, "coords" | "owner" | "status" | "lastSeen">
  >(
    () => ({
      coords: (d) => coordsSortKey(d.coords),
      owner: (d) => [d.planet_name, d.owner].filter(Boolean).join(" · ") || "Orbital Debris",
      status: () => "RUBBLE FIELD",
      lastSeen: {
        accessor: (d) => timestampSortKey(d.last_scanned_ts),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const debrisSort = useTableSort(filteredDebris, debrisSortCols);

  const debrisPagination = useTablePagination(
    debrisSort.sortedRows,
    `${recTab}|${recSearch}|${debrisSort.sortKey ?? ""}|${debrisSort.sortDirection ?? ""}`,
    25
  );

  return (
    <div className="intel-pane" id="rec-pane-debris" hidden={hidden}>
      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Coords"
                sortKey="coords"
                activeKey={debrisSort.sortKey}
                direction={debrisSort.sortDirection}
                onSort={debrisSort.requestSort}
              />
              <SortableTh
                label="Planet / Owner"
                sortKey="owner"
                activeKey={debrisSort.sortKey}
                direction={debrisSort.sortDirection}
                onSort={debrisSort.requestSort}
              />
              <SortableTh
                label="Status"
                sortKey="status"
                activeKey={debrisSort.sortKey}
                direction={debrisSort.sortDirection}
                onSort={debrisSort.requestSort}
              />
              <SortableTh
                label="Last Seen"
                sortKey="lastSeen"
                activeKey={debrisSort.sortKey}
                direction={debrisSort.sortDirection}
                onSort={debrisSort.requestSort}
                align="right"
              />
              <th className="col-right">Dispatch</th>
            </tr>
          </thead>
          <tbody id="rec-debris-tbody">
            {debrisPagination.pagedRows.length === 0 ? (
              <tr>
                <td colSpan={5} className="empty-cell">
                  {totalDebrisCount > 0
                    ? "No debris fields match the active search query."
                    : "No active floating debris fields spotted in scanned systems."}
                </td>
              </tr>
            ) : (
              debrisPagination.pagedRows.map((d, idx) => {
                const coords = String(d.coords || "—");
                return (
                  <tr key={`${coords}-${idx}`}>
                    <td className="tabular">{coords}</td>
                    <td>
                      {[d.planet_name, d.owner].filter(Boolean).join(" · ") || "Orbital Debris"}
                    </td>
                    <td>
                      <span className="status-word">RUBBLE FIELD</span>
                    </td>
                    <td className="col-right tabular">{fmtShortDateTime(d.last_scanned_ts)}</td>
                    <td className="col-right">
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={() => onRecycleDebris(coords)}
                      >
                        Recycle
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="rec-debris"
        totalItems={debrisPagination.totalItems}
        page={debrisPagination.page}
        pageSize={debrisPagination.pageSize}
        itemLabel="debris fields"
        onPageChange={debrisPagination.setPage}
        onPageSizeChange={debrisPagination.setPageSize}
      />
    </div>
  );
};
