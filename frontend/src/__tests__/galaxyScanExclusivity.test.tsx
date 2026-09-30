import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../context/ConsoleContext";
import { FeedbackBanner } from "../components/layout/FeedbackBanner";
import { AppSidebar } from "../components/layout/AppSidebar";
import { ConfigView } from "../features/config";
import { BuildingsView } from "../features/buildings";
import { DashboardPayload } from "../types/api";

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
    allow_galaxy_scan: true,
    galaxy_scan_block_other_actions: true,
    allow_multi_planet: true,
    max_planets: 11,
    min_colony_fields: 300,
  },
  state: {
    universe_id: 31,
    user_id: 103603,
    nickname: "cmdr",
    active_planet_id: 101,
    planets: [
      {
        planet_id: 101,
        name: "Homeworld",
        coords: "02:380:01",
        planet_type: "normal",
        temperature: 25,
        fields_used: 30,
        fields_max: 320,
        resources: { pig_iron: 50000, metal: 25000, kryptonite: 15000, spice: 8000 },
        storage: { pig_iron: 500000, metal: 500000, kryptonite: 500000, spice: 500000 },
        energy: { produced: 600, used: 300, net: 300 },
        buildings: { b1: 10, b2: 8, b5: 10 },
        ships: { s10: 5 },
      },
    ],
    researches: { r1: 4, r2: 6 },
    active_queues: [],
  },
  buildings_eta: {
    planet: { planet_id: 101, name: "Homeworld", coords: "02:380:01" },
    rows: [
      {
        key: "b1",
        building_id: 1,
        name: "Pig iron mine",
        category: "resources",
        current_level: 10,
        next_level: 11,
        can_build_now: true,
        cost: { pig_iron: 1200, metal: 500, kryptonite: 0, spice: 0 },
        missing: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
        wait_eta: "Ready",
        build_time_text: "1m 00s",
      },
    ],
  },
  plan: [],
};

function SeedHarness({
  dash = MOCK_DASHBOARD,
  page = "config",
  children,
}: {
  dash?: DashboardPayload;
  page?: string;
  children: React.ReactNode;
}) {
  const Inner = () => {
    const { setDashboard, setActivePage, setBuildingsEtaData } = useConsole();
    useEffect(() => {
      setDashboard(dash);
      if (dash.buildings_eta) {
        setBuildingsEtaData(dash.buildings_eta);
      }
      setActivePage(page);
    }, [setActivePage, setBuildingsEtaData, setDashboard]);
    return null;
  };
  return (
    <ConsoleProvider disableInitialFetch>
      <Inner />
      <FeedbackBanner />
      {children}
    </ConsoleProvider>
  );
}

describe("Galaxy Scan Exclusivity UI & HTTP Handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  it("exposes galaxy_scan_block_other_actions in ConfigView (default checked) and saves updates", async () => {
    const sentPayloads: any[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      sentPayloads.push(body);
      return new Response(
        JSON.stringify({
          ok: true,
          command: "update_config",
          config: { ...MOCK_DASHBOARD.config, ...(body.config || {}) },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    });

    const { container } = render(
      <SeedHarness page="config">
        <ConfigView />
      </SeedHarness>
    );

    const checkbox = await waitFor(() => {
      const el = container.querySelector(
        "#cfg-galaxy_scan_block_other_actions"
      ) as HTMLInputElement | null;
      expect(el).not.toBeNull();
      return el!;
    });
    expect(checkbox.checked).toBe(true);

    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);

    const saveBtn = container.querySelector("#btn-save-config") as HTMLButtonElement;
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(sentPayloads.some((p) => p.command === "update_config")).toBe(true);
    });
    const upd = sentPayloads.find((p) => p.command === "update_config");
    expect(upd.config.galaxy_scan_block_other_actions).toBe(false);
  });

  it("shows amber warning banner on HTTP 409 sweep_blocked commands and deferred live refreshes", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      const urlStr = String(url);
      if (urlStr.includes("/dashboard?refresh=1")) {
        return new Response(
          JSON.stringify({
            ...MOCK_DASHBOARD,
            sweep_deferred_refresh: true,
            sweep_message:
              "Galaxy scan in progress — wait for scan to finish, or Pause / disable Galaxy Scan first.",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (urlStr.includes("/command")) {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        if (body.command === "navigate" || body.command === "SyncEconomy" || body.command === "build") {
          return new Response(
            JSON.stringify({
              ok: false,
              sweep_in_progress: true,
              sweep_blocked: true,
              error:
                "Galaxy scan in progress — wait for scan to finish, or Pause / disable Galaxy Scan first.",
            }),
            { status: 409, headers: { "Content-Type": "application/json" } }
          );
        }
      }
      return new Response(JSON.stringify(MOCK_DASHBOARD), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const { container } = render(
      <SeedHarness page="buildings">
        <AppSidebar />
        <BuildingsView />
      </SeedHarness>
    );

    // 1. Clicking Sync Economy inside BuildingsView triggers HTTP 409 -> amber is-warn banner (not red error)
    const syncEconBtn = await waitFor(() => {
      const el = container.querySelector("#btn-sync-economy") as HTMLButtonElement | null;
      expect(el).not.toBeNull();
      return el!;
    });
    fireEvent.click(syncEconBtn);

    await waitFor(() => {
      const banner = container.querySelector("#feedback-banner") as HTMLElement | null;
      expect(banner).not.toBeNull();
      expect(banner!.hidden).toBe(false);
      expect(banner!.className).toContain("is-warn");
      expect(banner!.textContent || "").toContain("Galaxy scan in progress");
    });

    // 2. Clicking Sync Snapshot in AppSidebar receives sweep_deferred_refresh -> keeps amber warning (not green ok)
    const refreshBtn = container.querySelector("#btn-refresh") as HTMLButtonElement | null;
    if (refreshBtn) {
      fireEvent.click(refreshBtn);
      await waitFor(() => {
        const banner = container.querySelector("#feedback-banner") as HTMLElement | null;
        expect(banner!.className).toContain("is-warn");
        expect(banner!.textContent || "").toContain("Galaxy scan in progress");
      });
    }
  });
});
