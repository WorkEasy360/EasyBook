import type { DateString, DateTimeString, DecimalString, UUID } from "@/lib/api/types";

/**
 * Banking API contract.
 *
 * VERIFIED, not assumed: regenerated from the DRF serializer dump
 * (MODULES=banking, frontend/scripts/dump-serializers.py), the view classes in
 * backend/banking/api/views.py, and live responses captured from the dev
 * tenant for every shape no serializer describes — the account and
 * reconciliation summaries, suggestions, auto-match, apply-rules and transfer
 * candidates.
 *
 * Conventions the rest of the module relies on:
 *  - READ serializers return relations as bare ids (`account`, `bank_account`);
 *    WRITE serializers take `_id` suffixes (`account_id`, `bank_account_id`).
 *  - `BankTransaction.amount` is SIGNED from the account holder's view:
 *    positive is money in, negative is money out (banking/CLAUDE.md, "THE SIGN
 *    CONVENTION"). `is_inflow` is the server's reading of that sign.
 *  - Every balance, difference and verdict (`is_balanced`, `can_complete`) is
 *    computed by banking/selectors.py. The UI shows them; it never derives them.
 */

// ---------------------------------------------------------------- accounts

export type BankAccountKind = "bank" | "credit_card";

/** banking.BankAccountSerializer */
export interface BankAccount {
  id: UUID;
  kind: BankAccountKind;
  name: string;
  /** The ledger account (accounting.Account) this bank account is paired with, 1:1. */
  account: UUID;
  /** ISO code (the Currency primary key). */
  currency: string;
  bank_name: string;
  /**
   * "••••6789". The only form of the number the API ever returns — the
   * service keeps the last four digits and nothing else.
   */
  masked_number: string;
  branch_identifier: string;
  /** The STATEMENT balance at go-live, not the ledger's opening balance. */
  opening_balance: DecimalString;
  opening_balance_date: DateString | null;
  provider_key: string;
  is_active: boolean;
  notes: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** banking.BankAccountCreateSerializer */
export interface BankAccountInput {
  name: string;
  /** asset for kind=bank, liability for kind=credit_card (invalid_account_type). */
  account_id: UUID;
  kind?: BankAccountKind;
  /** Blank means the organization's default currency. */
  currency_code?: string;
  bank_name?: string;
  /** Write-only. Only its last four digits are stored; it is never echoed back. */
  account_number?: string;
  branch_identifier?: string;
  opening_balance?: DecimalString;
  /** Required when opening_balance is non-zero (opening_balance_date_required). */
  opening_balance_date?: DateString | null;
  provider_key?: string;
  notes?: string;
}

/**
 * banking.BankAccountUpdateSerializer (PATCH bank-accounts/{id}/).
 *
 * `kind` and `account` are absent on purpose — both are "make a new bank
 * account" operations. The opening balance fields are refused once any
 * statement line exists (opening_balance_locked), even if unchanged, so a form
 * must omit them rather than resend them.
 */
export interface BankAccountUpdateInput {
  name?: string;
  bank_name?: string;
  account_number?: string;
  branch_identifier?: string;
  notes?: string;
  is_active?: boolean;
  opening_balance?: DecimalString;
  opening_balance_date?: DateString | null;
  provider_key?: string;
}

/**
 * banking.BankAccountSummarySerializer — GET bank-accounts/{id}/summary/?as_of=
 *
 * Captured live: {"as_of":null,"book_balance":"-3300.59","statement_balance":
 * "100924.50","cleared_balance":"99882.00","unexplained_statement_amount":
 * "1042.50","uncleared_book_amount":"-103182.59","open_transaction_count":6}
 */
export interface BankAccountSummary {
  as_of: DateString | null;
  /** Posted journal lines on the linked ledger account, signed as a statement shows it. */
  book_balance: DecimalString;
  /** Opening balance plus every statement line that is not excluded. */
  statement_balance: DecimalString;
  /** Opening balance plus the MATCHED statement lines. */
  cleared_balance: DecimalString;
  /** statement − cleared: what the statement shows that is not yet explained. */
  unexplained_statement_amount: DecimalString;
  /** book − cleared: timing differences (uncleared cheques, deposits in transit). Not an error. */
  uncleared_book_amount: DecimalString;
  open_transaction_count: number;
}

// -------------------------------------------------------------- statements

export type StatementSourceFormat = "csv" | "ofx" | "qif" | "feed" | "manual";
export type StatementImportStatus = "pending" | "completed" | "failed";

/** banking.StatementImportSerializer */
export interface StatementImport {
  id: UUID;
  bank_account: UUID;
  source_format: StatementSourceFormat;
  file_name: string;
  status: StatementImportStatus;
  statement_start_date: DateString | null;
  statement_end_date: DateString | null;
  rows_read: number;
  rows_imported: number;
  rows_skipped_duplicate: number;
  error_message: string;
  created_at: DateTimeString;
}

export type AmountMode = "signed" | "debit_credit" | "indicator";

/**
 * banking.CsvColumnMappingSerializer. Column values are header TEXT; the
 * backend compares them case-insensitively after trimming and treating "_" as
 * a space (services/parsers.py :: _normalize_header).
 */
export interface CsvColumnMapping {
  date_column: string;
  amount_mode?: AmountMode;
  /** signed and indicator modes. */
  amount_column?: string;
  /** debit_credit mode: money out. */
  debit_column?: string;
  /** debit_credit mode: money in. */
  credit_column?: string;
  /** indicator mode: the Dr/Cr marker column. */
  indicator_column?: string;
  description_column?: string;
  counterparty_column?: string;
  reference_column?: string;
  external_id_column?: string;
  /** strptime formats tried in order; empty means the backend's defaults. */
  date_formats?: string[];
  invert_sign?: boolean;
}

/** banking.StatementImportCreateSerializer — POST bank-accounts/{id}/statement-imports/ */
export interface StatementImportInput {
  content: string;
  file_name?: string;
  mapping: CsvColumnMapping;
}

export type BankTransactionStatus = "unmatched" | "suggested" | "matched" | "excluded";

/** banking.BankTransactionSerializer — every field read-only (bank-reported fields are frozen). */
export interface BankTransaction {
  id: UUID;
  bank_account: UUID;
  statement_import: UUID | null;
  transaction_date: DateString;
  /** Signed: positive = money in, negative = money out. */
  amount: DecimalString;
  is_inflow: boolean;
  description: string;
  counterparty_name: string;
  bank_reference: string;
  external_id: string;
  status: BankTransactionStatus;
  excluded_reason: string;
  /** Set when a COMPLETED reconciliation locked this line. */
  reconciliation: UUID | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** banking.BankTransactionCreateSerializer — POST bank-accounts/{id}/transactions/ */
export interface BankTransactionInput {
  transaction_date: DateString;
  /** Signed, non-zero. */
  amount: DecimalString;
  description?: string;
  counterparty_name?: string;
  bank_reference?: string;
  external_id?: string;
}

// ----------------------------------------------------------------- matches

export type MatchType = "exact" | "amount_date" | "transfer" | "rule" | "categorization" | "manual";
export type SuggestionSource = "system" | "rule" | "user" | "ai";
/** The model's COUNTERPART_FIELDS. `journal_entry` is what a categorization points at. */
export type CounterpartType = "customer_payment" | "vendor_payment" | "expense" | "bank_transfer" | "journal_entry";

/**
 * banking.BankTransactionMatchSerializer.
 *
 * GET bank-transactions/{id}/suggestions/ returns EVERY match row on the line,
 * confirmed and not; `is_confirmed` is what separates a suggestion from the
 * reconciliation record. An unconfirmed row must be labelled a suggestion.
 */
export interface BankTransactionMatch {
  id: UUID;
  transaction: UUID;
  counterpart_type: CounterpartType;
  customer_payment: UUID | null;
  vendor_payment: UUID | null;
  expense: UUID | null;
  bank_transfer: UUID | null;
  journal_entry: UUID | null;
  amount: DecimalString;
  match_type: MatchType;
  suggestion_source: SuggestionSource;
  /** "0.000".."1.000", deterministic arithmetic (services/matching.py :: score_candidate). */
  confidence: DecimalString;
  /** e.g. "Transfer TRF-0001", "Customer payment PAY-0003", or the categorization memo. */
  reason: string;
  is_confirmed: boolean;
  confirmed_at: DateTimeString | null;
  created_at: DateTimeString;
}

/** POST bank-transactions/{id}/auto-match/ — captured live: {"matched":false,"match":null} */
export interface AutoMatchResult {
  matched: boolean;
  match: BankTransactionMatch | null;
}

/** banking.CreateMatchSerializer — POST bank-transactions/{id}/matches/ (creates CONFIRMED). */
export interface CreateMatchInput {
  counterpart_type: Exclude<CounterpartType, "journal_entry">;
  counterpart_id: UUID;
  /** Defaults to the line's full absolute amount. */
  amount?: DecimalString | null;
}

/** banking.CategorizeTransactionSerializer — POST bank-transactions/{id}/categorize/ (posts a journal). */
export interface CategorizeInput {
  account_id: UUID;
  description?: string;
}

/** banking.ExcludeTransactionSerializer — reason is required. */
export interface ExcludeInput {
  reason: string;
}

export type ApplyRulesReason =
  | "transaction_not_open"
  | "no_rule_matched"
  | "excluded"
  | "awaiting_confirmation"
  | "categorized";

/**
 * POST bank-transactions/{id}/apply-rules/ — captured live:
 * {"applied":false,"reason":"no_rule_matched","rule":null}
 */
export interface ApplyRulesResult {
  applied: boolean;
  reason: ApplyRulesReason;
  rule: BankRule | null;
}

/**
 * banking.TransferCandidateSerializer — GET bank-transactions/{id}/transfer-candidates/
 * Captured live: [{"id":"…","bank_account":"…","bank_account_name":"Banking ICICI
 * Savings","transaction_date":"2026-09-16","amount":"7000.00","description":"…"}]
 */
export interface TransferCandidate {
  id: UUID;
  bank_account: UUID;
  bank_account_name: string;
  transaction_date: DateString;
  amount: DecimalString;
  description: string;
}

/** banking.ConfirmTransferPairSerializer — POST bank-transfers/confirm-pair/ */
export interface ConfirmTransferPairInput {
  outflow_transaction_id: UUID;
  inflow_transaction_id: UUID;
  reference?: string;
}

// --------------------------------------------------------------- transfers

export type BankTransferStatus = "posted" | "void";

/** banking.BankTransferSerializer. There is no detail endpoint — list and void only. */
export interface BankTransfer {
  id: UUID;
  transfer_number: string;
  from_bank_account: UUID;
  to_bank_account: UUID;
  transfer_date: DateString;
  amount: DecimalString;
  currency: string;
  reference: string;
  description: string;
  status: BankTransferStatus;
  accounting_journal: UUID | null;
  voided_at: DateTimeString | null;
  void_reason: string;
  created_at: DateTimeString;
}

/** banking.BankTransferCreateSerializer — posts the journal immediately. */
export interface BankTransferInput {
  from_bank_account_id: UUID;
  to_bank_account_id: UUID;
  transfer_date: DateString;
  amount: DecimalString;
  reference?: string;
  description?: string;
}

// ------------------------------------------------------------------- rules

export type RuleAction = "categorize" | "exclude";
export type RuleDirection = "any" | "inflow" | "outflow";

/** banking.BankRuleSerializer */
export interface BankRule {
  id: UUID;
  name: string;
  priority: number;
  is_active: boolean;
  bank_account: UUID | null;
  description_contains: string;
  counterparty_contains: string;
  direction: RuleDirection;
  /** Compared against the ABSOLUTE amount. */
  amount_min: DecimalString | null;
  amount_max: DecimalString | null;
  action: RuleAction;
  target_account: UUID | null;
  /** Read-only on the API: no write serializer accepts them. */
  vendor: UUID | null;
  customer: UUID | null;
  /** Categorizes and posts the journal with no human review. Off by default. */
  auto_confirm: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** banking.BankRuleCreateSerializer */
export interface BankRuleInput {
  name: string;
  action?: RuleAction;
  target_account_id?: UUID | null;
  bank_account_id?: UUID | null;
  description_contains?: string;
  counterparty_contains?: string;
  direction?: RuleDirection;
  amount_min?: DecimalString | null;
  amount_max?: DecimalString | null;
  auto_confirm?: boolean;
  priority?: number;
}

/** banking.BankRuleUpdateSerializer — no bank_account_id: the scope is fixed at creation. */
export interface BankRuleUpdateInput {
  name?: string;
  priority?: number;
  is_active?: boolean;
  description_contains?: string;
  counterparty_contains?: string;
  direction?: RuleDirection;
  amount_min?: DecimalString | null;
  amount_max?: DecimalString | null;
  action?: RuleAction;
  target_account_id?: UUID | null;
  auto_confirm?: boolean;
}

// ---------------------------------------------------------- reconciliation

export type ReconciliationStatus = "in_progress" | "completed" | "abandoned";

/** banking.BankReconciliationSerializer */
export interface BankReconciliation {
  id: UUID;
  bank_account: UUID;
  statement_start_date: DateString;
  statement_end_date: DateString;
  /** Typed by a person from the bank's own statement — never derived. */
  statement_closing_balance: DecimalString;
  status: ReconciliationStatus;
  /** Frozen on completion; null while in progress. */
  cleared_balance: DecimalString | null;
  notes: string;
  completed_at: DateTimeString | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** banking.BankReconciliationCreateSerializer */
export interface BankReconciliationInput {
  bank_account_id: UUID;
  statement_start_date: DateString;
  statement_end_date: DateString;
  statement_closing_balance: DecimalString;
  notes?: string;
}

/**
 * banking.ReconciliationSummarySerializer — GET bank-reconciliations/{id}/summary/
 *
 * Captured live: {"statement_closing_balance":"102424.50","cleared_balance":
 * "99882.00","difference":"2542.50","book_balance":"-3300.59",
 * "transaction_count":7,"open_transaction_count":6,"is_balanced":false,
 * "can_complete":false}
 *
 * `is_balanced` and `can_complete` are the server's verdict; the Complete
 * button follows `can_complete` and nothing else.
 */
export interface ReconciliationSummary {
  statement_closing_balance: DecimalString;
  cleared_balance: DecimalString;
  difference: DecimalString;
  book_balance: DecimalString;
  transaction_count: number;
  open_transaction_count: number;
  is_balanced: boolean;
  can_complete: boolean;
}

// ------------------------------------------------------------------ labels

export const MATCH_TYPE_LABELS: Record<MatchType, string> = {
  exact: "Exact (amount, date and reference)",
  amount_date: "Amount and date",
  transfer: "Transfer",
  rule: "Rule",
  categorization: "Categorized",
  manual: "Manual",
};

export const SUGGESTION_SOURCE_LABELS: Record<SuggestionSource, string> = {
  system: "System",
  rule: "Rule",
  user: "Person",
  ai: "AI suggestion",
};

export const COUNTERPART_TYPE_LABELS: Record<CounterpartType, string> = {
  customer_payment: "Customer payment",
  vendor_payment: "Vendor payment",
  expense: "Expense",
  bank_transfer: "Bank transfer",
  journal_entry: "Journal",
};

export const AMOUNT_MODE_LABELS: Record<AmountMode, string> = {
  signed: "One signed amount column (negative = money out)",
  debit_credit: "Separate withdrawal and deposit columns",
  indicator: "One amount column plus a Dr/Cr column",
};

export const RULE_ACTION_LABELS: Record<RuleAction, string> = {
  categorize: "Categorize to an account",
  exclude: "Exclude from reconciliation",
};

export const RULE_DIRECTION_LABELS: Record<RuleDirection, string> = {
  any: "Any",
  inflow: "Money in",
  outflow: "Money out",
};

/** What each apply-rules outcome means (services/rules.py :: apply_rules_to_transaction). */
export const APPLY_RULES_REASON_LABELS: Record<ApplyRulesReason, string> = {
  transaction_not_open: "The transaction is already matched or excluded, so no rule was run.",
  no_rule_matched: "No active rule matches this transaction.",
  excluded: "A rule excluded this transaction.",
  awaiting_confirmation:
    "A rule matches, but it is not set to auto-confirm. Categorize the transaction yourself to post it.",
  categorized: "A rule categorized this transaction and posted its journal.",
};

export const STATEMENT_SOURCE_LABELS: Record<StatementSourceFormat, string> = {
  csv: "CSV",
  ofx: "OFX",
  qif: "QIF",
  feed: "Bank feed",
  manual: "Manual entry",
};
