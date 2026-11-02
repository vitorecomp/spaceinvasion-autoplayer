import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import { finiteNum, fmtInt } from "../../utils/formatters";

export interface CombatShipyardPaneProps {
  subTab: string;
  ships: Array<Record<string, any>>;
  defenses: Array<Record<string, any>>;
  offersById: ReadonlyMap<number, Record<string, any>>;
  shipQtys: Record<number, string>;
  onChangeShipQty: (unitId: number, value: string) => void;
  onBuildShip: (ship: Record<string, any>) => void;
  onQueueShip: (ship: Record<string, any>) => void;
}

export const CombatShipyardPane: React.FC<CombatShipyardPaneProps> = ({
  subTab,
  ships,
  defenses,
  offersById,
  shipQtys,
  onChangeShipQty,
  onBuildShip,
  onQueueShip,
}) => {
  const shipSortCols = useMemo<
    SortColumnMap<Record<string, any>, "name" | "owned" | "atkShd" | "cargo">
  >(
    () => ({
      name: (s) => String(s.name || s.key || ""),
      owned: { accessor: (s) => finiteNum(s.owned, 0), defaultDirection: "desc" },
      atkShd: {
        accessor: (s) => finiteNum(s.attack, 0) + finiteNum(s.shield, 0),
        defaultDirection: "desc",
      },
      cargo: { accessor: (s) => finiteNum(s.cargo, 0), defaultDirection: "desc" },
    }),
    []
  );

  const defSortCols = useMemo<
    SortColumnMap<Record<string, any>, "name" | "owned" | "atkShd" | "hull">
  >(
    () => ({
      name: (d) => String(d.name || d.key || ""),
      owned: { accessor: (d) => finiteNum(d.owned, 0), defaultDirection: "desc" },
      atkShd: {
        accessor: (d) => finiteNum(d.attack, 0) + finiteNum(d.shield, 0),
        defaultDirection: "desc",
      },
      hull: { accessor: (d) => finiteNum(d.hull, 0), defaultDirection: "desc" },
    }),
    []
  );

  const shipSort = useTableSort(ships, shipSortCols);
  const defSort = useTableSort(defenses, defSortCols);

  return (
    <>
      <div className="combat-subpane" id="cmb-pane-ships" hidden={subTab !== "ships"}>
        <div className="table-wrap table-scroll">
          <table>
            <thead>
              <tr>
                <SortableTh
                  label="Ship Class"
                  sortKey="name"
                  activeKey={shipSort.sortKey}
                  direction={shipSort.sortDirection}
                  onSort={shipSort.requestSort}
                />
                <SortableTh
                  label="Owned"
                  sortKey="owned"
                  activeKey={shipSort.sortKey}
                  direction={shipSort.sortDirection}
                  onSort={shipSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="ATK / SHD"
                  sortKey="atkShd"
                  activeKey={shipSort.sortKey}
                  direction={shipSort.sortDirection}
                  onSort={shipSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Cargo"
                  sortKey="cargo"
                  activeKey={shipSort.sortKey}
                  direction={shipSort.sortDirection}
                  onSort={shipSort.requestSort}
                  align="right"
                />
                <th className="col-right">Order</th>
              </tr>
            </thead>
            <tbody id="cmb-ships-tbody" data-testid="combat-shipyard-tbody">
              {shipSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty-cell">
                    No shipyard telemetry yet.
                  </td>
                </tr>
              ) : (
                shipSort.sortedRows.map((s) => {
                  const uid = Number(s.unit_id);
                  const offer = offersById.get(uid);
                  return (
                    <tr key={s.key || uid}>
                      <td>
                        {`${s.name} `}
                        <span className="action-sub tabular">{`(${s.key})`}</span>
                      </td>
                      <td className="col-right tabular">{fmtInt(s.owned)}</td>
                      <td className="col-right tabular">
                        {`${fmtInt(s.attack)} / ${fmtInt(s.shield)}`}
                      </td>
                      <td className="col-right tabular">{fmtInt(s.cargo)}</td>
                      <td className="col-right">
                        <div className="row-actions-wrap">
                          <input
                            type="number"
                            min={1}
                            value={shipQtys[uid] ?? "1"}
                            onChange={(e) => onChangeShipQty(uid, e.target.value)}
                            className="qty-inline"
                            aria-label={`Order quantity for ${s.name}`}
                          />
                          {offer && offer.can_build && (
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              onClick={() => onBuildShip(s)}
                            >
                              Build
                            </button>
                          )}
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            title={`Add ${s.name} order to P1 priority queue`}
                            aria-label={`Add ${s.name} order to P1 priority queue`}
                            onClick={() => onQueueShip(s)}
                          >
                            + Queue
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

      <div className="combat-subpane" id="cmb-pane-defense" hidden={subTab !== "defense"}>
        <div className="table-wrap table-scroll">
          <table>
            <thead>
              <tr>
                <SortableTh
                  label="Defense Structure"
                  sortKey="name"
                  activeKey={defSort.sortKey}
                  direction={defSort.sortDirection}
                  onSort={defSort.requestSort}
                />
                <SortableTh
                  label="Owned"
                  sortKey="owned"
                  activeKey={defSort.sortKey}
                  direction={defSort.sortDirection}
                  onSort={defSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="ATK / SHD"
                  sortKey="atkShd"
                  activeKey={defSort.sortKey}
                  direction={defSort.sortDirection}
                  onSort={defSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Hull"
                  sortKey="hull"
                  activeKey={defSort.sortKey}
                  direction={defSort.sortDirection}
                  onSort={defSort.requestSort}
                  align="right"
                />
              </tr>
            </thead>
            <tbody id="cmb-def-tbody">
              {defSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={4} className="empty-cell">
                    No defense data.
                  </td>
                </tr>
              ) : (
                defSort.sortedRows.map((d) => (
                  <tr key={d.key || d.name}>
                    <td>{`${d.name} (${d.key})`}</td>
                    <td className="col-right tabular">{fmtInt(d.owned)}</td>
                    <td className="col-right tabular">
                      {`${fmtInt(d.attack)} / ${fmtInt(d.shield)}`}
                    </td>
                    <td className="col-right tabular">{fmtInt(d.hull)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
};
