import { safeFiniteNumber } from "../../api/httpClient";
import type { FeedbackKind } from "../../types/api";

export function formatNotificationCode(
  n: { id?: unknown; notification_code?: unknown } | null | undefined
): string {
  const rawCode = typeof n?.notification_code === "string" ? n.notification_code.trim() : "";
  if (rawCode) return rawCode;
  const nid = Math.max(0, Math.trunc(safeFiniteNumber(n?.id, 0) ?? 0));
  return nid > 0 ? `NTF-${String(nid).padStart(4, "0")}` : "NTF-0000";
}

export async function copyNotificationCode(
  code: string,
  showFeedback: (message: string, kind?: FeedbackKind) => void
): Promise<void> {
  const clean = String(code || "").trim();
  if (!clean) return;
  let copied = false;
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(clean);
      copied = true;
    }
  } catch {
    copied = false;
  }
  if (!copied && typeof document !== "undefined") {
    try {
      const ta = document.createElement("textarea");
      ta.value = clean;
      ta.setAttribute("readonly", "true");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      if (typeof document.execCommand === "function") {
        copied = Boolean(document.execCommand("copy"));
      }
      document.body.removeChild(ta);
    } catch {
      copied = false;
    }
  }
  showFeedback(
    copied
      ? `Copied notification ID ${clean} — paste it in chat to investigate.`
      : `Notification ID: ${clean}`,
    "ok"
  );
}
