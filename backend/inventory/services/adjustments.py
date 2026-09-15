from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from inventory.models.stock_adjustment import (
    AdjustmentLineDirection,
    AdjustmentStatus,
    StockAdjustment,
    StockAdjustmentLine,
)
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_weighted_average_cost
from inventory.services.accounting_bridge import group_net_amount_by_inventory_account, post_inventory_journal
from inventory.services.movements import record_stock_movement
from items.models.item import ItemType


def _get_adjustment_for_update(*, adjustment_id, organization) -> StockAdjustment:
    try:
        return StockAdjustment.objects.select_for_update().get(id=adjustment_id, organization=organization)
    except StockAdjustment.DoesNotExist:
        raise ApplicationError("Stock adjustment not found.", code="adjustment_not_found", status_code=404)


@transaction.atomic
def create_draft_adjustment(
    *, organization, warehouse, adjustment_date, reason: str, lines: list[dict], memo: str = "",
    contra_account=None, created_by=None,
) -> StockAdjustment:
    if not lines:
        raise ApplicationError("A stock adjustment needs at least one line.", code="adjustment_too_few_lines")
    if warehouse.organization_id != organization.id:
        raise ApplicationError("Warehouse must belong to the organization.", code="warehouse_cross_org")
    if contra_account is not None and contra_account.organization_id != organization.id:
        raise ApplicationError("Contra account must belong to the organization.", code="cross_org_reference")

    for index, line in enumerate(lines, start=1):
        item = line["item"]
        if item.organization_id != organization.id:
            raise ApplicationError(f"Line {index}: item must belong to the organization.", code="item_cross_org")
        if item.item_type == ItemType.SERVICE or not item.track_inventory:
            raise ApplicationError(f"Line {index}: item does not track inventory.", code="item_not_tracked")
        if line["quantity"] <= 0:
            raise ApplicationError(f"Line {index}: quantity must be positive.", code="adjustment_line_quantity_invalid")
        if line["direction"] == AdjustmentLineDirection.IN and line.get("unit_cost") is None:
            raise ApplicationError(
                f"Line {index}: unit_cost is required for an inbound adjustment line.",
                code="adjustment_line_cost_required",
            )

    adjustment = StockAdjustment.objects.create(
        organization=organization, warehouse=warehouse, adjustment_date=adjustment_date, reason=reason,
        memo=memo, contra_account=contra_account, created_by=created_by,
    )
    for index, line in enumerate(lines, start=1):
        StockAdjustmentLine.objects.create(
            organization=organization, adjustment=adjustment, item=line["item"], line_number=index,
            direction=line["direction"], quantity=line["quantity"], unit_cost=line.get("unit_cost"),
            notes=line.get("notes", ""),
        )
    return adjustment


@transaction.atomic
def replace_draft_adjustment_lines(*, adjustment: StockAdjustment, lines: list[dict]) -> StockAdjustment:
    """Replaces all lines on a DRAFT stock adjustment — posted adjustments
    are immutable (see inventory/CLAUDE.md), so this raises otherwise."""
    if adjustment.status != AdjustmentStatus.DRAFT:
        raise ApplicationError("Only draft stock adjustments can be modified.", code="adjustment_not_draft")
    if not lines:
        raise ApplicationError("A stock adjustment needs at least one line.", code="adjustment_too_few_lines")

    for index, line in enumerate(lines, start=1):
        item = line["item"]
        if item.organization_id != adjustment.organization_id:
            raise ApplicationError(f"Line {index}: item must belong to the organization.", code="item_cross_org")
        if item.item_type == ItemType.SERVICE or not item.track_inventory:
            raise ApplicationError(f"Line {index}: item does not track inventory.", code="item_not_tracked")
        if line["quantity"] <= 0:
            raise ApplicationError(f"Line {index}: quantity must be positive.", code="adjustment_line_quantity_invalid")
        if line["direction"] == AdjustmentLineDirection.IN and line.get("unit_cost") is None:
            raise ApplicationError(
                f"Line {index}: unit_cost is required for an inbound adjustment line.",
                code="adjustment_line_cost_required",
            )

    adjustment.lines.all().delete()
    for index, line in enumerate(lines, start=1):
        StockAdjustmentLine.objects.create(
            organization=adjustment.organization, adjustment=adjustment, item=line["item"], line_number=index,
            direction=line["direction"], quantity=line["quantity"], unit_cost=line.get("unit_cost"),
            notes=line.get("notes", ""),
        )
    return adjustment


@transaction.atomic
def post_stock_adjustment(*, adjustment_id, organization, actor=None) -> StockAdjustment:
    """The single authoritative path to turn a DRAFT stock adjustment into
    posted StockMovements (and, if contra_account is set, a Phase 1
    accounting journal). Idempotent like accounting.services.posting.post_journal:
    posting an already-POSTED adjustment is a locked no-op."""
    adjustment = _get_adjustment_for_update(adjustment_id=adjustment_id, organization=organization)

    if adjustment.status == AdjustmentStatus.POSTED:
        return adjustment
    if adjustment.status != AdjustmentStatus.DRAFT:
        raise ApplicationError(
            f"Cannot post an adjustment in status '{adjustment.status}'.", code="adjustment_invalid_status"
        )

    lines = list(
        StockAdjustmentLine.objects.select_for_update()
        .select_related("item")
        .filter(adjustment=adjustment)
        .order_by("line_number")
    )
    if not lines:
        raise ApplicationError("Cannot post an adjustment with no lines.", code="adjustment_too_few_lines")

    value_entries = []
    for line in lines:
        item = line.item
        if item.organization_id != organization.id:
            raise ApplicationError("All items must belong to the posting organization.", code="item_cross_org")
        if not item.is_active:
            raise ApplicationError(f"Item {item.name} is inactive.", code="item_inactive")

        if line.direction == AdjustmentLineDirection.IN:
            movement_type = MovementType.ADJUSTMENT_IN
            unit_cost = line.unit_cost
        else:
            movement_type = MovementType.ADJUSTMENT_OUT
            _, unit_cost = get_weighted_average_cost(item=item, warehouse=adjustment.warehouse)

        record_stock_movement(
            organization=organization,
            item=item,
            warehouse=adjustment.warehouse,
            movement_type=movement_type,
            quantity=line.quantity,
            unit_cost=unit_cost,
            movement_date=adjustment.adjustment_date,
            source_type="stock_adjustment",
            source_id=str(adjustment.id),
            notes=line.notes,
            created_by=actor,
        )
        signed_value = line.quantity * unit_cost
        if line.direction == AdjustmentLineDirection.OUT:
            signed_value = -signed_value
        value_entries.append({"item": item, "signed_value": signed_value})

    journal = None
    if adjustment.contra_account is not None:
        net_by_account = group_net_amount_by_inventory_account(value_entries)
        journal = post_inventory_journal(
            organization=organization,
            posting_date=adjustment.adjustment_date,
            currency=organization.default_currency,
            net_amount_by_inventory_account=net_by_account,
            contra_account=adjustment.contra_account,
            memo=f"Stock adjustment — {adjustment.get_reason_display()}",
            source_type="stock_adjustment",
            source_id=str(adjustment.id),
            actor=actor,
        )

    adjustment.status = AdjustmentStatus.POSTED
    adjustment.posted_by = actor
    adjustment.posted_at = timezone.now()
    adjustment.accounting_journal = journal
    adjustment.save(update_fields=["status", "posted_by", "posted_at", "accounting_journal", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="inventory.StockAdjustment",
        object_id=adjustment.id,
        changes={"line_count": len(lines), "journal_id": str(journal.id) if journal else None},
    )
    return adjustment


@transaction.atomic
def reverse_stock_adjustment(*, adjustment_id, organization, actor=None, adjustment_date=None, memo: str = "") -> StockAdjustment:
    """Creates and posts a counter-adjustment that exactly mirrors the
    original's quantity impact (and reverses its accounting journal, if any),
    preserving the original untouched — never a destructive edit."""
    original = _get_adjustment_for_update(adjustment_id=adjustment_id, organization=organization)

    if original.status != AdjustmentStatus.POSTED:
        raise ApplicationError(
            f"Cannot reverse an adjustment in status '{original.status}'.", code="adjustment_invalid_status"
        )
    if hasattr(original, "reversal"):
        raise ApplicationError("This adjustment has already been reversed.", code="adjustment_already_reversed")

    original_lines = list(original.lines.select_related("item").order_by("line_number"))
    original_movements = list(
        StockMovement.objects.filter(source_type="stock_adjustment", source_id=str(original.id)).order_by("sequence")
    )
    cost_by_item_id = {m.item_id: m.unit_cost for m in original_movements}

    reversal_lines = []
    for line in original_lines:
        flipped = AdjustmentLineDirection.OUT if line.direction == AdjustmentLineDirection.IN else AdjustmentLineDirection.IN
        reversal_lines.append(
            {
                "item": line.item,
                "direction": flipped,
                "quantity": line.quantity,
                "unit_cost": cost_by_item_id.get(line.item_id) if flipped == AdjustmentLineDirection.IN else None,
            }
        )

    reversal = create_draft_adjustment(
        organization=organization,
        warehouse=original.warehouse,
        adjustment_date=adjustment_date or timezone.now().date(),
        reason=original.reason,
        lines=reversal_lines,
        memo=memo or f"Reversal of adjustment {original.id}",
        contra_account=original.contra_account,
        created_by=actor,
    )
    reversal.reverses = original
    reversal.save(update_fields=["reverses"])

    reversal = post_stock_adjustment(adjustment_id=reversal.id, organization=organization, actor=actor)

    original.status = AdjustmentStatus.REVERSED
    original.save(update_fields=["status", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="inventory.StockAdjustment",
        object_id=original.id,
        changes={"reversal_adjustment_id": str(reversal.id)},
    )
    return reversal
