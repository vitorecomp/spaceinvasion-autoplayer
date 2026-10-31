import { afterEach, describe, expect, it, vi } from "vitest";
import {
  apiCommand,
  apiGet,
  ensureJsonObject,
  feedbackKindForError,
  isRateLimitError,
  parseResponseJson,
} from "./httpClient";
import { jsonResponse } from "../test/consoleTestHelpers";

describe("httpClient non-dict JSON, HTTP 502 HTML, 429 rate-limit, and timeout guards", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("ensureJsonObject & parseResponseJson reject null, arrays, numbers, and strings without TypeError", async () => {
    for (const raw of ["null", "[1,2,3]", "42", '"plain-string"']) {
      let caught: any = null;
      try {
        await parseResponseJson(jsonResponse(raw, 200));
      } catch (err) {
        caught = err;
      }
      expect(caught).toBeTruthy();
      expect(caught instanceof TypeError).toBe(false);
      expect(String(caught.message)).not.toContain("Cannot read properties of null");
    }
  });

  it("apiGet handles HTTP 429 null JSON body with rateLimited=true and warn feedback classification", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse("null", 429))
    );

    let caught: any = null;
    try {
      await apiGet("/dashboard?refresh=0");
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeTruthy();
    expect(caught instanceof TypeError).toBe(false);
    expect(String(caught.message)).not.toContain("Cannot read properties of null");
    expect(caught.rateLimited).toBe(true);
    expect(isRateLimitError(caught)).toBe(true);
    expect(feedbackKindForError(caught)).toBe("warn");
  });

  it("apiCommand handles HTTP 502 HTML gateway errors cleanly without 'Unexpected token'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse("<html><body>502 Bad Gateway</body></html>", 502))
    );

    let caught: any = null;
    try {
      await apiCommand({ command: "plan" });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeTruthy();
    expect(String(caught.message)).toContain("HTTP 502");
    expect(String(caught.message)).not.toContain("Unexpected token");
    expect(feedbackKindForError(caught)).toBe("error");
  });

  it("apiCommand uses a generous timeout >= 15000ms by default", async () => {
    const timeoutDelays: number[] = [];
    const origSetTimeout = globalThis.setTimeout;
    vi.spyOn(globalThis, "setTimeout").mockImplementation(((fn: any, ms?: number, ...args: any[]) => {
      if (typeof ms === "number") timeoutDelays.push(ms);
      return origSetTimeout(fn, ms, ...args);
    }) as any);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ok: true }))
    );

    await apiCommand({ command: "scan_galaxy", dry_run: true });
    expect(timeoutDelays.some((d) => d >= 15000)).toBe(true);
  });

  it("ensureJsonObject passes plain object payloads through unchanged", () => {
    const obj = { ok: true, count: 3 };
    expect(ensureJsonObject(obj, { ok: true, status: 200 })).toEqual(obj);
  });
});
