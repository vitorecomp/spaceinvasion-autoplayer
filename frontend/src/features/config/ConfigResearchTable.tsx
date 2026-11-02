import React, { useMemo } from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import {
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import type { UniverseResearchMaxRow } from "../../types/api";
import { fmtShortUpdated } from "./configFormHelpers";

export interface ConfigResearchTableProps {
  researchRows: UniverseResearchMaxRow[];
}

export const ConfigResearchTable: React.FC<ConfigResearchTableProps> = ({ researchRows }) => {
  const researchSortCols = useMemo<
    SortColumnMap<
      UniverseResearchMaxRow,
      "research" | "ownLevel" | "uniMax" | "holder" | "catalogCap" | "updated"
    >
  >(
    () => ({
      research: (row) => String(row.name || row.key || row.research_key || ""),
      ownLevel: {
        accessor: (row) => Math.max(0, Math.round(safeFiniteNumber(row.own_level, 0) ?? 0)),
        defaultDirection: "desc",
      },
      uniMax: {
        accessor: (row) =>
          Math.max(0, Math.round(safeFiniteNumber(row.universe_max_level, 0) ?? 0)),
        defaultDirection: "desc",
      },
      holder: (row) =>
        [row.holder, row.holder_coords ? `[${row.holder_coords}]` : ""]
          .filter(Boolean)
          .join(" ") || null,
      catalogCap: {
        accessor: (row) => {
          const cap = safeFiniteNumber(row.catalog_max_level, null);
          return cap !== null && cap > 0 ? Math.round(cap) : null;
        },
        defaultDirection: "desc",
      },
      updated: {
        accessor: (row) => timestampSortKey(row.updated_ts),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const researchSort = useTableSort(researchRows, researchSortCols);

  return (
    <section
      className="config-research-section"
      id="cfg-universe-research-section"
      aria-labelledby="cfg-grp-univ-research"
    >
      <div className="config-group-head">
        <h3 id="cfg-grp-univ-research">
          Universe Max Research Levels — Persisted in SQLite (universe_research_max)
        </h3>
        <p className="config-group-sub">
          Highest observed research levels across server statistics, archived espionage reports, and your empire, stored monotonically in SQLite table universe_research_max. Click Refresh Max Research above to sync and persist the latest records.
        </p>
      </div>
      <div className="table-wrap">
        <table id="cfg-universe-research-max-table">
          <thead>
            <tr>
              <SortableTh
                label="Research"
                sortKey="research"
                activeKey={researchSort.sortKey}
                direction={researchSort.sortDirection}
                onSort={researchSort.requestSort}
              />
              <SortableTh
                label="Your Level"
                sortKey="ownLevel"
                activeKey={researchSort.sortKey}
                direction={researchSort.sortDirection}
                onSort={researchSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Universe Max"
                sortKey="uniMax"
                activeKey={researchSort.sortKey}
                direction={researchSort.sortDirection}
                onSort={researchSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Record Holder"
                sortKey="holder"
                activeKey={researchSort.sortKey}
                direction={researchSort.sortDirection}
                onSort={researchSort.requestSort}
              />
              <SortableTh
                label="Catalog Cap"
                sortKey="catalogCap"
                activeKey={researchSort.sortKey}
                direction={researchSort.sortDirection}
                onSort={researchSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Updated"
                sortKey="updated"
                activeKey={researchSort.sortKey}
                direction={researchSort.sortDirection}
                onSort={researchSort.requestSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody id="cfg-universe-research-max-tbody">
            {researchSort.sortedRows.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">
                  Click Refresh Max Research to populate and persist universe research records in SQLite.
                </td>
              </tr>
            ) : (
              researchSort.sortedRows.map((row) => {
                const rKey = String(row.key || row.research_key || `r${row.research_id}`);
                const ownLvl = Math.max(0, Math.round(safeFiniteNumber(row.own_level, 0) ?? 0));
                const maxLvl = Math.max(
                  0,
                  Math.round(safeFiniteNumber(row.universe_max_level, 0) ?? 0)
                );
                const capLvl = safeFiniteNumber(row.catalog_max_level, null);
                const holderTxt =
                  [row.holder, row.holder_coords ? `[${row.holder_coords}]` : ""]
                    .filter(Boolean)
                    .join(" ") || "—";
                return (
                  <tr key={rKey}>
                    <td>
                      <span className="action-title">{row.name}</span>{" "}
                      <span className="action-sub tabular">({rKey})</span>
                    </td>
                    <td className="col-right tabular">{`L${ownLvl}`}</td>
                    <td className="col-right tabular">{`L${maxLvl}`}</td>
                    <td>{holderTxt}</td>
                    <td className="col-right tabular">
                      {capLvl !== null && capLvl > 0 ? `L${Math.round(capLvl)}` : "Uncapped"}
                    </td>
                    <td className="col-right tabular">{fmtShortUpdated(row.updated_ts)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
};
