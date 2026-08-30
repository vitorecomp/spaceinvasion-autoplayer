import React from "react";
import type { SortDirection } from "../hooks/useTableSort";

export interface SortableThProps<Key extends string = string> {
  label: React.ReactNode;
  sortKey: Key;
  activeKey: Key | null;
  direction: SortDirection | null;
  onSort: (key: Key) => void;
  align?: "left" | "right";
  className?: string;
  title?: string;
}

export function SortableTh<Key extends string = string>({
  label,
  sortKey,
  activeKey,
  direction,
  onSort,
  align = "left",
  className,
  title,
}: SortableThProps<Key>): React.ReactElement {
  const isActive = activeKey === sortKey && direction !== null;
  const ariaSort: "ascending" | "descending" | "none" = isActive
    ? direction === "asc"
      ? "ascending"
      : "descending"
    : "none";

  const thClasses = [
    "th-sortable",
    align === "right" ? "col-right" : "",
    isActive ? "is-sorted" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  const labelText = typeof label === "string" ? label : "column";
  const computedTitle =
    title ??
    (isActive
      ? direction === "asc"
        ? `Sorted by ${labelText} (ascending) — click to toggle`
        : `Sorted by ${labelText} (descending) — click to toggle`
      : `Sort by ${labelText}`);

  return (
    <th scope="col" className={thClasses} aria-sort={ariaSort}>
      <button
        type="button"
        className="th-sort-btn"
        onClick={() => onSort(sortKey)}
        title={computedTitle}
      >
        <span className="th-sort-label">{label}</span>
        <span className="th-sort-icon" aria-hidden="true">
          <span
            className={`th-sort-arrow th-sort-up${isActive && direction === "asc" ? " is-active" : ""}`}
          />
          <span
            className={`th-sort-arrow th-sort-down${isActive && direction === "desc" ? " is-active" : ""}`}
          />
        </span>
      </button>
    </th>
  );
}

export default SortableTh;
