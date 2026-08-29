import React from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import { copyNotificationCode, formatNotificationCode } from "./notificationId";

function formatNotifAge(tsVal: unknown): string {
  const ts = safeFiniteNumber(tsVal, 0) ?? 0;
  if (ts <= 0) return "just now";
  const sec = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export const HelpAlertModal: React.FC = () => {
  const {
    dashboard,
    activeHelpNotif,
    ackActiveHelpPopup,
    closeHelpPopupOnly,
    openNotificationCenter,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const n = activeHelpNotif;
  const notifCode = formatNotificationCode(n);
  const sev = String(n?.severity || "ALERT").toUpperCase();
  const title = String(n?.title || "Action Validation Needs Attention");
  const cnt = Math.max(1, Math.round(safeFiniteNumber(n?.occurrence_count, 1) ?? 1));
  const metaParts: string[] = [];
  if (n?.planet_coords) metaParts.push(`[${n.planet_coords}]`);
  if (n?.action_kind) metaParts.push(String(n.action_kind));
  if (cnt > 1) metaParts.push(`x${cnt}`);
  if (n) metaParts.push(formatNotifAge(n.updated_ts));
  const metaTxt = n ? metaParts.join(" · ") : "SYSTEM";

  const isCaptchaGate = Boolean(
    n &&
      (n.category === "captcha_gate" ||
        n.category === "antibot" ||
        dashboard?.bot_control?.captcha_blocked)
  );

  const onCaptchaResume = async () => {
    try {
      await apiCommand({ command: "bot_control", paused: false, captcha_solved: true });
      await ackActiveHelpPopup("read");
      await loadDashboard({ refresh: true, force: true });
      showFeedback("CAPTCHA solved confirmed — page refresh & Commander automation resumed.", "ok");
    } catch (err: any) {
      showFeedback(`Resume failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <div
      className="notif-modal-backdrop"
      id="notif-help-modal"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="notif-help-title"
      aria-describedby="notif-help-prompt"
      hidden={!n}
    >
      <div className="notif-modal-card notif-help-card">
        <div className="window-bar">
          <div className="window-title-group">
            <span className="module-code tabular" id="notif-help-sev">
              {sev}
            </span>
            <span
              className="notif-id-pill tabular"
              id="notif-help-id"
              title="Notification ID — copy and share to troubleshoot"
            >
              {notifCode}
            </span>
            <h2 id="notif-help-title">{title}</h2>
          </div>
          <span className="module-meta tabular" id="notif-help-meta">
            {metaTxt}
          </span>
        </div>
        <div className="notif-modal-body">
          <p className="notif-msg-box tabular" id="notif-help-message">
            {String(n?.message || "")}
          </p>
          <p className="notif-prompt-copy" id="notif-help-prompt">
            {n
              ? String(
                  n.help_prompt ||
                    "Please inspect Chrome (:9222) to verify this action or acknowledge to continue."
                )
              : ""}
          </p>
        </div>
        <div className="notif-modal-footer">
          <div className="inline-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-notif-help-all"
              onClick={() => {
                closeHelpPopupOnly();
                openNotificationCenter();
              }}
            >
              All Notifications
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-notif-help-copy-id"
              onClick={() => {
                void copyNotificationCode(notifCode, showFeedback);
              }}
            >
              Copy ID ({notifCode})
            </button>
          </div>
          <div className="inline-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-notif-help-dismiss"
              onClick={() => (isCaptchaGate ? closeHelpPopupOnly() : ackActiveHelpPopup("dismissed"))}
            >
              {isCaptchaGate ? "Hide Popup" : "Dismiss"}
            </button>
            {isCaptchaGate ? (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                id="btn-captcha-resume"
                onClick={onCaptchaResume}
              >
                Solved in Chrome — Resume Bot
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                id="btn-notif-help-ack"
                onClick={() => ackActiveHelpPopup("read")}
              >
                Acknowledge
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default HelpAlertModal;
