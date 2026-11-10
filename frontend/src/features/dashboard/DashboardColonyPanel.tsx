import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { coordsSortKey, useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type { PlanetData, QueueJobData } from "../../types/api";
import { finiteNum, fmtClockTime, fmtHms } from "../../utils/formatters";
import {
  cleanQueueJobTitle,
  dashboardQueueDomainForJob,
  execJobBaseKey,
  formatQueueJobCargo,
  isActiveBuildingJob,
} from "../operations/planActions";

export interface DashboardColonyPanelProps {
  hasDashboard: boolean;
  planets: PlanetData[];
  chosenId: number | undefined;
  constructingPlanetIds: ReadonlySet<number>;
  planetMetaText: string;
  b2Pct: number;
  colonyCountTag: string;
  matrixAccountLine: string;
  activeQueues: QueueJobData[];
  effNowSec: number;
  onToggleBlastFurnaceRate: (pct: number) => void;
  onSelectColony: (planetId: number) => void;
}

export const DashboardColonyPanel: React.FC<DashboardColonyPanelProps> = ({
  hasDashboard,
  planets,
  chosenId,
  constructingPlanetIds,
  planetMetaText,
  b2Pct,
  colonyCountTag,
  matrixAccountLine,
  activeQueues,
  effNowSec,
  onToggleBlastFurnaceRate,
  onSelectColony,
}) => {
  const colonySortCols = useMemo<
    SortColumnMap<PlanetData, "coords" | "colony" | "state" | "fields">
  >(
    () => ({
      coords: (p) => coordsSortKey(p.coords),
      colony: (p) => String(p.name || "Colony"),
      state: (p) => {
        if (constructingPlanetIds.has(Number(p.planet_id))) return "BUILDING";
        const isActive = p.planet_id === chosenId;
        const pigStor = finiteNum(p.storage?.pig_iron, 0);
        const pigAmt = finiteNum(p.resources?.pig_iron, 0);
        const spiStor = finiteNum(p.storage?.spice, 0);
        const spiAmt = finiteNum(p.resources?.spice, 0);
        const nearFull =
          (pigStor > 0 && pigAmt / pigStor >= 0.85) ||
          (spiStor > 0 && spiAmt / spiStor >= 0.85);
        return nearFull ? "NEAR-CAP" : isActive ? "ONLINE" : "IDLE";
      },
      fields: {
        accessor: (p) => {
          const fMax = finiteNum(p.fields_max, null);
          const fUsed = finiteNum(p.fields_used, null);
          if (fMax !== null) return fMax * 10_000 + (fUsed ?? 0);
          return fUsed;
        },
        defaultDirection: "desc",
      },
    }),
    [chosenId, constructingPlanetIds]
  );

  const colonySort = useTableSort(planets, colonySortCols);

  const liveStripQueues = activeQueues.filter(
    (q) =>
      dashboardQueueDomainForJob(q) !== "building" || isActiveBuildingJob(q, effNowSec)
  );

  return (
    <article className="hud-window">
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">MAP</span>
          <h2>Empire Colonies &amp; Active Queues</h2>
          <span className="module-meta tabular" id="planet-meta">
            {planetMetaText}
          </span>
        </div>
        <div className="inline-actions">
          <span className="module-meta tabular" id="econ-bf-status">
            {b2Pct === 0 ? "B2: 0%" : `B2: ${b2Pct}%`}
          </span>
          <button
            type="button"
            className={b2Pct === 0 ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm"}
            id="btn-dash-b2-toggle"
            data-target-pct={b2Pct === 0 ? "100" : "0"}
            onClick={() => onToggleBlastFurnaceRate(b2Pct === 0 ? 100 : 0)}
          >
            {b2Pct === 0 ? "Resume b2 (100%)" : "Pause b2 (0% +Pig)"}
          </button>
          <span className="module-meta tabular" id="colony-count-tag">
            {colonyCountTag}
          </span>
        </div>
      </div>

      <div className="matrix-body">
        <div className="table-wrap table-scroll">
          <table>
            <thead>
              <tr>
                <SortableTh
                  label="Coords"
                  sortKey="coords"
                  activeKey={colonySort.sortKey}
                  direction={colonySort.sortDirection}
                  onSort={colonySort.requestSort}
                />
                <SortableTh
                  label="Colony"
                  sortKey="colony"
                  activeKey={colonySort.sortKey}
                  direction={colonySort.sortDirection}
                  onSort={colonySort.requestSort}
                />
                <SortableTh
                  label="State"
                  sortKey="state"
                  activeKey={colonySort.sortKey}
                  direction={colonySort.sortDirection}
                  onSort={colonySort.requestSort}
                />
                <SortableTh
                  label="Fields"
                  sortKey="fields"
                  activeKey={colonySort.sortKey}
                  direction={colonySort.sortDirection}
                  onSort={colonySort.requestSort}
                  align="right"
                />
              </tr>
            </thead>
            <tbody id="colony-tbody">
              {colonySort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={4} className="empty-cell">
                    {hasDashboard ? "No colonies discovered." : "Waiting for snapshot..."}
                  </td>
                </tr>
              ) : (
                colonySort.sortedRows.map((p) => {
                  const isActive = p.planet_id === chosenId;
                  const isConstructing = constructingPlanetIds.has(Number(p.planet_id));
                  const pigStor = finiteNum(p.storage?.pig_iron, 0);
                  const pigAmt = finiteNum(p.resources?.pig_iron, 0);
                  const spiStor = finiteNum(p.storage?.spice, 0);
                  const spiAmt = finiteNum(p.resources?.spice, 0);
                  const nearFull =
                    (pigStor > 0 && pigAmt / pigStor >= 0.85) ||
                    (spiStor > 0 && spiAmt / spiStor >= 0.85);
                  const statusWord = isConstructing
                    ? "BUILDING"
                    : nearFull
                      ? "NEAR-CAP"
                      : isActive
                        ? "ONLINE"
                        : "IDLE";
                  const statusClass = isConstructing
                    ? "status-word is-constructing"
                    : nearFull
                      ? "status-word is-warn"
                      : isActive
                        ? "status-word is-active-idle"
                        : "status-word is-idle";
                  const fUsed = finiteNum(p.fields_used, null);
                  const fMax = finiteNum(p.fields_max, null);

                  return (
                    <tr
                      key={p.planet_id}
                      className={`clickable-row${isActive ? " is-active" : ""}${isConstructing ? " is-constructing" : ""}`}
                      data-constructing={isConstructing ? "true" : "false"}
                      onClick={() => onSelectColony(p.planet_id)}
                    >
                      <td className="tabular">{p.coords || "--"}</td>
                      <td>{p.name || "Colony"}</td>
                      <td>
                        <span className={statusClass}>{statusWord}</span>
                      </td>
                      <td className="col-right tabular">
                        {`${fUsed === null ? "—" : fUsed}/${fMax === null ? "—" : fMax}`}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="queue-strip">
          <div className="queue-strip-head tabular">
            <span>ACTIVE QUEUES</span>
            <span id="matrix-account-line">{matrixAccountLine}</span>
          </div>
          <div id="active-queues-list" className="queue-summary tabular">
            {liveStripQueues.length === 0
              ? hasDashboard
                ? "No active construction or research jobs."
                : "No active construction jobs."
              : liveStripQueues.map((q, idx) => {
                  const catTag = `[${String(q.category || "JOB").toUpperCase()}]`;
                  const titleTxt = cleanQueueJobTitle(q);
                  const cargoInfo = formatQueueJobCargo(q);
                  const cargoSuffix =
                    cargoInfo && !cargoInfo.isEmpty
                      ? ` · ${cargoInfo.label}: ${cargoInfo.inline}`
                      : "";
                  const finTs = finiteNum(q.finish_ts, null);
                  return (
                    <React.Fragment key={`qstrip-${execJobBaseKey(q)}-${idx}`}>
                      {idx > 0 ? " · " : null}
                      <span>
                        {finTs !== null && finTs > 0 ? (
                          <>
                            {`${catTag} ${titleTxt}${cargoSuffix} (`}
                            <span data-finish-ts={String(finTs)}>
                              {fmtHms(Math.max(0, Math.round(finTs - effNowSec)))}
                            </span>
                            {` · ${fmtClockTime(finTs, effNowSec)})`}
                          </>
                        ) : q.remaining_text ? (
                          `${catTag} ${titleTxt}${cargoSuffix} (${q.remaining_text})`
                        ) : (
                          `${catTag} ${titleTxt}${cargoSuffix}`
                        )}
                      </span>
                    </React.Fragment>
                  );
                })}
          </div>
        </div>
      </div>
    </article>
  );
};
