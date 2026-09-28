from decimal import Decimal

from django.db import transaction

from accounting.services.currency import assert_base_currency
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from core.money import calculate_document_totals
from purchases.models.purchase_order import PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus
from purchases.models.vendor import Vendor
from purchases.services.line_items import build_line_snapshot
from purchases.services.vendors import assert_vendor_usable_for_new_transaction
from tax.services.documents import resolve_document_tax

PURCHASE_ORDER_NUMBER_SEQUENCE_KEY = "purchase_order"

# Purchase orders may transition only along these edges. PARTIALLY_RECEIVED
# and RECEIVED are reached only via the goods-receipt service, never through
# a generic status-transition endpoint — they are not listed here because
# nothing in this module drives them. Mirrors sales/services/sales_orders.py.
_ALLOWED_TRANSITIONS = {
    PurchaseOrderStatus.DRAFT: {PurchaseOrderStatus.APPROVED, PurchaseOrderStatus.CANCELLED},
    PurchaseOrderStatus.APPROVED: {PurchaseOrderStatus.CANCELLED, PurchaseOrderStatus.CLOSED},
    # CLOSED is a short-close: the buyer accepts that the outstanding balance
    # will never arrive. It has no sales-side mirror (we control our own
    # fulfilment) and is reachable from partial receipt too.
    PurchaseOrderStatus.PARTIALLY_RECEIVED: {PurchaseOrderStatus.CLOSED, PurchaseOrderStatus.CANCELLED},
    PurchaseOrderStatus.RECEIVED: {PurchaseOrderStatus.CLOSED},
}


def _get_order_for_update(*, order_id, organization) -> PurchaseOrder:
    try:
        return PurchaseOrder.objects.select_for_update().get(id=order_id, organization=organization)
    except PurchaseOrder.DoesNotExist:
        raise ApplicationError("Purchase order not found.", code="purchase_order_not_found", status_code=404)


@transaction.atomic
def create_purchase_order(
    *,
    organization,
    vendor: Vendor,
    order_date,
    lines: list[dict],
    expected_date=None,
    warehouse=None,
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    reference: str = "",
    notes: str = "",
    terms: str = "",
    place_of_supply=None,
    actor=None,
) -> PurchaseOrder:
    if vendor.organization_id != organization.id:
        raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
    assert_vendor_usable_for_new_transaction(vendor=vendor)
    if warehouse is not None and warehouse.organization_id != organization.id:
        raise ApplicationError("warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if not lines:
        raise ApplicationError("A purchase order needs at least one line.", code="purchase_order_no_lines")
    if expected_date is not None and expected_date < order_date:
        raise ApplicationError(
            "expected_date cannot be earlier than order_date.", code="purchase_order_expected_date_invalid"
        )

    currency = currency or vendor.currency
    assert_base_currency(organization=organization, currency=currency, exchange_rate=exchange_rate)

    order_number = allocate_sequence_number(
        organization_id=organization.id, key=PURCHASE_ORDER_NUMBER_SEQUENCE_KEY, prefix="PO-"
    )

    line_rows = [
        build_line_snapshot(organization=organization, line=line, line_number=index)
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)

    # Determined even though a purchase order carries no component columns
    # and posts nothing: the treatment is what converts forward to the bill.
    tax_treatment = resolve_document_tax(
        organization=organization, party=vendor, place_of_supply=place_of_supply
    )

    order = PurchaseOrder.objects.create(
        organization=organization,
        vendor=vendor,
        **tax_treatment,
        order_number=order_number,
        order_date=order_date,
        expected_date=expected_date,
        reference=reference,
        warehouse=warehouse,
        currency=currency,
        exchange_rate=exchange_rate,
        subtotal=totals["subtotal"],
        discount_total=totals["discount"],
        tax_total=totals["tax"],
        total=totals["total"],
        notes=notes,
        terms=terms,
    )
    for row in line_rows:
        PurchaseOrderLine.objects.create(organization=organization, order=order, **row)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.PurchaseOrder",
        object_id=order.id,
        changes={"order_number": order_number, "total": str(totals["total"])},
    )
    return order


@transaction.atomic
def replace_order_lines(*, order: PurchaseOrder, lines: list[dict], actor=None) -> PurchaseOrder:
    """Replaces all lines on a DRAFT purchase order and recomputes header
    totals. Raises otherwise — see PurchaseOrder._MUTABLE_AFTER_DRAFT_FIELDS."""
    if order.status != PurchaseOrderStatus.DRAFT:
        raise ApplicationError("Only draft purchase orders can be modified.", code="purchase_order_not_draft")
    if not lines:
        raise ApplicationError("A purchase order needs at least one line.", code="purchase_order_no_lines")

    line_rows = [
        build_line_snapshot(organization=order.organization, line=line, line_number=index)
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)

    order.lines.all().delete()
    for row in line_rows:
        PurchaseOrderLine.objects.create(organization=order.organization, order=order, **row)

    order.subtotal = totals["subtotal"]
    order.discount_total = totals["discount"]
    order.tax_total = totals["tax"]
    order.total = totals["total"]
    order.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "updated_at"])

    record_audit(
        organization_id=order.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.PurchaseOrder",
        object_id=order.id,
        changes={"total": str(totals["total"])},
    )
    return order


def _apply_transition(*, order: PurchaseOrder, to_status: str, organization, actor=None) -> PurchaseOrder:
    allowed = _ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise ApplicationError(
            f"Cannot move purchase order from '{order.status}' to '{to_status}'.",
            code="purchase_order_invalid_status",
        )
    from_status = order.status
    order.status = to_status
    order.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.PurchaseOrder",
        object_id=order.id,
        changes={"status": {"from": from_status, "to": to_status}},
    )
    return order


@transaction.atomic
def approve_purchase_order(*, order_id, organization, actor=None) -> PurchaseOrder:
    order = _get_order_for_update(order_id=order_id, organization=organization)
    if not order.lines.exists():
        raise ApplicationError("Cannot approve a purchase order with no lines.", code="purchase_order_no_lines")
    return _apply_transition(
        order=order, to_status=PurchaseOrderStatus.APPROVED, organization=organization, actor=actor
    )


@transaction.atomic
def cancel_purchase_order(*, order_id, organization, actor=None) -> PurchaseOrder:
    """Cancels a PO that has not yet been received against. Once goods have
    physically arrived, cancellation is no longer honest bookkeeping — the
    remaining balance is short-closed via `close_purchase_order` instead."""
    order = _get_order_for_update(order_id=order_id, organization=organization)
    if order.status in (PurchaseOrderStatus.PARTIALLY_RECEIVED, PurchaseOrderStatus.RECEIVED):
        raise ApplicationError(
            "A purchase order with received goods cannot be cancelled — close it instead.",
            code="purchase_order_has_receipts",
        )
    return _apply_transition(
        order=order, to_status=PurchaseOrderStatus.CANCELLED, organization=organization, actor=actor
    )


@transaction.atomic
def close_purchase_order(*, order_id, organization, actor=None) -> PurchaseOrder:
    """Short-closes a PO: the buyer accepts that any undelivered balance will
    never arrive. Purchase-side only — no sales mirror, because we control
    our own fulfilment but not a vendor's."""
    order = _get_order_for_update(order_id=order_id, organization=organization)
    return _apply_transition(
        order=order, to_status=PurchaseOrderStatus.CLOSED, organization=organization, actor=actor
    )


def refresh_order_receipt_status(*, order: PurchaseOrder, actor=None) -> PurchaseOrder:
    """Called by the goods-receipt service (never by a user-facing endpoint)
    after new stock arrives. Derives APPROVED -> PARTIALLY_RECEIVED ->
    RECEIVED from GoodsReceiptLine quantities — see
    selectors.py::get_received_quantity. A no-op for any other status, so a
    CANCELLED or CLOSED order is never silently reopened."""
    from purchases.selectors import get_received_quantity

    if order.status not in (PurchaseOrderStatus.APPROVED, PurchaseOrderStatus.PARTIALLY_RECEIVED):
        return order

    lines = list(order.lines.all())
    received_by_line = {line.id: get_received_quantity(purchase_order_line=line) for line in lines}
    fully_received = all(received_by_line[line.id] >= line.quantity for line in lines)
    any_received = any(received_by_line[line.id] > 0 for line in lines)

    if fully_received:
        new_status = PurchaseOrderStatus.RECEIVED
    elif any_received:
        new_status = PurchaseOrderStatus.PARTIALLY_RECEIVED
    else:
        new_status = order.status

    if new_status == order.status:
        return order

    from_status = order.status
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=order.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.PurchaseOrder",
        object_id=order.id,
        changes={"status": {"from": from_status, "to": new_status}, "reason": "goods_receipt"},
    )
    return order
