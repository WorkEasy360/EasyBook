"use client";

import * as React from "react";
import { cn } from "@/lib/cn";
import { useFieldContext } from "./field";
import { useFormatSettings } from "@/components/providers/org-provider";
import { isValidDecimal } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";

/**
 * Amount and quantity entry.
 *
 * The value is a decimal STRING throughout — it is never held as a number,
 * because round-tripping "1234.50" through a float and back is exactly the
 * class of error the money rules exist to prevent (root CLAUDE.md rule 3).
 *
 * `inputMode="decimal"` rather than `type="number"`: a number input silently
 * discards text it cannot parse, fires scroll-wheel changes over a focused
 * field, and formats per locale in ways that break decimal strings.
 */

export interface NumericInputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type" | "prefix"> {
  value: DecimalString;
  onValueChange: (value: DecimalString) => void;
  /** Currency symbol shown inside the control. Defaults to the org currency. */
  currency?: string | false;
  /** Decimal places allowed. 2 for money, more for quantities. */
  scale?: number;
  /** Rejects a negative value outright. On for quantities and rates. */
  nonNegative?: boolean;
  suffix?: React.ReactNode;
}

export function NumericInput({
  value,
  onValueChange,
  currency,
  scale = 2,
  nonNegative = false,
  suffix,
  className,
  onBlur,
  ...rest
}: NumericInputProps) {
  const field = useFieldContext();
  const settings = useFormatSettings();

  const symbol =
    currency === false ? null : currencySymbol(currency ?? settings.currency, settings.locale);

  function handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const next = event.target.value;

    // Allow the intermediate states a person types through: "", "-", "12.",
    // ".5". Rejecting these mid-keystroke makes the field feel broken.
    if (next === "" || next === "-" || next === "." || next === "-.") {
      if (nonNegative && next.startsWith("-")) return;
      onValueChange(next);
      return;
    }

    const pattern = nonNegative
      ? new RegExp(`^\\d*(\\.\\d{0,${scale}})?$`)
      : new RegExp(`^-?\\d*(\\.\\d{0,${scale}})?$`);
    if (!pattern.test(next)) return;

    onValueChange(next);
  }

  function handleBlur(event: React.FocusEvent<HTMLInputElement>) {
    // Normalise on blur so what is stored is what the API will accept.
    // "12." -> "12.00", "" stays empty for the form to treat as missing.
    if (value !== "" && isValidDecimal(value)) {
      const normalized = Number.isNaN(Number(value)) ? value : toFixedString(value, scale);
      if (normalized !== value) onValueChange(normalized);
    }
    onBlur?.(event);
  }

  return (
    <div className="relative">
      {symbol ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2.5 text-sm text-ink-500"
        >
          {symbol}
        </span>
      ) : null}

      <input
        id={field?.id}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        value={value}
        onChange={handleChange}
        onBlur={handleBlur}
        aria-describedby={field?.describedBy}
        aria-invalid={field?.invalid || undefined}
        aria-required={field?.required || undefined}
        disabled={rest.disabled ?? field?.disabled}
        data-numeric="true"
        className={cn(
          "h-9 w-full rounded-md border bg-white px-2.5 text-right text-sm text-ink-900",
          "transition-colors placeholder:text-ink-400",
          "disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-500 read-only:bg-ink-50",
          field?.invalid
            ? "border-danger-500 focus:border-danger-600"
            : "border-ink-300 hover:border-ink-400 focus:border-brand-600",
          symbol && "pl-7",
          suffix && "pr-8",
          className,
        )}
        {...rest}
      />

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
}

/** Quantity variant: more decimal places, no currency symbol. */
export function QuantityInput(props: Omit<NumericInputProps, "currency" | "scale"> & { scale?: number }) {
  return <NumericInput {...props} currency={false} scale={props.scale ?? 4} nonNegative />;
}

/** Percentage variant, for a tax or discount rate. */
export function PercentInput(props: Omit<NumericInputProps, "currency" | "suffix">) {
  return <NumericInput {...props} currency={false} suffix="%" />;
}

/** Pads/truncates a decimal string without going through a float. */
function toFixedString(value: string, scale: number): string {
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole = "0", fraction = ""] = unsigned.split(".");
  const paddedWhole = whole === "" ? "0" : whole;
  if (scale === 0) return `${negative ? "-" : ""}${paddedWhole}`;
  const paddedFraction = fraction.padEnd(scale, "0").slice(0, scale);
  return `${negative ? "-" : ""}${paddedWhole}.${paddedFraction}`;
}

/** The symbol alone, e.g. "₹", derived from the same locale data as display. */
function currencySymbol(currency: string, locale: string): string {
  try {
    const parts = new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
    }).formatToParts(0);
    return parts.find((part) => part.type === "currency")?.value ?? currency;
  } catch {
    return currency;
  }
}
