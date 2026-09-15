"""The ONLY sales-layer integration into accounting.services — see
sales/CLAUDE.md and root CLAUDE.md ("never create JournalLines directly from
sales"). Mirrors inventory/services/accounting_bridge.py exactly: this module
is a thin, mechanical wrapper — all business logic (which accounts, how much,
whether balanced) lives in the calling service (services/invoices.py, and
later services/payments.py / services/credit_notes.py).
"""

from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal


def post_sales_journal(
    *, organization, posting_date, currency, lines: list[dict], memo: str, source_type: str, source_id: str, actor=None
):
    """`lines` are pre-built, pre-grouped {"account_id", "debit"/"credit"}
    dicts that already balance (debit total == credit total) — the caller's
    responsibility, not this function's. Returns the posted JournalEntry."""
    journal = create_draft_journal(
        organization=organization,
        posting_date=posting_date,
        currency=currency,
        lines=lines,
        memo=memo,
        source_type=source_type,
        source_id=source_id,
        created_by=actor,
    )
    return post_journal(journal_id=journal.id, organization=organization, actor=actor)
