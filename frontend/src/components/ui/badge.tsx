import type * as React from "react";
import { cn } from "@/lib/cn";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger" | "brand";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-ink-100 text-ink-700 ring-ink-200",
  info: "bg-info-50 text-info-700 ring-info-100",
  success: "bg-success-50 text-success-700 ring-success-100",
  warning: "bg-warning-50 text-warning-700 ring-warning-100",
  danger: "bg-danger-50 text-danger-700 ring-danger-100",
  brand: "bg-brand-50 text-brand-800 ring-brand-100",
};

/**
 * Small dot shapes that differ per tone. Status must never be carried by
 * colour alone (spec §68) — the badge always shows its label as text, and the
 * marker shape gives a second, non-colour cue for the states that matter most.
 */
const MARKERS: Partial<Record<BadgeTone, string>> = {
  success: "●",
  warning: "◆",
  danger: "■",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  /** Renders the non-colour marker glyph. On for financial status badges. */
  marker?: boolean;
  size?: "sm" | "md";
}

export function Badge({
  tone = "neutral",
  marker = false,
  size = "md",
  className,
  children,
  ...rest
}: BadgeProps) {
  const glyph = marker ? MARKERS[tone] : undefined;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full font-medium ring-1 ring-inset whitespace-nowrap",
        size === "sm" ? "px-1.5 py-0.5 text-2xs" : "px-2 py-0.5 text-xs",
        TONES[tone],
        className,
      )}
      {...rest}
    >
      {glyph ? (
        <span aria-hidden="true" className="text-[0.5em] leading-none">
          {glyph}
        </span>
      ) : null}
      {children}
    </span>
  );
}
