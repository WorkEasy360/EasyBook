"""Balance Sheet as of a date — derived from posted `accounting.JournalLine`
closing balances (PHASE 8 spec §4).

CURRENT-PERIOD RESULT. This system never posts a year-end closing journal
(no `retained_earnings` account is auto-credited — see accounting/CLAUDE.md:
"no mutable authoritative balance field anywhere"). Income/Expense account
balances simply accumulate from inception. Because every posted journal
balances (debit == credit), that makes

    Assets = Liabilities + Equity + (cumulative Income - cumulative Expense)

a mathematical identity, not an approximation — see
`reports/tests/test_balance_sheet.py::test_balance_sheet_balances` for the
proof. So "current period result" is reported as a computed
`current_earnings` equity line (cumulative net profit from inception through
`as_of_date`, via `reports.selectors.pnl.get_profit_and_loss`), never a
stored account balance — consistent with root CLAUDE.md's "no second source
of truth".
"""

from decimal import Decimal

from django.db.models import Sum

from accounting.models.account import Account, AccountType
from accounting.models.journal import JournalLine, JournalStatus
from reports.selectors.classification import classify_asset, classify_liability
from reports.selectors.pnl import get_profit_and_loss

ZERO = Decimal("0")

_ASSET_SECTIONS = ("current_assets", "fixed_assets", "other_assets")
_LIABILITY_SECTIONS = ("current_liabilities", "long_term_liabilities")


def closing_aggregates_by_account(*, organization, as_of_date, account_types):
    """{account_id: (debit, credit)} for every posted line up to and
    including as_of_date — the full accumulated balance since inception, not
    merely a period slice. One grouped query. Shared with
    reports.selectors.cash_flow, which needs the same closing-balance-as-of
    building block for its indirect-method working-capital movements."""
    qs = JournalLine.objects.filter(
        organization=organization,
        journal_entry__status=JournalStatus.POSTED,
        journal_entry__posting_date__lte=as_of_date,
        account__account_type__in=account_types,
    )
    rows = qs.values("account_id").annotate(debit=Sum("base_debit"), credit=Sum("base_credit"))
    return {row["account_id"]: (row["debit"] or ZERO, row["credit"] or ZERO) for row in rows}


def get_balance_sheet(*, organization, as_of_date) -> dict:
    aggregates = closing_aggregates_by_account(
        organization=organization, as_of_date=as_of_date, account_types=[AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY]
    )
    accounts = {
        account.id: account
        for account in Account.objects.filter(organization=organization, id__in=aggregates.keys())
    }

    asset_sections = {key: [] for key in _ASSET_SECTIONS}
    liability_sections = {key: [] for key in _LIABILITY_SECTIONS}
    equity_rows = []

    for account_id, (debit, credit) in aggregates.items():
        account = accounts.get(account_id)
        if account is None:
            continue
        row = {
            "account_id": account.id,
            "account_code": account.code,
            "account_name": account.name,
        }
        if account.account_type == AccountType.ASSET:
            balance = debit - credit
            if balance == ZERO:
                continue
            asset_sections[classify_asset(account)].append({**row, "amount": balance})
        elif account.account_type == AccountType.LIABILITY:
            balance = credit - debit
            if balance == ZERO:
                continue
            liability_sections[classify_liability(account)].append({**row, "amount": balance})
        else:  # EQUITY
            balance = credit - debit
            if balance == ZERO:
                continue
            equity_rows.append({**row, "amount": balance})

    for rows in list(asset_sections.values()) + list(liability_sections.values()) + [equity_rows]:
        rows.sort(key=lambda r: r["account_code"])

    current_earnings = get_profit_and_loss(
        organization=organization, from_date=None, to_date=as_of_date
    )["totals"]["net_profit"]
    if current_earnings != ZERO:
        equity_rows.append(
            {
                "account_id": None,
                "account_code": "",
                "account_name": "Current Period Earnings",
                "amount": current_earnings,
            }
        )

    total_assets = sum((row["amount"] for rows in asset_sections.values() for row in rows), ZERO)
    total_liabilities = sum((row["amount"] for rows in liability_sections.values() for row in rows), ZERO)
    total_equity = sum((row["amount"] for row in equity_rows), ZERO)

    return {
        "as_of_date": as_of_date,
        "assets": asset_sections,
        "liabilities": liability_sections,
        "equity": equity_rows,
        "totals": {
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "total_liabilities_and_equity": total_liabilities + total_equity,
        },
        "is_balanced": total_assets == (total_liabilities + total_equity),
    }
