import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { coordsSortKey, useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import { fmtCompact, fmtInt } from "../../utils/formatters";
import type { FleetSlotBudgetRow, OngoingFlightViewModel } from "./fleetPlannerHelpers";
import { sanitizeMainWindowText } from "./plannerHelpers";

export interface FleetPlannerTablesProps {
  ongoingFlights: OngoingFlightViewModel[];
  slotRows: FleetSlotBudgetRow[];
}

export const FleetPlannerTables: React.FC<FleetPlannerTablesProps> = ({
  ongoingFlights,
  slotRows,
}) => {
  const slotSortCols = useMemo<
    SortColumnMap<
      FleetSlotBudgetRow,
      "planet" | "b10" | "slots" | "flights" | "docked" | "planned"
    >
  >(
    () => ({
      planet: (r) => coordsSortKey(r.planet_coords),
      b10: { accessor: (r) => r.b10_level, defaultDirection: "desc" },
      slots: { accessor: (r) => r.slots_used / Math.max(1, r.slots_max), defaultDirection: "desc" },
      flights: { accessor: (r) => r.active_flights, defaultDirection: "desc" },
      docked: { accessor: (r) => r.docked_ships_total, defaultDirection: "desc" },
      planned: { accessor: (r) => r.planned_actions, defaultDirection: "desc" },
    }),
    []
  );
  const slotSort = useTableSort(slotRows, slotSortCols);

  return (
    <>
      {ongoingFlights.length > 0 ? (
        <div
          className="ag-flt-ongoing-box"
          id="flt-ongoing-flights"
          role="region"
          aria-label="Ongoing fleet flights"
        >
          <div className="ag-flt-ongoing-head">
            <div className="window-title-group">
              <h3>Ongoing Flights</h3>
              <span className="sub">Live outbound expectations &amp; transport deliveries</span>
            </div>
            <span className="ag-toolbar-meta tabular">
              {`${ongoingFlights.length} ${ongoingFlights.length === 1 ? "flight" : "flights"}`}
            </span>
          </div>
          <div className="ag-flt-ongoing-list">
            {ongoingFlights.map((flight) => (
              <div key={flight.key} className="ag-flt-ongoing-row">
                <div className="ag-flt-ongoing-main">
                  <span
                    className={`ag-flt-dir-pill tabular ${flight.returning ? "is-returning" : "is-outbound"}`}
                  >
                    {flight.returning ? "RETURNING" : "OUTBOUND"}
                  </span>
                  <strong className="ag-flt-ongoing-title">
                    {sanitizeMainWindowText(flight.title)}
                  </strong>
                  <span className="ag-cell-sub tabular">
                    {sanitizeMainWindowText(flight.originLabel)}
                  </span>
                  {flight.detail ? (
                    <span className="ag-cell-sub">{sanitizeMainWindowText(flight.detail)}</span>
                  ) : null}
                </div>
                <div className="ag-flt-ongoing-meta tabular">
                  {flight.cargoInfo ? (
                    <span
                      className={`ag-flt-cargo-line is-${flight.cargoInfo.variant}`}
                      title={
                        flight.cargoInfo.inline
                          ? `${flight.cargoInfo.tag}: ${flight.cargoInfo.inline}${flight.cargoInfo.total > 0 ? ` (${fmtInt(flight.cargoInfo.total)} total)` : ""}`
                          : flight.cargoInfo.tag
                      }
                    >
                      <span
                        className={`ag-flt-cargo-badge is-${flight.cargoInfo.variant}`}
                        data-cargo-kind={flight.cargoInfo.variant}
                      >
                        {flight.cargoInfo.tag}
                      </span>
                      {flight.cargoInfo.inline ? (
                        <span className="ag-flt-cargo-vals">
                          {flight.cargoInfo.inline}
                          {flight.cargoInfo.multiRes
                            ? ` (${fmtCompact(flight.cargoInfo.total)})`
                            : ""}
                        </span>
                      ) : null}
                    </span>
                  ) : null}
                  <span className="eta-pill tabular">{flight.etaText}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="window-bar ag-subtable-bar">
        <div className="window-title-group">
          <h3>Per-Planet Fleet Slot &amp; Docked Budget</h3>
          <span className="sub">1 + Fleet base (b10) slots per planet · Live &amp; planned load</span>
        </div>
        <span className="ag-toolbar-meta tabular">{slotRows.length} planets</span>
      </div>

      <div className="table-wrap table-scroll">
        <table id="flt-slots-table">
          <thead>
            <tr>
              <SortableTh
                label="Planet"
                sortKey="planet"
                activeKey={slotSort.sortKey}
                direction={slotSort.sortDirection}
                onSort={slotSort.requestSort}
              />
              <SortableTh
                label="Fleet Base"
                sortKey="b10"
                activeKey={slotSort.sortKey}
                direction={slotSort.sortDirection}
                onSort={slotSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Fleet Slots"
                sortKey="slots"
                activeKey={slotSort.sortKey}
                direction={slotSort.sortDirection}
                onSort={slotSort.requestSort}
                align="right"
              />
              <SortableTh
                label="In Flight"
                sortKey="flights"
                activeKey={slotSort.sortKey}
                direction={slotSort.sortDirection}
                onSort={slotSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Docked Ships"
                sortKey="docked"
                activeKey={slotSort.sortKey}
                direction={slotSort.sortDirection}
                onSort={slotSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Planned"
                sortKey="planned"
                activeKey={slotSort.sortKey}
                direction={slotSort.sortDirection}
                onSort={slotSort.requestSort}
                align="right"
              />
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {slotSort.sortedRows.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-note">
                  No planet fleet budget rows loaded yet.
                </td>
              </tr>
            ) : (
              slotSort.sortedRows.map((row) => {
                const isFull = row.slots_used >= row.slots_max;
                return (
                  <tr key={`flt-slot-${row.planet_id}-${row.planet_coords}`}>
                    <td>
                      <strong>{sanitizeMainWindowText(row.planet_name)}</strong>
                      <div className="ag-cell-sub">{sanitizeMainWindowText(row.planet_coords)}</div>
                    </td>
                    <td className="num tabular">b10 L{row.b10_level}</td>
                    <td className="num tabular">
                      <strong>{row.slots_used}</strong> / {row.slots_max}
                    </td>
                    <td className="num tabular">{row.active_flights}</td>
                    <td className="num tabular">
                      <div>{fmtInt(row.docked_ships_total)}</div>
                      <div className="ag-cell-sub">
                        {sanitizeMainWindowText(row.docked_summary)}
                      </div>
                    </td>
                    <td className="num tabular">{row.planned_actions}</td>
                    <td>
                      <span className={`ag-status-pill ${isFull ? "is-sim" : "is-live"}`}>
                        {row.status}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </>
  );
};
