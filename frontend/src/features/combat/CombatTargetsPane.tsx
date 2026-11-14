import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { coordsSortKey, useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import { finiteNum, fmtInt } from "../../utils/formatters";
import { dispatchPopulateSimEvent } from "./simEvents";

export interface CombatTargetsPaneProps {
  hidden: boolean;
  raidTargets: Array<Record<string, any>>;
  onAttackTarget: (coords: string) => void;
}

export const CombatTargetsPane: React.FC<CombatTargetsPaneProps> = ({
  hidden,
  raidTargets,
  onAttackTarget,
}) => {
  const raidTargetSortCols = useMemo<
    SortColumnMap<Record<string, any>, "coords" | "owner" | "type" | "loot">
  >(
    () => ({
      coords: (t) => coordsSortKey(t.coords || t.target_coords),
      owner: (t) => [t.planet_name, t.owner].filter(Boolean).join(" · ") || null,
      type: (t) => {
        const color = String(t.status_color || "yellow");
        return t.is_spacia ? "SPACIA" : color === "yellow" ? "INACTIVE" : color.toUpperCase();
      },
      loot: {
        accessor: (t) => {
          const r = t.resources || t;
          const total =
            finiteNum(r.pig_iron, 0) +
            finiteNum(r.metal, 0) +
            finiteNum(r.kryptonite, 0) +
            finiteNum(r.spice, 0);
          return total > 0 ? total : null;
        },
        defaultDirection: "desc",
      },
    }),
    []
  );

  const raidTargetSort = useTableSort(raidTargets, raidTargetSortCols);

  return (
    <div className="combat-subpane" id="cmb-pane-targets" hidden={hidden}>
      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Coords"
                sortKey="coords"
                activeKey={raidTargetSort.sortKey}
                direction={raidTargetSort.sortDirection}
                onSort={raidTargetSort.requestSort}
              />
              <SortableTh
                label="Planet / Owner"
                sortKey="owner"
                activeKey={raidTargetSort.sortKey}
                direction={raidTargetSort.sortDirection}
                onSort={raidTargetSort.requestSort}
              />
              <SortableTh
                label="Type"
                sortKey="type"
                activeKey={raidTargetSort.sortKey}
                direction={raidTargetSort.sortDirection}
                onSort={raidTargetSort.requestSort}
              />
              <SortableTh
                label="Loot (SpEq)"
                sortKey="loot"
                activeKey={raidTargetSort.sortKey}
                direction={raidTargetSort.sortDirection}
                onSort={raidTargetSort.requestSort}
                align="right"
              />
              <th className="col-right">Ops</th>
            </tr>
          </thead>
          <tbody id="cmb-targets-tbody">
            {raidTargetSort.sortedRows.length === 0 ? (
              <tr>
                <td colSpan={5} className="empty-cell">
                  Click &quot;Scan Galaxy&quot; to populate yellow inactive &amp; Spacia targets.
                </td>
              </tr>
            ) : (
              raidTargetSort.sortedRows.map((t) => {
                const coords = String(t.coords || t.target_coords || "—");
                const resMap = t.resources || t;
                const color = String(t.status_color || "yellow");
                const totalRes =
                  finiteNum(resMap.pig_iron, 0) +
                  finiteNum(resMap.metal, 0) +
                  finiteNum(resMap.kryptonite, 0) +
                  finiteNum(resMap.spice, 0);
                return (
                  <tr key={coords}>
                    <td className="tabular">{coords}</td>
                    <td>
                      {[t.planet_name, t.owner].filter(Boolean).join(" · ") || "Unknown"}
                    </td>
                    <td>
                      <span
                        className={`status-word${color === "yellow" || t.is_spacia ? "" : " is-warn"}`}
                      >
                        {t.is_spacia
                          ? "SPACIA"
                          : color === "yellow"
                            ? "INACTIVE"
                            : color.toUpperCase()}
                      </span>
                    </td>
                    <td className="col-right tabular">
                      {Number.isFinite(totalRes)
                        ? totalRes > 0
                          ? fmtInt(totalRes)
                          : "Unspied"
                        : "—"}
                    </td>
                    <td className="col-right">
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() =>
                            dispatchPopulateSimEvent({
                              defense: t.defense || {},
                              fleet: t.fleet || {},
                              totalLoot: totalRes,
                            })
                          }
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
    </div>
  );
};
