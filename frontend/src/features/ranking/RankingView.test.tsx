import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RankingView } from "./RankingView";
import {
  SeededConsole,
  createBaseDashboard,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

describe("RankingView sign formatting, extreme/NaN sample resilience, and SVG chart safety", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("formats negative, zero, and sub-threshold ranking rates without double signs or '-0.0'", async () => {
    const dashNeg = createBaseDashboard({
      ranking: {
        current: {
          server_rank: 200,
          server_points: 5000,
          estimated_points: -0.01,
          buildings_points: 3000,
          research_points: 1000,
          spice_value: 20000,
          newbie_protected: true,
          attack_zone: { label: "AZ 3", range_text: "2,000 - 5,000" },
        },
        rates: {
          pts_per_hr_steady: -1.5,
          net_resources_per_hr: -500,
          speedup_factor: 5,
        },
        horizons: [],
        milestones: [],
        observed_trend: {
          sample_count: 4,
          delta_points: -12,
          observed_pts_per_hr: -0.5,
          elapsed_hours: 24,
          rank_climbed: 0,
          samples: [
            { estimated_points: 5012 },
            { estimated_points: 5000 },
          ],
        },
      },
    });

    const { container, unmount } = render(
      <SeededConsole page="ranking" dashboard={dashNeg}>
        <RankingView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelector("#rnk-rate-hr")?.textContent).toBe("-1.50 pts/hr");
    });

    const estPtsText = container.querySelector("#rnk-est-pts")?.textContent || "";
    expect(estPtsText.startsWith("~0.0")).toBe(true);
    expect(estPtsText).not.toContain("-0.0");

    const obsRateText = container.querySelector("#rnk-observed-rate")?.textContent || "";
    expect(obsRateText).toContain("-0.5 pts/hr observed");

    const obsSummary = container.querySelector("#rnk-observed-summary")?.textContent || "";
    expect(obsSummary).not.toContain("+-");
    expect(obsSummary).not.toContain("climbed +0");

    unmount();

    // Zero / sub-threshold negative steady rate renders "+0.00 pts/hr"
    const dashZero = createBaseDashboard({
      ranking: {
        ...dashNeg.ranking,
        rates: {
          pts_per_hr_steady: -0.001,
          net_resources_per_hr: 0,
          speedup_factor: 5,
        },
      },
    });

    const zeroView = render(
      <SeededConsole page="ranking" dashboard={dashZero}>
        <RankingView />
      </SeededConsole>
    );
    await waitFor(() => {
      expect(zeroView.container.querySelector("#rnk-rate-hr")?.textContent).toBe("+0.00 pts/hr");
    });
  });

  it("renders #rnk-chart SVG cleanly without NaN or Infinity when rank history contains extreme/non-finite points", async () => {
    const dashExtreme = createBaseDashboard({
      ranking: {
        current: {
          server_rank: 1,
          server_points: Number.POSITIVE_INFINITY,
          estimated_points: Number.NaN,
          buildings_points: 1000,
          research_points: 500,
          spice_value: Number.NaN,
          newbie_protected: false,
          attack_zone: { label: "AZ 10", range_text: "Top" },
        },
        rates: {
          pts_per_hr_steady: Number.NaN,
          net_resources_per_hr: Number.POSITIVE_INFINITY,
          speedup_factor: 5,
        },
        horizons: [],
        milestones: [],
        observed_trend: {
          sample_count: 3,
          delta_points: Number.NaN,
          observed_pts_per_hr: Number.NaN,
          elapsed_hours: 2,
          rank_climbed: 0,
          samples: [
            { estimated_points: 1000 },
            { estimated_points: Number.NaN },
            { estimated_points: 1500 },
          ],
        },
      },
    });

    const { container } = render(
      <SeededConsole page="ranking" dashboard={dashExtreme}>
        <RankingView />
      </SeededConsole>
    );

    await waitFor(() => {
      const svg = container.querySelector("#rnk-chart");
      expect(svg).not.toBeNull();
      expect(svg!.children.length).toBeGreaterThanOrEqual(1);
    });

    const text = container.textContent || "";
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("Infinity");
    expect(text).not.toContain("∞");

    const svgHtml = container.querySelector("#rnk-chart")!.outerHTML;
    expect(svgHtml).not.toContain("NaN");
    expect(svgHtml).not.toContain("Infinity");
  });
});
