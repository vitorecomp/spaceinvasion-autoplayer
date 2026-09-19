import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../context/ConsoleContext";
import { DashboardPayload } from "../types/api";
import { DashboardView } from "../features/dashboard";
import { OperationsView } from "../features/operations";
import { BuildingsView } from "../features/buildings";
import { LogsView } from "../features/logs";
import { ConfigView } from "../features/config";
import { ActionGraphView } from "../features/action-graph";
import { HelpAlertModal } from "../components/modals/HelpAlertModal";
import { NotificationCenterModal } from "../components/modals/NotificationCenterModal";
import { FeedbackBanner } from "../components/layout/FeedbackBanner";
import { AppHeader } from "../components/layout/AppHeader";

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
  logs: {
    commander: "Commander tick OK",
    game_api: "API listening on :8787",
  },
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

  it("renders DashboardView 3 queues, OperationsView, BuildingsView universe_research tab, and LogsView", async () => {
    const { container } = render(
      <SeedProvider page="dashboard">
        <DashboardView />
        <OperationsView />
        <BuildingsView />
        <LogsView />
      </SeedProvider>
    );

    await waitFor(() => {
      expect(container.querySelector("#dash-plan-tbody")?.textContent).toContain("Pig iron mine");
    });
    expect(container.querySelector("#dash-queue-card-building")).not.toBeNull();
    expect(container.querySelector("#dash-queue-card-fleet")).not.toBeNull();
    expect(container.querySelector("#dash-queue-card-research")).not.toBeNull();
    expect(container.querySelector("#plan-tbody")?.textContent).toContain("Pig iron mine");
    expect(container.querySelector("#logs-pre")?.textContent).toContain("Commander tick OK");

    const uniTab = container.querySelector('[data-bcat="universe_research"]')!;
    expect(uniTab).not.toBeNull();
    fireEvent.click(uniTab);
    await waitFor(() => {
      expect(container.querySelector("#view-buildings")?.textContent).toContain("StarLord");
    });
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

  it("supports per-planet parallel queue filtering and scoped resources on Dashboard and Parallel by Planet layout on Operations", async () => {
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
    dashRender.unmount();

    // 2) Operations Parallel by Planet layout splits planets into independent side-by-side cards
    const opsRender = render(
      <MultiSeed page="operations">
        <OperationsView />
      </MultiSeed>
    );
    await waitFor(() => {
      expect(opsRender.container.querySelector('[data-ops-layout="parallel"]')).not.toBeNull();
    });
    // Default layout keeps unified #ops-plan-table intact
    expect(opsRender.container.querySelector("#ops-plan-table")).not.toBeNull();
    fireEvent.click(opsRender.container.querySelector('[data-ops-layout="parallel"]')!);
    await waitFor(() => {
      expect(opsRender.container.querySelector('[data-planet-lane="12476"]')?.textContent).toContain(
        "Pig iron mine"
      );
      expect(opsRender.container.querySelector('[data-planet-lane="12453"]')?.textContent).toContain(
        "Kryptonite mine"
      );
      expect(
        opsRender.container.querySelector('[data-planet-lane="global-research"]')?.textContent
      ).toContain("Mining technology");
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
    const multiFleetDash: DashboardData = {
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
});
