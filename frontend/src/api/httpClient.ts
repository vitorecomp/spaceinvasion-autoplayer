export interface ApiError extends Error {
  status?: number;
  rateLimited?: boolean;
  retryAfter?: number;
}

export function safeFiniteNumber(val: unknown, fallback: number | null = null): number | null {
  if (val === null || val === undefined || typeof val === "boolean" || typeof val === "object") {
    return fallback;
  }
  if (typeof val === "string") {
    const trimmed = val.trim();
    if (!trimmed) return fallback;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  if (typeof val === "number") {
    return Number.isFinite(val) ? val : fallback;
  }
  return fallback;
}

export async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs = 15000
): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal, cache: "no-store" });
  } finally {
    clearTimeout(timer);
  }
}

export function ensureJsonObject(
  parsed: unknown,
  res?: { ok?: boolean; status?: number } | null,
  preview = ""
): Record<string, any> {
  if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
    return parsed as Record<string, any>;
  }
  const kind = parsed === null ? "null" : Array.isArray(parsed) ? "array" : typeof parsed;
  const snippet = preview ? `: ${preview}` : "";
  const err: ApiError = new Error(
    res && !res.ok
      ? `HTTP ${res.status}${snippet || ` (non-object JSON ${kind})`}`
      : `Invalid JSON object response (expected object, got ${kind})`
  );
  err.status = res?.status;
  err.rateLimited = res?.status === 429;
  throw err;
}

export async function parseResponseJson(res: any): Promise<Record<string, any>> {
  if (typeof res?.text === "function") {
    const raw = await res.text();
    if (!raw || !raw.trim()) {
      return {};
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      const preview = raw.replace(/\s+/g, " ").trim().slice(0, 120);
      const err: ApiError = new Error(
        res.ok
          ? `Invalid JSON response (${preview || "empty"})`
          : `HTTP ${res.status}${preview ? `: ${preview}` : ""}`
      );
      err.status = res.status;
      err.rateLimited = res.status === 429;
      throw err;
    }
    const preview = raw.replace(/\s+/g, " ").trim().slice(0, 120);
    return ensureJsonObject(parsed, res, preview);
  }
  let parsed: unknown;
  try {
    parsed = await res.json();
  } catch (parseErr: any) {
    const err: ApiError = new Error(
      res && !res.ok
        ? `HTTP ${res.status}`
        : `Invalid JSON response (${parseErr?.message || "parse error"})`
    );
    err.status = res?.status;
    err.rateLimited = res?.status === 429;
    throw err;
  }
  return ensureJsonObject(parsed, res);
}

export function isRateLimitError(err: any): boolean {
  if (!err) return false;
  if (err.rateLimited || err.status === 429) return true;
  const msg = String(err.message || err || "").toLowerCase();
  return (
    msg.includes("rate-limit") ||
    msg.includes("rate limit") ||
    msg.includes("rate_limit") ||
    msg.includes("cooldown") ||
    msg.includes("30 page views") ||
    msg.includes("too many requests") ||
    msg.includes("retry after")
  );
}

export function feedbackKindForError(err: any): "warn" | "error" {
  return isRateLimitError(err) ? "warn" : "error";
}

export interface HttpBridgeCallbacks {
  onStatusChange?: (online: boolean, note?: string) => void;
  onNotifications?: (notifications: Record<string, any>) => void;
}

let bridgeCallbacks: HttpBridgeCallbacks = {};

export function registerHttpBridgeCallbacks(cbs: HttpBridgeCallbacks): void {
  bridgeCallbacks = cbs;
}

export async function apiGet<T = Record<string, any>>(
  url: string,
  timeoutMs = 15000
): Promise<T> {
  const res = await fetchWithTimeout(url, {}, timeoutMs);
  const data = ensureJsonObject(await parseResponseJson(res), res);
  if (!res.ok || data?.ok === false) {
    const err: ApiError = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.rateLimited = Boolean(data?.rate_limited || res.status === 429);
    err.retryAfter = safeFiniteNumber(data?.retry_after, 0) ?? 0;
    throw err;
  }
  if (data?.notifications && typeof data.notifications === "object") {
    bridgeCallbacks.onNotifications?.(data.notifications);
  }
  return data as T;
}

export async function apiCommand<T = Record<string, any>>(
  payload: Record<string, any>,
  opts?: { timeoutMs?: number }
): Promise<T> {
  const timeoutMs = Number(opts?.timeoutMs ?? 22050);
  let res: Response;
  try {
    res = await fetchWithTimeout(
      "/command",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      timeoutMs
    );
  } catch (err: any) {
    const msg = err?.name === "AbortError" ? "timeout" : err?.message || "unreachable";
    bridgeCallbacks.onStatusChange?.(false, msg);
    throw err;
  }
  const data = ensureJsonObject(await parseResponseJson(res), res);
  bridgeCallbacks.onStatusChange?.(true);
  if (!res.ok || data?.ok === false) {
    const err: ApiError = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.rateLimited = Boolean(data?.rate_limited || res.status === 429);
    err.retryAfter = safeFiniteNumber(data?.retry_after, 0) ?? 0;
    throw err;
  }
  if (data?.notifications && typeof data.notifications === "object") {
    bridgeCallbacks.onNotifications?.(data.notifications);
  }
  return data as T;
}
