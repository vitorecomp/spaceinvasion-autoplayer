import React from "react";
import { SortableTh } from "../../components/SortableTh";
import { useTableSort } from "../../hooks/useTableSort";
import type {
  BuildingResourceRequestItem,
  PlannedActionData,
  QueueJobData,
} from "../../types/api";
import {
  finiteNum,
  fmtClockTime,
  fmtHms,
  formatResourcesInline,
} from "../../utils/formatters";
import {
  ActiveExecJobRow,
  PLAN_ACTION_SORT_COLUMNS,
  PlanActionRow,
} from "../operations/planActions";

export interface DashboardQueueCardProps {
  cardId: string;
  moduleCode: string;
  title: string;
  scopeBadge?: string;
  countId: string;
  addBtnId: string;
  addBtnAriaLabel: string;
  onAddQueueClick: () => void;
  gotoLabel: string;
  gotoBtnId?: string;
  gotoPageAttr?: string;
  onGotoClick: () => void;
  execTbodyId: string;
  planTbodyId: string;
  execJobs: QueueJobData[];
  plannedActions: PlannedActionData[];
  resourceRequests?: BuildingResourceRequestItem[];
  maxRows?: number;
  emptyText: string;
  nowEpochSec: number;
  onDispatch: (act: PlannedActionData, dryRun: boolean) => void;
  onUpdateQueue: (queueId: number, patch: Record<string, any>) => void;
  onCancelQueue: (queueId: number) => void;
  onPinQueue: (act: PlannedActionData, priority?: string) => void;
}

function normalizeRequestStatus(raw: string | undefined): {
  key: "in_flight" | "planned" | "requested";
  label: string;
} {
  const s = String(raw || "").trim().toLowerCase();
  if (s === "in_flight" || s === "inflight" || s === "in-flight" || s === "active") {
    return { key: "in_flight", label: "IN FLIGHT" };
  }
  if (s === "planned" || s === "queued" || s === "scheduled") {
    return { key: "planned", label: "PLANNED" };
  }
  return { key: "requested", label: "REQUESTED" };
}

export const DashboardQueueCard: React.FC<DashboardQueueCardProps> = ({
  cardId,
  moduleCode,
  title,
  scopeBadge,
  countId,
  addBtnId,
  addBtnAriaLabel,
  onAddQueueClick,
  gotoLabel,
  gotoBtnId,
  gotoPageAttr,
  onGotoClick,
  execTbodyId,
  planTbodyId,
  execJobs,
  plannedActions,
  resourceRequests,
  maxRows,
  emptyText,
  nowEpochSec,
  onDispatch,
  onUpdateQueue,
  onCancelQueue,
  onPinQueue,
}) => {
  const activeList = Array.isArray(execJobs) ? execJobs : [];
  const reqList = Array.isArray(resourceRequests) ? resourceRequests : [];
  const totalCount = activeList.length + plannedActions.length;
  const planSort = useTableSort(plannedActions, PLAN_ACTION_SORT_COLUMNS);
  const visibleRows =
    maxRows != null && maxRows > 0
      ? planSort.sortedRows.slice(0, maxRows)
      : planSort.sortedRows;

  return (
    <article className="hud-window dash-queue-card" id={cardId}>
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">{moduleCode}</span>
          <h2>{title}</h2>
          <span className="module-meta tabular dash-q-count" id={countId}>
            {String(totalCount)}
          </span>
          {reqList.length > 0 ? (
            <span className="module-meta tabular dash-req-count" id="dash-bld-req-count">
              {`${reqList.length} REQ`}
            </span>
          ) : null}
          {scopeBadge ? (
            <span className="module-meta tabular dash-q-scope">{scopeBadge}</span>
          ) : null}
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            id={addBtnId}
            aria-label={addBtnAriaLabel}
            onClick={onAddQueueClick}
          >
            + Queue
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id={gotoBtnId}
            data-goto-page={gotoPageAttr}
            onClick={onGotoClick}
          >
            {gotoLabel}
          </button>
        </div>
      </div>

      {reqList.length > 0 ? (
        <div
          className="dash-res-requests"
          id="dash-building-resource-requests"
          aria-label="Building queue resource requests"
        >
          <div className="dash-res-requests-head tabular">
            <span className="dash-res-requests-title">RESOURCE REQUESTS</span>
            <span className="dash-res-requests-meta">
              {`${reqList.length} ${reqList.length === 1 ? "request" : "requests"}`}
            </span>
          </div>
          <div className="dash-res-requests-list">
            {reqList.map((req, idx) => {
              const st = normalizeRequestStatus(req.status);
              const resBundle = req.resources || {
                pig_iron: req.pig_iron,
                metal: req.metal,
                kryptonite: req.kryptonite,
                spice: req.spice,
              };
              const resStr = formatResourcesInline(resBundle);
              const targetTitle = req.target_building
                ? `${req.target_building}${req.target_level != null ? ` → L${req.target_level}` : ""}`
                : req.label || "Resource delivery";
              const destLabel = req.planet_coords
                ? req.planet_name
                  ? `[${req.planet_coords}] ${req.planet_name}`
                  : `[${req.planet_coords}]`
                : req.planet_name || "";
              const routeLabel =
                req.source_coords && req.planet_coords
                  ? `[${req.source_coords}] → [${req.planet_coords}]`
                  : destLabel
                    ? `→ ${destLabel}`
                    : "";
              const finTs = finiteNum(req.finish_ts, null);
              const etaSec =
                finTs !== null && finTs > 0
                  ? Math.max(0, Math.round(finTs - nowEpochSec))
                  : finiteNum(req.eta_seconds, null);
              const etaText =
                etaSec !== null && etaSec > 0
                  ? fmtHms(Math.round(etaSec))
                  : finTs !== null && finTs > 0
                    ? "Arriving"
                    : "";
              const etaClockTitle =
                finTs !== null && finTs > 0
                  ? `ETA ${etaText} (at ${fmtClockTime(finTs, nowEpochSec)})`
                  : req.reason || undefined;
              return (
                <div
                  key={req.id || `res-req-${idx}-${req.planet_id ?? req.planet_coords ?? idx}`}
                  className={`dash-res-req-row is-${st.key.replace("_", "-")}`}
                  data-req-status={st.key}
                  title={req.reason || etaClockTitle}
                >
                  <div className="dash-res-req-main">
                    <span className={`dash-req-status is-${st.key.replace("_", "-")} tabular`}>
                      {st.label}
                    </span>
                    <span className="dash-req-target">{targetTitle}</span>
                    {routeLabel ? (
                      <span className="dash-req-route tabular">{routeLabel}</span>
                    ) : null}
                  </div>
                  <div className="dash-res-req-values tabular">
                    <span className="dash-req-amounts">{resStr}</span>
                    {etaText ? (
                      <span className="dash-req-eta" title={etaClockTitle}>
                        {etaText}
                      </span>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Prio"
                sortKey="priority"
                activeKey={planSort.sortKey}
                direction={planSort.sortDirection}
                onSort={planSort.requestSort}
              />
              <SortableTh
                label="Action"
                sortKey="action"
                activeKey={planSort.sortKey}
                direction={planSort.sortDirection}
                onSort={planSort.requestSort}
              />
              <SortableTh
                label="Missing / Cost"
                sortKey="costMissing"
                activeKey={planSort.sortKey}
                direction={planSort.sortDirection}
                onSort={planSort.requestSort}
              />
              <SortableTh
                label="Wait ETA"
                sortKey="waitEta"
                activeKey={planSort.sortKey}
                direction={planSort.sortDirection}
                onSort={planSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Total ETA / Finish"
                sortKey="totalEta"
                activeKey={planSort.sortKey}
                direction={planSort.sortDirection}
                onSort={planSort.requestSort}
                align="right"
              />
              <th className="col-right">Ops</th>
            </tr>
          </thead>
          <tbody id={execTbodyId} className="dash-exec-tbody" hidden={activeList.length === 0}>
            {activeList.map((job, idx) => (
              <ActiveExecJobRow
                key={`exec-${job.category || "job"}-${job.queue_slot ?? idx}-${job.name || idx}`}
                job={job}
                nowEpochSec={nowEpochSec}
              />
            ))}
          </tbody>
          <tbody id={planTbodyId}>
            {visibleRows.length > 0 ? (
              visibleRows.map((act, idx) => (
                <PlanActionRow
                  key={`plan-${act.manual_queue_id ?? "auto"}-${act.kind}-${act.planet_id}-${act.target_id ?? act.target_name}-${idx}`}
                  action={act}
                  compact={true}
                  nowEpochSec={nowEpochSec}
                  onDispatch={onDispatch}
                  onUpdateQueue={onUpdateQueue}
                  onCancelQueue={onCancelQueue}
                  onPinQueue={onPinQueue}
                />
              ))
            ) : activeList.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">
                  {emptyText}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </article>
  );
};

export default DashboardQueueCard;
