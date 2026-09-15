"""Read-only queries over posted accounting facts.

Deliberately NOT backed by a separate mutable ledger/balance table: every
figure here is derived, on demand, from posted JournalLine rows so there is
no projection that can drift from the authoritative journal history (see
accounting/CLAUDE.md). Revisit only if a proven performance need justifies a
materialized snapshot — not preemptively.
"""

from decimal import Decimal

from django.db.models import Sum

from accounting.models.account import Account
from accounting.models.journal import JournalLine, JournalStatus


def _posted_lines_for_account(*, account, up_to_date=None, from_date=None):
    qs = JournalLine.objects.filter(account=account, journal_entry__status=JournalStatus.POSTED)
    if from_date is not None:
        qs = qs.filter(journal_entry__posting_date__gte=from_date)
    if up_to_date is not None:
        qs = qs.filter(journal_entry__posting_date__lte=up_to_date)
    return qs


def get_account_activity(*, account: Account, from_date=None, to_date=None):
    """Opening balance (everything before from_date), period debit/credit,
    and closing balance for one account, entirely from posted JournalLines."""
    opening_totals = _posted_lines_for_account(account=account, up_to_date=None, from_date=None)
    if from_date is not None:
        opening_totals = opening_totals.filter(journal_entry__posting_date__lt=from_date)
    else:
        opening_totals = opening_totals.none()
    opening = opening_totals.aggregate(debit=Sum("base_debit"), credit=Sum("base_credit"))
    opening_debit = opening["debit"] or Decimal("0")
    opening_credit = opening["credit"] or Decimal("0")
    opening_balance = (opening_debit - opening_credit) if account.is_debit_normal else (opening_credit - opening_debit)

    period = _posted_lines_for_account(account=account, from_date=from_date, up_to_date=to_date).aggregate(
        debit=Sum("base_debit"), credit=Sum("base_credit")
    )
    period_debit = period["debit"] or Decimal("0")
    period_credit = period["credit"] or Decimal("0")

    net = (period_debit - period_credit) if account.is_debit_normal else (period_credit - period_debit)
    closing_balance = opening_balance + net

    return {
        "account": account,
        "opening_balance": opening_balance,
        "period_debit": period_debit,
        "period_credit": period_credit,
        "closing_balance": closing_balance,
    }


def get_account_running_ledger(*, account: Account, from_date=None, to_date=None):
    """Ordered list of posted lines for an account with a running balance,
    starting from the opening balance as of from_date."""
    activity = get_account_activity(account=account, from_date=from_date, to_date=to_date)
    running = activity["opening_balance"]
    entries = []
    lines = (
        _posted_lines_for_account(account=account, from_date=from_date, up_to_date=to_date)
        .select_related("journal_entry")
        .order_by("journal_entry__posting_date", "journal_entry__created_at", "line_number")
    )
    for line in lines:
        signed = (line.base_debit - line.base_credit) if account.is_debit_normal else (line.base_credit - line.base_debit)
        running += signed
        entries.append(
            {
                "journal_entry": line.journal_entry,
                "line": line,
                "debit": line.base_debit,
                "credit": line.base_credit,
                "running_balance": running,
            }
        )
    return {"opening_balance": activity["opening_balance"], "entries": entries, "closing_balance": running}


def get_trial_balance(*, organization, as_of_date, from_date=None):
    """Authoritative Trial Balance: one row per account with opening/period/
    closing figures, derived purely from posted JournalLines. Proves
    total debits == total credits across the organization's ledger."""
    accounts = Account.objects.filter(organization=organization).order_by("code")

    rows = []
    total_period_debit = Decimal("0")
    total_period_credit = Decimal("0")
    total_closing_debit = Decimal("0")
    total_closing_credit = Decimal("0")

    for account in accounts:
        activity = get_account_activity(account=account, from_date=from_date, to_date=as_of_date)
        closing = activity["closing_balance"]
        # `closing` is signed in the account's own normal-balance direction
        # (positive = normal balance side). A negative balance means the
        # account has swung to its non-normal side, so it lands in the other
        # column instead of going negative.
        if closing == Decimal("0"):
            closing_debit = closing_credit = Decimal("0")
        elif account.is_debit_normal:
            closing_debit, closing_credit = (closing, Decimal("0")) if closing > 0 else (Decimal("0"), -closing)
        else:
            closing_credit, closing_debit = (closing, Decimal("0")) if closing > 0 else (Decimal("0"), -closing)

        rows.append(
            {
                "account": account,
                "opening_balance": activity["opening_balance"],
                "period_debit": activity["period_debit"],
                "period_credit": activity["period_credit"],
                "closing_debit": closing_debit,
                "closing_credit": closing_credit,
            }
        )
        total_period_debit += activity["period_debit"]
        total_period_credit += activity["period_credit"]
        total_closing_debit += closing_debit
        total_closing_credit += closing_credit

    return {
        "rows": rows,
        "total_period_debit": total_period_debit,
        "total_period_credit": total_period_credit,
        "total_closing_debit": total_closing_debit,
        "total_closing_credit": total_closing_credit,
        "is_balanced": total_closing_debit == total_closing_credit,
    }
