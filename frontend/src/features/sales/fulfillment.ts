import Big from "big.js";
import { isValidDecimal } from "@/lib/money";
import type { DecimalString } from "@/lib/api/types";
import type { DeliveryChallan, SalesOrder } from "@/types/api/sales";

/**
 * How much of each sales-order line has already gone out.
 *
 * The backend never serializes a fulfilled quantity (SalesOrderLineSerializer
 * has none). It derives it on demand in sales/selectors.py ::
 * get_fulfilled_quantity — the sum of challan-line quantities linked to the
 * order line, counting only DISPATCHED and DELIVERED challans — and enforces
 * it on every challan create/edit (over_fulfillment). This mirrors that sum
 * from the challans the API lists, so a new challan can be PREFILLED with
 * what is left. It is a starting value for a form, never a figure shown as
 * authoritative, and the backend rejects any over-delivery regardless.
 *
 * Quantities, not money — but still decimal strings through Big, because
 * "0.1 + 0.2" of a unit is as wrong for stock as it is for rupees.
 */

export interface LineFulfillment {
  ordered: DecimalString;
  /** Dispatched or delivered — exactly what the backend counts. */
  fulfilled: DecimalString;
  /** ordered − fulfilled, floored at zero. */
  remaining: DecimalString;
  /** On DRAFT challans not yet dispatched; the backend does not count these. */
  inDrafts: DecimalString;
}

const COUNTED = new Set(["dispatched", "delivered"]);

function decimal(value: string): Big {
  return isValidDecimal(value) ? new Big(value) : new Big(0);
}

/**
 * @param challans every challan that may reference the order (the caller
 *   passes the customer's challans; ones for other orders are ignored).
 * @param excludeChallanId a draft being edited, whose own lines must not be
 *   counted against itself.
 */
export function fulfillmentByOrderLine(
  order: Pick<SalesOrder, "id" | "lines">,
  challans: ReadonlyArray<Pick<DeliveryChallan, "id" | "status" | "source_sales_order" | "lines">>,
  excludeChallanId?: string,
): Map<string, LineFulfillment> {
  const fulfilled = new Map<string, Big>();
  const drafts = new Map<string, Big>();

  for (const challan of challans) {
    if (challan.source_sales_order !== order.id || challan.id === excludeChallanId) continue;
    const bucket = COUNTED.has(challan.status) ? fulfilled : challan.status === "draft" ? drafts : null;
    if (!bucket) continue; // cancelled challans issued nothing
    for (const line of challan.lines) {
      if (!line.source_order_line) continue;
      bucket.set(line.source_order_line, (bucket.get(line.source_order_line) ?? new Big(0)).plus(decimal(line.quantity)));
    }
  }

  const result = new Map<string, LineFulfillment>();
  for (const line of order.lines) {
    const ordered = decimal(line.quantity);
    const done = fulfilled.get(line.id) ?? new Big(0);
    const left = ordered.minus(done);
    result.set(line.id, {
      ordered: ordered.toFixed(4),
      fulfilled: done.toFixed(4),
      remaining: (left.gt(0) ? left : new Big(0)).toFixed(4),
      inDrafts: (drafts.get(line.id) ?? new Big(0)).toFixed(4),
    });
  }
  return result;
}

/** "3.0000" → "3", "2.5000" → "2.5" — a form value, not a display format. */
export function trimQuantity(value: DecimalString): DecimalString {
  if (!isValidDecimal(value)) return value;
  return new Big(value).toString();
}
