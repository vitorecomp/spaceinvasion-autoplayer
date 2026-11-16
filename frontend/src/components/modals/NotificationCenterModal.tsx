import React from "react";
import {
  feedbackKindForError,
  safeFiniteNumber } from "../../api/httpClient";
import { useConsoleActions,
  useNotifCenterOpen,
  useNotifications,
  useUiPrefs,
} from "../../store";
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

export const NotificationCenterModal: React.FC = () => {
  const notifications = useNotifications();
  const notifCenterOpen = useNotifCenterOpen();
  const uiPrefs = useUiPrefs();
  const {
    closeNotificationCenter,
    refreshNotificationCenter,
    setUiPref,
    apiCommand,
    showFeedback,
  } = useConsoleActions();

  const items = Array.isArray(notifications.items) ? notifications.items : [];
  const curFilter = uiPrefs.notifFilter || "active";

  const onFilterClick = (f: "active" | "unread" | "all") => {
    setUiPref("notifFilter", f);
    refreshNotificationCenter(f);
  };

  const onMarkAllRead = async () => {
    try {
      await apiCommand({ command: "notifications_ack_all", status: "read" });
      await refreshNotificationCenter();
    } catch (err: any) {
      showFeedback(`Failed marking notifications read: ${err.message}`, feedbackKindForError(err));
    }
  };

  const onAckOne = async (nid: number, status: "read" | "dismissed") => {
    try {
      await apiCommand({ command: "notification_ack", notification_id: nid, status });
      await refreshNotificationCenter();
    } catch (err: any) {
      showFeedback(`Failed updating notification: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <div
      className="notif-modal-backdrop"
      id="notif-center-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="notif-center-title"
      hidden={!notifCenterOpen}
    >
      <div className="notif-modal-card notif-center-card">
        <div className="window-bar">
          <div className="window-title-group">
            <span className="module-code tabular">NTF</span>
            <h2 id="notif-center-title">Persistent System Notifications</h2>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm modal-close-btn"
            id="btn-notif-center-close"
            onClick={closeNotificationCenter}
          >
            Close
          </button>
        </div>
        <div className="notif-center-toolbar">
          <div className="tab-group">
            {(
              [
                ["active", "Active"],
                ["unread", "Unread"],
                ["all", "All History"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`tab-btn${curFilter === key ? " active" : ""}`}
                data-notif-filter={key}
                onClick={() => onFilterClick(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-notif-mark-all"
            onClick={onMarkAllRead}
          >
            Mark All Read
          </button>
        </div>
        <div className="notif-center-body" id="notif-center-list">
          {items.length === 0 ? (
            <p className="muted">
              {curFilter === "unread"
                ? "No unread notifications."
                : "No notifications recorded in SQLite yet."}
            </p>
          ) : (
            items.map((n) => {
              const notifCode = formatNotificationCode(n);
              const sev = String(n.severity || "warning").toLowerCase();
              const cnt = Math.max(1, Math.round(safeFiniteNumber(n.occurrence_count, 1) ?? 1));
              const mParts: string[] = [];
              if (n.planet_coords) mParts.push(`[${n.planet_coords}]`);
              if (cnt > 1) mParts.push(`x${cnt}`);
              mParts.push(String(n.status || "unread").toUpperCase());
              mParts.push(formatNotifAge(n.updated_ts));

              return (
                <div
                  key={n.id}
                  className={`notif-item-row${n.status === "unread" ? " is-unread" : ""}`}
                  data-notification-id={n.id}
                >
                  <div className="notif-item-head">
                    <div className="notif-item-title-wrap">
                      <span
                        className="notif-id-pill tabular"
                        data-notif-code={notifCode}
                        title="Notification ID — copy and share to troubleshoot"
                      >
                        {notifCode}
                      </span>
                      <span
                        className={`notif-sev-pill tabular ${sev === "error" ? "sev-error" : "sev-warning"}`}
                      >
                        {sev.toUpperCase()}
                      </span>
                      <span className="notif-item-title">
                        {String(n.title || "Notification")}
                      </span>
                    </div>
                    <span className="notif-item-meta tabular">{mParts.join(" · ")}</span>
                  </div>
                  {n.message ? (
                    <p className="notif-item-msg tabular">{String(n.message)}</p>
                  ) : null}
                  {n.help_prompt ? (
                    <p className="notif-item-help">{String(n.help_prompt)}</p>
                  ) : null}
                  <div className="notif-item-actions">
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      data-copy-notif-id={notifCode}
                      onClick={() => {
                        void copyNotificationCode(notifCode, showFeedback);
                      }}
                    >
                      Copy ID ({notifCode})
                    </button>
                    {n.status === "unread" ? (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => onAckOne(n.id, "read")}
                      >
                        Mark Read
                      </button>
                    ) : null}
                    {n.status !== "dismissed" ? (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => onAckOne(n.id, "dismissed")}
                      >
                        Dismiss
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};

export default NotificationCenterModal;
