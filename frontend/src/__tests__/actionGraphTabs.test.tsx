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
  let mockAdaptiveStatus: Record<string, any> | null = null;

  beforeEach(() => {
    postedCommands = [];
    mockCustomPlanetNames = {};
    mockConfigOptimizerActiveNull = false;
    mockIncludePlaceholderPlanet = false;
    mockAdaptiveStatus = null;
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
        if (cmd === "sync_planet_names") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                command: "sync_planet_names",
                synced_count: 1,
                synced: [
                  {
                    planet_id: body.planet_id || 100,
                    coords: "02:380:1",
                    old_game_name: "Homeworld",
                    requested_name: body.name || "Citadel Prime",
                    game_name: body.name || "Citadel Prime",
                    verified: true,
                  },
                ],
                skipped: [],
                custom_planet_names: {
                  [String(body.planet_id || 100)]: body.name || "Citadel Prime",
                },
                message: "Synced 1 planet name in-game via internalSettings.",
              }),
          };
        }
        if (cmd === "config_optimizer_update_planet") {
          const tb = body.target_buildings || {};
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                command: "config_optimizer_update_planet",
                run_id: 99,
                planet_specializations:
                  body.locked_specialization && body.locked_specialization !== "auto"
                    ? { [String(body.planet_id || 100)]: body.locked_specialization }
                    : {},
                custom_planet_names: body.planet_name
                  ? { [String(body.planet_id || 100)]: body.planet_name }
                  : {},
                active: {
                  run_id: 99,
                  result: {
                    elapsed_ms: 12,
                    summary: {
                      ships_per_month: 96.4,
                      whole_ships_per_month: 96,
                      target_ship_key: "s16",
                      limiting_factor: "spice",
                      fields_used: Object.values(tb).reduce(
                        (acc: number, v: any) => acc + Number(v || 0),
                        0
                      ),
                      fields_max: body.fields_max_override || 300,
                      warehouse_fields: 36,
                    },
                    planets: [
                      {
                        slot: body.slot || 1,
                        planet_id: body.planet_id || 100,
                        coords: body.coords || "02:380:1",
                        name: body.planet_name || "Homeworld",
                        game_name: "Homeworld",
                        temperature_c: 20,
                        fields_used_target: Object.values(tb).reduce(
                          (acc: number, v: any) => acc + Number(v || 0),
                          0
                        ),
                        fields_max: body.fields_max_override || 300,
                        detected_specialization: body.locked_specialization || "spice_shipyard",
                        locked_specialization: body.locked_specialization || "auto",
                        built_floors: { b1: 12, b3: 10, b4: 8, b5: 14, b7: 12, b9: 13 },
                        buildings: Object.entries(tb).map(([k, v]) => ({
                          key: k,
                          built_level: 0,
                          target_level: Number(v || 0),
                        })),
                      },
                    ],
                  },
                },
              }),
          };
        }
        if (cmd === "config_optimizer_yearly_projection") {
          const hy = Number(body.horizon_years || 5);
          const yrs = Array.from({ length: hy }, (_, i) => ({
            year: i + 1,
            label: `Year ${i + 1}`,
            start_ships_per_week: i * 8,
            avg_ships_per_week: (i + 0.5) * 8,
            end_ships_per_week: (i + 1) * 8,
            peak_ships_per_week: (i + 1) * 8,
            delta_ships_per_week: 8,
            total_ships_produced: Math.round((i + 0.5) * 8 * 52),
            cumulative_ships_produced: Math.round(((i + 1) * (i + 1) * 8 * 52) / 2),
            bottleneck: i < 2 ? "shipyards" : "kryptonite",
            completion_pct: Math.min(100, 30 + i * 15),
          }));
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                command: "config_optimizer_yearly_projection",
                projection: {
                  target_ship_key: "s16",
                  target_ship: "Imperial Starbase",
                  horizon_years: hy,
                  total_weeks: hy * 52,
                  current_ships_per_week: 0,
                  target_ships_per_week: 42.5,
                  target_ships_per_month: 182.1,
                  total_upgrade_levels: 320,
                  years: yrs,
                  weeks: [
                    {
                      week: 0,
                      year: 1,
                      week_in_year: 0,
                      ships_per_week: 0,
                      resource_cap_per_week: 5,
                      shipyard_cap_per_week: 0,
                      bottleneck: "shipyards",
                      completion_pct: 0,
                    },
                    {
                      week: hy * 52,
                      year: hy,
                      week_in_year: 52,
                      ships_per_week: hy * 8,
                      resource_cap_per_week: hy * 9,
                      shipyard_cap_per_week: hy * 8,
                      bottleneck: "kryptonite",
                      completion_pct: 95,
                    },
                  ],
                },
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
                adaptive_status: mockAdaptiveStatus
                  ? {
                      ...mockAdaptiveStatus,
                      enabled: Boolean(
                        mockDashboardState.config?.ai_adaptive_execution ?? mockAdaptiveStatus.enabled
                      ),
                    }
                  : null,
                job: { status: "completed", progress_pct: 100, elapsed_ms: 318 },
                active_summary: mockDashboardState.ai_planner_summary,
                attack_telemetry: {
                  hourly_loot_spice_eq: 45000,
                  median_loot_spice_eq: 16500,
                  median_round_trip_seconds: 1320,
                  current_fleet_slots: 12,
                },
                smelting: {
                  initial_live_mode: "off",
                  initial_live_any_enabled: false,
                  initial_live_all_enabled: false,
                  initial_live_enabled_count: 0,
                  initial_live_disabled_count: 1,
                  total_b2_planets: 1,
                  initial_target_enabled: true,
                  initial_target_pct: 100,
                  initial_toggle_needed: true,
                  initial_planets_needing_toggle: [
                    {
                      planet_id: 100,
                      coords: "02:380:1",
                      name: "Homeworld",
                      b2_level: 10,
                      from_pct: 0,
                      to_pct: 100,
                    },
                  ],
                  metal_smelted: 125000,
                  smelting_hours_total: 4.5,
                  smelting_time_text: "4h 30m",
                  smelting_toggles_count: 2,
                  smelting_spans_count: 1,
                  smelting_spans: [],
                  smelting_events: [
                    {
                      seq: 1,
                      time_hours: 0,
                      time_sec: 0,
                      time_text: "now",
                      week: 1,
                      action: "enable",
                      enabled: true,
                      target_pct: 100,
                      from_mode: "off",
                      from_enabled: false,
                      is_initial: true,
                      step_label: "Development center L12",
                      step_planet_coords: "02:380:1",
                      reason: "Initial live b2 OFF (0/1 ON) -> ENABLE 100%: Metal needed",
                    },
                    {
                      seq: 2,
                      time_hours: 4.5,
                      time_sec: 16200,
                      time_text: "4h 30m",
                      week: 1,
                      action: "disable",
                      enabled: false,
                      target_pct: 0,
                      from_mode: "on",
                      from_enabled: true,
                      is_initial: false,
                      step_label: "Kryptonite mine L11",
                      step_planet_coords: "02:380:1",
                      reason: "DISABLE 0%: Metal satisfied",
                    },
                  ],
                  planets: [
                    {
                      planet_id: 100,
                      coords: "02:380:1",
                      name: "Homeworld",
                      b2_level: 10,
                      initial_pct: 0,
                      initial_enabled: false,
                    },
                  ],
                },
                live_blast_furnace: {
                  mode: "off",
                  any_enabled: false,
                  all_enabled: false,
                  enabled_count: 0,
                  disabled_count: 1,
                  total_b2_planets: 1,
                  planets: [
                    {
                      planet_id: 100,
                      coords: "02:380:1",
                      name: "Homeworld",
                      b2_level: 10,
                      initial_pct: 0,
                      initial_enabled: false,
                    },
                  ],
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

    // Switch to Research sub-tab and verify Export buttons are removed
    fireEvent.click(document.getElementById("co-subtab-research")!);
    expect(document.getElementById("co-research-table")?.textContent).toContain("Mining technology");
    expect(document.getElementById("btn-co-export")).toBeNull();
    expect(document.getElementById("btn-co-export-footer")).toBeNull();
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

    // Tab 4: Fleet / Attack Planner (53+ parameters across Combat, Logistics, Scanner, Shipyard & Tech)
    fireEvent.click(document.getElementById("ag-tab-btn-fleet-planner")!);
    await waitFor(() => {
      expect(document.getElementById("ag-flt-control-panel")).not.toBeNull();
    });
    assertPanelHasDetailedDocs("ag-flt-control-panel", 53);
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("combat_safety_margin");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("attack_min_res_per_hour");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("recycle_min_res_per_hour");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("ai_resource_spare_pct");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("shipyard_targets.starcruiser");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("allow_fleet_research");
    expect(document.getElementById("ag-flt-control-panel")?.textContent).toContain("fleet_research_targets.r9");

    // Verify Min Attack Yield and Min Recycle Yield sit at the VERY TOP of the Shipyard & Tech group and sync independently
    const shipyardTabBtn = document.querySelector(
      '#ag-flt-control-panel [data-flt-ptab="shipyard"]'
    ) as HTMLButtonElement | null;
    expect(shipyardTabBtn).not.toBeNull();
    fireEvent.click(shipyardTabBtn!);
    const rphGroup = document.getElementById("flt-harvest-rph-group");
    expect(rphGroup).not.toBeNull();
    const shipyardPane = rphGroup!.parentElement!;
    expect(shipyardPane.hidden).toBe(false);
    expect(shipyardPane.firstElementChild).toBe(rphGroup);

    const atkRphInput = document.getElementById("flt-cfg-attack_min_res_per_hour") as HTMLInputElement;
    const recRphInput = document.getElementById("flt-cfg-recycle_min_res_per_hour") as HTMLInputElement;
    expect(atkRphInput).not.toBeNull();
    expect(recRphInput).not.toBeNull();
    expect(atkRphInput.value).toBe("0");
    expect(recRphInput.value).toBe("0");

    fireEvent.change(atkRphInput, { target: { value: "25000" } });
    fireEvent.change(recRphInput, { target: { value: "12000" } });
    fireEvent.click(document.getElementById("btn-flt-apply")!);
    await waitFor(() => {
      const lastUpd = [...postedCommands].reverse().find((c) => c.command === "update_config");
      expect(lastUpd?.config?.attack_min_res_per_hour).toBe(25000);
      expect(lastUpd?.config?.recycle_min_res_per_hour).toBe(12000);
    });
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

  it("renders AI-Planner initial Blast furnace (b2) state strip and Smelting schedule subview with zero extra SVGs", async () => {
    render(
      <Harness initialDashboard={mockDashboardState}>
        <ActionGraphView />
      </Harness>
    );

    fireEvent.click(document.getElementById("ag-tab-btn-ai-planner")!);
    await waitFor(() => {
      expect(document.getElementById("ai-smelt-live-badge")?.textContent).toContain("OFF (0/1 ON)");
    });

    expect(document.getElementById("ai-smelt-target-badge")?.textContent).toContain("ON 100%");
    expect(document.getElementById("ai-smelt-target-badge")?.textContent).toContain("TOGGLE NEEDED");
    expect(document.getElementById("ag-ai-smelting-strip")?.textContent).toContain("125.0k Metal");
    expect(document.getElementById("ag-ai-smelting-strip")?.textContent).toContain("2 toggles");
    expect(document.getElementById("ai-subtab-smelting")?.textContent).toContain("Smelting (2)");

    // Open Smelting subview and verify per-colony initial snapshot + toggle schedule table
    fireEvent.click(document.getElementById("ai-subtab-smelting")!);
    const smeltPane = document.getElementById("ai-view-smelting")!;
    expect(smeltPane.hidden).toBe(false);
    expect(document.getElementById("ag-ai-smelting-planets")?.textContent).toContain(
      "Homeworld [02:380:1] · b2 L10 · 0% → 100%"
    );
    const tbody = document.getElementById("ag-ai-smelting-tbody")!;
    expect(tbody.querySelectorAll("tr").length).toBe(2);
    expect(tbody.textContent).toContain("ENABLE 100%");
    expect(tbody.textContent).toContain("t=0 LIVE");
    expect(tbody.textContent).toContain("DISABLE 0%");
    expect(tbody.textContent).toContain("Development center L12");

    // Zero SVGs inside the smelting strip or subview
    expect(document.getElementById("ag-ai-smelting-strip")?.querySelectorAll("svg").length).toBe(0);
    expect(smeltPane.querySelectorAll("svg").length).toBe(0);
  });

  it("renders Tab 3 #ag-ai-adaptive-strip, toggles ai_adaptive_execution, displays per-colony idle diagnostics, fleet cargo, and 24h gate shares with zero SVGs", async () => {
    mockDashboardState = {
      ...mockDashboardState,
      custom_planet_names: { "101": "Smelter Prime" },
      config: {
        ...(mockDashboardState.config || {}),
        ai_adaptive_execution: false,
      },
    };
    mockAdaptiveStatus = {
      enabled: false,
      active_builders: 3,
      total_colonies: 10,
      idle_with_affordable_count: 1,
      ready_not_started_count: 1,
      counts: {
        building: 1,
        ready_not_started: 1,
        waiting_metal: 1,
        waiting_pig_iron: 1,
        waiting_kryptonite: 1,
        waiting_spice: 1,
        transport_inbound: 1,
        storage_blocked: 1,
        field_conflict: 1,
        idle_complete: 1,
      },
      window_24h: {
        bucket_count: 24,
        colony_samples: 240,
        idle_with_affordable_share_pct: 12.5,
        ready_not_started_share_pct: 3.2,
        state_shares_pct: { building: 35.0, waiting_metal: 25.0 },
      },
      transport_fleet: {
        owned_parked_cargo: 54000,
        in_flight_cargo: 30000,
        total_fleet_cargo: 84000,
        target_fleet_cargo: 613000,
        planned_cargo_per_hour_24h: 858000,
        realized_utilization: 0.5,
        delivered_cargo_per_hour_24h: 214500,
        delivered_vs_planned_pct: 25.0,
      },
      auto_replan: {
        cooldown_seconds: 1800,
        last_auto_replan_ts: null,
        last_trigger_reason: "plan_age_4h",
        drift_ready: true,
      },
      colonies: [
        {
          planet_id: 100,
          coords: "02:380:1",
          name: "Homeworld",
          state: "building",
          reason: "Constructing Development center L13",
          next_label: "Pig iron mine L14",
          idle_with_affordable_target_step: false,
          b2_level: 10,
          b2_pct: 100,
          energy_net: 400,
          b2_energy_margin_at_100: 400,
        },
        {
          planet_id: 101,
          coords: "02:380:2",
          name: "Colony 2",
          state: "waiting_metal",
          reason: "Short 4,200 Metal for Kryptonite mine L15",
          next_label: "Kryptonite mine L15",
          idle_with_affordable_target_step: false,
          b2_level: 8,
          b2_pct: 0,
          energy_net: 180,
          b2_energy_margin_at_100: 79,
        },
        {
          planet_id: 102,
          coords: "02:380:3",
          name: "Colony 3",
          state: "ready_not_started",
          reason: "Affordable target step ready to start",
          next_label: "Fusion plant L12",
          idle_with_affordable_target_step: true,
          b2_level: 6,
          b2_pct: 100,
          energy_net: 220,
          b2_energy_margin_at_100: 220,
        },
        {
          planet_id: 103,
          coords: "02:380:4",
          name: "Colony 4",
          state: "waiting_pig_iron",
          reason: "Short Pig iron",
          idle_with_affordable_target_step: false,
          b2_level: 5,
          b2_pct: 0,
          energy_net: 150,
          b2_energy_margin_at_100: 150,
        },
        {
          planet_id: 104,
          coords: "02:380:5",
          name: "Colony 5",
          state: "waiting_kryptonite",
          reason: "Short Kryptonite",
          idle_with_affordable_target_step: false,
          b2_level: 5,
          b2_pct: 0,
          energy_net: 150,
          b2_energy_margin_at_100: 150,
        },
        {
          planet_id: 105,
          coords: "02:380:6",
          name: "Colony 6",
          state: "waiting_spice",
          reason: "Short Spice",
          idle_with_affordable_target_step: false,
          b2_level: 5,
          b2_pct: 0,
          energy_net: 150,
          b2_energy_margin_at_100: 150,
        },
        {
          planet_id: 106,
          coords: "02:380:7",
          name: "Colony 7",
          state: "transport_inbound",
          reason: "Inbound cargo covers deficit",
          idle_with_affordable_target_step: false,
          b2_level: 4,
          b2_pct: 100,
          energy_net: 120,
          b2_energy_margin_at_100: 120,
        },
        {
          planet_id: 107,
          coords: "02:380:8",
          name: "Colony 8",
          state: "storage_blocked",
          reason: "Warehouse cap reached",
          idle_with_affordable_target_step: false,
          b2_level: 4,
          b2_pct: 0,
          energy_net: 120,
          b2_energy_margin_at_100: 120,
        },
        {
          planet_id: 108,
          coords: "02:380:9",
          name: "Colony 9",
          state: "field_conflict",
          reason: "Planet fields full",
          idle_with_affordable_target_step: false,
          b2_level: 4,
          b2_pct: 0,
          energy_net: 120,
          b2_energy_margin_at_100: 120,
        },
        {
          planet_id: 109,
          coords: "02:380:10",
          name: "Colony 10",
          state: "idle_complete",
          reason: "All planned horizon steps built",
          idle_with_affordable_target_step: false,
          b2_level: 0,
          b2_pct: 0,
          energy_net: 140,
          b2_energy_margin_at_100: 140,
        },
      ],
    };

    const { container } = render(
      <Harness initialDashboard={mockDashboardState}>
        <ConfigView />
        <ActionGraphView />
      </Harness>
    );

    fireEvent.click(document.getElementById("ag-tab-btn-ai-planner")!);
    await waitFor(() => {
      expect(document.getElementById("ag-ai-colony-diag-list")?.textContent).toContain("Smelter Prime [02:380:2]");
    });

    const strip = document.getElementById("ag-ai-adaptive-strip")!;
    expect(strip).not.toBeNull();
    expect(strip.querySelectorAll("svg").length).toBe(0);

    // Toggle starts OFF
    const toggle = document.getElementById("ai-adaptive-exec-toggle") as HTMLInputElement;
    expect(toggle).not.toBeNull();
    expect(toggle.type).toBe("checkbox");
    expect(toggle.checked).toBe(false);
    expect(document.getElementById("ai-adaptive-exec-state")?.textContent).toBe("OFF");

    // Summary badges
    expect(document.getElementById("ai-adaptive-builders-badge")?.textContent).toBe("Builders 3/10");
    const fleetBadge = document.getElementById("ai-adaptive-fleet-badge");
    expect(fleetBadge?.textContent).toBe("Cargo 84.0k / 613.0k");
    expect(fleetBadge?.getAttribute("title")).toContain("Delivered 24h: 214.5k/h (25% of plan)");
    expect(document.getElementById("ai-adaptive-gate24h-badge")?.textContent).toBe(
      "Idle w/ affordable: 12.5% · Ready gap: 3.2%"
    );
    expect(document.getElementById("ai-adaptive-replan-badge")?.textContent).toContain("Drift ready");
    expect(document.getElementById("ai-adaptive-replan-badge")?.textContent).toContain("plan_age_4h");

    // Per-colony diagnostic chips: custom name, all 10 state pills, b2 %, low energy margin, and reason tooltip
    const diagList = document.getElementById("ag-ai-colony-diag-list")!;
    const chips = Array.from(diagList.querySelectorAll(".ag-adaptive-chip"));
    expect(chips.length).toBe(10);
    for (const expectedPill of [
      "Building",
      "Ready",
      "Wait Metal",
      "Wait Pig",
      "Wait Kry",
      "Wait Spice",
      "Cargo Inbound",
      "Storage Cap",
      "Field Conflict",
      "Idle",
    ]) {
      expect(diagList.textContent).toContain(expectedPill);
    }

    const smelterChip = diagList.querySelector('[data-planet-id="101"]') as HTMLElement;
    expect(smelterChip.textContent).toContain("Smelter Prime [02:380:2]");
    expect(smelterChip.textContent).toContain("Wait Metal");
    expect(smelterChip.textContent).toContain("b2 0%");
    expect(smelterChip.textContent).toContain("Low E (79)");
    expect(smelterChip.getAttribute("title")).toContain("Short 4,200 Metal for Kryptonite mine L15");

    const homeChip = diagList.querySelector('[data-planet-id="100"]') as HTMLElement;
    expect(homeChip.textContent).toContain("b2 100%");
    expect(homeChip.textContent).not.toContain("Low E");

    const idleChip = diagList.querySelector('[data-planet-id="109"]') as HTMLElement;
    expect(idleChip.textContent).toContain("no b2");

    // Toggle adaptive execution ON -> dispatches update_config, refreshes dashboard + ai_planner_get, and syncs ConfigView
    fireEvent.click(toggle);
    await waitFor(() => {
      const upd = postedCommands.find(
        (c) => c.command === "update_config" && c.config?.ai_adaptive_execution === true
      );
      expect(upd).toBeDefined();
      expect(toggle.checked).toBe(true);
      expect(document.getElementById("ai-adaptive-exec-state")?.textContent).toBe("ON");
    });

    const cfgCheckbox = document.getElementById("cfg-ai_adaptive_execution") as HTMLInputElement;
    expect(cfgCheckbox).not.toBeNull();
    expect(cfgCheckbox.checked).toBe(true);

    // Confirm ai_planner_get was re-fetched after update_config
    const updIdx = postedCommands.findIndex(
      (c) => c.command === "update_config" && c.config?.ai_adaptive_execution === true
    );
    expect(
      postedCommands.slice(updIdx + 1).some((c) => c.command === "ai_planner_get")
    ).toBe(true);

    // Zero duplicate HTML IDs across entire rendered tree
    const allIds = Array.from(container.querySelectorAll("[id]")).map((el) => el.id);
    expect(new Set(allIds).size).toBe(allIds.length);

    // Switch to Config Optimization tab and verify #co-param-min_warehouse_level renders and submits in config_optimizer_run
    fireEvent.click(document.getElementById("ag-tab-btn-config-optimizer")!);
    await waitFor(() => {
      expect(document.getElementById("co-param-min_warehouse_level")).not.toBeNull();
    });
    const minWhInput = document.getElementById("co-param-min_warehouse_level") as HTMLInputElement;
    expect(minWhInput.value).toBe("0");
    const minWhRow = minWhInput.closest(".ag-param-item")!;
    expect(minWhRow.querySelector(".ag-param-item-key")?.textContent).toBe("min_warehouse_level");
    expect((minWhRow.querySelector(".ag-param-item-desc")?.textContent || "").length).toBeGreaterThan(80);

    const allowDemCheckbox = document.getElementById("co-param-allow_demolition") as HTMLInputElement;
    expect(allowDemCheckbox).not.toBeNull();
    expect(allowDemCheckbox.checked).toBe(false);
    const allowDemRow = allowDemCheckbox.closest(".ag-param-item")!;
    expect(allowDemRow.querySelector(".ag-param-item-key")?.textContent).toBe("allow_demolition");
    expect((allowDemRow.querySelector(".ag-param-item-desc")?.textContent || "").length).toBeGreaterThan(80);
    fireEvent.click(allowDemCheckbox);

    fireEvent.change(minWhInput, { target: { value: "10" } });
    fireEvent.click(document.getElementById("btn-co-run")!);
    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) =>
            c.command === "config_optimizer_run" &&
            c.params?.min_warehouse_level === 10 &&
            c.params?.allow_demolition === true
        )
      ).toBe(true);
    });
  });

  it("opens Planet Configuration modal on planet click, syncs planet names to game, and renders year-by-year Ships/Week canvas graph with zero extra SVGs", async () => {
    const { container } = render(
      <Harness initialDashboard={mockDashboardState}>
        <ActionGraphView />
      </Harness>
    );

    fireEvent.click(document.getElementById("ag-tab-btn-config-optimizer")!);
    await waitFor(() => {
      expect(document.querySelector('[data-co-planet-slot="1"]')).not.toBeNull();
    });

    // 1. Verify Ships/Week Graph button is present and Export JSON is removed
    expect(document.getElementById("btn-co-export")).toBeNull();
    const graphBtn = document.getElementById("btn-co-yearly-graph")!;
    expect(graphBtn).not.toBeNull();

    // 2. Open Year-by-Year Ships/Week Graph modal, switch horizon 5Y -> 10Y, verify <canvas> & 0 SVGs
    fireEvent.click(graphBtn);
    await waitFor(() => {
      expect(document.getElementById("co-yearly-graph-modal")).not.toBeNull();
      expect(document.getElementById("co-yearly-breakdown-table")?.textContent).toContain("Year 5");
    });
    expect(document.getElementById("co-yearly-graph-canvas")?.tagName).toBe("CANVAS");
    expect(document.getElementById("co-yearly-graph-modal")!.querySelectorAll("svg").length).toBe(0);

    fireEvent.click(document.getElementById("btn-co-yr-10")!);
    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) => c.command === "config_optimizer_yearly_projection" && c.horizon_years === 10
        )
      ).toBe(true);
      expect(document.getElementById("co-yearly-breakdown-table")?.textContent).toContain("Year 10");
    });
    fireEvent.click(document.getElementById("btn-co-yearly-close")!);
    expect(document.getElementById("co-yearly-graph-modal")).toBeNull();

    // 3. Click planet row -> opens #co-planet-edit-modal, edit target buildings + name + specialization, sync name & save
    const row1 = document.querySelector('[data-co-planet-slot="1"]') as HTMLElement;
    fireEvent.click(row1);
    await waitFor(() => {
      expect(document.getElementById("co-planet-edit-modal")).not.toBeNull();
    });
    expect(document.getElementById("co-planet-edit-modal")!.querySelectorAll("svg").length).toBe(0);

    const modalName = document.getElementById("co-modal-name") as HTMLInputElement;
    fireEvent.change(modalName, { target: { value: "Citadel Prime" } });
    fireEvent.click(document.getElementById("btn-co-modal-sync-name")!);
    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) =>
            c.command === "sync_planet_names" &&
            c.planet_id === 100 &&
            c.name === "Citadel Prime"
        )
      ).toBe(true);
    });

    fireEvent.change(document.getElementById("co-modal-spec")!, {
      target: { value: "spice_shipyard" },
    });
    fireEvent.change(document.getElementById("co-modal-bld-b4")!, {
      target: { value: "28" },
    });
    fireEvent.click(document.getElementById("btn-co-bld-inc-b9")!);
    fireEvent.click(document.getElementById("btn-co-modal-save")!);

    await waitFor(() => {
      const saveCmd = postedCommands.find((c) => c.command === "config_optimizer_update_planet");
      expect(saveCmd).toBeDefined();
      expect(saveCmd?.planet_name).toBe("Citadel Prime");
      expect(saveCmd?.locked_specialization).toBe("spice_shipyard");
      expect(saveCmd?.target_buildings?.b4).toBe(28);
    });

    // 4. Toolbar Sync Names to Game button triggers batch sync_planet_names
    fireEvent.click(document.getElementById("btn-co-sync-planet-names")!);
    await waitFor(() => {
      expect(
        postedCommands.filter((c) => c.command === "sync_planet_names").length
      ).toBeGreaterThanOrEqual(2);
    });

    // Zero SVGs inside Config Optimizer pane & modals
    expect(
      document.getElementById("ag-pane-config-optimizer")!.querySelectorAll("svg").length
    ).toBe(0);
    expect(container.querySelectorAll("canvas#co-yearly-graph-canvas").length).toBe(0);
  });
});
