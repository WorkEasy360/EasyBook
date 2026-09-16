from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from inventory.models.stock_movement import MovementType
from inventory.services.movements import record_stock_movement
from purchases.models.goods_receipt import GoodsReceipt, GoodsReceiptLine, GoodsReceiptStatus
from purchases.models.purchase_order import PurchaseOrder, PurchaseOrderStatus
from purchases.models.vendor import Vendor
from purchases.selectors import get_received_quantity
from purchases.services.line_items import is_inventoried
from purchases.services.purchase_orders import refresh_order_receipt_status
from purchases.services.vendors import assert_vendor_usable_for_new_transaction

GOODS_RECEIPT_NUMBER_SEQUENCE_KEY = "goods_receipt"


def _validate_line_item(*, organization, item) -> None:
    if item.organization_id != organization.id:
        raise ApplicationError("Item must belong to the posting organization.", code="item_cross_org")
    if not is_inventoried(item):
        # A service or non-tracked item has nothing to physically receive —
        # it goes straight onto a Bill as an expense. Rejected rather than
        # silently ignored, mirroring sales/services/deliveries.py.
        raise ApplicationError(
            "Only inventory-tracked product items can appear on a goods receipt.",
            code="item_not_receivable",
        )
    if not item.is_active:
        raise ApplicationError("Inactive items cannot be used in new goods receipts.", code="item_inactive")
    if not item.is_purchasable:
        raise ApplicationError("This item is not purchasable.", code="item_not_purchasable")


def _validate_source_order_line(*, organization, item, source_order_line, quantity, receipt=None) -> None:
    if source_order_line is None:
        return
    if source_order_line.organization_id != organization.id:
        raise ApplicationError(
            "Purchase order line must belong to the posting organization.", code="purchase_order_line_cross_org"
        )
    if source_order_line.item_id != item.id:
        raise ApplicationError(
            "Receipt line item must match the linked purchase order line's item.",
            code="purchase_order_line_item_mismatch",
        )
    already_received = get_received_quantity(purchase_order_line=source_order_line)
    if already_received + quantity > source_order_line.quantity:
        raise ApplicationError(
            f"Receiving {quantity} would exceed the ordered quantity "
            f"({source_order_line.quantity}, {already_received} already received).",
            code="over_receipt",
        )


def _resolve_unit_cost(*, line: dict, source_order_line) -> Decimal:
    """A receipt must state the cost basis of what arrived — unlike its
    outbound mirror, which values an issue from stock already on hand.

    Explicit `unit_cost` wins; otherwise the PO's agreed unit price is used,
    which is the whole point of having ordered at an agreed price. With
    neither, we refuse rather than defaulting to zero: a silent zero-cost
    receipt would corrupt the weighted-average valuation of everything that
    item holds, and it would do so invisibly.
    """
    unit_cost = line.get("unit_cost")
    if unit_cost is not None:
        if unit_cost < 0:
            raise ApplicationError("Receipt unit_cost cannot be negative.", code="receipt_unit_cost_invalid")
        return unit_cost
    if source_order_line is not None:
        return source_order_line.unit_price
    raise ApplicationError(
        "unit_cost is required for a receipt line with no linked purchase order line.",
        code="receipt_unit_cost_required",
    )


def _get_receipt_for_update(*, receipt_id, organization) -> GoodsReceipt:
    try:
        return GoodsReceipt.objects.select_for_update().get(id=receipt_id, organization=organization)
    except GoodsReceipt.DoesNotExist:
        raise ApplicationError("Goods receipt not found.", code="goods_receipt_not_found", status_code=404)


def _build_receipt_lines(*, organization, receipt, lines: list[dict]) -> None:
    for index, line in enumerate(lines, start=1):
        item = line["item"]
        quantity = line["quantity"]
        source_order_line = line.get("source_order_line")
        _validate_line_item(organization=organization, item=item)
        if quantity <= 0:
            raise ApplicationError(
                f"Line {index}: quantity must be positive.", code="goods_receipt_line_quantity_invalid"
            )
        _validate_source_order_line(
            organization=organization, item=item, source_order_line=source_order_line, quantity=quantity
        )
        GoodsReceiptLine.objects.create(
            organization=organization,
            receipt=receipt,
            item=item,
            source_order_line=source_order_line,
            line_number=index,
            description=line.get("description", ""),
            quantity=quantity,
            unit_cost=_resolve_unit_cost(line=line, source_order_line=source_order_line),
        )


@transaction.atomic
def create_goods_receipt(
    *,
    organization,
    vendor: Vendor,
    warehouse,
    receipt_date,
    lines: list[dict],
    source_purchase_order: PurchaseOrder | None = None,
    vendor_document_number: str = "",
    notes: str = "",
    actor=None,
) -> GoodsReceipt:
    if vendor.organization_id != organization.id:
        raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
    assert_vendor_usable_for_new_transaction(vendor=vendor)
    if warehouse.organization_id != organization.id:
        raise ApplicationError("Warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if source_purchase_order is not None:
        if source_purchase_order.organization_id != organization.id:
            raise ApplicationError(
                "Purchase order must belong to the posting organization.", code="purchase_order_cross_org"
            )
        if source_purchase_order.vendor_id != vendor.id:
            raise ApplicationError(
                "Purchase order does not belong to this vendor.", code="purchase_order_vendor_mismatch"
            )
        if source_purchase_order.status not in (
            PurchaseOrderStatus.APPROVED,
            PurchaseOrderStatus.PARTIALLY_RECEIVED,
        ):
            raise ApplicationError(
                f"Cannot receive against a purchase order in status '{source_purchase_order.status}'.",
                code="purchase_order_not_approved",
            )
    if not lines:
        raise ApplicationError("A goods receipt needs at least one line.", code="goods_receipt_no_lines")

    receipt_number = allocate_sequence_number(
        organization_id=organization.id, key=GOODS_RECEIPT_NUMBER_SEQUENCE_KEY, prefix="GR-"
    )

    receipt = GoodsReceipt.objects.create(
        organization=organization,
        vendor=vendor,
        receipt_number=receipt_number,
        source_purchase_order=source_purchase_order,
        warehouse=warehouse,
        receipt_date=receipt_date,
        vendor_document_number=vendor_document_number,
        notes=notes,
    )
    _build_receipt_lines(organization=organization, receipt=receipt, lines=lines)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.GoodsReceipt",
        object_id=receipt.id,
        changes={"receipt_number": receipt_number},
    )
    return receipt


@transaction.atomic
def replace_receipt_lines(*, receipt: GoodsReceipt, lines: list[dict], actor=None) -> GoodsReceipt:
    """Replaces all lines on a DRAFT goods receipt. Raises otherwise — see
    GoodsReceipt._MUTABLE_AFTER_DRAFT_FIELDS."""
    if receipt.status != GoodsReceiptStatus.DRAFT:
        raise ApplicationError("Only draft goods receipts can be modified.", code="goods_receipt_not_draft")
    if not lines:
        raise ApplicationError("A goods receipt needs at least one line.", code="goods_receipt_no_lines")

    receipt.lines.all().delete()
    _build_receipt_lines(organization=receipt.organization, receipt=receipt, lines=lines)

    record_audit(
        organization_id=receipt.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.GoodsReceipt",
        object_id=receipt.id,
        changes={"line_count": len(lines)},
    )
    return receipt


@transaction.atomic
def receive_goods(*, receipt_id, organization, actor=None) -> GoodsReceipt:
    """The single authoritative path from DRAFT to physically-received stock.

    Idempotent like accounting.services.posting.post_journal: receiving an
    already-RECEIVED receipt is a locked no-op, never double-receiving stock
    (root CLAUDE.md idempotency rule, and the first half of the phase's
    no-double-receipt guarantee — the second half lives in services/bills.py,
    which must not receive the same goods again at bill time).

    Posts NO accounting journal — see models/goods_receipt.py for why the
    Inventory/AP journal belongs to the Bill instead.
    """
    receipt = _get_receipt_for_update(receipt_id=receipt_id, organization=organization)

    if receipt.status == GoodsReceiptStatus.RECEIVED:
        return receipt
    if receipt.status != GoodsReceiptStatus.DRAFT:
        raise ApplicationError(
            f"Cannot receive a goods receipt in status '{receipt.status}'.", code="goods_receipt_invalid_status"
        )

    # NOT `.select_related("source_order_line")` — that FK is nullable, and
    # PostgreSQL rejects SELECT FOR UPDATE across a LEFT JOIN. `item` is a
    # required FK so it joins as INNER and is safe (see sales/CLAUDE.md).
    lines = list(
        GoodsReceiptLine.objects.select_for_update()
        .select_related("item")
        .filter(receipt=receipt)
        .order_by("line_number")
    )
    if not lines:
        raise ApplicationError("Cannot receive a goods receipt with no lines.", code="goods_receipt_no_lines")

    for line in lines:
        record_stock_movement(
            organization=organization,
            item=line.item,
            warehouse=receipt.warehouse,
            movement_type=MovementType.RECEIPT,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            movement_date=receipt.receipt_date,
            source_type="purchases.GoodsReceipt",
            source_id=str(receipt.id),
            notes=line.description,
            created_by=actor,
        )

    receipt.status = GoodsReceiptStatus.RECEIVED
    receipt.received_at = timezone.now()
    receipt.save(update_fields=["status", "received_at", "updated_at"])

    if receipt.source_purchase_order_id:
        order = PurchaseOrder.objects.select_for_update().get(pk=receipt.source_purchase_order_id)
        refresh_order_receipt_status(order=order, actor=actor)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="purchases.GoodsReceipt",
        object_id=receipt.id,
        changes={"line_count": len(lines)},
    )
    return receipt


@transaction.atomic
def cancel_goods_receipt(*, receipt_id, organization, actor=None) -> GoodsReceipt:
    """DRAFT only. Once goods are RECEIVED the stock has physically moved and
    reversing it is a Vendor Credit with `return_stock=True` (a real return
    to the supplier), not a quiet cancellation — same reasoning as
    sales.cancel_delivery refusing a dispatched challan."""
    receipt = _get_receipt_for_update(receipt_id=receipt_id, organization=organization)
    if receipt.status != GoodsReceiptStatus.DRAFT:
        raise ApplicationError(
            "Only a draft goods receipt (before the goods are received) can be cancelled.",
            code="goods_receipt_invalid_status",
        )
    receipt.status = GoodsReceiptStatus.CANCELLED
    receipt.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.GoodsReceipt",
        object_id=receipt.id,
        changes={"status": "cancelled"},
    )
    return receipt
