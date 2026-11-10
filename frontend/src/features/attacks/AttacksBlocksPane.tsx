import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import { coordsSortKey, useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import { finiteNum, fmtDurationCountdown } from "../../utils/formatters";

export type BlockStatusFilter = "all" | "active" | "cleared";

export const BLOCK_STATUS_FILTERS: ReadonlyArray<{
  key: BlockStatusFilter;
  label: string;
}> = [
  { key: "all", label: "All Blocks" },
  { key: "active", label: "Active Cooldown" },
  { key: "cleared", label: "Cleared" },
];

export interface AttacksBlocksPaneProps {
  filteredBlocks: Array<Record<string, any>>;
  totalBlocksCount: number;
  blockFilter: BlockStatusFilter;
  onBlockFilterChange: (next: BlockStatusFilter) => void;
  atkTab: "battles" | "blocks";
  atkSearch: string;
  hidden: boolean;
  onSpyBlockTarget: (coords: string) => void;
}

export const AttacksBlocksPane: React.FC<AttacksBlocksPaneProps> = ({
  filteredBlocks,
  totalBlocksCount,
  blockFilter,
  onBlockFilterChange,
  atkTab,
  atkSearch,
  hidden,
  onSpyBlockTarget,
}) => {
  const blockSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "coords" | "owner" | "bashHits" | "cooldown" | "reason"
    >
  >(
    () => ({
      coords: (bt) => coordsSortKey(bt.coords),
      owner: (bt) => [bt.planet_name, bt.owner].filter(Boolean).join(" · ") || null,
      bashHits: {
        accessor: (bt) => finiteNum(bt.bash_hits, 0),
        defaultDirection: "desc",
      },
      cooldown: {
        accessor: (bt) => (bt.active_block ? finiteNum(bt.remaining_seconds, 0) : 0),
        defaultDirection: "desc",
      },
      reason: (bt) => String(bt.block_reason || "").trim() || null,
    }),
    []
  );

  const blockSort = useTableSort(filteredBlocks, blockSortCols);

  const blockPagination = useTablePagination(
    blockSort.sortedRows,
    `${atkTab}|${blockFilter}|${atkSearch}|${blockSort.sortKey ?? ""}|${blockSort.sortDirection ?? ""}`,
    25
  );

  return (
    <div className="intel-pane" id="atk-pane-blocks" hidden={hidden}>
      <div className="intel-filter-bar">
        <div className="filter-chips" role="group" aria-label="Block status filters">
          {BLOCK_STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`chip-btn${blockFilter === f.key ? " active" : ""}`}
              data-atk-block-filter={f.key}
              onClick={() => onBlockFilterChange(f.key)}
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
                activeKey={blockSort.sortKey}
                direction={blockSort.sortDirection}
                onSort={blockSort.requestSort}
              />
              <SortableTh
                label="Owner / Planet"
                sortKey="owner"
                activeKey={blockSort.sortKey}
                direction={blockSort.sortDirection}
                onSort={blockSort.requestSort}
              />
              <SortableTh
                label="Bash Hits"
                sortKey="bashHits"
                activeKey={blockSort.sortKey}
                direction={blockSort.sortDirection}
                onSort={blockSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Cooldown Left"
                sortKey="cooldown"
                activeKey={blockSort.sortKey}
                direction={blockSort.sortDirection}
                onSort={blockSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Server Protection Reason"
                sortKey="reason"
                activeKey={blockSort.sortKey}
                direction={blockSort.sortDirection}
                onSort={blockSort.requestSort}
              />
              <th className="col-right">Ops</th>
            </tr>
          </thead>
          <tbody id="atk-blocks-tbody">
            {blockPagination.pagedRows.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">
                  {totalBlocksCount > 0
                    ? "No bash or server blocks match the active filter or search query."
                    : "No active bash protection or server blocks recorded."}
                </td>
              </tr>
            ) : (
              blockPagination.pagedRows.map((bt, idx) => {
                const coords = String(bt.coords || "—");
                return (
                  <tr key={`${coords}-${idx}`}>
                    <td className="tabular">{coords}</td>
                    <td>
                      {[bt.planet_name, bt.owner].filter(Boolean).join(" · ") || "Unknown"}
                    </td>
                    <td className="col-right tabular">
                      {String(Math.round(finiteNum(bt.bash_hits, 0)))}
                    </td>
                    <td className="col-right tabular">
                      {bt.active_block
                        ? fmtDurationCountdown(bt.remaining_seconds)
                        : "Cleared"}
                    </td>
                    <td>{String(bt.block_reason || "Bash / Server protection")}</td>
                    <td className="col-right">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => onSpyBlockTarget(coords)}
                      >
                        Spy
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
        idPrefix="atk-blocks"
        totalItems={blockPagination.totalItems}
        page={blockPagination.page}
        pageSize={blockPagination.pageSize}
        itemLabel="blocks"
        onPageChange={blockPagination.setPage}
        onPageSizeChange={blockPagination.setPageSize}
      />
    </div>
  );
};
