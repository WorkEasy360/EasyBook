import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";
import { EmptyState, ErrorState } from "./states";
import { Pagination } from "./pagination";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Paginated } from "@/lib/api/types";

/**
 * The application's one table (spec §18).
 *
 * A SERVER Component. It has no callbacks and no state: paging is a <Link>,
 * and filtering happens in the small client control above it. That keeps the
 * rows — which are the bulk of any list payload — out of the client bundle
 * entirely (spec §75, §77), and means a 200-row page ships no JavaScript to
 * render.
 *
 * Sorting is deliberately absent rather than inert. No list endpoint supports
 * `?ordering=` (see src/lib/api/capabilities.ts), so a clickable column header
 * would be a lie. When the backend gains OrderingFilter, `sortHref` turns it
 * on without touching the callers.
 *
 * Accessibility is structural: a real <caption>, <th scope>, and aria-sort
 * only where sorting genuinely exists.
 */

export interface Column<T> {
  /** Stable id; also the backend ordering field once sorting exists. */
  key: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  /** Right-aligns and applies tabular figures. Use for every amount. */
  numeric?: boolean;
  /** Hidden below this breakpoint — for secondary columns (spec §67). */
  hideBelow?: "sm" | "md" | "lg";
  width?: string;
  /** Accessible header text when `header` is an icon or abbreviation. */
  headerLabel?: string;
}

export interface DataTableProps<T> {
  /** Describes the table for screen readers. Required — never decorative. */
  caption: string;
  columns: Column<T>[];
  data: Paginated<T> | undefined;
  error?: ApiError | Error | null;
  getRowId: (row: T) => string;
  /** Makes the first cell a link to the record. */
  getRowHref?: (row: T) => string;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: { label: string; href?: string };
  page: number;
  pageSize: number;
  /** Builds the href for a page number, preserving the current filters. */
  buildPageHref: (page: number) => string;
  /** Rendered beneath the last row, e.g. a totals line. */
  footer?: React.ReactNode;
  /** Note under the table, e.g. the fixed server-side sort order. */
  note?: React.ReactNode;
}

export function DataTable<T>({
  caption,
  columns,
  data,
  error,
  getRowId,
  getRowHref,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  emptyAction,
  page,
  pageSize,
  buildPageHref,
  footer,
  note,
}: DataTableProps<T>) {
  if (error) {
    const forbidden = error instanceof ApiError && error.isForbidden;
    return (
      <div className="rounded-lg border border-ink-200 bg-white">
        <ErrorState
          title={forbidden ? "You do not have access to this list" : "Could not load this list"}
          message={error.message}
          reference={referenceOf(error)}
        />
      </div>
    );
  }

  const rows = data?.results ?? [];
  const count = data?.count ?? 0;

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-ink-200 bg-white">
        <EmptyState
          title={emptyTitle}
          {...(emptyDescription ? { description: emptyDescription } : {})}
          {...(emptyAction ? { action: emptyAction } : {})}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto rounded-lg border border-ink-200 bg-white">
        <table className="w-full min-w-max border-collapse text-sm">
          <caption className="sr-only">
            {caption}. {count} {count === 1 ? "result" : "results"}.
          </caption>
          <thead>
            <tr className="border-b border-ink-200 bg-ink-50">
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  style={column.width ? { width: column.width } : undefined}
                  className={cn(
                    "px-3 py-2 text-2xs font-semibold tracking-wide text-ink-600 uppercase",
                    column.numeric ? "text-right" : "text-left",
                    hideClass(column.hideBelow),
                  )}
                >
                  {/*
                    A TEXT header (an abbreviation like "Qty") is swapped for
                    its spoken label. A node header is left exposed: it may be
                    interactive — a select-all checkbox — and hiding it would
                    remove a working control from assistive tech.
                  */}
                  <span
                    aria-hidden={
                      column.headerLabel && (typeof column.header === "string" || typeof column.header === "number")
                        ? "true"
                        : undefined
                    }
                  >
                    {column.header}
                  </span>
                  {column.headerLabel ? <span className="sr-only">{column.headerLabel}</span> : null}
                </th>
              ))}
            </tr>
          </thead>

          <tbody className="divide-y divide-ink-100">
            {rows.map((row) => {
              const href = getRowHref?.(row);
              return (
                <tr
                  key={getRowId(row)}
                  className={cn(href && "hover:bg-brand-50/40 focus-within:bg-brand-50/40")}
                >
                  {columns.map((column, index) => (
                    <td
                      key={column.key}
                      className={cn(
                        "px-3 py-2.5 align-middle",
                        column.numeric && "numeric text-right",
                        hideClass(column.hideBelow),
                      )}
                    >
                      {/*
                        The link wraps the FIRST cell only. A <tr> cannot
                        contain an <a>, and making every cell a link would put
                        one row on the tab ring a dozen times.
                      */}
                      {href && index === 0 ? (
                        <Link
                          href={appHref(href)}
                          className="font-medium text-brand-700 hover:underline focus-visible:underline"
                        >
                          {column.cell(row)}
                        </Link>
                      ) : (
                        column.cell(row)
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>

          {footer ? (
            <tfoot className="border-t-2 border-ink-300 bg-ink-50 font-medium">{footer}</tfoot>
          ) : null}
        </table>
      </div>

      {note ? <p className="text-xs text-ink-500">{note}</p> : null}

      <Pagination page={page} pageSize={pageSize} count={count} buildHref={buildPageHref} />
    </div>
  );
}

function hideClass(breakpoint: Column<unknown>["hideBelow"]): string | undefined {
  switch (breakpoint) {
    case "sm":
      return "hidden sm:table-cell";
    case "md":
      return "hidden md:table-cell";
    case "lg":
      return "hidden lg:table-cell";
    default:
      return undefined;
  }
}

/** Totals row for a table footer. */
export function TableFooterRow({
  label,
  values,
  columnCount,
}: {
  label: string;
  values: Array<{ key: string; node: React.ReactNode }>;
  columnCount: number;
}) {
  const span = Math.max(1, columnCount - values.length);
  return (
    <tr>
      <td colSpan={span} className="px-3 py-2.5 text-right text-2xs text-ink-600 uppercase">
        {label}
      </td>
      {values.map((value) => (
        <td key={value.key} className="numeric px-3 py-2.5 text-right">
          {value.node}
        </td>
      ))}
    </tr>
  );
}
