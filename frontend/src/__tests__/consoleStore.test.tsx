import React from "react";
import { renderToString } from "react-dom/server";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { EspionageView } from "../features/espionage";
import {
  CONSOLE_STORE_STORAGE_KEY,
  ConsoleProvider,
  LEGACY_SESSION_STATE_KEY,
  buildDashboardPollUrl,
  buildPersistedPayload,
  consoleStore,
  createConsoleStore,
  flushConsoleStore,
  mergeDashboardDeltaResponse,
  persistConsoleStateToStorage,
  readPersistedConsoleState,
  rehydrateConsoleStore,
  resetConsoleStore,
  setConsoleStorePersistenceForTests,
  useActivePage,
  useApiOnline,
  useConsoleActions,
  useDashboard,
  useEspData,
  useHydrationMeta,
  useSelectedPlanetId,
  useUiPrefs,
} from "../store";
import { DashboardPayload } from "../types/api";

const SAMPLE_DASHBOARD: DashboardPayload = {
  ok: true,
  planner_mode: "priority",
  effective_planner_mode: "priority",
  health: {
    ok: true,
    in_game: true,
    debug_port_open: true,
    debug_port: 9222,
  },
  config: {
    planner_mode: "priority",
    playstyle: "fleeter",
    max_planets: 11,
  },
  state: {
    universe_id: 31,
    user_id: 103603,
    nickname: "ventkx86",
    active_planet_id: 12476,
    planets: [
      {
        planet_id: 12476,
        name: "Homeworld",
        coords: "02:380:12",
        planet_type: "normal",
        fields_used: 90,
        fields_max: 240,
        resources: { pig_iron: 12000, metal: 6000, kryptonite: 4000, spice: 1500 },
        storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
        energy: { produced: 800, used: 500, net: 300 },
      },
      {
        planet_id: 12499,
        name: "Colony Beta",
        coords: "02:381:05",
        planet_type: "normal",
        fields_used: 40,
        fields_max: 320,
        resources: { pig_iron: 5000, metal: 2000, kryptonite: 1200, spice: 400 },
      },
    ],
    researches: { r1: 8, r2: 6 },
    active_queues: [],
  },
  plan: [
    {
      priority: "P2",
      kind: "BUILD",
      planet_id: 12476,
      planet_coords: "02:380:12",
      target_id: 1,
      target_name: "Pig iron mine",
      target_level: 10,
      executable_now: true,
    },
  ],
  buildings_eta: {
    planet: { planet_id: 12476, coords: "02:380:12", building_time_divisor: 8 },
    buildings: [{ key: "b1", building_id: 1, name: "Pig iron mine", current_level: 9 }],
    researches: [{ key: "r1", research_id: 1, name: "Mining technology", current_level: 8 }],
  },
  logs: {
    commander: "Commander running normally",
    game_api: "API ready",
  },
};

const SAMPLE_ESP_DATA = {
  ok: true,
  targets: [
    {
      coords: "02:380:04",
      planet_name: "IdleTarget",
      owner: "Sleepy",
      spice_eq: 42000,
    },
  ],
  reports: [
    {
      id: 77,
      coords: "02:380:04",
      planet_name: "IdleTarget",
      spice_eq: 42000,
    },
  ],
};

describe("Persistent Console Store (useSyncExternalStore + localStorage)", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
    setConsoleStorePersistenceForTests(false);
    resetConsoleStore({ clearStorage: true });
    document.documentElement.classList.remove("si-hydrating");
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    setConsoleStorePersistenceForTests(false);
    resetConsoleStore({ clearStorage: true });
    window.history.replaceState(null, "", "/");
    document.documentElement.classList.remove("si-hydrating");
  });

  it("persists UI + server data across simulated F5 reload and renders hydrated data on frame 0 without null flash", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      const u = String(url);
      if (u.startsWith("/ui-version")) {
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      }
      if (u.startsWith("/dashboard")) {
        return { ok: true, status: 200, text: async () => JSON.stringify(SAMPLE_DASHBOARD) };
      }
      const body = opts?.body ? JSON.parse(String(opts.body)) : {};
      if (body.command === "espionage_intel") {
        return { ok: true, status: 200, text: async () => JSON.stringify(SAMPLE_ESP_DATA) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;
    let dashRef: ReturnType<typeof useDashboard> = null;
    let espRef: ReturnType<typeof useEspData> = null;
    const SessionDriver: React.FC = () => {
      const actions = useConsoleActions();
      const activePage = useActivePage();
      dashRef = useDashboard();
      espRef = useEspData();
      actionsRef = actions;
      return <div data-testid="active-page">{activePage}</div>;
    };

    // 1. Initial live session: load dashboard, navigate to espionage, adjust planet & prefs
    const firstMount = render(
      <ConsoleProvider>
        <SessionDriver />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(dashRef?.state?.nickname).toBe("ventkx86");
    });

    await act(async () => {
      actionsRef!.setActivePage("espionage");
      actionsRef!.setSelectedPlanetId(12499);
      actionsRef!.setUiPref("espTab", "reports");
      actionsRef!.setUiPref("resTab", "drive");
      actionsRef!.setUiPref("selectedResearchKey", "r7");
      actionsRef!.setUiPref("dashQueuePlanetScope", "12499");
    });

    await waitFor(() => {
      expect(espRef?.targets?.[0]?.coords).toBe("02:380:04");
    });

    // Trigger beforeunload (as browser F5 does) -> flushes immediately to localStorage
    window.dispatchEvent(new Event("beforeunload"));
    const rawSaved = localStorage.getItem(CONSOLE_STORE_STORAGE_KEY);
    expect(rawSaved).toBeTruthy();

    // Unmount and wipe in-memory store (simulating fresh JS runtime after F5!)
    firstMount.unmount();
    resetConsoleStore({ clearStorage: false });
    expect(consoleStore.getSnapshot().dashboard).toBeNull();
    expect(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)).toBeTruthy();

    // 2. Remount with NEVER-RESOLVING fetches so we prove frame-0 comes purely from sync hydration
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {}))
    );

    const frameLog: Array<{
      activePage: string;
      selectedPlanetId: number | null;
      hasDashboard: boolean;
      nickname: string | null;
      espCount: number;
      espTab: string;
      resTab: string;
      selectedResearchKey: string;
      dashScope: string;
      apiOnline: boolean;
      isHydratedFromCache: boolean | undefined;
    }> = [];

    const FirstFrameProbe: React.FC = () => {
      const activePage = useActivePage();
      const selectedPlanetId = useSelectedPlanetId();
      const dashboard = useDashboard();
      const espData = useEspData();
      const uiPrefs = useUiPrefs();
      const apiOnline = useApiOnline();
      const { isHydratedFromCache } = useHydrationMeta();
      frameLog.push({
        activePage,
        selectedPlanetId,
        hasDashboard: dashboard !== null,
        nickname: dashboard?.state?.nickname ?? null,
        espCount: espData?.targets?.length ?? 0,
        espTab: uiPrefs.espTab,
        resTab: uiPrefs.resTab,
        selectedResearchKey: uiPrefs.selectedResearchKey,
        dashScope: uiPrefs.dashQueuePlanetScope,
        apiOnline,
        isHydratedFromCache,
      });
      return <div data-testid="hydrated-nick">{dashboard?.state?.nickname || "empty"}</div>;
    };

    const secondMount = render(
      <ConsoleProvider>
        <FirstFrameProbe />
      </ConsoleProvider>
    );

    // Frame 0 MUST already have full hydrated state with ZERO null/dashboard flash!
    expect(frameLog.length).toBeGreaterThanOrEqual(1);
    expect(frameLog[0]).toEqual({
      activePage: "espionage",
      selectedPlanetId: 12499,
      hasDashboard: true,
      nickname: "ventkx86",
      espCount: 1,
      espTab: "reports",
      resTab: "drive",
      selectedResearchKey: "r7",
      dashScope: "12499",
      apiOnline: true,
      isHydratedFromCache: true,
    });
    expect(secondMount.getByTestId("hydrated-nick").textContent).toBe("ventkx86");

    // Store snapshot stays synchronized cleanly without legacy window.state globals
    const snap = consoleStore.getSnapshot();
    expect(snap.activePage).toBe("espionage");
    expect(snap.dashboard?.state?.nickname).toBe("ventkx86");
    expect(snap.uiPrefs.resTab).toBe("drive");
    expect(snap.uiPrefs.selectedResearchKey).toBe("r7");
    expect(snap.isHydratedFromCache).toBe(true);
  });

  it("stale-while-revalidate keeps cached dashboard & tab data intact during offline/failed polls and patches in-place when fresh data arrives", async () => {
    // Seed persisted state in localStorage
    const store = createConsoleStore({ persist: true, debounceMs: 0 });
    store.setDashboard(SAMPLE_DASHBOARD);
    store.setEspData(SAMPLE_ESP_DATA);
    store.setActivePage("espionage");
    store.setApiOnline(true);
    store.flushPersistence(true);
    store.destroy();
    resetConsoleStore({ clearStorage: false });

    let shouldFail = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (shouldFail) {
          throw new Error("connection refused");
        }
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ...SAMPLE_DASHBOARD,
                state: { ...SAMPLE_DASHBOARD.state, nickname: "updated-commander" },
              }),
          };
        }
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({
              ok: true,
              targets: [{ coords: "02:380:09", planet_name: "FreshTarget", spice_eq: 99000 }],
              reports: [],
            }),
        };
      })
    );

    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;
    let apiOnlineRef = true;
    let dashRef: ReturnType<typeof useDashboard> = null;
    let espRef: ReturnType<typeof useEspData> = null;
    const Probe: React.FC = () => {
      actionsRef = useConsoleActions();
      apiOnlineRef = useApiOnline();
      dashRef = useDashboard();
      espRef = useEspData();
      return null;
    };

    render(
      <ConsoleProvider>
        <Probe />
      </ConsoleProvider>
    );

    // Offline failure marks apiOnline false but MUST NOT clear cached dashboard or espData
    await waitFor(() => {
      expect(apiOnlineRef).toBe(false);
    });
    expect(dashRef?.state?.nickname).toBe("ventkx86");
    expect(espRef?.targets?.[0]?.coords).toBe("02:380:04");

    // Once API recovers, fresh data patches smoothly in-place
    shouldFail = false;
    await act(async () => {
      await actionsRef!.loadDashboard({ refresh: false, force: true });
    });

    await waitFor(() => {
      expect(dashRef?.state?.nickname).toBe("updated-commander");
      expect(espRef?.targets?.[0]?.coords).toBe("02:380:09");
      expect(apiOnlineRef).toBe(true);
    });
  });

  it("coalesces rapid updates via debounce and flushes immediately on beforeunload & pagehide", () => {
    vi.useFakeTimers();
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    const store = createConsoleStore({
      persist: true,
      hydrateOnInit: false,
      bindUnloadEvents: true,
      debounceMs: 120,
    });

    // Rapid burst of mutations should NOT write synchronously
    store.setActivePage("sectors");
    store.setSelectedPlanetId(12476);
    store.setUiPref("sectorGalaxy", 2);
    store.setDashboard(SAMPLE_DASHBOARD);
    expect(setItemSpy).not.toHaveBeenCalled();
    expect(store.hasPendingPersist()).toBe(true);

    // Advancing 120ms coalesces all 4 mutations into a single localStorage write
    vi.advanceTimersByTime(120);
    expect(setItemSpy).toHaveBeenCalledTimes(1);
    expect(store.hasPendingPersist()).toBe(false);

    // Next mutation flushed immediately by beforeunload BEFORE 120ms timer expires
    store.setActivePage("attacks");
    expect(store.hasPendingPersist()).toBe(true);
    expect(setItemSpy).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event("beforeunload"));
    expect(setItemSpy).toHaveBeenCalledTimes(2);
    expect(store.hasPendingPersist()).toBe(false);
    expect(JSON.parse(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)!).activePage).toBe("attacks");

    // Next mutation flushed immediately by pagehide BEFORE 120ms timer expires
    store.setActivePage("recyclers");
    expect(store.hasPendingPersist()).toBe(true);
    window.dispatchEvent(new Event("pagehide"));
    expect(setItemSpy).toHaveBeenCalledTimes(3);
    expect(store.hasPendingPersist()).toBe(false);
    expect(JSON.parse(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)!).activePage).toBe(
      "recyclers"
    );

    store.destroy();
  });

  it("gracefully prunes oversized payloads and retries slimmer tiers on QuotaExceededError with sessionStorage fallback", () => {
    const bigSectors = Array.from({ length: 900 }, (_, i) => ({
      sector: `02:${String((i % 400) + 1).padStart(3, "0")}`,
      galaxy: 2,
      system: (i % 400) + 1,
      score: i,
      reason: "X".repeat(120),
    }));
    const bigEmails = Array.from({ length: 220 }, (_, i) => ({
      id: i + 1,
      subject: `Mail ${i}`,
      body: "B".repeat(3500),
    }));
    const store = createConsoleStore({ persist: false });
    store.setActivePage("sectors");
    store.setSelectedPlanetId(12476);
    store.setDashboard({
      ...SAMPLE_DASHBOARD,
      logs: { commander: "L".repeat(80_000), game_api: "G".repeat(80_000) },
    });
    store.setSectorsData({ ok: true, sectors: bigSectors, scan_queue: bigSectors.slice(0, 300) });
    store.setRecData({ ok: true, emails: bigEmails, harvests: [], debris: [] });

    // 1. Tier 1 compaction trims logs, caps sectors/emails, and truncates oversized email bodies
    const tier1 = buildPersistedPayload(store.getSnapshot(), 1);
    expect((tier1.dashboard?.logs?.commander as string).length).toBeLessThanOrEqual(16_000);
    expect(tier1.sectorsData?.sectors.length).toBe(500);
    expect(tier1.sectorsData?.scan_queue.length).toBe(180);
    expect(tier1.recData?.emails.length).toBe(150);
    expect(tier1.recData?.emails[0].body.length).toBeLessThanOrEqual(2000);

    // 2. Simulated QuotaExceededError on localStorage for payloads > 25 KB triggers progressive slimming
    const origSetItem = Storage.prototype.setItem;
    const attempts: number[] = [];
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string
    ) {
      if (this === window.localStorage) {
        attempts.push(value.length);
        if (value.length > 25_000) {
          const err = new DOMException("Quota exceeded", "QuotaExceededError");
          throw err;
        }
      }
      return origSetItem.call(this, key, value);
    });

    const result = persistConsoleStateToStorage(store.getSnapshot(), { maxChars: 1_800_000 });
    expect(result.ok).toBe(true);
    expect(result.storage).toBe("local");
    expect(result.tier).toBeGreaterThanOrEqual(2);
    expect(attempts.length).toBeGreaterThanOrEqual(2);

    const parsed = JSON.parse(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)!);
    expect(parsed.activePage).toBe("sectors");
    expect(parsed.selectedPlanetId).toBe(12476);

    // 3. When localStorage ALWAYS throws QuotaExceededError, cleanly falls back to sessionStorage
    localStorage.clear();
    sessionStorage.clear();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string
    ) {
      if (this === window.localStorage) {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }
      return origSetItem.call(this, key, value);
    });

    const fallbackRes = persistConsoleStateToStorage(store.getSnapshot());
    expect(fallbackRes.ok).toBe(true);
    expect(fallbackRes.storage).toBe("session");
    expect(sessionStorage.getItem(CONSOLE_STORE_STORAGE_KEY)).toBeTruthy();

    // And readPersistedConsoleState recovers transparently from the sessionStorage fallback!
    const recovered = readPersistedConsoleState();
    expect(recovered.activePage).toBe("sectors");
    expect(recovered.selectedPlanetId).toBe(12476);

    // 4. When BOTH localStorage & sessionStorage fail, degrades cleanly to memory without throwing
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    const memRes = persistConsoleStateToStorage(store.getSnapshot());
    expect(memRes).toEqual({ ok: false, storage: "memory", tier: -1 });
  });

  it("migrates legacy sessionStorage si_console_ui_state on first load and sanitizes legacy bldCategory", () => {
    sessionStorage.setItem(
      LEGACY_SESSION_STATE_KEY,
      JSON.stringify({
        activePage: "research",
        selectedPlanetId: 12499,
        bldCategory: "universe_research",
        resTab: "combat",
        selectedResearchKey: "r9",
        agActiveTab: "ai-planner",
      })
    );

    const hydrated = rehydrateConsoleStore();
    expect(hydrated.activePage).toBe("research");
    expect(hydrated.selectedPlanetId).toBe(12499);
    expect(hydrated.uiPrefs.bldCategory).toBe("all");
    expect(hydrated.uiPrefs.resTab).toBe("combat");
    expect(hydrated.uiPrefs.selectedResearchKey).toBe("r9");
    expect(hydrated.uiPrefs.agActiveTab).toBe("ai-planner");
    // Only legacy UI prefs migrated (no cached API payloads yet)
    expect(hydrated.isHydratedFromCache).toBe(false);
  });

  it("supports research and fleet-control pages in ConsoleProvider and isolates disableInitialFetch tests from localStorage", async () => {
    // Seed localStorage before mounting a disableInitialFetch provider
    localStorage.setItem(
      CONSOLE_STORE_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        savedAt: Date.now(),
        activePage: "sectors",
        selectedPlanetId: 777,
        uiPrefs: { espTab: "reports" },
        dashboard: SAMPLE_DASHBOARD,
        apiOnline: true,
        lastHydratedAt: Date.now(),
      })
    );

    const commandCalls: Array<Record<string, any>> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, opts?: RequestInit) => {
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        commandCalls.push(body);
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({ ok: true, buildings_eta: SAMPLE_DASHBOARD.buildings_eta }),
        };
      })
    );

    let isolatedActions: ReturnType<typeof useConsoleActions> | null = null;
    let isolatedState: {
      activePage: string;
      dashboard: ReturnType<typeof useDashboard>;
      isHydratedFromCache: boolean;
    } | null = null;
    const IsolatedProbe: React.FC = () => {
      isolatedActions = useConsoleActions();
      const activePage = useActivePage();
      const dashboard = useDashboard();
      const { isHydratedFromCache } = useHydrationMeta();
      isolatedState = { activePage, dashboard, isHydratedFromCache };
      return null;
    };

    const isoMount = render(
      <ConsoleProvider disableInitialFetch>
        <IsolatedProbe />
      </ConsoleProvider>
    );

    // disableInitialFetch={true} MUST ignore localStorage by default and stay completely clean
    expect(isolatedState?.activePage).toBe("dashboard");
    expect(isolatedState?.dashboard).toBeNull();
    expect(isolatedState?.isHydratedFromCache).toBe(false);

    // Seed dashboard via store action and switch to research + fleet-control
    act(() => {
      isolatedActions!.setDashboard(SAMPLE_DASHBOARD);
    });
    expect(isolatedState?.dashboard?.state?.nickname).toBe("ventkx86");

    await act(async () => {
      isolatedActions!.setActivePage("research");
    });
    expect(isolatedState?.activePage).toBe("research");
    await waitFor(() => {
      expect(commandCalls.some((c) => c.command === "buildings_eta")).toBe(true);
    });

    await act(async () => {
      isolatedActions!.setActivePage("fleet-control");
    });
    expect(isolatedState?.activePage).toBe("fleet-control");

    isoMount.unmount();

    // localStorage was NOT overwritten by the isolated disableInitialFetch provider
    const stillSaved = JSON.parse(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)!);
    expect(stillSaved.activePage).toBe("sectors");
    expect(stillSaved.selectedPlanetId).toBe(777);

    // Explicitly enabling persistence via store flag or enablePersistence prop hydrates even with disableInitialFetch
    resetConsoleStore({ clearStorage: false });
    let optInState: {
      activePage: string;
      selectedPlanetId: number | null;
      isHydratedFromCache: boolean;
    } | null = null;
    const OptInProbe: React.FC = () => {
      const activePage = useActivePage();
      const selectedPlanetId = useSelectedPlanetId();
      const { isHydratedFromCache } = useHydrationMeta();
      optInState = { activePage, selectedPlanetId, isHydratedFromCache };
      return null;
    };
    render(
      <ConsoleProvider disableInitialFetch enablePersistence>
        <OptInProbe />
      </ConsoleProvider>
    );
    expect(optInState?.activePage).toBe("sectors");
    expect(optInState?.selectedPlanetId).toBe(777);
    expect(optInState?.isHydratedFromCache).toBe(true);

    // resetConsoleStore({ clearStorage: true }) wipes memory and localStorage
    act(() => {
      resetConsoleStore({ clearStorage: true });
    });
    expect(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)).toBeNull();
    expect(consoleStore.getSnapshot().dashboard).toBeNull();
    expect(consoleStore.getSnapshot().activePage).toBe("dashboard");
  });

  it("keeps SSR renderToString clean with strict 2-SVG invariant and zero literal '>' in HTML attributes", () => {
    const html = renderToString(<App disableInitialFetch />);
    expect(html).toContain('id="view-stage"');

    // Only active page (dashboard) mounts during SSR -> 1 SVG (#gann-radial-svg), <= 2 strict invariant
    const svgMatches = [...html.matchAll(/<svg\b[^>]*>/gi)].map((m) => m[0]);
    expect(svgMatches.length).toBe(1);
    const nonChartSvgs = svgMatches.filter((tag) => !tag.includes('id="rnk-chart"'));
    expect(nonChartSvgs.length).toBe(1);
    expect(nonChartSvgs[0]).toContain('id="gann-radial-svg"');

    // Zero literal '>' inside HTML attributes
    const attrPattern = /="([^"]*)"/g;
    let match: RegExpExecArray | null;
    while ((match = attrPattern.exec(html)) !== null) {
      expect(match[1]).not.toContain(">");
    }

    // Anti-flash si-hydrating script behavior: only sets class when cache exists, clears via fallback timeout
    vi.useFakeTimers();
    const runHeadBootstrap = () => {
      try {
        if (localStorage.getItem("si_console_store_v1")) {
          document.documentElement.classList.add("si-hydrating");
          setTimeout(() => {
            document.documentElement.classList.remove("si-hydrating");
          }, 350);
        }
      } catch {
        // Ignore storage errors
      }
    };

    // Cold boot: never adds si-hydrating
    localStorage.clear();
    runHeadBootstrap();
    expect(document.documentElement.classList.contains("si-hydrating")).toBe(false);

    // Warm F5 with cache: adds si-hydrating and removes after safety fallback timeout
    localStorage.setItem(CONSOLE_STORE_STORAGE_KEY, JSON.stringify({ v: 1 }));
    runHeadBootstrap();
    expect(document.documentElement.classList.contains("si-hydrating")).toBe(true);
    vi.advanceTimersByTime(350);
    expect(document.documentElement.classList.contains("si-hydrating")).toBe(false);
  });

  it("renders EspionageView cached rows on frame 0 after simulated F5 and supports setConsoleStorePersistenceForTests flag", () => {
    localStorage.setItem(
      CONSOLE_STORE_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        savedAt: Date.now(),
        activePage: "espionage",
        selectedPlanetId: 12476,
        uiPrefs: { espTab: "targets" },
        dashboard: SAMPLE_DASHBOARD,
        espData: SAMPLE_ESP_DATA,
        apiOnline: true,
        lastHydratedAt: Date.now(),
      })
    );
    resetConsoleStore({ clearStorage: false });
    setConsoleStorePersistenceForTests(true);

    const { container } = render(
      <ConsoleProvider disableInitialFetch>
        <EspionageView />
      </ConsoleProvider>
    );

    const viewEl = container.querySelector("#view-espionage") as HTMLElement | null;
    expect(viewEl).not.toBeNull();
    expect(viewEl?.hidden).toBe(false);
    expect(container.querySelector("#esp-targets-tbody")?.textContent).toContain("02:380:04");
    expect(container.querySelector("#esp-targets-tbody")?.textContent).toContain("IdleTarget");
  });

  it("isolates granular slice selectors so unrelated runtime ticks and uiPref changes do not re-render dashboard consumers", () => {
    let dashRenders = 0;
    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;

    const DashboardOnlyConsumer: React.FC = () => {
      const dash = useDashboard();
      actionsRef = useConsoleActions();
      dashRenders += 1;
      return <div data-testid="dash-nick">{dash?.state?.nickname || "none"}</div>;
    };

    render(
      <ConsoleProvider disableInitialFetch>
        <DashboardOnlyConsumer />
      </ConsoleProvider>
    );

    expect(dashRenders).toBe(1);

    // Unrelated runtime & UI-pref updates MUST NOT re-render DashboardOnlyConsumer
    act(() => {
      actionsRef!.setNowEpochSec(1700000001);
      actionsRef!.setNowEpochSec(1700000002);
      actionsRef!.showFeedback("Transient notice", "ok");
      actionsRef!.setUiPref("espTab", "reports");
      actionsRef!.beginGaCalc();
      actionsRef!.endGaCalc();
    });
    expect(dashRenders).toBe(1);

    // Updating dashboard itself triggers exactly one re-render
    act(() => {
      actionsRef!.setDashboard(SAMPLE_DASHBOARD);
    });
    expect(dashRenders).toBe(2);
  });

  it("preserves bot_control across unchanged /dashboard delta polls and applies changed paused/busy updates", async () => {
    const initialScanned: Record<string, number> = { "1:1": 1760000001, "2:380": 1760000002 };
    const requestedUrls: string[] = [];
    let pollStep = 0;

    const initialDeltaPayload: DashboardPayload = {
      ok: true,
      delta: false,
      snapshot_generation: 1,
      published_at: 1760000100,
      health: { ok: true, in_game: true, debug_port_open: true, debug_port: 9222 },
      bot_control: {
        paused: false,
        busy: false,
        mode_override: "auto",
        captcha_blocked: false,
        scanned_systems: initialScanned,
      },
      state: SAMPLE_DASHBOARD.state,
      config: SAMPLE_DASHBOARD.config,
      ui_meta: {
        server_boot_id: "boot-1",
        code_version: "code-1",
        css_version: "css-1",
        planner_mode: "priority",
        effective_planner_mode: "priority",
      },
      section_versions: {
        bot_control: "bc_v1",
        state: "st_v1",
        config: "cfg_v1",
        ui_meta: "ui_v1",
      },
      unchanged_sections: [],
      hourly_production_mode: "full",
      hourly_production_history: [
        {
          minute_bucket: 29000000,
          pig_iron_per_hour: 400,
          metal_per_hour: 250,
          kryptonite_per_hour: 200,
          spice_per_hour: 150,
          total_per_hour: 1000,
          speq_per_hour: 1000,
        },
      ],
      latest_minute_bucket: 29000000,
      loot_recycle_mode: "full",
      loot_recycle_hourly: [
        {
          hour_bucket: 485000,
          stolen_pig_iron: 2000,
          stolen_metal: 1500,
          stolen_kryptonite: 1000,
          stolen_spice: 500,
          stolen_total: 5000,
          recycled_pig_iron: 500,
          recycled_metal: 300,
          recycled_kryptonite: 200,
          recycled_spice: 0,
          recycled_total: 1000,
        },
      ],
      latest_loot_hour: 485000,
    };

    const unchangedDeltaPayload: DashboardPayload = {
      ok: true,
      delta: true,
      snapshot_generation: 1,
      published_at: 1760000105,
      health: { ok: true, in_game: true, debug_port_open: true, debug_port: 9222 },
      section_versions: {
        bot_control: "bc_v1",
        state: "st_v1",
        config: "cfg_v1",
        ui_meta: "ui_v1",
      },
      unchanged_sections: ["bot_control", "state", "config", "ui_meta"],
      hourly_production_mode: "delta",
      hourly_production_history: [
        {
          minute_bucket: 29000060,
          pig_iron_per_hour: 500,
          metal_per_hour: 300,
          kryptonite_per_hour: 220,
          spice_per_hour: 180,
          total_per_hour: 1200,
          speq_per_hour: 1200,
        },
      ],
      latest_minute_bucket: 29000060,
      loot_recycle_mode: "delta",
      loot_recycle_hourly: [
        {
          hour_bucket: 485001,
          stolen_pig_iron: 3000,
          stolen_metal: 2000,
          stolen_kryptonite: 1200,
          stolen_spice: 800,
          stolen_total: 7000,
          recycled_pig_iron: 700,
          recycled_metal: 500,
          recycled_kryptonite: 300,
          recycled_spice: 0,
          recycled_total: 1500,
        },
      ],
      latest_loot_hour: 485001,
    };

    const changedBotControlPayload: DashboardPayload = {
      ok: true,
      delta: true,
      snapshot_generation: 2,
      published_at: 1760000110,
      health: { ok: true, in_game: true, debug_port_open: true, debug_port: 9222 },
      bot_control: {
        paused: true,
        busy: true,
        mode_override: "dry-run",
        captcha_blocked: false,
        scanned_systems: { ...initialScanned, "3:120": 1760000003 },
      },
      section_versions: {
        bot_control: "bc_v2",
        state: "st_v1",
        config: "cfg_v1",
        ui_meta: "ui_v1",
      },
      unchanged_sections: ["state", "config", "ui_meta"],
      hourly_production_mode: "delta",
      hourly_production_history: [],
      latest_minute_bucket: 29000060,
      loot_recycle_mode: "delta",
      loot_recycle_hourly: [],
      latest_loot_hour: 485001,
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = String(url);
        requestedUrls.push(u);
        if (u.startsWith("/ui-version")) {
          return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
        }
        if (u.startsWith("/dashboard")) {
          const payload =
            pollStep === 0
              ? initialDeltaPayload
              : pollStep === 1
                ? unchangedDeltaPayload
                : changedBotControlPayload;
          pollStep += 1;
          return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
        }
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      })
    );

    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;
    let dashRef: ReturnType<typeof useDashboard> = null;
    const DeltaProbe: React.FC = () => {
      actionsRef = useConsoleActions();
      dashRef = useDashboard();
      return null;
    };

    render(
      <ConsoleProvider disableInitialFetch>
        <DeltaProbe />
      </ConsoleProvider>
    );

    // 1. Initial load sends versions={} and hydrates bot_control + ui_meta + histories
    await act(async () => {
      await actionsRef!.loadDashboard({ refresh: false, preserveConfig: false });
    });
    expect(requestedUrls[0]).toContain("versions=%7B%7D");
    expect(dashRef?.bot_control?.paused).toBe(false);
    expect(dashRef?.bot_control?.scanned_systems).toEqual(initialScanned);
    expect(dashRef?.code_version).toBe("code-1");
    expect(dashRef?.loot_recycle_history).toHaveLength(1);

    // 2. Unchanged poll sends tracked versions + cursors; bot_control & state stay intact and series append
    expect(buildDashboardPollUrl(false, dashRef)).toContain("since_minute_bucket=29000000");
    expect(buildDashboardPollUrl(true, dashRef)).toBe("/dashboard?refresh=1");

    await act(async () => {
      await actionsRef!.loadDashboard({ refresh: false, preserveConfig: true });
    });
    expect(requestedUrls[1]).toContain("since_minute_bucket=29000000");
    expect(requestedUrls[1]).toContain("since_loot_hour=485000");
    expect(dashRef?.bot_control?.paused).toBe(false);
    expect(dashRef?.bot_control?.scanned_systems).toEqual(initialScanned);
    expect(dashRef?.state?.nickname).toBe("ventkx86");
    expect(dashRef?.hourly_production_history).toHaveLength(2);
    expect(dashRef?.loot_recycle_history).toHaveLength(2);

    // 3. Changed bot_control (paused/busy/mode_override) updates in-place while preserving state
    await act(async () => {
      await actionsRef!.loadDashboard({ refresh: false, preserveConfig: true });
    });
    expect(dashRef?.bot_control?.paused).toBe(true);
    expect(dashRef?.bot_control?.busy).toBe(true);
    expect(dashRef?.bot_control?.mode_override).toBe("dry-run");
    expect(dashRef?.bot_control?.scanned_systems).toEqual({
      ...initialScanned,
      "3:120": 1760000003,
    });
    expect(dashRef?.section_versions?.bot_control).toBe("bc_v2");
    expect(dashRef?.state?.nickname).toBe("ventkx86");

    // Direct helper check: refresh=1 full payload drops stale section_versions when absent
    const fullReplacement = mergeDashboardDeltaResponse(dashRef, SAMPLE_DASHBOARD);
    expect(fullReplacement.section_versions).toBeUndefined();
  });

  it("ignores stale localStorage versions on delta poll and breaks repeat hot-reload loops via sessionStorage", async () => {
    localStorage.setItem(
      CONSOLE_STORE_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        savedAt: Date.now(),
        activePage: "dashboard",
        dashboard: {
          ...SAMPLE_DASHBOARD,
          server_boot_id: "boot-stale",
          code_version: "code-stale",
          css_version: "css-stale",
          ui_version: "code-stale:css-stale",
          ui_meta: {
            server_boot_id: "boot-stale",
            code_version: "code-stale",
            css_version: "css-stale",
            ui_version: "code-stale:css-stale",
          },
        },
        apiOnline: true,
        lastHydratedAt: Date.now(),
      })
    );
    resetConsoleStore({ clearStorage: false });
    setConsoleStorePersistenceForTests(true);

    let liveCodeVer = "code-live-1";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = String(url);
        if (u.startsWith("/ui-version")) {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                server_boot_id: "boot-live-1",
                code_version: liveCodeVer,
                css_version: "css-live-1",
                ui_version: `${liveCodeVer}:css-live-1`,
              }),
          };
        }
        if (u.startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ...SAMPLE_DASHBOARD,
                delta: true,
                server_boot_id: "boot-live-1",
                code_version: liveCodeVer,
                css_version: "css-live-1",
                ui_version: `${liveCodeVer}:css-live-1`,
                section_versions: { state: "st_v1", ui_meta: "ui_v1" },
                unchanged_sections: ["state", "ui_meta"],
              }),
          };
        }
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      })
    );

    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;
    let dashRef: ReturnType<typeof useDashboard> = null;
    const HotReloadProbe: React.FC = () => {
      actionsRef = useConsoleActions();
      dashRef = useDashboard();
      return null;
    };

    sessionStorage.clear();
    render(
      <ConsoleProvider>
        <HotReloadProbe />
      </ConsoleProvider>
    );

    // 1. Live /ui-version and delta /dashboard agree -> stale localStorage tokens overwritten with zero reload
    await waitFor(() => {
      expect(dashRef?.code_version).toBe("code-live-1");
    });
    expect(sessionStorage.getItem("si_hot_reload_guard")).toBeNull();

    // 2. First real code change writes sessionStorage guard and schedules reload; second poll for same target suppresses loop
    liveCodeVer = "code-live-2";
    let didReloadFirst = false;
    await act(async () => {
      didReloadFirst = await actionsRef!.loadDashboard({ refresh: false, preserveConfig: true });
    });
    expect(didReloadFirst).toBe(false);
    expect(sessionStorage.getItem("si_hot_reload_guard")).toContain("boot-live-1|code-live-2");

    let didReloadSecond = false;
    await act(async () => {
      didReloadSecond = await actionsRef!.loadDashboard({ refresh: false, preserveConfig: true });
    });
    expect(didReloadSecond).toBe(true);
    expect(dashRef?.code_version).toBe("code-live-2");

    // 3. Vite dev document (<script type="module" src="/src/main.tsx">) skips full-page code reload cleanly
    const viteScript = document.createElement("script");
    viteScript.type = "module";
    viteScript.src = "/src/main.tsx";
    document.body.appendChild(viteScript);
    try {
      sessionStorage.clear();
      liveCodeVer = "code-live-3";
      await act(async () => {
        await actionsRef!.checkApiHealthAndHotReload();
      });
      expect(sessionStorage.getItem("si_hot_reload_guard")).toBeNull();
    } finally {
      viteScript.remove();
      sessionStorage.clear();
    }
  });
});
