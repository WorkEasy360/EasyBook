import type { DateString, DecimalString, UUID } from "@/lib/api/types";
import type { Account } from "@/types/api/accounting";

/**
 * Report contracts — backend/reports/api/views.py + reports/selectors/*.
 *
 * VERIFIED, not guessed: every shape below was captured from a live response
 * of the dev tenant (2026-09-17) with non-empty rows — a posted invoice, a
 * posted bill, a posted expense, a posted stock adjustment, a bank account
 * and a project — and cross-checked against the selector that builds it.
 * The earlier hand-written file had wrong field names almost everywhere
 * (`change`/`percent_change` for variance, `total` on by-party rows,
 * `party_name`/`total_tax` on tax registers, bare arrays for row reports).
 *
 * Every figure is computed by the deterministic engine. The frontend displays
 * these values and never re-derives, re-totals or re-classifies them. Where a
 * report returns no total, the UI shows none.
 *
 * Money and quantities are decimal STRINGS. Zero is often "0" rather than
 * "0.00" (Python `str(Decimal("0"))`), and inventory values carry 8 places.
 */

/** Every flat row report wraps its rows — `{rows: [...]}`, never a bare array. */
export interface RowsEnvelope<T> {
  rows: T[];
}

/**
 * The echoed period of a by-party / by-item report. Both ends are null when
 * the request omitted them (the report then covers all time).
 */
export interface ReportPeriod {
  from_date: DateString | null;
  to_date: DateString | null;
}

export interface PeriodRowsEnvelope<T> extends RowsEnvelope<T> {
  period: ReportPeriod;
}

// --- Statements ---------------------------------------------------------------

/** One account's contribution to a statement section. */
export interface StatementRow {
  /**
   * Null for a COMPUTED line with no account behind it — the Balance Sheet's
   * "Current Period Earnings" (cumulative net profit, never a stored balance).
   */
  account_id: UUID | null;
  /** "" for the computed line. */
  account_code: string;
  account_name: string;
  amount: DecimalString;
}

/**
 * P&L section keys, in the order the API returns them
 * (reports/selectors/pnl.py :: _SECTION_ORDER). Classification comes from
 * `Account.account_subtype`, never the account name.
 */
export const PNL_SECTIONS = [
  "operating_income",
  "other_income",
  "cogs",
  "operating_expenses",
  "other_expense",
] as const;
export type PnlSection = (typeof PNL_SECTIONS)[number];

export interface PnlTotals {
  revenue: DecimalString;
  cogs: DecimalString;
  gross_profit: DecimalString;
  operating_expenses: DecimalString;
  operating_profit: DecimalString;
  other_income: DecimalString;
  other_expense: DecimalString;
  net_profit: DecimalString;
}

/** reports/selectors/params.py :: compute_variance */
export interface Variance {
  current: DecimalString;
  previous: DecimalString;
  variance: DecimalString;
  /**
   * Null when the previous figure is zero and the current is not — a change
   * "from zero" has no percentage. The backend asks for "n/a", never 0%.
   */
  variance_percent: DecimalString | null;
}

/** GET /reports/profit-loss/?from_date&to_date&comparison_from&comparison_to */
export interface ProfitAndLoss {
  /** `from_date` is null when omitted: the statement then runs from inception. */
  period: { from_date: DateString | null; to_date: DateString };
  currency: string;
  sections: Record<PnlSection, StatementRow[]>;
  totals: PnlTotals;
  /** Present only when comparison_from or comparison_to was sent. */
  comparison_period?: ReportPeriod;
  comparison_totals?: PnlTotals;
  /** Totals-level only — the API returns no per-account comparison. */
  variance?: Record<keyof PnlTotals, Variance>;
}

export const BALANCE_SHEET_ASSET_SECTIONS = ["current_assets", "fixed_assets", "other_assets"] as const;
export const BALANCE_SHEET_LIABILITY_SECTIONS = ["current_liabilities", "long_term_liabilities"] as const;
export type AssetSection = (typeof BALANCE_SHEET_ASSET_SECTIONS)[number];
export type LiabilitySection = (typeof BALANCE_SHEET_LIABILITY_SECTIONS)[number];

/** GET /reports/balance-sheet/?as_of_date — defaults to today when omitted. */
export interface BalanceSheet {
  as_of_date: DateString;
  currency: string;
  assets: Record<AssetSection, StatementRow[]>;
  liabilities: Record<LiabilitySection, StatementRow[]>;
  equity: StatementRow[];
  totals: {
    total_assets: DecimalString;
    total_liabilities: DecimalString;
    total_equity: DecimalString;
    total_liabilities_and_equity: DecimalString;
  };
  /**
   * The engine's own verdict. Displayed as-is — the UI never compares the two
   * totals itself to declare the sheet balanced.
   */
  is_balanced: boolean;
}

/** A cash-flow line — always an account (reports/api/views.py :: _cash_flow_rows_json). */
export interface CashFlowItem {
  account_id: UUID;
  account_code: string;
  account_name: string;
  /** Signed as the CASH effect, not the account's balance movement. */
  amount: DecimalString;
}

/**
 * GET /reports/cash-flow/?from_date(required)&to_date
 *
 * 400 `from_date_required` without a from_date, and 400
 * `cash_accounts_not_configured` when the organization has no bank-kind
 * BankAccount — the engine refuses to guess which accounts are cash.
 */
export interface CashFlow {
  period: { from_date: DateString; to_date: DateString };
  currency: string;
  opening_cash: DecimalString;
  operating_activities: {
    net_profit: DecimalString;
    adjustments: CashFlowItem[];
    total: DecimalString;
  };
  investing_activities: { items: CashFlowItem[]; total: DecimalString };
  financing_activities: { items: CashFlowItem[]; total: DecimalString };
  net_change_in_cash: DecimalString;
  closing_cash: DecimalString;
  cash_accounts: Array<{ bank_account_id: UUID; account_id: UUID; name: string }>;
  /** Engine check that operating + investing + financing == net change in cash. */
  reconciles: boolean;
}

// --- Ledger / trial balance / journals ----------------------------------------------

/** One row of GET /reports/trial-balance/ (and /accounting/reports/trial-balance/). */
export interface TrialBalanceRow {
  /** The full account (accounting AccountSerializer), not flattened fields. */
  account: Account;
  opening_balance: DecimalString;
  period_debit: DecimalString;
  period_credit: DecimalString;
  closing_debit: DecimalString;
  closing_credit: DecimalString;
}

export interface TrialBalance {
  as_of_date: DateString;
  from_date: DateString | null;
  is_balanced: boolean;
  total_period_debit: DecimalString;
  total_period_credit: DecimalString;
  total_closing_debit: DecimalString;
  total_closing_credit: DecimalString;
  rows: TrialBalanceRow[];
}

/** One entry of GET /reports/general-ledger/?account(required) — paginated. */
export interface GeneralLedgerEntry {
  journal_entry_id: UUID;
  journal_number: string;
  posting_date: DateString;
  source_type: string;
  source_id: string;
  description: string;
  debit: DecimalString;
  credit: DecimalString;
  running_balance: DecimalString;
}

/** The paginated envelope plus account-level figures computed over ALL pages. */
export interface GeneralLedgerPage {
  count: number;
  next: string | null;
  previous: string | null;
  results: GeneralLedgerEntry[];
  account: Account;
  opening_balance: DecimalString;
  closing_balance: DecimalString;
}

/**
 * Journal `source_type` values the posting services write — found by reading
 * every `source_type=` passed to a journal call in the backend services (the
 * `.void` variants are stock-movement tags; a void reverses the journal under
 * the ORIGINAL source type, accounting/services/posting.py). Manual journals
 * have an empty source type, which the report cannot filter on.
 */
export const JOURNAL_SOURCE_TYPES = [
  "sales.Invoice",
  "sales.CreditNote",
  "sales.CustomerPayment",
  "purchases.Bill",
  "purchases.Expense",
  "purchases.VendorPayment",
  "purchases.VendorCredit",
  "banking.BankTransaction",
  "banking.BankTransfer",
  "stock_adjustment",
  "opening_stock",
  "opening_balance",
] as const;

// --- Receivables / payables -----------------------------------------------------------

/** reports/selectors/receivables.py :: _invoice_row */
export interface InvoiceReportRow {
  invoice_id: UUID;
  invoice_number: string;
  customer_id: UUID;
  customer_name: string;
  invoice_date: DateString;
  due_date: DateString;
  total: DecimalString;
  amount_due: DecimalString;
  status: string;
}

/** reports/selectors/payables.py :: _bill_row */
export interface BillReportRow {
  bill_id: UUID;
  bill_number: string;
  vendor_id: UUID;
  vendor_name: string;
  bill_date: DateString;
  due_date: DateString;
  total: DecimalString;
  amount_due: DecimalString;
  status: string;
}

/**
 * Ageing bucket keys really are "1-30", "31-60", "61-90" and "90+" — hyphenated
 * strings, not identifiers. A wrong key reads as a missing value.
 */
export const AGEING_BUCKETS = ["current", "1-30", "31-60", "61-90", "90+"] as const;
export type AgeingBucket = (typeof AGEING_BUCKETS)[number];

export const AGEING_BUCKET_LABELS: Record<AgeingBucket, string> = {
  current: "Current",
  "1-30": "1–30 days",
  "31-60": "31–60 days",
  "61-90": "61–90 days",
  "90+": "90+ days",
};

/** Ageing rows are the document row plus the engine's days-past-due (0 when not due). */
export type InvoiceAgeingRow = InvoiceReportRow & { days_overdue: number };
export type BillAgeingRow = BillReportRow & { days_overdue: number };

/** GET /reports/receivables/ageing/ and /reports/payables/ageing/ ?as_of_date */
export interface AgeingReport<Row = InvoiceAgeingRow | BillAgeingRow> {
  as_of: DateString;
  /** Rows per bucket, for drill-down. */
  buckets: Record<AgeingBucket, Row[]>;
  totals: Record<AgeingBucket, DecimalString>;
  /** The overall figure lives here, not in `totals`. */
  grand_total: DecimalString;
}

export type ReceivablesAgeing = AgeingReport<InvoiceAgeingRow>;
export type PayablesAgeing = AgeingReport<BillAgeingRow>;

/** GET /reports/receivables/customer-balances/ — no params, no totals. */
export interface CustomerBalanceRow {
  customer_id: UUID;
  customer_name: string;
  balance: DecimalString;
}

/** GET /reports/payables/vendor-balances/ — no params, no totals. */
export interface VendorBalanceRow {
  vendor_id: UUID;
  vendor_name: string;
  balance: DecimalString;
}

// --- Sales / purchases / expenses -------------------------------------------------------

/** GET /reports/sales/by-customer/?from_date&to_date (invoice_date) */
export interface SalesByCustomerRow {
  customer_id: UUID;
  customer_name: string;
  invoice_count: number;
  taxable_value: DecimalString;
  tax_total: DecimalString;
  total_sales: DecimalString;
}

/** GET /reports/sales/by-item/?from_date&to_date */
export interface SalesByItemRow {
  item_id: UUID;
  item_name: string;
  item_sku: string;
  /** Four decimal places. */
  quantity: DecimalString;
  taxable_value: DecimalString;
  tax_total: DecimalString;
  total_sales: DecimalString;
}

/** GET /reports/purchases/by-vendor/?from_date&to_date (bill_date) */
export interface PurchasesByVendorRow {
  vendor_id: UUID;
  vendor_name: string;
  bill_count: number;
  taxable_value: DecimalString;
  tax_total: DecimalString;
  total_purchases: DecimalString;
}

/** GET /reports/purchases/by-item/?from_date&to_date */
export interface PurchasesByItemRow {
  item_id: UUID;
  item_name: string;
  item_sku: string;
  quantity: DecimalString;
  taxable_value: DecimalString;
  tax_total: DecimalString;
  total_purchases: DecimalString;
}

/**
 * GET /reports/expenses/by-category/?from_date&to_date — "category" is the
 * expense's `expense_account`; there is no separate category field.
 */
export interface ExpenseCategoryRow {
  account_id: UUID;
  account_code: string;
  account_name: string;
  expense_count: number;
  tax_total: DecimalString;
  total_amount: DecimalString;
}

// Inventory report rows (valuation/low-stock/movements/adjustments) are typed
// in src/types/api/inventory.ts — InventoryValuation, LowStockItemRow,
// StockMovement, StockAdjustment — all captured from live responses.

// --- Projects -------------------------------------------------------------------------------

/**
 * GET /reports/projects/profitability/?status — one row per project, straight
 * from projects.selectors.get_project_profitability. Gated on
 * VIEW_ALL_TIMESHEETS (margin exposes labour cost), not VIEW_PROJECTS.
 */
export interface ProjectProfitabilityRow {
  project_id: UUID;
  project_code: string;
  name: string;
  status: string;
  billing_method: string;
  revenue: DecimalString;
  unbilled_value: DecimalString;
  labour_cost: DecimalString;
  expense_cost: DecimalString;
  total_cost: DecimalString;
  margin: DecimalString;
  /** Null when revenue is zero. */
  margin_percent: DecimalString | null;
  budget_amount: DecimalString | null;
  budget_hours: DecimalString | null;
  hours_over_budget: DecimalString;
  total_hours: DecimalString;
  billable_hours: DecimalString;
  non_billable_hours: DecimalString;
  approved_hours: DecimalString;
  invoiced_hours: DecimalString;
  pending_approval_hours: DecimalString;
}

// --- GST / tax (compliance.selectors, passed through verbatim) ------------------------------

/**
 * The amount block every GST summary is built from
 * (compliance/selectors.py :: _zero_bucket).
 */
export interface GstBucket {
  taxable_value: DecimalString;
  cgst: DecimalString;
  sgst: DecimalString;
  igst: DecimalString;
  cess: DecimalString;
  document_count: number;
}

/** The register totals in the GST summary carry no document count. */
export type GstTaxTotals = Omit<GstBucket, "document_count">;

/**
 * Note the key names: GSTR-1/3B summaries echo `{from, to}`, while the
 * registers and the combined GST summary echo `{from_date, to_date}`.
 */
export interface GstReturnPeriod {
  from: DateString;
  to: DateString;
}

/**
 * GET /reports/tax/gstr1-summary/?from_date&to_date (both required, else 400
 * `date_range_required`).
 *
 * Keyed maps: `b2b` is keyed by customer GSTIN, `exports` by
 * "with_payment"/"without_payment", `b2c_small` by place-of-supply id ("" when
 * unset), `credit_debit_notes` by "registered"/"unregistered", and each
 * `hsn_summary` half by "<hsn>|<rate>" (hsn may be "").
 */
export interface Gstr1Summary {
  period: GstReturnPeriod;
  b2b: Record<string, GstBucket>;
  b2c_large: GstBucket;
  exports: Record<string, GstBucket>;
  b2c_small: Record<string, GstBucket>;
  nil_exempt: GstBucket;
  credit_debit_notes: Record<string, GstBucket>;
  hsn_summary: { b2b: Record<string, GstBucket>; b2c: Record<string, GstBucket> };
}

/** GET /reports/tax/gstr3b-summary/?from_date&to_date */
export interface Gstr3bSummary {
  period: GstReturnPeriod;
  outward: {
    taxable: GstBucket;
    zero_rated: GstBucket;
    nil_exempt: GstBucket;
    inward_reverse_charge: GstBucket;
  };
  /** Keyed by place-of-supply id ("" when unset). */
  inter_state_unregistered: Record<string, GstBucket>;
  itc: { all_other: GstBucket; reverse_charge: GstBucket };
}

/** GET /reports/tax/gst-summary/?from_date&to_date */
export interface GstSummary {
  period: { from_date: DateString; to_date: DateString };
  output_tax: GstTaxTotals;
  input_tax: GstTaxTotals;
  gstr1: Gstr1Summary;
  gstr3b: Gstr3bSummary;
}

/**
 * One output-register line. Credit notes appear with NEGATIVE amounts in the
 * same list, so the register nets to the period's liability.
 */
export interface OutputTaxRegisterRow {
  document_type: "invoice" | "credit_note" | string;
  document_id: UUID;
  document_number: string;
  document_date: DateString;
  party: string;
  gstin: string;
  /** Place-of-supply id, or null. */
  place_of_supply: string | null;
  supply_nature: string;
  is_reverse_charge: boolean;
  taxable_value: DecimalString;
  cgst: DecimalString;
  sgst: DecimalString;
  igst: DecimalString;
  cess: DecimalString;
  total: DecimalString;
}

/** One input-register line — bills only (expenses are not in the register). */
export interface InputTaxRegisterRow extends OutputTaxRegisterRow {
  vendor_bill_number: string;
}

/** GET /reports/tax/output-register/ and /input-register/ — rows plus the echoed period; no totals. */
export interface TaxRegister<Row> {
  period: { from_date: DateString; to_date: DateString };
  rows: Row[];
}
