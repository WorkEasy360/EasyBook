"use client";

import * as React from "react";
import { cn } from "@/lib/cn";

/**
 * Form field plumbing.
 *
 * FormField owns the ids and wires label / description / error to the control
 * via context, so a field cannot ship with an unlabelled input or an error
 * that a screen reader never announces (spec §19, §68). Controls read the
 * context rather than taking a dozen aria props from every call site.
 */

interface FieldContextValue {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  required: boolean;
  disabled: boolean;
}

const FieldContext = React.createContext<FieldContextValue | null>(null);

export function useFieldContext(): FieldContextValue | null {
  return React.useContext(FieldContext);
}

export interface FormFieldProps {
  label: React.ReactNode;
  /** Server or client validation message. Its presence sets aria-invalid. */
  error?: string | null;
  hint?: React.ReactNode;
  required?: boolean;
  disabled?: boolean;
  /** Hides the label visually but keeps it for assistive tech. */
  labelHidden?: boolean;
  className?: string;
  children: React.ReactNode;
}

export function FormField({
  label,
  error,
  hint,
  required = false,
  disabled = false,
  labelHidden = false,
  className,
  children,
}: FormFieldProps) {
  const id = React.useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  const context = React.useMemo<FieldContextValue>(
    () => ({ id, describedBy, invalid: Boolean(error), required, disabled }),
    [id, describedBy, error, required, disabled],
  );

  return (
    <FieldContext.Provider value={context}>
      <div className={cn("flex flex-col gap-1.5", className)}>
        <label
          htmlFor={id}
          className={cn(
            "text-xs font-medium text-ink-700",
            labelHidden && "sr-only",
            disabled && "text-ink-400",
          )}
        >
          {label}
          {required ? (
            <>
              <span aria-hidden="true" className="ml-0.5 text-danger-600">
                *
              </span>
              {/* "*" alone is not announced meaningfully by every reader. */}
              <span className="sr-only"> (required)</span>
            </>
          ) : null}
        </label>

        {children}

        {hint && !error ? (
          <p id={hintId} className="text-xs text-ink-500">
            {hint}
          </p>
        ) : null}

        {error ? (
          <p id={errorId} className="text-xs text-danger-600">
            {error}
          </p>
        ) : null}
      </div>
    </FieldContext.Provider>
  );
}

const CONTROL_BASE =
  "w-full rounded-md border bg-white text-sm text-ink-900 placeholder:text-ink-400 " +
  "transition-colors disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-500 " +
  "read-only:bg-ink-50";

function controlClasses(invalid: boolean, extra?: string): string {
  return cn(
    CONTROL_BASE,
    invalid
      ? "border-danger-500 focus:border-danger-600"
      : "border-ink-300 hover:border-ink-400 focus:border-brand-600",
    extra,
  );
}

// Omit the native `prefix`/`suffix` string attributes (an obscure RDFa
// holdover) so these can carry a node — a currency symbol or a "%".
export interface InputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "prefix"> {
  /** Right-aligned with tabular figures — for amounts, rates and quantities. */
  numeric?: boolean;
  /** Fixed text inside the control, e.g. a currency symbol or a "%" suffix. */
  prefix?: React.ReactNode;
  suffix?: React.ReactNode;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
  { numeric = false, prefix, suffix, className, ...rest },
  ref,
) {
  const field = useFieldContext();

  const control = (
    <input
      ref={ref}
      id={field?.id}
      aria-describedby={field?.describedBy}
      aria-invalid={field?.invalid || undefined}
      aria-required={field?.required || undefined}
      disabled={rest.disabled ?? field?.disabled}
      data-numeric={numeric || undefined}
      className={cn(
        controlClasses(Boolean(field?.invalid)),
        "h-9 px-2.5",
        numeric && "text-right",
        Boolean(prefix) && "pl-7",
        Boolean(suffix) && "pr-8",
        className,
      )}
      {...rest}
    />
  );

  if (!prefix && !suffix) return control;

  return (
    <div className="relative">
      {prefix ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2.5 text-sm text-ink-500"
        >
          {prefix}
        </span>
      ) : null}
      {control}
      {suffix ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2.5 text-sm text-ink-500"
        >
          {suffix}
        </span>
      ) : null}
    </div>
  );
});

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, rows = 3, ...rest }, ref) {
  const field = useFieldContext();
  return (
    <textarea
      ref={ref}
      id={field?.id}
      rows={rows}
      aria-describedby={field?.describedBy}
      aria-invalid={field?.invalid || undefined}
      aria-required={field?.required || undefined}
      disabled={rest.disabled ?? field?.disabled}
      className={cn(controlClasses(Boolean(field?.invalid)), "px-2.5 py-2", className)}
      {...rest}
    />
  );
});

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  /** Rendered as a disabled first option, so the control has no silent default. */
  placeholder?: string;
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { placeholder, className, children, ...rest },
  ref,
) {
  const field = useFieldContext();
  return (
    <select
      ref={ref}
      id={field?.id}
      aria-describedby={field?.describedBy}
      aria-invalid={field?.invalid || undefined}
      aria-required={field?.required || undefined}
      disabled={rest.disabled ?? field?.disabled}
      className={cn(controlClasses(Boolean(field?.invalid)), "h-9 px-2 pr-8", className)}
      {...rest}
    >
      {placeholder ? (
        <option value="" disabled>
          {placeholder}
        </option>
      ) : null}
      {children}
    </select>
  );
});

export interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: React.ReactNode;
  hint?: React.ReactNode;
}

export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, hint, className, ...rest },
  ref,
) {
  const generatedId = React.useId();
  const id = rest.id ?? generatedId;
  const hintId = hint ? `${id}-hint` : undefined;

  return (
    <div className={cn("flex items-start gap-2", className)}>
      <input
        ref={ref}
        id={id}
        type="checkbox"
        aria-describedby={hintId}
        className="mt-0.5 size-4 shrink-0 rounded border-ink-300 text-brand-700 accent-brand-700 disabled:cursor-not-allowed"
        {...rest}
      />
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm text-ink-800">
          {label}
        </label>
        {hint ? (
          <p id={hintId} className="text-xs text-ink-500">
            {hint}
          </p>
        ) : null}
      </div>
    </div>
  );
});

/**
 * Form-level error summary. Shown above the actions so a keyboard user does
 * not have to hunt for which field failed; `role="alert"` announces it when
 * the server rejects a submission.
 */
export function FormError({ message, reference }: { message: string | null; reference?: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700"
    >
      <p>{message}</p>
      {reference ? (
        <p className="mt-1 font-mono text-2xs text-danger-600/80">Reference: {reference}</p>
      ) : null}
    </div>
  );
}
