import { Badge, type BadgeTone } from "./badge";

/**
 * Document status presentation.
 *
 * Values are transcribed from the backend's own TextChoices — never invented
 * (spec §25, §47). If a status is not in this map the raw value is shown
 * rather than guessed at, which surfaces a backend change instead of hiding
 * it behind a plausible-looking grey chip.
 *
 * Every badge shows its LABEL as text and, for the states that carry money
 * consequences, a non-colour marker glyph. Colour is never the only signal
 * (spec §68).
 */

interface StatusMeta {
  label: string;
  tone: BadgeTone;
  /** Shows the shape marker. On for states an accountant must not misread. */
  marker?: boolean;
}

/** sales/models/quote.py :: QuoteStatus */
export const QUOTE_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  sent: { label: "Sent", tone: "info" },
  accepted: { label: "Accepted", tone: "success", marker: true },
  rejected: { label: "Rejected", tone: "danger", marker: true },
  expired: { label: "Expired", tone: "warning", marker: true },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** sales/models/sales_order.py :: SalesOrderStatus */
export const SALES_ORDER_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  confirmed: { label: "Confirmed", tone: "info" },
  partially_fulfilled: { label: "Partially Fulfilled", tone: "warning", marker: true },
  fulfilled: { label: "Fulfilled", tone: "success", marker: true },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** sales/models/delivery.py :: DeliveryChallanStatus */
export const DELIVERY_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  dispatched: { label: "Dispatched", tone: "info" },
  delivered: { label: "Delivered", tone: "success", marker: true },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** sales/models/invoice.py :: InvoiceStatus */
export const INVOICE_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  sent: { label: "Sent", tone: "info" },
  partially_paid: { label: "Partially Paid", tone: "warning", marker: true },
  paid: { label: "Paid", tone: "success", marker: true },
  overdue: { label: "Overdue", tone: "danger", marker: true },
  void: { label: "Void", tone: "neutral" },
};

/** sales/models/credit_note.py :: CreditNoteStatus */
export const CREDIT_NOTE_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  issued: { label: "Issued", tone: "success", marker: true },
  void: { label: "Void", tone: "neutral" },
};

/** purchases/models/purchase_order.py :: PurchaseOrderStatus */
export const PURCHASE_ORDER_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  approved: { label: "Approved", tone: "info" },
  partially_received: { label: "Partially Received", tone: "warning", marker: true },
  received: { label: "Received", tone: "success", marker: true },
  closed: { label: "Closed", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** purchases/models/goods_receipt.py :: GoodsReceiptStatus */
export const GOODS_RECEIPT_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  received: { label: "Received", tone: "success", marker: true },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** purchases/models/bill.py :: BillStatus */
export const BILL_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  open: { label: "Open", tone: "info" },
  partially_paid: { label: "Partially Paid", tone: "warning", marker: true },
  paid: { label: "Paid", tone: "success", marker: true },
  overdue: { label: "Overdue", tone: "danger", marker: true },
  void: { label: "Void", tone: "neutral" },
};

/** purchases/models/expense.py :: ExpenseStatus */
export const EXPENSE_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  posted: { label: "Posted", tone: "success", marker: true },
  void: { label: "Void", tone: "neutral" },
};

/** purchases/models/vendor_credit.py :: VendorCreditStatus */
export const VENDOR_CREDIT_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  issued: { label: "Issued", tone: "success", marker: true },
  void: { label: "Void", tone: "neutral" },
};

/** inventory/models/stock_adjustment.py :: AdjustmentStatus */
export const STOCK_ADJUSTMENT_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  posted: { label: "Posted", tone: "success", marker: true },
  reversed: { label: "Reversed", tone: "warning", marker: true },
};

/** accounting/models/journal.py :: JournalStatus */
export const JOURNAL_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  posted: { label: "Posted", tone: "success", marker: true },
  reversed: { label: "Reversed", tone: "warning", marker: true },
};

/** projects/models/project.py :: ProjectStatus */
export const PROJECT_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  active: { label: "Active", tone: "success", marker: true },
  on_hold: { label: "On Hold", tone: "warning", marker: true },
  completed: { label: "Completed", tone: "info" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** projects/models/time_entry.py :: TimeEntryStatus */
export const TIME_ENTRY_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  submitted: { label: "Submitted", tone: "info" },
  approved: { label: "Approved", tone: "success", marker: true },
  rejected: { label: "Rejected", tone: "danger", marker: true },
  invoiced: { label: "Invoiced", tone: "brand" },
};

/** banking/models/statement.py :: BankTransactionStatus */
export const BANK_TRANSACTION_STATUS: Record<string, StatusMeta> = {
  unmatched: { label: "Unmatched", tone: "warning", marker: true },
  suggested: { label: "Suggested", tone: "info" },
  matched: { label: "Matched", tone: "success", marker: true },
  excluded: { label: "Excluded", tone: "neutral" },
};

/** banking/models/reconciliation.py :: ReconciliationStatus */
export const BANK_RECONCILIATION_STATUS: Record<string, StatusMeta> = {
  in_progress: { label: "In Progress", tone: "info" },
  completed: { label: "Completed", tone: "success", marker: true },
  abandoned: { label: "Abandoned", tone: "neutral" },
};

/** banking/models/transfer.py :: BankTransferStatus */
export const BANK_TRANSFER_STATUS: Record<string, StatusMeta> = {
  posted: { label: "Posted", tone: "success", marker: true },
  void: { label: "Void", tone: "neutral" },
};

/** banking/models/statement.py :: StatementImportStatus */
export const STATEMENT_IMPORT_STATUS: Record<string, StatusMeta> = {
  pending: { label: "Pending", tone: "info" },
  completed: { label: "Completed", tone: "success", marker: true },
  failed: { label: "Failed", tone: "danger", marker: true },
};

/** documents/models/document.py :: UploadStatus */
export const DOCUMENT_UPLOAD_STATUS: Record<string, StatusMeta> = {
  uploading: { label: "Uploading", tone: "info" },
  scanning: { label: "Scanning", tone: "info" },
  ready: { label: "Ready", tone: "success", marker: true },
  failed: { label: "Failed", tone: "danger", marker: true },
  quarantined: { label: "Quarantined", tone: "danger", marker: true },
  archived: { label: "Archived", tone: "neutral" },
};

/** documents/models :: OCRStatus */
export const OCR_STATUS: Record<string, StatusMeta> = {
  not_requested: { label: "Not Requested", tone: "neutral" },
  queued: { label: "Queued", tone: "info" },
  processing: { label: "Processing", tone: "info" },
  needs_review: { label: "Needs Review", tone: "warning", marker: true },
  completed: { label: "Completed", tone: "success", marker: true },
  failed: { label: "Failed", tone: "danger", marker: true },
};

/** documents/models :: ReviewStatus */
export const DOCUMENT_REVIEW_STATUS: Record<string, StatusMeta> = {
  pending: { label: "Pending", tone: "warning", marker: true },
  approved: { label: "Approved", tone: "success", marker: true },
  rejected: { label: "Rejected", tone: "danger", marker: true },
};

/** automation/models/rule.py :: RuleStatus */
export const AUTOMATION_RULE_STATUS: Record<string, StatusMeta> = {
  draft: { label: "Draft", tone: "neutral" },
  active: { label: "Active", tone: "success", marker: true },
  paused: { label: "Paused", tone: "warning", marker: true },
  archived: { label: "Archived", tone: "neutral" },
};

/** automation/models/execution.py :: ExecutionStatus */
export const AUTOMATION_EXECUTION_STATUS: Record<string, StatusMeta> = {
  pending: { label: "Pending", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  succeeded: { label: "Succeeded", tone: "success", marker: true },
  partial: { label: "Partial", tone: "warning", marker: true },
  failed: { label: "Failed", tone: "danger", marker: true },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** automation/models/step_execution.py :: StepStatus */
export const AUTOMATION_STEP_STATUS: Record<string, StatusMeta> = {
  pending: { label: "Pending", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  succeeded: { label: "Succeeded", tone: "success", marker: true },
  failed: { label: "Failed", tone: "danger", marker: true },
  retrying: { label: "Retrying", tone: "warning", marker: true },
  skipped: { label: "Skipped", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** core/enums.py :: PaymentMethod */
export const PAYMENT_METHOD_LABELS: Record<string, string> = {
  cash: "Cash",
  bank_transfer: "Bank Transfer",
  cheque: "Cheque",
  card: "Card",
  upi: "UPI",
  other: "Other",
};

/** core/enums.py :: RecurringFrequency */
export const RECURRING_FREQUENCY_LABELS: Record<string, string> = {
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  yearly: "Yearly",
};

/** purchases/models/vendor_credit.py :: VendorCreditReason — differs from sales ("damaged", not "goodwill"). */
export const VENDOR_CREDIT_REASON_LABELS: Record<string, string> = {
  return: "Return",
  pricing_error: "Pricing Error",
  discount: "Discount",
  damaged: "Damaged",
  other: "Other",
};

/** projects :: BillingMethod */
export const BILLING_METHOD_LABELS: Record<string, string> = {
  non_billable: "Non-billable",
  hourly: "Hourly",
  fixed_fee: "Fixed Fee",
};

/** banking :: BankAccountKind */
export const BANK_ACCOUNT_KIND_LABELS: Record<string, string> = {
  bank: "Bank",
  credit_card: "Credit Card",
};

/** documents :: DocumentType */
export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  receipt: "Receipt",
  invoice: "Invoice",
  bill: "Bill",
  bank_statement: "Bank Statement",
  tax_document: "Tax Document",
  contract: "Contract",
  attachment: "Attachment",
  general: "General",
};

/** accounting/models/account.py :: AccountType */
export const ACCOUNT_TYPE_LABELS: Record<string, string> = {
  asset: "Asset",
  liability: "Liability",
  equity: "Equity",
  income: "Income",
  expense: "Expense",
};

/** sales/models/credit_note.py :: CreditNoteReason */
export const CREDIT_NOTE_REASON_LABELS: Record<string, string> = {
  return: "Return",
  pricing_error: "Pricing Error",
  discount: "Discount",
  goodwill: "Goodwill",
  other: "Other",
};

export function StatusBadge({
  status,
  map,
  size = "md",
}: {
  status: string | null | undefined;
  map: Record<string, StatusMeta>;
  size?: "sm" | "md";
}) {
  if (!status) return <span className="text-ink-400">—</span>;

  const meta = map[status];
  if (!meta) {
    // An unmapped status means the backend gained a state this build does not
    // know. Showing the raw value makes that visible rather than silently
    // rendering it as something familiar.
    return (
      <Badge tone="neutral" size={size}>
        {status}
      </Badge>
    );
  }

  return (
    <Badge tone={meta.tone} size={size} marker={meta.marker ?? false}>
      {meta.label}
    </Badge>
  );
}

/** Options for a status filter dropdown, derived from the same map. */
export function statusOptions(map: Record<string, StatusMeta>) {
  return Object.entries(map).map(([value, meta]) => ({ value, label: meta.label }));
}

/** Options for a plain label map (payment method, frequency, reason). */
export function labelOptions(map: Record<string, string>) {
  return Object.entries(map).map(([value, label]) => ({ value, label }));
}
