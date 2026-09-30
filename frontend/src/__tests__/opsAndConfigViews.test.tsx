import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../context/ConsoleContext";
import { DashboardPayload, QueueJobData } from "../types/api";
import { DashboardView } from "../features/dashboard";
import { OperationsView } from "../features/operations";
import { BuildingsView } from "../features/buildings";
import { ConfigView } from "../features/config";
import { ActionGraphView } from "../features/action-graph";
import { HelpAlertModal } from "../components/modals/HelpAlertModal";
import { NotificationCenterModal } from "../components/modals/NotificationCenterModal";
import { FeedbackBanner } from "../components/layout/FeedbackBanner";
import { AppHeader } from "../components/layout/AppHeader";
import { AppSidebar } from "../components/layout/AppSidebar";
import { renamePlanet } from "../api/httpClient";

const MOCK_DASHBOARD: DashboardPayload = {
  ok: true,
  planner_mode: "priority",
  health: {
    ok: true,
    in_game: true,
    debug_port_open: true,
    debug_port: 9222,
  },
  config: {
    planner_mode: "priority",
    playstyle: "fleeter",
    allow_multi_planet: true,
    max_planets: 11,
    min_colony_fields: 300,
    galaxy_scan_click_delay_seconds: 1.5,
    galaxy_scan_click_jitter_seconds: 0.6,
    galaxy_scan_explore_first: true,
    galaxy_scan_near_weight: 2.0,
    galaxy_scan_attack_weight: 3.0,
    reserved_spy_slots: 1,
    spy_dispatch_planet_id: 0,
  },
  state: {
    universe_id: 31,
    user_id: 103603,
    nickname: "ventkx86",
    active_planet_id: 12476,
    planets: [
      {
        planet_id: 12476,
        name: "Colony",
        coords: "02:380:12",
        planet_type: "normal",
        temperature: 31,
        fields_used: 45,
        fields_max: 230,
        resources: { pig_iron: 25000, metal: 12000, kryptonite: 8000, spice: 4000 },
        storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
        energy: { produced: 600, used: 420, net: 180 },
        buildings: { b1: 8, b2: 6, b5: 10, b8: 3, b9: 4, b10: 5 },
        ships: { s10: 10 },
      },
    ],
    researches: { r1: 4, r2: 6 },
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
      target_level: 9,
      cost: { pig_iron: 1200, kryptonite: 400 },
      missing: {},
      reason: "Upgrade mine",
      executable_now: true,
      wait_eta: "Ready",
      duration_text: "2m 00s",
      total_eta: "2m",
    },
  ],
  buildings_eta: {
    planet: { planet_id: 12476, coords: "02:380:12", building_time_divisor: 10 },
    planet_economy: { blast_furnace_pct: 100 },
    buildings: [
      {
        building_id: 1,
        key: "b1",
        name: "Pig iron mine",
        category: "mines",
        current_level: 8,
        next_level: 9,
        missing_total: 0,
        build_time_text: "2m 00s",
        wait_eta: "Ready",
        reach_eta: "2m 00s",
        wait_hours: 0,
        can_build_live: true,
      },
    ],
    researches: [],
  },
  universe_research_max: [
    {
      research_id: 1,
      key: "r1",
      slug: "mining-technology",
      name: "Mining technology",
      own_level: 4,
      universe_max_level: 15,
      catalog_max_level: 40,
      gap_to_max: 11,
      holder: "StarLord",
      holder_coords: "02:100:04",
      updated_ts: 1700000000,
    },
  ],
  galaxy_sweep: {
    scan_phase: "exploratory",
    explored_systems: 208,
    unscanned_systems: 192,
    total_systems: 400,
    coverage_pct: 52.0,
    universe_explored_systems: 208,
    universe_total_systems: 5600,
    universe_coverage_pct: 3.71,
    scan_galaxies: [2],
    in_progress: false,
  },
};

const SeedProvider: React.FC<{ page: string; children: React.ReactNode }> = ({
  page,
  children,
}) => {
  const Inner: React.FC = () => {
    const { setDashboard, setBuildingsEtaData, setActionGraphData, setActivePage } = useConsole();
    useEffect(() => {
      setDashboard(MOCK_DASHBOARD);
      if (MOCK_DASHBOARD.buildings_eta) setBuildingsEtaData(MOCK_DASHBOARD.buildings_eta);
      setActionGraphData({
        mode: "priority",
        simulated: false,
        state_available: true,
        is_dirty: false,
        changed_keys: [],
        active_config: MOCK_DASHBOARD.config,
        effective_config: MOCK_DASHBOARD.config,
        lanes: [
          {
            id: "lane:planet:12476",
            scope: "planet",
            label: "Colony [02:380:12]",
            planet_id: 12476,
            coords: "02:380:12",
            planet_type: "normal",
            energy: 180,
            node_count: 1,
          },
        ],
        nodes: [
          {
            id: "act_0",
            order_index: 0,
            exec_rank: 0,
            topological_layer: 0,
            lane_id: "lane:planet:12476",
            priority: "P2",
            kind: "BUILD",
            category: "economy",
            status: "ready",
            planet_id: 12476,
            planet_coords: "02:380:12",
            planet_name: "Colony",
            target_id: 1,
            target_name: "Pig iron mine",
            target_level: 9,
            reason: "Upgrade mine",
            executable_now: true,
            cost: { pig_iron: 1200, metal: 0, kryptonite: 400, spice: 0 },
            missing: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
            wait_seconds: 0,
            wait_eta: "Ready",
            duration_seconds: 120,
            duration_text: "2m 00s",
            total_seconds: 120,
            total_eta: "2m",
            eta_bottleneck: null,
            in_degree: 0,
            out_degree: 0,
          },
        ],
        edges: [],
        summary: {
          total_nodes: 1,
          total_edges: 0,
          ready_count: 1,
          waiting_count: 0,
          blocked_count: 0,
          queued_count: 0,
          max_layer: 0,
          critical_path_eta: "2m",
        },
      });
      setActivePage(page);
    }, [page, setActionGraphData, setActivePage, setBuildingsEtaData, setDashboard]);
    return null;
  };

  return (
    <ConsoleProvider disableInitialFetch>
      <Inner />
      {children}
    </ConsoleProvider>
  );
};

describe("Dashboard, Operations, Buildings, Logs, Config & ActionGraph React Views", () => {
  let postedCommands: Array<Record<string, any>> = [];

  beforeEach(() => {
    postedCommands = [];
    sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(MOCK_DASHBOARD),
          };
        }
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        postedCommands.push(body);
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({
              ok: true,
              config: { ...MOCK_DASHBOARD.config, ...(body.config || {}) },
              rows: MOCK_DASHBOARD.universe_research_max,
            }),
        };
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders ConfigView with grouped sections, detailed explanations, and new galaxy/spy parameters", async () => {
    const { container } = render(
      <SeedProvider page="config">
        <AppHeader />
        <ConfigView />
      </SeedProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".config-group").length).toBeGreaterThanOrEqual(5);
    });
    expect(container.querySelector("#hud-galaxy-coverage")?.textContent).toBe("GAL 52.0% · UNI 3.7%");
    expect(container.querySelector("#cfg-galaxy-coverage")?.textContent).toContain("GAL 52.0% (208/400) · UNI 3.7% (208/5600)");

    const configItems = container.querySelectorAll(".config-item");
    expect(configItems.length).toBeGreaterThanOrEqual(25);
    configItems.forEach((item) => {
      const desc = item.querySelector(".config-item-desc")?.textContent?.trim() || "";
      expect(desc.length).toBeGreaterThan(20);
    });

    expect(container.querySelector("#cfg-galaxy_scan_click_delay_seconds")).not.toBeNull();
    expect(container.querySelector("#cfg-reserved_spy_slots")).not.toBeNull();
    expect(container.querySelector("#cfg-spy_dispatch_planet_id")).not.toBeNull();

    const delayInp = container.querySelector(
      "#cfg-galaxy_scan_click_delay_seconds"
    ) as HTMLInputElement;
    fireEvent.change(delayInp, { target: { value: "2.4" } });
    fireEvent.click(container.querySelector("#btn-save-config")!);

    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) =>
            c.command === "update_config" &&
            Number(c.config?.galaxy_scan_click_delay_seconds) === 2.4
        )
      ).toBe(true);
    });
  });

  it("renders and saves Smart 2-Phase Galaxy Scanner config fields (explore-first, near weight, attack weight)", async () => {
    const { container } = render(
      <SeedProvider page="config">
        <ConfigView />
      </SeedProvider>
    );

    await waitFor(() => {
      expect(container.querySelector("#cfg-galaxy_scan_explore_first")).not.toBeNull();
    });

    const galaxyGroup = container.querySelector('[aria-labelledby="cfg-grp-galaxy"]')!;
    expect(galaxyGroup).not.toBeNull();
    for (const key of [
      "galaxy_scan_explore_first",
      "galaxy_scan_near_weight",
      "galaxy_scan_attack_weight",
    ]) {
      const ctrl = galaxyGroup.querySelector(`#cfg-${key}`);
      expect(ctrl).not.toBeNull();
      const item = ctrl!.closest(".config-item")!;
      expect(item.querySelector(".config-item-key")?.textContent).toBe(key);
      const desc = item.querySelector(".config-item-desc")?.textContent || "";
      expect(desc.length).toBeGreaterThan(120);
    }

    const exploreBox = container.querySelector("#cfg-galaxy_scan_explore_first") as HTMLInputElement;
    const nearInp = container.querySelector("#cfg-galaxy_scan_near_weight") as HTMLInputElement;
    const attackInp = container.querySelector("#cfg-galaxy_scan_attack_weight") as HTMLInputElement;
    expect(exploreBox.type).toBe("checkbox");
    expect(exploreBox.checked).toBe(true);
    expect(Number(nearInp.value)).toBe(2);
    expect(Number(attackInp.value)).toBe(3);
    expect(nearInp.min).toBe("0");
    expect(nearInp.max).toBe("20");
    expect(attackInp.min).toBe("0");
    expect(attackInp.max).toBe("20");

    const exploreDesc =
      exploreBox.closest(".config-item")!.querySelector(".config-item-desc")?.textContent || "";
    expect(exploreDesc).toContain("1–400");
    expect(exploreDesc.toLowerCase()).toContain("phase 2");

    fireEvent.click(exploreBox);
    fireEvent.change(nearInp, { target: { value: "4.5" } });
    fireEvent.change(attackInp, { target: { value: "7" } });
    fireEvent.click(container.querySelector("#btn-save-config")!);

    await waitFor(() => {
      const saved = postedCommands.find((c) => c.command === "update_config");
      expect(saved).toBeDefined();
      expect(saved!.config.galaxy_scan_explore_first).toBe(false);
      expect(Number(saved!.config.galaxy_scan_near_weight)).toBe(4.5);
      expect(Number(saved!.config.galaxy_scan_attack_weight)).toBe(7);
    });
  });

  it("supports mouse click-and-drag scrolling on ActionGraphView #ag-canvas-scroll", async () => {
    const { container } = render(
      <SeedProvider page="action-graph">
        <ActionGraphView />
      </SeedProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".ag-node-card").length).toBe(1);
    });

    const scrollEl = container.querySelector("#ag-canvas-scroll") as HTMLElement;
    expect(scrollEl).not.toBeNull();
    scrollEl.scrollLeft = 0;

    fireEvent.mouseDown(scrollEl, { clientX: 300, clientY: 120, button: 0 });
    fireEvent.mouseMove(scrollEl, { clientX: 180, clientY: 90 });
    expect(scrollEl.classList.contains("is-dragging")).toBe(true);
    expect(scrollEl.scrollLeft).toBe(120);
    expect(scrollEl.scrollTop).toBe(30);

    fireEvent.mouseUp(scrollEl);
    expect(scrollEl.classList.contains("is-dragging")).toBe(false);
  });

  it("renders DashboardView 3 queues, OperationsView, and BuildingsView without removed LogsView", async () => {
    const { container } = render(
      <SeedProvider page="dashboard">
        <AppSidebar />
        <DashboardView />
        <OperationsView />
        <BuildingsView />
      </SeedProvider>
    );

    await waitFor(() => {
      expect(container.querySelector("#dash-plan-tbody")?.textContent).toContain("Pig iron mine");
    });
    expect(container.querySelector("#dash-queue-card-building")).not.toBeNull();
    expect(container.querySelector("#dash-queue-card-fleet")).not.toBeNull();
    expect(container.querySelector("#dash-queue-card-research")).not.toBeNull();
    expect(container.querySelector("#plan-tbody")?.textContent).toContain("Pig iron mine");
    expect(container.querySelector("#view-buildings")?.textContent).toContain("Pig iron mine");
    expect(container.querySelector('[data-page="logs"]')).toBeNull();
    expect(container.querySelector("#view-logs")).toBeNull();
  });

  it("renders notification ID badge (NTF-XXXX) and Copy ID buttons in HelpAlertModal and NotificationCenterModal", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const mockNotifItem = {
      id: 42,
      notification_code: "NTF-0042",
      status: "unread",
      severity: "warning",
      category: "action_validation",
      title: "Spy Dispatch Blocked",
      message: "Target rejected probe.",
      planet_coords: "02:380:12",
      action_kind: "SPY",
      occurrence_count: 2,
      updated_ts: 1700000500,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ...MOCK_DASHBOARD,
                notifications: { unread_count: 1, items: [mockNotifItem] },
              }),
          };
        }
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({
              ok: true,
              unread_count: 1,
              items: [mockNotifItem],
            }),
        };
      })
    );

    const TriggerOpen: React.FC = () => {
      const { loadDashboard, openNotificationCenter } = useConsole();
      useEffect(() => {
        void loadDashboard({ force: true }).then(() => openNotificationCenter());
      }, [loadDashboard, openNotificationCenter]);
      return null;
    };

    const { container } = render(
      <ConsoleProvider disableInitialFetch>
        <TriggerOpen />
        <FeedbackBanner />
        <HelpAlertModal />
        <NotificationCenterModal />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelector("#notif-help-id")?.textContent).toBe("NTF-0042");
      expect(container.querySelector('[data-notif-code="NTF-0042"]')?.textContent).toBe("NTF-0042");
    });

    const copyBtn = container.querySelector("#btn-notif-help-copy-id") as HTMLButtonElement;
    expect(copyBtn).not.toBeNull();
    fireEvent.click(copyBtn);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("NTF-0042");
      expect(container.querySelector("#feedback-banner")?.textContent).toContain("NTF-0042");
    });
  });

  it("supports per-planet queue filtering, full-width equal planet chips, and removed Parallel/Full Queue buttons on Dashboard and Operations", async () => {
    const multiPlanetDash = {
      ...MOCK_DASHBOARD,
      planet_economies: {
        "12476": {
          planet_id: 12476,
          planet_coords: "02:380:12",
          blast_furnace_pct: 100,
          pig_iron_per_hr: 1200,
          metal_per_hr: 600,
          kryptonite_per_hr: 450,
          spice_per_hr: 180,
        },
        "12453": {
          planet_id: 12453,
          planet_coords: "02:379:05",
          blast_furnace_pct: 100,
          pig_iron_per_hr: 350,
          metal_per_hr: 120,
          kryptonite_per_hr: 90,
          spice_per_hr: 40,
        },
      },
      state: {
        ...MOCK_DASHBOARD.state,
        planets: [
          ...MOCK_DASHBOARD.state.planets,
          {
            planet_id: 12453,
            name: "Colony 2",
            coords: "02:379:05",
            planet_type: "normal",
            temperature: 28,
            fields_used: 12,
            fields_max: 315,
            resources: { pig_iron: 2900, metal: 900, kryptonite: 950, spice: 500 },
            storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
            energy: { produced: 120, used: 41 },
            buildings: { b1: 4, b3: 1 },
            ships: {},
            defense: {},
            economy_rates: { resource1: 100, resource2: 100, resource3: 100, resource4: 100 },
          },
        ],
      },
      plan: [
        ...MOCK_DASHBOARD.plan,
        {
          priority: "P2",
          kind: "BUILD",
          planet_id: 12453,
          planet_coords: "02:379:05",
          target_id: 3,
          target_name: "Kryptonite mine",
          target_level: 2,
          cost: { pig_iron: 136, metal: 0, kryptonite: 84, spice: 0 },
          missing: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
          reason: "Upgrade colony mine in parallel",
          executable_now: true,
        },
        {
          priority: "P3",
          kind: "RESEARCH",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 1,
          target_name: "Mining technology",
          target_level: 11,
          cost: { pig_iron: 10000, metal: 0, kryptonite: 0, spice: 0 },
          missing: { pig_iron: 5000, metal: 0, kryptonite: 0, spice: 0 },
          reason: "Global research",
          executable_now: false,
        },
      ],
    };

    const MultiSeed: React.FC<{ page: "dashboard" | "operations"; children: React.ReactNode }> = ({
      page,
      children,
    }) => {
      const Inner: React.FC = () => {
        const { setDashboard, setActivePage } = useConsole();
        useEffect(() => {
          setDashboard(multiPlanetDash as any);
          setActivePage(page);
        }, [setActivePage, setDashboard]);
        return null;
      };
      return (
        <ConsoleProvider disableInitialFetch>
          <Inner />
          {children}
        </ConsoleProvider>
      );
    };

    // 1) Dashboard planet scope filters Building Queue + SEC resources while keeping Research Queue global
    const dashRender = render(
      <MultiSeed page="dashboard">
        <DashboardView />
        <ActionGraphView />
      </MultiSeed>
    );
    await waitFor(() => {
      expect(dashRender.container.querySelector('[data-dash-planet-scope="12453"]')).not.toBeNull();
    });
    // ActionGraphView MUST remain hidden when activePage is dashboard
    expect(
      (dashRender.container.querySelector("#view-action-graph") as HTMLElement | null)?.hidden
    ).toBe(true);
    expect(dashRender.container.querySelector("#dash-queue-card-research")?.textContent).toContain(
      "GLOBAL · 1 SLOT"
    );
    // Default "All Planets" scope aggregates resources, storage, hourly rates, and energy across all colonies
    expect(dashRender.container.querySelector("#planet-title")?.textContent).toBe(
      "SECTOR // ALL COLONIES (2)"
    );
    expect(dashRender.container.querySelector("#val-pig")?.textContent).toBe("27,900");
    expect(dashRender.container.querySelector("#val-metal")?.textContent).toBe("12,900");
    expect(dashRender.container.querySelector("#val-kryp")?.textContent).toBe("8,950");
    expect(dashRender.container.querySelector("#val-spice")?.textContent).toBe("4,500");
    expect(dashRender.container.querySelector("#cap-pig")?.textContent).toBe("/ 200,000 (14%)");
    expect(dashRender.container.querySelector("#rate-pig")?.textContent).toBe("+1,550/h");
    expect(dashRender.container.querySelector("#rate-metal")?.textContent).toBe("+720/h");
    expect(dashRender.container.querySelector("#val-energy")?.textContent).toBe("+259");
    expect(dashRender.container.querySelector("#energy-ratio")?.textContent).toBe("720 / 461 E");

    // Clicking Colony 2 chip switches both Building Queue and SEC resources to Colony 2
    fireEvent.click(dashRender.container.querySelector('[data-dash-planet-scope="12453"]')!);
    await waitFor(() => {
      const bldBody = dashRender.container.querySelector("#dash-plan-tbody")?.textContent || "";
      expect(bldBody).toContain("Kryptonite mine");
      expect(bldBody).not.toContain("Pig iron mine");
      // Research stays global even when scoped to Colony 2!
      const resBody =
        dashRender.container.querySelector("#dash-plan-research-tbody")?.textContent || "";
      expect(resBody).toContain("Mining technology");
      expect(dashRender.container.querySelector("#planet-title")?.textContent).toBe(
        "SECTOR // [02:379:05] COLONY 2"
      );
      expect(dashRender.container.querySelector("#val-pig")?.textContent).toBe("2,900");
      expect(dashRender.container.querySelector("#val-metal")?.textContent).toBe("900");
      expect(dashRender.container.querySelector("#val-kryp")?.textContent).toBe("950");
      expect(dashRender.container.querySelector("#val-spice")?.textContent).toBe("500");
      expect(dashRender.container.querySelector("#cap-pig")?.textContent).toBe("/ 100,000 (3%)");
      expect(dashRender.container.querySelector("#rate-pig")?.textContent).toBe("+350/h");
      expect(dashRender.container.querySelector("#val-energy")?.textContent).toBe("+79");
      expect(dashRender.container.querySelector("#energy-ratio")?.textContent).toBe("120 / 41 E");
    });

    // Clicking All Planets chip restores aggregated empire resources
    fireEvent.click(dashRender.container.querySelector('[data-dash-planet-scope="all"]')!);
    await waitFor(() => {
      expect(dashRender.container.querySelector("#planet-title")?.textContent).toBe(
        "SECTOR // ALL COLONIES (2)"
      );
      expect(dashRender.container.querySelector("#val-pig")?.textContent).toBe("27,900");
      expect(dashRender.container.querySelector("#rate-pig")?.textContent).toBe("+1,550/h");
    });
    // Dashboard lower toolbar removes Parallel by Planet, Action Graph, and Full Queue shortcut buttons
    const dashToolbar = dashRender.container.querySelector(".dash-queues-toolbar")!;
    expect(dashToolbar.querySelector("#btn-dash-parallel-queues")).toBeNull();
    expect(dashToolbar.querySelector('[data-goto-page="action-graph"]')).toBeNull();
    expect(dashToolbar.querySelector('[data-goto-page="operations"]')).toBeNull();
    expect(dashToolbar.querySelectorAll(".dash-planet-scope-chips .planet-scope-chip").length).toBe(3);
    dashRender.unmount();

    // 2) Operations removes Unified Table / Parallel by Planet toggle buttons, keeps unified table + full-width planet chips
    const opsRender = render(
      <MultiSeed page="operations">
        <OperationsView />
      </MultiSeed>
    );
    await waitFor(() => {
      expect(opsRender.container.querySelector('[data-ops-planet="12453"]')).not.toBeNull();
    });
    expect(opsRender.container.querySelector('[data-ops-layout="parallel"]')).toBeNull();
    expect(opsRender.container.querySelector('[data-ops-layout="table"]')).toBeNull();
    expect(opsRender.container.querySelector("#ops-plan-table")).not.toBeNull();
    expect(opsRender.container.querySelectorAll(".ops-planet-scope-chips .planet-scope-chip").length).toBe(3);

    fireEvent.click(opsRender.container.querySelector('[data-ops-planet="12453"]')!);
    await waitFor(() => {
      const planBody = opsRender.container.querySelector("#plan-tbody")?.textContent || "";
      expect(planBody).toContain("Kryptonite mine");
      expect(planBody).not.toContain("Pig iron mine");
    });
  });

  it("AppHeader fast scan-suspend button toggles allow_galaxy_scan immediately without racing polls", async () => {
    let liveAllowScan = true;
    const cmds: any[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/command")) {
        const body = JSON.parse(String(init?.body || "{}"));
        cmds.push(body);
        if (body.command === "update_config" && typeof body.config?.allow_galaxy_scan === "boolean") {
          liveAllowScan = body.config.allow_galaxy_scan;
        }
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ ok: true, config: { allow_galaxy_scan: liveAllowScan } }),
        } as Response;
      }
      if (url.includes("/dashboard")) {
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({
              ...MOCK_DASHBOARD,
              config: { ...(MOCK_DASHBOARD.config || {}), allow_galaxy_scan: liveAllowScan },
            }),
        } as Response;
      }
      return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    const ScanSeed: React.FC<{ children: React.ReactNode }> = ({ children }) => {
      const Inner: React.FC = () => {
        const { setDashboard, setActivePage } = useConsole();
        useEffect(() => {
          setDashboard({
            ...MOCK_DASHBOARD,
            config: { ...(MOCK_DASHBOARD.config || {}), allow_galaxy_scan: true },
          });
          setActivePage("dashboard");
        }, [setDashboard, setActivePage]);
        return <>{children}</>;
      };
      return (
        <ConsoleProvider disableInitialFetch>
          <Inner />
        </ConsoleProvider>
      );
    };

    const { container } = render(
      <ScanSeed>
        <AppHeader />
        <FeedbackBanner />
      </ScanSeed>
    );

    await waitFor(() => {
      const btn = container.querySelector("#btn-hud-scan-toggle") as HTMLButtonElement | null;
      expect(btn).not.toBeNull();
      expect(btn?.textContent).toBe("SUSPEND SCANS");
      expect(btn?.getAttribute("aria-pressed")).toBe("false");
      expect(btn?.classList.contains("is-suspended")).toBe(false);
    });

    // 1. Click to suspend scans
    fireEvent.click(container.querySelector("#btn-hud-scan-toggle")!);
    await waitFor(() => {
      const btn = container.querySelector("#btn-hud-scan-toggle") as HTMLButtonElement | null;
      expect(btn?.textContent).toBe("SCANS SUSPENDED");
      expect(btn?.getAttribute("aria-pressed")).toBe("true");
      expect(btn?.classList.contains("is-suspended")).toBe(true);
      expect(container.querySelector("#feedback-banner")?.textContent).toContain("Galaxy scans suspended");
    });
    expect(cmds[0]).toEqual({
      command: "update_config",
      config: { allow_galaxy_scan: false },
    });

    // 2. Click again to resume scans
    fireEvent.click(container.querySelector("#btn-hud-scan-toggle")!);
    await waitFor(() => {
      const btn = container.querySelector("#btn-hud-scan-toggle") as HTMLButtonElement | null;
      expect(btn?.textContent).toBe("SUSPEND SCANS");
      expect(btn?.getAttribute("aria-pressed")).toBe("false");
      expect(btn?.classList.contains("is-suspended")).toBe(false);
      expect(container.querySelector("#feedback-banner")?.textContent).toContain("Galaxy scans resumed");
    });
    expect(cmds[1]).toEqual({
      command: "update_config",
      config: { allow_galaxy_scan: true },
    });
  });

  it("Dashboard multi-planet queues render all planets without 10-row truncation and scope active fleet jobs per planet", async () => {
    const elevenPlanets = Array.from({ length: 11 }, (_, idx) => ({
      planet_id: 13001 + idx,
      name: `Colony ${idx + 1}`,
      coords: `02:380:${String(idx + 1).padStart(2, "0")}`,
      planet_type: "normal",
      temperature: 20,
      fields_used: 40,
      fields_max: 320,
      resources: { pig_iron: 10000, metal: 5000, kryptonite: 4000, spice: 2000 },
      storage: { pig_iron: 200000, metal: 200000, kryptonite: 200000, spice: 200000 },
      energy: { produced: 500, used: 200 },
      economy_rates: { pig_iron: 500, metal: 300, kryptonite: 200, spice: 100 },
      buildings: { b1: 10 },
      ships: {},
    }));
    const manyBuildActions = elevenPlanets.map((p, idx) => ({
      priority: idx < 4 ? "P1" : "P2",
      kind: "BUILD",
      planet_id: p.planet_id,
      planet_coords: p.coords,
      target_id: 7,
      target_name: `Development center`,
      target_level: idx + 2,
      cost: { pig_iron: 1000, metal: 500, kryptonite: 500, spice: 0 },
      missing: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
      reason: `Build on ${p.coords}`,
      executable_now: true,
    }));
    const multiFleetDash: DashboardPayload = {
      ...MOCK_DASHBOARD,
      state: {
        ...MOCK_DASHBOARD.state!,
        active_planet_id: 13001,
        planets: elevenPlanets,
        active_queues: [
          {
            category: "fleet",
            name: "Recycle ↩ [02:379:01] Debris",
            detail: "Returning · 12 Recycler",
            origin_coords: "02:380:01",
            remaining_text: "00:30:00",
            remaining_seconds: 1800,
            cargo: { pig_iron: 16378, metal: 11572, kryptonite: 0, spice: 0 },
          },
          {
            category: "fleet",
            name: "Attack ↩ [02:377:04] Spacia",
            detail: "Returning · 20 Small transport",
            origin_coords: "02:380:02",
            remaining_text: "00:15:00",
            remaining_seconds: 900,
            cargo: { pig_iron: 12500, metal: 8200, kryptonite: 0, spice: 0 },
          },
        ],
      },
      plan: manyBuildActions,
    };

    const ElevenSeed: React.FC<{ children: React.ReactNode }> = ({ children }) => {
      const Inner: React.FC = () => {
        const { setDashboard, setActivePage } = useConsole();
        useEffect(() => {
          setDashboard(multiFleetDash as any);
          setActivePage("dashboard");
        }, [setActivePage, setDashboard]);
        return null;
      };
      return (
        <ConsoleProvider disableInitialFetch>
          <Inner />
          {children}
        </ConsoleProvider>
      );
    };

    const { container } = render(
      <ElevenSeed>
        <DashboardView />
      </ElevenSeed>
    );

    // 1. All Planets view must render ALL 11 planets (no 10-row cap hiding planet 11!)
    await waitFor(() => {
      const bldRows = container.querySelectorAll("#dash-plan-tbody tr");
      expect(bldRows.length).toBe(11);
      expect(container.querySelector("#dash-plan-tbody")?.textContent).toContain("02:380:11");
      // All Planets Fleet Queue shows both empire flights
      expect(container.querySelector("#dash-fleet-q-count")?.textContent).toBe("2");
      expect(container.querySelectorAll("#dash-exec-fleet-tbody tr").length).toBe(2);
    });

    // 2. Clicking Planet 11 (02:380:11, which has 0 flights) must scope Fleet Queue to 0 (NOT 2!)
    fireEvent.click(container.querySelector('[data-dash-planet-scope="13011"]')!);
    await waitFor(() => {
      expect(container.querySelector("#dash-bld-q-count")?.textContent).toBe("1");
      expect(container.querySelector("#dash-fleet-q-count")?.textContent).toBe("0");
      expect(container.querySelectorAll("#dash-exec-fleet-tbody tr").length).toBe(0);
    });

    // 3. Clicking Planet 1 (02:380:01, which has 1 returning recycle flight) shows only its 1 flight + Harvest cargo!
    fireEvent.click(container.querySelector('[data-dash-planet-scope="13001"]')!);
    await waitFor(() => {
      expect(container.querySelector("#dash-fleet-q-count")?.textContent).toBe("1");
      expect(container.querySelectorAll("#dash-exec-fleet-tbody tr").length).toBe(1);
      const fleetTxt = container.querySelector("#dash-exec-fleet-tbody")?.textContent || "";
      expect(fleetTxt).toContain("02:379:01");
      expect(fleetTxt).toContain("Harvest");
      expect(fleetTxt).toContain("16,378 Pig");
      expect(fleetTxt).toContain("11,572 Met");
    });

    // 4. Clicking Planet 2 (02:380:02, returning attack) shows Loot cargo!
    fireEvent.click(container.querySelector('[data-dash-planet-scope="13002"]')!);
    await waitFor(() => {
      const fleetTxt = container.querySelector("#dash-exec-fleet-tbody")?.textContent || "";
      expect(fleetTxt).toContain("02:377:04");
      expect(fleetTxt).toContain("Loot");
      expect(fleetTxt).toContain("12,500 Pig");
      expect(fleetTxt).toContain("8,200 Met");
      expect(fleetTxt).toContain("(20.7k)");
    });
  });

  it("highlights planet chips and colony status green ONLY when currently constructing a building", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    const dash: DashboardPayload = {
      ...MOCK_DASHBOARD,
      state: {
        ...MOCK_DASHBOARD.state!,
        active_planet_id: 12476,
        planets: [
          ...MOCK_DASHBOARD.state!.planets!,
          {
            planet_id: 12453,
            name: "Forge Alpha",
            coords: "02:379:05",
            planet_type: "normal",
            temperature: 28,
            fields_used: 12,
            fields_max: 315,
            resources: { pig_iron: 2900, metal: 900, kryptonite: 950, spice: 500 },
            storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
            energy: { produced: 120, used: 41 },
          },
          {
            planet_id: 12499,
            name: "Spice Outpost",
            coords: "02:381:09",
            planet_type: "normal",
            temperature: 15,
            fields_used: 10,
            fields_max: 300,
            resources: { pig_iron: 5000, metal: 1000, kryptonite: 500, spice: 200 },
            storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
            energy: { produced: 100, used: 30 },
          },
        ],
        active_queues: [
          {
            category: "building",
            name: "Kryptonite mine",
            target_level: 6,
            planet_id: 12453,
            origin_coords: "02:379:05",
            remaining_text: "00:12:00",
            remaining_seconds: 720,
            finish_ts: nowSec + 720,
          },
          {
            category: "fleet",
            name: "Attack [02:390:04]",
            detail: "Outbound · 15 Small fighter",
            origin_coords: "02:381:09",
            remaining_text: "00:10:00",
            remaining_seconds: 600,
            finish_ts: nowSec + 600,
          },
          {
            category: "research",
            name: "Mining technology",
            target_level: 9,
            remaining_text: "00:25:00",
            remaining_seconds: 1500,
            finish_ts: nowSec + 1500,
          },
        ],
      },
      // Planet 12476 and 12499 both have executable_now: true actions, yet must NOT turn green!
      plan: [
        ...MOCK_DASHBOARD.plan!,
        {
          priority: "P1",
          kind: "BUILD",
          planet_id: 12499,
          planet_coords: "02:381:09",
          target_id: 4,
          target_name: "Spice mine",
          target_level: 5,
          cost: { pig_iron: 500 },
          missing: {},
          executable_now: true,
        },
      ],
    };

    const Seed: React.FC<{ children: React.ReactNode }> = ({ children }) => {
      const Inner: React.FC = () => {
        const { setDashboard, setActivePage } = useConsole();
        useEffect(() => {
          setDashboard(dash);
          setActivePage("dashboard");
        }, [setActivePage, setDashboard]);
        return null;
      };
      return (
        <ConsoleProvider disableInitialFetch>
          <Inner />
          {children}
        </ConsoleProvider>
      );
    };

    const { container } = render(
      <Seed>
        <DashboardView />
        <OperationsView />
      </Seed>
    );

    await waitFor(() => {
      expect(container.querySelector('[data-dash-planet-scope="12453"]')).not.toBeNull();
    });

    // Dashboard planet chips: ONLY 12453 is green (.is-constructing); idle planets with ready actions are NOT green
    const dashHome = container.querySelector('[data-dash-planet-scope="12476"]')!;
    const dashForge = container.querySelector('[data-dash-planet-scope="12453"]')!;
    const dashOutpost = container.querySelector('[data-dash-planet-scope="12499"]')!;
    expect(dashForge.classList.contains("is-constructing")).toBe(true);
    expect(dashForge.getAttribute("data-constructing")).toBe("true");
    expect(dashHome.classList.contains("is-constructing")).toBe(false);
    expect(dashHome.classList.contains("has-ready")).toBe(false);
    expect(dashOutpost.classList.contains("is-constructing")).toBe(false);
    expect(dashOutpost.classList.contains("has-ready")).toBe(false);

    // Operations planet chips match the exact same rule
    const opsHome = container.querySelector('[data-ops-planet="12476"]')!;
    const opsForge = container.querySelector('[data-ops-planet="12453"]')!;
    const opsOutpost = container.querySelector('[data-ops-planet="12499"]')!;
    expect(opsForge.classList.contains("is-constructing")).toBe(true);
    expect(opsHome.classList.contains("is-constructing")).toBe(false);
    expect(opsHome.classList.contains("has-ready")).toBe(false);
    expect(opsOutpost.classList.contains("is-constructing")).toBe(false);

    // Empire Colonies table: ONLY constructing colony gets BUILDING + .is-constructing
    const colonyRows = Array.from(container.querySelectorAll("#colony-tbody tr"));
    const forgeRow = colonyRows.find((r) => r.textContent?.includes("Forge Alpha"))!;
    const homeRow = colonyRows.find((r) => r.textContent?.includes("02:380:12"))!;
    expect(forgeRow.querySelector(".status-word")?.textContent).toBe("BUILDING");
    expect(forgeRow.querySelector(".status-word")?.classList.contains("is-constructing")).toBe(true);
    expect(homeRow.querySelector(".status-word")?.textContent).toBe("ONLINE");
    expect(homeRow.querySelector(".status-word")?.classList.contains("is-constructing")).toBe(false);
  });

  it("displays Building Queue resource requests scoped by planet and EXPECTED / DELIVERED badges for ongoing flights", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    const dash: DashboardPayload = {
      ...MOCK_DASHBOARD,
      state: {
        ...MOCK_DASHBOARD.state!,
        active_planet_id: 12476,
        planets: [
          ...MOCK_DASHBOARD.state!.planets!,
          {
            planet_id: 12453,
            name: "Colony Beta",
            coords: "02:379:05",
            planet_type: "normal",
            temperature: 28,
            fields_used: 12,
            fields_max: 315,
            resources: { pig_iron: 2900, metal: 900, kryptonite: 950, spice: 500 },
            storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
            energy: { produced: 120, used: 41 },
          },
        ],
        active_queues: [
          {
            category: "fleet",
            name: "Attack [02:390:04] Spacia",
            detail: "Outbound · 20 Small fighter",
            origin_coords: "02:380:12",
            returning: false,
            remaining_text: "00:18:00",
            remaining_seconds: 1080,
            finish_ts: nowSec + 1080,
            expected_cargo: { pig_iron: 14000, metal: 6500, kryptonite: 2000, spice: 500 },
          },
          {
            category: "fleet",
            name: "Recycle [02:379:01] Debris",
            detail: "Outbound · 10 Recycler",
            origin_coords: "02:380:12",
            returning: false,
            remaining_text: "00:22:00",
            remaining_seconds: 1320,
            finish_ts: nowSec + 1320,
            expected_cargo: { pig_iron: 9000, metal: 4500, kryptonite: 0, spice: 0 },
          },
          {
            category: "fleet",
            name: "Transport [02:379:05] Colony Beta",
            detail: "Outbound · 5 Large transport",
            origin_coords: "02:380:12",
            returning: false,
            remaining_text: "00:08:00",
            remaining_seconds: 480,
            finish_ts: nowSec + 480,
            expected_cargo: { pig_iron: 12000, metal: 8000, kryptonite: 0, spice: 0 },
          },
          {
            category: "fleet",
            name: "Transport ↩ [02:379:05] Colony Beta",
            detail: "Returning · 5 Large transport",
            origin_coords: "02:380:12",
            returning: true,
            delivered: true,
            remaining_text: "00:05:00",
            remaining_seconds: 300,
            finish_ts: nowSec + 300,
            delivered_cargo: { pig_iron: 11000, metal: 7500, kryptonite: 1200, spice: 0 },
          },
          {
            category: "fleet",
            name: "Transport ↩ [02:380:12] Colony",
            detail: "Returning · 2 Small transport",
            origin_coords: "02:379:05",
            returning: true,
            delivered: true,
            remaining_text: "00:04:00",
            remaining_seconds: 240,
            finish_ts: nowSec + 240,
            delivered_cargo: null,
          },
        ],
      },
      building_resource_requests: [
        {
          id: "req-1",
          planet_id: 12453,
          planet_coords: "02:379:05",
          planet_name: "Colony Beta",
          target_building: "Development center",
          target_level: 10,
          status: "in_flight",
          resources: { pig_iron: 12000, metal: 8000, kryptonite: 0, spice: 0 },
          source_planet_id: 12476,
          source_coords: "02:380:12",
          source_name: "Colony",
          eta_seconds: 480,
          finish_ts: nowSec + 480,
        },
        {
          id: "req-2",
          planet_id: 12476,
          planet_coords: "02:380:12",
          planet_name: "Colony",
          target_building: "Fusion plant",
          target_level: 11,
          status: "requested",
          resources: { pig_iron: 5000, metal: 0, kryptonite: 3200, spice: 400 },
          reason: "Waiting for donor colony surplus",
        },
      ],
    };

    const Seed: React.FC<{ children: React.ReactNode }> = ({ children }) => {
      const Inner: React.FC = () => {
        const { setDashboard, setActivePage } = useConsole();
        useEffect(() => {
          setDashboard(dash);
          setActivePage("dashboard");
        }, [setActivePage, setDashboard]);
        return null;
      };
      return (
        <ConsoleProvider disableInitialFetch>
          <Inner />
          {children}
        </ConsoleProvider>
      );
    };

    const { container } = render(
      <Seed>
        <DashboardView />
      </Seed>
    );

    await waitFor(() => {
      expect(container.querySelector("#dash-building-resource-requests")).not.toBeNull();
    });

    // Building Queue resource requests show both requests under All Planets
    expect(container.querySelector("#dash-bld-req-count")?.textContent).toBe("2 REQ");
    const reqBox = container.querySelector("#dash-building-resource-requests")!;
    expect(reqBox.textContent).toContain("IN FLIGHT");
    expect(reqBox.textContent).toContain("Development center → L10");
    expect(reqBox.textContent).toContain("[02:380:12] → [02:379:05]");
    expect(reqBox.textContent).toContain("12,000 Pig");
    expect(reqBox.textContent).toContain("8,000 Met");
    expect(reqBox.textContent).toContain("REQUESTED");
    expect(reqBox.textContent).toContain("Fusion plant → L11");
    expect(reqBox.textContent).toContain("3,200 Kryp");

    // Scope to Colony Beta (12453) -> only Colony Beta's request remains
    fireEvent.click(container.querySelector('[data-dash-planet-scope="12453"]')!);
    await waitFor(() => {
      expect(container.querySelector("#dash-bld-req-count")?.textContent).toBe("1 REQ");
      const scopedText = container.querySelector("#dash-building-resource-requests")?.textContent || "";
      expect(scopedText).toContain("Development center → L10");
      expect(scopedText).not.toContain("Fusion plant → L11");
    });

    // Switch back to All Planets to inspect all 5 ongoing fleet flights
    fireEvent.click(container.querySelector('[data-dash-planet-scope="all"]')!);
    await waitFor(() => {
      expect(container.querySelectorAll("#dash-exec-fleet-tbody tr").length).toBe(5);
    });
    const fleetRows = Array.from(container.querySelectorAll("#dash-exec-fleet-tbody tr"));
    const attackRow = fleetRows.find((r) => r.textContent?.includes("02:390:04"))!;
    const recycleRow = fleetRows.find((r) => r.textContent?.includes("02:379:01"))!;
    const outTrRow = fleetRows.find(
      (r) => r.textContent?.includes("Transport [02:379:05]") && !r.textContent?.includes("↩")
    )!;
    const delivRow = fleetRows.find((r) => r.textContent?.includes("Transport ↩ [02:379:05]"))!;
    const delivNullRow = fleetRows.find((r) => r.textContent?.includes("Transport ↩ [02:380:12]"))!;

    expect(attackRow.querySelector('[data-cargo-variant="expected"]')?.textContent).toContain("EXPECTED");
    expect(attackRow.textContent).toContain("14,000 Pig");
    expect(attackRow.textContent).toContain("6,500 Met");
    expect(recycleRow.querySelector('[data-cargo-variant="expected"]')?.textContent).toContain("EXPECTED");
    expect(recycleRow.textContent).toContain("9,000 Pig");
    expect(outTrRow.querySelector('[data-cargo-variant="expected"]')?.textContent).toContain("EXPECTED");
    expect(outTrRow.textContent).toContain("12,000 Pig");

    expect(delivRow.querySelector('[data-cargo-variant="delivered"]')?.getAttribute("data-delivered")).toBe("true");
    expect(delivRow.textContent).toContain("DELIVERED");
    expect(delivRow.textContent).toContain("11,000 Pig");
    expect(delivRow.textContent).toContain("7,500 Met");
    expect(delivRow.textContent).toContain("1,200 Kryp");

    expect(delivNullRow.querySelector('[data-cargo-variant="delivered"]')?.textContent).toContain("DELIVERED");
    expect(delivNullRow.textContent).toContain("Cargo unloaded");

    // Active queues strip also surfaces EXPECTED and DELIVERED
    const stripText = container.querySelector("#active-queues-list")?.textContent || "";
    expect(stripText).toContain("EXPECTED:");
    expect(stripText).toContain("DELIVERED:");
  });

  it("moves Active Colony selector to AppHeader, Execution Mode to ConfigView, wires Research & Fleet Build / Fleet Control nav, and supports renamePlanet API", async () => {
    const customDash: DashboardPayload = {
      ...MOCK_DASHBOARD,
      state: {
        ...MOCK_DASHBOARD.state!,
        planets: [
          {
            ...MOCK_DASHBOARD.state!.planets![0],
            name: "Citadel Prime",
            game_name: "Colony",
            custom_name: "Citadel Prime",
          },
          {
            planet_id: 12453,
            name: "Forge Nine",
            coords: "02:379:05",
            planet_type: "normal",
            temperature: 14,
            fields_used: 20,
            fields_max: 310,
          },
        ],
        active_queues: [
          {
            category: "fleet",
            name: "Attack [02:390:04]",
            detail: "Outbound · 10 Small fighter",
            origin_coords: "02:380:12",
            remaining_seconds: 400,
          },
          {
            category: "fleet",
            name: "Transport [02:379:05]",
            detail: "Outbound · 4 Large transport",
            origin_coords: "02:380:12",
            remaining_seconds: 200,
          },
          {
            category: "fleet",
            name: "Small transport",
            detail: "Shipyard (40%)",
            origin_coords: "02:380:12",
            remaining_seconds: 150,
          },
          {
            category: "fleet",
            name: "Laser turret",
            detail: "Weapons factory",
            origin_coords: "02:380:12",
            remaining_seconds: 90,
          },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(customDash),
          };
        }
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        postedCommands.push(body);
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ ok: true }),
        };
      })
    );

    const ShellSeed: React.FC<{ children: React.ReactNode }> = ({ children }) => {
      const Inner: React.FC = () => {
        const { setDashboard, setActivePage } = useConsole();
        useEffect(() => {
          setDashboard(customDash);
          setActivePage("dashboard");
        }, [setActivePage, setDashboard]);
        return null;
      };
      return (
        <ConsoleProvider disableInitialFetch>
          <Inner />
          {children}
        </ConsoleProvider>
      );
    };

    const { container } = render(
      <ShellSeed>
        <AppSidebar />
        <AppHeader />
        <DashboardView />
        <ConfigView />
      </ShellSeed>
    );

    await waitFor(() => {
      expect(container.querySelector("header.hud-ruler #planet-select")).not.toBeNull();
    });

    // 1. Active Colony selector is in AppHeader, NOT AppSidebar, and reflects custom planet name
    const sidebar = container.querySelector("#app-sidebar")!;
    const header = container.querySelector("header.hud-ruler")!;
    expect(sidebar.querySelector("#planet-select")).toBeNull();
    const planetSelect = header.querySelector("#planet-select") as HTMLSelectElement;
    expect(planetSelect.textContent).toContain("02:380:12 Citadel Prime");
    expect(planetSelect.textContent).toContain("02:379:05 Forge Nine");
    fireEvent.change(planetSelect, { target: { value: "12453" } });
    await waitFor(() => {
      expect(header.querySelector("#planet-specs")?.textContent).toContain("TEMP 14°C");
      expect(header.querySelector("#planet-specs")?.textContent).toContain("20/310");
    });

    // 2. Execution Mode is in ConfigView, NOT AppSidebar, and sends bot_control on click
    expect(sidebar.querySelector("#mode-group-label")).toBeNull();
    expect(sidebar.querySelector('[data-mode="dry-run"]')).toBeNull();
    const configView = container.querySelector("#view-config")!;
    expect(configView.querySelector("#mode-group-label")?.textContent).toBe("Execution Mode");
    const dryRunBtn = configView.querySelector('[data-mode="dry-run"]') as HTMLButtonElement;
    expect(dryRunBtn).not.toBeNull();
    fireEvent.click(dryRunBtn);
    await waitFor(() => {
      expect(
        postedCommands.some((c) => c.command === "bot_control" && c.mode_override === "dry-run")
      ).toBe(true);
      // FeedbackBanner renders at the bottom of AppSidebar and shows the live feedback message
      const sidebarBanner = sidebar.querySelector("#feedback-banner") as HTMLElement | null;
      expect(sidebarBanner).not.toBeNull();
      expect(sidebarBanner?.hidden).toBe(false);
      expect(sidebarBanner?.textContent).toContain("Loop mode set to dry-run.");
    });

    // 3. Sidebar navigation has Research & ETAs, Fleet Build, and Fleet Control with live counts
    const resNav = sidebar.querySelector('[data-page="research"]')!;
    const buildNav = sidebar.querySelector('[data-page="combat"]')!;
    const fltCtrlNav = sidebar.querySelector('[data-page="fleet-control"]')!;
    expect(resNav.textContent).toContain("Research & ETAs");
    expect(sidebar.querySelector("#nav-res-badge")?.textContent).toBe("15");
    expect(buildNav.textContent).toContain("Fleet Build");
    expect(buildNav.textContent).not.toContain("Combat & Fleet");
    expect(fltCtrlNav.textContent).toContain("Fleet Control");
    // Only the 2 real ongoing flights count; Shipyard (40%) and Weapons factory jobs under category="fleet" are excluded
    expect(sidebar.querySelector("#nav-flt-ctrl-badge")?.textContent).toBe("2");

    // 4. Header titles update for research, combat (Fleet Build), and fleet-control (Fleet Control)
    fireEvent.click(buildNav);
    await waitFor(() => {
      expect(header.querySelector("#hud-page-title")?.textContent).toBe(
        "FLEET BUILD // SHIPYARD & SIM"
      );
    });
    fireEvent.click(fltCtrlNav);
    await waitFor(() => {
      expect(header.querySelector("#hud-page-title")?.textContent).toBe(
        "FLEET CONTROL // CURRENT FLIGHTS"
      );
    });

    // 5. Dashboard Research Queue jump button navigates directly to top-level "research" page
    fireEvent.click(sidebar.querySelector('[data-page="dashboard"]')!);
    await waitFor(() => {
      expect(header.querySelector("#hud-page-title")?.textContent).toBe("DASHBOARD // OVERVIEW");
    });
    fireEvent.click(container.querySelector("#btn-dash-goto-research")!);
    await waitFor(() => {
      expect(header.querySelector("#hud-page-title")?.textContent).toBe(
        "RESEARCH // LEVEL ETAS"
      );
    });

    // 6. renamePlanet helper dispatches rename_planet command with exact payload
    await renamePlanet(12476, "Citadel Prime");
    expect(
      postedCommands.some(
        (c) => c.command === "rename_planet" && c.planet_id === 12476 && c.name === "Citadel Prime"
      )
    ).toBe(true);

    // 7. ConfigView renders spy_reserved_slots_only and ai_adaptive_execution toggles and saves via update_config
    const adaptiveCheckbox = configView.querySelector(
      "#cfg-ai_adaptive_execution"
    ) as HTMLInputElement | null;
    expect(adaptiveCheckbox).not.toBeNull();
    expect(adaptiveCheckbox?.checked).toBe(false);
    const adaptiveItem = adaptiveCheckbox!.closest(".config-item")!;
    expect(adaptiveItem.querySelector(".config-item-key")?.textContent).toBe(
      "ai_adaptive_execution"
    );
    expect(adaptiveItem.querySelector(".config-item-bounds")?.textContent).toContain(
      "Boolean toggle"
    );
    expect(
      (adaptiveItem.querySelector(".config-item-desc")?.textContent || "").length
    ).toBeGreaterThan(80);
    expect(
      configView.querySelector('[aria-labelledby="cfg-grp-doctrine"] #cfg-ai_adaptive_execution')
    ).not.toBeNull();
    fireEvent.click(adaptiveCheckbox!);
    expect(adaptiveCheckbox?.checked).toBe(true);

    const spyResOnlyCheckbox = configView.querySelector(
      "#cfg-spy_reserved_slots_only"
    ) as HTMLInputElement | null;
    expect(spyResOnlyCheckbox).not.toBeNull();
    expect(spyResOnlyCheckbox?.checked).toBe(false);
    fireEvent.click(spyResOnlyCheckbox!);
    expect(spyResOnlyCheckbox?.checked).toBe(true);
    const pvLimitInput = configView.querySelector(
      "#cfg-page_view_limit_per_minute"
    ) as HTMLInputElement | null;
    expect(pvLimitInput).not.toBeNull();
    expect(pvLimitInput?.value).toBe("60");
    fireEvent.change(pvLimitInput!, { target: { value: "48" } });
    const saveCfgBtn = configView.querySelector("#btn-save-config") as HTMLButtonElement;
    fireEvent.click(saveCfgBtn);
    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) =>
            c.command === "update_config" &&
            c.config?.spy_reserved_slots_only === true &&
            c.config?.ai_adaptive_execution === true &&
            c.config?.page_view_limit_per_minute === 48
        )
      ).toBe(true);
    });
  });

  it("never duplicates or orphans EXEC building rows across multi-planet Pig iron mine reorders and planet scoping", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const colonies = [
        { planet_id: 12401, name: "#TC Planet", coords: "02:379:04", planet_type: "normal", temperature: 18, fields_used: 45, fields_max: 300 },
        { planet_id: 12402, name: "#TB Planet", coords: "02:380:06", planet_type: "normal", temperature: 22, fields_used: 44, fields_max: 300 },
        { planet_id: 12403, name: "#TT Planet", coords: "02:379:05", planet_type: "normal", temperature: 15, fields_used: 40, fields_max: 300 },
        { planet_id: 12404, name: "#TD Planet", coords: "02:381:10", planet_type: "normal", temperature: 25, fields_used: 38, fields_max: 300 },
      ];

      const jobP1: QueueJobData = {
        category: "building",
        name: "Pig iron mine",
        building_id: 1,
        target_level: 23,
        queue_slot: 1,
        detail: "In construction (1%)",
        origin_coords: "02:379:04",
        remaining_seconds: 3900,
        finish_ts: nowSec + 3900,
      };
      const jobP2: QueueJobData = {
        category: "building",
        name: "Pig iron mine",
        building_id: 1,
        target_level: 22,
        queue_slot: 1,
        detail: "In construction (1%)",
        origin_coords: "02:380:06",
        remaining_seconds: 3180,
        finish_ts: nowSec + 3180,
      };
      const jobP3: QueueJobData = {
        category: "building",
        name: "Development center",
        building_id: 7,
        target_level: 9,
        queue_slot: 1,
        detail: "In construction (55%)",
        origin_coords: "02:379:05",
        remaining_seconds: 540,
        finish_ts: nowSec + 540,
      };
      const jobP4: QueueJobData = {
        category: "building",
        name: "Pig iron mine",
        building_id: 1,
        target_level: 20,
        queue_slot: 1,
        detail: "In construction (1%)",
        origin_coords: "02:381:10",
        remaining_seconds: 1200,
        finish_ts: nowSec + 1200,
      };
      const expiredStaleJob: QueueJobData = {
        category: "building",
        name: "Pig iron mine",
        building_id: 1,
        target_level: 19,
        queue_slot: 1,
        detail: "In construction (100%)",
        origin_coords: "02:381:10",
        remaining_seconds: 0,
        finish_ts: nowSec - 45,
      };
      const duplicateSlotJob: QueueJobData = {
        category: "building",
        name: "Pig iron mine",
        building_id: 1,
        target_level: 22,
        queue_slot: 1,
        detail: "In construction (0%)",
        origin_coords: "02:379:04",
        remaining_seconds: 3899,
        finish_ts: nowSec + 3899,
      };

      const makeDash = (activePid: number, queues: QueueJobData[]): DashboardPayload => ({
        ...MOCK_DASHBOARD,
        state: {
          ...MOCK_DASHBOARD.state!,
          active_planet_id: activePid,
          planets: colonies as any,
          active_queues: queues,
        },
        plan: [
          {
            priority: "P1",
            kind: "BUILD",
            planet_id: 12401,
            planet_coords: "02:379:04",
            target_id: 1,
            target_name: "Pig iron mine",
            target_level: 24,
            cost: { pig_iron: 50000, metal: 25000 },
            missing: { metal: 5000 },
            executable_now: false,
          },
        ],
      });

      let pushUpdate!: (dash: DashboardPayload) => void;
      const Harness: React.FC = () => {
        const { setDashboard, setActivePage } = useConsole();
        pushUpdate = setDashboard;
        useEffect(() => {
          setDashboard(makeDash(12401, [jobP1, jobP2, jobP3, jobP4, expiredStaleJob, duplicateSlotJob]));
          setActivePage("dashboard");
        }, [setActivePage, setDashboard]);
        return <DashboardView />;
      };

      const { container } = render(
        <ConsoleProvider disableInitialFetch>
          <Harness />
        </ConsoleProvider>
      );

      // All Planets: exactly 4 active building jobs (expired & duplicate-slot suppressed), each with custom name + [coords]
      await waitFor(() => {
        const execRows = container.querySelectorAll("#dash-exec-building-tbody tr");
        expect(execRows.length).toBe(4);
      });
      expect(container.querySelector("#dash-exec-building-tbody")?.textContent).toContain(
        "#TC Planet [02:379:04]"
      );
      expect(container.querySelector("#dash-exec-building-tbody")?.textContent).toContain(
        "#TB Planet [02:380:06]"
      );
      expect(container.querySelector("#dash-plan-tbody")?.textContent).toContain(
        "BUILD · #TC Planet [02:379:04]"
      );

      // Simulate multiple commander planet-switch polls reordering identical "Pig iron mine" jobs
      pushUpdate(makeDash(12403, [jobP3, jobP2, jobP1, jobP4]));
      await waitFor(() => {
        expect(container.querySelectorAll("#dash-exec-building-tbody tr").length).toBe(4);
      });
      pushUpdate(makeDash(12402, [jobP2, jobP4, jobP3, jobP1]));
      await waitFor(() => {
        expect(container.querySelectorAll("#dash-exec-building-tbody tr").length).toBe(4);
      });
      pushUpdate(makeDash(12404, [jobP4, jobP1, jobP2, jobP3]));
      await waitFor(() => {
        expect(container.querySelectorAll("#dash-exec-building-tbody tr").length).toBe(4);
      });

      // Scope to 02:379:04 (#TC Planet): scope badge shows "#TC Planet [02:379:04]", count is 2, and EXEC tbody has ONLY 1 row!
      fireEvent.click(container.querySelector('[data-dash-planet-scope="12401"]')!);
      await waitFor(() => {
        expect(container.querySelector("#dash-bld-q-count")?.textContent).toBe("2");
        expect(
          container.querySelector("#dash-queue-card-building .dash-q-scope")?.textContent
        ).toBe("#TC Planet [02:379:04]");
        const scopedRows = container.querySelectorAll("#dash-exec-building-tbody tr");
        expect(scopedRows.length).toBe(1);
        expect(scopedRows[0].textContent).toContain("Pig iron mine → L23");
        expect(scopedRows[0].textContent).toContain("#TC Planet [02:379:04]");
      });

      // Zero React duplicate-key warnings
      const keyWarnings = consoleErrorSpy.mock.calls
        .map((args) => args.map(String).join(" "))
        .filter((msg) => msg.includes("Encountered two children with the same key"));
      expect(keyWarnings).toEqual([]);
    } finally {
      consoleErrorSpy.mockRestore();
    }
  });
});
