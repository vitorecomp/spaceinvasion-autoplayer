import React, { useMemo } from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { fmtShortDateTime } from "../../utils/formatters";

const EMAIL_CATS: Array<{ key: string; label: string }> = [
  { key: "all", label: "All Non-Spy/Battle" },
  { key: "general", label: "User / Alliance / Admin" },
  { key: "fleet_return", label: "Fleet Return" },
  { key: "fleet_transport", label: "Transport" },
  { key: "colonize_report", label: "Colonization" },
  { key: "counter_spy", label: "Counter-Spy" },
  { key: "recycle_report", label: "Recycle Raw" },
];

export interface RecyclersEmailsPaneProps {
  filteredEmails: Array<Record<string, any>>;
  totalEmailsCount: number;
  emailCat: string;
  onSelectEmailCat: (cat: string) => void;
  recTab: "harvests" | "debris" | "emails";
  recSearch: string;
  hidden: boolean;
}

export const RecyclersEmailsPane: React.FC<RecyclersEmailsPaneProps> = ({
  filteredEmails,
  totalEmailsCount,
  emailCat,
  onSelectEmailCat,
  recTab,
  recSearch,
  hidden,
}) => {
  const emailSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "time" | "folderType" | "sender" | "subject" | "coords" | "body"
    >
  >(
    () => ({
      time: {
        accessor: (m) => timestampSortKey(m.ts ?? m.ingested_ts, m.msg_date_text),
        defaultDirection: "desc",
      },
      folderType: (m) => `${m.folder_type || "inbox"} · ${m.category || "general"}`,
      sender: (m) => String(m.sender || "System"),
      subject: (m) => String(m.subject || "").trim() || null,
      coords: (m) => coordsSortKey(m.coords),
      body: (m) => String(m.body_text || "").trim() || null,
    }),
    []
  );

  const emailSort = useTableSort(filteredEmails, emailSortCols);

  const emailPagination = useTablePagination(
    emailSort.sortedRows,
    `${recTab}|${emailCat}|${recSearch}|${emailSort.sortKey ?? ""}|${emailSort.sortDirection ?? ""}`,
    25
  );

  return (
    <div className="intel-pane" id="rec-pane-emails" hidden={hidden}>
      <div className="email-filter-bar">
        <div className="filter-chips" role="group" aria-label="Email category filters">
          {EMAIL_CATS.map((c) => (
            <button
              key={c.key}
              type="button"
              className={`chip-btn${emailCat === c.key ? " active" : ""}`}
              data-email-cat={c.key}
              onClick={() => onSelectEmailCat(c.key)}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>
      <div className="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Time"
                sortKey="time"
                activeKey={emailSort.sortKey}
                direction={emailSort.sortDirection}
                onSort={emailSort.requestSort}
              />
              <SortableTh
                label="Folder / Type"
                sortKey="folderType"
                activeKey={emailSort.sortKey}
                direction={emailSort.sortDirection}
                onSort={emailSort.requestSort}
              />
              <SortableTh
                label="Sender"
                sortKey="sender"
                activeKey={emailSort.sortKey}
                direction={emailSort.sortDirection}
                onSort={emailSort.requestSort}
              />
              <SortableTh
                label="Subject"
                sortKey="subject"
                activeKey={emailSort.sortKey}
                direction={emailSort.sortDirection}
                onSort={emailSort.requestSort}
              />
              <SortableTh
                label="Coords"
                sortKey="coords"
                activeKey={emailSort.sortKey}
                direction={emailSort.sortDirection}
                onSort={emailSort.requestSort}
              />
              <SortableTh
                label="Message Body"
                sortKey="body"
                activeKey={emailSort.sortKey}
                direction={emailSort.sortDirection}
                onSort={emailSort.requestSort}
              />
            </tr>
          </thead>
          <tbody id="rec-emails-tbody">
            {emailPagination.pagedRows.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">
                  {totalEmailsCount > 0
                    ? "No emails or fleet notices match the active search query."
                    : "No other emails or fleet notices archived yet."}
                </td>
              </tr>
            ) : (
              emailPagination.pagedRows.map((m, idx) => {
                const rawTxt = String(m.body_text || "").trim();
                const bodyTxt =
                  rawTxt.length > 260 ? `${rawTxt.slice(0, 260)}...` : rawTxt || "—";
                return (
                  <tr key={m.msg_uid ?? m.id ?? idx}>
                    <td className="tabular">
                      {fmtShortDateTime(m.ts ?? m.ingested_ts, m.msg_date_text)}
                    </td>
                    <td className="tabular">
                      {`${m.folder_type || "inbox"} · ${m.category || "general"}`}
                    </td>
                    <td>{String(m.sender || "System")}</td>
                    <td>{String(m.subject || "—")}</td>
                    <td className="tabular">{String(m.coords || "—")}</td>
                    <td className="msg-body-cell">{bodyTxt}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        idPrefix="rec-emails"
        totalItems={emailPagination.totalItems}
        page={emailPagination.page}
        pageSize={emailPagination.pageSize}
        itemLabel="emails"
        onPageChange={emailPagination.setPage}
        onPageSizeChange={emailPagination.setPageSize}
      />
    </div>
  );
};
