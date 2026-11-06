import { useSyncExternalStore } from "react";
import { safeFiniteNumber } from "../api/httpClient";
import {
  CONSOLE_STORE_SCHEMA_VERSION,
  CONSOLE_STORE_STORAGE_KEY,
  ConsoleRuntimeState,
  ConsoleStore,
  ConsoleStoreState,
  CreateConsoleStoreOptions,
  DEFAULT_UI_PREFS,
  LEGACY_SESSION_STATE_KEY,
  PERSIST_DEBOUNCE_MS,
  ResetConsoleStoreOptions,
  SOFT_MAX_PERSIST_CHARS,
  UiPrefs,
  VALID_AG_TABS,
} from "./types";
import {
  SERVER_CONSOLE_SNAPSHOT,
  SERVER_RUNTIME_SNAPSHOT,
  buildPersistedPayload,
  createDefaultConsoleStoreState,
  createDefaultRuntimeState,
  isQuotaExceededError,
  isSupportedPageKey,
  normalizePageKey,
  persistConsoleStateToStorage,
  readPersistedConsoleState,
  sanitizePersistedBldCategory,
} from "./persistence";

export {
  CONSOLE_STORE_SCHEMA_VERSION,
  CONSOLE_STORE_STORAGE_KEY,
  DEFAULT_UI_PREFS,
  LEGACY_SESSION_STATE_KEY,
  PERSIST_DEBOUNCE_MS,
  SERVER_CONSOLE_SNAPSHOT,
  SERVER_RUNTIME_SNAPSHOT,
  SOFT_MAX_PERSIST_CHARS,
  VALID_AG_TABS,
  buildPersistedPayload,
  createDefaultConsoleStoreState,
  createDefaultRuntimeState,
  isQuotaExceededError,
  isSupportedPageKey,
  normalizePageKey,
  persistConsoleStateToStorage,
  readPersistedConsoleState,
  sanitizePersistedBldCategory,
};

export type {
  ConsoleRuntimeState,
  ConsoleStore,
  ConsoleStoreState,
  CreateConsoleStoreOptions,
  ResetConsoleStoreOptions,
  UiPrefs,
};

type StateChangeHook = (state: ConsoleStoreState) => void;

export function createConsoleStore(options: CreateConsoleStoreOptions = {}): ConsoleStore {
  const persistEnabled = options.persist ?? false;
  const debounceMs = options.debounceMs ?? PERSIST_DEBOUNCE_MS;
  const maxPersistChars = options.maxPersistChars ?? SOFT_MAX_PERSIST_CHARS;
  const storageKey = options.storageKey || CONSOLE_STORE_STORAGE_KEY;
  const legacyKey = options.legacyKey || LEGACY_SESSION_STATE_KEY;

  const listeners = new Set<() => void>();
  const runtimeListeners = new Set<() => void>();
  let snapshot: ConsoleStoreState = createDefaultConsoleStoreState();
  let runtime: ConsoleRuntimeState = createDefaultRuntimeState();
  let needsHydration = Boolean(persistEnabled);
  let pendingPersist = false;
  let persistTimer: ReturnType<typeof setTimeout> | null = null;
  let unloadBound = false;

  const emit = () => {
    listeners.forEach((listener) => {
      listener();
    });
  };

  const emitRuntime = () => {
    runtimeListeners.forEach((listener) => {
      listener();
    });
  };

  const clearPersistTimer = () => {
    if (persistTimer !== null) {
      clearTimeout(persistTimer);
      persistTimer = null;
    }
  };

  const flushPersistence = (force = false): boolean => {
    clearPersistTimer();
    if (!persistEnabled || typeof window === "undefined") {
      pendingPersist = false;
      return false;
    }
    if (!pendingPersist && !force) {
      return false;
    }
    pendingPersist = false;
    const res = persistConsoleStateToStorage(snapshot, {
      storageKey,
      maxChars: maxPersistChars,
    });
    return res.ok;
  };

  const schedulePersist = () => {
    if (!persistEnabled || typeof window === "undefined") return;
    pendingPersist = true;
    clearPersistTimer();
    if (debounceMs <= 0) {
      flushPersistence(true);
      return;
    }
    persistTimer = setTimeout(() => {
      persistTimer = null;
      flushPersistence(false);
    }, debounceMs);
  };

  const onUnload = () => {
    flushPersistence(false);
  };

  const onVisibilityChange = () => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      flushPersistence(false);
    }
  };

  const bindUnload = () => {
    if (!persistEnabled || unloadBound || typeof window === "undefined") return;
    unloadBound = true;
    window.addEventListener("pagehide", onUnload);
    window.addEventListener("beforeunload", onUnload);
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
  };

  const unbindUnload = () => {
    if (!unloadBound || typeof window === "undefined") return;
    unloadBound = false;
    window.removeEventListener("pagehide", onUnload);
    window.removeEventListener("beforeunload", onUnload);
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  };

  const hydrateSilent = (): ConsoleStoreState => {
    needsHydration = false;
    if (!persistEnabled || typeof window === "undefined") {
      return snapshot;
    }
    snapshot = readPersistedConsoleState(storageKey, legacyKey);
    return snapshot;
  };

  if (options.hydrateOnInit && persistEnabled && typeof window !== "undefined") {
    hydrateSilent();
  }
  if (options.bindUnloadEvents && persistEnabled && typeof window !== "undefined") {
    bindUnload();
  }

  const hydrateIfNeeded = (): ConsoleStoreState => {
    if (!needsHydration) return snapshot;
    return hydrateSilent();
  };

  const rehydrateFromStorage = (): ConsoleStoreState => {
    const prev = snapshot;
    hydrateSilent();
    runtime = createDefaultRuntimeState();
    if (snapshot !== prev) {
      emit();
    }
    emitRuntime();
    return snapshot;
  };

  const setState: ConsoleStore["setState"] = (updater, opts) => {
    const prev = snapshot;
    let next: ConsoleStoreState;
    if (typeof updater === "function") {
      next = updater(prev);
    } else {
      let changed = false;
      for (const key of Object.keys(updater) as Array<keyof ConsoleStoreState>) {
        if (!Object.is(prev[key], updater[key])) {
          changed = true;
          break;
        }
      }
      if (!changed && !opts?.touchHydratedAt) {
        return prev;
      }
      next = { ...prev, ...updater };
    }

    if (opts?.touchHydratedAt) {
      const now = Date.now();
      next = next === prev ? { ...prev, lastHydratedAt: now } : { ...next, lastHydratedAt: now };
    }

    if (Object.is(next, prev)) {
      return prev;
    }

    snapshot = next;
    needsHydration = false;
    if (opts?.persist !== false) {
      schedulePersist();
    }
    emit();
    return snapshot;
  };

  const setRuntime: ConsoleStore["setRuntime"] = (updater) => {
    const prev = runtime;
    let next: ConsoleRuntimeState;
    if (typeof updater === "function") {
      next = updater(prev);
    } else {
      let changed = false;
      for (const key of Object.keys(updater) as Array<keyof ConsoleRuntimeState>) {
        if (!Object.is(prev[key], updater[key])) {
          changed = true;
          break;
        }
      }
      if (!changed) return prev;
      next = { ...prev, ...updater };
    }
    if (Object.is(next, prev)) return prev;
    runtime = next;
    emitRuntime();
    return runtime;
  };

  const setDashboard: ConsoleStore["setDashboard"] = (next) => {
    setState(
      (prev) => {
        const resolved = typeof next === "function" ? next(prev.dashboard) : next;
        if (Object.is(prev.dashboard, resolved)) return prev;
        return {
          ...prev,
          dashboard: resolved,
          lastHydratedAt: resolved ? Date.now() : prev.lastHydratedAt,
        };
      },
      { persist: true }
    );
  };

  const setActivePage: ConsoleStore["setActivePage"] = (rawKey) => {
    const target = normalizePageKey(rawKey);
    setState({ activePage: target }, { persist: true });
    return target;
  };

  const setSelectedPlanetId: ConsoleStore["setSelectedPlanetId"] = (planetId) => {
    const pid = planetId == null ? null : safeFiniteNumber(planetId, null);
    setState({ selectedPlanetId: pid }, { persist: true });
  };

  const setUiPref: ConsoleStore["setUiPref"] = (key, value) => {
    setState(
      (prev) => {
        if (Object.is(prev.uiPrefs[key], value)) return prev;
        return {
          ...prev,
          uiPrefs: {
            ...prev.uiPrefs,
            [key]: value,
          },
        };
      },
      { persist: key !== "sidebarOpen" }
    );
  };

  const setUiPrefs: ConsoleStore["setUiPrefs"] = (next) => {
    setState(
      (prev) => {
        const resolved = typeof next === "function" ? next(prev.uiPrefs) : next;
        if (Object.is(prev.uiPrefs, resolved)) return prev;
        return { ...prev, uiPrefs: resolved };
      },
      { persist: true }
    );
  };

  const setBuildingsEtaData: ConsoleStore["setBuildingsEtaData"] = (data) => {
    setState({ buildingsEtaData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setCombatData: ConsoleStore["setCombatData"] = (data) => {
    setState({ combatData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setActionGraphData: ConsoleStore["setActionGraphData"] = (data) => {
    setState({ actionGraphData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setPlannerJobsStatus: ConsoleStore["setPlannerJobsStatus"] = (status) => {
    setState({ plannerJobsStatus: status }, { persist: true, touchHydratedAt: Boolean(status) });
  };

  const setSectorsData: ConsoleStore["setSectorsData"] = (data) => {
    setState({ sectorsData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setEspData: ConsoleStore["setEspData"] = (data) => {
    setState({ espData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setColonyData: ConsoleStore["setColonyData"] = (data) => {
    setState({ colonyData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setAtkData: ConsoleStore["setAtkData"] = (data) => {
    setState({ atkData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setRecData: ConsoleStore["setRecData"] = (data) => {
    setState({ recData: data }, { persist: true, touchHydratedAt: Boolean(data) });
  };

  const setLastOptimizerSummary: ConsoleStore["setLastOptimizerSummary"] = (summary) => {
    setState(
      { lastOptimizerSummary: summary },
      { persist: true, touchHydratedAt: Boolean(summary) }
    );
  };

  const setNotifications: ConsoleStore["setNotifications"] = (notifications) => {
    setState(
      { notifications },
      { persist: true, touchHydratedAt: notifications.items.length > 0 }
    );
  };

  const setApiOnline: ConsoleStore["setApiOnline"] = (online) => {
    setState({ apiOnline: Boolean(online) }, { persist: true });
  };

  const reset: ConsoleStore["reset"] = (opts) => {
    clearPersistTimer();
    pendingPersist = false;
    if (opts?.clearStorage && typeof window !== "undefined") {
      try {
        window.localStorage?.removeItem(storageKey);
      } catch {
        // Ignore storage errors
      }
      try {
        window.sessionStorage?.removeItem(storageKey);
        window.sessionStorage?.removeItem(legacyKey);
      } catch {
        // Ignore storage errors
      }
    }
    snapshot = createDefaultConsoleStoreState();
    runtime = createDefaultRuntimeState();
    needsHydration = Boolean(persistEnabled);
    emit();
    emitRuntime();
  };

  const destroy = () => {
    clearPersistTimer();
    pendingPersist = false;
    unbindUnload();
    listeners.clear();
    runtimeListeners.clear();
  };

  return {
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    getServerSnapshot: () => SERVER_CONSOLE_SNAPSHOT,
    subscribeRuntime: (listener) => {
      runtimeListeners.add(listener);
      return () => {
        runtimeListeners.delete(listener);
      };
    },
    getRuntimeSnapshot: () => runtime,
    getServerRuntimeSnapshot: () => SERVER_RUNTIME_SNAPSHOT,
    setRuntime,
    hydrateIfNeeded,
    rehydrateFromStorage,
    setState,
    setDashboard,
    setActivePage,
    setSelectedPlanetId,
    setUiPref,
    setUiPrefs,
    setBuildingsEtaData,
    setCombatData,
    setActionGraphData,
    setPlannerJobsStatus,
    setSectorsData,
    setEspData,
    setColonyData,
    setAtkData,
    setRecData,
    setLastOptimizerSummary,
    setNotifications,
    setApiOnline,
    flushPersistence,
    hasPendingPersist: () => pendingPersist,
    reset,
    destroy,
  };
}

let allowPersistWhenInitialFetchDisabled = false;
const resetHooks = new Set<StateChangeHook>();

export function setConsoleStorePersistenceForTests(enabled: boolean): void {
  allowPersistWhenInitialFetchDisabled = Boolean(enabled);
}

export function getConsoleStorePersistenceForTests(): boolean {
  return allowPersistWhenInitialFetchDisabled;
}

export function registerConsoleStoreResetHook(hook: StateChangeHook): () => void {
  resetHooks.add(hook);
  return () => {
    resetHooks.delete(hook);
  };
}

export const consoleStore: ConsoleStore = createConsoleStore({
  persist: true,
  hydrateOnInit: true,
  bindUnloadEvents: true,
});

export function useConsoleStore(): ConsoleStoreState {
  return useSyncExternalStore(
    consoleStore.subscribe,
    consoleStore.getSnapshot,
    consoleStore.getServerSnapshot
  );
}

export function flushConsoleStore(force = false): boolean {
  return consoleStore.flushPersistence(force);
}

export function rehydrateConsoleStore(): ConsoleStoreState {
  const next = consoleStore.rehydrateFromStorage();
  resetHooks.forEach((hook) => hook(next));
  return next;
}

export function resetConsoleStore(opts?: ResetConsoleStoreOptions): void {
  consoleStore.reset(opts);
  const next = consoleStore.getSnapshot();
  resetHooks.forEach((hook) => hook(next));
}
