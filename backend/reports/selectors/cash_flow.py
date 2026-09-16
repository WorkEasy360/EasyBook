"""Cash Flow Statement — indirect method (PHASE 8 spec §5).

CASH IDENTIFICATION. "Which GL accounts are cash" is not inferred from
account name or type — `banking.BankAccount` (`account` OneToOneField, see
banking/CLAUDE.md) is the one place that fact is recorded explicitly, so this
selector reads that link rather than guessing. Only `kind=BANK` accounts
count as cash/cash-equivalents; `CREDIT_CARD` accounts are liabilities, not
cash. If an organization has configured no BANK-kind BankAccount, this
selector refuses to guess and raises — see get_cash_flow_statement.

INDIRECT METHOD, PROVEN TO RECONCILE. Every other Balance Sheet account is
already split current/non-current the same explicit way Balance Sheet is
(reports.selectors.classification — account_subtype, default = current,
never inferred from account NAME). Given the Balance Sheet identity

    Assets = Liabilities + Equity + CumulativeNetIncome

(see reports/selectors/balance_sheet.py), splitting Assets into
Cash + NonCashCurrent + NonCashFixed and Liabilities into
Current + LongTerm, then differencing over the period, gives:

    Operating  = NetProfit(period) - Δ(current non-cash assets) + Δ(current liabilities)
    Investing  = -Δ(fixed/other assets)
    Financing  = Δ(long-term liabilities) + Δ(stored equity accounts)

and Operating + Investing + Financing == Closing Cash - Opening Cash EXACTLY,
by construction — not an approximation. See
reports/tests/test_cash_flow.py::test_cash_flow_reconciles_to_closing_cash.
"""

import datetime
from decimal import Decimal

from accounting.models.account import Account, AccountType
from core.exceptions import ApplicationError
from reports.selectors.balance_sheet import closing_aggregates_by_account
from reports.selectors.classification import classify_asset, classify_liability
from reports.selectors.pnl import get_profit_and_loss

ZERO = Decimal("0")
ONE_DAY = datetime.timedelta(days=1)


def _cash_bank_accounts(*, organization):
    from banking.models.bank_account import BankAccount, BankAccountKind

    return list(BankAccount.objects.filter(organization=organization, kind=BankAccountKind.BANK))


def _cash_balance(*, bank_accounts, as_of) -> Decimal:
    from banking.selectors import get_book_balance

    return sum((get_book_balance(bank_account=ba, as_of=as_of) for ba in bank_accounts), ZERO)


def _balance(debit: Decimal, credit: Decimal, *, is_debit_normal: bool) -> Decimal:
    return (debit - credit) if is_debit_normal else (credit - debit)


def _movement_rows(*, organization, opening_as_of, closing_as_of, account_type, exclude_account_ids, classify, is_debit_normal):
    """Per-account (closing - opening) balance movement for one account_type,
    excluding `exclude_account_ids` (the cash accounts), grouped by the
    given `classify` function. Returns {bucket: [{account_id, code, name,
    delta}]}."""
    opening = closing_aggregates_by_account(
        organization=organization, as_of_date=opening_as_of, account_types=[account_type]
    )
    closing = closing_aggregates_by_account(
        organization=organization, as_of_date=closing_as_of, account_types=[account_type]
    )
    account_ids = (set(opening) | set(closing)) - exclude_account_ids
    accounts = {a.id: a for a in Account.objects.filter(organization=organization, id__in=account_ids)}

    buckets: dict[str, list] = {}
    for account_id in account_ids:
        account = accounts.get(account_id)
        if account is None:
            continue
        o_debit, o_credit = opening.get(account_id, (ZERO, ZERO))
        c_debit, c_credit = closing.get(account_id, (ZERO, ZERO))
        opening_balance = _balance(o_debit, o_credit, is_debit_normal=is_debit_normal)
        closing_balance = _balance(c_debit, c_credit, is_debit_normal=is_debit_normal)
        delta = closing_balance - opening_balance
        if delta == ZERO:
            continue
        bucket = classify(account)
        buckets.setdefault(bucket, []).append(
            {"account_id": account.id, "account_code": account.code, "account_name": account.name, "delta": delta}
        )
    for rows in buckets.values():
        rows.sort(key=lambda r: r["account_code"])
    return buckets


def get_cash_flow_statement(*, organization, from_date, to_date) -> dict:
    if from_date is None:
        raise ApplicationError("from_date is required for the Cash Flow Statement.", code="from_date_required")

    bank_accounts = _cash_bank_accounts(organization=organization)
    if not bank_accounts:
        raise ApplicationError(
            "No cash/bank account is configured (banking.BankAccount with kind=bank). "
            "Cash Flow requires an explicit cash account link — see reports/CLAUDE.md.",
            code="cash_accounts_not_configured",
        )
    cash_account_ids = {ba.account_id for ba in bank_accounts}
    opening_as_of = from_date - ONE_DAY

    opening_cash = _cash_balance(bank_accounts=bank_accounts, as_of=opening_as_of)
    closing_cash = _cash_balance(bank_accounts=bank_accounts, as_of=to_date)
    net_change_in_cash = closing_cash - opening_cash

    net_profit = get_profit_and_loss(organization=organization, from_date=from_date, to_date=to_date)["totals"][
        "net_profit"
    ]

    asset_buckets = _movement_rows(
        organization=organization,
        opening_as_of=opening_as_of,
        closing_as_of=to_date,
        account_type=AccountType.ASSET,
        exclude_account_ids=cash_account_ids,
        classify=classify_asset,
        is_debit_normal=True,
    )
    liability_buckets = _movement_rows(
        organization=organization,
        opening_as_of=opening_as_of,
        closing_as_of=to_date,
        account_type=AccountType.LIABILITY,
        exclude_account_ids=cash_account_ids,
        classify=classify_liability,
        is_debit_normal=False,
    )
    equity_buckets = _movement_rows(
        organization=organization,
        opening_as_of=opening_as_of,
        closing_as_of=to_date,
        account_type=AccountType.EQUITY,
        exclude_account_ids=cash_account_ids,
        classify=lambda account: "equity",
        is_debit_normal=False,
    )

    current_asset_rows = asset_buckets.get("current_assets", [])
    fixed_asset_rows = asset_buckets.get("fixed_assets", []) + asset_buckets.get("other_assets", [])
    current_liability_rows = liability_buckets.get("current_liabilities", [])
    long_term_liability_rows = liability_buckets.get("long_term_liabilities", [])
    equity_rows = equity_buckets.get("equity", [])

    # Working-capital adjustments, signed as their CASH effect (an asset
    # increase consumes cash; a liability increase releases cash) — the
    # opposite sign of the account's own balance delta for assets.
    working_capital_adjustments = [
        {**row, "amount": -row["delta"]} for row in current_asset_rows
    ] + [
        {**row, "amount": row["delta"]} for row in current_liability_rows
    ]
    operating_total = net_profit + sum((row["amount"] for row in working_capital_adjustments), ZERO)

    investing_items = [{**row, "amount": -row["delta"]} for row in fixed_asset_rows]
    investing_total = sum((row["amount"] for row in investing_items), ZERO)

    financing_items = [{**row, "amount": row["delta"]} for row in long_term_liability_rows] + [
        {**row, "amount": row["delta"]} for row in equity_rows
    ]
    financing_total = sum((row["amount"] for row in financing_items), ZERO)

    for group in (working_capital_adjustments, investing_items, financing_items):
        for row in group:
            row.pop("delta", None)

    return {
        "period": {"from_date": from_date, "to_date": to_date},
        "opening_cash": opening_cash,
        "operating_activities": {
            "net_profit": net_profit,
            "adjustments": working_capital_adjustments,
            "total": operating_total,
        },
        "investing_activities": {"items": investing_items, "total": investing_total},
        "financing_activities": {"items": financing_items, "total": financing_total},
        "net_change_in_cash": net_change_in_cash,
        "closing_cash": closing_cash,
        "cash_accounts": [
            {"bank_account_id": ba.id, "account_id": ba.account_id, "name": ba.name} for ba in bank_accounts
        ],
        "reconciles": (operating_total + investing_total + financing_total) == net_change_in_cash,
    }
