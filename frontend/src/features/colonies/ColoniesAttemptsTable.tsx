import React, { useEffect, useMemo, useState } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import type { ColonyAttemptItem } from "../../types/api";
import { finiteNum, fmtInt, fmtShortDateTime } from "../../utils/formatters";
import { attemptOutcomeFor } from "./coloniesHelpers";

export interface ColoniesAttemptsTableProps {
  attempts: ColonyAttemptItem[];
  triedCount: number;
  minFieldsTarget: number;
}

export const ColoniesAttemptsTable: React.FC<ColoniesAttemptsTableProps> = ({
  attempts,
  triedCount,
  minFieldsTarget,
}) => {
  const [attemptPage, setAttemptPage] = useState<number>(1);
  const [attemptPageSize, setAttemptPageSize] = useState<number>(25);

  const attemptSortCols = useMemo<
    SortColumnMap<
      ColonyAttemptItem,
      "coords" | "outcome" | "rolledFields" | "targetExpected" | "temp" | "origin" | "time" | "details"
    >
  >(
    () => ({
      coords: (a) => coordsSortKey(a.target_coords),
      outcome: (a) => attemptOutcomeFor(a.event_type, minFieldsTarget).label,
      rolledFields: {
        accessor: (a) => {
          const r = finiteNum(a.fields_rolled, null);
          return r !== null && r > 0 ? r : null;
        },
        defaultDirection: "desc",
      },
      targetExpected: {
        accessor: (a) =>
          finiteNum(a.expected_max_fields, null) ??
          finiteNum(a.min_fields_target, minFieldsTarget),
        defaultDirection: "desc",
      },
      temp: {
        accessor: (a) => finiteNum(a.temperature, null),
        defaultDirection: "desc",
      },
      origin: (a) => coordsSortKey(a.origin_coords),
      time: {
        accessor: (a) => timestampSortKey(a.ts),
        defaultDirection: "desc",
      },
      details: (a) =>
        String(a.status_message || "").trim() || (a.grade ? `Grade ${a.grade}` : null),
    }),
    [minFieldsTarget]
  );

  const attemptSort = useTableSort(attempts, attemptSortCols);

  const attemptTotalPages = Math.max(
    1,
    Math.ceil(attemptSort.sortedRows.length / attemptPageSize)
  );
  const effectiveAttemptPage = Math.max(1, Math.min(attemptTotalPages, attemptPage));
  const pagedAttempts = useMemo(() => {
    const start = (effectiveAttemptPage - 1) * attemptPageSize;
    return attemptSort.sortedRows.slice(start, start + attemptPageSize);
  }, [attemptSort.sortedRows, effectiveAttemptPage, attemptPageSize]);

  useEffect(() => {
    setAttemptPage(1);
  }, [attemptSort.sortKey, attemptSort.sortDirection, attemptPageSize]);

  useEffect(() => {
    if (attemptPage > attemptTotalPages) {
      setAttemptPage(attemptTotalPages);
    }
  }, [attemptPage, attemptTotalPages]);

  return (
    <article className="hud-window col-attempts-window" aria-labelledby="col-attempts-title">
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">TRY</span>
          <h2 id="col-attempts-title">Tried Planets &amp; Colonization Attempts</h2>
          <span className="module-meta tabular" id="col-attempts-meta">
            {`${fmtInt(attempts.length)} EVENTS · ${fmtInt(triedCount)} COORDS`}
          </span>
        </div>
      </div>
      <div className="table-wrap table-scroll">
        <table id="colonies-attempts-table">
          <thead>
            <tr>
              <SortableTh
                label="Coords"
                sortKey="coords"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
              />
              <SortableTh
                label="Outcome"
                sortKey="outcome"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
              />
              <SortableTh
                label="Rolled Fields"
                sortKey="rolledFields"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Target / Expected"
                sortKey="targetExpected"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Temp"
                sortKey="temp"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Origin"
                sortKey="origin"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
              />
              <SortableTh
                label="Time"
                sortKey="time"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Details / Reason"
                sortKey="details"
                activeKey={attemptSort.sortKey}
                direction={attemptSort.sortDirection}
                onSort={attemptSort.requestSort}
              />
            </tr>
          </thead>
          <tbody id="colonies-attempts-tbody">
            {pagedAttempts.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-cell">
                  No colonization attempts recorded yet. Every Colony ship dispatch, kept 300+ roll, and deleted undersized roll is logged here.
                </td>
              </tr>
            ) : (
              pagedAttempts.map((a, idx) => {
                const aCoords = String(a.target_coords || "—");
                const aMin = Math.round(finiteNum(a.min_fields_target, minFieldsTarget));
                const outcome = attemptOutcomeFor(a.event_type, aMin);
                const rolled = finiteNum(a.fields_rolled, null);
                const expMin = finiteNum(a.expected_min_fields, null);
                const expMax = finiteNum(a.expected_max_fields, null);
                const expTxt =
                  expMax !== null && expMax > 0
                    ? expMin !== null && expMin > 0 && expMin !== expMax
                      ? `${Math.round(expMin)}–${Math.round(expMax)}`
                      : `${Math.round(expMax)}`
                    : "";
                const temp = finiteNum(a.temperature, null);
                const reasonTxt =
                  String(a.status_message || "").trim() ||
                  (a.grade ? `Grade ${a.grade}` : "") ||
                  "—";
                return (
                  <tr
                    key={`${a.id ?? idx}-${aCoords}`}
                    data-attempt-event={String(a.event_type || "")}
                  >
                    <td className="tabular">{aCoords}</td>
                    <td>
                      <span className={`outcome-badge ${outcome.cls}`}>{outcome.label}</span>
                    </td>
                    <td className="col-right tabular">
                      {rolled !== null && rolled > 0 ? (
                        <span className={`status-word${rolled >= aMin ? "" : " is-warn"}`}>
                          {`${Math.round(rolled)} f`}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="col-right tabular">
                      {expTxt ? `≥${aMin} · exp ${expTxt}` : `≥${aMin}`}
                    </td>
                    <td className="col-right tabular">
                      {temp !== null ? `${Math.round(temp)} °C` : "—"}
                    </td>
                    <td className="tabular">{a.origin_coords ? String(a.origin_coords) : "—"}</td>
                    <td className="col-right tabular">{fmtShortDateTime(a.ts)}</td>
                    <td>{reasonTxt}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="col-att"
        totalItems={attemptSort.sortedRows.length}
        page={effectiveAttemptPage}
        pageSize={attemptPageSize}
        pageSizeOptions={[10, 25, 50, 100]}
        itemLabel="events"
        onPageChange={setAttemptPage}
        onPageSizeChange={setAttemptPageSize}
      />
    </article>
  );
};
