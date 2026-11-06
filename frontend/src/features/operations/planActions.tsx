import React, { useEffect, useRef } from "react";
import type { PlannedActionData } from "../../types/api";
import {
  bottleneckTag,
  finiteNum,
  fmtClockTime,
  formatResourcesInline,
} from "../../utils/formatters";
import {
  actionPayloadFor,
  formatPlanetTag,
  normalizeCoordKey,
} from "./planActionHelpers";

export {
  DASH_FLEET_KINDS,
  MQ_BUILDINGS_CATALOG,
  MQ_RESEARCH_CATALOG,
  MQ_SHIPS_CATALOG,
  PLAN_ACTION_SORT_COLUMNS,
  actionPayloadFor,
  buildPinPayloadFromAction,
  buildPlanetNameMaps,
  cleanQueueJobTitle,
  dashboardQueueDomainForAction,
  dashboardQueueDomainForJob,
  execJobBaseKey,
  formatPlanetTag,
  isActiveBuildingJob,
  isOngoingFleetFlight,
  isPlanetConstructing,
  normalizeCoordKey,
  queueJobMatchesPlanet,
  type PlanActionSortKey,
} from "./planActionHelpers";
export {
  formatQueueJobCargo,
  type QueueJobCargoDisplay,
  type QueueJobCargoVariant,
} from "./queueJobCargo";
export { ActiveExecJobRow, type ActiveExecJobRowProps } from "./ActiveExecJobRow";

export interface PlanActionRowProps {
  action: PlannedActionData;
  compact?: boolean;
  nowEpochSec?: number;
  planetNameById?: ReadonlyMap<number, string>;
  planetNameByCoords?: ReadonlyMap<string, string>;
  onDispatch: (act: PlannedActionData, dryRun: boolean) => void;
  onUpdateQueue: (queueId: number, patch: Record<string, any>) => void;
  onCancelQueue: (queueId: number) => void;
  onPinQueue: (act: PlannedActionData, priority?: string) => void;
}

export const PlanActionRow: React.FC<PlanActionRowProps> = ({
  action: act,
  compact = false,
  nowEpochSec,
  planetNameById,
  planetNameByCoords,
  onDispatch,
  onUpdateQueue,
  onCancelQueue,
  onPinQueue,
}) => {
  const isManual = act.manual_queue_id != null && Number(act.manual_queue_id) > 0;
  const qid = isManual ? Number(act.manual_queue_id) : 0;
  const prioVal = String(act.priority || "?");

  const prioSelectRef = useRef<HTMLSelectElement | null>(null);
  const handledEventsRef = useRef<WeakSet<Event>>(new WeakSet());

  useEffect(() => {
    const sel = prioSelectRef.current;
    if (!sel || !isManual || compact) return;
    const onNativeChange = (ev: Event) => {
      if (handledEventsRef.current.has(ev)) return;
      handledEventsRef.current.add(ev);
      const nextVal = sel.value;
      if (nextVal && nextVal !== prioVal) {
        onUpdateQueue(qid, { priority: nextVal, move: "top" });
      }
    };
    sel.addEventListener("change", onNativeChange);
    return () => sel.removeEventListener("change", onNativeChange);
  }, [compact, isManual, onUpdateQueue, prioVal, qid]);

  let lvlStr = "";
  if (act.kind === "THROTTLE_BUILDING" && act.target_level != null) {
    lvlStr = ` (${act.target_level}%)`;
  } else if (act.kind === "BUILD_SHIP" && act.target_level) {
    lvlStr = ` x${act.target_level}`;
  } else if (act.target_level != null) {
    lvlStr = ` → L${act.target_level}`;
  }

  const costStr = formatResourcesInline(act.cost);
  const missStr = formatResourcesInline(act.missing);

  const hasRawWait =
    act.wait_eta !== undefined && act.wait_eta !== null && String(act.wait_eta).trim() !== "";
  const isBlocked =
    (hasRawWait && String(act.wait_eta) === "—") ||
    act.wait_hours === null ||
    (!hasRawWait &&
      !act.executable_now &&
      (act.wait_hours == null ||
        !Number.isFinite(Number(act.wait_hours)) ||
        Number(act.wait_hours) <= 0));
  const waitHoursNum =
    act.wait_hours !== undefined &&
    act.wait_hours !== null &&
    Number.isFinite(Number(act.wait_hours))
      ? Number(act.wait_hours)
      : 0;
  const waitEta = hasRawWait
    ? String(act.wait_eta)
    : isBlocked
      ? "—"
      : act.executable_now
        ? "Ready"
        : "—";
  const bnTag = bottleneckTag(act.eta_bottleneck);
  const isWaiting =
    !isBlocked &&
    (waitHoursNum > 0 || (waitEta !== "Ready" && waitEta !== "Instant" && waitEta !== "—"));

  const waitPillText = (isBlocked || isWaiting) && bnTag ? `${waitEta} (${bnTag})` : waitEta;

  const hasRawTotal =
    act.total_eta !== undefined && act.total_eta !== null && String(act.total_eta).trim() !== "";
  const hasRawDur =
    act.duration_text !== undefined &&
    act.duration_text !== null &&
    String(act.duration_text).trim() !== "";
  const durText = hasRawDur ? String(act.duration_text) : "";
  const totalBlocked =
    isBlocked || (hasRawTotal && String(act.total_eta) === "—") || act.total_hours === null;
  const totalEta = hasRawTotal
    ? String(act.total_eta)
    : totalBlocked
      ? "—"
      : durText || (act.executable_now ? "Ready" : "—");

  const totSecNum = finiteNum(act.total_seconds, null);
  const effNowSec =
    nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1000);

  const cmdTemplate = actionPayloadFor(act, false);
  const canPin = Boolean(
    actionPayloadFor(act.kind === "WAIT_RESOURCES" ? { ...act, kind: "BUILD" } : act, true)
  );
  const targetLabel = String(act.target_name || "command").replace(/>/g, "→");

  const actPid = act.planet_id != null ? Math.trunc(Number(act.planet_id)) : 0;
  const actCoordKey = normalizeCoordKey(act.planet_coords);
  const resolvedActPlanetName =
    (actPid > 0 ? planetNameById?.get(actPid) : undefined) ||
    (actCoordKey ? planetNameByCoords?.get(actCoordKey) : undefined) ||
    act.planet_name ||
    "";

  return (
    <tr className={isManual ? "is-manual-queued" : undefined}>
      <td>
        <div className="prio-cell-stack">
          <span className={`prio-tag prio-${prioVal}`}>
            <span className="prio-dot" />
            {prioVal}
          </span>
          {isManual ? (
            <span className="mq-pin-badge tabular">PIN #{act.manual_queue_id}</span>
          ) : null}
        </div>
      </td>

      <td>
        <div className="action-title">{`${act.target_name || "Action"}${lvlStr}`}</div>
        <div className="action-sub">{`${act.kind || "ACTION"} · ${formatPlanetTag(resolvedActPlanetName, act.planet_coords)}`}</div>
      </td>

      <td className="tabular">
        <div>{costStr}</div>
        {missStr !== "0" && missStr !== "—" ? (
          <div className="action-sub">{`Missing: ${missStr}`}</div>
        ) : null}
      </td>

      <td className="col-right tabular">
        <span
          className={`eta-pill${isBlocked ? " is-blocked" : isWaiting ? " is-wait" : ""}`}
          title={bnTag ? `Bottleneck: ${bnTag}` : undefined}
        >
          {waitPillText}
        </span>
      </td>

      <td className="col-right tabular">
        <span
          className={`eta-pill${totalBlocked ? " is-blocked" : isWaiting ? " is-wait" : ""}`}
        >
          {totalEta}
        </span>
        {compact && !totalBlocked && totSecNum !== null && totSecNum > 0 ? (
          <div className="finish-clock-sub tabular">
            {`at ${fmtClockTime(effNowSec + totSecNum, effNowSec)}`}
          </div>
        ) : !compact && durText && durText !== totalEta ? (
          <div className="action-sub">{durText === "Instant" ? "Instant" : `Dur: ${durText}`}</div>
        ) : null}
      </td>

      {!compact ? <td>{act.reason || ""}</td> : null}

      <td className="col-right">
        <div className="row-actions-wrap">
          {cmdTemplate && act.executable_now ? (
            <>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => onDispatch(act, false)}
              >
                Run
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => onDispatch(act, true)}
              >
                Sim
              </button>
            </>
          ) : !isManual ? (
            <span className="action-sub">Waiting</span>
          ) : null}

          {isManual ? (
            <>
              {!compact ? (
                <>
                  <select
                    ref={prioSelectRef}
                    className="mq-prio-select"
                    value={prioVal}
                    aria-label={`Priority for ${targetLabel} (#${qid})`}
                    onChange={(e) => {
                      handledEventsRef.current.add(e.nativeEvent);
                      const nextVal = e.target.value;
                      if (nextVal && nextVal !== prioVal) {
                        onUpdateQueue(qid, { priority: nextVal, move: "top" });
                      }
                    }}
                  >
                    {["P0", "P1", "P2", "P3"].map((pOpt) => (
                      <option key={pOpt} value={pOpt}>
                        {pOpt}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    title="Move to top of priority band"
                    aria-label={`Move ${targetLabel} (#${qid}) to top of ${prioVal}`}
                    onClick={() => onUpdateQueue(qid, { move: "top" })}
                  >
                    Top
                  </button>
                </>
              ) : null}

              <button
                type="button"
                className="btn btn-secondary btn-sm"
                title="Move up in queue"
                aria-label={`Move ${targetLabel} (#${qid}) up in queue`}
                onClick={() => onUpdateQueue(qid, { move: "up" })}
              >
                Up
              </button>

              {!compact ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  title="Move down in queue"
                  aria-label={`Move ${targetLabel} (#${qid}) down in queue`}
                  onClick={() => onUpdateQueue(qid, { move: "down" })}
                >
                  Dn
                </button>
              ) : null}

              <button
                type="button"
                className="btn btn-secondary btn-sm btn-danger-ghost"
                title="Remove from manual queue"
                aria-label={`Remove ${targetLabel} (#${qid}) from manual queue`}
                onClick={() => onCancelQueue(qid)}
              >
                Drop
              </button>
            </>
          ) : canPin ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              title="Pin and prioritize at top of P1 queue"
              aria-label={`Pin ${targetLabel} to P1 queue`}
              onClick={() => onPinQueue(act, "P1")}
            >
              Pin P1
            </button>
          ) : null}
        </div>
      </td>
    </tr>
  );
};
