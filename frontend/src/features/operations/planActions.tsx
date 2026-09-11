import React, { useEffect, useRef } from "react";
import {
  durationToSeconds,
  prioritySortKey,
  resourcesTotal,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import type { PlannedActionData, QueueJobData } from "../../types/api";
import {
  bottleneckTag,
  finiteNum,
  fmtClockTime,
  fmtHms,
  formatResourcesInline,
} from "../../utils/formatters";

export type PlanActionSortKey =
  | "priority"
  | "action"
  | "costMissing"
  | "waitEta"
  | "totalEta"
  | "reason";

export const PLAN_ACTION_SORT_COLUMNS: SortColumnMap<PlannedActionData, PlanActionSortKey> = {
  priority: (act) => prioritySortKey(act.priority),
  action: (act) =>
    `${act.target_name || act.kind || ""}${act.target_level != null ? ` ${act.target_level}` : ""}`,
  costMissing: (act) => resourcesTotal(act.cost) ?? resourcesTotal(act.missing),
  waitEta: (act) => {
    const rawWait =
      act.wait_eta !== undefined && act.wait_eta !== null ? String(act.wait_eta).trim() : "";
    if (rawWait === "—" || act.wait_hours === null) return null;
    const wh = finiteNum(act.wait_hours, null);
    if (wh !== null && (wh > 0 || act.executable_now || rawWait !== "")) {
      return wh * 3600;
    }
    if (rawWait !== "") return durationToSeconds(rawWait);
    return act.executable_now ? 0 : null;
  },
  totalEta: (act) => {
    const rawWait =
      act.wait_eta !== undefined && act.wait_eta !== null ? String(act.wait_eta).trim() : "";
    const rawTotal =
      act.total_eta !== undefined && act.total_eta !== null ? String(act.total_eta).trim() : "";
    if (
      rawWait === "—" ||
      rawTotal === "—" ||
      act.wait_hours === null ||
      act.total_hours === null
    ) {
      return null;
    }
    const ts = finiteNum(act.total_seconds, null);
    if (ts !== null) return ts;
    const th = finiteNum(act.total_hours, null);
    if (th !== null) return th * 3600;
    if (rawTotal !== "") return durationToSeconds(rawTotal);
    if (act.duration_text) return durationToSeconds(act.duration_text);
    return act.executable_now ? 0 : null;
  },
  reason: (act) => String(act.reason || "").trim() || null,
};

export const MQ_BUILDINGS_CATALOG = [
  { id: 1, name: "Pig iron mine" },
  { id: 2, name: "Blast furnace" },
  { id: 3, name: "Kryptonite mine" },
  { id: 4, name: "Spice mine" },
  { id: 5, name: "Fusion plant" },
  { id: 6, name: "Thermosolar plant" },
  { id: 7, name: "Development center" },
  { id: 8, name: "Research lab" },
  { id: 9, name: "Weapons factory" },
  { id: 10, name: "Fleet base" },
  { id: 11, name: "Pig iron warehouse" },
  { id: 12, name: "Metal warehouse" },
  { id: 13, name: "Kryptonite warehouse" },
  { id: 14, name: "Spice warehouse" },
  { id: 15, name: "Particle cannon" },
  { id: 16, name: "Particle shield" },
  { id: 17, name: "Microsystem accelerator" },
  { id: 18, name: "Teleporter" },
  { id: 19, name: "Galaxy scanner" },
  { id: 20, name: "Tunnel system" },
  { id: 21, name: "Quantum Array" },
] as const;

export const MQ_RESEARCH_CATALOG = [
  { id: 1, name: "Mining technology" },
  { id: 2, name: "Energy technology" },
  { id: 3, name: "Engine research" },
  { id: 4, name: "Rocket engine" },
  { id: 5, name: "Diffusion engine" },
  { id: 6, name: "Warp engine" },
  { id: 7, name: "Spy technology" },
  { id: 8, name: "Weapons technology" },
  { id: 9, name: "Shield technology" },
  { id: 10, name: "Defense systems" },
  { id: 11, name: "Targeting systems" },
  { id: 12, name: "Particle analyzer" },
  { id: 13, name: "Graviton technology" },
  { id: 14, name: "Teleportation" },
  { id: 15, name: "Black hole research" },
] as const;

export const MQ_SHIPS_CATALOG = [
  { id: 1, name: "Small transport", key: "s1" },
  { id: 2, name: "Large transport", key: "s2" },
  { id: 3, name: "Transmitter", key: "s3" },
  { id: 4, name: "Small fighter", key: "s4" },
  { id: 5, name: "Warship", key: "s5" },
  { id: 6, name: "Starcruiser", key: "s6" },
  { id: 7, name: "Phoenix", key: "s7" },
  { id: 8, name: "Colony ship", key: "s8" },
  { id: 9, name: "Solar cells", key: "s9" },
  { id: 10, name: "Spy probe", key: "s10" },
  { id: 11, name: "Recycler", key: "s11" },
  { id: 13, name: "Battleship", key: "s13" },
  { id: 14, name: "Stealth bomber", key: "s14" },
  { id: 15, name: "Destroyer", key: "s15" },
  { id: 16, name: "Imperial starbase", key: "s16" },
] as const;

export const DASH_FLEET_KINDS = new Set([
  "BUILD_SHIP",
  "SHIP",
  "BUILD_DEFENSE",
  "DEFENSE",
  "SPY",
  "TRANSPORT",
  "STATION",
  "ATTACK",
  "RECYCLE",
  "COLONIZE",
  "SCAN_GALAXY",
  "ESCAPE_FLIGHT",
  "ALERT_HOSTILE",
]);

export function dashboardQueueDomainForAction(
  act: PlannedActionData | null | undefined
): "building" | "fleet" | "research" {
  const kind = String(act?.kind || "").toUpperCase();
  if (kind === "RESEARCH") return "research";
  if (DASH_FLEET_KINDS.has(kind)) return "fleet";
  return "building";
}

export function dashboardQueueDomainForJob(
  job: QueueJobData | null | undefined
): "building" | "fleet" | "research" {
  const cat = String(job?.category || "").toLowerCase();
  if (cat === "research") return "research";
  if (cat === "fleet" || cat === "shipyard" || cat === "defense") return "fleet";
  return "building";
}

export function cleanQueueJobTitle(job: QueueJobData | null | undefined): string {
  const rawName = String(job?.name || "Active job")
    .replace(/^(?:cancel|abort)\s+/i, "")
    .replace(/\s*\(\s*Level\s+\d+\s*\)\s*$/i, "")
    .trim();
  const lvl = finiteNum(job?.target_level, null);
  if (lvl !== null && lvl > 0) {
    return `${rawName} → L${lvl}`;
  }
  return rawName || "Active job";
}

export function actionPayloadFor(
  action: PlannedActionData | null | undefined,
  dryRun: boolean
): Record<string, any> | null {
  if (!action) return null;
  if (action.kind === "CLAIM_TUTORIAL") {
    return { command: "claim_tutorial", dry_run: dryRun };
  }
  if (action.kind === "BUILD" && action.target_id != null) {
    return {
      command: "build",
      building_id: action.target_id,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "DEMOLISH_BUILDING" && action.target_id != null) {
    return {
      command: "demolish_building",
      building_id: action.target_id,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "RESEARCH" && action.target_id != null) {
    return {
      command: "research",
      research_id: action.target_id,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "BUILD_SHIP" && action.target_id != null) {
    return {
      command: "build_ship",
      ship_id: action.target_id,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "THROTTLE_BUILDING" && action.target_id != null) {
    return {
      command: "throttle_building",
      building_id: action.target_id,
      percentage: action.target_level == null ? 0 : finiteNum(action.target_level, 0),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "ESCAPE_FLIGHT") {
    return { command: "fleetsave_plan", planet_id: action.planet_id };
  }
  if (action.kind === "SCAN_GALAXY") {
    const m = /(\d{1,2}):(\d{1,3})/.exec(action.target_name || action.planet_coords || "");
    return {
      command: "scan_galaxy",
      galaxy: m ? Number(m[1]) : undefined,
      system: m ? Number(m[2]) : undefined,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "ATTACK") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "attack",
      target_coords: m ? m[1] : action.target_name,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "SPY") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "spy",
      target_coords: m ? m[1] : action.target_name,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "RECYCLE") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "recycle",
      target_coords: m ? m[1] : action.target_name,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "TRANSPORT") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    const c = action.cost || {};
    return {
      command: "transport",
      target_coords: m ? m[1] : action.target_name,
      dest_planet_id: action.target_id ?? undefined,
      planet_id: action.planet_id,
      resources: {
        pig_iron: Math.max(0, Math.round(finiteNum((c as any).pig_iron, 0))),
        metal: Math.max(0, Math.round(finiteNum((c as any).metal, 0))),
        kryptonite: Math.max(0, Math.round(finiteNum((c as any).kryptonite, 0))),
        spice: Math.max(0, Math.round(finiteNum((c as any).spice, 0))),
      },
      dry_run: dryRun,
    };
  }
  if (action.kind === "STATION") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "station",
      target_coords: m ? m[1] : action.target_name,
      dest_planet_id: action.target_id ?? undefined,
      planet_id: action.planet_id,
      ships_spec: action.target_name || action.reason || "",
      dry_run: dryRun,
    };
  }
  if (action.kind === "COLONIZE") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "colonize",
      target_coords: m ? m[1] : action.target_name,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  return null;
}

export function buildPinPayloadFromAction(
  act: PlannedActionData,
  priority = "P1"
): Record<string, any> {
  const normKind = act.kind === "WAIT_RESOURCES" ? "BUILD" : String(act.kind || "").toUpperCase();
  const mCoords = /(\d{1,2}:\d{1,3}(?::\d{1,2})?)/.exec(act.target_name || "");
  return {
    kind: normKind,
    priority,
    position: "top",
    planet_id: act.planet_id || 0,
    target_id: act.target_id ?? undefined,
    target_name: act.target_name || "",
    target_coords: mCoords ? mCoords[1] : undefined,
    target_level: act.target_level ?? undefined,
    deduplicate_pending: true,
  };
}

export interface PlanActionRowProps {
  action: PlannedActionData;
  compact?: boolean;
  nowEpochSec?: number;
  onDispatch: (act: PlannedActionData, dryRun: boolean) => void;
  onUpdateQueue: (queueId: number, patch: Record<string, any>) => void;
  onCancelQueue: (queueId: number) => void;
  onPinQueue: (act: PlannedActionData, priority?: string) => void;
}

export const PlanActionRow: React.FC<PlanActionRowProps> = ({
  action: act,
  compact = false,
  nowEpochSec,
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
        <div className="action-sub">{`${act.kind || "ACTION"} · [${act.planet_coords || "—"}]`}</div>
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

export interface ActiveExecJobRowProps {
  job: QueueJobData;
  nowEpochSec?: number;
}

export const ActiveExecJobRow: React.FC<ActiveExecJobRowProps> = ({ job, nowEpochSec }) => {
  const slot = Math.max(1, Math.trunc(finiteNum(job?.queue_slot, 1)));
  const detailText =
    String(job?.detail || "").trim() || (slot > 1 ? `In-game queue #${slot}` : "Executing now");
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
  let liveLabel = "Executing";
  if (slot > 1) {
    liveLabel = `Queued #${slot}`;
  } else if (detLow.includes("returning")) {
    liveLabel = "Returning";
  } else if (detLow.includes("outbound") || detLow.includes("flight")) {
    liveLabel = "In flight";
  }

  return (
    <tr className={slot > 1 ? "dash-exec-row is-queued-slot" : "dash-exec-row"}>
      <td>
        <span className={slot > 1 ? "prio-badge prio-game-q tabular" : "prio-badge prio-exec tabular"}>
          {slot > 1 ? `GAME #${slot}` : "EXEC"}
        </span>
      </td>
      <td>
        <div className="action-title">{cleanQueueJobTitle(job)}</div>
      </td>
      <td>
        <span className="action-sub">{detailText}</span>
      </td>
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
      <td className="col-right">
        <span className={slot > 1 ? "exec-live-label is-slot" : "exec-live-label"}>
          {liveLabel}
        </span>
      </td>
    </tr>
  );
};
