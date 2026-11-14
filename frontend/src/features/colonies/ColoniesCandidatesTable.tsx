import React, { useEffect, useMemo, useState } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  gradeSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import type {
  ColonyAttemptCoordStats,
  DashboardPayload,
} from "../../types/api";
import { finiteNum, fmtInt, gradeClassFor } from "../../utils/formatters";
import { attemptShortTag } from "./coloniesHelpers";

export interface ColoniesCandidatesTableProps {
  colonyData: Record<string, any> | null;
  galaxySweep: DashboardPayload["galaxy_sweep"];
  allCands: Array<Record<string, any>>;
  colFilter: string;
  onSelectFilter: (filter: string) => void;
  minFieldsTarget: number;
  attemptsByCoords: Record<string, ColonyAttemptCoordStats>;
  onSyncInbox: () => void;
  onProbeCandidate: (coords: string) => void;
  onColonizeCandidate: (coords: string) => void;
  onDiscardCandidate: (coords: string) => void;
}

export const ColoniesCandidatesTable: React.FC<ColoniesCandidatesTableProps> = ({
  colonyData,
  galaxySweep,
  allCands,
  colFilter,
  onSelectFilter,
  minFieldsTarget,
  attemptsByCoords,
  onSyncInbox,
  onProbeCandidate,
  onColonizeCandidate,
  onDiscardCandidate,
}) => {
  const [candSearch, setCandSearch] = useState<string>("");
  const [candPage, setCandPage] = useState<number>(1);
  const [candPageSize, setCandPageSize] = useState<number>(25);

  const filteredCands = useMemo(() => {
    const needle = candSearch.trim().toLowerCase();
    return allCands.filter((c) => {
      if (colFilter === "probed" && !c.probed) return false;
      if (colFilter === "qualified" && !c.meets_target) return false;
      if (colFilter === "unprobed" && Boolean(c.probed)) return false;
      if (!needle) return true;
      const hay = [
        c.coords,
        c.grade,
        c.orbital_band,
        c.fields_display,
        c.reason,
        c.position != null ? `pos ${c.position}` : "",
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [allCands, colFilter, candSearch]);

  const candSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "grade" | "score" | "coords" | "zone" | "fields" | "distance" | "assessment"
    >
  >(
    () => ({
      grade: (c) => gradeSortKey(c.grade),
      score: {
        accessor: (c) => finiteNum(c.score, null),
        defaultDirection: "desc",
      },
      coords: (c) => coordsSortKey(c.coords),
      zone: (c) => finiteNum(c.position, null) ?? (String(c.orbital_band || "").trim() || null),
      fields: {
        accessor: (c) => {
          const uf = finiteNum(c.uninhabited_fields, null);
          if (uf !== null && uf > 0) return uf;
          const cMax = finiteNum(c.colonize_max_fields, null);
          if (cMax !== null && cMax > 0) return cMax;
          return null;
        },
        defaultDirection: "desc",
      },
      distance: (c) => finiteNum(c.system_distance, null),
      assessment: (c) => String(c.reason || "").trim() || null,
    }),
    []
  );

  const candSort = useTableSort(filteredCands, candSortCols);

  const candTotalPages = Math.max(1, Math.ceil(candSort.sortedRows.length / candPageSize));
  const effectiveCandPage = Math.max(1, Math.min(candTotalPages, candPage));
  const pagedCands = useMemo(() => {
    const start = (effectiveCandPage - 1) * candPageSize;
    return candSort.sortedRows.slice(start, start + candPageSize);
  }, [candSort.sortedRows, effectiveCandPage, candPageSize]);

  useEffect(() => {
    setCandPage(1);
  }, [colFilter, candSearch, candSort.sortKey, candSort.sortDirection, candPageSize]);

  useEffect(() => {
    if (candPage > candTotalPages) {
      setCandPage(candTotalPages);
    }
  }, [candPage, candTotalPages]);

  return (
    <article className="hud-window page-fill col-candidates-window">
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">COL</span>
          <h2>Possible Colonies &amp; Spied Field Grading</h2>
          <span className="module-meta tabular" id="col-meta-count">
            {colonyData ? `${filteredCands.length} / ${allCands.length} SLOTS` : "0 CANDIDATES"}
          </span>
          {galaxySweep ? (
            <span className="gal-cov-inline tabular" id="col-galaxy-coverage">
              {`GAL ${Math.max(
                0,
                Math.min(100, finiteNum(galaxySweep.coverage_pct, 0))
              ).toFixed(1)}% (${fmtInt(galaxySweep.explored_systems ?? 0)}/${fmtInt(
                galaxySweep.total_systems ?? 400
              )}) · UNI ${Math.max(
                0,
                Math.min(
                  100,
                  finiteNum(
                    galaxySweep.universe_coverage_pct,
                    (finiteNum(
                      galaxySweep.universe_explored_systems ?? galaxySweep.explored_systems,
                      0
                    ) /
                      Math.max(1, finiteNum(galaxySweep.universe_total_systems, 5600))) *
                      100
                  )
                )
              ).toFixed(1)}%`}
            </span>
          ) : null}
        </div>
        <div className="filter-toolbar">
          <div className="filter-chips" role="group" aria-label="Colony candidate filters">
            <button
              type="button"
              className={`chip-btn${colFilter === "all" ? " active" : ""}`}
              data-col-filter="all"
              onClick={() => onSelectFilter("all")}
            >
              All Slots
            </button>
            <button
              type="button"
              className={`chip-btn${colFilter === "probed" ? " active" : ""}`}
              data-col-filter="probed"
              onClick={() => onSelectFilter("probed")}
            >
              Spied &amp; Graded
            </button>
            <button
              type="button"
              className={`chip-btn${colFilter === "qualified" ? " active" : ""}`}
              data-col-filter="qualified"
              onClick={() => onSelectFilter("qualified")}
            >
              300+ Fields (S / A+)
            </button>
            <button
              type="button"
              className={`chip-btn${colFilter === "unprobed" ? " active" : ""}`}
              data-col-filter="unprobed"
              onClick={() => onSelectFilter("unprobed")}
            >
              Unprobed (?)
            </button>
          </div>
          <input
            type="search"
            id="col-search"
            className="table-search-input"
            placeholder="Search coords / zone..."
            aria-label="Search colony candidates"
            value={candSearch}
            onChange={(e) => setCandSearch(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-col-sync"
            onClick={onSyncInbox}
          >
            Sync Spy Reports
          </button>
        </div>
      </div>

      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Grade"
                sortKey="grade"
                activeKey={candSort.sortKey}
                direction={candSort.sortDirection}
                onSort={candSort.requestSort}
              />
              <SortableTh
                label="Score"
                sortKey="score"
                activeKey={candSort.sortKey}
                direction={candSort.sortDirection}
                onSort={candSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Coords"
                sortKey="coords"
                activeKey={candSort.sortKey}
                direction={candSort.sortDirection}
                onSort={candSort.requestSort}
              />
              <SortableTh
                label="Orbital Zone"
                sortKey="zone"
                activeKey={candSort.sortKey}
                direction={candSort.sortDirection}
                onSort={candSort.requestSort}
              />
              <SortableTh
                label="Fields"
                sortKey="fields"
                activeKey={candSort.sortKey}
                direction={candSort.sortDirection}
                onSort={candSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Distance"
                sortKey="distance"
                activeKey={candSort.sortKey}
                direction={candSort.sortDirection}
                onSort={candSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Assessment"
                sortKey="assessment"
                activeKey={candSort.sortKey}
                direction={candSort.sortDirection}
                onSort={candSort.requestSort}
              />
              <th className="col-right">Actions</th>
            </tr>
          </thead>
          <tbody id="col-candidates-tbody">
            {pagedCands.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-cell">
                  {colonyData
                    ? "No matching colony slots. Click Scan Galaxy or probe free slots 7-12."
                    : "Scan neighbor systems or sync spy reports to grade free planet slots."}
                </td>
              </tr>
            ) : (
              pagedCands.map((c) => {
                const coords = String(c.coords || "—");
                const grade = String(c.grade || "?");
                const uf = finiteNum(c.uninhabited_fields, null);
                const cMin = finiteNum(c.colonize_min_fields, null);
                const cMax = finiteNum(c.colonize_max_fields, null);
                const fieldsDisp =
                  typeof c.fields_display === "string" && c.fields_display.trim()
                    ? c.fields_display.trim()
                    : uf !== null && uf > 0
                      ? `${Math.round(uf)} fields`
                      : cMax !== null && cMax > 0
                        ? cMin !== null && cMin > 0 && cMin !== cMax
                          ? `${Math.round(cMin)}–${Math.round(cMax)} fields`
                          : `${Math.round(cMax)} fields`
                        : "";
                const sysDist = Math.round(finiteNum(c.system_distance, 0));
                const trialStats = attemptsByCoords[coords];
                const trialDeleted =
                  trialStats && Math.round(finiteNum(trialStats.deleted_count, 0)) > 0;

                return (
                  <tr key={coords}>
                    <td>
                      <span className={`grade-badge ${gradeClassFor(grade)}`}>{grade}</span>
                    </td>
                    <td className="col-right tabular">
                      {`${Math.round(finiteNum(c.score, 0))}/100`}
                    </td>
                    <td className="tabular">
                      {coords}
                      {trialStats ? (
                        <span
                          className={`rate-chip col-trial-chip${trialDeleted ? " is-deleted" : ""}`}
                          data-trial-coords={coords}
                          title="Colonization attempts recorded in SQLite colony_attempts"
                        >
                          {attemptShortTag(trialStats, minFieldsTarget)}
                        </span>
                      ) : null}
                    </td>
                    <td>{`Pos ${c.position || "?"} · ${c.orbital_band || "Unknown"}`}</td>
                    <td className="col-right tabular">
                      {fieldsDisp ? (
                        <span className={`status-word${c.meets_target ? "" : " is-warn"}`}>
                          {fieldsDisp}
                        </span>
                      ) : (
                        "Unprobed"
                      )}
                    </td>
                    <td className="col-right tabular">
                      {sysDist === 0 ? "Same sys" : `${sysDist} sys`}
                    </td>
                    <td>{String(c.reason || "")}</td>
                    <td className="col-right">
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => onProbeCandidate(coords)}
                        >
                          {c.probed ? "Re-Probe" : "Probe"}
                        </button>
                        <button
                          type="button"
                          className={
                            c.meets_target
                              ? "btn btn-primary btn-sm"
                              : "btn btn-secondary btn-sm"
                          }
                          onClick={() => onColonizeCandidate(coords)}
                        >
                          Colonize
                        </button>
                        <button
                          type="button"
                          className="btn btn-warning btn-sm"
                          onClick={() => onDiscardCandidate(coords)}
                        >
                          Discard
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
      <TablePagination
        idPrefix="col-cand"
        totalItems={candSort.sortedRows.length}
        page={effectiveCandPage}
        pageSize={candPageSize}
        pageSizeOptions={[25, 50, 100, 200]}
        itemLabel="slots"
        onPageChange={setCandPage}
        onPageSizeChange={setCandPageSize}
      />
    </article>
  );
};
