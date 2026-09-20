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
  let mockCustomPlanetNames: Record<string, string> = {};
  let mockConfigOptimizerActiveNull = false;
  let mockIncludePlaceholderPlanet = false;

  beforeEach(() => {
    postedCommands = [];
    mockCustomPlanetNames = {};
    mockConfigOptimizerActiveNull = false;
    mockIncludePlaceholderPlanet = false;
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
        if (cmd === "rename_planet") {
          const pid = Number(body.planet_id || 0);
          const nextRawName = String(body.name || "").trim();
          const cleared = !nextRawName;
          if (cleared) {
            delete mockCustomPlanetNames[String(pid)];
          } else {
            mockCustomPlanetNames[String(pid)] = nextRawName;
          }
          const matchedPlanet = (mockDashboardState.state?.planets || []).find(
            (p) => Number(p.planet_id) === pid
          );
          const gameName = String((matchedPlanet as any)?.game_name || (pid === 100 ? "Homeworld" : `Colony ${pid - 99}`));
          const resolvedName = cleared ? gameName : nextRawName;
          if (mockDashboardState.state?.planets) {
            mockDashboardState = {
              ...mockDashboardState,
              custom_planet_names: { ...mockCustomPlanetNames },
              state: {
                ...mockDashboardState.state,
                planets: mockDashboardState.state.planets.map((p) =>
                  Number(p.planet_id) === pid
                    ? ({ ...p, name: resolvedName, game_name: gameName } as any)
                    : p
                ),
              },
            } as any;
          }
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                command: "rename_planet",
                planet_id: pid,
                name: resolvedName,
                game_name: gameName,
                cleared,
                custom_planet_names: { ...mockCustomPlanetNames },
                planet: {
                  planet_id: pid,
                  coords: matchedPlanet?.coords || "02:380:1",
                  name: resolvedName,
                  game_name: gameName,
                  custom_name: cleared ? null : nextRawName,
                },
                message: cleared
                  ? `Reset planet name to "${resolvedName}".`
                  : `Renamed planet to "${resolvedName}".`,
              }),
          };
        }
        if (cmd === "config_optimizer_get" || cmd === "config_optimizer_run") {
          const p100Name = mockCustomPlanetNames["100"] || "Homeworld";
          const planetsList: Array<Record<string, any>> = [
            {
              slot: 1,
              planet_id: 100,
              name: p100Name,
              game_name: "Homeworld",
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
          ];
          if (mockIncludePlaceholderPlanet) {
            planetsList.push({
              slot: 8,
              planet_id: 0,
              name: "Colony Slot #8",
              coords: "PREVIEW-8",
              is_placeholder: true,
              temperature: 30,
              fields_max: 300,
              fields_used_target: 220,
              detected_specialization: "metal",
              locked_specialization: "auto",
              built_floors: {},
              reserved_floors: {},
              buildings: {},
              warehouses: [],
              production: { monthly_ships: 9.2, monthly_spice_eq: 41000000 },
            });
          }
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                colonies_owned: mockDashboardState.colonies_owned,
                max_planets: mockDashboardState.max_planets,
                custom_planet_names: { ...mockCustomPlanetNames },
                job: { status: "completed", progress_pct: 100, elapsed_ms: 142 },
                active: mockConfigOptimizerActiveNull
                  ? null
                  : {
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
                        planets: planetsList,
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

  it("supports inline planet renaming in Config Optimizer with validation, Escape cancel, Enter save, Reset, and Dashboard sync", async () => {
    mockIncludePlaceholderPlanet = true;

    const DashboardNameProbe: React.FC = () => {
      const { dashboard } = useConsole();
      const p100 = (dashboard?.state?.planets || []).find((p) => Number(p.planet_id) === 100);
      return <span id="test-dash-p100-name">{p100?.name || ""}</span>;
    };

    render(
      <Harness initialDashboard={mockDashboardState}>
        <DashboardNameProbe />
        <ActionGraphView />
      </Harness>
    );

    fireEvent.click(document.getElementById("ag-tab-btn-config-optimizer")!);
    await waitFor(() => {
      expect(document.getElementById("btn-co-rename-100")).not.toBeNull();
    });

    // Placeholder preview planet row must NOT show a rename trigger; table must contain 0 inline SVGs
    expect(document.querySelectorAll("[data-co-rename-planet]").length).toBe(1);
    expect(document.getElementById("co-planets-table")?.querySelectorAll("svg").length).toBe(0);

    // Open inline rename editor
    fireEvent.click(document.getElementById("btn-co-rename-100")!);
    const inputEl = document.getElementById("co-rename-input-100") as HTMLInputElement;
    expect(inputEl).not.toBeNull();
    expect(inputEl.value).toBe("Homeworld");

    // Validation: > 48 chars displays role="alert" error and does not call API
    fireEvent.change(inputEl, { target: { value: "A".repeat(55) } });
    fireEvent.click(document.getElementById("btn-co-rename-save-100")!);
    expect(document.getElementById("co-rename-error-100")?.getAttribute("role")).toBe("alert");
    expect(document.getElementById("co-rename-error-100")?.textContent).toContain("48");
    expect(postedCommands.some((c) => c.command === "rename_planet")).toBe(false);

    // Escape cancels inline edit cleanly
    fireEvent.keyDown(inputEl, { key: "Escape" });
    expect(document.getElementById("co-rename-input-100")).toBeNull();
    expect(document.getElementById("btn-co-rename-100")).not.toBeNull();

    // Re-open, rename to "Fortress Prime", submit via Enter key
    fireEvent.click(document.getElementById("btn-co-rename-100")!);
    const inputEl2 = document.getElementById("co-rename-input-100") as HTMLInputElement;
    fireEvent.change(inputEl2, { target: { value: "Fortress Prime" } });
    fireEvent.keyDown(inputEl2, { key: "Enter" });

    await waitFor(() => {
      const renameCmd = postedCommands.find((c) => c.command === "rename_planet");
      expect(renameCmd).toBeDefined();
      expect(renameCmd?.planet_id).toBe(100);
      expect(renameCmd?.name).toBe("Fortress Prime");
    });

    await waitFor(() => {
      expect(document.getElementById("co-planets-table")?.textContent).toContain("Fortress Prime");
      expect(document.getElementById("co-planets-table")?.textContent).toContain("Game: Homeworld");
      expect(document.getElementById("co-planet-detail")?.textContent).toContain("Fortress Prime");
      expect(document.getElementById("test-dash-p100-name")?.textContent).toBe("Fortress Prime");
    });

    // Reset clears custom override by sending name: "" and restores in-game name
    fireEvent.click(document.getElementById("btn-co-rename-100")!);
    fireEvent.click(document.getElementById("btn-co-rename-reset-100")!);

    await waitFor(() => {
      const resetCmd = [...postedCommands]
        .reverse()
        .find((c) => c.command === "rename_planet");
      expect(resetCmd?.planet_id).toBe(100);
      expect(resetCmd?.name).toBe("");
    });

    await waitFor(() => {
      expect(document.getElementById("co-planets-table")?.textContent).toContain("Homeworld");
      expect(document.getElementById("co-planet-detail")?.textContent).toContain("Homeworld");
      expect(document.getElementById("test-dash-p100-name")?.textContent).toBe("Homeworld");
    });

    // Pre-run quick-rename strip works when no optimization run exists yet
    cleanup();
    mockConfigOptimizerActiveNull = true;
    render(
      <Harness initialDashboard={mockDashboardState}>
        <ActionGraphView />
      </Harness>
    );
    fireEvent.click(document.getElementById("ag-tab-btn-config-optimizer")!);
    await waitFor(() => {
      expect(document.getElementById("co-owned-planets-rename")).not.toBeNull();
    });
    expect(document.getElementById("btn-co-rename-100")).not.toBeNull();
  });

  it("renders FleetPlannerTab ongoing flights with EXPECTED and DELIVERED badges & values", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    const fleetDash = makeDashboard({ coloniesOwned: 4, maxPlanets: 11, plannerMode: "priority" });
    fleetDash.state!.active_queues = [
      {
        category: "fleet",
        name: "Attack -> Inactive [02:381:5]",
        planet_id: 100,
        planet_coords: "02:380:1",
        returning: false,
        finish_ts: nowSec + 420,
        remaining_text: "7m 0s",
        expected_cargo: { pig_iron: 12000, metal: 8000, kryptonite: 4000, spice: 1000 },
      } as any,
      {
        category: "fleet",
        name: "Recycle -> 02:381:9",
        planet_id: 100,
        planet_coords: "02:380:1",
        returning: false,
        finish_ts: nowSec + 600,
        remaining_text: "10m 0s",
        expected_cargo: { pig_iron: 25000, kryptonite: 15000 },
      } as any,
      {
        category: "fleet",
        name: "Transport -> Colony 2 [02:380:2]",
        planet_id: 100,
        planet_coords: "02:380:1",
        returning: false,
        finish_ts: nowSec + 300,
        remaining_text: "5m 0s",
        expected_cargo: { pig_iron: 40000, spice: 5000 },
      } as any,
      {
        category: "fleet",
        name: "Return Transport <- Colony 2 [02:380:2]",
        planet_id: 100,
        planet_coords: "02:380:1",
        returning: true,
        delivered: true,
        finish_ts: nowSec + 240,
        remaining_text: "4m 0s",
        delivered_cargo: { metal: 18000, kryptonite: 6000 },
      } as any,
      {
        category: "fleet",
        name: "Return Transport <- Colony 3 [02:380:3]",
        planet_id: 101,
        planet_coords: "02:380:2",
        returning: true,
        delivered: true,
        finish_ts: nowSec + 180,
        remaining_text: "3m 0s",
        delivered_cargo: null,
      } as any,
      {
        category: "fleet",
        name: "Return Attack <- Inactive [02:381:5]",
        planet_id: 100,
        planet_coords: "02:380:1",
        returning: true,
        finish_ts: nowSec + 120,
        remaining_text: "2m 0s",
        cargo: { pig_iron: 9500, spice: 2500 },
      } as any,
    ];
    mockDashboardState = fleetDash;

    render(
      <Harness initialDashboard={fleetDash}>
        <ActionGraphView />
      </Harness>
    );

    fireEvent.click(document.getElementById("ag-tab-btn-fleet-planner")!);
    await waitFor(() => {
      expect(document.getElementById("flt-ongoing-flights")).not.toBeNull();
    });

    const ongoingEl = document.getElementById("flt-ongoing-flights")!;
    expect(ongoingEl.textContent).toContain("6 flights");
    expect(ongoingEl.querySelectorAll("svg").length).toBe(0);

    const expectedBadges = ongoingEl.querySelectorAll('[data-cargo-kind="expected"]');
    const deliveredBadges = ongoingEl.querySelectorAll('[data-cargo-kind="delivered"]');
    const lootBadges = ongoingEl.querySelectorAll('[data-cargo-kind="loot"]');

    expect(expectedBadges.length).toBe(3);
    expect(deliveredBadges.length).toBe(2);
    expect(lootBadges.length).toBe(1);

    expectedBadges.forEach((b) => expect(b.textContent).toBe("EXPECTED"));
    deliveredBadges.forEach((b) => expect(b.textContent).toBe("DELIVERED"));
    expect(lootBadges[0].textContent).toBe("Loot");

    // Verify formatted cargo values appear cleanly alongside badges
    expect(ongoingEl.textContent).toContain("12,000 Pig");
    expect(ongoingEl.textContent).toContain("25,000 Pig");
    expect(ongoingEl.textContent).toContain("40,000 Pig");
    expect(ongoingEl.textContent).toContain("18,000 Met");
    expect(ongoingEl.textContent).toContain("6,000 Kryp");
    expect(ongoingEl.textContent).toContain("9,500 Pig");
  });

  it("renders GaNnScannerHud Active Builders, Hourly Production canvas line chart, Stolen & Recycled canvas bar chart, and preserves strict SVG & legacy ga_nn invariants", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    const hudDash = makeDashboard({
      coloniesOwned: 3,
      maxPlanets: 11,
      plannerMode: "ai_planner",
      liveAiUnlocked: false,
    }) as any;

    // Add an asteroid that must NOT be counted in normal Active Builders total
    hudDash.state.planets.push({
      planet_id: 199,
      name: "Asteroid",
      coords: "02:380:1",
      planet_type: "asteroid",
      resources: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
      storage: { pig_iron: 10000, metal: 10000, kryptonite: 10000, spice: 10000 },
      energy: { produced: 0, used: 0, net: 0 },
      buildings: {},
      ships: {},
    });

    // 2 active building queues + 1 expired building queue + 1 fleet queue -> exactly 2 / 3 builders
    hudDash.state.active_queues = [
      { category: "building", name: "Pig iron mine L13", planet_id: 100, planet_coords: "02:380:1", finish_ts: nowSec + 600 },
      { category: "building", name: "Kryptonite mine L11", planet_id: 101, planet_coords: "02:380:2", finish_ts: nowSec + 420 },
      { category: "building", name: "Expired Build", planet_id: 102, planet_coords: "02:380:3", finish_ts: nowSec - 30 },
      { category: "fleet", name: "Raid -> Inactive", planet_id: 102, planet_coords: "02:380:3", finish_ts: nowSec + 300 },
    ];

    hudDash.hourly_production_history = [
      {
        minute_bucket: "2026-11-18T21:20",
        recorded_at: nowSec - 120,
        pig_iron_per_hour: 50000,
        metal_per_hour: 30000,
        kryptonite_per_hour: 20000,
        spice_per_hour: 10000,
        total_per_hour: 110000,
        speq_per_hour: 45000,
        planet_count: 3,
        active_builders: 2,
      },
      {
        minute_bucket: "2026-11-18T21:21",
        recorded_at: nowSec - 60,
        pig_iron_per_hour: 55000,
        metal_per_hour: 32500,
        kryptonite_per_hour: 22500,
        spice_per_hour: 12500,
        total_per_hour: 122500,
        speq_per_hour: 50000,
        planet_count: 3,
        active_builders: 2,
      },
      {
        minute_bucket: "2026-11-18T21:22",
        recorded_at: nowSec,
        pig_iron_per_hour: 60000,
        metal_per_hour: 35000,
        kryptonite_per_hour: 25000,
        spice_per_hour: 15000,
        total_per_hour: 135000,
        speq_per_hour: 55000,
        planet_count: 3,
        active_builders: 2,
      },
    ];
    hudDash.current_hourly_production = hudDash.hourly_production_history[2];

    hudDash.loot_recycle_history = [
      {
        hour_bucket: "2026-11-18T20",
        stolen_pig_iron: 20000,
        stolen_metal: 12000,
        stolen_kryptonite: 7000,
        stolen_spice: 3000,
        stolen_total: 42000,
        recycled_pig_iron: 12000,
        recycled_metal: 0,
        recycled_kryptonite: 6000,
        recycled_spice: 0,
        recycled_total: 18000,
        total: 60000,
        attack_count: 2,
        recycle_count: 1,
        updated_at: nowSec - 3600,
      },
      {
        hour_bucket: "2026-11-18T21",
        stolen_pig_iron: 18000,
        stolen_metal: 10000,
        stolen_kryptonite: 7000,
        stolen_spice: 3000,
        stolen_total: 38000,
        recycled_pig_iron: 14000,
        recycled_metal: 0,
        recycled_kryptonite: 8000,
        recycled_spice: 0,
        recycled_total: 22000,
        total: 60000,
        attack_count: 2,
        recycle_count: 1,
        updated_at: nowSec,
      },
    ];
    hudDash.loot_recycle_summary = {
      total_stolen: 80000,
      total_recycled: 40000,
      grand_total: 120000,
      attack_count: 4,
      recycle_count: 2,
      window_hours: 24,
    };

    render(
      <Harness initialDashboard={hudDash}>
        <GaNnScannerHud />
      </Harness>
    );

    const hudEl = document.getElementById("gann-calc-hud")!;
    expect(hudEl.hidden).toBe(false);

    // Metric 1: Active Builders (2 / 3)
    expect(hudEl.textContent).toContain("Active Builders");
    expect(document.getElementById("gann-gen-val")?.textContent).toBe("2 / 3");
    expect(document.getElementById("gann-gen-sub")?.textContent).toContain("2/3 building · 1 idle");

    // Metric 2: Hourly Production line chart (<canvas id="gann-prod-chart">)
    expect(hudEl.textContent).toContain("Hourly Production");
    expect(document.getElementById("gann-nn-val")?.textContent).toBe("135.0k /h");
    const prodCanvas = document.getElementById("gann-prod-chart");
    expect(prodCanvas?.tagName).toBe("CANVAS");
    expect(document.getElementById("gann-nn-sub")?.textContent).toContain("PI 60.0k");
    expect(document.getElementById("gann-nn-sub")?.textContent).toContain("3m");

    // Metric 3: Stolen & Recycled stacked bar chart (<canvas id="gann-loot-chart">)
    expect(hudEl.textContent).toContain("Stolen & Recycled");
    expect(document.getElementById("gann-fit-val")?.textContent).toBe("120.0k");
    const lootCanvas = document.getElementById("gann-loot-chart");
    expect(lootCanvas?.tagName).toBe("CANVAS");
    expect(document.getElementById("gann-fit-sub")?.textContent).toContain("Stolen 80.0k · Recycled 40.0k (24h)");

    // Strict 2-SVG invariant: GaNnScannerHud renders ONLY #gann-radial-svg (0 new SVGs)
    const svgs = Array.from(hudEl.querySelectorAll("svg"));
    expect(svgs.length).toBe(1);
    expect(svgs[0].id).toBe("gann-radial-svg");

    // Background job running overrides Metric 1 to "Background Job"
    cleanup();
    const runningDash = {
      ...hudDash,
      planner_jobs_status: {
        ...hudDash.planner_jobs_status,
        ai_planner_job: { status: "running", progress_pct: 64, elapsed_ms: 910, phase: "ga_search" },
      },
    };
    render(
      <Harness initialDashboard={runningDash}>
        <GaNnScannerHud />
      </Harness>
    );
    expect(document.getElementById("gann-calc-hud")?.textContent).toContain("Background Job");
    expect(document.getElementById("gann-gen-val")?.textContent).toBe("64%");

    // Legacy ga_nn summary compatibility preserved (no regressions for test_ui.py)
    cleanup();
    const legacyGaDash = makeDashboard({
      coloniesOwned: 3,
      maxPlanets: 11,
      plannerMode: "ga_nn",
    }) as any;
    legacyGaDash.config.ga_generations = 4;
    legacyGaDash.optimizer_summary = {
      generations: 4,
      population_size: 8,
      nn_evaluations: 32,
      nn_train_loss: 0.0,
      nn_backend: "python",
      improvement_pct: 1200,
      elapsed_ms: 88,
      best_chromosome: ["BUILD:b1", "BUILD:b3"],
    };
    render(
      <Harness initialDashboard={legacyGaDash}>
        <GaNnScannerHud />
      </Harness>
    );
    expect(document.getElementById("gann-gen-val")?.textContent).toBe("4 / 4");
    expect(document.getElementById("gann-nn-val")?.textContent).toBe("32 evals");
    expect(document.getElementById("gann-fit-val")?.textContent).toBe(">+999.9%");
    expect(document.getElementById("gann-genome-val")?.textContent).toBe("2 genes");
  });
});
