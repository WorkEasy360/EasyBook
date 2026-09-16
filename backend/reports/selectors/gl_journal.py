"""General Ledger and Journal reports.

General Ledger reuses `accounting.selectors.get_account_running_ledger`
verbatim (PHASE 8 spec §6/§7: do not duplicate the existing Trial
Balance/Ledger logic) — this module only adds pagination over its already
deterministically-ordered `entries` list.

Journal report is new: it is just a filtered, paginated view over
`accounting.JournalEntry` — no new posting/balance logic.
"""

from accounting.models.journal import JournalEntry, JournalStatus
from accounting.selectors import get_account_running_ledger


def get_general_ledger_report(*, account, from_date=None, to_date=None) -> dict:
    return get_account_running_ledger(account=account, from_date=from_date, to_date=to_date)


def get_journal_report_queryset(
    *,
    organization,
    from_date=None,
    to_date=None,
    journal_number=None,
    account=None,
    source_type=None,
    status=None,
    created_by=None,
    posted_by=None,
):
    """Ordered, filterable JournalEntry queryset. Defaults to POSTED only —
    financial reporting normally excludes DRAFT/unposted entries (PHASE 8
    spec §8) — an explicit `status` opts into a different view."""
    qs = JournalEntry.objects.filter(organization=organization).prefetch_related("lines")
    qs = qs.filter(status=status) if status else qs.filter(status=JournalStatus.POSTED)
    if from_date is not None:
        qs = qs.filter(posting_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(posting_date__lte=to_date)
    if journal_number:
        qs = qs.filter(journal_number__icontains=journal_number)
    if account is not None:
        qs = qs.filter(lines__account=account).distinct()
    if source_type:
        qs = qs.filter(source_type=source_type)
    if created_by is not None:
        qs = qs.filter(created_by=created_by)
    if posted_by is not None:
        qs = qs.filter(posted_by=posted_by)
    # Deterministic ordering: posting_date then journal_number, never id/UUID
    # (root CLAUDE.md / inventory.CLAUDE.md's documented reason applies here
    # too — a random tiebreaker makes paginated results non-reproducible).
    return qs.order_by("posting_date", "journal_number", "created_at")
