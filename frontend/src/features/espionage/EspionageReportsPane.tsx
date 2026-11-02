import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  resourcesTotal,
  timestampSortKey,
  unitDictTotal,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  finiteNum,
  fmtInt,
  fmtShortDateTime,
  formatResourcesInline,
  formatUnitDictShort,
} from "../../utils/formatters";
import { espionageDistanceSortKey, formatEspionageDistance } from "./espionageHelpers";

export interface EspionageReportsPaneProps {
  filteredReports: Array<Record<string, any>>;
  totalReportsCount: number;
  originCoords: string;
  espTab: "targets" | "reports";
  espSearch: string;
  hidden: boolean;
}

export const EspionageReportsPane: React.FC<EspionageReportsPaneProps> = ({
  filteredReports,
  totalReportsCount,
  originCoords,
  espTab,
  espSearch,
  hidden,
}) => {
  const reportSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "recorded" | "coords" | "distance" | "owner" | "fields" | "resources" | "buildings" | "fleetDef"
    >
  >(
    () => ({
      recorded: {
        accessor: (r) => timestampSortKey(r.ts, r.msg_date_text),
        defaultDirection: "desc",
      },
      coords: (r) => coordsSortKey(r.coords || r.target_coords),
      distance: (r) =>
        espionageDistanceSortKey(r, originCoords, String(r.coords || r.target_coords || "")),
      owner: (r) =>
        [r.planet_name, r.owner].filter(Boolean).join(" · ") ||
        (r.uninhabited_fields ? "Uninhabited Slot" : null),
      fields: {
        accessor: (r) => {
          const uf = finiteNum(r.uninhabited_fields, null);
          return uf !== null && uf > 0 ? Math.round(uf) : null;
        },
        defaultDirection: "desc",
      },
      resources: {
        accessor: (r) =>
          finiteNum(r.spice_eq ?? r.loot_spice_eq, null) ?? resourcesTotal(r.resources),
        defaultDirection: "desc",
      },
      buildings: {
        accessor: (r) => {
          const b = unitDictTotal(r.buildings);
          const res = unitDictTotal(r.researches);
          return b === null && res === null ? null : (b ?? 0) + (res ?? 0);
        },
        defaultDirection: "desc",
      },
      fleetDef: {
        accessor: (r) => (unitDictTotal(r.fleet) ?? 0) + (unitDictTotal(r.defense) ?? 0),
        defaultDirection: "desc",
      },
    }),
    [originCoords]
  );

  const reportSort = useTableSort(filteredReports, reportSortCols);

  const reportPagination = useTablePagination(
    reportSort.sortedRows,
    `${espTab}|${espSearch}|${reportSort.sortKey ?? ""}|${reportSort.sortDirection ?? ""}`,
    25
  );

  return (
    <div className="intel-pane" id="esp-pane-reports" hidden={hidden}>
      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Recorded"
                sortKey="recorded"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
              />
              <SortableTh
                label="Coords"
                sortKey="coords"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
              />
              <SortableTh
                label="Distance"
                sortKey="distance"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Owner / Planet"
                sortKey="owner"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
              />
              <SortableTh
                label="Fields"
                sortKey="fields"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Resources & SpEq"
                sortKey="resources"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
              />
              <SortableTh
                label="Buildings / Techs"
                sortKey="buildings"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
              />
              <SortableTh
                label="Fleet / Defense"
                sortKey="fleetDef"
                activeKey={reportSort.sortKey}
                direction={reportSort.sortDirection}
                onSort={reportSort.requestSort}
              />
            </tr>
          </thead>
          <tbody id="esp-reports-tbody">
            {reportPagination.pagedRows.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-cell">
                  {totalReportsCount > 0
                    ? "No historical spy reports match the active search query."
                    : "No historical spy reports stored yet."}
                </td>
              </tr>
            ) : (
              reportPagination.pagedRows.map((r, idx) => {
                const rCoords = String(r.coords || r.target_coords || "—");
                const rDist = formatEspionageDistance(r, originCoords, rCoords);
                const uf = finiteNum(r.uninhabited_fields, null);
                const spEq = Math.round(finiteNum(r.spice_eq ?? r.loot_spice_eq, 0));
                const bStr = formatUnitDictShort(r.buildings, 3);
                const rStr = formatUnitDictShort(r.researches, 3);
                const fStr = formatUnitDictShort(r.fleet, 3);
                const dStr = formatUnitDictShort(r.defense, 3);
                return (
                  <tr key={r.id ?? `${r.coords}-${idx}`}>
                    <td className="tabular">{fmtShortDateTime(r.ts, r.msg_date_text)}</td>
                    <td className="tabular">{rCoords}</td>
                    <td className="col-right tabular" title={rDist.title}>
                      {rDist.label}
                    </td>
                    <td>
                      {[r.planet_name, r.owner].filter(Boolean).join(" · ") ||
                        (r.uninhabited_fields ? "Uninhabited Slot" : "Unknown")}
                    </td>
                    <td className="col-right tabular">
                      {uf !== null && uf > 0 ? `${Math.round(uf)} fields` : "—"}
                    </td>
                    <td className="tabular">
                      {`${formatResourcesInline(r.resources)} (${fmtInt(spEq)} SpEq)`}
                    </td>
                    <td className="tabular">
                      {bStr === "None" && rStr === "None" ? "—" : `B:${bStr} · R:${rStr}`}
                    </td>
                    <td className="tabular">
                      {fStr === "None" && dStr === "None"
                        ? "Clear (0)"
                        : `F:${fStr} · D:${dStr}`}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="esp-reports"
        totalItems={reportPagination.totalItems}
        page={reportPagination.page}
        pageSize={reportPagination.pageSize}
        itemLabel="reports"
        onPageChange={reportPagination.setPage}
        onPageSizeChange={reportPagination.setPageSize}
      />
    </div>
  );
};
