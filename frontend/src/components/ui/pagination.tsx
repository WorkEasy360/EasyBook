import { appHref } from "@/lib/routes";
import Link from "next/link";
import { cn } from "@/lib/cn";
import { Icons } from "./icons";

/**
 * Server pagination, rendered as links.
 *
 * Links rather than buttons so the whole list stays a Server Component, and
 * so a page is bookmarkable, openable in a new tab and reachable by the back
 * button (spec §66). Page numbers rather than infinite scroll because an
 * accountant checking a ledger needs to return to "the page I was on".
 */

export interface PaginationProps {
  page: number;
  pageSize: number;
  count: number;
  /** Builds the href for a page, preserving the current filters. */
  buildHref: (page: number) => string;
}

export function Pagination({ page, pageSize, count, buildHref }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(count / pageSize));
  const first = count === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, count);

  if (totalPages <= 1) {
    return (
      <p className="text-xs text-ink-500">
        {count} {count === 1 ? "result" : "results"}
      </p>
    );
  }

  return (
    <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-ink-500">
        Showing <span className="font-medium text-ink-700">{first}</span>–
        <span className="font-medium text-ink-700">{last}</span> of{" "}
        <span className="font-medium text-ink-700">{count}</span>
      </p>

      <div className="flex items-center gap-1">
        <StepLink
          href={buildHref(page - 1)}
          disabled={page <= 1}
          label="Previous page"
          icon={<Icons.chevronRight className="size-4 rotate-180" />}
        />

        {pageWindow(page, totalPages).map((entry, index) =>
          entry === "gap" ? (
            <span key={`gap-${index}`} aria-hidden="true" className="px-1 text-xs text-ink-400">
              …
            </span>
          ) : entry === page ? (
            <span
              key={entry}
              aria-current="page"
              aria-label={`Page ${entry}, current page`}
              className="flex h-8 min-w-8 items-center justify-center rounded-md bg-brand-700 px-2 text-xs font-medium text-white"
            >
              {entry}
            </span>
          ) : (
            <Link
              key={entry}
              href={appHref(buildHref(entry))}
              aria-label={`Page ${entry}`}
              className="flex h-8 min-w-8 items-center justify-center rounded-md border border-ink-200 bg-white px-2 text-xs font-medium text-ink-700 transition-colors hover:bg-ink-50"
            >
              {entry}
            </Link>
          ),
        )}

        <StepLink
          href={buildHref(page + 1)}
          disabled={page >= totalPages}
          label="Next page"
          icon={<Icons.chevronRight className="size-4" />}
        />
      </div>
    </nav>
  );
}

function StepLink({
  href,
  disabled,
  label,
  icon,
}: {
  href: string;
  disabled: boolean;
  label: string;
  icon: React.ReactNode;
}) {
  const className = cn(
    "flex size-8 items-center justify-center rounded-md border border-ink-200 bg-white text-ink-600",
    disabled ? "cursor-not-allowed opacity-40" : "transition-colors hover:bg-ink-50",
  );

  if (disabled) {
    // A disabled control must not be a link: an <a> with no href is not
    // focusable, and one with an href would still navigate.
    return (
      <span aria-disabled="true" aria-label={`${label}, unavailable`} className={className}>
        {icon}
      </span>
    );
  }

  return (
    <Link href={appHref(href)} aria-label={label} className={className}>
      {icon}
    </Link>
  );
}

/**
 * First, last and a window around the current page — bounded so a 4,000-page
 * general ledger does not render 4,000 links.
 */
function pageWindow(page: number, totalPages: number): Array<number | "gap"> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const pages = new Set<number>([1, totalPages, page]);
  if (page - 1 > 1) pages.add(page - 1);
  if (page + 1 < totalPages) pages.add(page + 1);

  const sorted = [...pages].sort((a, b) => a - b);
  const output: Array<number | "gap"> = [];

  for (let index = 0; index < sorted.length; index += 1) {
    const current = sorted[index];
    const previous = sorted[index - 1];
    if (current === undefined) continue;
    if (previous !== undefined && current - previous > 1) output.push("gap");
    output.push(current);
  }

  return output;
}
