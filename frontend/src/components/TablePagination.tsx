import React, { useCallback, useEffect, useMemo, useState } from "react";

export interface TablePaginationState<T> {
  page: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
  pagedRows: T[];
  setPage: (nextPage: number) => void;
  setPageSize: (nextSize: number) => void;
}

export function useTablePagination<T>(
  rows: readonly T[],
  resetKey = "",
  defaultPageSize = 25
): TablePaginationState<T> {
  const safeDefaultSize = Math.max(1, Math.round(defaultPageSize));
  const [state, setState] = useState<{ page: number; pageSize: number; resetKey: string }>(() => ({
    page: 1,
    pageSize: safeDefaultSize,
    resetKey,
  }));

  const pageSize = state.pageSize;
  const rawPage = state.resetKey === resetKey ? state.page : 1;
  const totalItems = rows.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const effectivePage = Math.max(1, Math.min(totalPages, rawPage));

  const pagedRows = useMemo(() => {
    const start = (effectivePage - 1) * pageSize;
    return rows.slice(start, start + pageSize);
  }, [rows, effectivePage, pageSize]);

  useEffect(() => {
    if (state.resetKey !== resetKey) {
      setState((prev) => ({ ...prev, page: 1, resetKey }));
    } else if (state.page > totalPages) {
      setState((prev) => ({ ...prev, page: totalPages }));
    }
  }, [resetKey, state.page, state.resetKey, totalPages]);

  const setPage = useCallback(
    (nextPage: number) => {
      if (Number.isFinite(nextPage)) {
        const clamped = Math.max(1, Math.round(nextPage));
        setState((prev) => ({ ...prev, page: clamped, resetKey }));
      }
    },
    [resetKey]
  );

  const setPageSize = useCallback(
    (nextSize: number) => {
      if (Number.isFinite(nextSize) && nextSize >= 1) {
        setState({ page: 1, pageSize: Math.round(nextSize), resetKey });
      }
    },
    [resetKey]
  );

  return {
    page: effectivePage,
    pageSize,
    totalItems,
    totalPages,
    pagedRows,
    setPage,
    setPageSize,
  };
}

export interface TablePaginationProps {
  idPrefix: string;
  totalItems: number;
  page: number;
  pageSize: number;
  pageSizeOptions?: readonly number[];
  itemLabel?: string;
  onPageChange: (nextPage: number) => void;
  onPageSizeChange: (nextSize: number) => void;
}

export const TablePagination: React.FC<TablePaginationProps> = ({
  idPrefix,
  totalItems,
  page,
  pageSize,
  pageSizeOptions = [25, 50, 100, 200],
  itemLabel = "items",
  onPageChange,
  onPageSizeChange,
}) => {
  const safeSize = Math.max(1, Math.round(pageSize));
  const totalPages = Math.max(1, Math.ceil(totalItems / safeSize));
  const safePage = Math.max(1, Math.min(totalPages, Math.round(page)));
  const startIdx = totalItems === 0 ? 0 : (safePage - 1) * safeSize + 1;
  const endIdx = totalItems === 0 ? 0 : Math.min(totalItems, safePage * safeSize);

  return (
    <div
      className="table-pagination-bar tabular"
      id={`${idPrefix}-pagination`}
      role="navigation"
      aria-label={`${itemLabel} pagination`}
    >
      <span className="table-page-summary" id={`${idPrefix}-page-summary`}>
        {totalItems === 0
          ? `0 ${itemLabel}`
          : `Showing ${startIdx}–${endIdx} of ${totalItems} ${itemLabel}`}
      </span>

      <div className="table-page-controls">
        <label className="table-page-size-label" htmlFor={`${idPrefix}-page-size`}>
          Rows
          <select
            id={`${idPrefix}-page-size`}
            className="table-page-size-select"
            value={String(safeSize)}
            onChange={(e) => {
              const parsed = Number(e.target.value);
              if (Number.isFinite(parsed) && parsed >= 1) {
                onPageSizeChange(Math.round(parsed));
              }
            }}
          >
            {pageSizeOptions.map((opt) => (
              <option key={opt} value={String(opt)}>
                {opt}
              </option>
            ))}
          </select>
        </label>

        <div className="table-page-buttons">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id={`${idPrefix}-page-first`}
            aria-label="First page"
            disabled={safePage <= 1}
            onClick={() => onPageChange(1)}
          >
            « First
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id={`${idPrefix}-page-prev`}
            aria-label="Previous page"
            disabled={safePage <= 1}
            onClick={() => onPageChange(Math.max(1, safePage - 1))}
          >
            ‹ Prev
          </button>
          <span className="table-page-indicator" id={`${idPrefix}-page-indicator`}>
            {`Page ${safePage} / ${totalPages}`}
          </span>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id={`${idPrefix}-page-next`}
            aria-label="Next page"
            disabled={safePage >= totalPages}
            onClick={() => onPageChange(Math.min(totalPages, safePage + 1))}
          >
            Next ›
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id={`${idPrefix}-page-last`}
            aria-label="Last page"
            disabled={safePage >= totalPages}
            onClick={() => onPageChange(totalPages)}
          >
            Last »
          </button>
        </div>
      </div>
    </div>
  );
};

export default TablePagination;
