import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BuildingsView } from "./BuildingsView";
import {
  SeededConsole,
  createBaseDashboard,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

describe("BuildingsView corrupt telemetry, whitespace bottleneck, and Blast Furnace button guards", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("falls back safely on corrupt building_time_divisor, whitespace bottleneck, and corrupt blast_furnace_pct", async () => {
    const dash = createBaseDashboard({
      buildings_eta: {
        planet: {
          planet_id: 12476,
          coords: "02:380:12",
          dev_center_level: 3,
          microsystem_level: 0,
          building_time_divisor: "bad" as any,
        },
        planet_economy: {
          blast_furnace_pct: "bad" as any,
        },
        buildings: [
          {
            building_id: 1,
            key: "b1",
            name: "Pig iron mine",
            category: "mines",
            current_level: 4,
            next_level: 5,
            missing_total: 0,
            build_time_text: "10s",
            wait_eta: "Ready",
            reach_eta: "10s",
            wait_hours: 0,
            bottleneck: "   ",
            can_build_live: false,
            offer_visible: true,
            unmet_prerequisites: [],
          },
          {
            building_id: 2,
            key: "b2",
            name: "Blast furnace",
            category: "mines",
            current_level: 3,
            next_level: 4,
            missing_total: 0,
            build_time_text: "20s",
            wait_eta: "Ready",
            reach_eta: "20s",
            wait_hours: 0,
            bottleneck: undefined as any,
            can_build_live: true,
            offer_visible: true,
            unmet_prerequisites: [],
          },
        ],
        researches: [],
      },
    });

    const { container } = render(
      <SeededConsole page="buildings" dashboard={dash}>
        <BuildingsView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelector("#eta-table-tbody")?.textContent).toContain("Pig iron mine");
    });

    const metaText = container.querySelector("#bld-planet-meta")?.textContent || "";
    expect(metaText).not.toContain("Div xbad");
    expect(metaText).toContain("Div x1");

    const tbodyText = container.querySelector("#eta-table-tbody")!.textContent || "";
    expect(tbodyText).toContain("Ready");
    expect(tbodyText).not.toContain("()");
    expect(tbodyText).not.toContain("(undefined)");
    expect(tbodyText).not.toContain("bad%");
  });
});
