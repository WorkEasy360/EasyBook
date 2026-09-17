# ACCOUNTING

PURPOSE
Authoritative deterministic double-entry accounting engine: Chart of Accounts, Journal Entries, posting, reversal, General Ledger, Trial Balance. Nothing outside this app may create authoritative GL effects.

OWNS
- `models/account.py` — `Account` (Chart of Accounts, hierarchical, org-scoped).
- `models/journal.py` — `JournalEntry`, `JournalLine`. Immutability of posted journals/lines is enforced in `save()`/`delete()` overrides, not just by convention.
- `services/accounts.py` — account creation/update, hierarchy + system-account guards.
- `services/journals.py` — DRAFT creation/line replacement (unbalanced allowed; posting is where balance is enforced).
- `services/posting.py` — `post_journal()`, `reverse_journal()`: the ONLY authoritative posting path.
- `services/opening_balances.py` — opening balances are an ordinary journal, nothing more.
- `services/fiscal.py` — resolves/validates the fiscal period for a posting date (delegates to `accounts.FiscalYear`, does not duplicate it).
- `selectors.py` — General Ledger and Trial Balance, computed on demand from posted `JournalLine`s.

INVARIANTS
- Decimal only, never float.
- `debit == credit` enforced at POST time (`services/posting.post_journal`), not at draft creation — drafts may be unbalanced work-in-progress.
- No mutable authoritative balance field anywhere (no `account.balance`). General Ledger/Trial Balance are always derived from posted `JournalLine` rows — see `selectors.py` docstring for why there is deliberately no separate ledger projection table.
- Posted journals/lines are immutable: `JournalEntry.save()`/`delete()` and `JournalLine.save()`/`delete()` raise `ValueError` outside the allowed DRAFT→POSTED/POSTED→REVERSED transitions. Corrections go through `reverse_journal()`, never direct edits.
- `post_journal()` is idempotent by construction: posting an already-POSTED journal is a no-op returning the existing journal (locked via `select_for_update()`), not a second Idempotency-Key system layered on top.
- A journal can be reversed at most once — `JournalEntry.reverses` is a `OneToOneField`.
- Ledger-derived figures (GL, Trial Balance, every report, bank book balance) filter `journal_entry__status__in=LEDGER_STATUSES` (POSTED + REVERSED), never `status=POSTED` alone: a reversed original stays a fact of its own period and its POSTED reversal cancels it. Filtering POSTED only drops the original but keeps the mirror, reporting minus the original instead of zero.
- Journal numbers are allocated at POST time via `accounts.services.allocate_sequence_number`, never at draft creation.
- Every accounting table is a `TenantScopedModel` with a matching RLS migration (see `core/CLAUDE.md`) — no exceptions.
- Authoritative accounting numbers (balances, journal totals, trial balance) are never computed by AI — see root `CLAUDE.md` pipeline rule.

READ FIRST
- `models/journal.py`, `services/posting.py`, `selectors.py`

TOKEN DISCIPLINE
- Do not duplicate root or `core`/`accounts` CLAUDE.md content here.
