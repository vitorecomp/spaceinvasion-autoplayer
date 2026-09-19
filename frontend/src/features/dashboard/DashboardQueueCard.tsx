import React from "react";
import { SortableTh } from "../../components/SortableTh";
import { useTableSort } from "../../hooks/useTableSort";
import type { PlannedActionData, QueueJobData } from "../../types/api";
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
  maxRows?: number;
  emptyText: string;
  nowEpochSec: number;
  onDispatch: (act: PlannedActionData, dryRun: boolean) => void;
  onUpdateQueue: (queueId: number, patch: Record<string, any>) => void;
  onCancelQueue: (queueId: number) => void;
  onPinQueue: (act: PlannedActionData, priority?: string) => void;
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
  maxRows,
  emptyText,
  nowEpochSec,
  onDispatch,
  onUpdateQueue,
  onCancelQueue,
  onPinQueue,
}) => {
  const activeList = Array.isArray(execJobs) ? execJobs : [];
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
