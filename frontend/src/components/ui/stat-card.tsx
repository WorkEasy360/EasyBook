"use client";

import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";
import { Skeleton } from "./states";

/**
 * KPI tile for the dashboard and module summaries.
 *
 * The value is always supplied as a rendered node — usually <Money> — so the
 * tile never formats an amount itself and cannot bypass the money rules
 * (spec §70).
 */

export interface StatCardProps {
  label: string;
  value: React.ReactNode;
  /** Secondary line: a comparison, a count, a period. */
  hint?: React.ReactNode;
  /** Makes the whole tile a link to the underlying list or report. */
  href?: string;
  tone?: "default" | "positive" | "negative" | "warning";
  isLoading?: boolean;
}

const TONES = {
  default: "text-ink-900",
  positive: "text-success-700",
  negative: "text-danger-600",
  warning: "text-warning-700",
} as const;

export function StatCard({
  label,
  value,
  hint,
  href,
  tone = "default",
  isLoading = false,
}: StatCardProps) {
  const body = (
    <>
      <p className="text-2xs font-medium tracking-wide text-ink-500 uppercase">{label}</p>
      {isLoading ? (
        <Skeleton className="mt-2 h-7 w-28" />
      ) : (
        <p className={cn("tabular mt-1.5 text-xl font-semibold", TONES[tone])}>{value}</p>
      )}
      {hint && !isLoading ? <p className="mt-1 text-xs text-ink-500">{hint}</p> : null}
    </>
  );

  const className = cn(
    "block rounded-lg border border-ink-200 bg-white px-4 py-3.5 shadow-xs",
    href && "transition-colors hover:border-brand-300 hover:bg-brand-50/30",
  );

  if (href && !isLoading) {
    return (
      <Link href={appHref(href)} className={className}>
        {body}
      </Link>
    );
  }

  return <div className={className}>{body}</div>;
}

/** Responsive grid for a row of tiles. */
export function StatGrid({
  children,
  columns = 4,
}: {
  children: React.ReactNode;
  columns?: 2 | 3 | 4 | 6;
}) {
  return (
    <div
      className={cn(
        "grid gap-3",
        columns === 2 && "grid-cols-1 sm:grid-cols-2",
        columns === 3 && "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
        columns === 4 && "grid-cols-2 lg:grid-cols-4",
        columns === 6 && "grid-cols-2 md:grid-cols-3 lg:grid-cols-6",
      )}
    >
      {children}
    </div>
  );
}
