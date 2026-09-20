import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../context/ConsoleContext";
import { ActionGraphView } from "../features/action-graph";
import { GaNnScannerHud } from "../components/layout/GaNnScannerHud";
import { ConfigView } from "../features/config";
import type { DashboardPayload } from "../types/api";

function makeDashboard({
  coloniesOwned = 7,
  maxPlanets = 11,
  plannerMode = "priority",
  liveAiUnlocked = coloniesOwned >= maxPlanets,
  pendingConflicts = [] as Array<Record<string, any>>,
}: {
  coloniesOwned?: number;
  maxPlanets?: number;
  plannerMode?: string;
  liveAiUnlocked?: boolean;
  pendingConflicts?: Array<Record<string, any>>;
} = {}): DashboardPayload {
  const planets = Array.from({ length: coloniesOwned }, (_, idx) => ({
    planet_id: 100 + idx,
    name: idx === 0 ? "Homeworld" : `Colony ${idx + 1}`,
    coords: `02:380:${idx + 1}`,
    planet_type: "normal",
    temperature: 25 + idx * 4,
    fields_used: 180,
    fields_max: 300,
    resources: { pig_iron: 50000, metal: 30000, kryptonite: 20000, spice: 10000 },
    storage: { pig_iron: 200000, metal: 200000, kryptonite: 200000, spice: 200000 },
    energy: { produced: 1400, used: 900, net: 500 },
    buildings: { b1: 12, b2: 10, b3: 10, b4: 10, b5: 12, b9: 8, b10: 6, b11: 8, b12: 8 },
    ships: { s6: 12 + idx, s10: 15, s11: 5 },
  }));

  return {
    ok: true,
    planner_mode: plannerMode,
    effective_planner_mode: liveAiUnlocked && plannerMode === "ai_planner" ? "ai_planner" : "priority",
    colonies_owned: coloniesOwned,
    max_planets: maxPlanets,
    live_ai_unlocked: liveAiUnlocked,
    pending_field_conflicts: pendingConflicts as any,
    planner_jobs_status: {
      ok: true,
      colonies_owned: coloniesOwned,
      max_planets: maxPlanets,
      live_ai_unlocked: liveAiUnlocked,
      config_optimizer_job: { status: "completed", progress_pct: 100, elapsed_ms: 142 },
      ai_planner_job: { status: "completed", progress_pct: 100, elapsed_ms: 318 },
      pending_field_conflicts: pendingConflicts as any,
    },
    health: { ok: true, in_game: true, debug_port_open: true, debug_port: 9222 },
    config: {
      planner_mode: plannerMode,
      playstyle: "fleeter",
      allow_research: true,
      allow_shipyard: true,
      allow_multi_planet: true,
      allow_raids: true,
      allow_pvp_in_az: false,
      allow_fleetsave: true,
      allow_galaxy_scan: true,
      allow_colonize: true,
      allow_transport: true,
      allow_transmitter: false,
      combat_safety_margin: 1.45,
      raid_min_loot_spice_eq: 2000,
      fleet_save_fuel_reserve_spice: 600,
      ai_resource_spare_pct: 15,
      min_colony_fields: 300,
      max_planets: maxPlanets,
      shipyard_targets: {
        "small-fighter": 0,
        "small-transport": 20,
        "large-transport": 120,
        "spy-probe": 40,
        "colony-ship": 1,
        recycler: 25,
        starcruiser: 30,
        phoenix: 10,
        transmitter: 5,
        battleship: 4,
        "stealth-bomber": 0,
        destroyer: 2,
      },
    },
    state: {
      universe_id: 31,
      user_id: 103603,
      nickname: "ventkx86",
      active_planet_id: 100,
      planets,
      researches: { r1: 12, r2: 14, r3: 10, r14: 4 },
      active_queues: [
        {
          category: "fleet",
          name: "Raid -> Inactive",
          planet_id: 100,
          planet_coords: "02:380:1",
          remaining_text: "4m 12s",
        },
      ],
    },
    universe_research_max: [
      { research_id: 1, key: "r1", slug: "mining-technology", name: "Mining technology", own_level: 12, universe_max_level: 30 },
      { research_id: 2, key: "r2", slug: "energy-technology", name: "Energy technology", own_level: 14, universe_max_level: 28 },
    ],
    plan: [
      {
        priority: "P2",
        kind: "BUILD",
        planet_id: 100,
        planet_coords: "02:380:1",
        target_id: 1,
        target_name: "Pig iron mine",
        target_level: 13,
        cost: { pig_iron: 5000, kryptonite: 1800 },
        missing: {},
        reason: "Expand production",
        executable_now: true,
        wait_eta: "Ready",
        duration_text: "3m 15s",
        total_eta: "3m 15s",
      },
      {
        priority: "P2",
        kind: "RAID",
        planet_id: 100,
        planet_coords: "02:380:1",
        target_name: "Inactive 02:381:5",
        reason: "High loot/h target",
        executable_now: true,
        wait_eta: "Now",
        total_eta: "18m",
      },
    ],
    ai_planner_summary: {
      reward_weeks: 26,
      discounted_reward_spice_eq: 125000000,
      total_net_spice_eq: 380000000,
      steps: 42,
      steps_completed: 4,
      elapsed_ms: 318,
      attack_telemetry: { hourly_loot_spice_eq: 45000, median_round_trip_seconds: 1320 },
    },
  };
}

const Harness: React.FC<{ initialDashboard: DashboardPayload; children: React.ReactNode }> = ({
  initialDashboard,
  children,
}) => {
  const Inner: React.FC = () => {
    const { setDashboard, setActivePage } = useConsole();
    useEffect(() => {
      setDashboard(initialDashboard);
      setActivePage("action-graph");
    }, [initialDashboard, setDashboard, setActivePage]);
    return null;
  };
  return (
    <ConsoleProvider disableInitialFetch>
      <Inner />
      {children}
    </ConsoleProvider>
  );
};

describe("ActionGraph 4-Tab Workspace", () => {
  let postedCommands: Array<Record<string, any>> = [];
  let mockDashboardState: DashboardPayload;
  let mockConflicts: Array<Record<string, any>> = [];

  beforeEach(() => {
    postedCommands = [];
    mockConflicts = [
      {
        conflict_key: "pid:100:blocked:b3:l22",
        planet_id: 100,
        planet_coords: "02:380:1",
        planet_name: "Homeworld",
        blocked_building_key: "b3",
        blocked_building_name: "Kryptonite mine",
        blocked_target_level: 22,
        candidate_building_key: "b15",
        candidate_level: 3,
        candidates: [{ building_key: "b15", name: "Particle cannon", level: 3, safe_to_demolish: true }],
        status: "pending",
      },
    ];
    mockDashboardState = makeDashboard({
      coloniesOwned: 7,
      maxPlanets: 11,
      plannerMode: "priority",
      pendingConflicts: mockConflicts,
    });
    sessionStorage.clear();
    HTMLAnchorElement.prototype.click = vi.fn();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(mockDashboardState),
          };
        }
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        postedCommands.push(body);
        const cmd = body.command;

        if (cmd === "planner_jobs_status") {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(mockDashboardState.planner_jobs_status),
          };
        }
        if (cmd === "config_optimizer_get" || cmd === "config_optimizer_run") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                colonies_owned: mockDashboardState.colonies_owned,
                max_planets: mockDashboardState.max_planets,
                job: { status: "completed", progress_pct: 100, elapsed_ms: 142 },
                active: {
                  run_id: 12,
                  updated_ts: 1763400000,
                  params: {
                    target_ship_key: body.params?.target_ship_key || "s16",
                    pad_generic_colonies: Boolean(body.params?.pad_generic_colonies),
                  },
                  result: {
                    elapsed_ms: 142,
                    inputs: { own_research_levels: { r1: 12, r2: 14 }, universe_research_max_levels: { r1: 30, r2: 28 } },
                    planned_research_levels: { r1: 24, r2: 22 },
                    summary: {
                      ships_per_month: 84.5,
                      whole_ships_per_month: 84,
                      target_ship_key: body.target_ship_key || "s16",
                      limiting_factor: "kryptonite",
                      fields_used: 245,
                      fields_max: 300,
                      warehouse_fields: 36,
                    },
                    planets: [
                      {
                        slot: 1,
                        planet_id: 100,
                        name: "Homeworld",
                        coords: "02:380:1",
                        temperature: 25,
                        fields_max: 300,
                        fields_used_target: 245,
                        is_central_planet: true,
                        detected_specialization: "kryptonite",
                        locked_specialization: "auto",
                        previous_specialization: "kryptonite",
                        specialization_changed: false,
                        built_floors: { b1: 12, b3: 10, b11: 8 },
                        reserved_floors: { b15: 3 },
                        buildings: {
                          b1: { built: 12, target_level: 32, last_level_time: "14h 10m", last_level_time_seconds: 51000 },
                          b3: { built: 10, target_level: 30, last_level_time: "16h 20m", last_level_time_seconds: 58800 },
                        },
                        warehouses: [{ key: "b11", level: 16, set_by: "warehouse_hours" }],
                        production: { monthly_ships: 11.4, monthly_spice_eq: 52000000 },
                      },
                    ],
                  },
                },
              }),
          };
        }
        if (cmd === "config_optimizer_export") {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify({ ok: true, filename: "config_optimizer_result.json", content: '{"ships_per_month":84.5}' }),
          };
        }
        if (cmd === "config_optimizer_set_specializations") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                planet_specializations: body.planet_specializations || {},
                locked_planets_count: Object.keys(body.planet_specializations || {}).length,
              }),
          };
        }
        if (cmd === "ai_planner_get" || cmd === "ai_planner_run") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                has_plan: true,
                colonies_owned: mockDashboardState.colonies_owned,
                max_planets: mockDashboardState.max_planets,
                live_ai_unlocked: mockDashboardState.live_ai_unlocked,
                pending_field_conflicts: mockConflicts,
                job: { status: "completed", progress_pct: 100, elapsed_ms: 318 },
                active_summary: mockDashboardState.ai_planner_summary,
                attack_telemetry: {
                  hourly_loot_spice_eq: 45000,
                  median_loot_spice_eq: 16500,
                  median_round_trip_seconds: 1320,
                  current_fleet_slots: 12,
                },
                weekly_breakdown: [
                  {
                    week: 1,
                    discount_weight: 1.0,
                    pig_iron: 2500000,
                    metal: 1500000,
                    kryptonite: 900000,
                    spice: 600000,
                    spice_eq: 12500000,
                    discounted_spice_eq: 12500000,
                    cumulative_discounted_spice_eq: 12500000,
                  },
                ],
                steps_page: {
                  total: 1,
                  page: 1,
                  page_size: 25,
                  items: [
                    {
                      seq: 1,
                      week: 1,
                      kind: "BUILD",
                      planet_id: 100,
                      planet_coords: "02:380:1",
                      planet_name: "Homeworld",
                      entity_key: "b3",
                      entity_name: "Kryptonite mine",
                      target_level: 11,
                      start: "0m",
                      duration_text: "18m",
                      status: "queued",
                    },
                  ],
                },
                graph: {
                  planner_mode: "ai_planner",
                  lanes: [{ planet_id: 100, planet_coords: "02:380:1", planet_name: "Homeworld", energy: 500 }],
                  nodes: [
                    {
                      id: "ai_step_1",
                      layer: 0,
                      seq: 1,
                      week: 1,
                      planet_id: 100,
                      planet_coords: "02:380:1",
                      kind: "BUILD",
                      priority: "P2",
                      status: "ready",
                      target_name: "Kryptonite mine",
                      target_level: 11,
                      reason: "High NPV ROI",
                      wait_eta: "0m",
                      total_eta: "18m",
                    },
                  ],
                  edges: [],
                  summary: { node_count: 1, edge_count: 0, layer_count: 1 },
                },
              }),
          };
        }
        if (cmd === "ai_planner_export") {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify({ ok: true, filename: "full_planner_path.txt", content: "#1 W1 BUILD b3 L11" }),
          };
        }
        if (cmd === "resolve_field_conflict") {
          mockConflicts = [];
          mockDashboardState = { ...mockDashboardState, pending_field_conflicts: [] };
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify({ ok: true, decision: body.decision, pending_field_conflicts: [] }),
          };
        }
        if (cmd === "fleet_planner_get") {
          const spare = body.config?.ai_resource_spare_pct ?? mockDashboardState.config?.ai_resource_spare_pct ?? 15;
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                slot_budgets: [
                  {
                    planet_id: 100,
                    planet_coords: "02:380:1",
                    planet_name: "Homeworld",
                    b10_level: 6,
                    slots_used: 2,
                    slots_max: 7,
                    active_flights: 2,
                    docked_ships_total: 42,
                    planned_actions: 1,
                    status: "OPEN",
                  },
                ],
                attack_telemetry: { hourly_loot_spice_eq: 45000, median_round_trip_seconds: 1320 },
                spare_budget: { ai_resource_spare_pct: spare },
                config: mockDashboardState.config,
                graph: {
                  planner_mode: "fleet_planner",
                  lanes: [{ planet_id: 100, planet_coords: "02:380:1", planet_name: "Homeworld", slots_used: 2, slots_max: 7 }],
                  nodes: [
                    {
                      id: "flt_1",
                      layer: 0,
                      planet_id: 100,
                      planet_coords: "02:380:1",
                      kind: "RAID",
                      priority: "P2",
                      status: "ready",
                      target_name: "Inactive 02:381:5",
                      reason: "High loot target",
                      wait_eta: "Now",
                      total_eta: "18m",
                    },
                  ],
                  edges: [],
                  summary: { node_count: 1, edge_count: 0, layer_count: 1 },
                },
              }),
          };
        }
        if (cmd === "update_config") {
          const nextCfg = { ...(mockDashboardState.config || {}), ...(body.config || {}) };
          mockDashboardState = { ...mockDashboardState, config: nextCfg };
          return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true, config: nextCfg }) };
        }
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      }) as any
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders all 4 sub-tabs, preserves Heuristics contracts, and loads Config Optimization targets & export", async () => {
    render(
      <Harness initialDashboard={mockDashboardState}>
        <ActionGraphView />
      </Harness>
    );

    expect(document.querySelector('#ag-workspace-tablist [data-ag-tab="heuristics"]')?.getAttribute("aria-selected")).toBe("true");
    expect(document.getElementById("ag-main-window")).not.toBeNull();
    expect(document.getElementById("ag-ai-handoff-card")?.textContent).toContain("7/11 PREVIEW ONLY");

    // Switch to Tab 2: Config Optimization
    fireEvent.click(document.getElementById("ag-tab-btn-config-optimizer")!);
    await waitFor(() => {
      expect(document.getElementById("co-kpi-ships")?.textContent).toContain("84.50");
    });
    expect(document.getElementById("co-kpi-limit")?.textContent).toBe("kryptonite");
    expect(document.getElementById("co-planets-table")?.textContent).toContain("02:380:1");

    // Change target ship, lock planet specialization, adjust switch threshold & toggle padding -> Run Config Optimizer
    fireEvent.change(document.getElementById("co-spec-select-1")!, { target: { value: "kryptonite" } });
    await waitFor(() => {
      const lockCmd = postedCommands.find((c) => c.command === "config_optimizer_set_specializations");
      expect(lockCmd?.planet_specializations).toEqual({ "100": "kryptonite" });
    });

    fireEvent.change(document.getElementById("co-param-specialization_switch_threshold_pct")!, {
      target: { value: "1.5" },
    });
    fireEvent.change(document.getElementById("co-param-target_ship_key")!, { target: { value: "s6" } });
    fireEvent.click(document.getElementById("co-param-pad_generic_colonies")!);
    fireEvent.click(document.getElementById("btn-co-run")!);

    await waitFor(() => {
      const runCmd = postedCommands.find((c) => c.command === "config_optimizer_run");
      expect(runCmd).toBeDefined();
      expect(runCmd?.params?.target_ship_key).toBe("s6");
      expect(runCmd?.params?.pad_generic_colonies).toBe(true);
      expect(runCmd?.params?.preserve_previous_specializations).toBe(true);
      expect(runCmd?.params?.specialization_switch_threshold_pct).toBe(1.5);
      expect(runCmd?.params?.planet_specializations).toEqual({ "100": "kryptonite" });
    });

    // Lock Current Roles & Unlock All toolbar actions
    fireEvent.click(document.getElementById("btn-co-unlock-all")!);
    await waitFor(() => {
      const unlockCmd = [...postedCommands]
        .reverse()
        .find((c) => c.command === "config_optimizer_set_specializations");
      expect(unlockCmd?.planet_specializations).toEqual({});
    });
    fireEvent.click(document.getElementById("btn-co-lock-all")!);
    await waitFor(() => {
      const lockAllCmd = [...postedCommands]
        .reverse()
        .find((c) => c.command === "config_optimizer_set_specializations");
      expect(lockAllCmd?.planet_specializations).toEqual({ "100": "kryptonite" });
    });

    // Switch to Research sub-tab and trigger Export JSON
    fireEvent.click(document.getElementById("co-subtab-research")!);
    expect(document.getElementById("co-research-table")?.textContent).toContain("Mining technology");

    fireEvent.click(document.getElementById("btn-co-export")!);
    await waitFor(() => {
      expect(postedCommands.some((c) => c.command === "config_optimizer_export")).toBe(true);
    });
  });

  it("enforces 7/11 vs 11/11 Colony Gate and handles Field Conflict Demolish & Keep/Reserve actions", async () => {
    render(
      <Harness initialDashboard={mockDashboardState}>
        <ActionGraphView />
      </Harness>
    );

    fireEvent.click(document.getElementById("ag-tab-btn-ai-planner")!);
    await waitFor(() => {
      expect(document.getElementById("ai-kpi-reward")?.textContent).toContain("125.00M");
    });

    // Colony gate locked at 7/11
    expect(document.getElementById("ag-ai-colony-gate")?.textContent).toContain("7 / 11 colonies founded");
    expect((document.getElementById("btn-ai-activate") as HTMLButtonElement).disabled).toBe(true);

    // Interactive field conflict decision banner present -> click Keep & Reserve
    expect(document.getElementById("ag-ai-field-conflicts")?.textContent).toContain("Kryptonite mine");
    fireEvent.click(document.getElementById("btn-conflict-reserve-0")!);

    await waitFor(() => {
      const resCmd = postedCommands.find((c) => c.command === "resolve_field_conflict");
      expect(resCmd).toBeDefined();
      expect(resCmd?.decision).toBe("keep_reserve");
      expect(resCmd?.building_key).toBe("b15");
    });

    // Now simulate 11/11 founded colonies unlocking Live AI-Planner execution
    cleanup();
    const unlockedDash = makeDashboard({
      coloniesOwned: 11,
      maxPlanets: 11,
      plannerMode: "priority",
      liveAiUnlocked: true,
    });
    mockDashboardState = unlockedDash;

    render(
      <Harness initialDashboard={unlockedDash}>
        <ActionGraphView />
      </Harness>
    );
    fireEvent.click(document.getElementById("ag-tab-btn-ai-planner")!);

    await waitFor(() => {
      const actBtn = document.getElementById("btn-ai-activate") as HTMLButtonElement;
      expect(actBtn.disabled).toBe(false);
    });
    fireEvent.click(document.getElementById("btn-ai-activate")!);
    await waitFor(() => {
      const upd = postedCommands.find((c) => c.command === "update_config" && c.config?.planner_mode === "ai_planner");
      expect(upd).toBeDefined();
    });
  });

  it("renders Tab 4 Fleet/Attack Planner, bids bidirectional sync with global Config, and updates GaNnScannerHud for AI-Planner", async () => {
    render(
      <Harness initialDashboard={makeDashboard({ coloniesOwned: 11, maxPlanets: 11, plannerMode: "ai_planner", liveAiUnlocked: true })}>
        <GaNnScannerHud />
        <ConfigView />
        <ActionGraphView />
      </Harness>
    );

    // GaNnScannerHud reflects AI-Planner & Colony Gate telemetry cleanly
    expect(document.getElementById("gann-calc-hud")?.hidden).toBe(false);
    expect(document.getElementById("gann-meta-line")?.textContent).toContain("COL 11/11");

    // Open Tab 4: Fleet / Attack Planner
    fireEvent.click(document.getElementById("ag-tab-btn-fleet-planner")!);
    await waitFor(() => {
      expect(document.getElementById("flt-kpi-slots")?.textContent).toContain("2 / 7");
    });
    expect(document.getElementById("flt-slots-table")?.textContent).toContain("b10 L6");

    // Edit AI Spare Resource Budget %, shipyard target, and fleet research target in Tab 4 and Apply to Config
    fireEvent.change(document.getElementById("flt-cfg-ai_resource_spare_pct")!, {
      target: { value: "25" },
    });
    fireEvent.click(document.querySelector('[data-flt-ptab="shipyard"]')!);
    fireEvent.change(document.getElementById("flt-shipyard-starcruiser")!, {
      target: { value: "45" },
    });
    expect(document.getElementById("flt-cfg-allow_fleet_research")).not.toBeNull();
    fireEvent.change(document.getElementById("flt-research-r9")!, {
      target: { value: "6" },
    });
    fireEvent.click(document.getElementById("btn-flt-apply")!);

    await waitFor(() => {
      const upd = postedCommands.find((c) => c.command === "update_config" && c.config?.ai_resource_spare_pct === 25);
      expect(upd).toBeDefined();
      expect(upd?.config?.shipyard_targets?.starcruiser).toBe(45);
      expect(upd?.config?.allow_fleet_research).toBe(true);
      expect(upd?.config?.fleet_research_targets?.r9).toBe(6);
    });

    // Verify GaNnScannerHud honors configured planner_mode even when effective_planner_mode falls back to priority (<11/11)
    cleanup();
    const fallbackPreviewDash = makeDashboard({
      coloniesOwned: 7,
      maxPlanets: 11,
      plannerMode: "ai_planner",
      liveAiUnlocked: false,
    });
    fallbackPreviewDash.effective_planner_mode = "priority";
    render(
      <Harness initialDashboard={fallbackPreviewDash}>
        <GaNnScannerHud />
      </Harness>
    );
    expect(document.getElementById("gann-calc-hud")?.hidden).toBe(false);
    expect(document.getElementById("gann-status-pill")?.textContent).toBe("PREVIEW ONLY");
  });

  it("renders Config-screen-style parameter keys, bounds, and detailed descriptions across all 4 Action Graph settings panels", async () => {
    render(
      <Harness initialDashboard={mockDashboardState}>
        <ActionGraphView />
      </Harness>
    );

    const assertPanelHasDetailedDocs = (panelId: string, minExpectedItems: number) => {
      const panel = document.getElementById(panelId);
      expect(panel).not.toBeNull();
      const items = Array.from(panel!.querySelectorAll(".ag-param-item"));
      expect(items.length).toBeGreaterThanOrEqual(minExpectedItems);

      for (const item of items) {
        const label = item.querySelector("label.ag-param-item-label");
        const keyBadge = item.querySelector("code.ag-param-item-key");
        const bounds = item.querySelector(".ag-param-item-bounds");
        const desc = item.querySelector("p.ag-param-item-desc");

        expect(label?.textContent?.trim().length).toBeGreaterThan(2);
        const forId = label?.getAttribute("for");
        expect(forId).toBeTruthy();
        expect(document.getElementById(forId!)).not.toBeNull();
        expect(keyBadge?.textContent?.trim().length).toBeGreaterThan(1);
        expect(bounds?.textContent?.trim().length).toBeGreaterThan(2);
        expect(desc?.textContent?.trim().length).toBeGreaterThan(35);
      }

      // Never reuse ConfigView's .config-item class inside Action Graph
      expect(panel!.querySelectorAll(".config-item").length).toBe(0);
    };

    // Tab 1: Heuristics (29 parameters across Mode & Thresholds + Automation & Limits)
    assertPanelHasDetailedDocs("ag-control-panel", 29);
    expect(document.getElementById("ag-control-panel")?.textContent).toContain("planner_mode");
    expect(document.getElementById("ag-control-panel")?.textContent).toContain("auto_throttle_blast_furnace");

    // Tab 2: Config Optimization (36 parameters across Objective & Fleet + Stability + Buildings & GA)
    fireEvent.click(document.getElementById("ag-tab-btn-config-optimizer")!);
    await waitFor(() => {
      expect(document.getElementById("ag-co-control-panel")).not.toBeNull();
    });
    assertPanelHasDetailedDocs("ag-co-control-panel", 36);
    expect(document.getElementById("ag-co-control-panel")?.textContent).toContain("central_planet_slot");
    expect(document.getElementById("ag-co-control-panel")?.textContent).toContain("preserve_previous_specializations");
    expect(document.getElementById("ag-co-control-panel")?.textContent).toContain("specialization_switch_threshold_pct");
    expect(document.getElementById("ag-co-control-panel")?.textContent).toContain("optimized_building_max_levels.b2");
    expect(document.getElementById("ag-co-control-panel")?.textContent).toContain("mine and shipyard specializations remain emergent");

    // Tab 3: AI-Planner (17 parameters across Horizon & Spare % + Weights & GA)
    fireEvent.click(document.getElementById("ag-tab-btn-ai-planner")!);
    await waitFor(() => {
      expect(document.getElementById("ag-ai-control-panel")).not.toBeNull();
    });
    assertPanelHasDetailedDocs("ag-ai-control-panel", 17);
    expect(document.getElementById("ag-ai-control-panel")?.textContent).toContain("weekly_discount");
    expect(document.getElementById("ag-ai-control-panel")?.textContent).toContain("transport_dispatch_delay_minutes");
    expect(document.getElementById("ag-ai-control-panel")?.textContent).toContain("resource_spare_percent.spice");
    expect(document.getElementById("ag-ai-control-panel")?.textContent).toContain("scarcity_focus");

    // Verify Fleet Dispatch Delay defaults to 120 minutes, edits cleanly, and submits with live refresh
    const delayInput = document.getElementById("ai-param-transport_dispatch_delay_minutes") as HTMLInputElement;
    expect(delayInput).not.toBeNull();
    expect(delayInput.value).toBe("120");
    fireEvent.change(delayInput, { target: { value: "45" } });
    fireEvent.click(document.getElementById("btn-ai-run")!);
    await waitFor(() => {
      const runCmd = postedCommands.find((c) => c.command === "ai_planner_run");
      expect(runCmd).toBeDefined();
      expect(runCmd?.params?.transport_dispatch_delay_minutes).toBe(45);
      expect(runCmd?.refresh).toBe(true);
    });

    // Tab 4: Fleet / Attack Planner (51+ parameters across Combat, Logistics, Scanner, Shipyard & Tech)
    fireEvent.click(document.getElementById("ag-tab-btn-fleet-planner")!);
    await waitFor(() => {
      expect(document.getElementById("ag-flt-control-panel")).not.toBeNull();
    });
    assertPanelHasDetailedDocs("ag-flt-control-panel", 51);
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("combat_safety_margin");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("ai_resource_spare_pct");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("shipyard_targets.starcruiser");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("allow_fleet_research");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("fleet_research_targets.r9");
  });
});
