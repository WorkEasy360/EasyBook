"""Profit & Loss — derived entirely from posted `accounting.JournalLine`
rows, never from Sales invoice totals (PHASE 8 spec §3). One grouped SQL
aggregate for the whole statement (not a per-account loop) to keep this
report cheap at realistic account-count scale — see reports/CLAUDE.md
PERFORMANCE.
"""

from decimal import Decimal

from django.db.models import Sum

from accounting.models.account import Account, AccountType
from accounting.models.journal import JournalLine, JournalStatus
from reports.selectors.classification import classify_expense, classify_income
from reports.selectors.params import compute_variance

ZERO = Decimal("0")

_SECTION_ORDER = ("operating_income", "other_income", "cogs", "operating_expenses", "other_expense")


def _period_aggregates_by_account(*, organization, from_date, to_date):
    """{account_id: (debit, credit)} for posted lines on INCOME/EXPENSE
    accounts in [from_date, to_date] — a single grouped query, not N per-
    account queries."""
    qs = JournalLine.objects.filter(
        organization=organization,
        journal_entry__status=JournalStatus.POSTED,
        account__account_type__in=[AccountType.INCOME, AccountType.EXPENSE],
    )
    if from_date is not None:
        qs = qs.filter(journal_entry__posting_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(journal_entry__posting_date__lte=to_date)
    rows = qs.values("account_id").annotate(debit=Sum("base_debit"), credit=Sum("base_credit"))
    return {row["account_id"]: (row["debit"] or ZERO, row["credit"] or ZERO) for row in rows}


def _build_sections(*, organization, from_date, to_date):
    aggregates = _period_aggregates_by_account(organization=organization, from_date=from_date, to_date=to_date)
    if not aggregates:
        return {key: [] for key in _SECTION_ORDER}

    accounts = {
        account.id: account
        for account in Account.objects.filter(organization=organization, id__in=aggregates.keys())
    }

    sections = {key: [] for key in _SECTION_ORDER}
    for account_id, (debit, credit) in aggregates.items():
        account = accounts.get(account_id)
        if account is None:
            continue
        if account.account_type == AccountType.INCOME:
            amount = credit - debit  # credit-normal
            section = classify_income(account)
        else:
            amount = debit - credit  # debit-normal
            section = classify_expense(account)
        if amount == ZERO:
            continue
        sections[section].append(
            {
                "account_id": account.id,
                "account_code": account.code,
                "account_name": account.name,
                "amount": amount,
            }
        )

    for rows in sections.values():
        rows.sort(key=lambda row: row["account_code"])
    return sections


def _totals(sections: dict) -> dict:
    def total(key):
        return sum((row["amount"] for row in sections[key]), ZERO)

    revenue = total("operating_income")
    other_income = total("other_income")
    cogs = total("cogs")
    operating_expenses = total("operating_expenses")
    other_expense = total("other_expense")

    gross_profit = revenue - cogs
    operating_profit = gross_profit - operating_expenses
    net_profit = operating_profit + other_income - other_expense

    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "operating_expenses": operating_expenses,
        "operating_profit": operating_profit,
        "other_income": other_income,
        "other_expense": other_expense,
        "net_profit": net_profit,
    }


def get_profit_and_loss(*, organization, from_date, to_date, comparison_from=None, comparison_to=None) -> dict:
    """Returns section rows (for drill-down by account_id) and totals for the
    period, plus a comparison-period totals block with variance when
    `comparison_from`/`comparison_to` are given (PHASE 8 spec §17)."""
    sections = _build_sections(organization=organization, from_date=from_date, to_date=to_date)
    totals = _totals(sections)

    result = {
        "period": {"from_date": from_date, "to_date": to_date},
        "sections": sections,
        "totals": totals,
    }

    if comparison_from is not None or comparison_to is not None:
        comparison_sections = _build_sections(
            organization=organization, from_date=comparison_from, to_date=comparison_to
        )
        comparison_totals = _totals(comparison_sections)
        result["comparison_period"] = {"from_date": comparison_from, "to_date": comparison_to}
        result["comparison_totals"] = comparison_totals
        result["variance"] = {
            key: compute_variance(current=totals[key], previous=comparison_totals[key]) for key in totals
        }

    return result
