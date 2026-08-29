import React, { useEffect } from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../context/ConsoleContext";
import { DashboardPayload } from "../types/api";
import { CombatView } from "../features/combat";
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
  espSeed?: Record<string, any>;
  colSeed?: Record<string, any>;
  atkSeed?: Record<string, any>;
  recSeed?: Record<string, any>;
  children: React.ReactNode;
}

const SeedState: React.FC<Omit<SeedHarnessProps, "children">> = ({
  page,
  espSeed,
  colSeed,
  atkSeed,
  recSeed,
}) => {
  const {
    setDashboard,
    setCombatData,
    setEspData,
    setColonyData,
    setAtkData,
    setRecData,
  } = useConsole();

  useEffect(() => {
    setDashboard(SAMPLE_DASHBOARD);
    if (SAMPLE_DASHBOARD.combat) setCombatData(SAMPLE_DASHBOARD.combat);
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
    setAtkData,
    setColonyData,
    setCombatData,
    setDashboard,
    setEspData,
    setRecData,
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

  it("renders EspionageView targets & historical reports and dispatches Spy/Attack", async () => {
    const espSeed = {
      summary: { spied_targets: 1, total_targets: 1, total_reports: 1, spacia_count: 1 },
      targets: [
        {
          coords: "02:383:02",
          planet_name: "Spacia",
          owner: "Spacia",
          is_spacia: true,
          has_spy_intel: true,
          resources: { pig_iron: 40000, metal: 20000, kryptonite: 10000, spice: 5000 },
          spice_eq: 30000,
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
    expect(container.querySelector("#esp-reports-tbody")?.textContent).toContain("B:b1:12");

    const buttons = Array.from(container.querySelectorAll("#esp-targets-tbody button")).map(
      (b) => b.textContent
    );
    expect(buttons).toEqual(["Spy", "Sim", "Attack"]);

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
});
