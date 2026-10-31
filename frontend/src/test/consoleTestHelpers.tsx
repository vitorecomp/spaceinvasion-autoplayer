import React, { useEffect } from "react";
import { vi } from "vitest";
import {
  ConsoleProvider,
  resetConsoleStore,
  useConsole,
} from "../context/ConsoleContext";
import { setConsoleStorePersistenceForTests } from "../store/consoleStore";
import type { DashboardPayload } from "../types/api";

export function createBaseDashboard(
  overrides: Partial<DashboardPayload> = {}
): DashboardPayload {
  const base: DashboardPayload = {
    ok: true,
    planner_mode: "priority",
    effective_planner_mode: "priority",
    health: {
      ok: true,
      in_game: true,
      debug_port_open: true,
      debug_port: 9222,
      rate_limited: false,
      retry_after: 0,
    },
    bot_control: {
      paused: false,
      captcha_blocked: false,
    },
    config: {
      planner_mode: "priority",
      playstyle: "fleeter",
      merchant_mode: false,
      auto_throttle_blast_furnace: true,
      allow_research: true,
      allow_shipyard: true,
      allow_multi_planet: true,
      allow_raids: true,
      allow_fleetsave: false,
      allow_galaxy_scan: true,
      allow_colonize: true,
      allow_transport: true,
      forbid_planetary_solar: true,
      max_planets: 11,
      min_colony_fields: 300,
      combat_safety_margin: 1.4,
      raid_min_loot_spice_eq: 1500,
      fleet_save_fuel_reserve_spice: 500,
      max_actions_per_hour: 60,
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
      speedup_factor: 5,
      planets: [
        {
          planet_id: 12476,
          name: "Homeworld",
          coords: "02:380:12",
          planet_type: "normal",
          temperature: 31,
          fields_used: 45,
          fields_max: 230,
          resources: { pig_iron: 25000, metal: 12000, kryptonite: 8000, spice: 4000 },
          storage: { pig_iron: 100000, metal: 100000, kryptonite: 100000, spice: 100000 },
          energy: { produced: 600, used: 380, net: 220 },
          buildings: { b1: 8, b2: 6, b3: 6, b4: 5, b5: 10, b6: 2, b7: 4, b8: 3, b9: 4, b10: 5 },
          ships: { s1: 6, s4: 15, s10: 10, s11: 4 },
          economy_rates: { resource1: 100, resource2: 100, resource3: 100, resource4: 100 },
        },
      ],
      researches: { r1: 4, r2: 6, r3: 3 },
      active_queues: [],
      fleet_alerts: [],
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
        reason: "Upgrade Pig iron mine",
        executable_now: true,
        wait_hours: 0,
        wait_seconds: 0,
        wait_eta: "Ready",
        duration_seconds: 120,
        duration_text: "2m 00s",
        total_hours: 0.033,
        total_seconds: 120,
        total_eta: "2m",
        eta_bottleneck: null,
      },
    ],
    manual_queue: [],
    buildings_eta: {
      planet: {
        planet_id: 12476,
        coords: "02:380:12",
        dev_center_level: 4,
        microsystem_level: 0,
        building_time_divisor: 5,
      },
      planet_economy: {
        blast_furnace_pct: 100,
        pig_iron_per_hr: 2400,
        metal_per_hr: 1200,
        kryptonite_per_hr: 900,
        spice_per_hr: 450,
      },
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
          bottleneck: null,
          can_build_live: true,
          offer_visible: true,
          unmet_prerequisites: [],
        },
      ],
      researches: [],
    },
    notifications: {
      unread_count: 0,
      items: [],
    },
  };

  return {
    ...base,
    ...overrides,
    health: overrides.health ? { ...base.health, ...overrides.health } : base.health,
    config: overrides.config ? { ...base.config, ...overrides.config } : base.config,
    state: overrides.state ? { ...base.state, ...overrides.state } : base.state,
  };
}

export function resetConsoleTestEnv(): void {
  if (typeof window !== "undefined") {
    window.history.replaceState(null, "", "/");
  }
  setConsoleStorePersistenceForTests(false);
  resetConsoleStore({ clearStorage: true });
  sessionStorage.clear();
  localStorage.clear();
  if (typeof document !== "undefined") {
    document.documentElement.classList.remove("si-hydrating");
  }
}

export function jsonResponse(body: unknown, status = 200): Response {
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => raw,
    json: async () => JSON.parse(raw),
  } as unknown as Response;
}

export interface MockFetchResult {
  fetchMock: ReturnType<typeof vi.fn>;
  postedCommands: Array<Record<string, any>>;
  requestedUrls: string[];
}

export function installMockFetch(options: {
  getDashboard: () => DashboardPayload;
  onCommand?: (
    body: Record<string, any>
  ) => Response | Promise<Response> | Record<string, any> | null | undefined;
  onUiVersion?: () => Response | Record<string, any>;
  onDashboard?: (url: string) => Response | Promise<Response> | null | undefined;
}): MockFetchResult {
  const postedCommands: Array<Record<string, any>> = [];
  const requestedUrls: string[] = [];

  const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
    const u = String(url);
    requestedUrls.push(u);

    if (u.startsWith("/ui-version")) {
      const custom = options.onUiVersion?.();
      if (custom && typeof (custom as any).text === "function") return custom as Response;
      return jsonResponse(
        custom ?? {
          ok: true,
          code_version: "v1",
          css_version: "css1",
          server_boot_id: "boot-1",
        }
      );
    }

    if (u.startsWith("/dashboard")) {
      const custom = await options.onDashboard?.(u);
      if (custom) return custom;
      return jsonResponse(options.getDashboard());
    }

    if (u === "/command" || u.startsWith("/command?")) {
      const body = opts?.body ? JSON.parse(String(opts.body)) : {};
      postedCommands.push(body);
      const custom = await options.onCommand?.(body);
      if (custom) {
        if (typeof (custom as any).text === "function") return custom as Response;
        return jsonResponse(custom);
      }
      return jsonResponse({
        ok: true,
        command: body.command,
        dry_run: Boolean(body.dry_run),
        executed: !body.dry_run,
        message: body.dry_run
          ? `[DRY-RUN] Would execute ${body.command}`
          : `Executed ${body.command}`,
      });
    }

    return jsonResponse({ ok: true });
  });

  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, postedCommands, requestedUrls };
}

export const SeededConsole: React.FC<{
  page: string;
  dashboard: DashboardPayload;
  actionGraph?: Record<string, any> | null;
  espData?: Record<string, any> | null;
  colonyData?: Record<string, any> | null;
  atkData?: Record<string, any> | null;
  recData?: Record<string, any> | null;
  disableInitialFetch?: boolean;
  children: React.ReactNode;
}> = ({
  page,
  dashboard,
  actionGraph,
  espData,
  colonyData,
  atkData,
  recData,
  disableInitialFetch = true,
  children,
}) => {
  const Seeder: React.FC = () => {
    const {
      setDashboard,
      setBuildingsEtaData,
      setActionGraphData,
      setEspData,
      setColonyData,
      setAtkData,
      setRecData,
      setActivePage,
      syncNotificationsPayload,
    } = useConsole();

    useEffect(() => {
      setDashboard(dashboard);
      if (dashboard.buildings_eta) {
        setBuildingsEtaData(dashboard.buildings_eta);
      }
      if (dashboard.notifications) {
        syncNotificationsPayload(dashboard.notifications);
      }
      if (actionGraph) {
        setActionGraphData(actionGraph);
      }
      if (espData) setEspData(espData);
      if (colonyData) setColonyData(colonyData);
      if (atkData) setAtkData(atkData);
      if (recData) setRecData(recData);
      setActivePage(page);
    }, [
      actionGraph,
      atkData,
      colonyData,
      dashboard,
      espData,
      page,
      recData,
      setActionGraphData,
      setActivePage,
      setAtkData,
      setBuildingsEtaData,
      setColonyData,
      setDashboard,
      setEspData,
      syncNotificationsPayload,
      setRecData,
    ]);

    return null;
  };

  return (
    <ConsoleProvider disableInitialFetch={disableInitialFetch}>
      <Seeder />
      {children}
    </ConsoleProvider>
  );
};
