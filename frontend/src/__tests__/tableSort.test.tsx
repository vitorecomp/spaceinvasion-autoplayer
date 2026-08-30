import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../context/ConsoleContext";
import {
  compareSortValues,
  coordsSortKey,
  durationToSeconds,
  gradeSortKey,
  prioritySortKey,
  resourcesTotal,
  timestampSortKey,
} from "../hooks/useTableSort";
import { DashboardPayload, PlannedActionData, QueueJobData } from "../types/api";
import { BuildingsView } from "../features/buildings";
import { ColoniesView } from "../features/colonies";
import { DashboardQueueCard } from "../features/dashboard/DashboardQueueCard";
import { EspionageView } from "../features/espionage";
import { OperationsView } from "../features/operations";

const SORT_DASHBOARD: DashboardPayload = {
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
    min_colony_fields: 300,
    allow_colonize: true,
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
        fields_used: 50,
        fields_max: 230,
      },
    ],
  },
  plan: [
    {
      priority: "P2",
      kind: "BUILD",
      planet_id: 12476,
      planet_coords: "02:380:12",
      target_id: 3,
      target_name: "Kryptonite mine",
      target_level: 8,
      cost: { pig_iron: 2000, metal: 1000 },
      missing: { pig_iron: 500 },
      wait_eta: "2h 00m",
      wait_hours: 2,
      total_eta: "2h 30m",
      total_hours: 2.5,
      total_seconds: 9000,
      reason: "Economy balance",
      executable_now: false,
    },
    {
      priority: "P0",
      kind: "BUILD",
      planet_id: 12476,
      planet_coords: "02:380:12",
      target_id: 5,
      target_name: "Fusion plant",
      target_level: 11,
      cost: { pig_iron: 500, metal: 200 },
      missing: {},
      wait_eta: "Ready",
      wait_hours: 0,
      total_eta: "15m",
      total_hours: 0.25,
      total_seconds: 900,
      reason: "Energy deficit",
      executable_now: true,
    },
    {
      priority: "P1",
      kind: "RESEARCH",
      planet_id: 12476,
      planet_coords: "02:380:12",
      target_id: 1,
      target_name: "Mining technology",
      target_level: 5,
      cost: { pig_iron: 5000, metal: 3000 },
      missing: { metal: 3000 },
      wait_eta: "—",
      wait_hours: null,
      total_eta: "—",
      total_hours: null,
      reason: "Blocked on Metal",
      executable_now: false,
    },
  ],
  buildings_eta: {
    planet: {
      planet_id: 12476,
      name: "Homeworld",
      coords: "02:380:12",
      dev_center_level: 6,
      research_lab_level: 5,
      microsystem_level: 0,
      building_time_divisor: 7,
    },
    planet_economy: {
      blast_furnace_pct: 100,
      pig_iron_per_hr: 1000,
    },
    buildings: [
      {
        key: "b3",
        building_id: 3,
        name: "Kryptonite mine",
        category: "mines",
        current_level: 6,
        next_level: 7,
        cost: { pig_iron: 1200 },
        missing: { pig_iron: 200 },
        missing_total: 200,
        build_time_text: "20m",
        build_seconds: 1200,
        wait_eta: "12m",
        wait_hours: 0.2,
        reach_eta: "32m",
        reach_hours: 0.53,
        can_build_live: false,
        horizons: [
          { level: 7, cost: { pig_iron: 1200 }, build_time_text: "20m", reach_eta: "32m" },
          { level: 8, cost: { pig_iron: 2400 }, build_time_text: "40m", reach_eta: "2h 10m" },
        ],
      },
      {
        key: "b1",
        building_id: 1,
        name: "Pig iron mine",
        category: "mines",
        current_level: 14,
        next_level: 15,
        cost: { pig_iron: 4000 },
        missing: {},
        missing_total: 0,
        build_time_text: "45m",
        build_seconds: 2700,
        wait_eta: "Ready",
        wait_hours: 0,
        reach_eta: "45m",
        reach_hours: 0.75,
        can_build_live: true,
        horizons: [],
      },
      {
        key: "b2",
        building_id: 2,
        name: "Blast furnace",
        category: "mines",
        current_level: 9,
        next_level: 10,
        cost: { pig_iron: 2500 },
        missing: { pig_iron: 900 },
        missing_total: 900,
        build_time_text: "30m",
        build_seconds: 1800,
        wait_eta: "—",
        wait_hours: null,
        reach_eta: "—",
        reach_hours: null,
        can_build_live: false,
        horizons: [],
      },
    ],
    researches: [],
  },
  db_summary: {
    spy_targets: [
      {
        coords: "02:390:05",
        planet_name: "FarRich",
        owner: "RichIdle",
        status_color: "yellow",
        is_inactive: true,
        has_spy_intel: true,
        uninhabited_fields: null,
        resources: { pig_iron: 40000, metal: 10000, kryptonite: 0, spice: 0 },
        spice_eq: 25000,
        fleet: {},
        defense: {},
      },
      {
        coords: "02:380:02",
        planet_name: "SameSysSmall",
        owner: "NearIdle",
        status_color: "yellow",
        is_inactive: true,
        has_spy_intel: true,
        uninhabited_fields: null,
        resources: { pig_iron: 2000, metal: 1000, kryptonite: 0, spice: 0 },
        spice_eq: 1500,
        fleet: { s4: 2 },
        defense: {},
      },
      {
        coords: "10:12:03",
        planet_name: "OtherGalaxy",
        owner: "GalaxyTen",
        status_color: "yellow",
        is_inactive: true,
        has_spy_intel: false,
        uninhabited_fields: 315,
        resources: {},
        spice_eq: 0,
        fleet: {},
        defense: {},
      },
    ],
  },
};

function ActivatePage({ page }: { page: string }) {
  const { setActivePage } = useConsole();
  useEffect(() => {
    setActivePage(page as any);
  }, [page, setActivePage]);
  return null;
}

describe("useTableSort helpers", () => {
  it("parses coordinates numerically across galaxies and systems", () => {
    expect(coordsSortKey("2:9:1")).toBeLessThan(coordsSortKey("2:10:1")!);
    expect(coordsSortKey("2:380:12")).toBeLessThan(coordsSortKey("10:1:1")!);
    expect(coordsSortKey("02:380:02")).toBe(2_038_002);
    expect(coordsSortKey("—")).toBeNull();
  });

  it("ranks priorities, grades, resources, durations, and timestamps cleanly", () => {
    expect(prioritySortKey("P0")).toBe(0);
    expect(prioritySortKey("P3")).toBe(3);
    expect(prioritySortKey("unknown")).toBeNull();

    expect(gradeSortKey("S")).toBeLessThan(gradeSortKey("A+")!);
    expect(gradeSortKey("A+")).toBeLessThan(gradeSortKey("B")!);
    expect(gradeSortKey("?")).toBeNull();

    expect(resourcesTotal({ pig_iron: 100, metal: 250, spice: 50 })).toBe(400);
    expect(resourcesTotal({})).toBeNull();

    expect(durationToSeconds("Ready")).toBe(0);
    expect(durationToSeconds("2h 30m (Pig)")).toBe(9000);
    expect(durationToSeconds("01:05:10")).toBe(3910);
    expect(durationToSeconds("—")).toBeNull();

    expect(timestampSortKey(1700000000)).toBe(1700000000);
    expect(timestampSortKey(0)).toBeNull();
  });

  it("always places null/missing values at the bottom regardless of asc or desc", () => {
    expect(compareSortValues(null, 5, "asc")).toBeGreaterThan(0);
    expect(compareSortValues(null, 5, "desc")).toBeGreaterThan(0);
    expect(compareSortValues(5, null, "asc")).toBeLessThan(0);
    expect(compareSortValues(5, null, "desc")).toBeLessThan(0);
  });
});

describe("Interactive click-to-sort tables across Operator Console views", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(SORT_DASHBOARD),
          };
        }
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        if (body.command === "espionage_intel") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                origin_coords: "02:380:12",
                summary: { total_targets: 3, spied_targets: 2, total_reports: 2 },
                targets: SORT_DASHBOARD.db_summary?.spy_targets || [],
                reports: [
                  {
                    id: 1,
                    ts: 1700000100,
                    coords: "02:390:05",
                    planet_name: "OldReport",
                    owner: "Alpha",
                    spice_eq: 500,
                    resources: { pig_iron: 500 },
                  },
                  {
                    id: 2,
                    ts: 1700000900,
                    coords: "02:380:02",
                    planet_name: "NewReport",
                    owner: "Beta",
                    spice_eq: 9000,
                    resources: { pig_iron: 9000 },
                  },
                ],
              }),
          };
        }
        if (body.command === "colony_candidates") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                min_colony_fields: 300,
                candidates: [
                  {
                    coords: "02:380:09",
                    position: 9,
                    orbital_band: "Prime",
                    grade: "B",
                    score: 65,
                    probed: true,
                    meets_target: false,
                    uninhabited_fields: 260,
                    system_distance: 0,
                    reason: "Below 300 target",
                  },
                  {
                    coords: "02:385:10",
                    position: 10,
                    orbital_band: "Prime",
                    grade: "?",
                    score: 40,
                    probed: false,
                    meets_target: false,
                    uninhabited_fields: null,
                    system_distance: 5,
                    reason: "Unprobed slot",
                  },
                  {
                    coords: "02:381:08",
                    position: 8,
                    orbital_band: "Prime",
                    grade: "S",
                    score: 96,
                    probed: true,
                    meets_target: true,
                    uninhabited_fields: 345,
                    system_distance: 1,
                    reason: "Elite 345 fields",
                  },
                ],
                colony_attempts: [
                  {
                    id: 10,
                    target_coords: "02:380:07",
                    event_type: "deleted_undersized",
                    fields_rolled: 215,
                    min_fields_target: 300,
                    origin_coords: "02:380:12",
                    ts: 1700000200,
                    status_message: "Deleted 215 < 300",
                  },
                  {
                    id: 11,
                    target_coords: "02:381:08",
                    event_type: "kept",
                    fields_rolled: 345,
                    min_fields_target: 300,
                    origin_coords: "02:380:12",
                    ts: 1700000500,
                    status_message: "Kept 345 fields",
                  },
                ],
              }),
          };
        }
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      })
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("sorts EspionageView Spied Targets by Coords, Distance, and Loot SpEq with 3-state reset", async () => {
    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="espionage" />
        <EspionageView hidden={false} />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#esp-targets-tbody tr")).toHaveLength(3);
    });

    const getCoordsOrder = () =>
      Array.from(container.querySelectorAll("#esp-targets-tbody tr td:first-child")).map((td) =>
        td.textContent?.trim()
      );

    // Natural order from API
    expect(getCoordsOrder()).toEqual(["02:390:05", "02:380:02", "10:12:03"]);

    // Click "Coords" header -> ascending numeric coords (2:380:02 < 2:390:05 < 10:12:03)
    const coordsBtn = Array.from(container.querySelectorAll("#esp-pane-targets th button")).find(
      (b) => b.textContent?.trim() === "Coords"
    ) as HTMLButtonElement;
    expect(coordsBtn).toBeTruthy();

    fireEvent.click(coordsBtn);
    expect(coordsBtn.closest("th")?.getAttribute("aria-sort")).toBe("ascending");
    expect(getCoordsOrder()).toEqual(["02:380:02", "02:390:05", "10:12:03"]);

    // 2nd click -> descending
    fireEvent.click(coordsBtn);
    expect(coordsBtn.closest("th")?.getAttribute("aria-sort")).toBe("descending");
    expect(getCoordsOrder()).toEqual(["10:12:03", "02:390:05", "02:380:02"]);

    // 3rd click -> reset back to natural server order
    fireEvent.click(coordsBtn);
    expect(coordsBtn.closest("th")?.getAttribute("aria-sort")).toBe("none");
    expect(getCoordsOrder()).toEqual(["02:390:05", "02:380:02", "10:12:03"]);

    // Click "Loot SpEq" -> descending first, unspied target stays at the bottom
    const lootBtn = Array.from(container.querySelectorAll("#esp-pane-targets th button")).find(
      (b) => b.textContent?.trim() === "Loot SpEq"
    ) as HTMLButtonElement;
    fireEvent.click(lootBtn);
    expect(lootBtn.closest("th")?.getAttribute("aria-sort")).toBe("descending");
    expect(getCoordsOrder()).toEqual(["02:390:05", "02:380:02", "10:12:03"]);

    // 2nd click -> ascending (lowest spied loot first, unspied STILL at bottom!)
    fireEvent.click(lootBtn);
    expect(lootBtn.closest("th")?.getAttribute("aria-sort")).toBe("ascending");
    expect(getCoordsOrder()).toEqual(["02:380:02", "02:390:05", "10:12:03"]);
  });

  it("sorts ColoniesView candidates by Grade (keeping ? at bottom) and Tried Planets by Rolled Fields", async () => {
    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="colonies" />
        <ColoniesView hidden={false} />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#col-candidates-tbody tr")).toHaveLength(3);
      expect(container.querySelectorAll("#colonies-attempts-tbody tr")).toHaveLength(2);
    });

    const getGrades = () =>
      Array.from(container.querySelectorAll("#col-candidates-tbody tr td:first-child")).map((td) =>
        td.textContent?.trim()
      );

    // Click "Grade" -> S, B, ? (? always stays bottom)
    const gradeBtn = Array.from(container.querySelectorAll(".col-candidates-window th button")).find(
      (b) => b.textContent?.trim() === "Grade"
    ) as HTMLButtonElement;
    fireEvent.click(gradeBtn);
    expect(getGrades()).toEqual(["S", "B", "?"]);

    // 2nd click -> B, S, ? (? STILL at bottom)
    fireEvent.click(gradeBtn);
    expect(getGrades()).toEqual(["B", "S", "?"]);

    // Sort Tried Planets by Rolled Fields (default desc -> 345 f first, then 215 f)
    const rolledBtn = Array.from(
      container.querySelectorAll("#colonies-attempts-table th button")
    ).find((b) => b.textContent?.trim() === "Rolled Fields") as HTMLButtonElement;
    fireEvent.click(rolledBtn);
    const attemptCoords = Array.from(
      container.querySelectorAll("#colonies-attempts-tbody tr td:first-child")
    ).map((td) => td.textContent?.trim());
    expect(attemptCoords).toEqual(["02:381:08", "02:380:07"]);
  });

  it("sorts OperationsView priority queue by Priority and Wait ETA (keeping blocked rows at bottom)", async () => {
    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="operations" />
        <OperationsView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#plan-tbody tr")).toHaveLength(3);
    });

    const getPriorities = () =>
      Array.from(container.querySelectorAll("#plan-tbody tr td:first-child")).map((td) =>
        td.textContent?.trim()
      );

    expect(getPriorities()).toEqual(["P2", "P0", "P1"]);

    const prioBtn = Array.from(container.querySelectorAll("#ops-plan-table th button")).find(
      (b) => b.textContent?.trim() === "Priority"
    ) as HTMLButtonElement;
    fireEvent.click(prioBtn);
    expect(getPriorities()).toEqual(["P0", "P1", "P2"]);

    const waitBtn = Array.from(container.querySelectorAll("#ops-plan-table th button")).find(
      (b) => b.textContent?.trim() === "Wait ETA"
    ) as HTMLButtonElement;
    fireEvent.click(waitBtn);
    // Ready (P0) -> 2h 00m (P2) -> Blocked "—" (P1)
    expect(getPriorities()).toEqual(["P0", "P2", "P1"]);
  });

  it("sorts DashboardQueueCard planned items while keeping active EXEC rows pinned at top", () => {
    const execJobs: QueueJobData[] = [
      {
        category: "building",
        queue_slot: 1,
        name: "Pig iron mine",
        target_level: 15,
        remaining_seconds: 600,
      },
    ];
    const plannedActions = SORT_DASHBOARD.plan as PlannedActionData[];

    const { container } = render(
      <DashboardQueueCard
        cardId="card-test-bld"
        moduleCode="BLD"
        title="Buildings Queue"
        countId="cnt-test"
        addBtnId="add-test"
        addBtnAriaLabel="Add"
        onAddQueueClick={() => {}}
        gotoLabel="All"
        onGotoClick={() => {}}
        execTbodyId="dash-exec-test-tbody"
        planTbodyId="dash-plan-test-tbody"
        execJobs={execJobs}
        plannedActions={plannedActions}
        emptyText="Empty"
        nowEpochSec={1700000000}
        onDispatch={() => {}}
        onUpdateQueue={() => {}}
        onCancelQueue={() => {}}
        onPinQueue={() => {}}
      />
    );

    const prioBtn = Array.from(container.querySelectorAll("#card-test-bld th button")).find(
      (b) => b.textContent?.trim() === "Prio"
    ) as HTMLButtonElement;
    fireEvent.click(prioBtn);

    // Active EXEC job stays pinned in exec tbody
    expect(container.querySelector("#dash-exec-test-tbody")?.textContent).toContain("EXEC");
    // Planned rows sorted P0 -> P1 -> P2
    const planPrios = Array.from(
      container.querySelectorAll("#dash-plan-test-tbody tr td:first-child")
    ).map((td) => td.textContent?.trim());
    expect(planPrios).toEqual(["P0", "P1", "P2"]);
  });

  it("sorts BuildingsView unit table by Level and Unit name cleanly", async () => {
    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="buildings" />
        <BuildingsView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#eta-table-tbody tr")).toHaveLength(3);
    });

    const getUnitNames = () =>
      Array.from(container.querySelectorAll("#eta-table-tbody tr td:first-child")).map((td) =>
        td.textContent?.trim()
      );

    // Click "Lvl" -> descending first (14, 9, 6)
    const lvlBtn = Array.from(container.querySelectorAll("#bld-table th button")).find(
      (b) => b.textContent?.trim() === "Lvl"
    ) as HTMLButtonElement;
    fireEvent.click(lvlBtn);
    expect(getUnitNames()[0]).toContain("Pig iron mine");
    expect(getUnitNames()[1]).toContain("Blast furnace");
    expect(getUnitNames()[2]).toContain("Kryptonite mine");

    // Click "Unit" -> alphabetical (Blast furnace, Kryptonite mine, Pig iron mine)
    const unitBtn = Array.from(container.querySelectorAll("#bld-table th button")).find(
      (b) => b.textContent?.trim() === "Unit"
    ) as HTMLButtonElement;
    fireEvent.click(unitBtn);
    expect(getUnitNames()[0]).toContain("Blast furnace");
    expect(getUnitNames()[1]).toContain("Kryptonite mine");
    expect(getUnitNames()[2]).toContain("Pig iron mine");
  });
});
