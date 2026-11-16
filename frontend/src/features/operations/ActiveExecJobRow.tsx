import React from "react";
import type { QueueJobData } from "../../types/api";
import { finiteNum, fmtClockTime, fmtCompact, fmtHms, fmtInt } from "../../utils/formatters";
import {
  cleanQueueJobTitle,
  dashboardQueueDomainForJob,
  formatPlanetTag,
  normalizeCoordKey,
} from "./planActionHelpers";
import { formatQueueJobCargo } from "./queueJobCargo";

export interface ActiveExecJobRowProps {
  job: QueueJobData;
  compact?: boolean;
  nowEpochSec?: number;
  planetNameById?: ReadonlyMap<number, string>;
  planetNameByCoords?: ReadonlyMap<string, string>;
}

export const ActiveExecJobRow: React.FC<ActiveExecJobRowProps> = ({
  job,
  compact = false,
  nowEpochSec,
  planetNameById,
  planetNameByCoords,
}) => {
  const slot = Math.max(1, Math.trunc(finiteNum(job?.queue_slot, 1)));
  const detailText =
    String(job?.detail || "").trim() || (slot > 1 ? `In-game queue #${slot}` : "Executing now");
  const cargoInfo = formatQueueJobCargo(job);
  const finTs = finiteNum(job?.finish_ts, null);
  const effNowSec =
    nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1000);
  const remSec =
    finTs !== null && finTs > 0
      ? Math.max(0, Math.round(finTs - effNowSec))
      : finiteNum(job?.remaining_seconds, null);

  let remDisplay = String(job?.remaining_text || "").trim() || "Running";
  if (finTs !== null && finTs > 0) {
    remDisplay = remSec !== null && remSec > 0 ? fmtHms(remSec) : "Finishing...";
  }

  const finDisplay = finTs !== null && finTs > 0 ? fmtClockTime(finTs, effNowSec) : "In progress";
  const detLow = String(job?.detail || "").toLowerCase();
  const isReturning =
    Boolean(job?.returning) || detLow.includes("returning") || String(job?.name || "").includes("↩");
  let liveLabel = "Executing";
  if (slot > 1) {
    liveLabel = `Queued #${slot}`;
  } else if (isReturning) {
    liveLabel = "Returning";
  } else if (detLow.includes("outbound") || detLow.includes("flight")) {
    liveLabel = "In flight";
  }

  const isBldDomain = dashboardQueueDomainForJob(job) === "building";
  const bldCoords = isBldDomain ? String(job?.origin_coords || job?.planet_coords || "").trim() : "";
  const jobPid =
    job?.planet_id != null && Number.isFinite(Number(job.planet_id))
      ? Math.trunc(Number(job.planet_id))
      : 0;
  const bldCoordKey = bldCoords ? normalizeCoordKey(bldCoords) : "";
  const bldPlanetName = isBldDomain
    ? (jobPid > 0 ? planetNameById?.get(jobPid) : undefined) ||
      (bldCoordKey ? planetNameByCoords?.get(bldCoordKey) : undefined) ||
      ""
    : "";

  return (
    <tr className={slot > 1 ? "dash-exec-row is-queued-slot" : "dash-exec-row"}>
      <td>
        <span className={slot > 1 ? "prio-badge prio-game-q tabular" : "prio-badge prio-exec tabular"}>
          {slot > 1 ? `GAME #${slot}` : "EXEC"}
        </span>
      </td>
      <td>
        <div className="action-title">{cleanQueueJobTitle(job)}</div>
        {bldCoords || bldPlanetName ? (
          <div className="action-sub tabular">{formatPlanetTag(bldPlanetName, bldCoords)}</div>
        ) : null}
      </td>
      <td>
        <span className="action-sub">{detailText}</span>
        {cargoInfo ? (
          <div
            className={`exec-cargo-line is-${cargoInfo.variant} tabular${cargoInfo.isEmpty ? " is-empty" : ""}`}
            data-cargo-variant={cargoInfo.variant}
            data-delivered={cargoInfo.delivered ? "true" : undefined}
            title={`${cargoInfo.label}: ${cargoInfo.inline}${cargoInfo.total > 0 ? ` (${fmtInt(cargoInfo.total)} total)` : ""}`}
          >
            <span className="exec-cargo-tag">{cargoInfo.label}</span>{" "}
            {cargoInfo.inline}
            {cargoInfo.multiRes ? ` (${fmtCompact(cargoInfo.total)})` : ""}
          </div>
        ) : null}
      </td>
      {compact ? (
        <td className="col-right tabular dash-eta-cell">
          <div className="dash-eta-stack">
            <span
              className="eta-pill is-exec-timer"
              data-finish-ts={finTs !== null && finTs > 0 ? String(finTs) : undefined}
            >
              {remDisplay}
            </span>{" "}
            <span className="eta-pill is-finish-at">{finDisplay}</span>
          </div>
        </td>
      ) : (
        <>
          <td className="col-right tabular">
            <span
              className="eta-pill is-exec-timer"
              data-finish-ts={finTs !== null && finTs > 0 ? String(finTs) : undefined}
            >
              {remDisplay}
            </span>
          </td>
          <td className="col-right tabular">
            <span className="eta-pill is-finish-at">{finDisplay}</span>
          </td>
        </>
      )}
      <td className="col-right">
        <span className={slot > 1 ? "exec-live-label is-slot" : "exec-live-label"}>
          {liveLabel}
        </span>
      </td>
    </tr>
  );
};
