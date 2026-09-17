import type { QueryParams } from "./query";

/**
 * Query-key factory.
 *
 * EVERY key starts with the organization id. The query client is already
 * remounted per tenant (src/app/(app)/layout.tsx), so this is the second line
 * of defence: even if a client were somehow reused across a switch, a key
 * collision between two organizations is impossible (spec §100).
 *
 * Keys are also the invalidation vocabulary. Posting an invoice must expire
 * the invoice list, the customer's balance, the AR reports and the dashboard —
 * `resource()` prefixes make that one call instead of six.
 */

export type QueryKey = readonly unknown[];

/** Stable key fragment for a params object — order-independent. */
function normalize(params: QueryParams | undefined): Record<string, unknown> {
  if (!params) return {};
  const entries = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .sort(([a], [b]) => a.localeCompare(b));
  return Object.fromEntries(entries);
}

export const queryKeys = {
  /** Everything for one tenant. Invalidating this clears the organization. */
  org: (organizationId: string): QueryKey => [organizationId] as const,

  /** All keys for one resource family, e.g. ["org","sales/invoices"]. */
  resource: (organizationId: string, resource: string): QueryKey =>
    [organizationId, resource] as const,

  /** A filtered/paginated list. */
  list: (organizationId: string, resource: string, params?: QueryParams): QueryKey =>
    [organizationId, resource, "list", normalize(params)] as const,

  /** A single record. */
  detail: (organizationId: string, resource: string, id: string): QueryKey =>
    [organizationId, resource, "detail", id] as const,

  /** A sub-collection of one record, e.g. an invoice's payments. */
  nested: (
    organizationId: string,
    resource: string,
    id: string,
    child: string,
    params?: QueryParams,
  ): QueryKey => [organizationId, resource, "detail", id, child, normalize(params)] as const,

  /** A report, which is parameterised by period rather than by id. */
  report: (organizationId: string, report: string, params?: QueryParams): QueryKey =>
    [organizationId, "reports", report, normalize(params)] as const,

  /** Reference data used by pickers — items, accounts, warehouses, taxes. */
  lookup: (organizationId: string, kind: string, search?: string): QueryKey =>
    [organizationId, "lookup", kind, search ?? ""] as const,
};

/**
 * What a write to `resource` should expire.
 *
 * Financial writes cascade: posting an invoice changes the customer's
 * outstanding balance, the ledger, the trial balance and every AR report. It
 * is far safer to over-invalidate — a refetch costs a request, a stale balance
 * costs the user's trust in the numbers.
 */
export const INVALIDATES_ON_WRITE: Record<string, string[]> = {
  "sales/customers": ["reports"],
  "sales/quotes": ["sales/orders", "sales/invoices"],
  "sales/orders": ["sales/quotes", "sales/deliveries", "sales/invoices"],
  "sales/deliveries": ["sales/orders", "sales/invoices", "inventory", "reports"],
  "sales/invoices": ["sales/customers", "reports", "accounting", "sales/payments", "inventory"],
  "sales/payments": ["sales/invoices", "sales/customers", "reports", "accounting", "bank-transactions"],
  "sales/credit-notes": ["sales/invoices", "sales/customers", "reports", "accounting", "inventory"],
  "sales/recurring-invoices": ["sales/invoices"],
  "purchases/vendors": ["reports"],
  "purchases/orders": ["purchases/goods-receipts", "purchases/bills"],
  "purchases/goods-receipts": ["purchases/orders", "inventory", "purchases/bills", "reports"],
  "purchases/bills": ["purchases/vendors", "reports", "accounting", "purchases/payments", "inventory"],
  "purchases/expenses": ["reports", "accounting", "projects"],
  "purchases/payments": ["purchases/bills", "purchases/vendors", "reports", "accounting", "bank-transactions"],
  "purchases/vendor-credits": ["purchases/bills", "purchases/vendors", "reports", "accounting", "inventory"],
  "purchases/recurring-bills": ["purchases/bills"],
  "purchases/recurring-expenses": ["purchases/expenses"],
  items: ["inventory", "reports"],
  "items/units": ["items"],
  "inventory/warehouses": ["inventory"],
  "inventory/adjustments": ["inventory", "items", "reports", "accounting"],
  "inventory/transfers": ["inventory", "reports"],
  "accounting/accounts": ["accounting", "reports"],
  "accounting/journals": ["accounting", "reports"],
  projects: ["time-entries", "reports", "sales/invoices"],
  "time-entries": ["projects", "reports"],
  "bank-accounts": ["bank-transactions", "accounting", "reports"],
  "bank-transactions": ["bank-accounts", "bank-reconciliations", "accounting", "reports"],
  "bank-transfers": ["bank-accounts", "bank-transactions", "accounting", "reports"],
  "bank-rules": ["bank-transactions"],
  "bank-reconciliations": ["bank-accounts", "bank-transactions", "accounting", "reports"],
  documents: ["documents"],
  "automation/rules": ["automation"],
};

/** Resource prefixes to invalidate after writing `resource`, including itself. */
export function invalidationTargets(resource: string): string[] {
  return [resource, ...(INVALIDATES_ON_WRITE[resource] ?? [])];
}
