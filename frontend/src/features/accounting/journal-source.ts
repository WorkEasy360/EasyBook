/**
 * Where a journal came from, as a link to the document that raised it.
 *
 * `source_type` values are the literals the posting bridges pass to
 * accounting (grep for `source_type=` across the backend apps): e.g. "sales.Invoice" from
 * sales/services/invoices.py, "stock_adjustment" from
 * inventory/services/adjustments.py. A reversal copies its original's source
 * (accounting/services/posting.py :: reverse_journal), so a voided invoice's
 * reversing journal links back to the same invoice.
 *
 * An unrecognised type is shown as its raw label with no link rather than a
 * guessed route.
 */

export interface JournalSource {
  label: string;
  href: string | null;
}

const SOURCE_ROUTES: Record<string, { label: string; route: string | null }> = {
  "sales.Invoice": { label: "Invoice", route: "/sales/invoices" },
  "sales.CreditNote": { label: "Credit note", route: "/sales/credit-notes" },
  "sales.CustomerPayment": { label: "Payment received", route: "/sales/payments" },
  "sales.DeliveryChallan": { label: "Delivery challan", route: "/sales/deliveries" },
  "purchases.Bill": { label: "Bill", route: "/purchases/bills" },
  "purchases.Expense": { label: "Expense", route: "/purchases/expenses" },
  "purchases.GoodsReceipt": { label: "Goods receipt", route: "/purchases/goods-receipts" },
  "purchases.VendorPayment": { label: "Payment made", route: "/purchases/payments" },
  "purchases.VendorCredit": { label: "Vendor credit", route: "/purchases/vendor-credits" },
  stock_adjustment: { label: "Stock adjustment", route: "/inventory/adjustments" },
  "banking.BankTransaction": { label: "Bank transaction", route: "/banking/transactions" },
  // Transfers have a list and a create page but no per-record route, so they
  // are named but not linked rather than pointed at a guessed URL.
  "banking.BankTransfer": { label: "Bank transfer", route: null },
};

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Null for a manual journal (no source_type). */
export function journalSourceOf(sourceType: string, sourceId: string): JournalSource | null {
  if (!sourceType) return null;
  // ".void" variants are stock-movement markers; the document is the same.
  const base = sourceType.endsWith(".void") ? sourceType.slice(0, -".void".length) : sourceType;
  const known = SOURCE_ROUTES[base];
  if (!known) return { label: sourceType, href: null };
  const href = known.route && UUID_PATTERN.test(sourceId) ? `${known.route}/${sourceId}` : null;
  return { label: known.label, href };
}
