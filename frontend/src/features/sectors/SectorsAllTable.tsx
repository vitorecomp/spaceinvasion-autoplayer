import React, { useEffect, useMemo, useState } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type { GalaxyStatsItem, ScannedSectorItem, ScannedSectorsPayload } from "../../types/api";
import { finiteNum, fmtInt, fmtShortDateTime } from "../../utils/formatters";
import { formatSectorDistance, formatShortAge, isSectorRowScanned } from "./sectorsHelpers";

const STATUS_FILTERS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "all", label: "All Sectors" },
  { key: "scanned", label: "Scanned" },
  { key: "unscanned", label: "Unscanned" },
  { key: "queued", label: "Queued" },
  { key: "debris", label: "Has Debris" },
  { key: "targets", label: "Has Targets" },
  { key: "colony", label: "300+ Slots" },
  { key: "stale", label: "Stale (1h+)" },
];

export interface SectorsAllTableProps {
  sectorsData: ScannedSectorsPayload | null;
  allSectors: ScannedSectorItem[];
  galaxyStats: GalaxyStatsItem[];
  galaxyFilter: number;
  statusFilter: string;
  totalScannedAll: number;
  uniTotal: number;
  originLabel: string;
  refreshingSector: string | null;
  onSelectGalaxy: (g: number) => void;
  onSelectStatusFilter: (key: string) => void;
  onRefreshDb: () => void;
  onScanSector: (sectorLabel: string, galaxy: number, system: number) => void;
}

export const SectorsAllTable: React.FC<SectorsAllTableProps> = ({
  sectorsData,
  allSectors,
  galaxyStats,
  galaxyFilter,
  statusFilter,
  totalScannedAll,
  uniTotal,
  originLabel,
  refreshingSector,
  onSelectGalaxy,
  onSelectStatusFilter,
  onRefreshDb,
  onScanSector,
}) => {
  const [sectorSearch, setSectorSearch] = useState<string>("");
  const [sectorPage, setSectorPage] = useState<number>(1);
  const [sectorPageSize, setSectorPageSize] = useState<number>(25);

  const filteredSectors = useMemo(() => {
    const needle = sectorSearch.trim().toLowerCase();
    return allSectors.filter((s) => {
      if (galaxyFilter > 0 && Math.round(finiteNum(s.galaxy, 0)) !== galaxyFilter) {
        return false;
      }
      const scanned = isSectorRowScanned(s);
      if (statusFilter === "scanned" && !scanned) return false;
      if (statusFilter === "unscanned" && scanned) return false;
      if (statusFilter === "queued" && finiteNum(s.queue_pos, 0) <= 0) return false;
      if (statusFilter === "debris" && finiteNum(s.debris_count, 0) <= 0) return false;
      if (statusFilter === "targets" && finiteNum(s.target_count, 0) <= 0) return false;
      if (statusFilter === "colony" && finiteNum(s.prime_colony_free_count, 0) <= 0) return false;
      if (statusFilter === "stale" && !(scanned && finiteNum(s.age_seconds, 0) >= 3600)) {
        return false;
      }
      if (!needle) return true;
      const owners = Array.isArray(s.frequent_owners) ? s.frequent_owners.join(" ") : "";
      const hay = `${s.sector || ""} ${owners} ${s.reason || ""}`.toLowerCase();
      return hay.includes(needle);
    });
  }, [allSectors, galaxyFilter, statusFilter, sectorSearch]);

  const sectorSortCols = useMemo<
    SortColumnMap<
      ScannedSectorItem,
      | "sector"
      | "distance"
      | "occupied"
      | "debris"
      | "targets"
      | "free"
      | "raidsScore"
      | "lastRefresh"
    >
  >(
    () => ({
      sector: (s) => Math.round(finiteNum(s.galaxy, 0)) * 1000 + Math.round(finiteNum(s.system, 0)),
      distance: (s) => finiteNum(s.system_distance, null),
      occupied: {
        accessor: (s) => finiteNum(s.occupied_count, 0),
        defaultDirection: "desc",
      },
      debris: {
        accessor: (s) => finiteNum(s.debris_count, 0),
        defaultDirection: "desc",
      },
      targets: {
        accessor: (s) => finiteNum(s.target_count, 0),
        defaultDirection: "desc",
      },
      free: {
        accessor: (s) =>
          Math.round(finiteNum(s.prime_colony_free_count, 0)) * 100 +
          Math.round(finiteNum(s.free_count, 0)),
        defaultDirection: "desc",
      },
      raidsScore: {
        accessor: (s) => finiteNum(s.score, 0),
        defaultDirection: "desc",
      },
      lastRefresh: {
        accessor: (s) => finiteNum(s.last_scanned_ts, 0),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const sectorSort = useTableSort(filteredSectors, sectorSortCols);

  const sectorTotalPages = Math.max(1, Math.ceil(sectorSort.sortedRows.length / sectorPageSize));
  const effectiveSectorPage = Math.max(1, Math.min(sectorTotalPages, sectorPage));
  const pagedSectors = useMemo(() => {
    const start = (effectiveSectorPage - 1) * sectorPageSize;
    return sectorSort.sortedRows.slice(start, start + sectorPageSize);
  }, [sectorSort.sortedRows, effectiveSectorPage, sectorPageSize]);

  useEffect(() => {
    setSectorPage(1);
  }, [
    galaxyFilter,
    statusFilter,
    sectorSearch,
    sectorSort.sortKey,
    sectorSort.sortDirection,
    sectorPageSize,
  ]);

  useEffect(() => {
    if (sectorPage > sectorTotalPages) {
      setSectorPage(sectorTotalPages);
    }
  }, [sectorPage, sectorTotalPages]);

  return (
    <article className="hud-window page-fill sectors-all-window" id="sectors-all-window">
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">SEC</span>
          <h2>All-Galaxy Sectors (G1–G14)</h2>
          <span className="module-meta tabular" id="sec-meta-count">
            {`${fmtInt(filteredSectors.length)} / ${fmtInt(allSectors.length)} SHOWN · ${fmtInt(totalScannedAll)} / ${fmtInt(uniTotal)} SCANNED · ORIGIN [${originLabel}]`}
          </span>
        </div>
        <div className="filter-toolbar">
          <div className="sector-gal-select-wrap">
            <label className="sr-only" htmlFor="sec-galaxy-select">
              Galaxy filter
            </label>
            <select
              id="sec-galaxy-select"
              className="sector-gal-select tabular"
              aria-label="Galaxy filter"
              value={String(galaxyFilter)}
              onChange={(e) => onSelectGalaxy(Math.max(0, Math.round(Number(e.target.value) || 0)))}
            >
              <option value="0" data-sec-gal="0">
                {`All Galaxies (${fmtInt(totalScannedAll)})`}
              </option>
              {galaxyStats.map((gs) => {
                const qSuffix = gs.queued_count > 0 ? ` · Q${gs.queued_count}` : "";
                const ownedSuffix = gs.is_owned ? " ★" : "";
                return (
                  <option
                    key={gs.galaxy}
                    value={String(gs.galaxy)}
                    data-sec-gal={String(gs.galaxy)}
                  >
                    {`G${gs.galaxy}${ownedSuffix} (${gs.scanned_count}${qSuffix})`}
                  </option>
                );
              })}
            </select>
          </div>
          <div className="filter-chips" role="group" aria-label="Sector status filter">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className={`chip-btn${statusFilter === f.key ? " active" : ""}`}
                data-sec-filter={f.key}
                onClick={() => onSelectStatusFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <input
            type="search"
            id="sec-search"
            className="table-search-input"
            placeholder="Search sector / owner..."
            aria-label="Search scanned sectors"
            value={sectorSearch}
            onChange={(e) => setSectorSearch(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-sec-refresh"
            onClick={onRefreshDb}
          >
            Refresh DB
          </button>
        </div>
      </div>

      <div className="table-scroll">
        <table className="data-table" id="sectors-table">
          <thead>
            <tr>
              <SortableTh
                label="Sector"
                sortKey="sector"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <SortableTh
                label="Distance"
                sortKey="distance"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <SortableTh
                label="Occupied"
                sortKey="occupied"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <SortableTh
                label="Debris"
                sortKey="debris"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <SortableTh
                label="Targets"
                sortKey="targets"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <SortableTh
                label="Free / 300+"
                sortKey="free"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <SortableTh
                label="Raids / Score"
                sortKey="raidsScore"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <SortableTh
                label="Last Refresh"
                sortKey="lastRefresh"
                activeKey={sectorSort.sortKey}
                direction={sectorSort.sortDirection}
                onSort={sectorSort.requestSort}
              />
              <th>Ops</th>
            </tr>
          </thead>
          <tbody id="sectors-tbody">
            {pagedSectors.length === 0 ? (
              <tr>
                <td colSpan={9} className="empty-state">
                  {sectorsData
                    ? "No sectors match the current galaxy or status filter."
                    : "Click Refresh DB or run a Galaxy Sweep to populate sectors."}
                </td>
              </tr>
            ) : (
              pagedSectors.map((sec) => {
                const scanned = isSectorRowScanned(sec);
                const occ = Math.max(0, Math.round(finiteNum(sec.occupied_count, 0)));
                const maxSlots = Math.max(1, Math.round(finiteNum(sec.max_slots, 16)));
                const occPct = Math.min(100, Math.round((occ / maxSlots) * 100));
                const occFillCls =
                  occ >= maxSlots
                    ? "sector-occ-fill is-full"
                    : occ >= 11
                      ? "sector-occ-fill is-dense"
                      : "sector-occ-fill";
                const debris = Math.max(0, Math.round(finiteNum(sec.debris_count, 0)));
                const spacia = Math.max(0, Math.round(finiteNum(sec.spacia_count, 0)));
                const inact = Math.max(0, Math.round(finiteNum(sec.inactive_count, 0)));
                const targets = Math.max(0, Math.round(finiteNum(sec.target_count, 0)));
                const free = Math.max(0, Math.round(finiteNum(sec.free_count, 0)));
                const prime = Math.max(0, Math.round(finiteNum(sec.prime_colony_free_count, 0)));
                const dRaids = Math.max(0, Math.round(finiteNum(sec.direct_raid_count, 0)));
                const affRaids = Math.max(0, Math.round(finiteNum(sec.owner_affinity_raid_count, 0)));
                const score = finiteNum(sec.score, 0);
                const scans = Math.max(1, Math.round(finiteNum(sec.scan_count, 1)));
                const distFmt = formatSectorDistance(sec);
                const freqOwners = Array.isArray(sec.frequent_owners) ? sec.frequent_owners : [];
                const qPos = finiteNum(sec.queue_pos, null);
                const qIsRefresh = sec.queue_phase && sec.queue_phase !== "exploratory";
                const isRowBusy = refreshingSector === sec.sector;

                return (
                  <tr key={sec.sector} className={scanned ? undefined : "sector-row-unscanned"}>
                    <td className="tabular">
                      <strong>{sec.sector}</strong>
                      {qPos !== null && qPos > 0 ? (
                        <span
                          className={`sector-queue-badge${qIsRefresh ? " is-refresh" : ""}`}
                          title={`Queued #${Math.round(qPos)} in upcoming smart sweep (${qIsRefresh ? "Refresh" : "Discovery"})`}
                        >
                          {`#${Math.round(qPos)}`}
                        </span>
                      ) : null}
                    </td>
                    <td className="tabular" title={distFmt.title}>
                      {distFmt.label}
                    </td>
                    <td
                      className="tabular"
                      title={
                        scanned
                          ? `${occ} occupied out of ${maxSlots} slots (${Math.max(0, Math.round(finiteNum(sec.active_occupied_count, 0)))} active)`
                          : "Sector has not been scanned yet"
                      }
                    >
                      {scanned ? (
                        <span className="sector-occ-cell">
                          <span>{`${occ} / ${maxSlots}`}</span>
                          <span className="sector-occ-meter" aria-hidden="true">
                            <span className={occFillCls} style={{ width: `${occPct}%` }} />
                          </span>
                        </span>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="tabular">
                      {!scanned ? (
                        <span className="text-muted">—</span>
                      ) : debris > 0 ? (
                        <span className="outcome-badge is-draw">{`${debris} Debris`}</span>
                      ) : (
                        <span className="text-muted">0</span>
                      )}
                    </td>
                    <td className="tabular">
                      {!scanned ? (
                        <span className="text-muted">—</span>
                      ) : targets > 0 ? (
                        <>
                          {spacia > 0 ? (
                            <span className="outcome-badge is-win">{`${spacia} SPC`}</span>
                          ) : null}{" "}
                          {inact > 0 ? (
                            <span className="outcome-badge is-pending">{`${inact} INA`}</span>
                          ) : null}
                          {spacia === 0 && inact === 0 ? (
                            <span className="outcome-badge is-pending">{`${targets} TGT`}</span>
                          ) : null}
                        </>
                      ) : (
                        <span className="text-muted">0</span>
                      )}
                    </td>
                    <td className="tabular">
                      {scanned ? (
                        <>
                          <span>{`${free} free`}</span>
                          {prime > 0 ? (
                            <span className="sector-subline">{`${prime} prime 300+`}</span>
                          ) : null}
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="tabular" title={sec.reason || ""}>
                      <strong>{score.toFixed(1)}</strong>
                      {dRaids > 0 || affRaids > 0 ? (
                        <span className="sector-subline">
                          {`${dRaids} raids${affRaids > 0 ? ` · ${affRaids} aff` : ""}`}
                        </span>
                      ) : null}
                      {freqOwners.length > 0 ? (
                        <span className="sector-subline">{freqOwners.slice(0, 2).join(", ")}</span>
                      ) : null}
                    </td>
                    <td className="tabular">
                      {scanned ? (
                        <>
                          <span>{fmtShortDateTime(sec.last_scanned_ts)}</span>
                          <span className="sector-subline">
                            {`${formatShortAge(sec.age_seconds)} · x${scans}`}
                          </span>
                        </>
                      ) : (
                        <span className="sector-phase-pill is-discovery">Unscanned</span>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        disabled={isRowBusy}
                        onClick={() => onScanSector(sec.sector, sec.galaxy, sec.system)}
                      >
                        {isRowBusy ? "Scanning..." : scanned ? "Refresh" : "Scan"}
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
        idPrefix="sec-all"
        totalItems={sectorSort.sortedRows.length}
        page={effectiveSectorPage}
        pageSize={sectorPageSize}
        pageSizeOptions={[25, 50, 100, 200]}
        itemLabel="sectors"
        onPageChange={setSectorPage}
        onPageSizeChange={setSectorPageSize}
      />
    </article>
  );
};
