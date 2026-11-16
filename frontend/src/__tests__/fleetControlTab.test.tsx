import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsoleActions } from "../store";
import { CombatView } from "../features/combat";
import {
  FleetControlView,
  classifyFlightMission,
  isOngoingFlightJob,
  type FlightQueueJob,
} from "../features/fleet-control";
import type { DashboardPayload } from "../types/api";

function makeDashboard(
  activeQueues: FlightQueueJob[],
  overrides: Record<string, any> = {}
): DashboardPayload {
  return {
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
    },
    custom_planet_names: {
      "101": "Alpha Forge",
      "102": "Beta Vault",
    },
    state: {
      universe_id: 31,
      user_id: 103603,
      nickname: "commander",
      active_planet_id: 101,
      planets: [
        {
          planet_id: 101,
          name: "Homeworld Raw",
          coords: "02:380:12",
          planet_type: "normal",
          fields_used: 48,
          fields_max: 240,
          ships: { s1: 12, s4: 25, s11: 4 },
        },
        {
          planet_id: 102,
          name: "Colony Two Raw",
          coords: "02:381:05",
          planet_type: "normal",
          fields_used: 30,
          fields_max: 310,
          ships: { s1: 8, s2: 4 },
        },
      ],
      active_queues: activeQueues,
    },
    combat: {
      planet_id: 101,
      attack_zone: { label: "AZ 3", range_text: "2,000 - 10,000" },
      newbie_protected: false,
      safety_margin: 1.4,
      combat_techs: { weapons: 5, shield: 4, defense: 4, spy: 6, targeting: 3 },
      galaxy_scanner_level: 1,
      stationed_fleet_power: 2400,
      stationed_defense_power: 600,
      total_cargo_capacity: 55000,
      weapons_factory_level: 5,
      ships: [
        {
          unit_id: 4,
          key: "s4",
          name: "Small fighter",
          owned: 25,
          attack: 50,
          shield: 10,
          cargo: 50,
        },
      ],
      ship_offers: [{ unit_id: 4, can_build: true }],
      defenses: [],
    },
    ...overrides,
  } as unknown as DashboardPayload;
}

const InitPage: React.FC<{ page: string; children: React.ReactNode }> = ({ page, children }) => {
  const { setActivePage, loadDashboard } = useConsoleActions();
  useEffect(() => {
    setActivePage(page as any);
    loadDashboard({ refresh: false, force: true });
  }, [page, setActivePage, loadDashboard]);
  return <>{children}</>;
};

const Harness: React.FC<{ page: string; children: React.ReactNode }> = ({ page, children }) => (
  <ConsoleProvider disableInitialFetch>
    <InitPage page={page}>{children}</InitPage>
  </ConsoleProvider>
);

describe("Fleet Build rename & Fleet Control tab", () => {
  let fetchedUrls: string[];
  let commandCalls: Array<Record<string, any>>;
  let currentDashboard: DashboardPayload;

  beforeEach(() => {
    fetchedUrls = [];
    commandCalls = [];
    const nowSec = Math.floor(Date.now() / 1000);
    currentDashboard = makeDashboard([
      // Non-flight queues that MUST be excluded from Fleet Control
      {
        category: "building",
        name: "Metal mine (Level 12)",
        detail: "In construction",
        remaining_seconds: 1200,
        finish_ts: nowSec + 1200,
        origin_coords: "02:380:12",
      },
      {
        category: "research",
        name: "Weapons technology (Level 6)",
        detail: "Researching",
        remaining_seconds: 2400,
        finish_ts: nowSec + 2400,
      },
      {
        category: "fleet",
        name: "10x Small fighter",
        detail: "Shipyard (40%)",
        remaining_seconds: 300,
        finish_ts: nowSec + 300,
        origin_coords: "02:380:12",
      },
      {
        category: "fleet",
        name: "5x Missile turret",
        detail: "Weapons factory",
        remaining_seconds: 400,
        finish_ts: nowSec + 400,
        origin_coords: "02:380:12",
      },
      // 1. Outbound ATTACK with expected_cargo (and Small transport in detail to verify no misclassification)
      {
        category: "fleet",
        name: "Attack → Spacia (02:383:02)",
        detail: "Outbound · 6 Small transport, 12 Small fighter",
        origin_coords: "02:380:12",
        remaining_seconds: 600,
        finish_ts: nowSec + 600,
        expected_cargo: { pig_iron: 25000, metal: 12000, kryptonite: 4000, spice: 1000 },
      },
      // 2. Outbound RECYCLE with expected_cargo
      {
        category: "fleet",
        name: "Recycle → 02:380:09",
        detail: "Outbound · 4 Recycler",
        origin_coords: "02:380:12",
        remaining_seconds: 900,
        finish_ts: nowSec + 900,
        expected_cargo: { pig_iron: 14000, metal: 6000, kryptonite: 0, spice: 0 },
      },
      // 3. Outbound TRANSPORT between own colonies, falling back to job.cargo when expected_cargo is absent
      {
        category: "fleet",
        name: "Transport → 02:381:05",
        detail: "Outbound · 8 Small transport",
        origin_coords: "02:380:12",
        remaining_seconds: 1500,
        finish_ts: nowSec + 1500,
        cargo: { pig_iron: 10000, metal: 5000, kryptonite: 0, spice: 0 },
      },
      // 4. Returning TRANSPORT with delivered=true and delivered_cargo populated
      {
        category: "fleet",
        name: "Transport ↩ 02:380:12",
        detail: "Returning · 4 Large transport",
        origin_coords: "02:381:05",
        remaining_seconds: 420,
        finish_ts: nowSec + 420,
        delivered: true,
        delivered_cargo: { pig_iron: 18000, metal: 9000, kryptonite: 2000, spice: 500 },
      },
      // 5. Returning TRANSPORT with delivered=true and delivered_cargo=null (clean badge, no null/NaN)
      {
        category: "TRANSPORT",
        label: "Shuttle Run",
        name: "Transport ↩ 02:381:05",
        origin_coords: "02:381:05",
        ships: { s1: 5 },
        remaining_seconds: 180,
        finish_ts: nowSec + 180,
        delivered: true,
        delivered_cargo: null,
      },
      // 6. Outbound SPY
      {
        category: "SPY",
        name: "Spy → IdlePrime (02:380:04)",
        detail: "Outbound · 3 Spy probe",
        origin_coords: "02:380:12",
        remaining_seconds: 120,
        finish_ts: nowSec + 120,
      },
      // 7. Outbound ESCAPE flight ("Other" mission filter)
      {
        category: "ESCAPE",
        name: "Escape flight → 02:381:05",
        origin_coords: "02:380:12",
        ships: { s2: 3, s4: 10 },
        remaining_seconds: 3600,
        finish_ts: nowSec + 3600,
        cargo: { pig_iron: 5000, metal: 0, kryptonite: 0, spice: 3000 },
      },
    ]);

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        fetchedUrls.push(String(url));
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(currentDashboard),
          };
        }
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        commandCalls.push(body);
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ ok: true, executed: true, message: "OK" }),
        };
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renames CombatView copy to Fleet Build while preserving IDs and zero <svg> elements", async () => {
    const { container } = render(
      <Harness page="combat">
        <CombatView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#cmb-az-val")?.textContent).toContain("AZ 3");
    });

    const section = container.querySelector("#view-combat") as HTMLElement;
    expect(section).toBeTruthy();
    expect(section.getAttribute("aria-label")).toContain("Fleet Build");
    expect(section.textContent).toContain("Fleet Build Navigation");
    expect(section.textContent).toContain("Shipyard & Defenses");
    expect(section.textContent).not.toContain("Combat & Fleet");

    // Existing controls & IDs remain intact
    expect(container.querySelector("#cmb-ships-tbody")?.textContent).toContain("Small fighter");
    expect(container.querySelector("#btn-run-sim")).toBeTruthy();
    expect(container.querySelector("#fs-target")).toBeTruthy();
    expect(container.querySelectorAll("svg").length).toBe(0);
  });

  it("renders FleetControlView with ongoing flights, KPI summary, custom planet names, cargo flags, and zero <svg>", async () => {
    const { container } = render(
      <Harness page="fleet-control">
        <FleetControlView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#flt-ctrl-kpi-total")?.textContent).toBe("7");
    });

    const section = container.querySelector("#view-fleet-control") as HTMLElement;
    expect(section).toBeTruthy();
    expect(section.hidden).toBe(false);
    expect(section.className).toContain("view-panel");
    expect(section.getAttribute("aria-label")).toBe("Fleet Control — current flights");
    expect(container.querySelectorAll("svg").length).toBe(0);

    // KPI summary bar
    expect(container.querySelector("#flt-ctrl-kpi-dir")?.textContent).toContain("5 Outbound");
    expect(container.querySelector("#flt-ctrl-kpi-dir")?.textContent).toContain("2 Returning");
    expect(container.querySelector("#flt-ctrl-kpi-missions")?.textContent).toContain("ATK 1");
    expect(container.querySelector("#flt-ctrl-kpi-missions")?.textContent).toContain("REC 1");
    expect(container.querySelector("#flt-ctrl-kpi-missions")?.textContent).toContain("TRN 3");
    expect(container.querySelector("#flt-ctrl-kpi-missions-sub")?.textContent).toContain("Spy 1");
    expect(container.querySelector("#flt-ctrl-kpi-missions-sub")?.textContent).toContain("Other 1");

    // Excluded building/research/shipyard queues never appear in the table
    const tbody = container.querySelector("#flt-ctrl-tbody")!;
    const rows = Array.from(tbody.querySelectorAll("tr"));
    expect(rows.length).toBe(7);
    expect(tbody.textContent).not.toContain("Metal mine");
    expect(tbody.textContent).not.toContain("Weapons technology");
    expect(tbody.textContent).not.toContain("Shipyard (40%)");
    expect(tbody.textContent).not.toContain("Missile turret");

    // Attack row was NOT misclassified as Transport despite "Small transport" in detail
    const attackRow = tbody.querySelector('tr[data-flight-row="attack"]')!;
    expect(attackRow).toBeTruthy();
    expect(attackRow.textContent).toContain("Attack");
    expect(attackRow.textContent).toContain("OUTBOUND");
    expect(attackRow.textContent).toContain("Alpha Forge");
    expect(attackRow.textContent).toContain("Spacia");
    expect(attackRow.textContent).toContain("6 Small transport, 12 Small fighter");
    expect(attackRow.querySelector('[data-cargo-flag="EXPECTED"]')?.textContent).toContain("EXPECTED");
    expect(attackRow.querySelector('[data-cargo-flag="EXPECTED"]')?.textContent).toContain("25,000 Pig");
    expect(attackRow.querySelector('[data-cargo-flag="EXPECTED"]')?.textContent).toContain("12,000 Met");

    // Outbound Recycle shows EXPECTED badge + formatted resources
    const recycleRow = tbody.querySelector('tr[data-flight-row="recycle"]')!;
    expect(recycleRow).toBeTruthy();
    expect(recycleRow.querySelector('[data-cargo-flag="EXPECTED"]')?.textContent).toContain("14,000 Pig");

    // Outbound Transport between own colonies displays custom planet names on BOTH Origin and Target + EXPECTED fallback from job.cargo
    const transportRows = Array.from(tbody.querySelectorAll('tr[data-flight-row="transport"]'));
    expect(transportRows.length).toBe(3);
    const outboundTransport = transportRows.find((r) => r.textContent?.includes("OUTBOUND"))!;
    expect(outboundTransport.textContent).toContain("Alpha Forge");
    expect(outboundTransport.textContent).toContain("Beta Vault");
    expect(outboundTransport.querySelector('[data-cargo-flag="EXPECTED"]')?.textContent).toContain("EXPECTED");
    expect(outboundTransport.querySelector('[data-cargo-flag="EXPECTED"]')?.textContent).toContain("10,000 Pig");

    // Returning Transport with delivered_cargo populated
    const deliveredWithCargo = transportRows.find((r) => r.textContent?.includes("18,000 Pig"))!;
    expect(deliveredWithCargo).toBeTruthy();
    expect(deliveredWithCargo.textContent).toContain("RETURNING");
    expect(deliveredWithCargo.querySelector('[data-cargo-flag="DELIVERED"]')?.textContent).toContain("DELIVERED");
    expect(deliveredWithCargo.querySelector('[data-cargo-flag="DELIVERED"]')?.textContent).toContain("9,000 Met");

    // Returning Transport with delivered=true and delivered_cargo=null renders clean DELIVERED badge
    const deliveredNullCargo = transportRows.find((r) => r.textContent?.includes("Shuttle Run"))!;
    expect(deliveredNullCargo).toBeTruthy();
    expect(deliveredNullCargo.textContent).toContain("5 Small transport");
    const delFlag = deliveredNullCargo.querySelector('[data-cargo-flag="DELIVERED"]')!;
    expect(delFlag.textContent?.trim()).toBe("DELIVERED");
    expect(deliveredNullCargo.textContent).not.toMatch(/\bnull\b|\bundefined\b|\bNaN\b/);

    // Escape flight ships formatted from object map
    const escapeRow = tbody.querySelector('tr[data-flight-row="escape"]')!;
    expect(escapeRow).toBeTruthy();
    expect(escapeRow.textContent).toContain("3 Large transport, 10 Small fighter");

    // Live countdown ETA pill carries data-finish-ts and HH:MM:SS format
    const timerPills = Array.from(tbody.querySelectorAll(".eta-pill.is-exec-timer"));
    expect(timerPills.length).toBe(7);
    expect(timerPills.every((el) => el.getAttribute("data-finish-ts"))).toBe(true);
    expect(timerPills[0].textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it("supports Mission pills, Direction pills, Planet filter (with custom names), and Refresh button", async () => {
    const { container } = render(
      <Harness page="fleet-control">
        <FleetControlView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#flt-ctrl-tbody tr").length).toBe(7);
    });

    // Planet dropdown displays renamed planet names
    const planetSelect = container.querySelector("#flt-ctrl-planet-filter") as HTMLSelectElement;
    const optionTexts = Array.from(planetSelect.options).map((o) => o.textContent);
    expect(optionTexts).toContain("Alpha Forge [02:380:12]");
    expect(optionTexts).toContain("Beta Vault [02:381:05]");

    // Mission filter -> Attack
    fireEvent.click(container.querySelector('[data-flt-mission="attack"]')!);
    expect(container.querySelectorAll("#flt-ctrl-tbody tr").length).toBe(1);
    expect(container.querySelector("#flt-ctrl-tbody")?.textContent).toContain("Spacia");

    // Direction filter -> Returning while Mission=Attack => empty filtered state
    fireEvent.click(container.querySelector('[data-flt-dir="returning"]')!);
    expect(container.querySelector("#flt-ctrl-tbody")?.textContent).toContain(
      "No ongoing flights match the current filters."
    );

    // Reset Filters restores all 7 rows
    fireEvent.click(container.querySelector("#btn-flt-ctrl-reset-filters")!);
    expect(container.querySelectorAll("#flt-ctrl-tbody tr").length).toBe(7);

    // Direction filter -> Returning => 2 returning transport flights
    fireEvent.click(container.querySelector('[data-flt-dir="returning"]')!);
    expect(container.querySelectorAll("#flt-ctrl-tbody tr").length).toBe(2);

    // Reset direction, then filter by Beta Vault (planet 102) => matches flights with origin OR target 02:381:05
    fireEvent.click(container.querySelector('[data-flt-dir="all"]')!);
    fireEvent.change(planetSelect, { target: { value: "102" } });
    // Outbound Transport -> Beta Vault, Returning Transport from Beta Vault, Returning Shuttle Run on Beta Vault, Escape -> Beta Vault = 4
    expect(container.querySelectorAll("#flt-ctrl-tbody tr").length).toBe(4);

    // Refresh button calls /dashboard?refresh=1
    fireEvent.click(container.querySelector("#btn-flt-ctrl-refresh")!);
    await waitFor(() => {
      expect(fetchedUrls.some((u) => u.includes("/dashboard?refresh=1"))).toBe(true);
    });

    // Open Fleet nav button sends navigate internalFleet
    fireEvent.click(container.querySelector("#btn-flt-ctrl-nav-fleet")!);
    await waitFor(() => {
      expect(
        commandCalls.some((c) => c.command === "navigate" && c.action === "internalFleet")
      ).toBe(true);
    });
  });

  it("renders clean empty state when no active flights are in progress and respects hidden visibility", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    currentDashboard = makeDashboard([
      {
        category: "building",
        name: "Pig iron mine (Level 10)",
        detail: "In construction",
        remaining_seconds: 600,
        finish_ts: nowSec + 600,
      },
      {
        category: "fleet",
        name: "4x Recycler",
        detail: "Shipyard (25%)",
        remaining_seconds: 300,
        finish_ts: nowSec + 300,
      },
    ]);

    const { container } = render(
      <Harness page="dashboard">
        <FleetControlView />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#flt-ctrl-kpi-total")?.textContent).toBe("0");
    });

    const section = container.querySelector("#view-fleet-control") as HTMLElement;
    expect(section.hidden).toBe(true);
    expect(container.querySelector("#flt-ctrl-tbody")?.textContent).toContain(
      "No active flights in progress."
    );
    expect(container.querySelectorAll("svg").length).toBe(0);
  });

  it("correctly classifies flight helpers for edge-case job categories and missions", () => {
    expect(isOngoingFlightJob({ category: "building", name: "Mine" })).toBe(false);
    expect(isOngoingFlightJob({ category: "fleet", name: "Ship", detail: "Shipyard (10%)" })).toBe(false);
    expect(isOngoingFlightJob({ category: "fleet", name: "Defense", detail: "Weapons factory" })).toBe(false);
    expect(isOngoingFlightJob({ category: "COLONIZE", name: "Colonize → 02:390:08" })).toBe(true);
    expect(isOngoingFlightJob({ category: "STATION", name: "Station → 02:381:05" })).toBe(true);
    expect(isOngoingFlightJob({ category: "TRANSFER", name: "Transfer → 02:381:05" })).toBe(true);
    expect(isOngoingFlightJob({ category: "SAVE", name: "Escape flight → 02:381:05" })).toBe(true);
    expect(
      classifyFlightMission({
        category: "fleet",
        name: "Attack → 02:383:02",
        detail: "Outbound · 10 Small transport, 20 Small fighter",
      })
    ).toBe("ATTACK");
  });
});
