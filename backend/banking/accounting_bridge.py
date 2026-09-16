"""The ONLY banking-layer entry point into accounting.services.

Mirrors sales/accounting_bridge.py and purchases/accounting_bridge.py exactly:
a thin mechanical wrapper, with every decision about which accounts and how
much left to the calling service.

Note how few callers this module has compared with its sales and purchases
counterparts. That is the shape of the phase, not an oversight: importing a
statement posts nothing, and matching a statement line to a payment posts
nothing either, because the payment already posted its own journal. Only
`services/transfers.py` (money genuinely moved between two of our accounts)
and `services/matching.py::categorize_transaction` (a line with no existing
document behind it) create a journal here.
"""

from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal


def post_banking_journal(
    *, organization, posting_date, currency, lines: list[dict], memo: str, source_type: str, source_id: str, actor=None
):
    """`lines` are pre-built {"account_id", "debit"/"credit"} dicts that
    already balance — the caller's responsibility, not this function's.
    Returns the posted JournalEntry."""
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
