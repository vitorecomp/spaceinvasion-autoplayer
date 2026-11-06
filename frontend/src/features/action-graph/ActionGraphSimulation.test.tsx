import React from "react";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackBanner } from "../../components/layout/FeedbackBanner";
import { ConsoleProvider, useConsoleActions, useGaCalc } from "../../store";
import { ConfigView } from "../config";
import { ActionGraphView } from "./ActionGraphView";
import {
  SeededConsole,
  createBaseDashboard,
  installMockFetch,
  jsonResponse,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

function makeSampleGraph(overrides: Record<string, any> = {}) {
  return {
    mode: "priority",
    simulated: false,
    state_available: true,
    is_dirty: false,
    changed_keys: [],
    active_config: createBaseDashboard().config,
    effective_config: createBaseDashboard().config,
    lanes: [
      {
        id: "lane:planet:12476",
        scope: "planet",
        label: "Homeworld [02:380:12]",
        planet_id: 12476,
        coords: "02:380:12",
        planet_type: "normal",
        energy: 220,
        node_count: 2,
      },
    ],
    nodes: [
      {
        id: "act_0",
        order_index: 0,
        exec_rank: 0,
        topological_layer: 0,
        lane_id: "lane:planet:12476",
        priority: "P0",
        kind: "ALERT_HOSTILE",
        category: "defense",
        status: "blocked",
        planet_id: 12476,
        planet_coords: "02:380:12",
        planet_name: "Homeworld",
        target_id: null,
        target_name: "Escape flight / Fleet Save",
        target_level: null,
        reason: "Incoming hostile attack",
        executable_now: false,
        cost: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
        missing: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
        wait_seconds: 0,
        wait_eta: "Ready",
        duration_seconds: 0,
        duration_text: "Instant",
        total_seconds: 0,
        total_eta: "Ready",
        eta_bottleneck: null,
        in_degree: 0,
        out_degree: 1,
      },
      {
        id: "act_1",
        order_index: 1,
        exec_rank: 1,
        topological_layer: 1,
        lane_id: "lane:planet:12476",
        priority: "P0",
        kind: "ESCAPE_FLIGHT",
        category: "defense",
        status: "ready",
        planet_id: 12476,
        planet_coords: "02:380:12",
        planet_name: "Homeworld",
        target_id: null,
        target_name: "Save -> 02:380:15",
        target_level: null,
        reason: "Evacuate fleet and spice",
        executable_now: true,
        cost: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 4000 },
        missing: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
        wait_seconds: 0,
        wait_eta: "Ready",
        duration_seconds: 0,
        duration_text: "Instant",
        total_seconds: 0,
        total_eta: "Ready",
        eta_bottleneck: null,
        in_degree: 1,
        out_degree: 0,
      },
    ],
    edges: [
      {
        id: "e_0_1",
        source: "act_0",
        target: "act_1",
        kind: "survival_gate",
        label: "P0 Escape",
      },
    ],
    summary: {
      total_nodes: 2,
      total_edges: 1,
      ready_count: 1,
      waiting_count: 0,
      blocked_count: 1,
      queued_count: 0,
      max_layer: 1,
      critical_path_eta: "Ready",
    },
    ...overrides,
  };
}

describe("ActionGraph simulation preview, roundtrip revert, explicit apply, error banner, and refcounted calc", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("renders DAG SVG with hostile P0 nodes, opens node inspector on click, and stays hidden on dashboard", async () => {
    const dash = createBaseDashboard();
    const graph = makeSampleGraph();
    installMockFetch({ getDashboard: () => dash });

    const { container, unmount } = render(
      <SeededConsole page="dashboard" dashboard={dash} actionGraph={graph}>
        <ActionGraphView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect((container.querySelector("#view-action-graph") as HTMLElement).hidden).toBe(true);
    });
    unmount();

    const agView = render(
      <SeededConsole page="action-graph" dashboard={dash} actionGraph={graph}>
        <ActionGraphView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect((agView.container.querySelector("#view-action-graph") as HTMLElement).hidden).toBe(false);
      expect(agView.container.querySelectorAll(".ag-node-card").length).toBe(2);
    });

    expect(agView.container.querySelector("#ag-edges-svg")).not.toBeNull();
    expect(agView.container.querySelector("#ag-sim-status-pill")?.textContent).toBe("LIVE ACTIVE CONFIG");

    const viewText = (agView.container.querySelector("#view-action-graph")?.textContent || "").toLowerCase();
    expect(viewText).toContain("escape flight");
    expect(viewText).not.toContain("configuração");
    expect(viewText).not.toContain("edifícios");

    // Click first node -> opens inspector showing #1
    fireEvent.click(agView.container.querySelectorAll(".ag-node-card")[0]);
    await waitFor(() => {
      expect(agView.container.querySelector(".ag-inspector-bar")?.textContent).toContain("#1");
    });
  });

  it("simulates isolated parameter overrides without calling update_config, reverts cleanly, and applies explicitly", async () => {
    let serverDash = createBaseDashboard();
    const baseGraph = makeSampleGraph();

    const { postedCommands } = installMockFetch({
      getDashboard: () => serverDash,
      onCommand: (body) => {
        if (body.command === "action_graph") {
          const overrides = body.config_overrides || {};
          const effCfg = { ...serverDash.config, ...overrides };
          const changedKeys = Object.keys(overrides).filter(
            (k) => String((overrides as any)[k]) !== String((serverDash.config as any)?.[k])
          );
          return {
            ok: true,
            action_graph: makeSampleGraph({
              mode: effCfg.planner_mode || "priority",
              simulated: changedKeys.length > 0,
              is_dirty: changedKeys.length > 0,
              changed_keys: changedKeys,
              active_config: serverDash.config,
              effective_config: effCfg,
            }),
          };
        }
        if (body.command === "update_config") {
          const nextCfg = { ...serverDash.config, ...(body.config || {}) };
          serverDash = createBaseDashboard({
            planner_mode: String(nextCfg.planner_mode || "priority"),
            config: nextCfg,
          });
          return { ok: true, config: nextCfg };
        }
        return { ok: true };
      },
    });

    const { container } = render(
      <SeededConsole page="action-graph" dashboard={serverDash} actionGraph={baseGraph}>
        <FeedbackBanner />
        <ActionGraphView />
        <ConfigView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelector("#ag-cfg-playstyle")).not.toBeNull();
    });

    // Change playstyle to "miner" and simulate -> preview banner, ZERO update_config calls
    const playSel = container.querySelector("#ag-cfg-playstyle") as HTMLSelectElement;
    fireEvent.change(playSel, { target: { value: "miner" } });
    fireEvent.click(container.querySelector("#btn-ag-simulate")!);

    await waitFor(() => {
      expect(container.querySelector("#ag-sim-status-pill")?.textContent).toBe("SIMULATION PREVIEW · UNSAVED");
      expect(container.querySelector("#ag-dirty-badge")?.textContent).toBe("MODIFIED (PREVIEW)");
    });
    expect(postedCommands.filter((c) => c.command === "update_config").length).toBe(0);

    // Revert back to "fleeter" and re-simulate -> back to LIVE ACTIVE CONFIG / ACTIVE SYNCED
    fireEvent.change(playSel, { target: { value: "fleeter" } });
    fireEvent.click(container.querySelector("#btn-ag-simulate")!);

    await waitFor(() => {
      expect(container.querySelector("#ag-sim-status-pill")?.textContent).toBe("LIVE ACTIVE CONFIG");
      expect(container.querySelector("#ag-dirty-badge")?.textContent).toBe("ACTIVE SYNCED");
    });

    // Change playstyle to "hybrid" and explicitly Apply -> calls update_config & syncs banner
    fireEvent.change(playSel, { target: { value: "hybrid" } });
    fireEvent.click(container.querySelector("#btn-ag-apply")!);

    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) => c.command === "update_config" && c.config?.playstyle === "hybrid"
        )
      ).toBe(true);
      expect(container.querySelector("#feedback-banner")?.textContent).toContain(
        "Applied parameters to active StrategyConfig"
      );
    });
  });

  it("displays 'Simulation preview error' on invalid override without mutating active config", async () => {
    const serverDash = createBaseDashboard();
    const { postedCommands } = installMockFetch({
      getDashboard: () => serverDash,
      onCommand: (body) => {
        if (body.command === "action_graph") {
          return jsonResponse({ ok: false, error: "Invalid combat_safety_margin" }, 400);
        }
        return { ok: true };
      },
    });

    const { container } = render(
      <SeededConsole page="action-graph" dashboard={serverDash} actionGraph={makeSampleGraph()}>
        <FeedbackBanner />
        <ActionGraphView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelector("#btn-ag-simulate")).not.toBeNull();
    });

    fireEvent.click(container.querySelector("#btn-ag-simulate")!);

    await waitFor(() => {
      expect(container.querySelector("#feedback-banner")?.textContent).toContain("Simulation preview error");
    });
    expect(postedCommands.some((c) => c.command === "update_config")).toBe(false);
  });

  it("refcounts concurrent beginGaCalc / endGaCalc operations cleanly without underflow or deadlock", async () => {
    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;
    let gaRef: ReturnType<typeof useGaCalc> | null = null;
    const Driver: React.FC = () => {
      actionsRef = useConsoleActions();
      gaRef = useGaCalc();
      return <div data-testid="ga-count">{gaRef.gaCalcCount}</div>;
    };

    const { getByTestId } = render(
      <ConsoleProvider disableInitialFetch>
        <Driver />
      </ConsoleProvider>
    );

    expect(getByTestId("ga-count").textContent).toBe("0");

    act(() => {
      actionsRef!.beginGaCalc();
    });
    expect(getByTestId("ga-count").textContent).toBe("1");
    expect(gaRef!.gaCalculating).toBe(true);

    act(() => {
      actionsRef!.beginGaCalc();
    });
    expect(getByTestId("ga-count").textContent).toBe("2");

    act(() => {
      actionsRef!.endGaCalc();
    });
    expect(getByTestId("ga-count").textContent).toBe("1");
    expect(gaRef!.gaCalculating).toBe(true);

    act(() => {
      actionsRef!.endGaCalc();
      // Extra endGaCalc must clamp cleanly at 0 (no underflow)
      actionsRef!.endGaCalc();
    });
    expect(getByTestId("ga-count").textContent).toBe("0");
    expect(gaRef!.gaCalculating).toBe(false);
  });
});
