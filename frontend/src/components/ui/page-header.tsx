import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";
import { Icons } from "./icons";

export interface Crumb {
  label: string;
  href?: string;
}

/**
 * Page title block. Every route uses this so the <h1>, the breadcrumb trail
 * and the primary actions sit in the same place on every screen — which is
 * both a usability and a heading-hierarchy requirement (spec §68).
 */
export interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  breadcrumbs?: Crumb[];
  actions?: React.ReactNode;
  /** Status chip rendered beside the title, e.g. an invoice's state. */
  meta?: React.ReactNode;
  className?: string;
}

export function PageHeader({
  title,
  description,
  breadcrumbs,
  actions,
  meta,
  className,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        "border-b border-ink-200 bg-white px-4 py-4 sm:px-6",
        className,
      )}
    >
      {breadcrumbs && breadcrumbs.length > 0 ? (
        <nav aria-label="Breadcrumb" className="mb-2">
          <ol className="flex flex-wrap items-center gap-1 text-xs text-ink-500">
            {breadcrumbs.map((crumb, index) => {
              const last = index === breadcrumbs.length - 1;
              return (
                <li key={`${crumb.label}-${index}`} className="flex items-center gap-1">
                  {crumb.href && !last ? (
                    <Link href={appHref(crumb.href)} className="hover:text-ink-800 hover:underline">
                      {crumb.label}
                    </Link>
                  ) : (
                    <span aria-current={last ? "page" : undefined} className={cn(last && "text-ink-700")}>
                      {crumb.label}
                    </span>
                  )}
                  {!last ? (
                    <Icons.chevronRight className="size-3 shrink-0 text-ink-300" />
                  ) : null}
                </li>
              );
            })}
          </ol>
        </nav>
      ) : null}

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-lg font-semibold tracking-tight text-ink-900">{title}</h1>
            {meta}
          </div>
          {description ? (
            <div className="mt-1 text-sm text-ink-500">{description}</div>
          ) : null}
        </div>

        {actions ? (
          <div data-print="hide" className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        ) : null}
      </div>
    </div>
  );
}
