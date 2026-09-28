import type { DateString, DateTimeString, DecimalString, UUID } from "@/lib/api/types";

/**
 * Purchases API contracts (backend/purchases/api/serializers.py).
 *
 * REGENERATED, not hand-remembered: every interface below was transcribed
 * from `scripts/dump-serializers.py` output for MODULES=purchases and checked
 * against live responses from the dev tenant (vendor, PO, goods receipt,
 * bill, expense, vendor payment, vendor credit, both recurring templates, and
 * both three-way-match endpoints). What the earlier hand-written file had
 * wrong is listed in the purchases agent report; the notable ones are marked
 * inline.
 *
 * Conventions that hold for every resource here:
 *  - READ serializers return relations as bare ids under the relation name
 *    (`vendor`, `payable_account`); WRITE serializers take `_id` suffixes
 *    (`vendor_id`, `payable_account_id`). Vendor is the one exception — it is
 *    a ModelSerializer, read and written under the same keys.
 *  - Money, quantities and rates are decimal STRINGS.
 *  - Totals are never accepted from the client; the engine recomputes them.
 */

/** Address JSON on a vendor. Free-form on the backend (a JSONField). */
export interface PartyAddress {
  line1?: string;
  line2?: string;
  city?: string;
  state?: string;
  state_code?: string;
  postal_code?: string;
  country?: string;
}

// ------------------------------------------------------------------ vendors

/** purchases.VendorSerializer — a ModelSerializer: read AND write shape. */
export interface Vendor {
  id: UUID;
  vendor_code: string;
  display_name: string;
  legal_name: string;
  email: string;
  phone: string;
  gstin: string;
  pan: string;
  billing_address: PartyAddress;
  shipping_address: PartyAddress;
  currency: string;
  /** 0 = due on receipt. */
  payment_terms_days: number;
  /** A convenience default a form may read. Never applied by the backend on its own. */
  default_payable_account: UUID | null;
  is_active: boolean;
  notes: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** POST /purchases/vendors/, PATCH /purchases/vendors/{id}/. */
export interface VendorInput {
  vendor_code: string;
  display_name: string;
  currency: string;
  legal_name?: string;
  email?: string;
  phone?: string;
  gstin?: string;
  pan?: string;
  billing_address?: PartyAddress;
  shipping_address?: PartyAddress;
  payment_terms_days?: number;
  /** Must be a LIABILITY account (services/vendors.py). */
  default_payable_account?: UUID | null;
  is_active?: boolean;
  notes?: string;
}

// ------------------------------------------------------------ priced lines

/** Read shape shared by purchase order, bill and vendor credit lines. */
export interface PurchasePricedLine {
  id: UUID;
  line_number: number;
  item: UUID;
  description: string;
  hsn_sac_snapshot: string;
  tax_label: string;
  quantity: DecimalString;
  unit_price: DecimalString;
  discount_percent: DecimalString;
  tax_rate: DecimalString;
  line_base: DecimalString;
  discount_amount: DecimalString;
  taxable_amount: DecimalString;
  tax_amount: DecimalString;
  line_total: DecimalString;
}

/** purchases.PricedLineInputSerializer. */
export interface PurchasePricedLineInput {
  item_id: UUID;
  description?: string;
  quantity: DecimalString;
  unit_price: DecimalString;
  discount_percent?: DecimalString;
  tax_rate?: DecimalString;
}

interface DocumentTotalsFields {
  subtotal: DecimalString;
  discount_total: DecimalString;
  tax_total: DecimalString;
  total: DecimalString;
}

// --------------------------------------------------------- purchase orders

export type PurchaseOrderStatus =
  | "draft"
  | "approved"
  | "partially_received"
  | "received"
  | "closed"
  | "cancelled";

/** purchases.PurchaseOrderSerializer */
export interface PurchaseOrder extends DocumentTotalsFields {
  id: UUID;
  vendor: UUID;
  /** Allocated at creation ("PO-0001"), unlike a bill's number. */
  order_number: string;
  status: PurchaseOrderStatus;
  order_date: DateString;
  expected_date: DateString | null;
  reference: string;
  warehouse: UUID | null;
  currency: string;
  exchange_rate: DecimalString;
  notes: string;
  terms: string;
  lines: PurchasePricedLine[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.PurchaseOrderCreateSerializer */
export interface PurchaseOrderInput {
  vendor_id: UUID;
  order_date: DateString;
  expected_date?: DateString | null;
  warehouse_id?: UUID | null;
  currency?: string;
  reference?: string;
  notes?: string;
  terms?: string;
  lines: PurchasePricedLineInput[];
}

/**
 * PATCH /purchases/orders/{id}/ — DRAFT only (purchase_order_not_draft).
 * PurchaseOrderDetailView.update reads exactly these header keys plus
 * `lines`; vendor and warehouse are fixed at creation.
 */
export interface PurchaseOrderUpdateInput {
  order_date?: DateString;
  expected_date?: DateString | null;
  reference?: string;
  notes?: string;
  terms?: string;
  lines?: PurchasePricedLineInput[];
}

// ---------------------------------------------------------- goods receipts

export type GoodsReceiptStatus = "draft" | "received" | "cancelled";

/** purchases.GoodsReceiptLineSerializer */
export interface GoodsReceiptLine {
  id: UUID;
  item: UUID;
  source_order_line: UUID | null;
  line_number: number;
  description: string;
  quantity: DecimalString;
  /** 4 dp. Defaults to the linked PO line's unit price when not supplied. */
  unit_cost: DecimalString;
}

/** purchases.GoodsReceiptSerializer */
export interface GoodsReceipt {
  id: UUID;
  vendor: UUID;
  /** Allocated at creation ("GR-0001"). */
  receipt_number: string;
  status: GoodsReceiptStatus;
  source_purchase_order: UUID | null;
  warehouse: UUID;
  receipt_date: DateString;
  vendor_document_number: string;
  notes: string;
  received_at: DateTimeString | null;
  lines: GoodsReceiptLine[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.GoodsReceiptLineInputSerializer */
export interface GoodsReceiptLineInput {
  item_id: UUID;
  source_order_line_id?: UUID | null;
  description?: string;
  quantity: DecimalString;
  /** Required unless the line links a PO line (receipt_unit_cost_required). */
  unit_cost?: DecimalString | null;
}

/** purchases.GoodsReceiptCreateSerializer */
export interface GoodsReceiptInput {
  vendor_id: UUID;
  warehouse_id: UUID;
  receipt_date: DateString;
  source_purchase_order_id?: UUID | null;
  vendor_document_number?: string;
  notes?: string;
  lines: GoodsReceiptLineInput[];
}

/** PATCH /purchases/goods-receipts/{id}/ — DRAFT only; these keys plus `lines`. */
export interface GoodsReceiptUpdateInput {
  receipt_date?: DateString;
  vendor_document_number?: string;
  notes?: string;
  lines?: GoodsReceiptLineInput[];
}

/** POST /purchases/goods-receipts/{id}/convert-to-bill/ — BillFromGoodsReceiptSerializer. Returns a Bill. */
export interface BillFromGoodsReceiptInput {
  bill_date: DateString;
  due_date: DateString;
  payable_account_id: UUID;
  tax_recoverable_account_id?: UUID | null;
  price_variance_account_id?: UUID | null;
  vendor_bill_number?: string;
}

// ------------------------------------------------------------------- bills

/** "overdue" is in the enum but never stored — lateness is derived from due_date. */
export type BillStatus = "draft" | "open" | "partially_paid" | "paid" | "overdue" | "void";

/** purchases.BillLineSerializer */
export interface BillLine extends PurchasePricedLine {
  source_goods_receipt_line: UUID | null;
  source_order_line: UUID | null;
  /** Only meaningful on non-inventoried lines; null falls back to the item's purchase_account. */
  expense_account: UUID | null;
}

/**
 * purchases.BillSerializer.
 *
 * There is NO posted_at / voided_at / void_reason on this serializer (the
 * model stores them, the API does not expose them), and `bill_number` is ""
 * until the bill is posted.
 */
export interface Bill extends DocumentTotalsFields {
  id: UUID;
  vendor: UUID;
  bill_number: string;
  vendor_bill_number: string;
  status: BillStatus;
  source_purchase_order: UUID | null;
  bill_date: DateString;
  due_date: DateString;
  reference: string;
  currency: string;
  exchange_rate: DecimalString;
  payable_account: UUID;
  tax_recoverable_account: UUID | null;
  price_variance_account: UUID | null;
  warehouse: UUID | null;
  accounting_journal: UUID | null;
  /** Derived on read from payment allocations. Authoritative; never recomputed here. */
  amount_paid: DecimalString;
  /** total − paid − vendor credits applied. Authoritative. */
  amount_due: DecimalString;
  notes: string;
  lines: BillLine[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.BillLineInputSerializer */
export interface BillLineInput extends PurchasePricedLineInput {
  source_goods_receipt_line_id?: UUID | null;
  source_order_line_id?: UUID | null;
  expense_account_id?: UUID | null;
}

/** purchases.BillCreateSerializer */
export interface BillInput {
  vendor_id: UUID;
  bill_date: DateString;
  due_date: DateString;
  /** LIABILITY. */
  payable_account_id: UUID;
  vendor_bill_number?: string;
  reference?: string;
  source_purchase_order_id?: UUID | null;
  /** Required when an inventoried line has no linked goods receipt line (warehouse_required). */
  warehouse_id?: UUID | null;
  /** ASSET. */
  tax_recoverable_account_id?: UUID | null;
  /** EXPENSE. Required at posting only when a receipt-linked line is billed at a different cost. */
  price_variance_account_id?: UUID | null;
  currency?: string;
  notes?: string;
  lines: BillLineInput[];
}

/** PATCH /purchases/bills/{id}/ — DRAFT only (bill_not_draft); these keys plus `lines`. */
export interface BillUpdateInput {
  bill_date?: DateString;
  due_date?: DateString;
  reference?: string;
  vendor_bill_number?: string;
  notes?: string;
  lines?: BillLineInput[];
}

// ---------------------------------------------------------------- expenses

export type ExpenseStatus = "draft" | "posted" | "void";

/**
 * purchases.ExpenseSerializer. Single-amount document: there are no lines.
 * `tax_amount` and `total` are computed by the service from amount/tax_rate.
 * No posted_at / void_reason exposed.
 */
export interface Expense {
  id: UUID;
  vendor: UUID | null;
  /** "" until posted. */
  expense_number: string;
  status: ExpenseStatus;
  expense_date: DateString;
  reference: string;
  description: string;
  currency: string;
  exchange_rate: DecimalString;
  expense_account: UUID;
  paid_through_account: UUID;
  tax_recoverable_account: UUID | null;
  accounting_journal: UUID | null;
  amount: DecimalString;
  tax_rate: DecimalString;
  tax_amount: DecimalString;
  total: DecimalString;
  is_billable: boolean;
  customer: UUID | null;
  project: UUID | null;
  notes: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.ExpenseCreateSerializer */
export interface ExpenseInput {
  expense_date: DateString;
  amount: DecimalString;
  /** EXPENSE. */
  expense_account_id: UUID;
  /** ASSET (paid now) or LIABILITY (payable later). */
  paid_through_account_id: UUID;
  vendor_id?: UUID | null;
  tax_rate?: DecimalString;
  /** ASSET. Needed at posting when tax_rate > 0 (tax_account_required). */
  tax_recoverable_account_id?: UUID | null;
  currency?: string;
  reference?: string;
  description?: string;
  /** Requires customer_id (billable_customer_required). */
  is_billable?: boolean;
  customer_id?: UUID | null;
  project_id?: UUID | null;
  notes?: string;
}

/** purchases.ExpenseUpdateSerializer — DRAFT only. Accounts and parties are fixed at creation. */
export interface ExpenseUpdateInput {
  expense_date?: DateString;
  amount?: DecimalString;
  tax_rate?: DecimalString;
  reference?: string;
  description?: string;
  is_billable?: boolean;
  notes?: string;
}

// ---------------------------------------------------------------- payments

/** core/enums.py :: PaymentMethod */
export type PaymentMethod = "cash" | "bank_transfer" | "cheque" | "card" | "upi" | "other";

/** purchases.VendorPaymentAllocationSerializer. `bill: null` is the vendor-advance remainder. */
export interface VendorPaymentAllocation {
  id: UUID;
  bill: UUID | null;
  amount: DecimalString;
}

/** purchases.VendorPaymentSerializer — every field read-only; payments are append-only. */
export interface VendorPayment {
  id: UUID;
  vendor: UUID;
  payment_number: string;
  payment_date: DateString;
  amount: DecimalString;
  currency: string;
  payment_method: PaymentMethod;
  reference: string;
  source_account: UUID;
  accounting_journal: UUID | null;
  notes: string;
  allocations: VendorPaymentAllocation[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.VendorPaymentCreateSerializer */
export interface VendorPaymentInput {
  vendor_id: UUID;
  payment_date: DateString;
  amount: DecimalString;
  /** ASSET (bank/cash). */
  source_account_id: UUID;
  /** Bills must be open or partially_paid (bill_not_payable) and not over-allocated. */
  allocations?: Array<{ bill_id: UUID; amount: DecimalString }>;
  /** ASSET. Required when amount exceeds the allocations (vendor_advance_account_required). */
  vendor_advance_account_id?: UUID | null;
  currency?: string;
  payment_method?: PaymentMethod;
  reference?: string;
  notes?: string;
}

// ---------------------------------------------------------- vendor credits

export type VendorCreditStatus = "draft" | "issued" | "void";
/** purchases/models/vendor_credit.py :: VendorCreditReason */
export type VendorCreditReason = "return" | "pricing_error" | "discount" | "damaged" | "other";

/** purchases.VendorCreditLineSerializer */
export interface VendorCreditLine extends PurchasePricedLine {
  source_bill_line: UUID | null;
  expense_account: UUID | null;
  /** True moves the quantity OUT of `warehouse` on issue. */
  return_stock: boolean;
  /** 4 dp; required when return_stock. */
  unit_cost: DecimalString | null;
}

/** purchases.VendorCreditSerializer. `credit_number` is "" until issued. */
export interface VendorCredit extends DocumentTotalsFields {
  id: UUID;
  vendor: UUID;
  credit_number: string;
  vendor_credit_number: string;
  status: VendorCreditStatus;
  reason: VendorCreditReason;
  source_bill: UUID | null;
  credit_date: DateString;
  reference: string;
  currency: string;
  exchange_rate: DecimalString;
  payable_account: UUID | null;
  tax_recoverable_account: UUID | null;
  unapplied_credit_account: UUID | null;
  warehouse: UUID | null;
  accounting_journal: UUID | null;
  /** Frozen at issue: the part netted against the source bill's balance. */
  amount_applied_to_bill: DecimalString;
  notes: string;
  lines: VendorCreditLine[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.VendorCreditLineInputSerializer */
export interface VendorCreditLineInput extends PurchasePricedLineInput {
  source_bill_line_id?: UUID | null;
  expense_account_id?: UUID | null;
  return_stock?: boolean;
  unit_cost?: DecimalString | null;
}

/** purchases.VendorCreditCreateSerializer */
export interface VendorCreditInput {
  vendor_id: UUID;
  credit_date: DateString;
  source_bill_id?: UUID | null;
  reason?: VendorCreditReason;
  vendor_credit_number?: string;
  reference?: string;
  /** LIABILITY. Falls back to the source bill's on issue. */
  payable_account_id?: UUID | null;
  /** ASSET. */
  tax_recoverable_account_id?: UUID | null;
  /** ASSET. Required on issue when the credit exceeds the bill's balance. */
  unapplied_credit_account_id?: UUID | null;
  /** Required when any line returns stock. */
  warehouse_id?: UUID | null;
  currency?: string;
  notes?: string;
  lines: VendorCreditLineInput[];
}

/** PATCH /purchases/vendor-credits/{id}/ — DRAFT only; these keys plus `lines`. */
export interface VendorCreditUpdateInput {
  credit_date?: DateString;
  reference?: string;
  vendor_credit_number?: string;
  reason?: VendorCreditReason;
  notes?: string;
  lines?: VendorCreditLineInput[];
}

// --------------------------------------------------------------- recurring

export type RecurringFrequency = "weekly" | "monthly" | "quarterly" | "yearly";

/** purchases.RecurringBillTemplateLineSerializer — inputs only, no computed amounts. */
export interface RecurringBillTemplateLine {
  id: UUID;
  item: UUID;
  expense_account: UUID | null;
  line_number: number;
  description: string;
  quantity: DecimalString;
  unit_price: DecimalString;
  discount_percent: DecimalString;
  tax_rate: DecimalString;
}

/** purchases.RecurringBillTemplateSerializer. No template-level totals exist. */
export interface RecurringBillTemplate {
  id: UUID;
  vendor: UUID;
  frequency: RecurringFrequency;
  start_date: DateString;
  end_date: DateString | null;
  /** A DATE despite the name. */
  next_run_at: DateString;
  is_active: boolean;
  due_days: number;
  payable_account: UUID;
  tax_recoverable_account: UUID | null;
  currency: string;
  exchange_rate: DecimalString;
  reference: string;
  notes: string;
  lines: RecurringBillTemplateLine[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.RecurringBillTemplateLineInputSerializer. Inventoried items are rejected (recurring_item_inventoried). */
export interface RecurringBillTemplateLineInput extends PurchasePricedLineInput {
  expense_account_id?: UUID | null;
}

/** purchases.RecurringBillTemplateCreateSerializer */
export interface RecurringBillTemplateInput {
  vendor_id: UUID;
  frequency: RecurringFrequency;
  start_date: DateString;
  end_date?: DateString | null;
  due_days?: number;
  payable_account_id: UUID;
  tax_recoverable_account_id?: UUID | null;
  currency?: string;
  reference?: string;
  notes?: string;
  is_active?: boolean;
  lines: RecurringBillTemplateLineInput[];
}

/** purchases.RecurringBillTemplateUpdateSerializer — any status; vendor, start date and accounts are fixed. */
export interface RecurringBillTemplateUpdateInput {
  frequency?: RecurringFrequency;
  end_date?: DateString | null;
  due_days?: number;
  reference?: string;
  notes?: string;
  is_active?: boolean;
  lines?: RecurringBillTemplateLineInput[];
}

/** purchases.RecurringExpenseTemplateSerializer */
export interface RecurringExpenseTemplate {
  id: UUID;
  vendor: UUID | null;
  frequency: RecurringFrequency;
  start_date: DateString;
  end_date: DateString | null;
  next_run_at: DateString;
  is_active: boolean;
  expense_account: UUID;
  paid_through_account: UUID;
  tax_recoverable_account: UUID | null;
  currency: string;
  exchange_rate: DecimalString;
  amount: DecimalString;
  tax_rate: DecimalString;
  description: string;
  reference: string;
  notes: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** purchases.RecurringExpenseTemplateCreateSerializer */
export interface RecurringExpenseTemplateInput {
  frequency: RecurringFrequency;
  start_date: DateString;
  amount: DecimalString;
  expense_account_id: UUID;
  paid_through_account_id: UUID;
  vendor_id?: UUID | null;
  end_date?: DateString | null;
  tax_rate?: DecimalString;
  tax_recoverable_account_id?: UUID | null;
  currency?: string;
  description?: string;
  reference?: string;
  notes?: string;
  is_active?: boolean;
}

/** purchases.RecurringExpenseTemplateUpdateSerializer */
export interface RecurringExpenseTemplateUpdateInput {
  frequency?: RecurringFrequency;
  end_date?: DateString | null;
  amount?: DecimalString;
  tax_rate?: DecimalString;
  description?: string;
  reference?: string;
  notes?: string;
  is_active?: boolean;
}

// -------------------------------------------------------- three-way match

/** services/three_way_match.py :: MatchException */
export type MatchExceptionCode =
  | "quantity_over_received"
  | "quantity_over_billed"
  | "billed_not_received"
  | "received_not_billed"
  | "price_mismatch"
  | "not_on_order";

/** GET /purchases/orders/{id}/match/ and /bills/{id}/match/ query (ThreeWayMatchQuerySerializer). Both default to 0 = exact. */
export interface ThreeWayMatchQuery {
  quantity_tolerance?: DecimalString;
  price_tolerance?: DecimalString;
}

/**
 * One PO line in GET /purchases/orders/{id}/match/ (a plain dict from
 * match_purchase_order, not a serializer). Received and billed quantities
 * count only RECEIVED receipts and non-draft, non-void bills.
 */
export interface PurchaseOrderMatchLine {
  line_id: UUID;
  line_number: number;
  item_id: UUID;
  description: string;
  ordered_quantity: DecimalString;
  received_quantity: DecimalString;
  billed_quantity: DecimalString;
  ordered_unit_price: DecimalString;
  billed_unit_prices: DecimalString[];
  max_price_variance: DecimalString;
  exceptions: MatchExceptionCode[];
}

/** A line on a bill raised against the PO that points at no PO line. */
export interface UnorderedBillLine {
  bill_id: UUID;
  bill_number: string;
  line_number: number;
  item_id: UUID;
  description: string;
  quantity: DecimalString;
  unit_price: DecimalString;
  line_total: DecimalString;
}

export interface PurchaseOrderMatch {
  purchase_order_id: UUID;
  order_number: string;
  status: PurchaseOrderStatus;
  matched: boolean;
  exceptions: MatchExceptionCode[];
  lines: PurchaseOrderMatchLine[];
  unordered_bill_lines: UnorderedBillLine[];
}

/** One bill line in GET /purchases/bills/{id}/match/. Order-side fields are null when the line links no PO line. */
export interface BillMatchLine {
  line_id: UUID;
  line_number: number;
  item_id: UUID;
  description: string;
  billed_quantity: DecimalString;
  billed_unit_price: DecimalString;
  ordered_quantity: DecimalString | null;
  ordered_unit_price: DecimalString | null;
  received_quantity: DecimalString | null;
  price_variance: DecimalString;
  goods_receipt_line_id: UUID | null;
  exceptions: MatchExceptionCode[];
}

/** A bill with no source purchase order matches vacuously (`matched: true`, no exceptions). */
export interface BillMatch {
  bill_id: UUID;
  bill_number: string;
  status: BillStatus;
  purchase_order_id: UUID | null;
  matched: boolean;
  exceptions: MatchExceptionCode[];
  lines: BillMatchLine[];
}
