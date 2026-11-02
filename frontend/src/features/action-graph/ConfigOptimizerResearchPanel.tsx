import React from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import type { SortDirection } from "../../hooks/useTableSort";
import type { ResearchPlanRow } from "./configOptimizerDraft";

export type ResearchSortKey = "name" | "current" | "uniMax" | "planned" | "gap";

export interface ConfigOptimizerResearchPanelProps {
  hidden: boolean;
  pagedResearch: ResearchPlanRow[];
  totalItems: number;
  page: number;
  pageSize: number;
  sortKey: ResearchSortKey | null;
  sortDirection: SortDirection;
  onRequestSort: (key: ResearchSortKey) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

export const ConfigOptimizerResearchPanel: React.FC<ConfigOptimizerResearchPanelProps> = ({
  hidden,
  pagedResearch,
  totalItems,
  page,
  pageSize,
  sortKey,
  sortDirection,
  onRequestSort,
  onPageChange,
  onPageSizeChange,
}) => {
  return (
    <div className="ag-co-content" hidden={hidden}>
      <div className="table-wrap table-scroll">
        <table id="co-research-table">
          <thead>
            <tr>
              <SortableTh
                label="Research (r1–r15)"
                sortKey="name"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
              />
              <SortableTh
                label="Current"
                sortKey="current"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <SortableTh
                label="Universe Max"
                sortKey="uniMax"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <SortableTh
                label="Planned Target"
                sortKey="planned"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <SortableTh
                label="Levels to Build"
                sortKey="gap"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="co-research-tbody">
            {pagedResearch.map((r) => (
              <tr key={r.key}>
                <td>
                  <span className="action-title">{r.name}</span>{" "}
                  <span className="action-sub tabular">{`(${r.key})`}</span>
                </td>
                <td className="col-right tabular">{`L${r.current}`}</td>
                <td className="col-right tabular">{`L${r.universeMax}`}</td>
                <td className="col-right tabular">{`L${r.planned}`}</td>
                <td className="col-right tabular">{r.gap > 0 ? `+${r.gap}` : "0"}</td>
                <td>
                  <span className={`eta-pill tabular${r.gap > 0 ? " is-wait" : ""}`}>
                    {r.gap > 0 ? `UPGRADE +${r.gap}` : "AT TARGET"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="co-research"
        totalItems={totalItems}
        page={page}
        pageSize={pageSize}
        itemLabel="technologies"
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </div>
  );
};
