export const SIM_POPULATE_EVENT = "si:populate-combat-sim";

export interface SimPopulateTarget {
  defense?: Record<string, number>;
  fleet?: Record<string, number>;
  totalLoot?: number;
}

export function dispatchPopulateSimEvent(detail: SimPopulateTarget): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<SimPopulateTarget>(SIM_POPULATE_EVENT, { detail }));
}
