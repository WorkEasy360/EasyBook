import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.exceptions import ApplicationError
from inventory.models.stock_movement import OUTBOUND_MOVEMENT_TYPES, MovementType, StockMovement
from inventory.models.warehouse import Warehouse
from inventory.selectors import get_stock_on_hand
from inventory.services.settings import allows_negative_stock
from items.models.item import Item, ItemType


def _as_movement_datetime(value):
    """Accepts either a date (as used by accounting.JournalEntry.posting_date,
    so inventory services can share one "as of" value with an accounting
    posting) or a datetime, and always returns an aware datetime for
    StockMovement.movement_date."""
    if value is None:
        return timezone.now()
    if isinstance(value, datetime.datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    return timezone.make_aware(datetime.datetime.combine(value, datetime.time.min))

def _validate_item_and_warehouse(*, organization, item: Item, warehouse: Warehouse) -> None:
    if item.organization_id != organization.id:
        raise ApplicationError("Item must belong to the posting organization.", code="item_cross_org")
    if warehouse.organization_id != organization.id:
        raise ApplicationError("Warehouse must belong to the posting organization.", code="warehouse_cross_org")
    if item.item_type == ItemType.SERVICE:
        raise ApplicationError("Service items cannot have stock movements.", code="service_no_stock_movement")
    if not item.track_inventory:
        raise ApplicationError("Item does not track inventory.", code="item_not_tracked")
    if not item.is_active:
        raise ApplicationError("Inactive items cannot receive new stock movements.", code="item_inactive")
    if not warehouse.is_active:
        raise ApplicationError("Inactive warehouses cannot receive new stock movements.", code="warehouse_inactive")


@transaction.atomic
def record_stock_movement(
    *,
    organization,
    item: Item,
    warehouse: Warehouse,
    movement_type: str,
    quantity: Decimal,
    unit_cost: Decimal | None = None,
    movement_date=None,
    source_type: str = "",
    source_id: str = "",
    notes: str = "",
    created_by=None,
) -> StockMovement:
    """The single authoritative way to change stock-on-hand. See
    inventory/CLAUDE.md — no other code path may create a StockMovement.

    Concurrency-safe without a separate balance/lock row: locks every
    existing movement for this (item, warehouse) pair via select_for_update()
    before evaluating the negative-stock policy, so a concurrent movement
    against the same pair blocks until this transaction commits or rolls
    back (see inventory/CLAUDE.md for why this is sufficient).
    """
    _validate_item_and_warehouse(organization=organization, item=item, warehouse=warehouse)
    if quantity <= 0:
        raise ApplicationError("Movement quantity must be positive.", code="movement_quantity_invalid")
    if movement_type not in MovementType.values:
        raise ApplicationError("Unknown movement type.", code="movement_type_invalid")

    list(StockMovement.objects.select_for_update().filter(item=item, warehouse=warehouse))

    if movement_type in OUTBOUND_MOVEMENT_TYPES and not allows_negative_stock(organization):
        on_hand = get_stock_on_hand(item=item, warehouse=warehouse)
        if on_hand - quantity < 0:
            raise ApplicationError(
                f"Insufficient stock: {on_hand} available, {quantity} requested.",
                code="insufficient_stock",
            )

    return StockMovement.objects.create(
        organization=organization,
        item=item,
        warehouse=warehouse,
        movement_type=movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        movement_date=_as_movement_datetime(movement_date),
        source_type=source_type,
        source_id=source_id,
        notes=notes,
        created_by=created_by,
    )
