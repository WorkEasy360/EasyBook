from django.db import transaction
from django.utils import timezone

from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from inventory.models.stock_movement import MovementType
from inventory.selectors import get_weighted_average_cost
from inventory.services.movements import record_stock_movement
from items.models.item import ItemType
from sales.models.customer import Customer
from sales.models.delivery import DeliveryChallan, DeliveryChallanLine, DeliveryChallanStatus
from sales.models.sales_order import SalesOrder, SalesOrderLine, SalesOrderStatus
from sales.selectors import get_fulfilled_quantity
from sales.services.customers import assert_customer_usable_for_new_transaction
from sales.services.sales_orders import refresh_order_fulfillment_status

DELIVERY_CHALLAN_NUMBER_SEQUENCE_KEY = "delivery_challan"


def _validate_line_item(*, organization, item) -> None:
    if item.organization_id != organization.id:
        raise ApplicationError("Item must belong to the posting organization.", code="item_cross_org")
    if item.item_type != ItemType.PRODUCT or not item.track_inventory:
        # Service items have nothing to physically deliver — see
        # sales/CLAUDE.md and root CLAUDE.md (never invent stock movement for
        # non-tracked items).
        raise ApplicationError(
            "Only inventory-tracked product items can appear on a delivery challan.", code="item_not_deliverable"
        )
    if not item.is_active:
        raise ApplicationError("Inactive items cannot be used in new deliveries.", code="item_inactive")


def _validate_source_order_line(*, organization, item, source_order_line, quantity) -> None:
    if source_order_line is None:
        return
    if source_order_line.organization_id != organization.id:
        raise ApplicationError(
            "Sales order line must belong to the posting organization.", code="sales_order_line_cross_org"
        )
    if source_order_line.item_id != item.id:
        raise ApplicationError(
            "Delivery line item must match the linked sales order line's item.", code="sales_order_line_item_mismatch"
        )
    already_fulfilled = get_fulfilled_quantity(sales_order_line=source_order_line)
    if already_fulfilled + quantity > source_order_line.quantity:
        raise ApplicationError(
            f"Delivering {quantity} would exceed the ordered quantity "
            f"({source_order_line.quantity}, {already_fulfilled} already fulfilled).",
            code="over_fulfillment",
        )


def _get_challan_for_update(*, challan_id, organization) -> DeliveryChallan:
    try:
        return DeliveryChallan.objects.select_for_update().get(id=challan_id, organization=organization)
    except DeliveryChallan.DoesNotExist:
        raise ApplicationError("Delivery challan not found.", code="delivery_not_found", status_code=404)


@transaction.atomic
def create_delivery_challan(
    *,
    organization,
    customer: Customer,
    warehouse,
    challan_date,
    lines: list[dict],
    source_sales_order: SalesOrder | None = None,
    notes: str = "",
    actor=None,
) -> DeliveryChallan:
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    assert_customer_usable_for_new_transaction(customer=customer)
    if warehouse.organization_id != organization.id:
        raise ApplicationError("Warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if source_sales_order is not None:
        if source_sales_order.organization_id != organization.id:
            raise ApplicationError("Sales order must belong to the posting organization.", code="sales_order_cross_org")
        if source_sales_order.status not in (SalesOrderStatus.CONFIRMED, SalesOrderStatus.PARTIALLY_FULFILLED):
            raise ApplicationError(
                f"Cannot deliver against a sales order in status '{source_sales_order.status}'.",
                code="sales_order_not_confirmed",
            )
    if not lines:
        raise ApplicationError("A delivery challan needs at least one line.", code="delivery_no_lines")

    challan_number = allocate_sequence_number(
        organization_id=organization.id, key=DELIVERY_CHALLAN_NUMBER_SEQUENCE_KEY, prefix="DC-"
    )

    challan = DeliveryChallan.objects.create(
        organization=organization,
        customer=customer,
        challan_number=challan_number,
        source_sales_order=source_sales_order,
        warehouse=warehouse,
        challan_date=challan_date,
        notes=notes,
    )
    for index, line in enumerate(lines, start=1):
        item = line["item"]
        quantity = line["quantity"]
        source_order_line = line.get("source_order_line")
        _validate_line_item(organization=organization, item=item)
        if quantity <= 0:
            raise ApplicationError(f"Line {index}: quantity must be positive.", code="delivery_line_quantity_invalid")
        _validate_source_order_line(
            organization=organization, item=item, source_order_line=source_order_line, quantity=quantity
        )
        DeliveryChallanLine.objects.create(
            organization=organization,
            challan=challan,
            item=item,
            source_order_line=source_order_line,
            line_number=index,
            description=line.get("description", ""),
            quantity=quantity,
        )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.DeliveryChallan",
        object_id=challan.id,
        changes={"challan_number": challan_number},
    )
    return challan


@transaction.atomic
def replace_delivery_lines(*, challan: DeliveryChallan, lines: list[dict], actor=None) -> DeliveryChallan:
    """Replaces all lines on a DRAFT delivery challan. Raises otherwise —
    see DeliveryChallan._MUTABLE_AFTER_DRAFT_FIELDS."""
    if challan.status != DeliveryChallanStatus.DRAFT:
        raise ApplicationError("Only draft delivery challans can be modified.", code="delivery_not_draft")
    if not lines:
        raise ApplicationError("A delivery challan needs at least one line.", code="delivery_no_lines")

    for index, line in enumerate(lines, start=1):
        item = line["item"]
        quantity = line["quantity"]
        source_order_line = line.get("source_order_line")
        _validate_line_item(organization=challan.organization, item=item)
        if quantity <= 0:
            raise ApplicationError(f"Line {index}: quantity must be positive.", code="delivery_line_quantity_invalid")
        _validate_source_order_line(
            organization=challan.organization, item=item, source_order_line=source_order_line, quantity=quantity
        )

    challan.lines.all().delete()
    for index, line in enumerate(lines, start=1):
        DeliveryChallanLine.objects.create(
            organization=challan.organization,
            challan=challan,
            item=line["item"],
            source_order_line=line.get("source_order_line"),
            line_number=index,
            description=line.get("description", ""),
            quantity=line["quantity"],
        )

    record_audit(
        organization_id=challan.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.DeliveryChallan",
        object_id=challan.id,
        changes={"line_count": len(lines)},
    )
    return challan


@transaction.atomic
def dispatch_delivery(*, challan_id, organization, actor=None) -> DeliveryChallan:
    """The single authoritative path from DRAFT to physically-issued stock.
    Idempotent like accounting.services.posting.post_journal: dispatching an
    already-DISPATCHED challan is a locked no-op, never double-issuing stock
    (see sales/CLAUDE.md and root CLAUDE.md idempotency rule).
    """
    challan = _get_challan_for_update(challan_id=challan_id, organization=organization)

    if challan.status == DeliveryChallanStatus.DISPATCHED:
        return challan
    if challan.status != DeliveryChallanStatus.DRAFT:
        raise ApplicationError(
            f"Cannot dispatch a delivery challan in status '{challan.status}'.", code="delivery_invalid_status"
        )

    # NOT `.select_related("source_order_line")` here — that FK is nullable,
    # and PostgreSQL rejects SELECT FOR UPDATE across a LEFT JOIN ("FOR
    # UPDATE cannot be applied to the nullable side of an outer join").
    # `item` is a required FK so it joins as INNER and is safe to include.
    lines = list(
        DeliveryChallanLine.objects.select_for_update()
        .select_related("item")
        .filter(challan=challan)
        .order_by("line_number")
    )
    if not lines:
        raise ApplicationError("Cannot dispatch a delivery challan with no lines.", code="delivery_no_lines")

    # Re-check fulfilment NOW, not only when the draft was created. Drafts do
    # not count towards get_fulfilled_quantity, so two drafts can each pass
    # the create-time check for the full ordered quantity; without this, both
    # dispatch and the order ships twice. Locking the order lines first
    # serialises concurrent dispatches against the same order: the second
    # waits, then sees the first one's committed DISPATCHED lines.
    order_line_ids = sorted({line.source_order_line_id for line in lines if line.source_order_line_id})
    if order_line_ids:
        order_lines = {
            order_line.id: order_line
            for order_line in SalesOrderLine.objects.select_for_update().filter(pk__in=order_line_ids).order_by("pk")
        }
        dispatching = {}
        for line in lines:
            if line.source_order_line_id:
                dispatching[line.source_order_line_id] = dispatching.get(line.source_order_line_id, 0) + line.quantity
        for order_line_id, quantity in dispatching.items():
            order_line = order_lines[order_line_id]
            already_fulfilled = get_fulfilled_quantity(sales_order_line=order_line)
            if already_fulfilled + quantity > order_line.quantity:
                raise ApplicationError(
                    f"Dispatching {quantity} would exceed the ordered quantity "
                    f"({order_line.quantity}, {already_fulfilled} already dispatched).",
                    code="over_fulfillment",
                )

    for line in lines:
        # OUT movements are valued at the current weighted-average cost,
        # computed at dispatch time — same pattern as
        # inventory.services.adjustments.post_stock_adjustment for an OUT line.
        _, unit_cost = get_weighted_average_cost(item=line.item, warehouse=challan.warehouse)
        record_stock_movement(
            organization=organization,
            item=line.item,
            warehouse=challan.warehouse,
            movement_type=MovementType.ISSUE,
            quantity=line.quantity,
            unit_cost=unit_cost,
            movement_date=challan.challan_date,
            source_type="sales.DeliveryChallan",
            source_id=str(challan.id),
            notes=line.description,
            created_by=actor,
        )

    challan.status = DeliveryChallanStatus.DISPATCHED
    challan.dispatched_at = timezone.now()
    challan.save(update_fields=["status", "dispatched_at", "updated_at"])

    if challan.source_sales_order_id:
        order = SalesOrder.objects.select_for_update().get(pk=challan.source_sales_order_id)
        refresh_order_fulfillment_status(order=order, actor=actor)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="sales.DeliveryChallan",
        object_id=challan.id,
        changes={"line_count": len(lines)},
    )
    return challan


@transaction.atomic
def mark_delivered(*, challan_id, organization, actor=None) -> DeliveryChallan:
    challan = _get_challan_for_update(challan_id=challan_id, organization=organization)
    if challan.status == DeliveryChallanStatus.DELIVERED:
        return challan
    if challan.status != DeliveryChallanStatus.DISPATCHED:
        raise ApplicationError(
            f"Cannot mark delivered a challan in status '{challan.status}'.", code="delivery_invalid_status"
        )
    challan.status = DeliveryChallanStatus.DELIVERED
    challan.delivered_at = timezone.now()
    challan.save(update_fields=["status", "delivered_at", "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.DeliveryChallan",
        object_id=challan.id,
        changes={"status": "delivered"},
    )
    return challan


@transaction.atomic
def cancel_delivery(*, challan_id, organization, actor=None) -> DeliveryChallan:
    challan = _get_challan_for_update(challan_id=challan_id, organization=organization)
    if challan.status != DeliveryChallanStatus.DRAFT:
        raise ApplicationError(
            "Only a draft delivery challan (before dispatch) can be cancelled.", code="delivery_invalid_status"
        )
    challan.status = DeliveryChallanStatus.CANCELLED
    challan.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.DeliveryChallan",
        object_id=challan.id,
        changes={"status": "cancelled"},
    )
    return challan
