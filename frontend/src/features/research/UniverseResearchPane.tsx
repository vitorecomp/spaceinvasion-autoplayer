import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import {
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { finiteNum, fmtShortDateTime } from "../../utils/formatters";

export interface UniverseResearchPaneProps {
  hidden: boolean;
  universeMaxRows: Array<Record<string, any>>;
  syncingUniverseMax: boolean;
  onRefreshUniverseResearch: () => void;
}

export const UniverseResearchPane: React.FC<UniverseResearchPaneProps> = ({
  hidden,
  universeMaxRows,
  syncingUniverseMax,
  onRefreshUniverseResearch,
}) => {
  const uniMaxSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "research" | "ownLevel" | "uniMax" | "gap" | "spiedMax" | "holder" | "catalogCap" | "updated"
    >
  >(
    () => ({
      research: (r) => String(r.name || r.slug || r.key || ""),
      ownLevel: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.own_level, 0))),
        defaultDirection: "desc",
      },
      uniMax: {
        accessor: (r) => {
          const own = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
          return Math.max(own, Math.round(finiteNum(r.universe_max_level, own)));
        },
        defaultDirection: "desc",
      },
      gap: {
        accessor: (r) => {
          const own = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
          const uni = Math.max(own, Math.round(finiteNum(r.universe_max_level, own)));
          return Math.max(0, finiteNum(r.gap_to_max, uni - own));
        },
        defaultDirection: "desc",
      },
      spiedMax: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.spied_max_level, 0))),
        defaultDirection: "desc",
      },
      holder: (r) => {
        const own = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
        const uni = Math.max(own, Math.round(finiteNum(r.universe_max_level, own)));
        if (r.holder) {
          return `${r.holder}${r.holder_coords ? ` [${r.holder_coords}]` : ""}`;
        }
        return uni > 0 && own >= uni ? "You" : null;
      },
      catalogCap: {
        accessor: (r) => {
          const cap = finiteNum(r.catalog_max_level, null);
          return cap !== null && cap > 0 ? cap : null;
        },
        defaultDirection: "desc",
      },
      updated: {
        accessor: (r) => timestampSortKey(r.updated_ts),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const uniMaxSort = useTableSort(universeMaxRows, uniMaxSortCols);

  return (
    <div className="intel-pane" id="res-pane-universe-research" hidden={hidden}>
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">UNV</span>
          <h3>Universe Max Research Levels</h3>
          <span className="module-meta tabular">{`${universeMaxRows.length} TECHNOLOGIES`}</span>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-res-refresh-universe-research"
            disabled={syncingUniverseMax}
            onClick={onRefreshUniverseResearch}
          >
            {syncingUniverseMax ? "Syncing..." : "Refresh Max Research"}
          </button>
        </div>
      </div>

      <div className="table-wrap table-scroll">
        <table id="res-universe-research-table">
          <thead>
            <tr>
              <SortableTh
                label="Research"
                sortKey="research"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
              />
              <SortableTh
                label="Your Level"
                sortKey="ownLevel"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Universe Max"
                sortKey="uniMax"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Delta / Gap"
                sortKey="gap"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Spied Max"
                sortKey="spiedMax"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Record Holder"
                sortKey="holder"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
              />
              <SortableTh
                label="Catalog Cap"
                sortKey="catalogCap"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Updated"
                sortKey="updated"
                activeKey={uniMaxSort.sortKey}
                direction={uniMaxSort.sortDirection}
                onSort={uniMaxSort.requestSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody id="res-universe-research-tbody">
            {uniMaxSort.sortedRows.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-cell">
                  Click Refresh Max Research to compute and persist universe research records.
                </td>
              </tr>
            ) : (
              uniMaxSort.sortedRows.map((r, idx) => {
                const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
                const uniMax = Math.max(
                  ownLvl,
                  Math.round(finiteNum(r.universe_max_level, ownLvl))
                );
                const spiedMax = Math.max(0, Math.round(finiteNum(r.spied_max_level, 0)));
                const catCap = finiteNum(r.catalog_max_level, null);
                const gap = Math.max(0, finiteNum(r.gap_to_max, uniMax - ownLvl));
                const isLeader = uniMax > 0 && ownLvl >= uniMax;
                const holderStr = r.holder
                  ? `${r.holder}${r.holder_coords ? ` [${r.holder_coords}]` : ""}`
                  : isLeader
                    ? "You"
                    : "—";

                return (
                  <tr key={r.key || r.slug || idx}>
                    <td>
                      <div className="action-title">
                        {r.name || r.slug || "Research"}
                        {r.key ? (
                          <span className="action-sub tabular">{` (${r.key})`}</span>
                        ) : null}
                      </div>
                    </td>
                    <td className="col-right tabular">{`L${ownLvl}`}</td>
                    <td className="col-right tabular">
                      <span className={`eta-pill${isLeader ? "" : gap > 0 ? " is-wait" : ""}`}>
                        {`L${uniMax}`}
                      </span>
                    </td>
                    <td className="col-right tabular">
                      {isLeader ? "Leader (0)" : gap > 0 ? `-${gap} Lvl` : "0"}
                    </td>
                    <td className="col-right tabular">{`L${spiedMax}`}</td>
                    <td>{holderStr}</td>
                    <td className="col-right tabular">
                      {catCap !== null && catCap > 0 ? `L${Math.round(catCap)}` : "—"}
                    </td>
                    <td className="col-right tabular">{fmtShortDateTime(r.updated_ts)}</td>
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
