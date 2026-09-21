import React, { useEffect, useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import {
  fmtCompact,
  fmtInt,
  formatResourcesInline,
} from "../../utils/formatters";
import {
  buildPlanetLookup,
  extractCustomPlanetNames,
  flightMatchesPlanetFilter,
  isOngoingFlightJob,
  parseFlightRow,
  summarizeFlights,
  type FlightDirectionFilter,
  type FlightMissionFilter,
  type FlightQueueJob,
  type ParsedFlightRow,
} from "./flightHelpers";

export interface FleetControlViewProps {
  hidden?: boolean;
}

const MISSION_PILLS: Array<{ key: FlightMissionFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "attack", label: "Attack" },
  { key: "recycle", label: "Recycle" },
  { key: "transport", label: "Transport" },
  { key: "spy", label: "Spy" },
  { key: "other", label: "Other" },
];

const DIRECTION_PILLS: Array<{ key: FlightDirectionFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "outbound", label: "Outbound" },
  { key: "returning", label: "Returning" },
];

type FlightSortKey =
  | "mission"
  | "direction"
  | "origin"
  | "target"
  | "ships"
  | "cargo"
  | "eta";

export const FleetControlView: React.FC<FleetControlViewProps> = ({ hidden }) => {
  const {
    dashboard,
    activePage,
    nowEpochSec,
    loadDashboard,
    apiCommand,
    showFeedback,
  } = useConsole();

  const [missionFilter, setMissionFilter] = useState<FlightMissionFilter>("all");
  const [directionFilter, setDirectionFilter] = useState<FlightDirectionFilter>("all");
  const [planetFilter, setPlanetFilter] = useState<string>("all");
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [localNowSec, setLocalNowSec] = useState<number>(() =>
    typeof window === "undefined" ? 0 : Math.floor(Date.now() / 1000)
  );

  const isHidden = hidden ?? (activePage as string) !== "fleet-control";

  useEffect(() => {
    if (isHidden || typeof window === "undefined") return;
    setLocalNowSec(Math.floor(Date.now() / 1000));
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") {
        setLocalNowSec(Math.floor(Date.now() / 1000));
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [isHidden]);

  const effNowSec = Math.max(
    nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : 0,
    localNowSec > 0 ? localNowSec : 0
  ) || Math.floor(Date.now() / 1000);

  const customPlanetNames = useMemo(
    () => extractCustomPlanetNames(dashboard),
    [dashboard]
  );

  const planetLookup = useMemo(
    () => buildPlanetLookup(dashboard?.state?.planets, customPlanetNames),
    [dashboard?.state?.planets, customPlanetNames]
  );

  const allFlights = useMemo<ParsedFlightRow[]>(() => {
    const rawQueues = Array.isArray(dashboard?.state?.active_queues)
      ? (dashboard!.state!.active_queues as FlightQueueJob[])
      : [];
    const ongoing = rawQueues.filter((job) => isOngoingFlightJob(job));
    return ongoing.map((job, idx) => parseFlightRow(job, idx, planetLookup, effNowSec));
  }, [dashboard?.state?.active_queues, effNowSec, planetLookup]);

  const summary = useMemo(() => summarizeFlights(allFlights), [allFlights]);

  const selectedFilterPlanetId = useMemo<number | null>(() => {
    if (planetFilter === "all") return null;
    const n = Number(planetFilter);
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
  }, [planetFilter]);

  const filteredFlights = useMemo(() => {
    return allFlights.filter((row) => {
      if (missionFilter !== "all" && row.missionFilter !== missionFilter) {
        return false;
      }
      if (directionFilter !== "all" && row.direction !== directionFilter) {
        return false;
      }
      if (
        selectedFilterPlanetId !== null &&
        !flightMatchesPlanetFilter(row, selectedFilterPlanetId, planetLookup)
      ) {
        return false;
      }
      return true;
    });
  }, [allFlights, directionFilter, missionFilter, planetLookup, selectedFilterPlanetId]);

  const sortColumns = useMemo<SortColumnMap<ParsedFlightRow, FlightSortKey>>(
    () => ({
      mission: (r) => r.missionLabel,
      direction: (r) => (r.direction === "outbound" ? "0-outbound" : "1-returning"),
      origin: (r) => `${r.originName} ${r.originCoords}`.trim(),
      target: (r) => `${r.targetName} ${r.targetCoords}`.trim(),
      ships: (r) => r.shipsText || r.labelText || null,
      cargo: {
        accessor: (r) => {
          if (!r.cargo) return null;
          if (r.cargo.total > 0) return r.cargo.total;
          return r.cargo.kind === "DELIVERED" ? 0 : null;
        },
        defaultDirection: "desc",
      },
      eta: {
        accessor: (r) =>
          r.remainingSec !== null
            ? r.remainingSec
            : r.finishTs !== null
              ? Math.max(0, Math.round(r.finishTs - effNowSec))
              : null,
        defaultDirection: "asc",
      },
    }),
    [effNowSec]
  );

  const flightSort = useTableSort(filteredFlights, sortColumns, {
    initialKey: "eta",
    initialDirection: "asc",
  });

  const hasActiveFilters =
    missionFilter !== "all" || directionFilter !== "all" || planetFilter !== "all";

  const handleResetFilters = () => {
    setMissionFilter("all");
    setDirectionFilter("all");
    setPlanetFilter("all");
  };

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      showFeedback("Refreshing live active flights from Chrome...", "ok");
      const ok = await loadDashboard({ refresh: true, force: true });
      if (ok) {
        showFeedback("Fleet Control flights refreshed.", "ok");
      }
    } catch (err: any) {
      showFeedback(`Flight refresh failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setRefreshing(false);
    }
  };

  const handleNavFleet = async () => {
    try {
      await apiCommand({ command: "navigate", action: "internalFleet" });
      showFeedback("Chrome switched to internalFleet.", "ok");
    } catch (err: any) {
      showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const expectedInline =
    summary.expectedTotal > 0 ? formatResourcesInline(summary.expectedBundle) : "";
  const deliveredInline =
    summary.deliveredTotal > 0 ? formatResourcesInline(summary.deliveredBundle) : "";

  const cargoSubText = (() => {
    const parts: string[] = [];
    if (expectedInline) parts.push(`Expected: ${expectedInline}`);
    if (deliveredInline) parts.push(`Delivered: ${deliveredInline}`);
    else if (summary.deliveredCount > 0) {
      parts.push(
        `${summary.deliveredCount} delivered return${summary.deliveredCount === 1 ? "" : "s"}`
      );
    }
    return parts.length > 0 ? parts.join(" · ") : "No expected or delivered cargo in flight";
  })();

  return (
    <section
      className="page-view view-panel"
      id="view-fleet-control"
      hidden={isHidden}
      aria-label="Fleet Control — current flights"
    >
      <div className="combat-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Active Flights</span>
          <strong className="kpi-value tabular" id="flt-ctrl-kpi-total">
            {fmtInt(summary.total)}
          </strong>
          <span className="kpi-sub tabular" id="flt-ctrl-kpi-dir">
            {`${fmtInt(summary.outboundCount)} Outbound · ${fmtInt(summary.returningCount)} Returning`}
          </span>
        </div>

        <div className="kpi-card">
          <span className="kpi-label tabular">Mission Breakdown</span>
          <strong className="kpi-value tabular" id="flt-ctrl-kpi-missions">
            {`ATK ${summary.missionCounts.attack} · REC ${summary.missionCounts.recycle} · TRN ${summary.missionCounts.transport}`}
          </strong>
          <span className="kpi-sub tabular" id="flt-ctrl-kpi-missions-sub">
            {`Spy ${summary.missionCounts.spy} · Other ${summary.missionCounts.other}`}
          </span>
        </div>

        <div className="kpi-card">
          <span className="kpi-label tabular">Expected / Delivered Cargo</span>
          <strong className="kpi-value tabular" id="flt-ctrl-kpi-cargo">
            {`Exp ${summary.expectedTotal > 0 ? fmtCompact(summary.expectedTotal) : "0"} · Del ${
              summary.deliveredTotal > 0
                ? fmtCompact(summary.deliveredTotal)
                : summary.deliveredCount > 0
                  ? `${summary.deliveredCount}✓`
                  : "0"
            }`}
          </strong>
          <span className="kpi-sub tabular" id="flt-ctrl-kpi-cargo-sub" title={cargoSubText}>
            {cargoSubText}
          </span>
        </div>

        <div className="kpi-card">
          <span className="kpi-label tabular">Fleet Control Sync</span>
          <div className="combat-jumps">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-flt-ctrl-refresh"
              disabled={refreshing}
              onClick={handleRefresh}
            >
              {refreshing ? "Refreshing..." : "Refresh Flights"}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-flt-ctrl-nav-fleet"
              onClick={handleNavFleet}
            >
              Open Fleet
            </button>
          </div>
          <span className="kpi-sub tabular">
            {`${planetLookup.ordered.length} ${planetLookup.ordered.length === 1 ? "colony" : "colonies"} tracked`}
          </span>
        </div>
      </div>

      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">FLT</span>
            <h2>Fleet Control — Current Ongoing Flights</h2>
            <span className="module-meta tabular" id="flt-ctrl-count-meta">
              {filteredFlights.length === allFlights.length
                ? `${allFlights.length} active`
                : `${filteredFlights.length} / ${allFlights.length} shown`}
            </span>
          </div>
          <div className="inline-actions">
            {hasActiveFilters ? (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-flt-ctrl-reset-filters"
                onClick={handleResetFilters}
              >
                Reset Filters
              </button>
            ) : null}
          </div>
        </div>

        <div className="email-filter-bar" aria-label="Flight filters">
          <div className="filter-chips" role="group" aria-label="Mission filter">
            {MISSION_PILLS.map((pill) => {
              const active = missionFilter === pill.key;
              const count = summary.missionCounts[pill.key];
              return (
                <button
                  key={pill.key}
                  type="button"
                  className={`chip-btn${active ? " active" : ""}`}
                  data-flt-mission={pill.key}
                  aria-pressed={active}
                  onClick={() => setMissionFilter(pill.key)}
                >
                  {`${pill.label} (${count})`}
                </button>
              );
            })}
          </div>

          <div className="filter-chips" role="group" aria-label="Direction filter">
            {DIRECTION_PILLS.map((pill) => {
              const active = directionFilter === pill.key;
              const count =
                pill.key === "all"
                  ? summary.total
                  : pill.key === "outbound"
                    ? summary.outboundCount
                    : summary.returningCount;
              return (
                <button
                  key={pill.key}
                  type="button"
                  className={`chip-btn${active ? " active" : ""}`}
                  data-flt-dir={pill.key}
                  aria-pressed={active}
                  onClick={() => setDirectionFilter(pill.key)}
                >
                  {`${pill.label} (${count})`}
                </button>
              );
            })}
          </div>

          <select
            id="flt-ctrl-planet-filter"
            className="mq-prio-select"
            aria-label="Filter by planet"
            value={planetFilter}
            onChange={(e) => setPlanetFilter(e.target.value)}
            style={{ width: "auto", minWidth: "180px" }}
          >
            <option value="all">All Planets</option>
            {planetLookup.ordered.map((entry) => (
              <option key={entry.planet.planet_id} value={String(entry.planet.planet_id)}>
                {entry.rawCoords ? `${entry.displayName} [${entry.rawCoords}]` : entry.displayName}
              </option>
            ))}
          </select>
        </div>

        <div className="table-wrap table-scroll">
          <table id="flt-ctrl-table" className="data-table">
            <thead>
              <tr>
                <SortableTh
                  label="Mission"
                  sortKey="mission"
                  activeKey={flightSort.sortKey}
                  direction={flightSort.sortDirection}
                  onSort={flightSort.requestSort}
                />
                <SortableTh
                  label="Direction"
                  sortKey="direction"
                  activeKey={flightSort.sortKey}
                  direction={flightSort.sortDirection}
                  onSort={flightSort.requestSort}
                />
                <SortableTh
                  label="Origin"
                  sortKey="origin"
                  activeKey={flightSort.sortKey}
                  direction={flightSort.sortDirection}
                  onSort={flightSort.requestSort}
                />
                <SortableTh
                  label="Target"
                  sortKey="target"
                  activeKey={flightSort.sortKey}
                  direction={flightSort.sortDirection}
                  onSort={flightSort.requestSort}
                />
                <SortableTh
                  label="Ships / Label"
                  sortKey="ships"
                  activeKey={flightSort.sortKey}
                  direction={flightSort.sortDirection}
                  onSort={flightSort.requestSort}
                />
                <SortableTh
                  label="Cargo / Status"
                  sortKey="cargo"
                  activeKey={flightSort.sortKey}
                  direction={flightSort.sortDirection}
                  onSort={flightSort.requestSort}
                />
                <SortableTh
                  label="ETA / Arrival"
                  sortKey="eta"
                  activeKey={flightSort.sortKey}
                  direction={flightSort.sortDirection}
                  onSort={flightSort.requestSort}
                  align="right"
                />
              </tr>
            </thead>
            <tbody id="flt-ctrl-tbody" data-testid="fleet-control-tbody">
              {flightSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={7} className="empty-cell">
                    {allFlights.length === 0
                      ? "No active flights in progress."
                      : "No ongoing flights match the current filters."}
                  </td>
                </tr>
              ) : (
                flightSort.sortedRows.map((row) => (
                  <tr key={row.id} className="queue-row" data-flight-row={row.mission.toLowerCase()}>
                    <td>
                      <span
                        className={`${row.missionBadgeClass} tabular`}
                        data-flight-mission={row.mission}
                      >
                        {row.missionLabel}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`sector-phase-pill ${
                          row.direction === "returning" ? "is-refresh" : "is-discovery"
                        } tabular`}
                        data-flight-dir={row.direction}
                      >
                        {row.direction === "returning" ? "RETURNING" : "OUTBOUND"}
                      </span>
                    </td>
                    <td>
                      <div className="action-title">{row.originName}</div>
                      {row.originCoords && row.originCoords !== row.originName ? (
                        <span className="action-sub tabular">{`[${row.originCoords}]`}</span>
                      ) : null}
                    </td>
                    <td>
                      <div className="action-title">{row.targetName}</div>
                      {row.targetCoords && row.targetCoords !== row.targetName ? (
                        <span className="action-sub tabular">{`[${row.targetCoords}]`}</span>
                      ) : null}
                    </td>
                    <td>
                      <div className="action-title">{row.shipsText || row.labelText || "—"}</div>
                      {row.shipsText && row.labelText && row.labelText !== row.shipsText ? (
                        <span className="action-sub">{row.labelText}</span>
                      ) : null}
                    </td>
                    <td>
                      {row.cargo ? (
                        row.cargo.kind === "DELIVERED" ? (
                          <div
                            className="exec-cargo-line tabular"
                            data-cargo-flag="DELIVERED"
                            title={
                              row.cargo.inline
                                ? `Delivered: ${row.cargo.inline} (${fmtInt(row.cargo.total)} total)`
                                : "Delivered"
                            }
                          >
                            <span className="outcome-badge is-win tabular">DELIVERED</span>
                            {row.cargo.inline ? (
                              <>
                                {" "}
                                <span>{row.cargo.inline}</span>
                                {row.cargo.multiRes ? ` (${fmtCompact(row.cargo.total)})` : ""}
                              </>
                            ) : null}
                          </div>
                        ) : (
                          <div
                            className={`exec-cargo-line tabular${row.cargo.isEmpty ? " is-empty" : ""}`}
                            data-cargo-flag={row.cargo.kind}
                            title={`${row.cargo.kind}: ${row.cargo.inline}${
                              row.cargo.total > 0 ? ` (${fmtInt(row.cargo.total)} total)` : ""
                            }`}
                          >
                            <span className="exec-cargo-tag">{row.cargo.kind}</span>{" "}
                            <span>{row.cargo.inline}</span>
                            {row.cargo.multiRes ? ` (${fmtCompact(row.cargo.total)})` : ""}
                          </div>
                        )
                      ) : (
                        <span className="action-sub">—</span>
                      )}
                    </td>
                    <td className="col-right tabular">
                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "flex-end",
                          gap: "2px",
                        }}
                      >
                        <span
                          className="eta-pill is-exec-timer"
                          data-finish-ts={row.finishTs !== null ? String(row.finishTs) : undefined}
                        >
                          {row.countdownText}
                        </span>
                        <span className="eta-pill is-finish-at">{row.finishClockText}</span>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
};

export default FleetControlView;
