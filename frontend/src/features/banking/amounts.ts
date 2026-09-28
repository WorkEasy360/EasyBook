import { isValidDecimal, money, multiply, toDecimalString } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";

/**
 * String-level helpers for the banking sign convention.
 *
 * A statement line's amount is SIGNED from the account holder's view —
 * positive money in, negative money out (banking/CLAUDE.md). These helpers
 * move a sign on and off a decimal string without arithmetic, so no amount is
 * ever rounded, re-scaled or passed through a float on its way to the API.
 */

export type Direction = "in" | "out";

/** "1250.00" + out → "-1250.00". Any sign the person typed is replaced, not combined. */
export function signedAmount(magnitude: DecimalString, direction: Direction): DecimalString {
  const bare = unsigned(magnitude);
  return direction === "out" ? `-${bare}` : bare;
}

/** "-1250.00" → "1250.00". For showing money out in its own column. */
export function unsigned(amount: DecimalString): DecimalString {
  return amount.trim().replace(/^[-+]/, "");
}

/** A positive, non-zero decimal string — what every banking amount input requires. */
export function isPositiveAmount(value: DecimalString): boolean {
  return isValidDecimal(value) && money(value).gt(0);
}

/**
 * The matcher's confidence ("0.800") as a whole percentage ("80%"). A display
 * of the server's deterministic score, not a figure anything depends on.
 */
export function confidencePercent(confidence: DecimalString): string {
  if (!isValidDecimal(confidence)) return "—";
  return `${toDecimalString(multiply(confidence, "100"), 0)}%`;
}
