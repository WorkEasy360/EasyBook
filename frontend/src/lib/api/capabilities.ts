/**
 * What each list endpoint can ACTUALLY do.
 *
 * Verified by reading every `get_queryset()` in each module's api/views.py, not
 * assumed from the shape of the UI we would like to build (spec §105).
 *
 * The finding that shapes every list screen:
 *
 *   NO endpoint supports `?search=` or `?ordering=`.
 *
 * DRF's SearchFilter and OrderingFilter are not configured anywhere —
 * `filter_backends`, `search_fields` and `ordering_fields` appear only in
 * `admin.py` files, which are Django admin, not the API. Each view filters by
 * hand from `request.query_params.get(...)`, and unrecognised params are
 * ignored silently.
 *
 * So a search box or a sortable column header would look like it worked and
 * do nothing — the exact "fake screen disconnected from backend" failure the
 * brief forbids (spec §108). Lists therefore expose only the filters listed
 * here, and `supportsSearch`/`supportsOrdering` gate those controls.
 *
 * BACKEND CONTRACT BLOCKER — to enable search and sorting, each list view
 * needs `filter_backends = [SearchFilter, OrderingFilter]` plus `search_fields`
 * and `ordering_fields`. Until then this file is the single source of truth,
 * and a test asserts no screen offers a control the API cannot honour.
 */

import { buildSearch, type QueryParams } from "./query";

export interface ListCapabilities {
  /** Query params the endpoint reads. Anything else is ignored by the server. */
  filters: readonly string[];
  /** True only when the view configures DRF's SearchFilter. */
  supportsSearch: boolean;
  /** True only when the view configures DRF's OrderingFilter. */
  supportsOrdering: boolean;
  /** Fixed server-side sort, for the "sorted by" hint on the list. */
  defaultOrder?: string;
  /** True when `?export=csv` is wired (reports/exports/csv.py). */
  supportsCsvExport?: boolean;
}

const NONE = { supportsSearch: false, supportsOrdering: false } as const;

export const LIST_CAPABILITIES: Record<string, ListCapabilities> = {
  // Extracted from every api/views.py class body (query_params.get calls) and
  // each model's Meta.ordering — not from the UI we would like to build.

  // --- sales (sales/api/views.py) ---------------------------------------
  "sales/customers": { ...NONE, filters: ["is_active"], defaultOrder: "name" },
  "sales/quotes": { ...NONE, filters: ["status", "customer"], defaultOrder: "issue date, newest first" },
  "sales/orders": { ...NONE, filters: ["status", "customer"], defaultOrder: "order date, newest first" },
  "sales/deliveries": { ...NONE, filters: ["status", "customer"], defaultOrder: "challan date, newest first" },
  "sales/invoices": { ...NONE, filters: ["status", "customer"], defaultOrder: "invoice date, newest first" },
  "sales/payments": { ...NONE, filters: ["customer"], defaultOrder: "payment date, newest first" },
  "sales/credit-notes": { ...NONE, filters: ["status", "customer"], defaultOrder: "credit note date, newest first" },
  "sales/recurring-invoices": { ...NONE, filters: ["is_active", "customer"], defaultOrder: "next run date" },

  // --- purchases (purchases/api/views.py) -------------------------------
  "purchases/vendors": { ...NONE, filters: ["is_active"], defaultOrder: "name" },
  "purchases/orders": { ...NONE, filters: ["status", "vendor"], defaultOrder: "order date, newest first" },
  "purchases/goods-receipts": { ...NONE, filters: ["status", "vendor"], defaultOrder: "receipt date, newest first" },
  "purchases/bills": { ...NONE, filters: ["status", "vendor"], defaultOrder: "bill date, newest first" },
  "purchases/expenses": {
    ...NONE,
    filters: ["status", "vendor", "is_billable"],
    defaultOrder: "expense date, newest first",
  },
  "purchases/payments": { ...NONE, filters: ["vendor"], defaultOrder: "payment date, newest first" },
  "purchases/vendor-credits": { ...NONE, filters: ["status", "vendor"], defaultOrder: "credit date, newest first" },
  "purchases/recurring-bills": { ...NONE, filters: ["is_active", "vendor"], defaultOrder: "next run date" },
  "purchases/recurring-expenses": { ...NONE, filters: ["is_active", "vendor"], defaultOrder: "next run date" },

  // --- items / inventory (items/api/views.py, inventory/api/views.py) ----
  items: { ...NONE, filters: ["is_active", "item_type"], defaultOrder: "name" },
  // Units and warehouses read no query params at all.
  "items/units": { ...NONE, filters: [], defaultOrder: "code" },
  "inventory/warehouses": { ...NONE, filters: [], defaultOrder: "code" },
  // Not paginated: a bare array (see StockOnHandRow).
  "inventory/stock-summary": { ...NONE, filters: ["item_id", "warehouse_id", "low_stock_only"] },
  "inventory/movements": {
    ...NONE,
    filters: ["item_id", "warehouse_id", "movement_type"],
    defaultOrder: "movement date, oldest first",
  },
  // The adjustment list reads `status` only — a warehouse filter would be ignored.
  "inventory/adjustments": { ...NONE, filters: ["status"], defaultOrder: "adjustment date, newest first" },
  // reports/api/views.py :: _resolve_warehouse/_resolve_item — the param names
  // differ from the inventory endpoints (`warehouse`, not `warehouse_id`).
  "reports/inventory/summary": { ...NONE, filters: ["warehouse", "item", "as_of_date"], supportsCsvExport: true },

  // --- accounting --------------------------------------------------------
  "accounting/accounts": { ...NONE, filters: ["account_type", "is_active"], defaultOrder: "code" },
  // Journals read `status` only; a date range is ignored. The dated view is
  // /reports/journals/, which does take from_date/to_date.
  "accounting/journals": { ...NONE, filters: ["status"], defaultOrder: "posting date, newest first" },

  // --- projects ----------------------------------------------------------
  projects: { ...NONE, filters: ["status", "customer"], defaultOrder: "name" },
  "projects/tasks": { ...NONE, filters: ["is_active"], defaultOrder: "name" },
  "time-entries": {
    ...NONE,
    filters: ["project", "status", "from_date", "to_date"],
    defaultOrder: "entry date, newest first",
  },

  // --- banking -----------------------------------------------------------
  "bank-accounts": { ...NONE, filters: ["is_active", "kind"], defaultOrder: "name" },
  "bank-transactions": {
    ...NONE,
    filters: ["bank_account", "status", "from_date", "to_date"],
    defaultOrder: "transaction date, newest first",
  },
  "bank-transfers": { ...NONE, filters: [], defaultOrder: "transfer date, newest first" },
  "bank-rules": { ...NONE, filters: [], defaultOrder: "priority, then name" },
  "bank-reconciliations": { ...NONE, filters: ["bank_account"], defaultOrder: "statement end date, newest first" },

  // --- documents ---------------------------------------------------------
  // The ONE real search in the product: documents/api/urls.py exposes a
  // dedicated /documents/search/ endpoint (q, document_type, folder_id,
  // tags), separate from the list.
  documents: { ...NONE, filters: ["document_type", "upload_status"], defaultOrder: "newest first" },

  // --- automation --------------------------------------------------------
  "automation/rules": { ...NONE, filters: ["status", "trigger_type"], defaultOrder: "highest priority first, then name" },
  "automation/executions": { ...NONE, filters: ["rule", "status"], defaultOrder: "newest first" },
};

/** Conservative default: an unknown resource gets no search and no sorting. */
export function capabilitiesFor(resource: string): ListCapabilities {
  return LIST_CAPABILITIES[resource] ?? { ...NONE, filters: [] };
}

/**
 * Reports that accept `?export=csv` (reports/exports/csv.py :: wants_csv).
 *
 * Deliberately NOT every report: the multi-section statements (P&L, Balance
 * Sheet, Cash Flow) and the paginated transaction reports (General Ledger,
 * Journal, Inventory Movement) are not wired for CSV, and the brief forbids
 * offering an export that does not exist (spec §87).
 *
 * Note the param is `export`, not `format` — DRF reserves `format` for its own
 * renderer negotiation and 404s on an unknown one.
 */
export const CSV_EXPORTABLE_REPORTS = new Set([
  "receivables/customer-balances",
  "receivables/outstanding-invoices",
  "receivables/overdue-invoices",
  "payables/vendor-balances",
  "payables/outstanding-bills",
  "payables/overdue-bills",
  "sales/by-customer",
  "sales/by-item",
  "purchases/by-vendor",
  "purchases/by-item",
  "expenses/by-category",
  "inventory/summary",
  "inventory/low-stock",
  "projects/profitability",
]);

export function supportsCsvExport(report: string): boolean {
  return CSV_EXPORTABLE_REPORTS.has(report);
}

/**
 * Same-origin download URL for a report's CSV, through the BFF.
 *
 * No trailing slash: Next 308-redirects `/api/bff/x/` to `/api/bff/x`, and the
 * BFF appends Django's slash itself. Returns null for a report with no CSV,
 * so a caller cannot render an export button the backend cannot honour.
 */
export function csvExportHref(report: string, params: QueryParams = {}): string | null {
  if (!supportsCsvExport(report)) return null;
  const search = buildSearch({ ...params, export: "csv" });
  return `/api/bff/reports/${report}?${search}`;
}
