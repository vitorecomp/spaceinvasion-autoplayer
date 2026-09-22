import React from "react";
import { renderToString } from "react-dom/server";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { EspionageView } from "../features/espionage";
import {
  ConsoleProvider,
  flushConsoleStore,
  rehydrateConsoleStore,
  resetConsoleStore,
  useConsole,
} from "../context/ConsoleContext";
import {
  CONSOLE_STORE_STORAGE_KEY,
  LEGACY_SESSION_STATE_KEY,
  buildPersistedPayload,
  consoleStore,
  createConsoleStore,
  persistConsoleStateToStorage,
  readPersistedConsoleState,
  setConsoleStorePersistenceForTests,
} from "../store/consoleStore";
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

    let ctxRef: ReturnType<typeof useConsole> | null = null;
    const SessionDriver: React.FC = () => {
      const ctx = useConsole();
      ctxRef = ctx;
      return <div data-testid="active-page">{ctx.activePage}</div>;
    };

    // 1. Initial live session: load dashboard, navigate to espionage, adjust planet & prefs
    const firstMount = render(
      <ConsoleProvider>
        <SessionDriver />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(ctxRef?.dashboard?.state?.nickname).toBe("ventkx86");
    });

    await act(async () => {
      ctxRef!.setActivePage("espionage");
      ctxRef!.setSelectedPlanetId(12499);
      ctxRef!.setUiPref("espTab", "reports");
      ctxRef!.setUiPref("resTab", "drive");
      ctxRef!.setUiPref("selectedResearchKey", "r7");
      ctxRef!.setUiPref("dashQueuePlanetScope", "12499");
    });

    await waitFor(() => {
      expect(ctxRef?.espData?.targets?.[0]?.coords).toBe("02:380:04");
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
      const ctx = useConsole();
      frameLog.push({
        activePage: ctx.activePage,
        selectedPlanetId: ctx.selectedPlanetId,
        hasDashboard: ctx.dashboard !== null,
        nickname: ctx.dashboard?.state?.nickname ?? null,
        espCount: ctx.espData?.targets?.length ?? 0,
        espTab: ctx.uiPrefs.espTab,
        resTab: ctx.uiPrefs.resTab,
        selectedResearchKey: ctx.uiPrefs.selectedResearchKey,
        dashScope: ctx.uiPrefs.dashQueuePlanetScope,
        apiOnline: ctx.apiOnline,
        isHydratedFromCache: ctx.isHydratedFromCache,
      });
      return <div data-testid="hydrated-nick">{ctx.dashboard?.state?.nickname || "empty"}</div>;
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

    // Legacy window bridges (window.state & window.__SI_STATE__) stay 100% synchronized
    const w = window as any;
    expect(w.state.activePage).toBe("espionage");
    expect(w.state.dashboard?.state?.nickname).toBe("ventkx86");
    expect(w.state.resTab).toBe("drive");
    expect(w.state.selectedResearchKey).toBe("r7");
    expect(w.__SI_STATE__).toBe(w.state);
    expect(w.__SI_STATE__.isHydratedFromCache).toBe(true);
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

    let ctxRef: ReturnType<typeof useConsole> | null = null;
    const Probe: React.FC = () => {
      ctxRef = useConsole();
      return null;
    };

    render(
      <ConsoleProvider>
        <Probe />
      </ConsoleProvider>
    );

    // Offline failure marks apiOnline false but MUST NOT clear cached dashboard or espData
    await waitFor(() => {
      expect(ctxRef?.apiOnline).toBe(false);
    });
    expect(ctxRef?.dashboard?.state?.nickname).toBe("ventkx86");
    expect(ctxRef?.espData?.targets?.[0]?.coords).toBe("02:380:04");

    // Once API recovers, fresh data patches smoothly in-place
    shouldFail = false;
    await act(async () => {
      await ctxRef!.loadDashboard({ refresh: false, force: true });
    });

    await waitFor(() => {
      expect(ctxRef?.dashboard?.state?.nickname).toBe("updated-commander");
      expect(ctxRef?.espData?.targets?.[0]?.coords).toBe("02:380:09");
      expect(ctxRef?.apiOnline).toBe(true);
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

  it("supports research and fleet-control pages in ConsoleContext and isolates disableInitialFetch tests from localStorage", async () => {
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

    let isolatedCtx: ReturnType<typeof useConsole> | null = null;
    const IsolatedProbe: React.FC = () => {
      isolatedCtx = useConsole();
      return null;
    };

    const isoMount = render(
      <ConsoleProvider disableInitialFetch>
        <IsolatedProbe />
      </ConsoleProvider>
    );

    // disableInitialFetch={true} MUST ignore localStorage by default and stay completely clean
    expect(isolatedCtx?.activePage).toBe("dashboard");
    expect(isolatedCtx?.dashboard).toBeNull();
    expect(isolatedCtx?.isHydratedFromCache).toBe(false);

    // Seed dashboard via legacy renderResearchView global hook and switch to research + fleet-control
    (globalThis as any).renderResearchView(SAMPLE_DASHBOARD);
    expect(isolatedCtx?.dashboard?.state?.nickname).toBe("ventkx86");

    await act(async () => {
      isolatedCtx!.setActivePage("research");
    });
    expect(isolatedCtx?.activePage).toBe("research");
    await waitFor(() => {
      expect(commandCalls.some((c) => c.command === "buildings_eta")).toBe(true);
    });

    await act(async () => {
      isolatedCtx!.setActivePage("fleet-control");
    });
    expect(isolatedCtx?.activePage).toBe("fleet-control");

    isoMount.unmount();

    // localStorage was NOT overwritten by the isolated disableInitialFetch provider
    const stillSaved = JSON.parse(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)!);
    expect(stillSaved.activePage).toBe("sectors");
    expect(stillSaved.selectedPlanetId).toBe(777);

    // Explicitly enabling persistence via store flag or enablePersistence prop hydrates even with disableInitialFetch
    resetConsoleStore({ clearStorage: false });
    let optInCtx: ReturnType<typeof useConsole> | null = null;
    const OptInProbe: React.FC = () => {
      optInCtx = useConsole();
      return null;
    };
    render(
      <ConsoleProvider disableInitialFetch enablePersistence>
        <OptInProbe />
      </ConsoleProvider>
    );
    expect(optInCtx?.activePage).toBe("sectors");
    expect(optInCtx?.selectedPlanetId).toBe(777);
    expect(optInCtx?.isHydratedFromCache).toBe(true);

    // resetConsoleStore({ clearStorage: true }) wipes memory, window.state, and localStorage
    act(() => {
      resetConsoleStore({ clearStorage: true });
    });
    expect(localStorage.getItem(CONSOLE_STORE_STORAGE_KEY)).toBeNull();
    expect(consoleStore.getSnapshot().dashboard).toBeNull();
    expect((window as any).state.dashboard).toBeNull();
    expect((window as any).__SI_STATE__.activePage).toBe("dashboard");
  });

  it("keeps SSR renderToString clean with strict 2-SVG invariant and zero literal '>' in HTML attributes", () => {
    const html = renderToString(<App disableInitialFetch />);
    expect(html).toContain('id="view-stage"');

    // Strict 2-SVG invariant (#gann-radial-svg + #rnk-chart)
    const svgMatches = [...html.matchAll(/<svg\b[^>]*>/gi)].map((m) => m[0]);
    expect(svgMatches.length).toBe(2);
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
});
