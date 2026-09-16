"""Derived banking figures. Nothing here is stored.

THREE BALANCES, AND WHY THEY ARE THREE.

    book       what our ledger says, from posted JournalLines
    statement  what the bank says, from imported statement lines
    cleared    the part of the statement we have explained

A reconciliation is the exercise of accounting for the gaps between them, so
collapsing any two into one stored number would delete the thing being
reconciled. `cleared` differing from `statement` means work left to do;
`book` differing from `cleared` means genuine timing differences —
outstanding cheques, deposits in transit — which are normal and are reported
rather than treated as errors.

SIGN. All three use the statement convention (positive = money held,
negative = money owed), NOT each account's normal balance. That is why
`get_book_balance` sums debit minus credit directly instead of calling
`accounting.selectors.get_account_activity`, which flips the sign for
credit-normal accounts: a credit card's book balance has to come out negative
here so it can be compared against a card statement without a special case.
"""

from decimal import Decimal

from django.db.models import Sum

from accounting.models.journal import JournalLine, JournalStatus
from banking.models.match import COUNTERPART_FIELDS, BankTransactionMatch
from banking.models.statement import BankTransaction, BankTransactionStatus


def _account_transactions(*, bank_account, as_of=None, from_date=None):
    qs = BankTransaction.objects.filter(bank_account=bank_account)
    if bank_account.opening_balance_date is not None:
        qs = qs.filter(transaction_date__gt=bank_account.opening_balance_date)
    if from_date is not None:
        qs = qs.filter(transaction_date__gte=from_date)
    if as_of is not None:
        qs = qs.filter(transaction_date__lte=as_of)
    return qs


def _sum(qs) -> Decimal:
    return qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def get_book_balance(*, bank_account, as_of=None) -> Decimal:
    """Ledger balance of the linked GL account, signed as a statement would
    show it (debits positive). Derived from posted journal lines only."""
    qs = JournalLine.objects.filter(
        account_id=bank_account.account_id, journal_entry__status=JournalStatus.POSTED
    )
    if as_of is not None:
        qs = qs.filter(journal_entry__posting_date__lte=as_of)
    totals = qs.aggregate(debit=Sum("base_debit"), credit=Sum("base_credit"))
    return (totals["debit"] or Decimal("0")) - (totals["credit"] or Decimal("0"))


def get_statement_balance(*, bank_account, as_of=None) -> Decimal:
    """What the bank's own running balance should read, from the lines we hold.

    EXCLUDED lines are left out because excluding a line asserts it is not a
    real transaction (an import artefact or a row belonging to someone else).
    A line that IS real but has no counterpart in the books must be
    categorized, not excluded — see services/transactions.py.
    """
    movements = _sum(_account_transactions(bank_account=bank_account, as_of=as_of).exclude(
        status=BankTransactionStatus.EXCLUDED
    ))
    return bank_account.opening_balance + movements


def get_cleared_balance(*, bank_account, as_of=None) -> Decimal:
    """The part of the statement balance we have actually explained."""
    movements = _sum(
        _account_transactions(bank_account=bank_account, as_of=as_of).filter(
            status=BankTransactionStatus.MATCHED
        )
    )
    return bank_account.opening_balance + movements


def get_matched_amount(*, transaction, exclude_match_id=None) -> Decimal:
    """Total of CONFIRMED matches against one statement line.

    Unconfirmed suggestions are excluded on purpose: several competing
    suggestions for the same line are normal and expected, and counting them
    would make an ordinary two-candidate line look over-explained.
    """
    qs = BankTransactionMatch.objects.filter(transaction=transaction, is_confirmed=True)
    if exclude_match_id is not None:
        qs = qs.exclude(pk=exclude_match_id)
    return qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def get_unexplained_amount(*, transaction) -> Decimal:
    return abs(transaction.amount) - get_matched_amount(transaction=transaction)


def get_counterpart_matched_amount(
    *, counterpart_field: str, counterpart, exclude_match_id=None, bank_account=None
) -> Decimal:
    """Total already matched against a book document, across every statement
    line. Stops one payment being used to explain two different deposits.

    `bank_account` narrows the question to one side of the movement, and
    exists for transfers. A transfer legitimately appears on TWO statements —
    it leaves one account and arrives in another — so its capacity is one
    full amount PER LEG, not one in total. Asking the unqualified question
    for a transfer would refuse the second leg and leave every internal
    movement permanently half-reconciled.
    """
    if counterpart_field not in COUNTERPART_FIELDS:
        raise ValueError(f"Unknown counterpart field '{counterpart_field}'.")
    qs = BankTransactionMatch.objects.filter(**{counterpart_field: counterpart}, is_confirmed=True)
    if bank_account is not None:
        qs = qs.filter(transaction__bank_account=bank_account)
    if exclude_match_id is not None:
        qs = qs.exclude(pk=exclude_match_id)
    return qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def get_bank_account_summary(*, bank_account, as_of=None) -> dict:
    book = get_book_balance(bank_account=bank_account, as_of=as_of)
    statement = get_statement_balance(bank_account=bank_account, as_of=as_of)
    cleared = get_cleared_balance(bank_account=bank_account, as_of=as_of)
    open_lines = _account_transactions(bank_account=bank_account, as_of=as_of).filter(
        status__in=[BankTransactionStatus.UNMATCHED, BankTransactionStatus.SUGGESTED]
    )
    return {
        "bank_account": bank_account,
        "as_of": as_of,
        "book_balance": book,
        "statement_balance": statement,
        "cleared_balance": cleared,
        # What the statement shows that we have not explained yet.
        "unexplained_statement_amount": statement - cleared,
        # Book entries the bank has not shown yet: uncleared cheques and
        # deposits in transit. Expected to be non-zero; not an error.
        "uncleared_book_amount": book - cleared,
        "open_transaction_count": open_lines.count(),
    }


def get_reconciliation_summary(*, reconciliation) -> dict:
    """Everything `complete_reconciliation` checks, as a read-only preview so
    a user can see what is blocking closure before attempting it."""
    bank_account = reconciliation.bank_account
    cleared = get_cleared_balance(bank_account=bank_account, as_of=reconciliation.statement_end_date)
    in_period = BankTransaction.objects.filter(
        bank_account=bank_account,
        transaction_date__gte=reconciliation.statement_start_date,
        transaction_date__lte=reconciliation.statement_end_date,
    )
    open_lines = in_period.filter(
        status__in=[BankTransactionStatus.UNMATCHED, BankTransactionStatus.SUGGESTED]
    )
    return {
        "reconciliation": reconciliation,
        "statement_closing_balance": reconciliation.statement_closing_balance,
        "cleared_balance": cleared,
        "difference": reconciliation.statement_closing_balance - cleared,
        "book_balance": get_book_balance(
            bank_account=bank_account, as_of=reconciliation.statement_end_date
        ),
        "transaction_count": in_period.count(),
        "open_transaction_count": open_lines.count(),
        "is_balanced": reconciliation.statement_closing_balance == cleared,
        "can_complete": reconciliation.statement_closing_balance == cleared and not open_lines.exists(),
    }


def get_open_transactions(*, bank_account=None, organization=None):
    """Statement lines still needing a decision, oldest first — the work queue."""
    qs = BankTransaction.objects.filter(
        status__in=[BankTransactionStatus.UNMATCHED, BankTransactionStatus.SUGGESTED]
    )
    if bank_account is not None:
        qs = qs.filter(bank_account=bank_account)
    if organization is not None:
        qs = qs.filter(organization=organization)
    return qs.order_by("transaction_date", "created_at")
