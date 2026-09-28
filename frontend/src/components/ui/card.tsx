import type * as React from "react";
import { cn } from "@/lib/cn";

/**
 * Panel container. Separation comes from a border first and a very low shadow
 * second (spec §10) — stacked heavy cards turn a dense financial screen into
 * a scrapbook.
 */
export function Card({ className, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-lg border border-ink-200 bg-white shadow-xs", className)}
      {...rest}
    />
  );
}

// Omit the native `title` (a string tooltip) so the prop can be a node —
// a card heading often carries a badge alongside its text.
export interface CardHeaderProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  /**
   * Heading level. Defaults to h2 — a card inside a page whose <h1> is the
   * page title. Pass h3 when nested deeper so the outline stays correct
   * (spec §68: correct heading hierarchy).
   */
  as?: "h2" | "h3" | "h4";
}

export function CardHeader({
  title,
  description,
  actions,
  as: Heading = "h2",
  className,
  ...rest
}: CardHeaderProps) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-start justify-between gap-3 border-b border-ink-200 px-4 py-3",
        className,
      )}
      {...rest}
    >
      <div className="min-w-0">
        <Heading className="truncate text-sm font-semibold text-ink-900">{title}</Heading>
        {description ? <p className="mt-0.5 text-xs text-ink-500">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function CardBody({ className, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-4 py-4", className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-end gap-2 border-t border-ink-200 bg-ink-50 px-4 py-3",
        className,
      )}
      {...rest}
    />
  );
}
