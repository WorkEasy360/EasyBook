"use client";

import { appHref } from "@/lib/routes";
import * as React from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";
import { isActiveHref } from "@/lib/navigation";
import { usePathname } from "next/navigation";

/**
 * Detail-view building blocks: definition lists, tab strips, totals panels and
 * section headings. Shared so a customer, an invoice and a bill all present
 * their facts the same way.
 */

/** A label/value pair. Uses a real <dl> so the relationship is in the markup. */
export function DetailList({
  items,
  columns = 2,
  className,
}: {
  items: Array<{ label: string; value: React.ReactNode; span?: boolean }>;
  columns?: 1 | 2 | 3;
  className?: string;
}) {
  return (
    <dl
      className={cn(
        "grid gap-x-6 gap-y-3",
        columns === 1 && "grid-cols-1",
        columns === 2 && "grid-cols-1 sm:grid-cols-2",
        columns === 3 && "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
        className,
      )}
    >
      {items.map((item) => (
        <div key={item.label} className={cn("min-w-0", item.span && "sm:col-span-full")}>
          <dt className="text-2xs font-medium tracking-wide text-ink-500 uppercase">{item.label}</dt>
          <dd className="mt-0.5 text-sm text-ink-900">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Totals panel for a document. Money is right-aligned with tabular figures so
 * subtotal, tax and grand total line up as a column.
 */
export function TotalsPanel({
  rows,
  className,
}: {
  rows: Array<{ label: string; value: React.ReactNode; emphasis?: boolean; muted?: boolean }>;
  className?: string;
}) {
  return (
    <dl className={cn("flex w-full max-w-xs flex-col gap-1.5", className)}>
      {rows.map((row) => (
        <div
          key={row.label}
          className={cn(
            "flex items-baseline justify-between gap-4",
            row.emphasis && "mt-1.5 border-t border-ink-300 pt-2.5",
          )}
        >
          <dt
            className={cn(
              "text-sm",
              row.emphasis ? "font-semibold text-ink-900" : "text-ink-600",
              row.muted && "text-ink-500",
            )}
          >
            {row.label}
          </dt>
          <dd className={cn("text-sm", row.emphasis && "text-base font-semibold")}>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Tab strip for a detail page's sub-views (Overview, Invoices, Payments…).
 *
 * Tabs are LINKS, not buttons: each sub-view is a distinct URL, so it is
 * bookmarkable, shareable and survives a refresh (spec §66).
 */
export function DetailTabs({
  tabs,
  className,
}: {
  tabs: Array<{ label: string; href: string; count?: number }>;
  className?: string;
}) {
  const pathname = usePathname();

  return (
    <div className={cn("border-b border-ink-200 bg-white px-4 sm:px-6", className)} data-print="hide">
      <nav aria-label="Sections" className="-mb-px flex gap-1 overflow-x-auto">
        {tabs.map((tab) => {
          // Exact match for the base tab, prefix for the rest, so "Overview"
          // does not stay lit while a sibling tab is open.
          const active =
            tabs.filter((other) => isActiveHref(pathname, other.href)).slice(-1)[0]?.href === tab.href;
          return (
            <Link
              key={tab.href}
              href={appHref(tab.href)}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm transition-colors",
                active
                  ? "border-brand-700 font-medium text-brand-800"
                  : "border-transparent text-ink-600 hover:border-ink-300 hover:text-ink-900",
              )}
            >
              {tab.label}
              {tab.count !== undefined ? (
                <span
                  className={cn(
                    "rounded-full px-1.5 py-0.5 text-2xs",
                    active ? "bg-brand-100 text-brand-800" : "bg-ink-100 text-ink-600",
                  )}
                >
                  {tab.count}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

/** Section heading inside a detail body. */
export function Section({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("flex flex-col gap-3", className)}>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
          {description ? <p className="mt-0.5 text-xs text-ink-500">{description}</p> : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** Standard page body padding, so every route lines up. */
export function PageBody({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("flex flex-col gap-5 px-4 py-5 sm:px-6", className)}>{children}</div>;
}
