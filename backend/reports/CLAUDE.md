# REPORTS

PURPOSE
Read-only authoritative financial and operational reporting layer. Reports derive from other apps' posted records; this app owns no business domain and creates no new financial facts.

OWNS
- `selectors/pnl.py`, `selectors/balance_sheet.py`, `selectors/cash_flow.py` — P&L, Balance Sheet, Cash Flow. All three derive from `accounting.JournalLine` (posted only), never from Sales/Purchases document totals.
- `selectors/classification.py` — the ONE place `Account.account_subtype` is read for report grouping (fixed/current asset, long-term/current liability, COGS/operating/other-income-expense). `account_subtype` is a free-text field with no seeded chart of accounts (accounting/models/account.py) — this module treats it as an explicit, organization-set opt-in against a small documented vocabulary, defaulting to the safe bucket (current/operating) when blank, and NEVER infers anything from `Account.name`.
- `selectors/gl_journal.py` — General Ledger (thin pagination wrapper over `accounting.selectors.get_account_running_ledger`) and Journal report (a filtered `JournalEntry` queryset — no new posting/balance logic). Trial Balance is exposed at `/api/v1/reports/trial-balance/` as a thin wrapper reusing `accounting.selectors.get_trial_balance` verbatim; `/api/v1/accounting/reports/trial-balance/` remains the original endpoint.
- `selectors/receivables.py` / `selectors/payables.py` — AR/AP ageing, customer/vendor balances, outstanding/overdue documents, sales/purchases by customer/vendor/item, expenses by category. Built on `sales.selectors`/`purchases.selectors` plus grouped SQL aggregates over `Invoice`/`InvoiceLine`/`Bill`/`BillLine`/`Expense`. Sales reports key on `Invoice.invoice_date` (purchases: `Bill.bill_date`/`Expense.expense_date`) — the same field `compliance.selectors` already uses for the GST registers.
- `selectors/inventory.py` — Stock Summary/Valuation/Movement/Adjustment/Low-Stock. Valuation reuses `inventory.selectors.get_weighted_average_cost` per (item, warehouse) pair that actually has movement history — never a new valuation algorithm.
- `selectors/tax.py` — pure pass-through to `compliance.selectors` (output/input tax registers, GSTR-1/3B summaries). `compliance` has no HTTP API of its own; these are the first GST report endpoints this codebase serves.
- `selectors/projects.py` — fans out `projects.selectors.get_project_profitability` (already computes revenue/cost/margin/hours) across an organization's projects. Gated on `VIEW_ALL_TIMESHEETS`, not `VIEW_PROJECTS`, mirroring projects/CLAUDE.md's own reasoning (margin exposes labour cost).
- `selectors/params.py` — shared date parsing, Decimal-safe JSON serialization (`to_json_safe`), and divide-by-zero-safe variance computation.
- `exports/csv.py` — CSV export for flat row-list reports via `?export=csv` (NOT `?format=` — that query param is reserved by DRF's content negotiation and only JSONRenderer is registered; asking for `format=csv` 404s). Multi-section statements (P&L/BS/CF) and paginated transaction-detail reports (GL/Journal/Inventory Movement/Adjustments) are not wired to CSV in this phase.
- `api/` — one view per report under `/api/v1/reports/...` (see `api/urls.py` for the full list).

CASH FLOW CLASSIFICATION (`selectors/cash_flow.py`)
Cash/bank accounts are identified via `banking.BankAccount.account` (an explicit `OneToOneField`), never inferred from account name or type — root CLAUDE.md forbids that guess, and this is the one place in the codebase that fact is recorded unambiguously. Only `kind=BANK` accounts count as cash; a `CREDIT_CARD`-kind account is a liability. If an organization has configured no BANK-kind `BankAccount`, the endpoint refuses (400 `cash_accounts_not_configured`) rather than silently reporting zero cash. The Operating/Investing/Financing split is the indirect method, proven (not merely believed) to reconcile exactly to Closing − Opening cash — see the module docstring and `tests/test_cash_flow.py::test_cash_flow_reconciles_to_closing_cash` for the algebra.

BALANCE SHEET "CURRENT EARNINGS" (`selectors/balance_sheet.py`)
This system never posts a year-end closing journal — Income/Expense balances simply accumulate from inception. Because every posted journal balances, `Assets = Liabilities + Equity + cumulative(Income − Expense)` is a mathematical identity, not an approximation. The Balance Sheet reports cumulative net profit as a computed `current_earnings` equity line (via `selectors/pnl.py`, `from_date=None`) — never a stored account balance. See `tests/test_balance_sheet.py::test_balance_sheet_balances`.

REQUIRED CONFIGURATION FOR FULL REPORT FIDELITY
- Cash Flow: at least one `banking.BankAccount` with `kind=bank`.
- Balance Sheet current/fixed split, P&L COGS/other-income/other-expense split: set `Account.account_subtype` to one of `fixed_asset`, `other_asset`, `long_term_liability`, `cogs`/`cost_of_goods_sold`, `other_income`, `other_expense` (case-insensitive). Unset defaults to the safe bucket (current asset/liability, operating income/expense) — reports never fail or drop an account for missing configuration, they just cannot subdivide it further.

INVARIANTS
- Reports never mutate source data — every selector here is read-only.
- Financial reports derive from POSTED accounting entries only (root CLAUDE.md pipeline rule).
- Decimal only, everywhere; `to_json_safe`/`money()` are the only places a report value becomes a string for JSON — never a float.
- Tenant isolation mandatory: every selector takes an explicit `organization` and every queryset also relies on `TenantManager`'s GUC-scoped `.objects` manager as defense in depth (core/CLAUDE.md) — no report queries `all_objects`.
- No AI-generated financial values (root CLAUDE.md).
- Tax reports reuse `compliance.selectors` verbatim; inventory reports reuse `inventory.selectors` verbatim; GL/Trial Balance reuse `accounting.selectors` verbatim. Nothing here recomputes a number another app already owns.
- Totals returned by a report represent the full filtered dataset, never only the current page — pagination (GL, Journal, Inventory Movement/Adjustments) is applied only to the transaction-detail list, not to any total.
- No persistent report/balance tables exist or should be added without a proven, measured performance need (root CLAUDE.md, phase spec §2/§31) — every report here is computed on demand.

PERFORMANCE
- P&L/Balance Sheet use one grouped SQL aggregate (`GROUP BY account_id`) for the whole statement, not a per-account Python loop.
- Inventory Stock Summary/Valuation iterate only the (item, warehouse) pairs that actually have `StockMovement` rows (a `.values(...).distinct()` query), never the full item × warehouse cross product. That query MUST call `.order_by()` before `.distinct()` — `StockMovement`'s default ordering otherwise gets pulled into the SELECT and silently turns "distinct pair" into "distinct pair+timestamp" (a documented Django gotcha; see the comment at `selectors/inventory.py::_active_item_warehouse_pairs`).
- Sales/Purchases-by-customer/vendor/item use grouped `.annotate(Sum(...))` aggregates, never a Python loop over documents.
- No speculative caching (phase spec §31) — none exists here.

READ FIRST
- `selectors/pnl.py`, `selectors/balance_sheet.py`, `selectors/cash_flow.py`, `selectors/classification.py`, `api/views.py`

TOKEN DISCIPLINE
- Do not duplicate root, `core`, `accounting`, `sales`, `purchases`, `inventory`, `tax`, `compliance`, `banking` or `projects` CLAUDE.md content here.
