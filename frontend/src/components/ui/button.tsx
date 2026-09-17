import * as React from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "./spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "link";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-brand-700 text-white border border-brand-700 hover:bg-brand-800 hover:border-brand-800 active:bg-brand-900",
  secondary:
    "bg-white text-ink-800 border border-ink-300 hover:bg-ink-50 active:bg-ink-100",
  ghost:
    "bg-transparent text-ink-700 border border-transparent hover:bg-ink-100 active:bg-ink-200",
  danger:
    "bg-danger-600 text-white border border-danger-600 hover:bg-danger-700 hover:border-danger-700",
  link: "bg-transparent text-brand-700 border border-transparent underline underline-offset-2 hover:text-brand-800 px-0",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  lg: "h-10 px-4 text-sm gap-2",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /**
   * Shows a spinner AND disables the button. Every mutation button must pass
   * this while its request is in flight (spec §16) — it is the click-guard
   * that stops a double-post, working with the idempotency key rather than
   * instead of it.
   */
  loading?: boolean;
  /** Announced by a screen reader while `loading` is true. */
  loadingLabel?: string;
  leadingIcon?: React.ReactNode;
  trailingIcon?: React.ReactNode;
  fullWidth?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    loadingLabel = "Working…",
    leadingIcon,
    trailingIcon,
    fullWidth = false,
    className,
    children,
    disabled,
    type = "button",
    ...rest
  },
  ref,
) {
  const isDisabled = disabled || loading;

  return (
    <button
      ref={ref}
      // Defaults to "button": an unlabelled <button> inside a <form> submits
      // it, which has caused a posted invoice from a "Add line" click more
      // than once in this category of app.
      type={type}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center justify-center rounded-md font-medium whitespace-nowrap",
        "transition-colors duration-100",
        "disabled:cursor-not-allowed disabled:opacity-55",
        VARIANTS[variant],
        SIZES[size],
        fullWidth && "w-full",
        className,
      )}
      {...rest}
    >
      {loading ? (
        <>
          <Spinner className="size-3.5" />
          <span>{children}</span>
          {/* Politely announced without disturbing the visible label. */}
          <span className="sr-only" role="status">
            {loadingLabel}
          </span>
        </>
      ) : (
        <>
          {leadingIcon ? (
            <span aria-hidden="true" className="shrink-0">
              {leadingIcon}
            </span>
          ) : null}
          <span>{children}</span>
          {trailingIcon ? (
            <span aria-hidden="true" className="shrink-0">
              {trailingIcon}
            </span>
          ) : null}
        </>
      )}
    </button>
  );
});

export interface IconButtonProps extends Omit<ButtonProps, "leadingIcon" | "trailingIcon" | "fullWidth"> {
  /** Required: an icon-only control is unusable without an accessible name. */
  label: string;
  icon: React.ReactNode;
}

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, size = "md", variant = "ghost", className, ...rest },
  ref,
) {
  return (
    <Button
      ref={ref}
      variant={variant}
      size={size}
      aria-label={label}
      title={label}
      className={cn(
        "px-0",
        size === "sm" && "size-8",
        size === "md" && "size-9",
        size === "lg" && "size-10",
        className,
      )}
      {...rest}
    >
      <span aria-hidden="true" className="inline-flex">
        {icon}
      </span>
    </Button>
  );
});
