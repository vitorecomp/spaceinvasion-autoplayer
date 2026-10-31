import React from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppHeader } from "../../components/layout/AppHeader";
import { ConsoleProvider } from "../../context/ConsoleContext";
import { HelpAlertModal } from "./HelpAlertModal";
import { NotificationCenterModal } from "./NotificationCenterModal";
import {
  createBaseDashboard,
  installMockFetch,
  resetConsoleTestEnv,
} from "../../test/consoleTestHelpers";

describe("Top-bar notification badge, auto-opened HelpAlertModal, and NotificationCenterModal persistence", () => {
  beforeEach(() => {
    resetConsoleTestEnv();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetConsoleTestEnv();
  });

  it("displays unread badge, auto-opens HelpAlertModal, opens NotificationCenterModal, and hides badge after Mark All Read", async () => {
    let notifState = {
      unread_count: 1,
      active_count: 1,
      items: [
        {
          id: 42,
          code: "NTF-0042",
          severity: "warning",
          category: "action_aborted",
          action_kind: "SPY",
          planet_coords: "02:380:12",
          target_name: "02:381:13",
          title: "SPY could not execute: 02:381:13",
          message: "Fleet dispatch failed: unexpected server error",
          help_prompt: "Please check [02:380:12] in Chrome (:9222) if operator action is needed.",
          status: "unread",
          occurrence_count: 1,
          updated_ts: Math.floor(Date.now() / 1000) - 5,
        },
      ],
    };

    const getDash = () =>
      createBaseDashboard({
        notifications: notifState,
      });

    const { postedCommands } = installMockFetch({
      getDashboard: getDash,
      onCommand: (body) => {
        if (body.command === "notifications_ack_all" || body.command === "notification_ack") {
          notifState = {
            unread_count: 0,
            active_count: 0,
            items: notifState.items.map((item) => ({ ...item, status: "read" })),
          };
          return { ok: true, notifications: notifState };
        }
        return { ok: true, notifications: notifState };
      },
    });

    const { container } = render(
      <ConsoleProvider>
        <AppHeader />
        <HelpAlertModal />
        <NotificationCenterModal />
      </ConsoleProvider>
    );

    await waitFor(() => {
      const badge = container.querySelector("#notif-unread-badge") as HTMLElement;
      expect(badge).not.toBeNull();
      expect(badge.hidden).toBe(false);
      expect(badge.textContent).toBe("1");
    });

    const helpModal = container.querySelector("#notif-help-modal") as HTMLElement;
    expect(helpModal.hidden).toBe(false);
    expect(container.querySelector("#notif-help-title")?.textContent).toContain("SPY");
    expect(container.querySelector("#notif-help-prompt")?.textContent).toContain("Chrome (:9222)");

    fireEvent.click(container.querySelector("#btn-notifications")!);
    await waitFor(() => {
      const center = container.querySelector("#notif-center-modal") as HTMLElement;
      expect(center.hidden).toBe(false);
      expect(container.querySelector("#notif-center-list")?.textContent).toContain("02:381:13");
    });

    fireEvent.click(container.querySelector("#btn-notif-mark-all")!);
    await waitFor(() => {
      expect(postedCommands.some((c) => c.command === "notifications_ack_all")).toBe(true);
      const badge = container.querySelector("#notif-unread-badge") as HTMLElement;
      expect(badge.hidden).toBe(true);
    });
  });
});
