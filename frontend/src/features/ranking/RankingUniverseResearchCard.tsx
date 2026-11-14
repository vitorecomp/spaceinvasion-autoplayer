import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type { UniverseResearchMaxRow } from "../../types/api";
import { finiteNum, fmtInt } from "../../utils/formatters";

export interface RankingUniverseResearchCardProps {
  uniRows: UniverseResearchMaxRow[];
  syncingUniRes: boolean;
  onSyncUniverseResearch: () => void;
}

export const RankingUniverseResearchCard: React.FC<RankingUniverseResearchCardProps> = ({
  uniRows,
  syncingUniRes,
  onSyncUniverseResearch,
}) => {
  const uniSortCols = useMemo<
    SortColumnMap<
      UniverseResearchMaxRow,
      "research" | "ownLevel" | "uniMax" | "gap" | "holder" | "catalogCap"
    >
  >(
    () => ({
      research: (r) => String(r.name || r.key || r.research_key || ""),
      ownLevel: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.own_level, 0))),
        defaultDirection: "desc",
      },
      uniMax: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.universe_max_level, 0))),
        defaultDirection: "desc",
      },
      gap: {
        accessor: (r) => {
          const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
          const uniMax = Math.max(0, Math.round(finiteNum(r.universe_max_level, 0)));
          return Math.max(0, Math.round(finiteNum(r.gap_to_max, Math.max(0, uniMax - ownLvl))));
        },
        defaultDirection: "desc",
      },
      holder: (r) => {
        const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
        const uniMax = Math.max(0, Math.round(finiteNum(r.universe_max_level, 0)));
        if (r.holder) {
          return r.holder_coords ? `${r.holder} [${r.holder_coords}]` : String(r.holder);
        }
        return uniMax > 0 && ownLvl >= uniMax ? "You" : null;
      },
      catalogCap: {
        accessor: (r) => {
          const cap = finiteNum(r.catalog_max_level, null);
          return cap !== null && cap > 0 ? cap : null;
        },
        defaultDirection: "desc",
      },
    }),
    []
  );

  const uniSort = useTableSort(uniRows, uniSortCols);

  return (
    <div className="ranking-chart-card">
      <div className="card-subhead">
        <h3>Universe Max Research Levels</h3>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id="btn-sync-universe-research"
          onClick={onSyncUniverseResearch}
          disabled={syncingUniRes}
        >
          {syncingUniRes ? "Syncing..." : "Sync Records"}
        </button>
      </div>
      <div className="table-wrap table-scroll">
        <table id="universe-research-max-table">
          <thead>
            <tr>
              <SortableTh
                label="Research"
                sortKey="research"
                activeKey={uniSort.sortKey}
                direction={uniSort.sortDirection}
                onSort={uniSort.requestSort}
              />
              <SortableTh
                label="Your Level"
                sortKey="ownLevel"
                activeKey={uniSort.sortKey}
                direction={uniSort.sortDirection}
                onSort={uniSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Universe Max"
                sortKey="uniMax"
                activeKey={uniSort.sortKey}
                direction={uniSort.sortDirection}
                onSort={uniSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Delta / Gap"
                sortKey="gap"
                activeKey={uniSort.sortKey}
                direction={uniSort.sortDirection}
                onSort={uniSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Record Holder"
                sortKey="holder"
                activeKey={uniSort.sortKey}
                direction={uniSort.sortDirection}
                onSort={uniSort.requestSort}
              />
              <SortableTh
                label="Catalog Cap"
                sortKey="catalogCap"
                activeKey={uniSort.sortKey}
                direction={uniSort.sortDirection}
                onSort={uniSort.requestSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody id="universe-research-max-tbody">
            {uniSort.sortedRows.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">
                  Click Sync Records to load universe max research levels.
                </td>
              </tr>
            ) : (
              uniSort.sortedRows.map((r) => {
                const rKey = String(r.key || r.research_key || `r${r.research_id}`);
                const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
                const uniMax = Math.max(0, Math.round(finiteNum(r.universe_max_level, 0)));
                const gap = Math.max(
                  0,
                  Math.round(finiteNum(r.gap_to_max, Math.max(0, uniMax - ownLvl)))
                );
                const catCap = finiteNum(r.catalog_max_level, null);
                const holderTxt = r.holder
                  ? r.holder_coords
                    ? `${r.holder} [${r.holder_coords}]`
                    : String(r.holder)
                  : uniMax > 0 && ownLvl >= uniMax
                    ? "You"
                    : "—";

                return (
                  <tr key={rKey}>
                    <td>
                      {`${r.name} `}
                      <span className="action-sub tabular">{`(${rKey})`}</span>
                    </td>
                    <td className="col-right tabular">{`L${fmtInt(ownLvl)}`}</td>
                    <td className="col-right tabular">{`L${fmtInt(uniMax)}`}</td>
                    <td className="col-right tabular">
                      {gap <= 0 && uniMax > 0 ? (
                        <span className="status-word">LEADER (0)</span>
                      ) : gap > 0 ? (
                        <span className="status-word is-warn">{`-${fmtInt(gap)} lvls`}</span>
                      ) : (
                        "0"
                      )}
                    </td>
                    <td>{holderTxt}</td>
                    <td className="col-right tabular">
                      {catCap !== null && catCap > 0 ? `L${fmtInt(catCap)}` : "Uncapped"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
