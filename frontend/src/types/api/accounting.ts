import type { DateString, DateTimeString, DecimalString, Paginated, UUID } from "@/lib/api/types";

/**
 * Accounting contracts.
 *
 * VERIFIED, not transcribed from memory: rewritten from
 * `scripts/dump-serializers.py` (MODULES=accounting), accounting/api/views.py,
 * reports/api/views.py (TrialBalanceView, GeneralLedgerReportView) and live
 * responses captured from the dev tenant. Corrections against the previous
 * hand-written file are noted inline.
 */

export type AccountType = "asset" | "liability" | "equity" | "income" | "expense";

/** accounting.AccountSerializer — read AND write (a ModelSerializer). */
export interface Account {
  id: UUID;
  code: string;
  name: string;
  account_type: AccountType;
  account_subtype: string;
  /** Parent account id. Written back under the same key (`parent`, not `parent_id`). */
  parent: UUID | null;
  description: string;
  /** Platform-assigned; never settable through the API. */
  is_system: boolean;
  is_active: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** POST /accounting/accounts/ and PATCH /accounting/accounts/{id}/. */
export interface AccountInput {
  code?: string;
  name?: string;
  account_type?: AccountType;
  account_subtype?: string;
  parent?: UUID | null;
  description?: string;
  is_active?: boolean;
}

/**
 * accounting/services/accounts.py :: SYSTEM_ACCOUNT_MUTABLE_FIELDS. Any other
 * key in a PATCH to a system account is refused with `system_account_protected`
 * — including `is_active`, so a system account cannot be deactivated either.
 */
export const SYSTEM_ACCOUNT_MUTABLE_FIELDS = ["name", "description", "account_subtype"] as const;

export type JournalStatus = "draft" | "posted" | "reversed";

/** accounting.JournalLineSerializer — read-only. */
export interface JournalLine {
  id: UUID;
  line_number: number;
  account: UUID;
  description: string;
  debit: DecimalString;
  credit: DecimalString;
}

/** accounting.JournalEntrySerializer */
export interface JournalEntry {
  id: UUID;
  /** "" until posting — numbers are allocated at POST time, never on a draft. */
  journal_number: string;
  reference: string;
  posting_date: DateString;
  memo: string;
  status: JournalStatus;
  /** Provenance when an engine raised this entry, e.g. "sales.Invoice". "" for manual journals. */
  source_type: string;
  source_id: string;
  /** Currency code (Currency's primary key). */
  currency: string;
  exchange_rate: DecimalString;
  fiscal_year: UUID | null;
  created_by: UUID | null;
  posted_by: UUID | null;
  posted_at: DateTimeString | null;
  /** Set on a REVERSING entry, pointing at the journal it reversed. */
  reverses: UUID | null;
  lines: JournalLine[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/**
 * accounting.JournalLineInputSerializer.
 * CORRECTION: the key is `account_id` — the old type said `account`, which the
 * serializer rejects as a missing required field.
 */
export interface JournalLineInput {
  account_id: UUID;
  description?: string;
  debit?: DecimalString;
  credit?: DecimalString;
}

/** accounting.JournalEntryCreateSerializer — creates a DRAFT; balance is enforced only on posting. */
export interface JournalEntryInput {
  posting_date: DateString;
  currency: string;
  exchange_rate?: DecimalString;
  reference?: string;
  memo?: string;
  source_type?: string;
  source_id?: string;
  lines: JournalLineInput[];
}

/**
 * PATCH /accounting/journals/{id}/ (JournalEntryDetailView.update): draft
 * only, and ONLY these keys are read — currency and exchange rate are fixed
 * once the draft exists. `lines` replaces every line.
 */
export interface JournalEntryUpdateInput {
  posting_date?: DateString;
  reference?: string;
  memo?: string;
  lines?: JournalLineInput[];
}

/** accounting.JournalEntryReverseSerializer. Returns the new, already-POSTED reversing entry (201). */
export interface JournalReverseInput {
  posting_date?: DateString;
  memo?: string;
}

/** One posted line on an account, with the running balance the engine computed. */
export interface LedgerEntry {
  journal_entry_id: UUID;
  journal_number: string;
  posting_date: DateString;
  description: string;
  /** Base-currency amounts. */
  debit: DecimalString;
  credit: DecimalString;
  /** Signed in the account's normal-balance direction: positive = normal side. */
  running_balance: DecimalString;
}

/**
 * GET /accounting/accounts/{id}/ledger/?from_date&to_date — unpaginated.
 * CORRECTION: `account` is the full Account object and the rows are under
 * `entries` (not `rows`), keyed `journal_entry_id` (not `journal_entry`).
 */
export interface AccountLedger {
  account: Account;
  opening_balance: DecimalString;
  closing_balance: DecimalString;
  entries: LedgerEntry[];
}

/** GET /reports/general-ledger/ row — the account ledger row plus provenance. */
export interface GeneralLedgerEntry extends LedgerEntry {
  source_type: string;
  source_id: string;
}

/**
 * GET /reports/general-ledger/?account=…&from_date&to_date&page — PAGINATED,
 * with the account and period balances added beside the page. `account` is
 * required (400 `account_required`). The running balance is computed over the
 * whole period before paging, so a page never restates it.
 */
export interface GeneralLedgerReport extends Paginated<GeneralLedgerEntry> {
  account: Account;
  opening_balance: DecimalString;
  closing_balance: DecimalString;
}

/**
 * Trial balance row. CORRECTION: the account is nested (`account`), and each
 * row carries opening, period debit/credit and closing debit/credit — not a
 * flat `account_code`/`debit`/`credit`.
 */
export interface TrialBalanceRow {
  account: Account;
  opening_balance: DecimalString;
  period_debit: DecimalString;
  period_credit: DecimalString;
  closing_debit: DecimalString;
  closing_credit: DecimalString;
}

/**
 * GET /reports/trial-balance/?as_of_date&from_date (VIEW_REPORTS).
 * `is_balanced` is the engine's own closing-debits == closing-credits verdict.
 * `as_of_date` defaults to the SERVER's date when omitted, so callers pass it.
 */
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

/**
 * Whether a debit increases this account type (accounting/models/account.py
 * DEBIT_NORMAL_TYPES). Presentation only — orients a column heading.
 */
export function isDebitNormal(type: AccountType): boolean {
  return type === "asset" || type === "expense";
}
