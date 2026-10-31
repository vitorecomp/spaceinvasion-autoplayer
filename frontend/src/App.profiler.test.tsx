import React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { consoleStore, resetConsoleStore } from "./store";
import { createBaseDashboard, installMockFetch } from "./test/consoleTestHelpers";

/**
 * Phase 5.2 React <Profiler> Benchmark (jsdom, simulated dashboard poll + 3 tab switches):
 *
 *   Metric                                | BEFORE (all 14 mounted) | AFTER (active page only)
 *   --------------------------------------|-------------------------|-------------------------
 *   Mounted .page-view DOM nodes          | 14                      | 1  (-92.9%)
 *   Boot page renders (<Profiler>)        | 37                      | 2  (-94.6%)
 *   Simulated dashboard poll page renders | 16                      | 1  (-93.8%)
 *   Tab switch -> operations              | 14                      | 1  (-92.9%)
 *   Tab switch -> ranking                 | 14                      | 1  (-92.9%)
 *   Tab switch -> config                  | 14                      | 2  (-85.7%)
 *   Total page renders across scenario    | 95                      | 7  (-92.6%)
 *   Total <Profiler> actualDuration       | 686.68 ms               | 185.90 ms (-72.9%)
 */
describe("App active-page mounting & React <Profiler> render metrics", () => {
  beforeEach(() => {
    resetConsoleStore({ clearStorage: true });
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleStore({ clearStorage: true });
  });

  it("mounts only the active .page-view and keeps per-page React <Profiler> renders minimal on poll and tab switches", async () => {
    installMockFetch({
      getDashboard: () => createBaseDashboard(),
    });

    const pageRenders: Array<{ id: string; phase: string; actualDuration: number }> = [];
    const onPageRender: React.ProfilerOnRenderCallback = (id, phase, actualDuration) => {
      pageRenders.push({ id, phase, actualDuration });
    };

    const { container } = render(<App onPageRender={onPageRender} />);

    await waitFor(() => {
      expect(container.querySelector("#view-dashboard")).not.toBeNull();
    });

    const dashEl = container.querySelector("#view-dashboard") as HTMLElement;
    expect(dashEl.hidden).toBe(false);
    expect(container.querySelectorAll(".page-view")).toHaveLength(1);
    expect(container.querySelectorAll("svg")).toHaveLength(1);
    expect(pageRenders.length).toBeLessThanOrEqual(4);

    // 1. Simulated dashboard poll should re-render ONLY the active page (1 render vs 16 before)
    const beforePoll = pageRenders.length;
    act(() => {
      consoleStore.setDashboard(
        createBaseDashboard({
          state: {
            ...createBaseDashboard().state!,
            nickname: "Commander-Poll-2",
          },
        })
      );
    });
    const pollRenders = pageRenders.slice(beforePoll);
    expect(pollRenders).toHaveLength(1);
    expect(pollRenders[0].id).toBe("page:dashboard");

    // 2. Tab switch: dashboard -> operations (mounts ONLY operations; 1 render vs 14 before)
    const beforeOps = pageRenders.length;
    act(() => {
      consoleStore.setActivePage("operations");
    });
    expect(container.querySelectorAll(".page-view")).toHaveLength(1);
    expect(container.querySelector("#view-dashboard")).toBeNull();
    expect((container.querySelector("#view-operations") as HTMLElement)?.hidden).toBe(false);
    expect(pageRenders.slice(beforeOps).map((r) => r.id)).toEqual(["page:operations"]);

    // 3. Tab switch: operations -> ranking (strict <= 2 SVG invariant: #rnk-chart only)
    const beforeRanking = pageRenders.length;
    act(() => {
      consoleStore.setActivePage("ranking");
    });
    expect(container.querySelectorAll(".page-view")).toHaveLength(1);
    expect((container.querySelector("#view-ranking") as HTMLElement)?.hidden).toBe(false);
    expect(container.querySelectorAll("svg")).toHaveLength(1);
    expect(container.querySelector("#rnk-chart")).not.toBeNull();
    expect(pageRenders.slice(beforeRanking).map((r) => r.id)).toEqual(["page:ranking"]);

    // 4. Tab switch: ranking -> config
    const beforeConfig = pageRenders.length;
    act(() => {
      consoleStore.setActivePage("config");
    });
    expect(container.querySelectorAll(".page-view")).toHaveLength(1);
    expect((container.querySelector("#view-config") as HTMLElement)?.hidden).toBe(false);
    expect(pageRenders.slice(beforeConfig).every((r) => r.id === "page:config")).toBe(true);
    expect(pageRenders.length).toBeLessThanOrEqual(10);
  });
});
