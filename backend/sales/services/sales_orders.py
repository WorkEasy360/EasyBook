from decimal import Decimal

from django.db import transaction

from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from core.money import calculate_document_totals
from sales.models.customer import Customer
from sales.models.quote import Quote, QuoteStatus
from sales.models.sales_order import SalesOrder, SalesOrderLine, SalesOrderStatus
from sales.services.customers import assert_customer_usable_for_new_transaction
from sales.services.line_items import build_line_snapshot
from tax.services.documents import carry_forward_tax, resolve_document_tax

SALES_ORDER_NUMBER_SEQUENCE_KEY = "sales_order"

# Sales orders may transition only along these edges. PARTIALLY_FULFILLED and
# FULFILLED are reached only via the delivery-challan fulfillment service
# (Slice 4), never through a generic status-transition endpoint — they are
# not listed here because nothing in this module drives them.
_ALLOWED_TRANSITIONS = {
    SalesOrderStatus.DRAFT: {SalesOrderStatus.CONFIRMED, SalesOrderStatus.CANCELLED},
    SalesOrderStatus.CONFIRMED: {SalesOrderStatus.CANCELLED},
}


def _get_order_for_update(*, order_id, organization) -> SalesOrder:
    try:
        return SalesOrder.objects.select_for_update().get(id=order_id, organization=organization)
    except SalesOrder.DoesNotExist:
        raise ApplicationError("Sales order not found.", code="sales_order_not_found", status_code=404)


@transaction.atomic
def create_sales_order(
    *,
    organization,
    customer: Customer,
    order_date,
    lines: list[dict],
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    notes: str = "",
    terms: str = "",
    source_quote: Quote | None = None,
    place_of_supply=None,
    tax_treatment: dict | None = None,
    actor=None,
) -> SalesOrder:
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    assert_customer_usable_for_new_transaction(customer=customer)
    if not lines:
        raise ApplicationError("A sales order needs at least one line.", code="sales_order_no_lines")

    currency = currency or customer.currency
    tax_treatment = tax_treatment or resolve_document_tax(
        organization=organization, party=customer, place_of_supply=place_of_supply
    )

    order_number = allocate_sequence_number(
        organization_id=organization.id, key=SALES_ORDER_NUMBER_SEQUENCE_KEY, prefix="SO-"
    )

    line_rows = [
        build_line_snapshot(organization=organization, line=line, line_number=index)
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)

    order = SalesOrder.objects.create(
        organization=organization,
        customer=customer,
        order_number=order_number,
        source_quote=source_quote,
        order_date=order_date,
        currency=currency,
        exchange_rate=exchange_rate,
        subtotal=totals["subtotal"],
        discount_total=totals["discount"],
        tax_total=totals["tax"],
        total=totals["total"],
        **tax_treatment,
        notes=notes,
        terms=terms,
    )
    for row in line_rows:
        SalesOrderLine.objects.create(organization=organization, order=order, **row)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.SalesOrder",
        object_id=order.id,
        changes={"order_number": order_number, "total": str(totals["total"])},
    )
    return order


@transaction.atomic
def convert_quote_to_sales_order(*, quote: Quote, order_date, actor=None) -> SalesOrder:
    """Builds a Sales Order from an ACCEPTED quote's lines. A quote converts
    at most once — see SalesOrder.source_quote (OneToOneField)."""
    if quote.status != QuoteStatus.ACCEPTED:
        raise ApplicationError(
            f"Cannot convert a quote in status '{quote.status}' to a sales order.", code="quote_not_acceptable"
        )
    if hasattr(quote, "sales_order"):
        raise ApplicationError("This quote has already been converted to a sales order.", code="quote_already_converted")

    lines = [
        {
            "item": line.item,
            "description": line.description,
            "hsn_sac_snapshot": line.hsn_sac_snapshot,
            "tax_label": line.tax_label,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "discount_percent": line.discount_percent,
            "tax_rate": line.tax_rate,
        }
        for line in quote.lines.all()
    ]

    # The quote's treatment is COPIED, not re-determined: the customer's
    # master data may have changed between quoting and acceptance, and the tax
    # the customer was quoted is the tax they should be ordered under.
    carried = carry_forward_tax(quote)
    carried.pop("is_reverse_charge", None)

    return create_sales_order(
        organization=quote.organization,
        customer=quote.customer,
        order_date=order_date,
        lines=lines,
        tax_treatment=carried,
        currency=quote.currency,
        exchange_rate=quote.exchange_rate,
        notes=quote.notes,
        terms=quote.terms,
        source_quote=quote,
        actor=actor,
    )


@transaction.atomic
def replace_order_lines(*, order: SalesOrder, lines: list[dict], actor=None) -> SalesOrder:
    """Replaces all lines on a DRAFT sales order and recomputes header totals.
    Raises if the order is not a draft — see SalesOrder._MUTABLE_AFTER_DRAFT_FIELDS."""
    if order.status != SalesOrderStatus.DRAFT:
        raise ApplicationError("Only draft sales orders can be modified.", code="sales_order_not_draft")
    if not lines:
        raise ApplicationError("A sales order needs at least one line.", code="sales_order_no_lines")

    line_rows = [
        build_line_snapshot(organization=order.organization, line=line, line_number=index)
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)

    order.lines.all().delete()
    for row in line_rows:
        SalesOrderLine.objects.create(organization=order.organization, order=order, **row)

    order.subtotal = totals["subtotal"]
    order.discount_total = totals["discount"]
    order.tax_total = totals["tax"]
    order.total = totals["total"]
    order.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "updated_at"])

    record_audit(
        organization_id=order.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.SalesOrder",
        object_id=order.id,
        changes={"total": str(totals["total"])},
    )
    return order


def _apply_transition(*, order: SalesOrder, to_status: str, organization, actor=None) -> SalesOrder:
    allowed = _ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise ApplicationError(
            f"Cannot move sales order from '{order.status}' to '{to_status}'.", code="sales_order_invalid_status"
        )
    from_status = order.status
    order.status = to_status
    order.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.SalesOrder",
        object_id=order.id,
        changes={"status": {"from": from_status, "to": to_status}},
    )
    return order


@transaction.atomic
def confirm_order(*, order_id, organization, actor=None) -> SalesOrder:
    order = _get_order_for_update(order_id=order_id, organization=organization)
    if not order.lines.exists():
        raise ApplicationError("Cannot confirm a sales order with no lines.", code="sales_order_no_lines")
    return _apply_transition(order=order, to_status=SalesOrderStatus.CONFIRMED, organization=organization, actor=actor)


@transaction.atomic
def cancel_order(*, order_id, organization, actor=None) -> SalesOrder:
    order = _get_order_for_update(order_id=order_id, organization=organization)
    return _apply_transition(order=order, to_status=SalesOrderStatus.CANCELLED, organization=organization, actor=actor)


def refresh_order_fulfillment_status(*, order: SalesOrder, actor=None) -> SalesOrder:
    """Called by the delivery-challan dispatch service (never by a user-facing
    endpoint) after new fulfillment happens. Derives CONFIRMED ->
    PARTIALLY_FULFILLED -> FULFILLED from DeliveryChallanLine quantities —
    see sales/selectors.py::get_fulfilled_quantity. A no-op for any other
    order status (DRAFT/CANCELLED orders are never touched here)."""
    from sales.selectors import get_fulfilled_quantity

    if order.status not in (SalesOrderStatus.CONFIRMED, SalesOrderStatus.PARTIALLY_FULFILLED):
        return order

    lines = list(order.lines.all())
    fulfilled_by_line = {line.id: get_fulfilled_quantity(sales_order_line=line) for line in lines}
    fully_fulfilled = all(fulfilled_by_line[line.id] >= line.quantity for line in lines)
    any_fulfilled = any(fulfilled_by_line[line.id] > 0 for line in lines)

    if fully_fulfilled:
        new_status = SalesOrderStatus.FULFILLED
    elif any_fulfilled:
        new_status = SalesOrderStatus.PARTIALLY_FULFILLED
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
        object_type="sales.SalesOrder",
        object_id=order.id,
        changes={"status": {"from": from_status, "to": new_status}, "reason": "delivery_fulfillment"},
    )
    return order
