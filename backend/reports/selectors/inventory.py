"""Inventory reports — derived from `inventory.StockMovement` via
`inventory.selectors.get_weighted_average_cost` (PHASE 8 spec §11). No new
valuation algorithm: this module never recomputes cost itself, it only
groups the existing per-(item, warehouse) replay across the (item,
warehouse) pairs that actually have movements.
"""

from decimal import Decimal

from inventory.selectors import get_low_stock_items, get_weighted_average_cost

ZERO = Decimal("0")


def _active_item_warehouse_pairs(*, organization, item=None, warehouse=None):
    from inventory.models.stock_movement import StockMovement

    qs = StockMovement.objects.filter(organization=organization)
    if item is not None:
        qs = qs.filter(item=item)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    # .order_by() clears StockMovement's default ordering (movement_date,
    # sequence) — left in place, Django pulls those columns into the SELECT
    # to satisfy ORDER BY, which silently turns .distinct() into distinct-per
    # -movement instead of distinct-per-(item, warehouse) pair (a documented
    # Django gotcha: https://docs.djangoproject.com/en/stable/ref/models/querysets/#distinct).
    return list(qs.order_by().values_list("item_id", "warehouse_id").distinct())


def get_stock_summary(*, organization, warehouse=None, item=None, as_of=None) -> list[dict]:
    """Item x Warehouse rows with quantity on hand, average cost, and
    inventory value — computed only for pairs with actual movement history,
    not the full item x warehouse cross product."""
    from inventory.models.warehouse import Warehouse
    from items.models.item import Item

    pairs = _active_item_warehouse_pairs(organization=organization, item=item, warehouse=warehouse)
    if not pairs:
        return []

    items = {
        obj.id: obj
        for obj in Item.objects.filter(organization=organization, id__in={p[0] for p in pairs})
    }
    warehouses = {
        obj.id: obj
        for obj in Warehouse.objects.filter(organization=organization, id__in={p[1] for p in pairs})
    }

    rows = []
    for item_id, warehouse_id in pairs:
        item_obj = items.get(item_id)
        warehouse_obj = warehouses.get(warehouse_id)
        if item_obj is None or warehouse_obj is None:
            continue
        quantity, average_cost = get_weighted_average_cost(item=item_obj, warehouse=warehouse_obj, as_of=as_of)
        if quantity == ZERO and average_cost == ZERO:
            continue
        rows.append(
            {
                "item_id": item_obj.id,
                "item_name": item_obj.name,
                "item_sku": item_obj.sku,
                "warehouse_id": warehouse_obj.id,
                "warehouse_name": warehouse_obj.name,
                "quantity_on_hand": quantity,
                "average_cost": average_cost,
                "inventory_value": quantity * average_cost,
            }
        )
    rows.sort(key=lambda r: (r["item_name"], r["warehouse_name"]))
    return rows


def get_inventory_valuation(*, organization, warehouse=None, as_of=None) -> dict:
    rows = get_stock_summary(organization=organization, warehouse=warehouse, as_of=as_of)
    return {
        "as_of": as_of,
        "rows": rows,
        "total_value": sum((row["inventory_value"] for row in rows), ZERO),
    }


def get_inventory_movement_queryset(*, organization, item=None, warehouse=None, from_date=None, to_date=None):
    from inventory.models.stock_movement import StockMovement

    qs = StockMovement.objects.filter(organization=organization).select_related("item", "warehouse")
    if item is not None:
        qs = qs.filter(item=item)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    if from_date is not None:
        qs = qs.filter(movement_date__date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(movement_date__date__lte=to_date)
    # Same deterministic tiebreaker the valuation replay itself uses — never
    # the UUID primary key (inventory/CLAUDE.md).
    return qs.order_by("movement_date", "sequence")


def get_stock_adjustment_queryset(*, organization, from_date=None, to_date=None, status=None, warehouse=None):
    from inventory.models.stock_adjustment import StockAdjustment

    qs = StockAdjustment.objects.filter(organization=organization).prefetch_related("lines")
    if status:
        qs = qs.filter(status=status)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    if from_date is not None:
        qs = qs.filter(adjustment_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(adjustment_date__lte=to_date)
    return qs.order_by("adjustment_date", "created_at")


def get_low_stock_report(*, organization, warehouse=None) -> list[dict]:
    return [
        {
            "item_id": row["item"].id,
            "item_name": row["item"].name,
            "item_sku": row["item"].sku,
            "on_hand": row["on_hand"],
            "reorder_level": row["reorder_level"],
        }
        for row in get_low_stock_items(organization=organization, warehouse=warehouse)
    ]
