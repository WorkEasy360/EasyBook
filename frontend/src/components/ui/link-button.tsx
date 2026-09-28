import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";

/**
 * A navigation that looks like a button — "New invoice", "Edit".
 *
 * It is a real <a>, not a <button> with an onClick: it opens in a new tab,
 * shows its destination on hover, and works before hydration. Styles match
 * Button's variants so the two sit side by side in a header.
 */

const VARIANTS = {
  primary: "border-brand-700 bg-brand-700 text-white hover:bg-brand-800",
  secondary: "border-ink-300 bg-white text-ink-800 hover:bg-ink-50",
  ghost: "border-transparent bg-transparent text-ink-700 hover:bg-ink-100",
} as const;

export interface LinkButtonProps {
  href: string;
  variant?: keyof typeof VARIANTS;
  size?: "sm" | "md";
  className?: string;
  children: React.ReactNode;
  /** For a CSV export or other file the browser should download. */
  download?: boolean;
}

export function LinkButton({
  href,
  variant = "secondary",
  size = "md",
  className,
  children,
  download = false,
}: LinkButtonProps) {
  const classes = cn(
    "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium whitespace-nowrap transition-colors",
    size === "sm" ? "h-8 px-3 text-xs" : "h-9 px-3.5 text-sm",
    VARIANTS[variant],
    className,
  );

  if (download) {
    // A plain anchor: a download must not go through the client router,
    // which would try to render the CSV as a page.
    return (
      <a href={href} className={classes} download>
        {children}
      </a>
    );
  }

  return (
    <Link href={appHref(href)} className={classes}>
      {children}
    </Link>
  );
}
