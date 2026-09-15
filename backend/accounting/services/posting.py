from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.models.journal import JournalEntry, JournalLine, JournalStatus
from accounting.services.fiscal import assert_period_open, get_fiscal_year_for_date
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError

JOURNAL_NUMBER_SEQUENCE_KEY = "journal"


def _get_journal_for_update(*, journal_id, organization) -> JournalEntry:
    try:
        return JournalEntry.objects.select_for_update().get(id=journal_id, organization=organization)
    except JournalEntry.DoesNotExist:
        raise ApplicationError("Journal entry not found.", code="journal_not_found", status_code=404)


@transaction.atomic
def post_journal(*, journal_id, organization, actor=None) -> JournalEntry:
    """The single authoritative path to turn a DRAFT journal into a posted,
    immutable accounting fact. See accounting/CLAUDE.md — no other module may
    create authoritative GL effects directly.

    Idempotent: posting an already-POSTED journal is a no-op that returns the
    existing (unchanged) journal rather than erroring or double-posting —
    this is the whole idempotency story for posting (see accounting/CLAUDE.md),
    deliberately not a second Idempotency-Key system layered on top.
    """
    journal = _get_journal_for_update(journal_id=journal_id, organization=organization)

    if journal.status == JournalStatus.POSTED:
        return journal
    if journal.status != JournalStatus.DRAFT:
        raise ApplicationError(
            f"Cannot post a journal entry in status '{journal.status}'.", code="journal_invalid_status"
        )

    lines = list(
        JournalLine.objects.select_for_update().select_related("account").filter(journal_entry=journal)
    )
    if len(lines) < 2:
        raise ApplicationError("A journal entry needs at least two lines to post.", code="journal_too_few_lines")

    for line in lines:
        if line.account.organization_id != organization.id:
            raise ApplicationError("All accounts must belong to the posting organization.", code="account_cross_org")
        if not line.account.is_active:
            raise ApplicationError(
                f"Account {line.account.code} is inactive and cannot receive postings.", code="account_inactive"
            )

    total_debit = sum((line.base_debit for line in lines), Decimal("0"))
    total_credit = sum((line.base_credit for line in lines), Decimal("0"))
    if total_debit != total_credit:
        raise ApplicationError("Total debits must equal total credits to post.", code="journal_unbalanced")
    if total_debit <= 0:
        raise ApplicationError("A journal entry must have a nonzero total to post.", code="journal_zero_total")

    fiscal_year = get_fiscal_year_for_date(organization=organization, posting_date=journal.posting_date)
    assert_period_open(fiscal_year)

    journal_number = allocate_sequence_number(
        organization_id=organization.id, key=JOURNAL_NUMBER_SEQUENCE_KEY, prefix="JE-"
    )

    journal.journal_number = journal_number
    journal.fiscal_year = fiscal_year
    journal.status = JournalStatus.POSTED
    journal.posted_by = actor
    journal.posted_at = timezone.now()
    journal.save(
        update_fields=["journal_number", "fiscal_year", "status", "posted_by", "posted_at", "updated_at"]
    )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="accounting.JournalEntry",
        object_id=journal.id,
        changes={"journal_number": journal_number, "total": str(total_debit)},
    )
    return journal


@transaction.atomic
def reverse_journal(*, journal_id, organization, actor=None, posting_date=None, memo: str = "") -> JournalEntry:
    """Creates and posts a new journal that exactly mirrors the financial
    impact of a posted journal, preserving the original untouched. A journal
    can be reversed at most once (JournalEntry.reverses is a OneToOneField)."""
    original = _get_journal_for_update(journal_id=journal_id, organization=organization)

    if original.status != JournalStatus.POSTED:
        raise ApplicationError(
            f"Cannot reverse a journal entry in status '{original.status}'.", code="journal_invalid_status"
        )
    if hasattr(original, "reversal"):
        raise ApplicationError("This journal entry has already been reversed.", code="journal_already_reversed")

    original_lines = list(JournalLine.objects.select_related("account").filter(journal_entry=original))

    reversal = JournalEntry.objects.create(
        organization=organization,
        posting_date=posting_date or timezone.now().date(),
        currency=original.currency,
        exchange_rate=original.exchange_rate,
        reference=original.reference,
        memo=memo or f"Reversal of {original.journal_number}",
        source_type=original.source_type,
        source_id=original.source_id,
        status=JournalStatus.DRAFT,
        created_by=actor,
        reverses=original,
    )
    for index, line in enumerate(original_lines, start=1):
        JournalLine.objects.create(
            organization=organization,
            journal_entry=reversal,
            account=line.account,
            line_number=index,
            description=f"Reversal: {line.description}" if line.description else "Reversal",
            debit=line.credit,
            credit=line.debit,
            base_debit=line.base_credit,
            base_credit=line.base_debit,
        )

    reversal = post_journal(journal_id=reversal.id, organization=organization, actor=actor)

    original.status = JournalStatus.REVERSED
    original.save(update_fields=["status", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="accounting.JournalEntry",
        object_id=original.id,
        changes={"reversal_journal_number": reversal.journal_number},
    )
    return reversal
