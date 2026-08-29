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
        <ConfigView />
      </SeedProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll(".config-group").length).toBeGreaterThanOrEqual(5);
    });

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
});
