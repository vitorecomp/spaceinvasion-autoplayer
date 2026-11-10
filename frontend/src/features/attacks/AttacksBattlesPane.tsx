import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  finiteNum,
  fmtInt,
  fmtShortDateTime,
  formatResourcesInline,
} from "../../utils/formatters";

export type BattleOutcomeFilter = "all" | "win" | "loss" | "draw" | "debris";

export const BATTLE_OUTCOME_FILTERS: ReadonlyArray<{
  key: BattleOutcomeFilter;
  label: string;
}> = [
  { key: "all", label: "All Outcomes" },
  { key: "win", label: "Wins" },
  { key: "loss", label: "Losses" },
  { key: "draw", label: "Draws" },
  { key: "debris", label: "Debris Created" },
];

export interface AttacksBattlesPaneProps {
  filteredBattles: Array<Record<string, any>>;
  totalBattlesCount: number;
  outcomeFilter: BattleOutcomeFilter;
  onOutcomeFilterChange: (next: BattleOutcomeFilter) => void;
  atkTab: "battles" | "blocks";
  atkSearch: string;
  hidden: boolean;
}

export const AttacksBattlesPane: React.FC<AttacksBattlesPaneProps> = ({
  filteredBattles,
  totalBattlesCount,
  outcomeFilter,
  onOutcomeFilterChange,
  atkTab,
  atkSearch,
  hidden,
}) => {
  const battleSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "time" | "target" | "defender" | "outcome" | "rounds" | "loot" | "losses" | "debris"
    >
  >(
    () => ({
      time: {
        accessor: (b) => timestampSortKey(b.ts, b.msg_date_text),
        defaultDirection: "desc",
      },
      target: (b) => coordsSortKey(b.coords),
      defender: (b) => String(b.defender_name || b.subject || "").trim() || null,
      outcome: (b) => String(b.outcome || "draw").toLowerCase(),
      rounds: {
        accessor: (b) => finiteNum(b.rounds, null),
        defaultDirection: "desc",
      },
      loot: {
        accessor: (b) => finiteNum(b.loot?.spice_eq, 0),
        defaultDirection: "desc",
      },
      losses: {
        accessor: (b) =>
          finiteNum(b.attacker_losses?.spice_eq, 0) + finiteNum(b.defender_losses?.spice_eq, 0),
        defaultDirection: "desc",
      },
      debris: {
        accessor: (b) =>
          finiteNum(b.debris?.pig_iron, 0) + finiteNum(b.debris?.metal, 0),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const battleSort = useTableSort(filteredBattles, battleSortCols);

  const battlePagination = useTablePagination(
    battleSort.sortedRows,
    `${atkTab}|${outcomeFilter}|${atkSearch}|${battleSort.sortKey ?? ""}|${battleSort.sortDirection ?? ""}`,
    25
  );

  return (
    <div className="intel-pane" id="atk-pane-battles" hidden={hidden}>
      <div className="intel-filter-bar">
        <div className="filter-chips" role="group" aria-label="Battle outcome filters">
          {BATTLE_OUTCOME_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`chip-btn${outcomeFilter === f.key ? " active" : ""}`}
              data-atk-outcome={f.key}
              onClick={() => onOutcomeFilterChange(f.key)}
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
                label="Time"
                sortKey="time"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
              />
              <SortableTh
                label="Target"
                sortKey="target"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
              />
              <SortableTh
                label="Defender"
                sortKey="defender"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
              />
              <SortableTh
                label="Outcome"
                sortKey="outcome"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
              />
              <SortableTh
                label="Rounds"
                sortKey="rounds"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Plundered Loot"
                sortKey="loot"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
              />
              <SortableTh
                label="Losses (Atk / Def)"
                sortKey="losses"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
              />
              <SortableTh
                label="Debris Created"
                sortKey="debris"
                activeKey={battleSort.sortKey}
                direction={battleSort.sortDirection}
                onSort={battleSort.requestSort}
              />
            </tr>
          </thead>
          <tbody id="atk-battles-tbody">
            {battlePagination.pagedRows.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-cell">
                  {totalBattlesCount > 0
                    ? "No battle reports match the active filter or search query."
                    : "Click Sync All Folders to archive in-game battle reports."}
                </td>
              </tr>
            ) : (
              battlePagination.pagedRows.map((b, idx) => {
                const outVal = String(b.outcome || "draw").toLowerCase();
                const outCls =
                  outVal === "win" ? "is-win" : outVal === "loss" ? "is-loss" : "is-draw";
                const lootSp = Math.round(finiteNum(b.loot?.spice_eq, 0));
                const atkLoss = Math.round(finiteNum(b.attacker_losses?.spice_eq, 0));
                const defLoss = Math.round(finiteNum(b.defender_losses?.spice_eq, 0));
                const dPig = Math.round(finiteNum(b.debris?.pig_iron, 0));
                const dMet = Math.round(finiteNum(b.debris?.metal, 0));

                return (
                  <tr key={b.id ?? `${b.coords}-${idx}`}>
                    <td className="tabular">{fmtShortDateTime(b.ts, b.msg_date_text)}</td>
                    <td className="tabular">{String(b.coords || "—")}</td>
                    <td>{String(b.defender_name || b.subject || "Unknown")}</td>
                    <td>
                      <span className={`outcome-badge ${outCls}`}>{outVal.toUpperCase()}</span>
                    </td>
                    <td className="col-right tabular">
                      {String(Math.round(finiteNum(b.rounds, 0)))}
                    </td>
                    <td className="tabular">
                      {lootSp > 0
                        ? `${formatResourcesInline(b.loot)} (+${fmtInt(lootSp)} SpEq)`
                        : "0"}
                    </td>
                    <td className="tabular">
                      {`Atk ${fmtInt(atkLoss)} / Def ${fmtInt(defLoss)} SpEq`}
                    </td>
                    <td className="tabular">
                      {dPig > 0 || dMet > 0
                        ? `${fmtInt(dPig)} Pig · ${fmtInt(dMet)} Met`
                        : "None"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="atk-battles"
        totalItems={battlePagination.totalItems}
        page={battlePagination.page}
        pageSize={battlePagination.pageSize}
        itemLabel="reports"
        onPageChange={battlePagination.setPage}
        onPageSizeChange={battlePagination.setPageSize}
      />
    </div>
  );
};
