import Big from "big.js";
import type { DecimalString } from "./api/types";

/**
 * Money helpers. Root CLAUDE.md rule 3: never float for money.
 *
 * Two rules hold everywhere in this app:
 *  1. Amounts stay decimal STRINGS from the API to the screen. They are never
 *     parsed into a JS number, not even for display — Intl.NumberFormat.format
 *     accepts a string and formats it at full precision (ECMA-402 "Intl.
 *     NumberFormat v3"), so there is no reason to go through a float.
 *  2. Any arithmetic the UI does is an ESTIMATE shown before the server
 *     answers (spec §8). The posted figures always come back from the
 *     deterministic accounting engine and overwrite whatever was estimated.
 */

/** Big is configured to throw rather than silently round away precision. */
Big.DP = 10;
Big.RM = Big.roundHalfUp;
Big.strict = false;

export type Money = Big;

export function money(value: DecimalString | number | Big | null | undefined): Money {
  if (value === null || value === undefined || value === "") return new Big(0);
  if (value instanceof Big) return value;
  try {
    return new Big(value);
  } catch {
    return new Big(0);
  }
}

/** True when the string is a well-formed decimal the backend would accept. */
export function isValidDecimal(value: string): boolean {
  if (value.trim() === "") return false;
  try {
    new Big(value);
    return true;
  } catch {
    return false;
  }
}

export function add(a: Money | DecimalString, b: Money | DecimalString): Money {
  return money(a).plus(money(b));
}

export function subtract(a: Money | DecimalString, b: Money | DecimalString): Money {
  return money(a).minus(money(b));
}

export function multiply(a: Money | DecimalString, b: Money | DecimalString): Money {
  return money(a).times(money(b));
}

export function sum(values: Array<Money | DecimalString>): Money {
  return values.reduce<Money>((total, value) => total.plus(money(value)), new Big(0));
}

/** Percentage OF an amount, e.g. percentOf("1000", "18") -> 180. */
export function percentOf(amount: Money | DecimalString, percent: Money | DecimalString): Money {
  return money(amount).times(money(percent)).div(100);
}

/** Back to the wire format: a plain decimal string, fixed to `scale` places. */
export function toDecimalString(value: Money | DecimalString, scale = 2): DecimalString {
  return money(value).toFixed(scale);
}

export function isZero(value: Money | DecimalString): boolean {
  return money(value).eq(0);
}

export function isNegative(value: Money | DecimalString): boolean {
  return money(value).lt(0);
}

export function compare(a: Money | DecimalString, b: Money | DecimalString): -1 | 0 | 1 {
  return money(a).cmp(money(b)) as -1 | 0 | 1;
}

/**
 * Intl.NumberFormat.format accepts a decimal STRING and formats it at full
 * precision (ECMA-402 "Intl.NumberFormat v3", lib.es2023.intl.d.ts). Passing
 * a JS number instead silently corrupts large amounts — formatting
 * "9007199254740993.01" as a number yields ...994.00. The cast is to
 * `StringNumericLiteral`, a template-literal type no runtime string can be
 * proven to match; Big.toFixed() always produces exactly that shape.
 */
function formatDecimal(formatter: Intl.NumberFormat, decimal: string): string {
  return formatter.format(decimal as Intl.StringNumericLiteral);
}

export interface CurrencyFormatOptions {
  /** ISO 4217 code, e.g. "INR". Comes from the organization's settings. */
  currency: string;
  /** BCP 47 tag. "en-IN" gives the Indian digit grouping (1,00,000). */
  locale?: string;
  /**
   * Accounting presentation: negatives in parentheses rather than with a
   * minus sign (spec §71). Use for statements and ledgers; leave off for
   * inputs and running totals where a minus reads more clearly.
   */
  accounting?: boolean;
  /** Omit the symbol — for table columns that carry it in the header. */
  hideSymbol?: boolean;
  minimumFractionDigits?: number;
  maximumFractionDigits?: number;
}

/**
 * The single place money becomes text. Feature components must not
 * hand-format amounts (spec §70).
 */
export function formatMoney(
  value: DecimalString | Money | null | undefined,
  options: CurrencyFormatOptions,
): string {
  const {
    currency,
    locale = "en-IN",
    accounting = false,
    hideSymbol = false,
    minimumFractionDigits = 2,
    maximumFractionDigits = 2,
  } = options;

  const amount = money(value);
  const negative = amount.lt(0);
  // Format the magnitude, then apply the sign presentation ourselves, so that
  // accounting parentheses wrap the symbol too: (₹1,000.00), not ₹(1,000.00).
  const magnitude = negative ? amount.times(-1) : amount;

  const formatter = new Intl.NumberFormat(locale, {
    style: hideSymbol ? "decimal" : "currency",
    currency,
    currencyDisplay: "narrowSymbol",
    minimumFractionDigits,
    maximumFractionDigits,
  });

  const text = formatDecimal(formatter, magnitude.toFixed(maximumFractionDigits));

  if (!negative) return text;
  return accounting ? `(${text})` : `-${text}`;
}

/**
 * Quantities are decimals too (0.5 hours, 12.25 kg) but are not currency and
 * must not gain a symbol.
 */
export function formatQuantity(
  value: DecimalString | Money | null | undefined,
  options: { locale?: string; maximumFractionDigits?: number } = {},
): string {
  const { locale = "en-IN", maximumFractionDigits = 4 } = options;
  const amount = money(value);
  return formatDecimal(
    new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits }),
    amount.toFixed(maximumFractionDigits),
  );
}

export function formatPercent(
  value: DecimalString | Money | null | undefined,
  options: { locale?: string; maximumFractionDigits?: number } = {},
): string {
  const { locale = "en-IN", maximumFractionDigits = 2 } = options;
  const amount = money(value);
  const formatted = formatDecimal(
    new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits }),
    amount.toFixed(maximumFractionDigits),
  );
  return `${formatted}%`;
}

/**
 * Screen-reader text for an amount. Parentheses do not read as "negative",
 * and colour alone must never carry the meaning (spec §68), so accounting
 * figures get an explicit spoken label.
 */
export function moneyAriaLabel(
  value: DecimalString | Money | null | undefined,
  options: CurrencyFormatOptions,
): string {
  const amount = money(value);
  const spoken = formatMoney(amount.lt(0) ? amount.times(-1) : amount, {
    ...options,
    accounting: false,
  });
  return amount.lt(0) ? `negative ${spoken}` : spoken;
}
