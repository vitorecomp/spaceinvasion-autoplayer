import { createContext, useContext, useMemo, useRef, useSyncExternalStore } from "react";
import {
  DashboardPayload,
  FeedbackState,
  NotificationsState,
  PageKey,
  PlanetData,
  PlannerJobsStatusPayload,
} from "../types/api";
import { consoleStore } from "./consoleStore";
import { selectActivePlanet } from "./fallbackActionGraph";
import {
  ConsoleActions,
  ConsoleRuntimeState,
  ConsoleStore,
  ConsoleStoreState,
  UiPrefs,
} from "./types";

export const ConsoleStoreInstanceContext = createContext<ConsoleStore>(consoleStore);
export const ConsoleActionsContext = createContext<ConsoleActions | null>(null);

export function shallowEqual<T>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || a === null || typeof b !== "object" || b === null) {
    return false;
  }
  const keysA = Object.keys(a as Record<string, unknown>);
  const keysB = Object.keys(b as Record<string, unknown>);
  if (keysA.length !== keysB.length) return false;
  for (const key of keysA) {
    if (
      !Object.prototype.hasOwnProperty.call(b, key) ||
      !Object.is((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key])
    ) {
      return false;
    }
  }
  return true;
}

export function useConsoleStoreInstance(): ConsoleStore {
  return useContext(ConsoleStoreInstanceContext);
}

export function useConsoleActions(): ConsoleActions {
  const actions = useContext(ConsoleActionsContext);
  if (!actions) {
    throw new Error("useConsoleActions must be used inside <ConsoleProvider>");
  }
  return actions;
}

export function usePersistedSelector<T>(
  selector: (state: ConsoleStoreState) => T,
  isEqual: (a: T, b: T) => boolean = Object.is
): T {
  const store = useConsoleStoreInstance();
  const clientCacheRef = useRef<{ has: boolean; value: T }>({ has: false, value: undefined as T });
  const serverCacheRef = useRef<{ has: boolean; value: T }>({ has: false, value: undefined as T });

  const getSnapshot = () => {
    const next = selector(store.getSnapshot());
    if (clientCacheRef.current.has && isEqual(clientCacheRef.current.value, next)) {
      return clientCacheRef.current.value;
    }
    clientCacheRef.current = { has: true, value: next };
    return next;
  };

  const getServerSnapshot = () => {
    const next = selector(store.getServerSnapshot());
    if (serverCacheRef.current.has && isEqual(serverCacheRef.current.value, next)) {
      return serverCacheRef.current.value;
    }
    serverCacheRef.current = { has: true, value: next };
    return next;
  };

  return useSyncExternalStore(store.subscribe, getSnapshot, getServerSnapshot);
}

export function useRuntimeSelector<T>(
  selector: (runtime: ConsoleRuntimeState) => T,
  isEqual: (a: T, b: T) => boolean = Object.is
): T {
  const store = useConsoleStoreInstance();
  const clientCacheRef = useRef<{ has: boolean; value: T }>({ has: false, value: undefined as T });
  const serverCacheRef = useRef<{ has: boolean; value: T }>({ has: false, value: undefined as T });

  const getSnapshot = () => {
    const next = selector(store.getRuntimeSnapshot());
    if (clientCacheRef.current.has && isEqual(clientCacheRef.current.value, next)) {
      return clientCacheRef.current.value;
    }
    clientCacheRef.current = { has: true, value: next };
    return next;
  };

  const getServerSnapshot = () => {
    const next = selector(store.getServerRuntimeSnapshot());
    if (serverCacheRef.current.has && isEqual(serverCacheRef.current.value, next)) {
      return serverCacheRef.current.value;
    }
    serverCacheRef.current = { has: true, value: next };
    return next;
  };

  return useSyncExternalStore(store.subscribeRuntime, getSnapshot, getServerSnapshot);
}

/* ==================== GRANULAR DOMAIN SLICE SELECTORS ==================== */

export function useActivePage(): PageKey {
  return usePersistedSelector((s) => s.activePage);
}

export function useSelectedPlanetId(): number | null {
  return usePersistedSelector((s) => s.selectedPlanetId);
}

export function useActivePlanet(): PlanetData | null {
  return usePersistedSelector((s) => selectActivePlanet(s.dashboard, s.selectedPlanetId));
}

export function useUiPrefs(): UiPrefs {
  return usePersistedSelector((s) => s.uiPrefs);
}

export function useUiPref<K extends keyof UiPrefs>(key: K): UiPrefs[K] {
  return usePersistedSelector((s) => s.uiPrefs[key]);
}

export function useDashboard(): DashboardPayload | null {
  return usePersistedSelector((s) => s.dashboard);
}

export function useApiOnline(): boolean {
  return usePersistedSelector((s) => s.apiOnline);
}

export function useApiStatus(): {
  apiOnline: boolean;
  chromeOnline: boolean;
  reconnectAttempts: number;
} {
  const apiOnline = usePersistedSelector((s) => s.apiOnline);
  const chromeOnline = usePersistedSelector((s) => Boolean(s.dashboard?.health?.in_game));
  const reconnectAttempts = useRuntimeSelector((r) => r.reconnectAttempts);
  return useMemo(
    () => ({ apiOnline, chromeOnline, reconnectAttempts }),
    [apiOnline, chromeOnline, reconnectAttempts]
  );
}

export function useHydrationMeta(): {
  isHydratedFromCache: boolean;
  lastHydratedAt: number | null;
} {
  return usePersistedSelector(
    (s) => ({
      isHydratedFromCache: s.isHydratedFromCache,
      lastHydratedAt: s.lastHydratedAt,
    }),
    shallowEqual
  );
}

export function useNowEpochSec(): number {
  return useRuntimeSelector((r) => r.nowEpochSec);
}

export function useFeedback(): FeedbackState | null {
  return useRuntimeSelector((r) => r.feedback);
}

export function useGaCalc(): { gaCalcCount: number; gaCalculating: boolean } {
  const gaCalcCount = useRuntimeSelector((r) => r.gaCalcCount);
  return useMemo(
    () => ({ gaCalcCount, gaCalculating: gaCalcCount > 0 }),
    [gaCalcCount]
  );
}

export function useBuildingsEtaData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.buildingsEtaData);
}

export function useCombatData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.combatData);
}

export function useSectorsData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.sectorsData);
}

export function useEspData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.espData);
}

export function useColonyData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.colonyData);
}

export function useAtkData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.atkData);
}

export function useRecData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.recData);
}

export function useActionGraphData(): Record<string, any> | null {
  return usePersistedSelector((s) => s.actionGraphData);
}

export function usePlannerJobsStatus(): PlannerJobsStatusPayload | null {
  return usePersistedSelector((s) => s.plannerJobsStatus);
}

export function useLastOptimizerSummary(): Record<string, any> | null {
  return usePersistedSelector((s) => s.lastOptimizerSummary);
}

export function useNotifications(): NotificationsState {
  return usePersistedSelector((s) => s.notifications);
}

export function useNotifCenterOpen(): boolean {
  return useRuntimeSelector((r) => r.notifCenterOpen);
}

export function useActiveHelpNotif(): Record<string, any> | null {
  return useRuntimeSelector((r) => r.activeHelpNotif);
}

export function useAgSimState(): {
  agSimDirty: boolean;
  agSimRunning: number;
  agHydrationTick: number;
} {
  return useRuntimeSelector(
    (r) => ({
      agSimDirty: r.agSimDirty,
      agSimRunning: r.agSimRunning,
      agHydrationTick: r.agHydrationTick,
    }),
    shallowEqual
  );
}

export function useConfigSyncState(): {
  configDirty: boolean;
  configSaving: number;
  configHydrationTick: number;
} {
  return useRuntimeSelector(
    (r) => ({
      configDirty: r.configDirty,
      configSaving: r.configSaving,
      configHydrationTick: r.configHydrationTick,
    }),
    shallowEqual
  );
}
