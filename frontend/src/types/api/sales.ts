import type {
  DateString,
  DateTimeString,
  DecimalString,
  UUID,
} from "@/lib/api/types";

/**
 * Sales contracts — regenerated from backend/sales/api/serializers.py with
 * scripts/dump-serializers.py (MODULES=sales), not guessed. An earlier,
 * hand-written version of this file had the wrong input names throughout
 * (`customer` for `customer_id`, `quote_date` for `issue_date`,
 * `deposit_account` for `destination_account`), every one of which the API
 * would have rejected.
 *
 * Conventions:
 *  - READ serializers return relations as bare ids (`customer`), WRITE
 *    serializers take them with an `_id` suffix (`customer_id`).
 *  - Every amount is a DecimalField, serialized as a STRING. Money has 2
 *    places, quantities and unit costs 4, exchange rates 8, percentages 2.
 *  - `currency` is the Currency primary key, which is its ISO code ("INR").
 *  - Lines are replaced wholesale on update (`*LinesUpdateSerializer`); only
 *    DRAFT documents accept an update at all.
 *
 * Re-verified 2026-09-17 against a fresh serializer dump AND live responses
 * (quote → send → accept → convert, order confirm, challan dispatch/deliver,
 * payment, credit note issue, recurring activate):
 *  - Every transition endpoint (send/accept/reject/cancel, confirm,
 *    dispatch/deliver, issue/void, activate/deactivate) returns the SAME read
 *    serializer as the detail endpoint, with status 200.
 *  - Quote, order, challan and payment numbers are allocated on CREATE;
 *    invoice and credit-note numbers only on post/issue (blank until then).
 *  - `amount_paid` is a SerializerMethodField and comes back as "0" (no
 *    fixed scale) before any payment.
 */

/** JSON address blob; the backend stores it unvalidated as JSONField. */
export interface Address {
  line1?: string;
  line2?: string;
  city?: string;
  state?: string;
  state_code?: string;
  postal_code?: string;
  country?: string;
}

// ---------------------------------------------------------------- customers

/** sales.CustomerSerializer */
export interface Customer {
  id: UUID;
  customer_code: string;
  display_name: string;
  legal_name: string;
  email: string;
  phone: string;
  gstin: string;
  pan: string;
  currency: string;
  payment_terms_days: number;
  credit_limit: DecimalString | null;
  billing_address: Address | null;
  shipping_address: Address | null;
  notes: string;
  is_active: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

export interface CustomerInput {
  customer_code: string;
  display_name: string;
  legal_name?: string;
  email?: string;
  phone?: string;
  gstin?: string;
  pan?: string;
  currency: string;
  payment_terms_days?: number;
  credit_limit?: DecimalString | null;
  billing_address?: Address | null;
  shipping_address?: Address | null;
  notes?: string;
  is_active?: boolean;
}

// -------------------------------------------------------------- priced lines

/**
 * The read shape shared by quote, order, invoice and credit note lines. Every
 * computed figure — base, discount, taxable, tax, total — is the backend's.
 */
export interface PricedLine {
  id: UUID;
  line_number: number;
  item: UUID;
  description: string;
  quantity: DecimalString;
  unit_price: DecimalString;
  discount_percent: DecimalString;
  discount_amount: DecimalString;
  /** Quantity × unit price, before discount. */
  line_base: DecimalString;
  taxable_amount: DecimalString;
  tax_rate: DecimalString;
  tax_amount: DecimalString;
  /** e.g. "CGST+SGST" or "IGST", decided by place of supply on the server. */
  tax_label: string;
  hsn_sac_snapshot: string;
  line_total: DecimalString;
}

/** The write shape shared by quote, order, invoice and recurring lines. */
export interface PricedLineInput {
  item_id: UUID;
  description?: string;
  quantity: DecimalString;
  unit_price: DecimalString;
  discount_percent?: DecimalString;
  tax_rate?: DecimalString;
}

/** Document totals, all read-only and all computed by the backend. */
export interface DocumentTotals {
  subtotal: DecimalString;
  discount_total: DecimalString;
  tax_total: DecimalString;
  total: DecimalString;
}

// -------------------------------------------------------------------- quotes

export type QuoteStatus = "draft" | "sent" | "accepted" | "rejected" | "expired" | "cancelled";

export type QuoteLine = PricedLine;

/** sales.QuoteSerializer */
export interface Quote extends DocumentTotals {
  id: UUID;
  quote_number: string;
  customer: UUID;
  currency: string;
  exchange_rate: DecimalString;
  issue_date: DateString;
  expiry_date: DateString | null;
  status: QuoteStatus;
  lines: QuoteLine[];
  notes: string;
  terms: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** sales.QuoteCreateSerializer */
export interface QuoteInput {
  customer_id: UUID;
  currency?: string;
  exchange_rate?: DecimalString;
  issue_date: DateString;
  expiry_date?: DateString | null;
  notes?: string;
  terms?: string;
  lines: PricedLineInput[];
}

/** PATCH on a draft quote — views.py :: QuoteDetailView.update */
export interface QuoteUpdateInput {
  issue_date?: DateString;
  expiry_date?: DateString | null;
  notes?: string;
  terms?: string;
  lines?: PricedLineInput[];
}

/**
 * sales.QuoteConvertSerializer — POST sales/quotes/{id}/convert/.
 *
 * Only an ACCEPTED quote converts (quote_not_acceptable), and it converts at
 * most once PER TARGET (quote_already_converted): one order and one invoice
 * may both come from the same quote. `order_date`/`invoice_date` are NOT
 * nullable — sending null is a validation error — so omit them to default
 * to the quote's issue date. `due_date` and `receivable_account_id` are
 * required when the target is an invoice (serializer.validate).
 */
export type QuoteConvertInput =
  | { target: "sales_order"; order_date?: DateString }
  | {
      target: "invoice";
      invoice_date?: DateString;
      due_date: DateString;
      receivable_account_id: UUID;
      tax_payable_account_id?: UUID | null;
      warehouse_id?: UUID | null;
    };

/** 201 with InvoiceSerializer or SalesOrderSerializer — the created DRAFT document. */
export type QuoteConvertResult = Invoice | SalesOrder;

// -------------------------------------------------------------- sales orders

export type SalesOrderStatus = "draft" | "confirmed" | "partially_fulfilled" | "fulfilled" | "cancelled";

/**
 * sales.SalesOrderLineSerializer. There is NO fulfilled/delivered quantity on
 * the line: the backend derives it on demand (sales/selectors.py ::
 * get_fulfilled_quantity — challan lines of DISPATCHED or DELIVERED challans)
 * and never serializes it.
 */
export type SalesOrderLine = PricedLine;

/** sales.SalesOrderSerializer */
export interface SalesOrder extends DocumentTotals {
  id: UUID;
  order_number: string;
  customer: UUID;
  currency: string;
  exchange_rate: DecimalString;
  order_date: DateString;
  status: SalesOrderStatus;
  source_quote: UUID | null;
  lines: SalesOrderLine[];
  notes: string;
  terms: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** sales.SalesOrderCreateSerializer */
export interface SalesOrderInput {
  customer_id: UUID;
  currency?: string;
  exchange_rate?: DecimalString;
  order_date: DateString;
  notes?: string;
  terms?: string;
  lines: PricedLineInput[];
}

/** PATCH on a draft order — views.py :: SalesOrderDetailView.update */
export interface SalesOrderUpdateInput {
  order_date?: DateString;
  notes?: string;
  terms?: string;
  lines?: PricedLineInput[];
}

// ------------------------------------------------------------ delivery challans

export type DeliveryStatus = "draft" | "dispatched" | "delivered" | "cancelled";

/** sales.DeliveryChallanLineSerializer — quantities only, no prices. */
export interface DeliveryChallanLine {
  id: UUID;
  line_number: number;
  item: UUID;
  description: string;
  quantity: DecimalString;
  source_order_line: UUID | null;
}

/** sales.DeliveryChallanSerializer */
export interface DeliveryChallan {
  id: UUID;
  challan_number: string;
  customer: UUID;
  source_sales_order: UUID | null;
  warehouse: UUID;
  challan_date: DateString;
  status: DeliveryStatus;
  lines: DeliveryChallanLine[];
  notes: string;
  dispatched_at: DateTimeString | null;
  delivered_at: DateTimeString | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** sales.DeliveryChallanLineInputSerializer */
export interface DeliveryChallanLineInput {
  item_id: UUID;
  description?: string;
  quantity: DecimalString;
  source_order_line_id?: UUID | null;
}

/** sales.DeliveryChallanCreateSerializer */
export interface DeliveryChallanInput {
  customer_id: UUID;
  warehouse_id: UUID;
  challan_date: DateString;
  source_sales_order_id?: UUID | null;
  notes?: string;
  lines: DeliveryChallanLineInput[];
}

/** PATCH on a draft challan — views.py :: DeliveryChallanDetailView.update */
export interface DeliveryChallanUpdateInput {
  challan_date?: DateString;
  notes?: string;
  lines?: DeliveryChallanLineInput[];
}

// ------------------------------------------------------------------ invoices

/**
 * sales/models/invoice.py :: InvoiceStatus. Nothing in the backend ever
 * stores "overdue" — lateness is derived from due_date by the reports — so a
 * late invoice stays "sent" or "partially_paid" here.
 */
export type InvoiceStatus = "draft" | "sent" | "partially_paid" | "paid" | "overdue" | "void";

export interface InvoiceLine extends PricedLine {
  source_delivery_challan_line: UUID | null;
}

/** sales.InvoiceSerializer */
export interface Invoice extends DocumentTotals {
  id: UUID;
  invoice_number: string;
  customer: UUID;
  currency: string;
  exchange_rate: DecimalString;
  invoice_date: DateString;
  due_date: DateString;
  reference: string;
  status: InvoiceStatus;
  lines: InvoiceLine[];
  /** SerializerMethodField — authoritative, never computed in the browser. */
  amount_paid: DecimalString;
  amount_due: DecimalString;
  receivable_account: UUID;
  tax_payable_account: UUID | null;
  warehouse: UUID | null;
  source_quote: UUID | null;
  notes: string;
  terms: string;
  posted_at: DateTimeString | null;
  posted_by: UUID | null;
  voided_at: DateTimeString | null;
  voided_by: UUID | null;
  void_reason: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

export interface InvoiceLineInput extends PricedLineInput {
  source_delivery_challan_line_id?: UUID | null;
}

/** sales.InvoiceCreateSerializer */
export interface InvoiceInput {
  customer_id: UUID;
  currency?: string;
  exchange_rate?: DecimalString;
  invoice_date: DateString;
  due_date: DateString;
  reference?: string;
  receivable_account_id: UUID;
  tax_payable_account_id?: UUID | null;
  warehouse_id?: UUID | null;
  notes?: string;
  terms?: string;
  lines: InvoiceLineInput[];
}

/**
 * PATCH on a draft invoice — views.py :: InvoiceDetailView.update. Customer,
 * accounts and warehouse are fixed once the draft exists.
 */
export interface InvoiceUpdateInput {
  invoice_date?: DateString;
  due_date?: DateString;
  reference?: string;
  notes?: string;
  terms?: string;
  lines?: InvoiceLineInput[];
}

// ------------------------------------------------------------------ payments

/**
 * sales.PaymentAllocationSerializer. `invoice: null` is the one row the
 * backend writes for any unallocated remainder — unapplied customer credit.
 */
export interface PaymentAllocation {
  id: UUID;
  invoice: UUID | null;
  amount: DecimalString;
}

export type PaymentMethod = "cash" | "bank_transfer" | "cheque" | "card" | "upi" | "other";

/** sales.CustomerPaymentSerializer — read-only once recorded (no update view). */
export interface CustomerPayment {
  id: UUID;
  payment_number: string;
  customer: UUID;
  currency: string;
  payment_date: DateString;
  amount: DecimalString;
  payment_method: PaymentMethod;
  reference: string;
  destination_account: UUID;
  allocations: PaymentAllocation[];
  notes: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** sales.CustomerPaymentCreateSerializer */
export interface CustomerPaymentInput {
  customer_id: UUID;
  currency?: string;
  payment_date: DateString;
  amount: DecimalString;
  /** Defaults to "other" on the server. */
  payment_method?: PaymentMethod;
  reference?: string;
  destination_account_id: UUID;
  /** Required by the service when the payment exceeds its allocations. */
  unapplied_credit_account_id?: UUID | null;
  notes?: string;
  allocations?: Array<{ invoice_id: UUID; amount: DecimalString }>;
}

// -------------------------------------------------------------- credit notes

export type CreditNoteStatus = "draft" | "issued" | "void";

export type CreditNoteReason = "return" | "pricing_error" | "discount" | "goodwill" | "other";

export interface CreditNoteLine extends PricedLine {
  /** Put the returned quantity back into stock when issued. */
  restock: boolean;
  unit_cost: DecimalString | null;
  source_invoice_line: UUID | null;
}

/**
 * sales.CreditNoteSerializer. The model's `amount_applied_to_invoice` (how
 * much of the total reduced the source invoice's balance on issue) and its
 * `accounting_journal` are NOT serialized.
 */
export interface CreditNote extends DocumentTotals {
  id: UUID;
  credit_note_number: string;
  customer: UUID;
  source_invoice: UUID | null;
  currency: string;
  exchange_rate: DecimalString;
  credit_note_date: DateString;
  reason: CreditNoteReason;
  reference: string;
  status: CreditNoteStatus;
  lines: CreditNoteLine[];
  receivable_account: UUID | null;
  tax_payable_account: UUID | null;
  unapplied_credit_account: UUID | null;
  warehouse: UUID | null;
  notes: string;
  issued_at: DateTimeString | null;
  issued_by: UUID | null;
  voided_at: DateTimeString | null;
  voided_by: UUID | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** sales.CreditNoteLineInputSerializer */
export interface CreditNoteLineInput extends PricedLineInput {
  restock?: boolean;
  unit_cost?: DecimalString | null;
  source_invoice_line_id?: UUID | null;
}

/** sales.CreditNoteCreateSerializer */
export interface CreditNoteInput {
  customer_id: UUID;
  currency?: string;
  exchange_rate?: DecimalString;
  credit_note_date: DateString;
  reason: CreditNoteReason;
  reference?: string;
  source_invoice_id?: UUID | null;
  receivable_account_id?: UUID | null;
  tax_payable_account_id?: UUID | null;
  unapplied_credit_account_id?: UUID | null;
  warehouse_id?: UUID | null;
  notes?: string;
  lines: CreditNoteLineInput[];
}

/** PATCH on a draft credit note — views.py :: CreditNoteDetailView.update */
export interface CreditNoteUpdateInput {
  credit_note_date?: DateString;
  reference?: string;
  notes?: string;
  lines?: CreditNoteLineInput[];
}

// ---------------------------------------------------------------- recurring

export type RecurringFrequency = "weekly" | "monthly" | "quarterly" | "yearly";

/** sales.RecurringInvoiceTemplateLineSerializer */
export interface RecurringInvoiceTemplateLine {
  id: UUID;
  line_number: number;
  item: UUID;
  description: string;
  quantity: DecimalString;
  unit_price: DecimalString;
  discount_percent: DecimalString;
  tax_rate: DecimalString;
}

/** sales.RecurringInvoiceTemplateSerializer */
export interface RecurringInvoiceTemplate {
  id: UUID;
  customer: UUID;
  currency: string;
  exchange_rate: DecimalString;
  frequency: RecurringFrequency;
  start_date: DateString;
  end_date: DateString | null;
  /** The next date the scheduler will generate an invoice for. */
  next_run_at: DateString;
  due_days: number;
  is_active: boolean;
  reference: string;
  receivable_account: UUID;
  tax_payable_account: UUID | null;
  warehouse: UUID | null;
  lines: RecurringInvoiceTemplateLine[];
  notes: string;
  terms: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** sales.RecurringInvoiceTemplateCreateSerializer */
export interface RecurringInvoiceTemplateInput {
  customer_id: UUID;
  currency?: string;
  exchange_rate?: DecimalString;
  frequency: RecurringFrequency;
  start_date: DateString;
  end_date?: DateString | null;
  due_days?: number;
  is_active?: boolean;
  reference?: string;
  receivable_account_id: UUID;
  tax_payable_account_id?: UUID | null;
  warehouse_id?: UUID | null;
  notes?: string;
  terms?: string;
  lines: PricedLineInput[];
}

/**
 * sales.RecurringInvoiceTemplateUpdateSerializer — PATCH on any template,
 * active or not. Unknown keys (customer, frequency, start_date, accounts,
 * is_active) are silently ignored, not rejected; activation has its own
 * endpoints. Unlike create, the update does NOT check end_date >= start_date.
 */
export interface RecurringInvoiceTemplateUpdateInput {
  due_days?: number;
  end_date?: DateString | null;
  reference?: string;
  notes?: string;
  terms?: string;
  lines?: PricedLineInput[];
}
