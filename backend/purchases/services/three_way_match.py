"""Three-way match: Purchase Order vs Goods Receipt vs Bill.

The control that catches a vendor billing for goods we never ordered, never
received, or agreed a different price for. It is deliberately a READ-ONLY
ANALYSIS: it reports discrepancies and never blocks, posts, or adjusts
anything. Approval workflow (who may post a bill despite an exception) is
Phase 11's automation concern, and a matcher that silently refused to post
would hide the far more common legitimate case — a genuine price increase
the buyer has accepted.

Everything here derives from the documents themselves via `selectors.py`;
no match result is stored. Re-running it after a correction always reflects
the corrected state, and there is no cached verdict to go stale — the same
never-store-a-derived-answer rule the General Ledger follows.

Tolerances are caller-supplied and default to zero (exact match required).
There is no organization-level tolerance setting: that is a policy decision
this phase has no configuration surface for, and inventing one would mean
guessing a number that belongs to the business.
"""

from decimal import Decimal

from core.exceptions import ApplicationError
from purchases.models.bill import BillLine, BillStatus
from purchases.models.purchase_order import PurchaseOrder
from purchases.selectors import get_billed_quantity, get_received_quantity


class MatchException:
    """Discrepancy codes. Strings, not a TextChoices enum — nothing persists
    them, so they need no database representation."""

    QUANTITY_OVER_RECEIVED = "quantity_over_received"
    QUANTITY_OVER_BILLED = "quantity_over_billed"
    BILLED_NOT_RECEIVED = "billed_not_received"
    RECEIVED_NOT_BILLED = "received_not_billed"
    PRICE_MISMATCH = "price_mismatch"
    NOT_ON_ORDER = "not_on_order"


def _billed_unit_prices(*, purchase_order_line) -> list[Decimal]:
    """Distinct unit prices this PO line has actually been billed at, across
    non-draft, non-void bills."""
    return list(
        BillLine.objects.filter(source_order_line=purchase_order_line)
        .exclude(bill__status__in=[BillStatus.DRAFT, BillStatus.VOID])
        .values_list("unit_price", flat=True)
        .distinct()
    )


def match_purchase_order(
    *,
    organization,
    purchase_order: PurchaseOrder,
    quantity_tolerance: Decimal = Decimal("0"),
    price_tolerance: Decimal = Decimal("0"),
) -> dict:
    """Compares a purchase order against everything received and billed
    against it.

    Returns a plain dict (not a model): per-line ordered/received/billed
    quantities, the prices billed, and a list of exception codes, plus a
    document-level roll-up. `matched` is True only when no line raised an
    exception.
    """
    if purchase_order.organization_id != organization.id:
        raise ApplicationError(
            "Purchase order must belong to the requesting organization.", code="purchase_order_cross_org"
        )
    if quantity_tolerance < 0 or price_tolerance < 0:
        raise ApplicationError("Tolerances cannot be negative.", code="match_tolerance_invalid")

    line_results = []
    for line in purchase_order.lines.select_related("item").order_by("line_number"):
        ordered = line.quantity
        received = get_received_quantity(purchase_order_line=line)
        billed = get_billed_quantity(purchase_order_line=line)
        exceptions = []

        if received - ordered > quantity_tolerance:
            exceptions.append(MatchException.QUANTITY_OVER_RECEIVED)
        if billed - ordered > quantity_tolerance:
            exceptions.append(MatchException.QUANTITY_OVER_BILLED)
        # The single most valuable check in the whole report: the vendor has
        # invoiced more than physically turned up.
        if billed - received > quantity_tolerance:
            exceptions.append(MatchException.BILLED_NOT_RECEIVED)
        if received - billed > quantity_tolerance:
            exceptions.append(MatchException.RECEIVED_NOT_BILLED)

        billed_prices = _billed_unit_prices(purchase_order_line=line)
        price_variances = [price - line.unit_price for price in billed_prices]
        if any(abs(variance) > price_tolerance for variance in price_variances):
            exceptions.append(MatchException.PRICE_MISMATCH)

        line_results.append({
            "line_id": line.id,
            "line_number": line.line_number,
            "item_id": line.item_id,
            "description": line.description,
            "ordered_quantity": ordered,
            "received_quantity": received,
            "billed_quantity": billed,
            "ordered_unit_price": line.unit_price,
            "billed_unit_prices": billed_prices,
            "max_price_variance": max(price_variances, key=abs) if price_variances else Decimal("0"),
            "exceptions": exceptions,
        })

    unordered = _unordered_bill_lines(purchase_order=purchase_order)

    all_exceptions = sorted({code for result in line_results for code in result["exceptions"]})
    if unordered:
        all_exceptions = sorted({*all_exceptions, MatchException.NOT_ON_ORDER})

    return {
        "purchase_order_id": purchase_order.id,
        "order_number": purchase_order.order_number,
        "status": purchase_order.status,
        "matched": not all_exceptions,
        "exceptions": all_exceptions,
        "lines": line_results,
        "unordered_bill_lines": unordered,
    }


def _unordered_bill_lines(*, purchase_order: PurchaseOrder) -> list[dict]:
    """Lines on bills raised against this PO that do not point at any PO
    line — goods or services the vendor billed which were never ordered.

    Caught separately from the per-line walk precisely because there is no
    PO line to hang them off; a purely line-driven match would miss them
    entirely, which is the gap this check exists to close.
    """
    orphan_lines = (
        BillLine.objects.filter(bill__source_purchase_order=purchase_order, source_order_line__isnull=True)
        .exclude(bill__status__in=[BillStatus.DRAFT, BillStatus.VOID])
        .select_related("bill", "item")
        .order_by("bill__bill_date", "line_number")
    )
    return [
        {
            "bill_id": line.bill_id,
            "bill_number": line.bill.bill_number,
            "line_number": line.line_number,
            "item_id": line.item_id,
            "description": line.description,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "line_total": line.line_total,
        }
        for line in orphan_lines
    ]


def match_bill(
    *,
    organization,
    bill,
    quantity_tolerance: Decimal = Decimal("0"),
    price_tolerance: Decimal = Decimal("0"),
) -> dict:
    """The same analysis oriented around ONE bill — the view a person
    actually wants when a bill lands on their desk and they must decide
    whether to approve it.

    A bill with no `source_purchase_order` matches vacuously: with nothing
    agreed in advance there is nothing to compare against, and reporting
    that as a failure would flag every legitimate ad-hoc purchase.
    """
    if bill.organization_id != organization.id:
        raise ApplicationError("Bill must belong to the requesting organization.", code="bill_cross_org")
    if quantity_tolerance < 0 or price_tolerance < 0:
        raise ApplicationError("Tolerances cannot be negative.", code="match_tolerance_invalid")

    line_results = []
    for line in bill.lines.select_related("item").order_by("line_number"):
        order_line = line.source_order_line
        exceptions = []
        ordered_quantity = None
        ordered_unit_price = None
        received_quantity = None
        price_variance = Decimal("0")

        if order_line is None:
            if bill.source_purchase_order_id is not None:
                exceptions.append(MatchException.NOT_ON_ORDER)
        else:
            ordered_quantity = order_line.quantity
            ordered_unit_price = order_line.unit_price
            received_quantity = get_received_quantity(purchase_order_line=order_line)
            billed_quantity = get_billed_quantity(purchase_order_line=order_line)

            if billed_quantity - ordered_quantity > quantity_tolerance:
                exceptions.append(MatchException.QUANTITY_OVER_BILLED)
            if billed_quantity - received_quantity > quantity_tolerance:
                exceptions.append(MatchException.BILLED_NOT_RECEIVED)

            price_variance = line.unit_price - ordered_unit_price
            if abs(price_variance) > price_tolerance:
                exceptions.append(MatchException.PRICE_MISMATCH)

        line_results.append({
            "line_id": line.id,
            "line_number": line.line_number,
            "item_id": line.item_id,
            "description": line.description,
            "billed_quantity": line.quantity,
            "billed_unit_price": line.unit_price,
            "ordered_quantity": ordered_quantity,
            "ordered_unit_price": ordered_unit_price,
            "received_quantity": received_quantity,
            "price_variance": price_variance,
            "goods_receipt_line_id": line.source_goods_receipt_line_id,
            "exceptions": exceptions,
        })

    all_exceptions = sorted({code for result in line_results for code in result["exceptions"]})
    return {
        "bill_id": bill.id,
        "bill_number": bill.bill_number,
        "status": bill.status,
        "purchase_order_id": bill.source_purchase_order_id,
        "matched": not all_exceptions,
        "exceptions": all_exceptions,
        "lines": line_results,
    }
