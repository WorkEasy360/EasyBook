"""Explicit, opt-in account classification for P&L/Balance Sheet/Cash Flow
grouping beyond the base `Account.account_type`.

`accounting.Account.account_subtype` is a free-text field with no seeded
chart of accounts and no enforced vocabulary (accounting/models/account.py).
Root CLAUDE.md forbids inferring classifications from account NAMES, so this
module never inspects `Account.name` — it reads `account_subtype` against a
small documented vocabulary and nothing else. An account whose subtype is
blank, or does not match a known label, falls into the documented DEFAULT
bucket for its account_type — never dropped from a statement, and never
guessed at from free text. Organizations that want fixed-asset/long-term
liability/COGS/other-income splits set `account_subtype` to one of these
labels; see reports/CLAUDE.md for the operator-facing version of this list.
"""

from accounting.models.account import AccountType

# Income subtypes (AccountType.INCOME)
SUBTYPE_OTHER_INCOME = "other_income"

# Expense subtypes (AccountType.EXPENSE)
SUBTYPE_COGS = {"cogs", "cost_of_goods_sold"}
SUBTYPE_OTHER_EXPENSE = "other_expense"

# Asset subtypes (AccountType.ASSET)
SUBTYPE_FIXED_ASSET = "fixed_asset"
SUBTYPE_OTHER_ASSET = "other_asset"
SUBTYPE_CURRENT_ASSET = "current_asset"

# Liability subtypes (AccountType.LIABILITY)
SUBTYPE_LONG_TERM_LIABILITY = "long_term_liability"
SUBTYPE_CURRENT_LIABILITY = "current_liability"


def _normalized(account) -> str:
    return (account.account_subtype or "").strip().lower()


def classify_income(account) -> str:
    """'operating_income' (default) or 'other_income'."""
    return "other_income" if _normalized(account) == SUBTYPE_OTHER_INCOME else "operating_income"


def classify_expense(account) -> str:
    """'cogs', 'other_expense', or 'operating_expenses' (default)."""
    subtype = _normalized(account)
    if subtype in SUBTYPE_COGS:
        return "cogs"
    if subtype == SUBTYPE_OTHER_EXPENSE:
        return "other_expense"
    return "operating_expenses"


def classify_asset(account) -> str:
    """'fixed_asset', 'other_asset', or 'current_asset' (default)."""
    subtype = _normalized(account)
    if subtype == SUBTYPE_FIXED_ASSET:
        return "fixed_assets"
    if subtype == SUBTYPE_OTHER_ASSET:
        return "other_assets"
    return "current_assets"


def classify_liability(account) -> str:
    """'long_term_liability' or 'current_liability' (default)."""
    subtype = _normalized(account)
    if subtype == SUBTYPE_LONG_TERM_LIABILITY:
        return "long_term_liabilities"
    return "current_liabilities"


def is_balance_sheet_current(account) -> bool:
    """True unless explicitly marked fixed/long-term — the working-capital
    split used by the indirect-method Cash Flow Statement (reports/CLAUDE.md)."""
    if account.account_type == AccountType.ASSET:
        return classify_asset(account) != "fixed_assets"
    if account.account_type == AccountType.LIABILITY:
        return classify_liability(account) != "long_term_liabilities"
    return True
