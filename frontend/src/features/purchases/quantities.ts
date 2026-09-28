import Big from "big.js";
import { isValidDecimal } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";

/**
 * Pure helpers behind the purchase forms' PREFILLS and estimates.
 *
 * Nothing here decides a financial figure. They choose sensible starting
 * values (how much of an order is still outstanding, how much of a payment is
 * left unallocated) that the user reviews and the backend re-validates:
 * over_receipt, over_billing, over_allocation and
 * vendor_advance_account_required are all enforced server-side regardless.
 * Arithmetic is decimal (big.js) on strings, never float.
 */

function decimal(value: DecimalString | number | null | undefined): Big {
  if (value === null || value === undefined) return new Big(0);
  const text = typeof value === "number" ? String(value) : value;
  return isValidDecimal(text) ? new Big(text) : new Big(0);
}

/** Trims a 4-dp quantity to its shortest exact form: "6.0000" → "6", "2.5000" → "2.5". */
export function plainQuantity(value: Big): DecimalString {
  return value.toFixed(4).replace(/\.?0+$/, "") || "0";
}

/**
 * How much of a purchase order line has neither physically arrived nor been
 * billed — the part a NEW goods receipt or a NEW direct bill may still cover.
 *
 * Why max(received, billed) and not just `ordered − received`: in this
 * backend a bill whose line links no goods-receipt line RECEIVES the stock
 * itself on posting (purchases/services/bills.py, case (b)), but the match
 * endpoint's `received_quantity` only counts goods receipts. Goods billed
 * directly are therefore already in stock while still showing as "not
 * received". Prefilling `ordered − received` would invite receiving them a
 * second time. Taking the larger of the two covers both routes:
 *  - receipt then receipt-linked bill: received = billed → max is either;
 *  - direct bill: billed > received → the billed stock is not offered again.
 */
export function outstandingOrderQuantity(
  ordered: DecimalString | number,
  received: DecimalString | number | null | undefined,
  billed: DecimalString | number | null | undefined,
): DecimalString {
  const receivedQty = decimal(received);
  const billedQty = decimal(billed);
  const done = receivedQty.gt(billedQty) ? receivedQty : billedQty;
  const remaining = decimal(ordered).minus(done);
  return remaining.gt(0) ? plainQuantity(remaining) : "0";
}

/**
 * Quantity already billed per goods-receipt line, from a set of bills.
 *
 * The same rule as selectors.get_billed_quantity_for_receipt_line: every bill
 * counts except VOID ones — drafts included, because two drafts claiming the
 * same received goods is the mistake that guard exists to catch. Used only to
 * decide whether to offer "Convert to bill"; the backend refuses a fully
 * billed receipt (goods_receipt_fully_billed) regardless.
 */
export function billedByReceiptLine(
  bills: ReadonlyArray<{
    status: string;
    lines: ReadonlyArray<{ source_goods_receipt_line: string | null; quantity: DecimalString }>;
  }>,
): Map<string, DecimalString> {
  const totals = new Map<string, Big>();
  for (const bill of bills) {
    if (bill.status === "void") continue;
    for (const line of bill.lines) {
      if (!line.source_goods_receipt_line) continue;
      const previous = totals.get(line.source_goods_receipt_line) ?? new Big(0);
      totals.set(line.source_goods_receipt_line, previous.plus(decimal(line.quantity)));
    }
  }
  return new Map([...totals].map(([id, total]) => [id, plainQuantity(total)]));
}

/** True when `quantity` exceeds `covered` (either may be absent). */
export function exceeds(quantity: DecimalString | number, covered: DecimalString | number | null | undefined): boolean {
  return decimal(quantity).gt(decimal(covered));
}

/** True when a quantity string is numerically above zero. */
export function isPositive(value: DecimalString | number | null | undefined): boolean {
  return decimal(value).gt(0);
}

export interface AllocationSummary {
  /** Sum of the valid, positive allocation amounts entered. */
  allocated: DecimalString;
  /** amount − allocated; what the backend books as a vendor advance when positive. */
  remainder: DecimalString;
  /** Allocations exceed the payment (allocation_exceeds_payment on the server). */
  overAllocated: boolean;
  /** A positive remainder needs vendor_advance_account_id (vendor_advance_account_required). */
  needsAdvanceAccount: boolean;
}

/**
 * The payment form's running ESTIMATE of how a payment splits between bills
 * and an advance. Mirrors the checks in purchases/services/payments.py
 * :: record_vendor_payment so the obvious mistakes surface before a round
 * trip; the server re-derives all of it.
 */
export function allocationSummary(
  amount: DecimalString,
  allocations: ReadonlyArray<DecimalString>,
): AllocationSummary {
  const total = decimal(amount);
  const allocated = allocations.reduce((sum, value) => {
    const entry = decimal(value);
    return entry.gt(0) ? sum.plus(entry) : sum;
  }, new Big(0));
  const remainder = total.minus(allocated);
  return {
    allocated: allocated.toFixed(2),
    remainder: remainder.toFixed(2),
    overAllocated: remainder.lt(0),
    needsAdvanceAccount: remainder.gt(0),
  };
}

/**
 * An expense's tax and total, ESTIMATED while typing. The server's
 * purchases/services/expenses.py :: _compute_totals rounds the amount and the
 * tax half-up to 2 dp; the saved expense shows the server's figures.
 */
export function estimateExpense(
  amount: DecimalString,
  taxRate: DecimalString,
): { amount: DecimalString; tax: DecimalString; total: DecimalString } | null {
  if (!isValidDecimal(amount)) return null;
  const rateText = taxRate.trim() === "" ? "0" : taxRate;
  if (!isValidDecimal(rateText)) return null;
  const base = new Big(amount).round(2, Big.roundHalfUp);
  const rate = new Big(rateText);
  if (base.lte(0) || rate.lt(0)) return null;
  const tax = base.times(rate).div(100).round(2, Big.roundHalfUp);
  return { amount: base.toFixed(2), tax: tax.toFixed(2), total: base.plus(tax).toFixed(2) };
}
