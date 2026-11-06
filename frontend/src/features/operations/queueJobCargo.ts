import type { QueueJobData, ResourceBundle } from "../../types/api";
import { finiteNum, formatResourcesInline } from "../../utils/formatters";

export type QueueJobCargoVariant = "expected" | "delivered" | "loot" | "harvest" | "cargo";

export interface QueueJobCargoDisplay {
  label: "EXPECTED" | "DELIVERED" | "Loot" | "Harvest" | "Cargo";
  variant: QueueJobCargoVariant;
  inline: string;
  total: number;
  multiRes: boolean;
  isEmpty: boolean;
  delivered: boolean;
}

function summarizeResourceBundle(bundle: ResourceBundle | null | undefined): {
  hasObject: boolean;
  pig: number;
  met: number;
  kry: number;
  spi: number;
  total: number;
  nonZeroCount: number;
} {
  if (!bundle || typeof bundle !== "object") {
    return { hasObject: false, pig: 0, met: 0, kry: 0, spi: 0, total: 0, nonZeroCount: 0 };
  }
  const pig = Math.max(0, Math.round(finiteNum(bundle.pig_iron, 0)));
  const met = Math.max(0, Math.round(finiteNum(bundle.metal, 0)));
  const kry = Math.max(0, Math.round(finiteNum(bundle.kryptonite, 0)));
  const spi = Math.max(0, Math.round(finiteNum(bundle.spice, 0)));
  const total = pig + met + kry + spi;
  const nonZeroCount = [pig, met, kry, spi].filter((v) => v > 0).length;
  return { hasObject: true, pig, met, kry, spi, total, nonZeroCount };
}

export function formatQueueJobCargo(
  job: QueueJobData | null | undefined
): QueueJobCargoDisplay | null {
  if (!job) return null;

  const nameLow = String(job.name || "").toLowerCase();
  const detLow = String(job.detail || "").toLowerCase();
  const isReturning =
    Boolean(job.returning) || detLow.includes("returning") || nameLow.includes("↩");
  const isAttack = nameLow.includes("attack");
  const isRecycle = nameLow.includes("recycle");
  const isTransport =
    !isAttack &&
    !isRecycle &&
    (nameLow.includes("transport") || nameLow.includes("transfer"));
  const isDelivered = Boolean(job.delivered) || (isReturning && isTransport);

  const cargoSummary = summarizeResourceBundle(job.cargo);
  const expectedSummary = summarizeResourceBundle(job.expected_cargo);
  const deliveredSummary = summarizeResourceBundle(job.delivered_cargo);

  // 1. Returning transport after delivery -> DELIVERED flag + delivered amounts
  if (isDelivered) {
    if (deliveredSummary.total > 0) {
      return {
        label: "DELIVERED",
        variant: "delivered",
        inline: formatResourcesInline({
          pig_iron: deliveredSummary.pig,
          metal: deliveredSummary.met,
          kryptonite: deliveredSummary.kry,
          spice: deliveredSummary.spi,
        }),
        total: deliveredSummary.total,
        multiRes: deliveredSummary.nonZeroCount > 1,
        isEmpty: false,
        delivered: true,
      };
    }
    return {
      label: "DELIVERED",
      variant: "delivered",
      inline: "Cargo unloaded",
      total: 0,
      multiRes: false,
      isEmpty: false,
      delivered: true,
    };
  }

  // 2. Outbound Attack / Recycle / Transport -> EXPECTED flag + expected resources
  if (!isReturning && (isAttack || isRecycle || isTransport)) {
    const expSource =
      expectedSummary.total > 0
        ? expectedSummary
        : isTransport && cargoSummary.total > 0
          ? cargoSummary
          : expectedSummary;
    if (expSource.total > 0) {
      return {
        label: "EXPECTED",
        variant: "expected",
        inline: formatResourcesInline({
          pig_iron: expSource.pig,
          metal: expSource.met,
          kryptonite: expSource.kry,
          spice: expSource.spi,
        }),
        total: expSource.total,
        multiRes: expSource.nonZeroCount > 1,
        isEmpty: false,
        delivered: false,
      };
    }
    if (expectedSummary.hasObject) {
      return {
        label: "EXPECTED",
        variant: "expected",
        inline: "0 (none)",
        total: 0,
        multiRes: false,
        isEmpty: true,
        delivered: false,
      };
    }
  }

  // 3. Returning Attack (Loot) / Recycle (Harvest) or generic cargo
  if (!cargoSummary.hasObject) return null;
  const label: "Loot" | "Harvest" | "Cargo" =
    isReturning && isAttack ? "Loot" : isReturning && isRecycle ? "Harvest" : "Cargo";
  const variant: QueueJobCargoVariant =
    label === "Loot" ? "loot" : label === "Harvest" ? "harvest" : "cargo";

  if (cargoSummary.total > 0) {
    return {
      label,
      variant,
      inline: formatResourcesInline({
        pig_iron: cargoSummary.pig,
        metal: cargoSummary.met,
        kryptonite: cargoSummary.kry,
        spice: cargoSummary.spi,
      }),
      total: cargoSummary.total,
      multiRes: cargoSummary.nonZeroCount > 1,
      isEmpty: false,
      delivered: false,
    };
  }
  if (isReturning && (isAttack || isRecycle)) {
    return {
      label,
      variant,
      inline: "0 (empty)",
      total: 0,
      multiRes: false,
      isEmpty: true,
      delivered: false,
    };
  }
  return null;
}
