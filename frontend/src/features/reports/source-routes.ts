/**
 * Where a `source_type` + `source_id` pair on a journal or stock movement
 * points in the UI.
 *
 * The keys are the literal strings the backend services write (found by
 * reading every `source_type=` in sales/purchases/inventory/banking/accounting
 * services). `.void` variants are the stock movements written when a document
 * is voided, and point at the same document. Types with no detail page
 * (opening balances, stock transfers, bank matching) are labelled but not
 * linked — a link to a page that cannot show the record would be a dead end.
 */

interface SourceRoute {
  label: string;
  href?: (id: string) => string;
}

const ROUTES: Record<string, SourceRoute> = {
  "sales.Invoice": { label: "Invoice", href: (id) => `/sales/invoices/${id}` },
  "sales.Invoice.void": { label: "Invoice (void)", href: (id) => `/sales/invoices/${id}` },
  "sales.CreditNote": { label: "Credit note", href: (id) => `/sales/credit-notes/${id}` },
  "sales.CreditNote.void": { label: "Credit note (void)", href: (id) => `/sales/credit-notes/${id}` },
  "sales.CustomerPayment": { label: "Customer payment", href: (id) => `/sales/payments/${id}` },
  "sales.DeliveryChallan": { label: "Delivery challan", href: (id) => `/sales/deliveries/${id}` },
  "purchases.Bill": { label: "Bill", href: (id) => `/purchases/bills/${id}` },
  "purchases.Bill.void": { label: "Bill (void)", href: (id) => `/purchases/bills/${id}` },
  "purchases.Expense": { label: "Expense", href: (id) => `/purchases/expenses/${id}` },
  "purchases.VendorPayment": { label: "Vendor payment", href: (id) => `/purchases/payments/${id}` },
  "purchases.VendorCredit": { label: "Vendor credit", href: (id) => `/purchases/vendor-credits/${id}` },
  "purchases.VendorCredit.void": { label: "Vendor credit (void)", href: (id) => `/purchases/vendor-credits/${id}` },
  "purchases.GoodsReceipt": { label: "Goods receipt", href: (id) => `/purchases/goods-receipts/${id}` },
  stock_adjustment: { label: "Stock adjustment", href: (id) => `/inventory/adjustments/${id}` },
  stock_transfer: { label: "Stock transfer" },
  opening_stock: { label: "Opening stock" },
  opening_balance: { label: "Opening balance" },
  "banking.BankTransaction": { label: "Bank transaction" },
  "banking.BankTransfer": { label: "Bank transfer" },
};

export interface ResolvedSource {
  label: string;
  href: string | null;
}

/** Label and (when a page exists) link for a record's source. */
export function resolveSource(sourceType: string | null | undefined, sourceId: string | null | undefined): ResolvedSource {
  if (!sourceType) return { label: "Manual", href: null };
  const route = ROUTES[sourceType];
  if (!route) return { label: sourceType, href: null };
  return { label: route.label, href: route.href && sourceId ? route.href(sourceId) : null };
}
