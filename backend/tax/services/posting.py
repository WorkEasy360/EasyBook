"""Turning a document's GST components into journal lines.

Shared by `sales` and `purchases` because the shape is identical and only the
side differs: output tax is CREDITED (a liability we owe), input tax is
DEBITED (an asset we reclaim). Duplicating this in both would mean two places
to get the fallback behaviour wrong.

This module builds lines. It never posts them - `accounting.services.posting`
remains the only authoritative posting path, and the caller assembles the full
balanced journal.

THE FALLBACK IS THE POINT
-------------------------
When an organization has no `TaxAccountMapping` for a component, the whole
tax total goes to the document's own `tax_payable_account` /
`tax_recoverable_account` as a single line - byte-for-byte the journal this
codebase produced before Phase 7. That is what lets every existing
organization, and every existing test, keep posting unchanged while GST is
adopted incrementally. Configuring the mapping is what opts an organization
into per-component posting; nothing forces it.
"""

from decimal import Decimal

from core.exceptions import ApplicationError
from tax.enums import TaxComponent, TaxDirection
from tax.selectors import get_tax_account_map

ZERO = Decimal("0")

# The order components appear in the journal. Fixed so journal lines are
# stable across posts and diffable in tests, rather than following dict
# iteration order over a mapping table.
_COMPONENTS = (
    (TaxComponent.CGST, "cgst"),
    (TaxComponent.SGST_UTGST, "sgst"),
    (TaxComponent.IGST, "igst"),
    (TaxComponent.CESS, "cess"),
)


def _component_amount(document, prefix):
    """Reads one component off a document, whichever name it uses.

    Line-based documents (Invoice, Bill, ...) aggregate their lines into
    `cgst_total` and friends. `purchases.Expense` has no line model - it IS its
    own line - so it carries `cgst_amount` instead. Checking both here keeps
    one posting path for every document rather than giving Expense a parallel
    one that could drift.
    """
    for suffix in ("_total", "_amount"):
        value = getattr(document, prefix + suffix, None)
        if value is not None:
            return value
    return ZERO


def build_tax_journal_lines(
    *,
    organization,
    document,
    direction: str,
    fallback_account_id,
    side: str,
    tax_total=None,
    account_required_message: str,
    account_required_code: str = "tax_account_required",
) -> list[dict]:
    """Returns the journal lines for one document's tax.

    `side` is "credit" or "debit". `tax_total` defaults to the document's
    `tax_total`, overridable for `Expense`, which calls its total `tax_amount`.

    Three outcomes:
      - no tax at all -> no lines;
      - components populated AND every non-zero one mapped -> one line each;
      - anything else -> a single line to `fallback_account_id`, which must
        then exist.
    """
    total = document.tax_total if tax_total is None else tax_total
    if total <= 0:
        return []

    component_amounts = [
        (component, _component_amount(document, prefix) or ZERO)
        for component, prefix in _COMPONENTS
    ]
    has_components = any(amount != ZERO for _, amount in component_amounts)

    if has_components:
        accounts = get_tax_account_map(organization=organization, direction=direction)
        missing = [
            component
            for component, amount in component_amounts
            if amount != ZERO and component not in accounts
        ]
        if not missing:
            return [
                {"account_id": accounts[component].id, side: amount}
                for component, amount in component_amounts
                if amount != ZERO
            ]
        # Partially mapped is not a usable state: posting some components to
        # their own accounts and the rest to a catch-all would produce a
        # tax ledger that reconciles to neither. Fall through to the single
        # fallback line, which at least remains internally consistent.

    if fallback_account_id is None:
        raise ApplicationError(account_required_message, code=account_required_code)
    return [{"account_id": fallback_account_id, side: total}]


def build_output_tax_lines(*, organization, document, fallback_account_id, tax_total=None):
    """Sales side: output tax is a liability, so it is CREDITED."""
    return build_tax_journal_lines(
        organization=organization,
        document=document,
        direction=TaxDirection.OUTPUT,
        fallback_account_id=fallback_account_id,
        side="credit",
        tax_total=tax_total,
        account_required_message=(
            "tax_payable_account is required to post a document with tax, unless every "
            "tax component is mapped to an account."
        ),
    )


def build_input_tax_lines(*, organization, document, fallback_account_id, tax_total=None):
    """Purchase side: input tax is an asset, so it is DEBITED.

    Whether that input tax is actually recoverable is the organization's call,
    expressed by which account they map it to - `purchases/CLAUDE.md` deferred
    that decision here, and the answer is that this module records where the
    configuration says it goes rather than adjudicating eligibility, which
    turns on s.17(5) blocked-credit rules no system can infer from a bill.
    """
    return build_tax_journal_lines(
        organization=organization,
        document=document,
        direction=TaxDirection.INPUT,
        fallback_account_id=fallback_account_id,
        side="debit",
        tax_total=tax_total,
        account_required_message=(
            "tax_recoverable_account is required to post a document with tax, unless every "
            "tax component is mapped to an account."
        ),
    )


def build_reverse_charge_lines(*, organization, document, tax_total=None):
    """The liability leg of a reverse-charge purchase.

    Under IGST Act s.5(3)/(4) the recipient pays the tax the supplier would
    normally have collected. That single transaction creates BOTH an input
    credit (handled by `build_input_tax_lines`) and an output liability - this
    leg. Omitting it would leave the bill's journal claiming a credit for tax
    nobody ever owed.

    Returns [] when the organization has no RCM_PAYABLE mapping, because there
    is no sensible document-level fallback for a liability the document does
    not have a field for; `services/bills.py` refuses the post in that case
    rather than silently posting half of it.
    """
    total = document.tax_total if tax_total is None else tax_total
    if total <= 0:
        return []
    accounts = get_tax_account_map(
        organization=organization, direction=TaxDirection.RCM_PAYABLE
    )
    account = accounts.get(TaxComponent.IGST) or accounts.get(TaxComponent.CGST)
    if account is None:
        return []
    return [{"account_id": account.id, "credit": total}]
