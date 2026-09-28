import { isValidDecimal, money, subtract, sum, toDecimalString } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";

/**
 * Form-time arithmetic for a manual journal. An ESTIMATE only.
 *
 * The authority is accounting/services/posting.py :: post_journal, which sums
 * the lines' BASE amounts and refuses to post unless debits == credits
 * (`journal_unbalanced`) and the total is non-zero (`journal_zero_total`).
 * This mirrors those checks so the user sees the problem before a round trip;
 * it never replaces them.
 */

export interface JournalLineAmounts {
  debit: DecimalString;
  credit: DecimalString;
}

export type LineSide = "debit" | "credit" | "both" | "none" | "invalid";

/** A blank amount is zero; anything else must be a well-formed decimal. */
function amountOf(value: DecimalString): { ok: boolean; zero: boolean } {
  if (value.trim() === "") return { ok: true, zero: true };
  if (!isValidDecimal(value)) return { ok: false, zero: true };
  return { ok: true, zero: money(value).eq(0) };
}

/**
 * Which side a line sits on, matching accounting/services/journals.py ::
 * _validate_line_shape: exactly one of debit/credit must be non-zero.
 */
export function lineSide(line: JournalLineAmounts): LineSide {
  const debit = amountOf(line.debit);
  const credit = amountOf(line.credit);
  if (!debit.ok || !credit.ok) return "invalid";
  if (money(line.debit || "0").lt(0) || money(line.credit || "0").lt(0)) return "invalid";
  if (!debit.zero && !credit.zero) return "both";
  if (debit.zero && credit.zero) return "none";
  return debit.zero ? "credit" : "debit";
}

export interface JournalTotalsEstimate {
  debit: DecimalString;
  credit: DecimalString;
  /** debit − credit. Zero when balanced. */
  difference: DecimalString;
  /** Equal and non-zero — the two conditions posting enforces. */
  balanced: boolean;
  /** False while any line has an unparseable amount; those lines are left out. */
  complete: boolean;
}

export function estimateJournalTotals(lines: readonly JournalLineAmounts[]): JournalTotalsEstimate {
  const usable = lines.filter((line) => lineSide(line) !== "invalid");
  const debit = sum(usable.map((line) => line.debit || "0"));
  const credit = sum(usable.map((line) => line.credit || "0"));
  const difference = subtract(debit, credit);
  return {
    debit: toDecimalString(debit),
    credit: toDecimalString(credit),
    difference: toDecimalString(difference),
    balanced: difference.eq(0) && debit.gt(0),
    complete: usable.length === lines.length,
  };
}
