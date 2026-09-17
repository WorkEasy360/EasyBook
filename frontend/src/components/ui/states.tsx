import type * as React from "react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

/**
 * The four states every data surface must have (spec §107): loading, empty,
 * error, and forbidden. They live together so no screen can ship with one of
 * them quietly missing.
 */

export function Skeleton({ className, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded bg-ink-200/70", className)}
      // A skeleton is a placeholder for content that is not there yet.
      // Exposing it would make a screen reader announce meaningless boxes;
      // the live region on the surrounding surface does the announcing.
      aria-hidden="true"
      {...rest}
    />
  );
}

/** Table-shaped skeleton, sized to the real column count to avoid a reflow. */
export function TableSkeleton({ rows = 8, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="divide-y divide-ink-200" aria-hidden="true">
      {Array.from({ length: rows }, (_, rowIndex) => (
        <div key={rowIndex} className="flex items-center gap-4 px-4 py-3">
          {Array.from({ length: columns }, (_, columnIndex) => (
            <Skeleton
              key={columnIndex}
              className={cn("h-4", columnIndex === 0 ? "w-[22%]" : "flex-1")}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export interface EmptyStateProps {
  title: string;
  description?: string;
  /** The obvious next step. An empty list with no way forward is a dead end. */
  action?: { label: string; onClick?: () => void; href?: string };
  icon?: React.ReactNode;
  compact?: boolean;
}

export function EmptyState({ title, description, action, icon, compact = false }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "px-4 py-8" : "px-6 py-14",
      )}
    >
      {icon ? (
        <div aria-hidden="true" className="mb-3 text-ink-400">
          {icon}
        </div>
      ) : null}
      <p className="text-sm font-medium text-ink-900">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-sm text-ink-500">{description}</p> : null}
      {action ? (
        <div className="mt-4">
          {action.href ? (
            <a
              href={action.href}
              className="inline-flex h-9 items-center justify-center rounded-md border border-brand-700 bg-brand-700 px-3.5 text-sm font-medium text-white transition-colors hover:bg-brand-800"
            >
              {action.label}
            </a>
          ) : (
            <Button variant="primary" onClick={action.onClick}>
              {action.label}
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}

export interface ErrorStateProps {
  title?: string;
  message: string;
  /** Shown as "Reference: …" so support can find the request in the logs. */
  reference?: string | null;
  onRetry?: () => void;
  compact?: boolean;
}

export function ErrorState({
  title = "Something went wrong",
  message,
  reference,
  onRetry,
  compact = false,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "px-4 py-8" : "px-6 py-12",
      )}
    >
      <p className="text-sm font-medium text-ink-900">{title}</p>
      <p className="mt-1 max-w-md text-sm text-ink-600">{message}</p>
      {reference ? (
        <p className="mt-2 font-mono text-2xs text-ink-400">Reference: {reference}</p>
      ) : null}
      {onRetry ? (
        <div className="mt-4">
          <Button variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Shown where a panel would be if the role cannot see it. Distinct from an
 * error: nothing failed, and there is nothing for the user to retry.
 */
export function ForbiddenState({
  resource = "this information",
  compact = false,
}: {
  resource?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "px-4 py-8" : "px-6 py-12",
      )}
    >
      <p className="text-sm font-medium text-ink-900">You do not have access to {resource}</p>
      <p className="mt-1 max-w-sm text-sm text-ink-500">
        Ask an administrator in your organization if you need this.
      </p>
    </div>
  );
}
