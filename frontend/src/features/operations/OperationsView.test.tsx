import React from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackBanner } from "../../components/layout/FeedbackBanner";
import { OperationsView } from "./OperationsView";
import {
  SeededConsole,
  createBaseDashboard,
  installMockFetch,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

describe("OperationsView ETA filters, blocked fallbacks, and Manual Queue CRUD", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("renders all ETA columns, filters ready actions via [data-filter='ready'], and formats legacy blocked pills", async () => {
    const dash = createBaseDashboard({
      plan: [
        {
          priority: "P1",
          kind: "BUILD",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 1,
          target_name: "Pig iron mine",
          target_level: 9,
          executable_now: true,
          wait_hours: 0,
          wait_eta: "Ready",
          duration_text: "4m 12s",
          total_eta: "4m",
          reason: "Ready build",
        },
        {
          priority: "P2",
          kind: "BUILD",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 3,
          target_name: "Kryptonite mine",
          target_level: 7,
          executable_now: false,
          wait_hours: null,
          wait_eta: "—",
          duration_text: "8m 00s",
          total_eta: "—",
          eta_bottleneck: "metal",
          reason: "Blast furnace paused",
        },
        {
          // Legacy zero-wait non-executable blocked payload
          priority: "P2",
          kind: "WAIT_RESOURCES",
          planet_id: 12476,
          planet_coords: "02:380:12",
          target_id: 5,
          target_name: "Fusion plant",
          target_level: 11,
          executable_now: false,
          wait_hours: 0,
          eta_bottleneck: "pig_iron",
          reason: "Legacy blocked zero-wait",
        },
      ],
    });

    const { container } = render(
      <SeededConsole page="operations" dashboard={dash}>
        <OperationsView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#plan-tbody tr").length).toBe(3);
    });

    const thCount = container.querySelectorAll("#view-operations thead th").length;
    expect(thCount).toBeGreaterThanOrEqual(7);
    expect(container.querySelector("#plan-count-badge")?.textContent).toContain("3 TASKS");

    const blockedPills = Array.from(container.querySelectorAll("#plan-tbody .eta-pill.is-blocked")).map(
      (el) => el.textContent || ""
    );
    expect(blockedPills.some((t) => t.includes("— (MET)"))).toBe(true);
    expect(blockedPills.some((t) => t.includes("— (PIG)"))).toBe(true);

    const readyFilterBtn = container.querySelector('[data-filter="ready"]') as HTMLButtonElement;
    fireEvent.click(readyFilterBtn);

    await waitFor(() => {
      expect(container.querySelectorAll("#plan-tbody tr").length).toBe(1);
      expect(container.querySelector("#plan-count-badge")?.textContent).toContain("1 TASKS");
    });
  });

  it("renders matching colSpan placeholder when Operations queue is empty", async () => {
    const dash = createBaseDashboard({ plan: [] });
    const { container } = render(
      <SeededConsole page="operations" dashboard={dash}>
        <OperationsView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelector("#plan-tbody td.empty-cell")).not.toBeNull();
    });

    const thCount = container.querySelectorAll("#view-operations thead th").length;
    const emptyTd = container.querySelector("#plan-tbody td.empty-cell") as HTMLTableCellElement;
    expect(emptyTd.colSpan).toBe(thCount);
    expect(container.querySelector("#plan-count-badge")?.textContent).toBe("0 TASKS");
  });

  it("pins, filters, reorders, and drops Manual Queue items without NaN or undefined", async () => {
    let manualQueue = [
      {
        id: 10,
        priority: "P1",
        kind: "BUILD",
        planet_id: 12476,
        planet_coords: "02:380:12",
        target_id: 1,
        target_name: "Pig iron mine",
        target_level: 9,
        sort_rank: 1,
        status: "pending",
      },
      {
        id: 11,
        priority: "P1",
        kind: "SPY",
        planet_id: 12476,
        planet_coords: "02:380:12",
        target_coords: "02:380:04",
        target_name: "02:380:04",
        sort_rank: 2,
        status: "pending",
      },
    ];

    const buildDash = () =>
      createBaseDashboard({
        manual_queue: manualQueue,
        plan: manualQueue.map((item) => ({
          priority: item.priority,
          kind: item.kind,
          planet_id: item.planet_id,
          planet_coords: item.planet_coords,
          target_id: item.target_id ?? null,
          target_name: item.target_name,
          target_level: item.target_level ?? null,
          executable_now: true,
          wait_eta: "Ready",
          total_eta: "Ready",
          manual_queue_id: item.id,
          manual_sort_rank: item.sort_rank,
          reason: "Manual pinned item",
        })),
      });

    const { postedCommands } = installMockFetch({
      getDashboard: buildDash,
      onCommand: (body) => {
        if (body.command === "queue_add") {
          const nextId = 12;
          manualQueue = [
            ...manualQueue,
            {
              id: nextId,
              priority: body.priority || "P1",
              kind: body.kind || "BUILD",
              planet_id: 12476,
              planet_coords: "02:380:12",
              target_id: body.target_id ?? 1,
              target_name: "Pinned task",
              target_level: body.target_level ?? 9,
              sort_rank: manualQueue.length + 1,
              status: "pending",
            },
          ];
          return { ok: true, id: nextId, manual_queue: manualQueue };
        }
        if (body.command === "queue_update") {
          manualQueue = [manualQueue[1], manualQueue[0]].map((x, idx) => ({
            ...x,
            sort_rank: idx + 1,
          }));
          return { ok: true, manual_queue: manualQueue };
        }
        if (body.command === "queue_cancel") {
          manualQueue = manualQueue.filter((item) => item.id !== body.queue_id);
          return { ok: true, manual_queue: manualQueue };
        }
        return { ok: true };
      },
    });

    const { container } = render(
      <SeededConsole page="operations" dashboard={buildDash()}>
        <FeedbackBanner />
        <OperationsView />
      </SeededConsole>
    );

    await waitFor(() => {
      expect(container.querySelectorAll("#plan-tbody tr").length).toBe(2);
    });

    // Filter to pinned items
    const pinnedBtn = container.querySelector('[data-filter="pinned"]') as HTMLButtonElement | null;
    if (pinnedBtn) {
      fireEvent.click(pinnedBtn);
      await waitFor(() => {
        expect(container.querySelectorAll("#plan-tbody tr").length).toBe(2);
      });
    }

    // Reorder second pinned item up
    const upButtons = Array.from(container.querySelectorAll<HTMLButtonElement>("#plan-tbody button")).filter(
      (b) => b.title?.toLowerCase().includes("move up") || b.textContent?.trim() === "↑"
    );
    expect(upButtons.length).toBeGreaterThan(0);
    fireEvent.click(upButtons[upButtons.length - 1]);

    await waitFor(() => {
      expect(postedCommands.some((c) => c.command === "queue_update")).toBe(true);
    });

    // Drop a pinned item
    const dropBtn = Array.from(container.querySelectorAll<HTMLButtonElement>("#plan-tbody button")).find(
      (b) => b.textContent?.trim() === "Drop" || b.title?.toLowerCase().includes("remove")
    );
    expect(dropBtn).toBeTruthy();
    fireEvent.click(dropBtn!);

    await waitFor(() => {
      expect(postedCommands.some((c) => c.command === "queue_cancel")).toBe(true);
    });

    // Submit composer form to pin a new BUILD item
    const form = container.querySelector("#mq-composer-form") as HTMLFormElement;
    expect(form).not.toBeNull();
    fireEvent.submit(form);

    await waitFor(() => {
      expect(postedCommands.some((c) => c.command === "queue_add")).toBe(true);
    });

    const opsText = container.querySelector("#view-operations")!.textContent || "";
    expect(opsText).not.toContain("NaN");
    expect(opsText).not.toContain("undefined");
  });
});
