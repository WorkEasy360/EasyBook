"use client";

import { cn } from "@/lib/cn";
import { formatMoney, formatQuantity, isNegative, moneyAriaLabel } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";
import { useFormatSettings } from "@/components/providers/org-provider";

/**
 * The one component that renders an amount (spec §70). Feature code must not
 * call Intl or build a currency string itself.
 *
 * Amounts arrive from the API as decimal strings and stay strings all the way
 * here — see src/lib/money.ts for why they are never parsed into a number.
 */

export interface MoneyProps {
  value: DecimalString | null | undefined;
  /**
   * Accounting presentation — negatives in parentheses (spec §71). On for
   * statements and ledgers; off for inputs and running totals, where a minus
   * sign reads more directly.
   */
  accounting?: boolean;
  /** Override the organization currency, for a foreign-currency document. */
  currency?: string;
  /** Column already carries the symbol in its header. */
  hideSymbol?: boolean;
  /** Emphasis for a total row. */
  strong?: boolean;
  /** Tints negatives red. Always paired with the sign or parentheses. */
  colorNegative?: boolean;
  className?: string;
}

export function Money({
  value,
  accounting = false,
  currency,
  hideSymbol = false,
  strong = false,
  colorNegative = true,
  className,
}: MoneyProps) {
  const settings = useFormatSettings();
  const resolvedCurrency = currency ?? settings.currency;

  if (value === null || value === undefined || value === "") {
    return (
      <span className={cn("tabular text-ink-400", className)} aria-label="not available">
        —
      </span>
    );
  }

  const negative = isNegative(value);
  const text = formatMoney(value, {
    currency: resolvedCurrency,
    locale: settings.locale,
    accounting,
    hideSymbol,
  });

  return (
    <span
      className={cn(
        "tabular whitespace-nowrap",
        strong && "font-semibold",
        negative && colorNegative && "text-danger-600",
        className,
      )}
      // Parentheses do not read as "negative" aloud, and colour carries
      // nothing at all to a screen reader (spec §68).
      aria-label={moneyAriaLabel(value, { currency: resolvedCurrency, locale: settings.locale })}
    >
      {text}
    </span>
  );
}

export function Quantity({
  value,
  unit,
  className,
}: {
  value: DecimalString | null | undefined;
  unit?: string | null;
  className?: string;
}) {
  const settings = useFormatSettings();

  if (value === null || value === undefined || value === "") {
    return <span className={cn("tabular text-ink-400", className)}>—</span>;
  }

  return (
    <span className={cn("tabular whitespace-nowrap", className)}>
      {formatQuantity(value, { locale: settings.locale })}
      {unit ? <span className="ml-1 text-ink-500">{unit}</span> : null}
    </span>
  );
}
