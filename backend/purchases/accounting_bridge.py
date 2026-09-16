"""The ONLY purchases-layer integration into accounting.services — see
purchases/CLAUDE.md and root CLAUDE.md ("no module may directly create
authoritative JournalLines"). Mirrors sales/accounting_bridge.py and
inventory/services/accounting_bridge.py exactly: this module is a thin,
mechanical wrapper — all business logic (which accounts, how much, whether
balanced) lives in the calling service (services/bills.py,
services/expenses.py, services/payments.py, services/vendor_credits.py).
"""

from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal


def post_purchase_journal(
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
