import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AttacksView } from "../attacks";
import { ColoniesView } from "../colonies";
import { RecyclersView } from "../recyclers";
import { EspionageView } from "./EspionageView";
import {
  SeededConsole,
  createBaseDashboard,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

describe("Espionage, Colonies, Attacks & Recyclers intel rendering parity", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("renders SPACIA & multi-system scanned targets, 330-field Unprobed colonies, WIN battles, and 14,310 Kry recycler harvests without NaN/undefined", async () => {
    const dash = createBaseDashboard();

    const espData = {
      ok: true,
      targets: [
        {
          coords: "02:380:04",
          planet_name: "Spacia Planet",
          owner: "Spacia",
          status_color: "yellow",
          is_spacia: true,
          distance: 8,
          spice_eq: 52000,
          resources: { pig_iron: 40000, metal: 20000, kryptonite: 10000, spice: 5000 },
        },
        {
          coords: "02:381:05",
          planet_name: "IdleSystem381",
          owner: "SleeperA",
          status_color: "yellow",
          is_spacia: false,
          distance: 15,
          spice_eq: 24000,
          resources: { pig_iron: 20000, metal: 10000, kryptonite: 5000, spice: 2000 },
        },
        {
          coords: "02:382:07",
          planet_name: "IdleSystem382",
          owner: "SleeperB",
          status_color: "yellow",
          is_spacia: false,
          distance: 22,
          spice_eq: 18000,
          resources: { pig_iron: 15000, metal: 8000, kryptonite: 4000, spice: 1000 },
        },
      ],
      reports: [],
    };

    const colonyData = {
      ok: true,
      min_fields: 300,
      owned_planets: 2,
      max_planets: 11,
      candidates: [
        {
          coords: "02:380:08",
          position: 8,
          colonize_min_fields: 330,
          colonize_max_fields: 345,
          temperature: 22,
          grade: "S",
          probed: false,
          status: "Unprobed",
        },
      ],
      tried_planets: [],
    };

    const atkData = {
      ok: true,
      summary: { total_raids: 4, wins: 4, losses: 0, total_loot_spice_eq: 120000 },
      battles: [
        {
          id: 1,
          coords: "02:378:15",
          target_coords: "02:378:15",
          defender_name: "FarmPrime",
          outcome: "WIN",
          ts: 1700000100,
          loot: { pig_iron: 12000, metal: 6000, kryptonite: 3000, spice: 1500 },
          loot_spice_eq: 24000,
        },
      ],
      blocks: [],
    };

    const recData = {
      ok: true,
      summary: { harvests: 2, total_pig_iron: 20000, total_kryptonite: 14310 },
      recycles: [
        {
          id: 1,
          coords: "02:380:04",
          pig_iron: 20000,
          kryptonite: 14310,
          ts: 1700000200,
        },
      ],
      debris_targets: [],
      other_emails: [
        {
          id: 99,
          category: "fleet_return",
          folder_type: "fleet_return",
          subject: "Fleet returned from 02:380:04",
          body_text: "14,310 Kry collected",
          ts: 1700000250,
        },
      ],
    };

    const { container } = render(
      <SeededConsole
        page="espionage"
        dashboard={dash}
        espData={espData}
        colonyData={colonyData}
        atkData={atkData}
        recData={recData}
      >
        <EspionageView />
        <ColoniesView />
        <AttacksView />
        <RecyclersView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelector("#view-espionage")?.textContent).toContain("02:380:04");
    });

    const espTxt = container.querySelector("#view-espionage")!.textContent || "";
    const colTxt = container.querySelector("#view-colonies")!.textContent || "";
    const atkTxt = container.querySelector("#view-attacks")!.textContent || "";
    const recTxt = container.querySelector("#view-recyclers")!.textContent || "";

    for (const txt of [espTxt, colTxt, atkTxt, recTxt]) {
      expect(txt).not.toContain("NaN");
      expect(txt).not.toContain("undefined");
    }

    expect(espTxt.toUpperCase()).toContain("SPACIA");
    expect(espTxt).toContain("02:380:04");
    expect(espTxt).toContain("02:381:05");
    expect(espTxt).toContain("02:382:07");

    expect(colTxt).toContain("330");
    expect(colTxt).toContain("Unprobed");

    expect(atkTxt).toContain("WIN");
    expect(atkTxt).toContain("02:378:15");

    expect(recTxt).toContain("14,310");
    expect(recTxt).toContain("fleet_return");
  });
});
