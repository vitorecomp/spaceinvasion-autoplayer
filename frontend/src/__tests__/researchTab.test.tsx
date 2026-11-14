import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, CONSOLE_PAGE_ORDER } from "../App";
import { ConsoleProvider, resetConsoleStore, useConsoleActions } from "../store";
import { BuildingsView } from "../features/buildings";
import { ResearchView } from "../features/research";
import { PAGE_TITLES, type DashboardPayload } from "../types/api";

const RESEARCH_TAB_DASHBOARD: DashboardPayload = {
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
        fields_used: 95,
        fields_max: 230,
      },
    ],
    researches: { r1: 8, r2: 6, r6: 0 },
  },
  buildings_eta: {
    planet: {
      planet_id: 12476,
      name: "Homeworld",
      coords: "02:380:12",
      dev_center_level: 8,
      research_lab_level: 10,
      microsystem_level: 2,
      building_time_divisor: 36,
      research_time_divisor: 110,
    },
    planet_economy: {
      blast_furnace_pct: 100,
      pig_iron_per_hr: 5000,
      blast_furnace_pig_used_per_hr: 2000,
    },
    buildings: [
      {
        key: "b1",
        building_id: 1,
        name: "Pig iron mine",
        category: "mines",
        current_level: 18,
        next_level: 19,
        cost: { pig_iron: 9000, kryptonite: 3000 },
        missing: {},
        missing_total: 0,
        build_time_text: "35m",
        build_seconds: 2100,
        wait_eta: "Ready",
        wait_hours: 0,
        reach_eta: "35m",
        reach_hours: 0.58,
        can_build_live: true,
        unmet_prerequisites: [],
        horizons: [
          {
            level: 19,
            cost: { pig_iron: 9000, kryptonite: 3000 },
            build_time_text: "35m",
            wait_eta: "Ready",
            reach_eta: "35m",
          },
          {
            level: 20,
            cost: { pig_iron: 14000, kryptonite: 4800 },
            build_time_text: "55m",
            wait_eta: "1h 10m",
            reach_eta: "2h 05m",
          },
        ],
      },
      {
        key: "b2",
        building_id: 2,
        name: "Blast furnace",
        category: "mines",
        current_level: 14,
        next_level: 15,
        cost: { pig_iron: 6000, metal: 2000 },
        missing: { pig_iron: 1200 },
        missing_total: 1200,
        build_time_text: "25m",
        build_seconds: 1500,
        wait_eta: "15m",
        wait_hours: 0.25,
        reach_eta: "40m",
        reach_hours: 0.67,
        bottleneck: "pig_iron",
        can_build_live: false,
        unmet_prerequisites: [],
        horizons: [],
      },
      {
        key: "b5",
        building_id: 5,
        name: "Fusion plant",
        category: "energy",
        current_level: 16,
        next_level: 17,
        cost: { pig_iron: 8000, kryptonite: 3500 },
        missing: {},
        missing_total: 0,
        build_time_text: "42m",
        build_seconds: 2520,
        wait_eta: "Ready",
        wait_hours: 0,
        reach_eta: "42m",
        reach_hours: 0.7,
        can_build_live: true,
        unmet_prerequisites: [],
        horizons: [],
      },
    ],
    researches: [
      {
        key: "r1",
        research_id: 1,
        slug: "mining_technology",
        name: "Mining technology",
        current_level: 8,
        next_level: 9,
        cost: { pig_iron: 12000, metal: 4000, kryptonite: 6000, spice: 1000 },
        missing: {},
        missing_total: 0,
        build_time_text: "1h 20m",
        build_time_seconds: 4800,
        wait_eta: "Ready",
        wait_hours: 0,
        reach_eta: "1h 20m",
        reach_hours: 1.33,
        can_research_live: true,
        active: false,
        unmet_prerequisites: [],
        horizons: [
          {
            level: 9,
            cost: { pig_iron: 12000, metal: 4000, kryptonite: 6000, spice: 1000 },
            build_time_text: "1h 20m",
            wait_eta: "Ready",
            reach_eta: "1h 20m",
            bottleneck: null,
          },
          {
            level: 10,
            cost: { pig_iron: 24000, metal: 8000, kryptonite: 12000, spice: 2000 },
            build_time_text: "2h 40m",
            wait_eta: "3h 15m",
            reach_eta: "7h 15m",
            bottleneck: "kryptonite",
          },
          {
            level: 11,
            cost: { pig_iron: 48000, metal: 16000, kryptonite: 24000, spice: 4000 },
            build_time_text: "5h 20m",
            wait_eta: "9h 00m",
            reach_eta: "18h 20m",
            bottleneck: "kryptonite",
          },
        ],
      },
      {
        key: "r2",
        research_id: 2,
        slug: "energy_technology",
        name: "Energy technology",
        current_level: 6,
        next_level: 7,
        cost: { pig_iron: 8000, metal: 2000, kryptonite: 12000, spice: 4000 },
        missing: { pig_iron: 2500 },
        missing_total: 2500,
        build_time_text: "50m",
        build_time_seconds: 3000,
        wait_eta: "30m",
        wait_hours: 0.5,
        reach_eta: "1h 20m",
        reach_hours: 1.33,
        bottleneck: "pig_iron",
        can_research_live: false,
        active: true,
        unmet_prerequisites: [],
        horizons: [
          {
            level: 7,
            cost: { pig_iron: 8000, metal: 2000, kryptonite: 12000, spice: 4000 },
            build_time_text: "50m",
            wait_eta: "30m",
            reach_eta: "1h 20m",
            bottleneck: "pig_iron",
          },
        ],
      },
      {
        key: "r6",
        research_id: 6,
        slug: "warp_drive",
        name: "Warp drive",
        current_level: 0,
        next_level: 1,
        cost: { pig_iron: 40000, metal: 20000, kryptonite: 30000, spice: 15000 },
        missing: { metal: 20000 },
        missing_total: 20000,
        build_time_text: "4h 10m",
        build_time_seconds: 15000,
        wait_eta: "6h 00m",
        wait_hours: 6,
        reach_eta: "10h 10m",
        reach_hours: 10.17,
        bottleneck: "metal",
        can_research_live: false,
        active: false,
        unmet_prerequisites: ["Research lab L7", "Diffusion engine L5"],
        horizons: [
          {
            level: 1,
            cost: { pig_iron: 40000, metal: 20000, kryptonite: 30000, spice: 15000 },
            build_time_text: "4h 10m",
            wait_eta: "6h 00m",
            reach_eta: "10h 10m",
            bottleneck: "metal",
          },
        ],
      },
    ],
  },
  universe_research_max: [
    {
      research_id: 1,
      key: "r1",
      slug: "mining_technology",
      name: "Mining technology",
      own_level: 8,
      universe_max_level: 22,
      spied_max_level: 19,
      catalog_max_level: 30,
      gap_to_max: 14,
      holder: "StarLord",
      holder_coords: "01:100:08",
      updated_ts: 1700000400,
    },
    {
      research_id: 2,
      key: "r2",
      slug: "energy_technology",
      name: "Energy technology",
      own_level: 6,
      universe_max_level: 16,
      spied_max_level: 15,
      catalog_max_level: 25,
      gap_to_max: 10,
      holder: "NovaTech",
      holder_coords: "02:200:04",
      updated_ts: 1700000300,
    },
  ],
};

function ActivatePage({ page }: { page: string }) {
  const { setActivePage } = useConsoleActions();
  useEffect(() => {
    setActivePage(page as any);
  }, [page, setActivePage]);
  return null;
}

describe("Dedicated Research tab split from BuildingsView", () => {
  let commandCalls: Array<Record<string, any>>;
  let addedResearchTitleShim = false;

  beforeEach(() => {
    // Allow isolated pre-merge execution before @dash-ui adds "research" to types/api.ts
    if (!("research" in PAGE_TITLES)) {
      (PAGE_TITLES as Record<string, string>).research = "RESEARCH // LEVEL ETAS";
      addedResearchTitleShim = true;
    } else {
      addedResearchTitleShim = false;
    }
    resetConsoleStore({ clearStorage: true });
    sessionStorage.clear();
    window.history.replaceState(null, "", "#dashboard");
    commandCalls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(RESEARCH_TAB_DASHBOARD),
          };
        }
        if (String(url).startsWith("/ui-version")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify({ ok: true, code_version: "v1" }),
          };
        }
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        commandCalls.push(body);
        if (body.command === "buildings_eta") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({ ok: true, buildings_eta: RESEARCH_TAB_DASHBOARD.buildings_eta }),
          };
        }
        if (body.command === "universe_research_max") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                rows: RESEARCH_TAB_DASHBOARD.universe_research_max,
                message: "Universe Max Research synced.",
              }),
          };
        }
        return {
          ok: true,
          status: 200,
          text: async () => JSON.stringify({ ok: true, message: "OK" }),
        };
      })
    );
  });

  afterEach(() => {
    cleanup();
    resetConsoleStore({ clearStorage: true });
    vi.restoreAllMocks();
    if (addedResearchTitleShim) {
      delete (PAGE_TITLES as Record<string, string>).research;
      addedResearchTitleShim = false;
    }
  });

  it("renders BuildingsView with buildings only, clean header/meta, and zero research tabs or rows", async () => {
    // Clear hash and seed a legacy persisted research category to verify safe fallback to "all"
    window.history.replaceState(null, "", "/");
    sessionStorage.setItem(
      "si_console_ui_state",
      JSON.stringify({ activePage: "buildings", bldCategory: "research" })
    );

    const { container } = render(
      <ConsoleProvider>
        <BuildingsView />
        <ResearchView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#eta-table-tbody tr")).toHaveLength(3);
    });

    const bldSection = container.querySelector("#view-buildings") as HTMLElement;
    const resSection = container.querySelector("#view-research") as HTMLElement;
    expect(bldSection.hidden).toBe(false);
    expect(resSection.hidden).toBe(true);

    // Header title and building-specific metadata
    expect(bldSection.querySelector("h2")?.textContent).toBe("Buildings — Level Reach Estimates");
    const bldMeta = container.querySelector("#bld-planet-meta")?.textContent || "";
    expect(bldMeta).toBe("[02:380:12] · Dev L8 · Micro L2 · Div x36");
    expect(bldMeta).not.toContain("Lab L");

    // Category filters contain only building tabs (no research or universe_research)
    const filterButtons = Array.from(container.querySelectorAll("#bld-category-filters button")).map(
      (b) => b.getAttribute("data-bcat")
    );
    expect(filterButtons).toEqual([
      "buildings",
      "mines",
      "energy",
      "infra",
      "storage",
      "special",
      "defense",
    ]);
    expect(bldSection.querySelector('[data-bcat="research"]')).toBeNull();
    expect(bldSection.querySelector('[data-bcat="universe_research"]')).toBeNull();

    // Table shows only buildings, never research technologies
    const tbodyText = container.querySelector("#eta-table-tbody")?.textContent || "";
    expect(tbodyText).toContain("Pig iron mine");
    expect(tbodyText).toContain("Blast furnace");
    expect(tbodyText).toContain("Fusion plant");
    expect(tbodyText).not.toContain("Mining technology");
    expect(tbodyText).not.toContain("Energy technology");
    expect(tbodyText).not.toContain("Warp drive");
  });

  it("renders ResearchView on activePage === 'research' with header, filtering, sorting, inspector, target level selector, and actions", async () => {
    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="research" />
        <BuildingsView />
        <ResearchView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#res-eta-tbody tr")).toHaveLength(3);
    });

    const bldSection = container.querySelector("#view-buildings") as HTMLElement;
    const resSection = container.querySelector("#view-research") as HTMLElement;
    expect(bldSection.hidden).toBe(true);
    expect(resSection.hidden).toBe(false);
    expect(window.location.hash).toBe("#research");

    // Header title & Lab/Micro/Div metadata
    expect(resSection.querySelector("h2")?.textContent).toBe("Research — Level Reach Estimates");
    expect(container.querySelector("#res-planet-meta")?.textContent).toBe(
      "[02:380:12] · Lab L10 · Micro L2 · Div x110"
    );

    // All research rows displayed, no building rows
    const resTbodyText = container.querySelector("#res-eta-tbody")?.textContent || "";
    expect(resTbodyText).toContain("Mining technology");
    expect(resTbodyText).toContain("Energy technology");
    expect(resTbodyText).toContain("ACTIVE");
    expect(resTbodyText).toContain("Warp drive");
    expect(resTbodyText).toContain("Locked");
    expect(resTbodyText).not.toContain("Pig iron mine");

    // Inspector shows r1 by default with Universe Max comparison and target level selector
    expect(container.querySelector("#res-insp-key")?.textContent).toBe("R1");
    expect(container.querySelector("#res-insp-title")?.textContent).toBe("Mining technology");
    expect(container.querySelector("#res-insp-meta-box")?.textContent).toContain("StarLord");

    // Select target level L10 via target level selector -> updates cost/ETA and pin button
    const tgtL10Btn = container.querySelector(
      '#res-insp-target-selector button[data-target-level="10"]'
    ) as HTMLButtonElement;
    expect(tgtL10Btn).not.toBeNull();
    fireEvent.click(tgtL10Btn);
    expect(container.querySelector("#res-insp-meta-box")?.textContent).toContain("Target L10 Cost");
    expect(container.querySelector("#res-insp-meta-box")?.textContent).toContain("7h 15m");
    expect(container.querySelector("#btn-res-insp-pin-queue")?.textContent).toContain(
      "+ Pin L10 to Queue (P1)"
    );

    // Pin L10 to queue -> sends RESEARCH command with target_level 10
    fireEvent.click(container.querySelector("#btn-res-insp-pin-queue")!);
    await waitFor(() => {
      expect(
        commandCalls.some(
          (c) =>
            c.command === "queue_add" &&
            c.kind === "RESEARCH" &&
            c.target_id === 1 &&
            c.target_level === 10
        )
      ).toBe(true);
    });

    // Live Start Research button sends research command
    fireEvent.click(container.querySelector("#btn-res-insp-research")!);
    await waitFor(() => {
      expect(
        commandCalls.some(
          (c) => c.command === "research" && c.research_id === 1 && c.planet_id === 12476
        )
      ).toBe(true);
    });

    // Click Warp drive row -> inspector shows unmet prerequisites
    const warpRow = Array.from(container.querySelectorAll("#res-eta-tbody tr")).find((tr) =>
      tr.textContent?.includes("Warp drive")
    ) as HTMLTableRowElement;
    fireEvent.click(warpRow);
    expect(container.querySelector("#res-insp-key")?.textContent).toBe("R6");
    expect(container.querySelector("#res-insp-meta-box")?.textContent).toContain(
      "Research lab L7, Diffusion engine L5"
    );

    // Filter sub-tabs: Locked -> 1 row (Warp drive), Unlocked -> 2 rows
    fireEvent.click(container.querySelector('[data-res-tab="locked"]')!);
    expect(container.querySelectorAll("#res-eta-tbody tr")).toHaveLength(1);
    expect(container.querySelector("#res-eta-tbody")?.textContent).toContain("Warp drive");

    fireEvent.click(container.querySelector('[data-res-tab="unlocked"]')!);
    expect(container.querySelectorAll("#res-eta-tbody tr")).toHaveLength(2);

    // Back to All and sort by Technology alphabetically
    fireEvent.click(container.querySelector('[data-res-tab="all"]')!);
    const techSortBtn = Array.from(container.querySelectorAll("#res-table th button")).find(
      (b) => b.textContent?.trim() === "Technology"
    ) as HTMLButtonElement;
    fireEvent.click(techSortBtn);
    const ordered = Array.from(
      container.querySelectorAll("#res-eta-tbody tr td:first-child")
    ).map((td) => td.textContent?.trim());
    expect(ordered[0]).toContain("Energy technology");
    expect(ordered[1]).toContain("Mining technology");
    expect(ordered[2]).toContain("Warp drive");

    // Refresh ETAs button calls buildings_eta with refresh: true
    fireEvent.click(container.querySelector("#btn-res-refresh-eta")!);
    await waitFor(() => {
      expect(
        commandCalls.some((c) => c.command === "buildings_eta" && c.refresh === true)
      ).toBe(true);
    });
  });

  it("renders Universe Max Research sub-tab inside ResearchView and syncs records", async () => {
    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="research" />
        <ResearchView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#res-eta-tbody tr")).toHaveLength(3);
    });

    const uniTab = container.querySelector('[data-res-tab="universe_research"]')!;
    fireEvent.click(uniTab);

    const uniPane = container.querySelector("#res-pane-universe-research") as HTMLElement;
    expect(uniPane.hidden).toBe(false);
    expect(container.querySelector("#res-universe-research-tbody")?.textContent).toContain(
      "StarLord [01:100:08]"
    );

    fireEvent.click(container.querySelector("#btn-res-refresh-universe-research")!);
    await waitFor(() => {
      expect(
        commandCalls.some((c) => c.command === "universe_research_max" && c.sync === true)
      ).toBe(true);
    });
  });

  it("restores ResearchView directly from #research URL hash on initial mount", async () => {
    window.history.replaceState(null, "", "#research");

    const { container } = render(
      <ConsoleProvider>
        <BuildingsView />
        <ResearchView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#res-eta-tbody tr")).toHaveLength(3);
    });

    const bldSection = container.querySelector("#view-buildings") as HTMLElement;
    const resSection = container.querySelector("#view-research") as HTMLElement;
    expect(bldSection.hidden).toBe(true);
    expect(resSection.hidden).toBe(false);
  });

  it("preserves strict zero-SVG, unique-ID, and attribute-safety invariants across BuildingsView and ResearchView", async () => {
    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="research" />
        <BuildingsView />
        <ResearchView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#res-eta-tbody tr")).toHaveLength(3);
    });

    // Zero <svg> elements added
    expect(container.querySelectorAll("svg")).toHaveLength(0);

    // Every DOM id across mounted BuildingsView + ResearchView must be unique
    const allIds = Array.from(container.querySelectorAll("[id]")).map((el) => el.id);
    expect(new Set(allIds).size).toBe(allIds.length);

    // No literal '>' inside any HTML attribute value (prerender.mjs invariant)
    const allEls = Array.from(container.querySelectorAll("*"));
    for (const el of allEls) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.value).not.toContain(">");
      }
    }
  });

  it("orders ResearchView immediately after BuildingsView, mounts only active page in App, and removes FeedbackBanner from .main-column", () => {
    const bldIdx = CONSOLE_PAGE_ORDER.indexOf("buildings");
    const resIdx = CONSOLE_PAGE_ORDER.indexOf("research");
    expect(bldIdx).toBeGreaterThanOrEqual(0);
    expect(resIdx).toBe(bldIdx + 1);

    const { container } = render(<App disableInitialFetch={true} />);

    const stage = container.querySelector("#view-stage");
    expect(stage).not.toBeNull();
    // Only the active page (dashboard) is mounted initially
    expect(stage?.querySelectorAll(".page-view")).toHaveLength(1);
    expect(stage?.querySelector("#view-dashboard")).not.toBeNull();

    // Switching to buildings mounts only BuildingsView; switching to research mounts only ResearchView
    const navBld = container.querySelector('.sidebar-link[data-page="buildings"]') as HTMLButtonElement;
    const navRes = container.querySelector('.sidebar-link[data-page="research"]') as HTMLButtonElement;
    expect(navBld).not.toBeNull();
    expect(navRes).not.toBeNull();
    fireEvent.click(navBld);
    expect(stage?.querySelectorAll(".page-view")).toHaveLength(1);
    expect(stage?.querySelector("#view-buildings")).not.toBeNull();
    fireEvent.click(navRes);
    expect(stage?.querySelectorAll(".page-view")).toHaveLength(1);
    expect(stage?.querySelector("#view-research")).not.toBeNull();

    // FeedbackBanner must no longer be mounted inside .main-column (moved to sidebar by @dash-ui)
    expect(container.querySelector(".main-column #feedback-banner")).toBeNull();

    // Strict <= 2 SVG invariant (0 on research, 1 on dashboard, 1 on ranking)
    expect(container.querySelectorAll("svg").length).toBeLessThanOrEqual(2);
  });

  it("displays Config Optimizer building level goals, specialization, sorting, and inspector line in BuildingsView", async () => {
    const dashWithGoals: DashboardPayload = {
      ...RESEARCH_TAB_DASHBOARD,
      config_optimizer_goals: {
        run_id: 42,
        updated_ts: 1700000100,
        by_planet_id: {
          "12476": {
            planet_id: 12476,
            coords: "02:380:12",
            specialization: "pig_iron",
            goals: { b1: 25, b2: 14, b5: 12 },
          },
        },
        by_coords: {
          "02:380:12": {
            planet_id: 12476,
            coords: "02:380:12",
            specialization: "pig_iron",
            goals: { b1: 25, b2: 14, b5: 12 },
          },
        },
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).startsWith("/dashboard")) {
          return { ok: true, status: 200, text: async () => JSON.stringify(dashWithGoals) };
        }
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      })
    );

    const { container } = render(
      <ConsoleProvider>
        <ActivatePage page="buildings" />
        <BuildingsView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#eta-table-tbody tr")).toHaveLength(3);
    });

    // Planet meta bar displays Config Optimizer specialization
    expect(container.querySelector("#bld-planet-meta")?.textContent).toContain("Goal: Pig Iron");

    // Goal column header exists and cells show remaining delta (+7), reached (L14), and above-goal (L12)
    const goalSortBtn = Array.from(container.querySelectorAll("#bld-table th button")).find(
      (b) => b.textContent?.trim() === "Goal"
    ) as HTMLButtonElement | undefined;
    expect(goalSortBtn).toBeDefined();
    const goalCells = Array.from(
      container.querySelectorAll('#eta-table-tbody tr td[data-col="goal"]')
    ).map((td) => td.textContent?.trim());
    expect(goalCells).toEqual(["L25 +7", "L14", "L12"]);

    // Clicking Goal sort header sorts descending first (25, 14, 12), then ascending (12, 14, 25)
    fireEvent.click(goalSortBtn!);
    fireEvent.click(goalSortBtn!);
    const ascGoals = Array.from(
      container.querySelectorAll('#eta-table-tbody tr td[data-col="goal"]')
    ).map((td) => td.textContent?.trim());
    expect(ascGoals).toEqual(["L12", "L14", "L25 +7"]);

    // BuildingInspector shows Config Optimizer Goal detail for the selected building (b1: L18 -> L25)
    expect(container.querySelector("#insp-meta-box")?.textContent).toContain(
      "Config Optimizer Goal: L25 (+7 remaining) · Pig Iron"
    );
  });
});
