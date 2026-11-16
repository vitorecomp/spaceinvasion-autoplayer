import React from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppHeader } from "../../components/layout/AppHeader";
import { FeedbackBanner } from "../../components/layout/FeedbackBanner";
import { ConsoleProvider } from "../../store";
import { BuildingsView } from "../buildings";
import { OperationsView } from "../operations";
import { DashboardView } from "./DashboardView";
import {
  SeededConsole,
  createBaseDashboard,
  installMockFetch,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

describe("DashboardView ETA rendering, Blast Furnace controls, Sim actions, queues & alerts", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("renders Wait ETA, Total ETA, blocked/wait pills, legacy null fallbacks, and empty-queue matching colSpan", async () => {
    const dashWithItems = createBaseDashboard({
      plan: [
        {
          priority: "P1",
          kind: "BUILD",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 1,
          target_name: "Pig iron mine",
          target_level: 9,
          executable_now: false,
          wait_hours: null,
          wait_eta: "—",
          duration_text: "4m 12s",
          total_eta: "—",
          eta_bottleneck: "pig_iron",
          reason: "Starving pig iron",
        },
        {
          priority: "P2",
          kind: "BUILD",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 3,
          target_name: "Kryptonite mine",
          target_level: 7,
          executable_now: false,
          wait_hours: 1.5,
          wait_eta: "1.5h",
          duration_text: "8m 00s",
          total_eta: "1.6h",
          eta_bottleneck: "metal",
          reason: "Wait for metal",
        },
        {
          priority: "P2",
          kind: "BUILD",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 4,
          target_name: "Spice mine",
          target_level: 6,
          executable_now: true,
          // Legacy payload omitting ETA fields completely
          reason: "Legacy action without ETA fields",
        },
      ],
    });

    const { container, unmount } = render(
      <SeededConsole page="dashboard" dashboard={dashWithItems}>
        <DashboardView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#dash-plan-tbody tr").length).toBe(3);
    });

    const tbodyText = container.querySelector("#dash-plan-tbody")!.textContent || "";
    expect(tbodyText).not.toContain("undefined");
    expect(tbodyText).not.toContain("null");
    expect(tbodyText).toContain("— (PIG)");
    expect(tbodyText).toContain("1.5h (MET)");
    expect(container.querySelector("#dash-plan-tbody .eta-pill.is-blocked")).not.toBeNull();
    expect(container.querySelector("#dash-plan-tbody .eta-pill.is-wait")).not.toBeNull();
    expect(container.querySelector("#dash-eta-tbody")).toBeNull();

    unmount();

    // Empty queue renders single placeholder cell whose colSpan matches thead th count
    const emptyDash = createBaseDashboard({ plan: [] });
    const emptyView = render(
      <SeededConsole page="dashboard" dashboard={emptyDash}>
        <DashboardView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(emptyView.container.querySelector("#dash-plan-tbody td.empty-cell")).not.toBeNull();
    });
    const thCount = emptyView.container.querySelectorAll("#dash-queue-card-building thead th").length;
    const emptyTd = emptyView.container.querySelector("#dash-plan-tbody td.empty-cell") as HTMLTableCellElement;
    expect(thCount).toBe(5);
    expect(emptyTd.colSpan).toBe(thCount);
  });

  it("splits Building, Fleet, and Research queues cleanly and routes + Queue jump presets", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    const dash = createBaseDashboard({
      state: {
        ...createBaseDashboard().state,
        active_queues: [
          {
            category: "building",
            name: "Fusion plant",
            building_id: 5,
            target_level: 23,
            planet_id: 12476,
            origin_coords: "02:380:12",
            queue_slot: 1,
            remaining_seconds: 600,
            finish_ts: nowSec + 600,
          },
          {
            category: "building",
            name: "Pig iron warehouse",
            building_id: 11,
            target_level: 5,
            planet_id: 12476,
            origin_coords: "02:380:12",
            queue_slot: 2,
            remaining_seconds: 1200,
            finish_ts: nowSec + 1200,
          },
          {
            category: "fleet",
            name: "Attack -> [02:378:15] Colony",
            planet_id: 12476,
            origin_coords: "02:380:12",
            remaining_seconds: 400,
            finish_ts: nowSec + 400,
          },
          {
            category: "research",
            name: "Mining technology",
            target_level: 9,
            remaining_seconds: 900,
            finish_ts: nowSec + 900,
          },
        ],
      },
      plan: [
        {
          priority: "P2",
          kind: "BUILD",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 4,
          target_name: "Spice mine",
          target_level: 6,
          executable_now: true,
          wait_eta: "Ready",
          total_eta: "3m",
        },
        {
          priority: "P2",
          kind: "BUILD_SHIP",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 10,
          target_name: "Spy probe",
          target_level: 5,
          executable_now: true,
          wait_eta: "Ready",
          total_eta: "1m",
        },
        {
          priority: "P2",
          kind: "RESEARCH",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 1,
          target_name: "Mining technology",
          target_level: 10,
          executable_now: true,
          wait_eta: "Ready",
          total_eta: "10m",
        },
      ],
    });

    const { container } = render(
      <SeededConsole page="dashboard" dashboard={dash}>
        <DashboardView />
        <OperationsView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelector("#dash-plan-tbody")?.textContent).toContain("Spice mine");
    });

    const bldCardText = container.querySelector("#dash-queue-card-building")!.textContent || "";
    const fltCardText = container.querySelector("#dash-queue-card-fleet")!.textContent || "";
    const resCardText = container.querySelector("#dash-queue-card-research")!.textContent || "";

    expect(bldCardText).toContain("EXEC");
    expect(bldCardText).toContain("Fusion plant → L23");
    expect(bldCardText).toContain("GAME #2");
    expect(bldCardText).toContain("Pig iron warehouse → L5");
    expect(bldCardText).toContain("Spice mine");
    expect(bldCardText).not.toContain("Spy probe");
    expect(bldCardText).not.toContain("Mining technology");

    expect(fltCardText).toContain("EXEC");
    expect(fltCardText).toContain("Attack -> [02:378:15] Colony");
    expect(fltCardText).toContain("Spy probe");
    expect(fltCardText).not.toContain("Spice mine");

    expect(resCardText).toContain("Mining technology");
    expect(resCardText).not.toContain("Spice mine");

    const cellTexts = Array.from(
      container.querySelectorAll(
        "#dash-queue-card-building td, #dash-queue-card-fleet td, #dash-queue-card-research td"
      )
    ).map((td) => td.textContent?.trim() || "");
    const clockCells = cellTexts.filter((t) => /\b\d{2}:\d{2}:\d{2}\b/.test(t));
    expect(clockCells.length).toBeGreaterThanOrEqual(4);

    // Clicking Fleet "+ Queue" jump navigates to Operations with mq-kind preset to BUILD_SHIP
    const fleetQueueJump = container.querySelector("#btn-dash-add-fleet") as HTMLButtonElement | null;
    if (fleetQueueJump) {
      fireEvent.click(fleetQueueJump);
      await waitFor(() => {
        const mqKind = container.querySelector("#mq-kind") as HTMLSelectElement | null;
        expect(mqKind?.value).toBe("BUILD_SHIP");
      });
    }
  });

  it("switches #planet-select to update #hud-bf-pill to 0% PAUSED and toggles pause/resume buttons", async () => {
    const dash = createBaseDashboard({
      state: {
        ...createBaseDashboard().state,
        active_planet_id: 101,
        planets: [
          {
            planet_id: 101,
            name: "Alpha",
            coords: "02:380:01",
            planet_type: "normal",
            fields_used: 30,
            fields_max: 230,
            resources: { pig_iron: 10000, metal: 5000, kryptonite: 3000, spice: 1000 },
            storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
            energy: { produced: 300, used: 150, net: 150 },
            economy_rates: { resource1: 100, resource2: 100, resource3: 100, resource4: 100 },
          },
          {
            planet_id: 102,
            name: "BetaPaused",
            coords: "02:380:02",
            planet_type: "normal",
            fields_used: 20,
            fields_max: 230,
            resources: { pig_iron: 4000, metal: 2000, kryptonite: 1000, spice: 500 },
            storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
            energy: { produced: 200, used: 100, net: 100 },
            economy_rates: { resource1: 100, resource2: 0, resource3: 100, resource4: 100 },
          },
        ],
      },
    });

    installMockFetch({
      getDashboard: () => dash,
      onCommand: (body) => {
        if (body.command === "buildings_eta") {
          const pid = Number(body.planet_id || 101);
          return {
            ok: true,
            buildings_eta: {
              planet: { planet_id: pid, coords: pid === 102 ? "02:380:02" : "02:380:01" },
              planet_economy: { blast_furnace_pct: pid === 102 ? 0 : 100 },
              buildings: [],
              researches: [],
            },
          };
        }
        return undefined;
      },
    });

    const { container } = render(
      <SeededConsole page="dashboard" dashboard={dash}>
        <AppHeader />
        <DashboardView />
        <BuildingsView />
      </SeededConsole>
    );

    await waitFor(() => {
      const sel = container.querySelector("#planet-select") as HTMLSelectElement;
      expect(sel.options.length).toBe(2);
    });

    expect(container.querySelector("#hud-bf-pill")?.textContent).not.toContain("0% PAUSED");
    const sel = container.querySelector("#planet-select") as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: "102" } });

    await waitFor(() => {
      expect(container.querySelector("#hud-bf-pill")?.textContent).toContain("0% PAUSED");
      expect((container.querySelector("#btn-b2-pause") as HTMLButtonElement).disabled).toBe(true);
      expect((container.querySelector("#btn-b2-resume") as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it("dispatches dry_run Sim commands (including DEMOLISH_BUILDING) and renders clean non-error feedback banner", async () => {
    const dash = createBaseDashboard({
      plan: [
        {
          priority: "P1",
          kind: "DEMOLISH_BUILDING",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 6,
          target_name: "Thermosolar plant",
          target_level: 1,
          executable_now: true,
          wait_eta: "Ready",
          duration_text: "1m 00s",
          total_eta: "1m",
          reason: "Reclaim 1 field",
        },
      ],
    });

    const { postedCommands } = installMockFetch({
      getDashboard: () => dash,
      onCommand: (body) => {
        if (body.command === "demolish_building") {
          return {
            ok: true,
            dry_run: true,
            executed: false,
            message: "[DRY-RUN] Would execute DEMOLISH_BUILDING: Thermosolar plant -> L1",
          };
        }
        return undefined;
      },
    });

    const { container } = render(
      <ConsoleProvider>
        <FeedbackBanner />
        <DashboardView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      const btns = Array.from(container.querySelectorAll("#dash-plan-tbody button"));
      expect(btns.some((b) => b.textContent?.trim() === "Sim")).toBe(true);
    });

    const simBtn = Array.from(container.querySelectorAll("#dash-plan-tbody button")).find(
      (b) => b.textContent?.trim() === "Sim"
    )!;
    fireEvent.click(simBtn);

    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) => c.command === "demolish_building" && c.building_id === 6 && c.dry_run === true
        )
      ).toBe(true);
      const banner = container.querySelector("#feedback-banner")!;
      expect(banner.textContent).toContain("[DRY-RUN] Would execute DEMOLISH_BUILDING: Thermosolar plant -> L1");
      expect(banner.className).not.toContain("is-error");
    });
  });

  it("renders tutorial strip step progress, hostile alert beacon, and negative-zero resource rate safety", async () => {
    const dash = createBaseDashboard({
      state: {
        ...createBaseDashboard().state,
        tutorial: {
          step: 2,
          total_steps: 11,
          tasks: [{ description: "Upgrade Pig iron mine 1/2", building_name: "Pig iron mine", current: 1, target: 2 }],
        },
        fleet_alerts: [{ mission: "Attack", summary: "Hostile inbound", hostile: true }],
      },
      buildings_eta: {
        ...createBaseDashboard().buildings_eta,
        planet_economy: {
          blast_furnace_pct: 100,
          pig_iron_per_hr: -0.1,
          metal_per_hr: -1,
          kryptonite_per_hr: 500,
          spice_per_hr: 200,
        },
      },
    });

    const { container } = render(
      <SeededConsole page="dashboard" dashboard={dash}>
        <AppHeader />
        <DashboardView />
        <BuildingsView />
      </SeededConsole>
    );

    await waitFor(() => {
      const strip = container.querySelector("#tutorial-strip") as HTMLElement;
      expect(strip.hidden).toBe(false);
    });

    expect(container.querySelector("#tutorial-title")?.textContent).toContain("2/11");
    expect(container.querySelector("#hud-alert-beacon")?.className).toContain("is-alert");
    expect(container.querySelector("#hud-alert-beacon")?.textContent).toContain("HOSTILE ALERT (1)");

    expect(container.querySelector("#rate-pig")?.textContent).toBe("0/h");
    expect(container.querySelector("#rate-pig")?.className).not.toContain("is-neg");
    expect(container.querySelector("#rate-metal")?.textContent).toBe("-1/h");
    expect(container.querySelector("#rate-metal")?.className).toContain("is-neg");
  });
});
