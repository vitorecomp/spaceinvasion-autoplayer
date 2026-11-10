import type { ColonyAttemptCoordStats } from "../../types/api";
import { finiteNum } from "../../utils/formatters";

export interface AttemptOutcome {
  label: string;
  cls: string;
}

/** Map a `colony_attempts.event_type` to a short outcome pill label + badge modifier. */
export function attemptOutcomeFor(eventType: unknown, minFields: number): AttemptOutcome {
  const ev = String(eventType ?? "").trim().toLowerCase();
  if (ev.includes("delet") || ev.includes("abandon") || ev.includes("undersized")) {
    return { label: `DELETED <${minFields}`, cls: "is-loss" };
  }
  if (ev.includes("kept") || ev.includes("keep")) {
    return { label: `KEPT ≥${minFields}`, cls: "is-win" };
  }
  if (
    ev.includes("fail") ||
    ev.includes("reject") ||
    ev.includes("abort") ||
    ev.includes("block") ||
    ev.includes("error")
  ) {
    return { label: "FAILED", cls: "is-loss" };
  }
  if (ev.includes("settl")) {
    return { label: "SETTLED", cls: "is-draw" };
  }
  if (ev.includes("dispatch") || ev.includes("launch") || ev.includes("sent")) {
    return { label: "DISPATCHED", cls: "is-pending" };
  }
  return { label: ev ? ev.toUpperCase().replace(/_/g, " ") : "UNKNOWN", cls: "is-pending" };
}

export function attemptShortTag(stats: ColonyAttemptCoordStats, minFields: number): string {
  const tried = Math.max(0, Math.round(finiteNum(stats.attempts_count, 0)));
  const lastFields = finiteNum(stats.last_fields_rolled, null);
  const outcome = attemptOutcomeFor(stats.last_event_type, minFields);
  const lastBits: string[] = [];
  if (lastFields !== null && lastFields > 0) lastBits.push(`${Math.round(lastFields)}f`);
  if (stats.last_event_type) lastBits.push(outcome.label.split(" ")[0].toLowerCase());
  return lastBits.length > 0 ? `Tried ${tried}x · Last: ${lastBits.join(" ")}` : `Tried ${tried}x`;
}
