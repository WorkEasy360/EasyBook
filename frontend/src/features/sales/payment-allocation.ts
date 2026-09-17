import Big from "big.js";
import { isValidDecimal } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";

/**
 * Form-time ESTIMATE of how a customer payment splits across invoices.
 *
 * Mirrors the checks in sales/services/payments.py :: record_payment so the
 * obvious mistakes surface before a round trip:
 *   - each allocation must be positive          (allocation_amount_invalid)
 *   - and no more than the invoice's amount due  (over_allocation)
 *   - allocations together cannot exceed the payment (allocation_exceeds_payment)
 *   - any remainder becomes unapplied credit, which needs a liability
 *     account                                    (unapplied_credit_account_required)
 *
 * The amount due each invoice is compared against is the SERVER's
 * `amount_due` as last fetched. The backend re-checks every rule under a row
 * lock, so a payment recorded elsewhere in the meantime is still caught.
 */

export interface AllocationEstimate {
  /** Sum of the well-formed, positive allocation amounts. */
  allocated: DecimalString;
  /** payment − allocated; null while the payment amount is not a number. */
  unapplied: DecimalString | null;
  /** Allocations add up to more than the payment. */
  exceedsPayment: boolean;
  /** A remainder exists, so the unapplied credit account is required. */
  needsUnappliedAccount: boolean;
}

function parse(value: string | undefined | null): Big | null {
  if (value === undefined || value === null || value.trim() === "" || !isValidDecimal(value)) return null;
  return new Big(value);
}

export function estimateAllocation(amount: string, allocations: Record<string, string>): AllocationEstimate {
  let allocated = new Big(0);
  for (const raw of Object.values(allocations)) {
    const value = parse(raw);
    if (value && value.gt(0)) allocated = allocated.plus(value);
  }

  const payment = parse(amount);
  if (!payment) {
    return { allocated: allocated.toFixed(2), unapplied: null, exceedsPayment: false, needsUnappliedAccount: false };
  }
  const unapplied = payment.minus(allocated);
  return {
    allocated: allocated.toFixed(2),
    unapplied: unapplied.toFixed(2),
    exceedsPayment: unapplied.lt(0),
    needsUnappliedAccount: unapplied.gt(0),
  };
}

/** The per-row problem, or null. Blank means "not allocated", which is fine. */
export function allocationProblem(raw: string | undefined, amountDue: DecimalString): string | null {
  if (raw === undefined || raw.trim() === "") return null;
  const value = parse(raw);
  if (!value) return "Enter an amount.";
  if (value.lte(0)) return "Enter an amount above zero, or leave it blank.";
  const due = parse(amountDue);
  if (due && value.gt(due)) return "More than this invoice's balance due.";
  return null;
}

/** Only the rows with an amount, in the API's shape. */
export function allocationsInput(allocations: Record<string, string>): Array<{ invoice_id: string; amount: DecimalString }> {
  return Object.entries(allocations)
    .filter(([, raw]) => {
      const value = parse(raw);
      return value !== null && value.gt(0);
    })
    .map(([invoiceId, raw]) => ({ invoice_id: invoiceId, amount: raw.trim() }));
}
