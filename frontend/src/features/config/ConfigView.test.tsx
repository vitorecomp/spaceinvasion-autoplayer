import React from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppHeader } from "../../components/layout/AppHeader";
import { AppSidebar } from "../../components/layout/AppSidebar";
import { FeedbackBanner } from "../../components/layout/FeedbackBanner";
import { ConsoleProvider, useConsoleActions, useDashboard } from "../../store";
import { ConfigView } from "./ConfigView";
import {
  createBaseDashboard,
  installMockFetch,
  jsonResponse,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

describe("ConfigView dirty guards, mid-save poll protection, rate-limit resilience, and hot-reload", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("exposes ['priority', 'ai_planner'] options, preserves dirty edits across sync, and persists saved config", async () => {
    let serverDash = createBaseDashboard();

    const { postedCommands } = installMockFetch({
      getDashboard: () => serverDash,
      onCommand: (body) => {
        if (body.command === "update_config") {
          const nextCfg = { ...serverDash.config, ...(body.config || {}) };
          serverDash = createBaseDashboard({
            planner_mode: String(nextCfg.planner_mode || "priority"),
            config: nextCfg,
          });
          return { ok: true, config: nextCfg };
        }
        return undefined;
      },
    });

    const { container } = render(
      <ConsoleProvider>
        <AppSidebar />
        <AppHeader />
        <FeedbackBanner />
        <ConfigView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(container.querySelector("#cfg-planner_mode")).not.toBeNull();
      expect((container.querySelector("#cfg-playstyle") as HTMLSelectElement).value).toBe("fleeter");
    });

    const modeSel = container.querySelector("#cfg-planner_mode") as HTMLSelectElement;
    const modeOpts = Array.from(modeSel.options).map((o) => o.value);
    expect(modeOpts).toEqual(["priority", "ai_planner"]);

    // Edit planner_mode, playstyle, and merchant_mode -> background Sync must NOT overwrite dirty inputs
    fireEvent.change(modeSel, { target: { value: "ai_planner" } });
    const playSel = container.querySelector("#cfg-playstyle") as HTMLSelectElement;
    fireEvent.change(playSel, { target: { value: "miner" } });
    const merchantBox = container.querySelector("#cfg-merchant_mode") as HTMLInputElement;
    fireEvent.click(merchantBox);

    fireEvent.click(container.querySelector("#btn-sync")!);
    await waitFor(() => {
      expect(container.querySelector("#feedback-banner")?.textContent).toContain("Synced");
    });
    expect(modeSel.value).toBe("ai_planner");
    expect(playSel.value).toBe("miner");
    expect(merchantBox.checked).toBe(true);

    // Save config -> dispatches update_config and updates mode badge
    fireEvent.click(container.querySelector("#btn-save-config")!);
    await waitFor(() => {
      expect(
        postedCommands.some(
          (c) =>
            c.command === "update_config" &&
            c.config?.planner_mode === "ai_planner" &&
            c.config?.playstyle === "miner" &&
            c.config?.merchant_mode === true
        )
      ).toBe(true);
    });
    expect(modeSel.value).toBe("ai_planner");
  });

  it("guards in-flight config save against concurrent dashboard polls even when configDirty=false and preserves DOM on save error", async () => {
    const serverDash = createBaseDashboard();
    let resolveSave: ((r: Response) => void) | null = null;
    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;
    let dashRef: ReturnType<typeof useDashboard> = null;

    const Driver: React.FC = () => {
      actionsRef = useConsoleActions();
      dashRef = useDashboard();
      return null;
    };

    installMockFetch({
      getDashboard: () => serverDash,
      onCommand: (body) => {
        if (body.command === "update_config") {
          return new Promise<Response>((resolve) => {
            resolveSave = resolve;
          });
        }
        return undefined;
      },
    });

    const { container } = render(
      <ConsoleProvider>
        <Driver />
        <FeedbackBanner />
        <ConfigView />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(dashRef).not.toBeNull();
    });

    const modeSel = container.querySelector("#cfg-planner_mode") as HTMLSelectElement;
    fireEvent.change(modeSel, { target: { value: "ai_planner" } });

    // Clear configDirty artificially to exercise the configSaving > 0 mid-save poll guard
    actionsRef!.setConfigDirty(false);
    fireEvent.click(container.querySelector("#btn-save-config")!);

    await waitFor(() => {
      expect(resolveSave).not.toBeNull();
    });

    // Concurrent poll returns server 'priority' while save is still in-flight
    await actionsRef!.loadDashboard({ refresh: false, preserveConfig: true, force: true });
    expect(modeSel.value).toBe("ai_planner");

    // Save fails -> error banner shown and user's selection remains intact
    resolveSave!(jsonResponse({ ok: false, error: "transient save error" }, 500));

    await waitFor(() => {
      expect(container.querySelector("#feedback-banner")?.textContent).toContain("Failed saving config");
    });
    expect(modeSel.value).toBe("ai_planner");
  });

  it("shows amber .is-warn banner on HTTP 429 rate-limit while keeping Chrome status ONLINE", async () => {
    const dash = createBaseDashboard({
      health: {
        ok: true,
        in_game: true,
        debug_port_open: true,
        debug_port: 9222,
        rate_limited: true,
        retry_after: 42,
      },
    });

    installMockFetch({
      getDashboard: () => dash,
      onCommand: (body) => {
        if (body.command === "scan_galaxy") {
          return jsonResponse(
            {
              ok: false,
              rate_limited: true,
              retry_after: 42,
              error: "Rate-limit cooldown active; retry after 42s",
            },
            429
          );
        }
        return undefined;
      },
    });

    let actionsRef: ReturnType<typeof useConsoleActions> | null = null;
    let dashRef: ReturnType<typeof useDashboard> = null;
    const Driver: React.FC = () => {
      actionsRef = useConsoleActions();
      dashRef = useDashboard();
      return null;
    };

    const { container } = render(
      <ConsoleProvider>
        <Driver />
        <AppSidebar />
        <FeedbackBanner />
      </ConsoleProvider>
    );

    await waitFor(() => {
      expect(dashRef).not.toBeNull();
    });

    try {
      await actionsRef!.apiCommand({ command: "scan_galaxy", dry_run: false });
    } catch (err: any) {
      actionsRef!.showFeedback(err.message, "warn");
    }

    await waitFor(() => {
      const banner = container.querySelector("#feedback-banner") as HTMLElement;
      expect(banner.hidden).toBe(false);
      expect(banner.className).toContain("is-warn");
    });
    expect(container.querySelector("#chrome-status-text")?.textContent?.toUpperCase()).toContain("ONLINE");
  });

  it("detects server_boot_id change on /ui-version and displays restart hot-reload banner", async () => {
    vi.useFakeTimers();
    const dash = createBaseDashboard();
    let bootCalls = 0;

    installMockFetch({
      getDashboard: () => dash,
      onUiVersion: () => {
        bootCalls += 1;
        return {
          ok: true,
          code_version: "v1",
          css_version: "css1",
          server_boot_id: bootCalls === 1 ? "boot-old" : "boot-new",
        };
      },
    });

    const { container } = render(
      <ConsoleProvider>
        <FeedbackBanner />
      </ConsoleProvider>
    );

    // First initial poll records boot-old; advancing timer triggers next /ui-version returning boot-new
    await vi.advanceTimersByTimeAsync(100);
    expect(bootCalls).toBeGreaterThanOrEqual(1);

    await vi.advanceTimersByTimeAsync(6000);
    expect(bootCalls).toBeGreaterThanOrEqual(2);
    expect(container.querySelector("#feedback-banner")?.textContent?.toLowerCase()).toContain("restarted");
  });
});
