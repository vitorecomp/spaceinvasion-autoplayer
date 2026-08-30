import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../context/ConsoleContext";
import { DashboardPayload } from "../types/api";
import { CombatView } from "../features/combat";
import { SectorsView } from "../features/sectors";
import { EspionageView } from "../features/espionage";
import { ColoniesView } from "../features/colonies";
import { AttacksView } from "../features/attacks";
import { RecyclersView } from "../features/recyclers";
import { RankingView } from "../features/ranking";

const SAMPLE_DASHBOARD: DashboardPayload = {
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
        name: "Colony",
        coords: "02:380:12",
        planet_type: "normal",
        fields_used: 50,
        fields_max: 230,
        ships: { s1: 6, s4: 15, s8: 1, s10: 8, s11: 3 },
      },
    ],
    raid_targets: [
      {
        coords: "02:380:04",
        planet_name: "IdlePrime",
        owner: "SleepyOne",
        status_color: "yellow",
        is_spacia: false,
        resources: { pig_iron: 10000, metal: 5000, kryptonite: 3000, spice: 2000 },
        defense: { d1: 2 },
        fleet: { s4: 1 },
      },
    ],
  },
  combat: {
    planet_id: 12476,
    attack_zone: { label: "AZ 2", range_text: "500 - 2,000" },
    newbie_protected: false,
    safety_margin: 1.4,
    combat_techs: { weapons: 4, shield: 3, defense: 5, spy: 6, targeting: 2 },
    galaxy_scanner_level: 1,
    stationed_fleet_power: 1200,
    stationed_defense_power: 350,
    total_cargo_capacity: 30000,
    weapons_factory_level: 4,
    ships: [
      {
        unit_id: 4,
        key: "s4",
        name: "Small fighter",
        owned: 15,
        attack: 50,
        shield: 10,
        cargo: 50,
      },
    ],
    ship_offers: [{ unit_id: 4, can_build: true }],
    defenses: [
      {
        unit_id: 1,
        key: "d1",
        name: "Missile turret",
        owned: 5,
        attack: 80,
        shield: 20,
        hull: 2000,
      },
    ],
  },
  ranking: {
    current: {
      server_rank: 142,
      server_points: 18500,
      estimated_points: 18520.4,
      buildings_points: 12000,
      research_points: 4000,
      spice_value: 95000,
      newbie_protected: false,
      attack_zone: { label: "AZ 5", range_text: "10,000 - 25,000" },
    },
    rates: {
      pts_per_hr_steady: 42.5,
      net_resources_per_hr: 85000,
      speedup_factor: 10,
    },
    horizons: [
      {
        label: "+24h",
        steady_points: 19540,
        steady_delta: 1020,
        steady_az: "AZ 5",
        compound_points: 19800,
        compound_delta: 1280,
        compound_az: "AZ 5",
      },
    ],
    milestones: [
      {
        label: "AZ 6",
        target_points: 25000,
        missing_points: 6480,
        steady_eta: "6.3d",
        compound_eta: "5.1d",
      },
    ],
    observed_trend: {
      sample_count: 3,
      delta_points: 320,
      observed_pts_per_hr: 48.2,
      elapsed_hours: 6.6,
      rank_climbed: 4,
      samples: [
        { estimated_points: 18200 },
        { estimated_points: 18350 },
        { estimated_points: 18520 },
      ],
    },
  },
  universe_research_max: [
    {
      research_id: 1,
      key: "r1",
      slug: "mining-technology",
      name: "Mining technology",
      own_level: 8,
      universe_max_level: 14,
      spied_max_level: 14,
      server_record_level: 14,
      catalog_max_level: 40,
      gap_to_max: 6,
      holder: "TopMiner",
      holder_coords: "01:100:08",
      updated_ts: 1700000000,
    },
    {
      research_id: 16,
      key: "r16",
      slug: "urplasma-extraction",
      name: "Urplasma-extraction",
      own_level: 2,
      universe_max_level: 2,
      catalog_max_level: null,
      gap_to_max: 0,
      holder: "",
      holder_coords: "",
      updated_ts: 1700000000,
    },
  ],
};

interface SeedHarnessProps {
  page: string;
  sectorsSeed?: Record<string, any>;
  espSeed?: Record<string, any>;
  colSeed?: Record<string, any>;
  atkSeed?: Record<string, any>;
  recSeed?: Record<string, any>;
  children: React.ReactNode;
}

const SeedState: React.FC<Omit<SeedHarnessProps, "children">> = ({
  page,
  sectorsSeed,
  espSeed,
  colSeed,
  atkSeed,
  recSeed,
}) => {
  const {
    setDashboard,
    setCombatData,
    setSectorsData,
    setEspData,
    setColonyData,
    setAtkData,
    setRecData,
  } = useConsole();

  useEffect(() => {
    setDashboard(SAMPLE_DASHBOARD);
    if (SAMPLE_DASHBOARD.combat) setCombatData(SAMPLE_DASHBOARD.combat);
    if (sectorsSeed) setSectorsData(sectorsSeed);
    if (espSeed) setEspData(espSeed);
    if (colSeed) setColonyData(colSeed);
    if (atkSeed) setAtkData(atkSeed);
    if (recSeed) setRecData(recSeed);
  }, [
    atkSeed,
    colSeed,
    espSeed,
    page,
    recSeed,
    sectorsSeed,
    setAtkData,
    setColonyData,
    setCombatData,
    setDashboard,
    setEspData,
    setRecData,
    setSectorsData,
  ]);

  return null;
};

const Harness: React.FC<SeedHarnessProps> = ({ children, ...seedProps }) => (
  <ConsoleProvider disableInitialFetch>
    <SeedState {...seedProps} />
    {children}
  </ConsoleProvider>
);

describe("Combat, Intel & Ranking React Feature Modules", () => {
  let commandCalls: Array<Record<string, any>> = [];

  beforeEach(() => {
    commandCalls = [];
    sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, opts?: RequestInit) => {
        if (String(url).startsWith("/dashboard")) {
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify(SAMPLE_DASHBOARD),
          };
        }
        const body = opts?.body ? JSON.parse(String(opts.body)) : {};
        commandCalls.push(body);
        if (body.command === "simulate_combat") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                simulation: {
                  safe_to_attack: true,
                  win_ratio: 2.85,
                  safety_margin: 1.4,
                  attacker_power: 950,
                  defender_power: 333,
                  cargo_capacity: 20000,
                  total_plunder: 10000,
                  loot_spice_equivalent: 4500,
                  notes: "Safe 1:1 raid margin.",
                },
              }),
          };
        }
        if (body.command === "fleetsave_plan") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                escape_plan: { mission: "SAVE", target: body.target_coords || "02:380:13" },
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
                qualified_count: 0,
                probed_count: 0,
                unprobed_count: 1,
                candidates: [
                  {
                    coords: "02:380:10",
                    position: 10,
                    orbital_band: "Prime Mid-Orbit",
                    grade: "?",
                    score: 55,
                    probed: false,
                    meets_target: false,
                    uninhabited_fields: null,
                    system_distance: 0,
                    reason: "Prime unprobed slot",
                  },
                ],
              }),
          };
        }
        if (body.command === "universe_research_max") {
          return {
            ok: true,
            status: 200,
            text: async () =>
              JSON.stringify({
                ok: true,
                command: "universe_research_max",
                synced: true,
                rows: [
                  {
                    research_id: 1,
                    key: "r1",
                    slug: "mining-technology",
                    name: "Mining technology",
                    own_level: 8,
                    universe_max_level: 16,
                    catalog_max_level: 40,
                    gap_to_max: 8,
                    holder: "Empress",
                    holder_coords: "01:050:07",
                    updated_ts: 1700000100,
                  },
                ],
              }),
          };
        }
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

  it("renders CombatView shipyard orders, 1:1 simulator, and escape flight planner", async () => {
    const { container } = render(
      <Harness page="combat">
        <CombatView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#cmb-az-val")?.textContent).toContain("AZ 2");
    });
    expect(container.querySelector("#cmb-tech-val")?.textContent).toBe("L4 / L3 / L5");
    expect(container.querySelector("#cmb-ships-tbody")?.textContent).toContain("Small fighter");

    // Build ship order
    const qtyInput = container.querySelector("#cmb-ships-tbody input.qty-inline") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "5" } });
    const buildBtn = Array.from(container.querySelectorAll("#cmb-ships-tbody button")).find(
      (b) => b.textContent === "Build"
    )!;
    fireEvent.click(buildBtn);
    await waitFor(() => {
      expect(commandCalls.some((c) => c.command === "build_ship" && c.quantity === 5)).toBe(true);
    });

    // Run 1:1 Combat Simulator
    fireEvent.click(container.querySelector("#btn-run-sim")!);
    await waitFor(() => {
      expect(container.querySelector("#sim-verdict-badge")?.textContent).toBe("SAFE TO RAID");
    });
    expect(container.querySelector("#sim-ratio-text")?.textContent).toContain("2.85x");

    // Escape flight planner
    fireEvent.change(container.querySelector("#fs-target")!, { target: { value: "02:381:05" } });
    fireEvent.click(container.querySelector("#btn-calc-fleetsave")!);
    await waitFor(() => {
      expect(container.querySelector("#fleetsave-output")?.textContent).toContain("02:381:05");
    });
  });

  it("renders EspionageView targets & historical reports with Distance and dispatches Spy/Attack", async () => {
    const espSeed = {
      origin_coords: "02:380:12",
      summary: { spied_targets: 1, total_targets: 2, total_reports: 1, spacia_count: 1 },
      targets: [
        {
          coords: "02:383:02",
          planet_name: "Spacia",
          owner: "Spacia",
          is_spacia: true,
          has_spy_intel: true,
          distance: 2985,
          system_distance: 3,
          galaxy_delta: 0,
          system_delta: 3,
          position_delta: 10,
          resources: { pig_iron: 40000, metal: 20000, kryptonite: 10000, spice: 5000 },
          spice_eq: 30000,
          fleet: {},
          defense: {},
        },
        {
          coords: "03:392:05",
          planet_name: "FarColony",
          owner: "DistantOne",
          is_inactive: true,
          has_spy_intel: false,
          resources: {},
          spice_eq: 0,
          fleet: {},
          defense: {},
        },
      ],
      reports: [
        {
          id: 1,
          ts: 1700000070,
          coords: "02:383:02",
          planet_name: "Spacia",
          owner: "Spacia",
          resources: { pig_iron: 40000 },
          spice_eq: 10000,
          buildings: { b1: 12 },
          researches: { r1: 4 },
          fleet: {},
          defense: {},
        },
      ],
    };

    const { container } = render(
      <Harness page="espionage" espSeed={espSeed}>
        <EspionageView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#esp-targets-tbody")?.textContent).toContain("02:383:02");
    });
    expect(container.querySelector("#esp-meta-count")?.textContent).toContain("FROM 02:380:12");
    expect(container.querySelector("#esp-targets-tbody")?.textContent).toContain("3 sys");
    expect(container.querySelector("#esp-targets-tbody")?.textContent).toContain("1G · 12 sys");
    expect(container.querySelector("#esp-reports-tbody")?.textContent).toContain("3 sys");
    expect(container.querySelector("#esp-reports-tbody")?.textContent).toContain("B:b1:12");

    const firstRowButtons = Array.from(
      container.querySelectorAll("#esp-targets-tbody tr:first-child button")
    ).map((b) => b.textContent);
    expect(firstRowButtons).toEqual(["Spy", "Sim", "Attack"]);

    fireEvent.click(container.querySelector("#esp-targets-tbody button")!);
    await waitFor(() => {
      expect(commandCalls.some((c) => c.command === "spy" && c.target_coords === "02:383:02")).toBe(
        true
      );
    });
  });

  it("renders ColoniesView with Probe, Colonize, AND Discard buttons and updates candidates", async () => {
    const colSeed = {
      qualified_count: 1,
      probed_count: 1,
      unprobed_count: 1,
      colony_ships: 1,
      spy_probes: 8,
      origin_coords: "02:380:12",
      planet_count: 1,
      grade_counts: { S: 1 },
      candidates: [
        {
          coords: "02:380:09",
          position: 9,
          orbital_band: "Prime Mid-Orbit",
          grade: "S",
          score: 96,
          probed: true,
          meets_target: true,
          uninhabited_fields: 330,
          system_distance: 0,
          reason: "330 buildable fields",
        },
        {
          coords: "02:380:10",
          position: 10,
          orbital_band: "Prime Mid-Orbit",
          grade: "?",
          score: 55,
          probed: false,
          meets_target: false,
          uninhabited_fields: null,
          system_distance: 0,
          reason: "Prime unprobed slot",
        },
      ],
    };

    const { container } = render(
      <Harness page="colonies" colSeed={colSeed}>
        <ColoniesView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#col-candidates-tbody")?.textContent).toContain("330 fields");
    });
    expect(container.querySelector("#col-candidates-tbody")?.textContent).toContain("Unprobed");

    const firstRowButtons = Array.from(
      container.querySelectorAll("#col-candidates-tbody tr:first-child button")
    );
    expect(firstRowButtons.map((b) => b.textContent)).toEqual([
      "Re-Probe",
      "Colonize",
      "Discard",
    ]);
    const discardBtn = firstRowButtons[2];
    expect(discardBtn.className).toContain("btn-warning");

    fireEvent.click(discardBtn);
    await waitFor(() => {
      expect(
        commandCalls.some(
          (c) => c.command === "discard_colony_candidate" && c.target_coords === "02:380:09"
        )
      ).toBe(true);
    });
    await waitFor(() => {
      expect(container.querySelector("#col-candidates-tbody")?.textContent).not.toContain(
        "02:380:09"
      );
    });
  });

  it("renders ColoniesView Tried Planets attempts table, outcome pills, KPI chip, and candidate trial badges", async () => {
    const colSeed = {
      qualified_count: 1,
      probed_count: 1,
      unprobed_count: 1,
      colony_ships: 1,
      spy_probes: 8,
      origin_coords: "02:380:12",
      planet_count: 2,
      min_colony_fields: 300,
      grade_counts: { S: 1 },
      candidates: [
        {
          coords: "02:380:09",
          position: 9,
          orbital_band: "Prime Mid-Orbit",
          grade: "S",
          score: 96,
          probed: true,
          meets_target: true,
          uninhabited_fields: 330,
          system_distance: 0,
          reason: "330 buildable fields",
        },
        {
          coords: "02:381:08",
          position: 8,
          orbital_band: "Prime Mid-Orbit",
          grade: "A",
          score: 80,
          probed: true,
          meets_target: false,
          colonize_min_fields: 140,
          colonize_max_fields: 340,
          system_distance: 1,
          reason: "Range roll",
        },
      ],
      colony_attempts: [
        {
          id: 3,
          ts: 1700000300,
          event_type: "deleted_undersized",
          target_coords: "02:381:08",
          origin_coords: "02:380:12",
          planet_id: 99001,
          fields_rolled: 215,
          min_fields_target: 300,
          expected_min_fields: 140,
          expected_max_fields: 340,
          temperature: 22,
          status_message: "Rolled 215 < 300 fields; colony abandoned",
        },
        {
          id: 2,
          ts: 1700000200,
          event_type: "kept",
          target_coords: "02:379:07",
          origin_coords: "02:380:12",
          planet_id: 99000,
          fields_rolled: 318,
          min_fields_target: 300,
          temperature: 35,
          grade: "S",
          status_message: "",
        },
        {
          id: 1,
          ts: 1700000100,
          event_type: "dispatched",
          target_coords: "02:381:08",
          origin_coords: "02:380:12",
          min_fields_target: 300,
          expected_min_fields: 140,
          expected_max_fields: 340,
          status_message: "Colony ship (s8) launched",
        },
      ],
      colony_attempts_summary: {
        total_events: 3,
        unique_coords_tried: 2,
        dispatched_count: 1,
        deleted_undersized_count: 1,
        kept_count: 1,
        failed_count: 0,
        by_coords: {
          "02:381:08": {
            attempts_count: 2,
            deleted_count: 1,
            last_event_type: "deleted_undersized",
            last_fields_rolled: 215,
            last_ts: 1700000300,
          },
        },
      },
    };

    const { container } = render(
      <Harness page="colonies" colSeed={colSeed}>
        <ColoniesView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#colonies-attempts-tbody tr").length).toBe(3);
    });
    expect(container.querySelector("#colonies-attempts-table")).not.toBeNull();

    const rows = Array.from(container.querySelectorAll("#colonies-attempts-tbody tr"));
    const pills = rows.map((r) => r.querySelector(".outcome-badge")?.textContent);
    expect(pills).toEqual(["DELETED <300", "KEPT ≥300", "DISPATCHED"]);
    expect(rows[0].querySelector(".outcome-badge")?.className).toContain("is-loss");
    expect(rows[1].querySelector(".outcome-badge")?.className).toContain("is-win");
    expect(rows[0].textContent).toContain("02:381:08");
    expect(rows[0].textContent).toContain("215 f");
    expect(rows[0].textContent).toContain("140–340");
    expect(rows[0].textContent).toContain("22 °C");
    expect(rows[0].textContent).toContain("Rolled 215 < 300 fields");
    expect(rows[1].textContent).toContain("318 f");
    expect(rows[1].textContent).toContain("Grade S");
    expect(rows[2].textContent).toContain("Colony ship (s8) launched");
    expect(container.querySelector("#colonies-attempts-tbody")?.textContent).not.toMatch(
      /undefined|null|NaN/
    );

    expect(container.querySelector("#col-kpi-tried")?.textContent).toBe("2 Tried · 1 Del");
    expect(container.querySelector("#col-kpi-tried-sub")?.textContent).toContain("1 kept");

    const trialChip = container.querySelector('[data-trial-coords="02:381:08"]');
    expect(trialChip).not.toBeNull();
    expect(trialChip?.textContent).toBe("Tried 2x · Last: 215f deleted");
    expect(trialChip?.className).toContain("is-deleted");
    expect(container.querySelector('[data-trial-coords="02:380:09"]')).toBeNull();
    // Candidate action buttons are unchanged by the trial chip
    const firstRowButtons = Array.from(
      container.querySelectorAll("#col-candidates-tbody tr:first-child button")
    ).map((b) => b.textContent);
    expect(firstRowButtons).toEqual(["Re-Probe", "Colonize", "Discard"]);
  });

  it("renders ColoniesView Tried Planets empty state when no attempts are logged", async () => {
    const { container } = render(
      <Harness page="colonies" colSeed={{ candidates: [], qualified_count: 0 }}>
        <ColoniesView hidden={false} />
      </Harness>
    );
    await waitFor(() => {
      expect(container.querySelector("#colonies-attempts-tbody")?.textContent).toContain(
        "No colonization attempts recorded yet"
      );
    });
    expect(container.querySelector("#col-kpi-tried")?.textContent).toBe("0 Tried · 0 Del");
  });

  it("renders AttacksView and RecyclersView KPIs, battles, blocks, harvests, debris, and emails", async () => {
    const atkSeed = {
      summary: {
        total_battles: 1,
        wins: 1,
        losses: 0,
        draws: 0,
        total_loot_spice_eq: 5842,
        total_losses_spice_eq: 0,
        active_blocks_count: 1,
      },
      raid_stats: { total_24h: 2 },
      battles: [
        {
          id: 1,
          coords: "02:383:02",
          defender_name: "Spacia",
          outcome: "win",
          rounds: 1,
          loot: { pig_iron: 3000, spice_eq: 5842 },
        },
      ],
      blocked_targets: [
        {
          coords: "02:378:15",
          owner: "Rival",
          bash_hits: 3,
          active_block: true,
          remaining_seconds: 3600,
          block_reason: "3/24h bash protection",
        },
      ],
    };

    const recSeed = {
      summary: {
        total_runs: 1,
        recyclers_owned: 4,
        total_spice_eq: 30521,
        total_pig_iron: 17036,
        total_metal: 12036,
        total_kryptonite: 14310,
        total_spice: 18098,
        active_debris_count: 1,
        other_email_count: 1,
        category_counts: { general: 0, fleet_return: 1 },
      },
      recycles: [
        {
          id: 1,
          coords: "02:380:05",
          recycler_count: 3,
          cargo_capacity: 60000,
          harvested_pig_iron: 17036,
          harvested_metal: 12036,
          harvested_kryptonite: 14310,
          harvested_spice: 18098,
          spice_eq: 30521,
        },
      ],
      debris_targets: [
        {
          coords: "02:380:05",
          planet_name: "DebrisTarget",
          owner: "Pirate",
          last_scanned_ts: 1700000050,
        },
      ],
      other_emails: [
        {
          msg_uid: "uid:1",
          folder_type: "fleet",
          category: "fleet_return",
          sender: "System",
          subject: "Fleet return",
          coords: "02:380:12",
          body_text: "A fleet returning from duty reaches Colony.",
        },
      ],
    };

    const { container } = render(
      <Harness page="attacks" atkSeed={atkSeed} recSeed={recSeed}>
        <AttacksView hidden={false} />
        <RecyclersView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#atk-battles-tbody")?.textContent).toContain("WIN");
    });
    expect(container.querySelector("#atk-blocks-tbody")?.textContent).toContain("02:378:15");
    expect(container.querySelector("#rec-kpi-raw")?.textContent).toContain("14,310 Kry");
    expect(container.querySelector("#rec-emails-tbody")?.textContent).toContain("fleet_return");

    const recycleBtn = container.querySelector("#rec-debris-tbody button")!;
    fireEvent.click(recycleBtn);
    await waitFor(() => {
      expect(
        commandCalls.some((c) => c.command === "recycle" && c.target_coords === "02:380:05")
      ).toBe(true);
    });
  });

  it("renders RankingView SVG trajectory and Universe Max Research Levels table", async () => {
    const { container } = render(
      <Harness page="ranking">
        <RankingView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#rnk-server-pos")?.textContent).toContain("#142");
    });
    expect(container.querySelector("#rnk-chart polyline")).not.toBeNull();
    expect(container.querySelectorAll("#rnk-chart circle").length).toBe(3);

    const uniTbody = container.querySelector("#universe-research-max-tbody")!;
    expect(uniTbody.textContent).toContain("Mining technology");
    expect(uniTbody.textContent).toContain("TopMiner [01:100:08]");
    expect(uniTbody.textContent).toContain("Uncapped");
    expect(container.querySelector("#view-ranking")?.textContent).not.toContain("∞");
    expect(container.querySelector("#view-ranking")?.textContent).not.toContain("Infinity");
    expect(container.querySelector("#view-ranking")?.textContent).not.toContain("NaN");

    fireEvent.click(container.querySelector("#btn-sync-universe-research")!);
    await waitFor(() => {
      expect(
        commandCalls.some((c) => c.command === "universe_research_max" && c.sync === true)
      ).toBe(true);
    });
    await waitFor(() => {
      expect(uniTbody.textContent).toContain("Empress [01:050:07]");
    });
  });

  it("renders SectorsView with all 14 galaxies, upcoming scan queue, sortable columns, and Refresh vs Discovery split controls", async () => {
    const sectorsSeed = {
      ok: true,
      origin_coords: "02:380:12",
      galaxies_available: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
      galaxy_stats: Array.from({ length: 14 }, (_, idx) => {
        const g = idx + 1;
        return {
          galaxy: g,
          is_owned: g === 2,
          in_sweep_scope: true,
          scanned_count: g === 2 || g === 3 ? 1 : 0,
          unscanned_count: g === 2 || g === 3 ? 399 : 400,
          total_systems: 400,
          coverage_pct: g === 2 || g === 3 ? 0.25 : 0,
          occupied_count: g === 2 ? 11 : g === 3 ? 4 : 0,
          debris_count: g === 2 ? 2 : 0,
          target_count: g === 2 ? 3 : 0,
          prime_free_count: g === 2 ? 1 : g === 3 ? 3 : 0,
          queued_count: g === 1 || g === 2 ? 1 : 0,
        };
      }),
      scan_queue: [
        {
          queue_pos: 1,
          sector: "01:380",
          galaxy: 1,
          system: 380,
          phase: "exploratory",
          scanned: false,
          last_scanned_ts: 0,
          age_seconds: null,
          scan_count: 0,
          occupied_count: 0,
          debris_count: 0,
          target_count: 0,
          free_count: 0,
          prime_colony_free_count: 0,
          direct_raid_count: 0,
          owner_affinity_raid_count: 0,
          system_distance: 400,
          galaxy_delta: 1,
          system_delta: 0,
          score: 365.0,
          reason: "unscanned sector (dist 400)",
        },
        {
          queue_pos: 2,
          sector: "02:382",
          galaxy: 2,
          system: 382,
          phase: "targeted_refresh",
          scanned: true,
          last_scanned_ts: 1700001000,
          age_seconds: 900,
          scan_count: 4,
          occupied_count: 11,
          debris_count: 2,
          target_count: 3,
          free_count: 5,
          prime_colony_free_count: 1,
          direct_raid_count: 5,
          owner_affinity_raid_count: 2,
          system_distance: 2,
          galaxy_delta: 0,
          system_delta: 2,
          score: 48.2,
          reason: "near d=2, debris x2",
        },
      ],
      summary: {
        scanned_sectors: 2,
        total_scanned_all_galaxies: 2,
        explored_systems: 2,
        total_systems: 5600,
        coverage_pct: 0.04,
        universe_explored_systems: 2,
        universe_total_systems: 5600,
        universe_coverage_pct: 0.04,
        total_occupied: 15,
        avg_occupied: 7.5,
        total_free: 17,
        total_prime_free: 4,
        total_debris_fields: 2,
        sectors_with_debris: 1,
        total_targets: 3,
        total_spacia: 1,
        total_inactives: 2,
        sweep_pages: 100,
        refresh_pct: 20,
        discovery_pct: 80,
        effective_refresh_pct: 20,
        effective_discovery_pct: 80,
        scan_all_galaxies: false,
        queue_count: 2,
        queue_discovery_count: 1,
        queue_refresh_count: 1,
        queue_galaxies: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
      },
      sectors: [
        {
          sector: "02:382",
          galaxy: 2,
          system: 382,
          scanned: true,
          queue_pos: 2,
          queue_phase: "targeted_refresh",
          occupied_count: 11,
          max_slots: 16,
          active_occupied_count: 8,
          free_count: 5,
          prime_colony_free_count: 1,
          debris_count: 2,
          target_count: 3,
          inactive_count: 2,
          spacia_count: 1,
          scan_count: 4,
          last_scanned_ts: 1700001000,
          age_seconds: 900,
          system_distance: 2,
          circular_distance: 2,
          galaxy_delta: 0,
          system_delta: 2,
          direct_raid_count: 5,
          owner_affinity_raid_count: 2,
          frequent_owners: ["FarmKing (5)"],
          score: 48.2,
          reason: "near d=2, debris x2",
        },
        {
          sector: "03:010",
          galaxy: 3,
          system: 10,
          scanned: true,
          queue_pos: null,
          queue_phase: null,
          occupied_count: 4,
          max_slots: 16,
          active_occupied_count: 4,
          free_count: 12,
          prime_colony_free_count: 3,
          debris_count: 0,
          target_count: 0,
          inactive_count: 0,
          spacia_count: 0,
          scan_count: 1,
          last_scanned_ts: 1700000200,
          age_seconds: 7200,
          system_distance: 770,
          circular_distance: 770,
          galaxy_delta: 1,
          system_delta: 370,
          direct_raid_count: 0,
          owner_affinity_raid_count: 0,
          frequent_owners: [],
          score: 6.4,
          reason: "cross-gal d=770",
        },
      ],
    };

    const { container } = render(
      <Harness page="sectors" sectorsSeed={sectorsSeed}>
        <SectorsView hidden={false} />
      </Harness>
    );

    await waitFor(() => {
      expect(container.querySelector("#sec-kpi-count")?.textContent).toContain("2 Sectors");
    });
    expect(container.querySelector("#sec-kpi-coverage")?.textContent).toContain("UNI 0.0%");
    expect(container.querySelector("#sec-kpi-occupied")?.textContent).toContain("15 Occupied");
    expect(container.querySelector("#sec-kpi-debris")?.textContent).toContain("2 Debris");
    // All 14 galaxy chips (G1..G14) + All G chip render
    expect(container.querySelectorAll('[data-sec-gal]').length).toBe(15);
    expect(container.querySelector('[data-sec-gal="14"]')?.textContent).toContain("G14 (0)");
    // Upcoming Scan Queue table renders both discovery & refresh entries
    expect(container.querySelector("#sec-queue-meta")?.textContent).toContain("2 QUEUED");
    expect(container.querySelectorAll("#sectors-queue-tbody tr").length).toBe(2);
    expect(container.querySelector("#sectors-queue-tbody")?.textContent).toContain("01:380");
    expect(container.querySelector("#sectors-queue-tbody")?.textContent).toContain("DISCOVERY");
    expect(container.querySelector("#sectors-queue-tbody")?.textContent).toContain("REFRESH");

    // Filter queue to Discovery only, then reset to All
    fireEvent.click(container.querySelector('[data-sec-qfilter="exploratory"]')!);
    expect(container.querySelectorAll("#sectors-queue-tbody tr").length).toBe(1);
    expect(container.querySelector("#sectors-queue-tbody")?.textContent).toContain("01:380");
    fireEvent.click(container.querySelector('[data-sec-qfilter="all"]')!);
    expect(container.querySelectorAll("#sectors-queue-tbody tr").length).toBe(2);

    expect(container.querySelector("#sectors-tbody")?.textContent).toContain("02:382");
    expect(container.querySelector("#sectors-tbody")?.textContent).toContain("#2");
    expect(container.querySelector("#sectors-tbody")?.textContent).toContain("11 / 16");
    expect(container.querySelector("#sectors-tbody")?.textContent).toContain("2 sys");
    expect(container.querySelector("#sectors-tbody")?.textContent).toContain("1G · 370 sys");

    // Filter to debris-only sectors and verify sorting by Occupied
    fireEvent.click(container.querySelector('[data-sec-filter="debris"]')!);
    expect(container.querySelectorAll("#sectors-tbody tr").length).toBe(1);
    expect(container.querySelector("#sectors-tbody")?.textContent).toContain("02:382");

    // Reset filter to All Sectors and verify click-to-sort on Occupied (desc -> asc)
    fireEvent.click(container.querySelector('[data-sec-filter="all"]')!);
    expect(container.querySelectorAll("#sectors-tbody tr").length).toBe(2);
    const occHeaderBtn = Array.from(container.querySelectorAll("#sectors-table thead button")).find(
      (b) => b.textContent?.includes("Occupied")
    )!;
    fireEvent.click(occHeaderBtn); // desc: 11/16 first
    expect(container.querySelectorAll("#sectors-tbody tr")[0].textContent).toContain("02:382");
    fireEvent.click(occHeaderBtn); // asc: 4/16 first
    expect(container.querySelectorAll("#sectors-tbody tr")[0].textContent).toContain("03:010");

    // Click Scan Now on queue item #1 (01:380) and single-sector refresh on first row (03:010)
    fireEvent.click(container.querySelector("#sectors-queue-tbody tr button")!);
    fireEvent.click(container.querySelector("#sectors-tbody tr button")!);
    await waitFor(() => {
      expect(
        commandCalls.some(
          (c) => c.command === "scan_galaxy" && c.galaxy === 1 && c.system === 380
        )
      ).toBe(true);
      expect(
        commandCalls.some(
          (c) => c.command === "scan_galaxy" && c.galaxy === 3 && c.system === 10
        )
      ).toBe(true);
    });

    // Clicking G4 chip requests galaxy 4 including unscanned sectors
    fireEvent.click(container.querySelector('[data-sec-gal="4"]')!);
    await waitFor(() => {
      expect(
        commandCalls.some(
          (c) => c.command === "scanned_sectors" && c.galaxy === 4 && c.include_unscanned === true
        )
      ).toBe(true);
    });

    // Edit Refresh % + toggle All 14 Galaxies -> syncs Discovery % and saves config via Apply Split
    const refInput = container.querySelector("#sec-refresh-pct") as HTMLInputElement;
    const discInput = container.querySelector("#sec-discovery-pct") as HTMLInputElement;
    const allGalsCb = container.querySelector("#sec-all-galaxies") as HTMLInputElement;
    fireEvent.change(refInput, { target: { value: "35" } });
    fireEvent.click(allGalsCb);
    expect(discInput.value).toBe("65");
    expect(allGalsCb.checked).toBe(true);
    expect(container.querySelector("#sec-split-readout")?.textContent).toContain(
      "35 Refresh · 65 Discovery"
    );

    fireEvent.click(container.querySelector("#btn-sec-save-split")!);
    await waitFor(() => {
      expect(
        commandCalls.some(
          (c) =>
            c.command === "update_config" &&
            c.config?.galaxy_scan_refresh_pct === 35 &&
            c.config?.galaxy_sweep_pages === 100 &&
            c.config?.galaxy_scan_explore_first === true &&
            c.config?.galaxy_scan_all_galaxies === true
        )
      ).toBe(true);
    });
  });
});
