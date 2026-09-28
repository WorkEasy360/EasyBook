import Big from "big.js";
import { isValidDecimal } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";

/**
 * Form-time ESTIMATE of a priced document's totals.
 *
 * This mirrors backend/core/money.py :: calculate_line and
 * calculate_document_totals step for step — discount first, tax on the
 * post-discount amount, each figure rounded half-up to 2 places at line
 * level, header totals summed from the rounded lines — so that in the
 * ordinary case the number the user sees while typing is the number the
 * server will store.
 *
 * It is still only an estimate (spec §8). It is shown as one, it is never
 * sent to the API, and after save every figure on screen comes from the
 * server's response. If the backend's rules change, the server wins and this
 * merely drifts; nothing is ever posted from it.
 */

export interface LineInputs {
  quantity: string;
  unit_price: string;
  discount_percent: string;
  tax_rate: string;
}

export interface LineEstimate {
  line_base: DecimalString;
  discount_amount: DecimalString;
  taxable_amount: DecimalString;
  tax_amount: DecimalString;
  line_total: DecimalString;
}

export interface TotalsEstimate {
  subtotal: DecimalString;
  discount_total: DecimalString;
  tax_total: DecimalString;
  total: DecimalString;
  /** False while any line is incomplete; the totals then cover valid lines only. */
  complete: boolean;
}

function roundMoney(value: Big): Big {
  return value.round(2, Big.roundHalfUp);
}

function decimalOr(value: string, fallback: string): Big | null {
  const raw = value.trim() === "" ? fallback : value;
  return isValidDecimal(raw) ? new Big(raw) : null;
}

/** Null when the line cannot be computed yet (blank or malformed inputs). */
export function estimateLine(line: LineInputs): LineEstimate | null {
  const quantity = decimalOr(line.quantity, "");
  const unitPrice = decimalOr(line.unit_price, "");
  const discount = decimalOr(line.discount_percent, "0");
  const taxRate = decimalOr(line.tax_rate, "0");

  if (!quantity || !unitPrice || !discount || !taxRate) return null;
  // The same guards the backend raises on; an invalid line has no estimate.
  if (quantity.lte(0) || unitPrice.lt(0) || discount.lt(0) || discount.gt(100) || taxRate.lt(0)) {
    return null;
  }

  const lineBase = roundMoney(quantity.times(unitPrice));
  const discountAmount = roundMoney(lineBase.times(discount).div(100));
  const taxable = lineBase.minus(discountAmount);
  const taxAmount = roundMoney(taxable.times(taxRate).div(100));
  const lineTotal = taxable.plus(taxAmount);

  return {
    line_base: lineBase.toFixed(2),
    discount_amount: discountAmount.toFixed(2),
    taxable_amount: taxable.toFixed(2),
    tax_amount: taxAmount.toFixed(2),
    line_total: lineTotal.toFixed(2),
  };
}

export function estimateTotals(lines: readonly LineInputs[]): TotalsEstimate {
  let subtotal = new Big(0);
  let discount = new Big(0);
  let tax = new Big(0);
  let total = new Big(0);
  let complete = lines.length > 0;

  for (const line of lines) {
    const estimate = estimateLine(line);
    if (!estimate) {
      complete = false;
      continue;
    }
    subtotal = subtotal.plus(estimate.line_base);
    discount = discount.plus(estimate.discount_amount);
    tax = tax.plus(estimate.tax_amount);
    total = total.plus(estimate.line_total);
  }

  return {
    subtotal: subtotal.toFixed(2),
    discount_total: discount.toFixed(2),
    tax_total: tax.toFixed(2),
    total: total.toFixed(2),
    complete,
  };
}
