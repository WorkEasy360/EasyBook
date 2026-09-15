from decimal import Decimal

from django.db import transaction

from accounting.models.account import Account
from accounting.models.journal import JournalEntry, JournalLine, JournalStatus
from core.exceptions import ApplicationError


def _validate_line_shape(line: dict, index: int) -> None:
    debit = line.get("debit", Decimal("0")) or Decimal("0")
    credit = line.get("credit", Decimal("0")) or Decimal("0")
    if debit < 0 or credit < 0:
        raise ApplicationError(f"Line {index}: debit/credit cannot be negative.", code="journal_line_negative")
    if debit > 0 and credit > 0:
        raise ApplicationError(
            f"Line {index}: a line cannot have both debit and credit.", code="journal_line_both_sides"
        )
    if debit == 0 and credit == 0:
        raise ApplicationError(f"Line {index}: a line cannot be zero on both sides.", code="journal_line_zero")


@transaction.atomic
def create_draft_journal(
    *,
    organization,
    posting_date,
    currency,
    lines: list[dict],
    reference: str = "",
    memo: str = "",
    source_type: str = "",
    source_id: str = "",
    exchange_rate: Decimal = Decimal("1"),
    created_by=None,
) -> JournalEntry:
    """Creates a DRAFT journal with its lines. Deliberately does NOT require
    debit == credit here — that invariant is enforced at posting time
    (accounting.services.posting.post_journal), so a draft can be a
    work-in-progress. See accounting/CLAUDE.md."""
    if len(lines) < 2:
        raise ApplicationError("A journal entry needs at least two lines.", code="journal_too_few_lines")

    account_ids = {line["account_id"] for line in lines}
    accounts = {a.id: a for a in Account.objects.filter(id__in=account_ids)}
    if len(accounts) != len(account_ids):
        raise ApplicationError("One or more accounts were not found in this organization.", code="account_not_found")
    for account in accounts.values():
        if not account.is_active:
            raise ApplicationError(
                f"Account {account.code} is inactive and cannot receive postings.", code="account_inactive"
            )

    journal = JournalEntry.objects.create(
        organization=organization,
        posting_date=posting_date,
        currency=currency,
        exchange_rate=exchange_rate,
        reference=reference,
        memo=memo,
        source_type=source_type,
        source_id=source_id,
        status=JournalStatus.DRAFT,
        created_by=created_by,
    )
    for index, line in enumerate(lines, start=1):
        _validate_line_shape(line, index)
        debit = line.get("debit", Decimal("0")) or Decimal("0")
        credit = line.get("credit", Decimal("0")) or Decimal("0")
        JournalLine.objects.create(
            organization=organization,
            journal_entry=journal,
            account=accounts[line["account_id"]],
            line_number=index,
            description=line.get("description", ""),
            debit=debit,
            credit=credit,
            base_debit=debit * exchange_rate,
            base_credit=credit * exchange_rate,
        )
    return journal


@transaction.atomic
def replace_draft_lines(*, journal: JournalEntry, lines: list[dict]) -> JournalEntry:
    """Replaces all lines on a DRAFT journal. Raises if the journal is not a
    draft — posted journals are immutable (accounting/CLAUDE.md)."""
    if journal.status != JournalStatus.DRAFT:
        raise ApplicationError("Only draft journal entries can be modified.", code="journal_not_draft")
    if len(lines) < 2:
        raise ApplicationError("A journal entry needs at least two lines.", code="journal_too_few_lines")

    account_ids = {line["account_id"] for line in lines}
    accounts = {a.id: a for a in Account.objects.filter(id__in=account_ids)}
    if len(accounts) != len(account_ids):
        raise ApplicationError("One or more accounts were not found in this organization.", code="account_not_found")

    journal.lines.all().delete()
    for index, line in enumerate(lines, start=1):
        _validate_line_shape(line, index)
        debit = line.get("debit", Decimal("0")) or Decimal("0")
        credit = line.get("credit", Decimal("0")) or Decimal("0")
        JournalLine.objects.create(
            organization=journal.organization,
            journal_entry=journal,
            account=accounts[line["account_id"]],
            line_number=index,
            description=line.get("description", ""),
            debit=debit,
            credit=credit,
            base_debit=debit * journal.exchange_rate,
            base_credit=credit * journal.exchange_rate,
        )
    return journal
