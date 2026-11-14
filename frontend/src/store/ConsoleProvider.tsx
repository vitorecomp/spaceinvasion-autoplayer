import React, { useEffect, useLayoutEffect, useMemo } from "react";
import { registerHttpBridgeCallbacks } from "../api/httpClient";
import { createConsoleActions } from "./actions";
import {
  consoleStore,
  createConsoleStore,
  getConsoleStorePersistenceForTests,
} from "./consoleStore";
import { ConsoleActionsContext, ConsoleStoreInstanceContext } from "./selectors";
import { ConsoleStore } from "./types";

const useClientSyncEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

export interface ConsoleProviderProps {
  children: React.ReactNode;
  disableInitialFetch?: boolean;
  enablePersistence?: boolean;
}

export const ConsoleProvider: React.FC<ConsoleProviderProps> = ({
  children,
  disableInitialFetch = false,
  enablePersistence,
}) => {
  const shouldPersist =
    enablePersistence !== undefined
      ? enablePersistence
      : !disableInitialFetch || getConsoleStorePersistenceForTests();

  const activeStore: ConsoleStore = useMemo(() => {
    if (shouldPersist) {
      consoleStore.hydrateIfNeeded();
      return consoleStore;
    }
    return createConsoleStore({
      persist: false,
      hydrateOnInit: false,
      bindUnloadEvents: false,
    });
  }, [shouldPersist]);

  const actions = useMemo(
    () => createConsoleActions(activeStore, { shouldPersist, disableInitialFetch }),
    [activeStore, shouldPersist, disableInitialFetch]
  );

  useEffect(() => {
    return () => {
      actions.cleanup();
      if (shouldPersist) {
        activeStore.flushPersistence(false);
      }
    };
  }, [actions, activeStore, shouldPersist]);

  useClientSyncEffect(() => {
    registerHttpBridgeCallbacks({
      onStatusChange: actions.setApiStatus,
      onNotifications: actions.syncNotificationsPayload,
    });
  }, [actions]);

  useClientSyncEffect(() => {
    if (disableInitialFetch || typeof window === "undefined") return;

    void actions.checkApiHealthAndHotReload();
    void actions.loadDashboard({ refresh: false, preserveConfig: false });

    const tickTimer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      const curPage = activeStore.getSnapshot().activePage;
      const notifOpen = activeStore.getRuntimeSnapshot().notifCenterOpen;
      if (
        curPage === "dashboard" ||
        curPage === "operations" ||
        (curPage as string) === "fleet-control" ||
        notifOpen
      ) {
        actions.setNowEpochSec(Math.floor(Date.now() / 1000));
      }
    }, 1000);

    const versionTimer = setInterval(() => {
      if (!activeStore.getSnapshot().dashboard?.bot_control?.captcha_blocked) {
        void actions.checkApiHealthAndHotReload();
      }
    }, 1200);

    const dashTimer = setInterval(() => {
      const snap = activeStore.getSnapshot();
      if (
        document.visibilityState === "visible" &&
        snap.apiOnline &&
        !snap.dashboard?.bot_control?.captcha_blocked
      ) {
        void actions.loadDashboard({ refresh: false, preserveConfig: true });
      }
    }, 5000);

    const onOnline = () => {
      if (!activeStore.getSnapshot().dashboard?.bot_control?.captcha_blocked) {
        void actions.checkApiHealthAndHotReload();
      }
    };
    const onVis = () => {
      if (
        document.visibilityState === "visible" &&
        !activeStore.getSnapshot().dashboard?.bot_control?.captcha_blocked
      ) {
        void actions.checkApiHealthAndHotReload();
        void actions.loadDashboard({ refresh: false, preserveConfig: true });
      }
    };
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev && ev.key === "Escape") {
        const rt = activeStore.getRuntimeSnapshot();
        if (rt.notifCenterOpen) {
          actions.closeNotificationCenter();
        } else if (rt.activeHelpNotif !== null) {
          actions.closeHelpPopupOnly();
        }
      }
    };

    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVis);
    document.addEventListener("keydown", onKeyDown);

    return () => {
      clearInterval(tickTimer);
      clearInterval(versionTimer);
      clearInterval(dashTimer);
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVis);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [actions, activeStore, disableInitialFetch]);

  return (
    <ConsoleStoreInstanceContext.Provider value={activeStore}>
      <ConsoleActionsContext.Provider value={actions}>{children}</ConsoleActionsContext.Provider>
    </ConsoleStoreInstanceContext.Provider>
  );
};
