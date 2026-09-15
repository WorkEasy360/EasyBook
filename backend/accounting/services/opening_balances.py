from django.db import transaction

from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal


@transaction.atomic
def post_opening_balances(*, organization, fiscal_year, currency, balances: list[dict], actor=None):
    """Opening balances are just an ordinary journal, dated to the start of
    the fiscal year and tagged source_type='opening_balance' — there is no
    `account.balance` field anywhere; double-entry, posting validation,
    immutability and reversal all apply exactly as they do to any journal
    (see accounting/CLAUDE.md)."""
    journal = create_draft_journal(
        organization=organization,
        posting_date=fiscal_year.start_date,
        currency=currency,
        lines=balances,
        memo=f"Opening balances for {fiscal_year.start_date.year}-{fiscal_year.end_date.year}",
        source_type="opening_balance",
        source_id=str(fiscal_year.id),
        created_by=actor,
    )
    return post_journal(journal_id=journal.id, organization=organization, actor=actor)
