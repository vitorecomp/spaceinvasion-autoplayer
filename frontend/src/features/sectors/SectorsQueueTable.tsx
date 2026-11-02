import React, { useEffect, useMemo, useState } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type { ScanQueueItem } from "../../types/api";
import { finiteNum, fmtInt } from "../../utils/formatters";
import { formatSectorDistance, formatShortAge } from "./sectorsHelpers";

export interface SectorsQueueTableProps {
  scanQueue: ScanQueueItem[];
  queueDiscCount: number;
  queueRefCount: number;
  queueScopeLabel: string;
  previewPending: boolean;
  isShowingPreview: boolean;
  refreshingSector: string | null;
  onScanSector: (sectorLabel: string, galaxy: number, system: number) => void;
}

export const SectorsQueueTable: React.FC<SectorsQueueTableProps> = ({
  scanQueue,
  queueDiscCount,
  queueRefCount,
  queueScopeLabel,
  previewPending,
  isShowingPreview,
  refreshingSector,
  onScanSector,
}) => {
  const [queuePhaseFilter, setQueuePhaseFilter] = useState<
    "all" | "exploratory" | "targeted_refresh"
  >("all");
  const [queuePage, setQueuePage] = useState<number>(1);
  const [queuePageSize, setQueuePageSize] = useState<number>(25);

  const queueGalBreakdown = useMemo(() => {
    const counts = new Map<number, number>();
    const order: number[] = [];
    scanQueue.forEach((q) => {
      const gi = Math.round(finiteNum(q.galaxy, 0));
      if (gi >= 1 && gi <= 14) {
        if (!counts.has(gi)) order.push(gi);
        counts.set(gi, (counts.get(gi) ?? 0) + 1);
      }
    });
    return order.map((g) => ({ galaxy: g, count: counts.get(g) ?? 0 }));
  }, [scanQueue]);

  const filteredQueue = useMemo(() => {
    return scanQueue.filter((q) => {
      if (queuePhaseFilter === "exploratory") return q.phase === "exploratory";
      if (queuePhaseFilter === "targeted_refresh") return q.phase !== "exploratory";
      return true;
    });
  }, [scanQueue, queuePhaseFilter]);

  const queueSortCols = useMemo<
    SortColumnMap<
      ScanQueueItem,
      "queuePos" | "phase" | "sector" | "distance" | "status" | "score" | "reason"
    >
  >(
    () => ({
      queuePos: (q) => finiteNum(q.queue_pos, 0),
      phase: (q) => (q.phase === "exploratory" ? "0_discovery" : "1_refresh"),
      sector: (q) => Math.round(finiteNum(q.galaxy, 0)) * 1000 + Math.round(finiteNum(q.system, 0)),
      distance: (q) => finiteNum(q.system_distance, null),
      status: (q) => (q.scanned ? finiteNum(q.age_seconds, 0) : Number.POSITIVE_INFINITY),
      score: {
        accessor: (q) => finiteNum(q.score, 0),
        defaultDirection: "desc",
      },
      reason: (q) => String(q.reason || ""),
    }),
    []
  );

  const queueSort = useTableSort(filteredQueue, queueSortCols);

  const queueTotalPages = Math.max(1, Math.ceil(queueSort.sortedRows.length / queuePageSize));
  const effectiveQueuePage = Math.max(1, Math.min(queueTotalPages, queuePage));
  const pagedQueue = useMemo(() => {
    const start = (effectiveQueuePage - 1) * queuePageSize;
    return queueSort.sortedRows.slice(start, start + queuePageSize);
  }, [queueSort.sortedRows, effectiveQueuePage, queuePageSize]);

  useEffect(() => {
    setQueuePage(1);
  }, [queuePhaseFilter, queueSort.sortKey, queueSort.sortDirection, queuePageSize]);

  useEffect(() => {
    if (queuePage > queueTotalPages) {
      setQueuePage(queueTotalPages);
    }
  }, [queuePage, queueTotalPages]);

  return (
    <article className="hud-window sectors-queue-window" id="sectors-queue-window">
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">QUE</span>
          <h2>Upcoming Scan Queue</h2>
          <span className="module-meta tabular" id="sec-queue-meta">
            {`${fmtInt(scanQueue.length)} QUEUED · ${fmtInt(queueDiscCount)} DISCOVERY · ${fmtInt(queueRefCount)} REFRESH · SCOPE ${queueScopeLabel}${previewPending ? " · UPDATING..." : isShowingPreview ? " · PREVIEW (UNSAVED)" : ""}`}
          </span>
        </div>
        <div className="filter-toolbar">
          {queueGalBreakdown.length > 0 ? (
            <div className="sector-queue-gal-pills tabular" aria-label="Queued sectors by galaxy">
              {queueGalBreakdown.map((gb) => (
                <span key={gb.galaxy} className="sector-queue-badge">
                  {`G${gb.galaxy}: ${gb.count}`}
                </span>
              ))}
            </div>
          ) : null}
          <div className="filter-chips" role="group" aria-label="Queue phase filter">
            {[
              { key: "all" as const, label: `All (${scanQueue.length})` },
              { key: "exploratory" as const, label: `Discovery (${queueDiscCount})` },
              { key: "targeted_refresh" as const, label: `Refresh (${queueRefCount})` },
            ].map((qf) => (
              <button
                key={qf.key}
                type="button"
                className={`chip-btn${queuePhaseFilter === qf.key ? " active" : ""}`}
                data-sec-qfilter={qf.key}
                onClick={() => setQueuePhaseFilter(qf.key)}
              >
                {qf.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="table-scroll">
        <table className="data-table" id="sectors-queue-table">
          <thead>
            <tr>
              <SortableTh
                label="#"
                sortKey="queuePos"
                activeKey={queueSort.sortKey}
                direction={queueSort.sortDirection}
                onSort={queueSort.requestSort}
              />
              <SortableTh
                label="Phase"
                sortKey="phase"
                activeKey={queueSort.sortKey}
                direction={queueSort.sortDirection}
                onSort={queueSort.requestSort}
              />
              <SortableTh
                label="Sector"
                sortKey="sector"
                activeKey={queueSort.sortKey}
                direction={queueSort.sortDirection}
                onSort={queueSort.requestSort}
              />
              <SortableTh
                label="Distance"
                sortKey="distance"
                activeKey={queueSort.sortKey}
                direction={queueSort.sortDirection}
                onSort={queueSort.requestSort}
              />
              <SortableTh
                label="Current Intel"
                sortKey="status"
                activeKey={queueSort.sortKey}
                direction={queueSort.sortDirection}
                onSort={queueSort.requestSort}
              />
              <SortableTh
                label="Score"
                sortKey="score"
                activeKey={queueSort.sortKey}
                direction={queueSort.sortDirection}
                onSort={queueSort.requestSort}
              />
              <SortableTh
                label="Smart Scanner Reason"
                sortKey="reason"
                activeKey={queueSort.sortKey}
                direction={queueSort.sortDirection}
                onSort={queueSort.requestSort}
              />
              <th>Ops</th>
            </tr>
          </thead>
          <tbody id="sectors-queue-tbody">
            {pagedQueue.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-state">
                  {scanQueue.length > 0
                    ? "No queued sectors match the selected phase filter."
                    : "Click Refresh DB to load the smart scanner queue."}
                </td>
              </tr>
            ) : (
              pagedQueue.map((qItem) => {
                const isDisc = qItem.phase === "exploratory";
                const distFmt = formatSectorDistance(qItem);
                const isRowBusy = refreshingSector === qItem.sector;
                const scans = Math.max(0, Math.round(finiteNum(qItem.scan_count, 0)));
                const occ = Math.max(0, Math.round(finiteNum(qItem.occupied_count, 0)));
                const deb = Math.max(0, Math.round(finiteNum(qItem.debris_count, 0)));
                const tgt = Math.max(0, Math.round(finiteNum(qItem.target_count, 0)));

                return (
                  <tr key={`${qItem.queue_pos}-${qItem.sector}`}>
                    <td className="tabular">
                      <strong>{`#${qItem.queue_pos}`}</strong>
                    </td>
                    <td>
                      <span
                        className={`sector-phase-pill ${isDisc ? "is-discovery" : "is-refresh"}`}
                      >
                        {isDisc ? "DISCOVERY" : "REFRESH"}
                      </span>
                    </td>
                    <td className="tabular">
                      <strong>{qItem.sector}</strong>
                    </td>
                    <td className="tabular" title={distFmt.title}>
                      {distFmt.label}
                    </td>
                    <td className="tabular">
                      {qItem.scanned ? (
                        <>
                          <span>{`${formatShortAge(qItem.age_seconds)} · x${Math.max(1, scans)}`}</span>
                          <span className="sector-subline">
                            {`${occ}/16 occ · ${deb} deb · ${tgt} tgt`}
                          </span>
                        </>
                      ) : (
                        <span className="text-muted">Unscanned</span>
                      )}
                    </td>
                    <td className="tabular">
                      <strong>{finiteNum(qItem.score, 0).toFixed(1)}</strong>
                    </td>
                    <td className="sector-reason-cell">{qItem.reason || "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        disabled={isRowBusy}
                        onClick={() => onScanSector(qItem.sector, qItem.galaxy, qItem.system)}
                      >
                        {isRowBusy ? "Scanning..." : "Scan Now"}
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="sec-queue"
        totalItems={queueSort.sortedRows.length}
        page={effectiveQueuePage}
        pageSize={queuePageSize}
        pageSizeOptions={[10, 25, 50, 100]}
        itemLabel="queued sectors"
        onPageChange={setQueuePage}
        onPageSizeChange={setQueuePageSize}
      />
    </article>
  );
};
