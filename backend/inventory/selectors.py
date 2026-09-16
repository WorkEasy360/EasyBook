"""Read-only queries over stock movements.

Like accounting/selectors.py, deliberately NOT backed by a mutable balance
table: stock-on-hand and weighted-average cost are always derived from
StockMovement rows so nothing can drift from the authoritative movement
history. See inventory/CLAUDE.md for the documented tradeoffs (replay cost,
backdating, negative-stock edge cases).
"""

import datetime
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from inventory.models.stock_movement import INBOUND_MOVEMENT_TYPES, StockMovement


def _as_of_cutoff(value):
    """Normalizes an `as_of` filter value for comparison against
    `StockMovement.movement_date`, which is an aware DateTimeField.

    A bare `date` is widened to the END of that day, not the start. Callers
    pass a date constantly (`challan.challan_date`, `invoice.invoice_date`,
    `bill.bill_date` — all DateFields), and Django would otherwise coerce it
    to midnight, silently EXCLUDING every movement made during that day. That
    made "the average cost as of the dispatch date" skip the dispatch date's
    own receipts. The coercion also emitted a naive-datetime RuntimeWarning,
    which was the visible symptom of the real off-by-one.

    A datetime is passed through, made aware if it is not already.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    end_of_day = datetime.datetime.combine(value, datetime.time.max)
    return timezone.make_aware(end_of_day)


def get_stock_on_hand(*, item, warehouse=None, as_of=None) -> Decimal:
    qs = StockMovement.objects.filter(item=item)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    cutoff = _as_of_cutoff(as_of)
    if cutoff is not None:
        qs = qs.filter(movement_date__lte=cutoff)

    inbound = qs.filter(movement_type__in=INBOUND_MOVEMENT_TYPES).aggregate(total=Sum("quantity"))["total"]
    outbound = qs.exclude(movement_type__in=INBOUND_MOVEMENT_TYPES).aggregate(total=Sum("quantity"))["total"]
    return (inbound or Decimal("0")) - (outbound or Decimal("0"))


def get_weighted_average_cost(*, item, warehouse, as_of=None) -> tuple[Decimal, Decimal]:
    """Replays movement history chronologically for one (item, warehouse).

    Returns (quantity, average_cost). Issues never change average_cost (only
    receipts/opening/adjustment-in/transfer-in do); if quantity reaches zero
    (or goes negative under an ALLOW_NEGATIVE_STOCK policy) average_cost
    simply freezes at its last computed value until the next inbound
    movement. Always a full replay (never cached), so a backdated movement
    is automatically reflected correctly the next time this is called.
    """
    qs = StockMovement.objects.filter(item=item, warehouse=warehouse)
    cutoff = _as_of_cutoff(as_of)
    if cutoff is not None:
        qs = qs.filter(movement_date__lte=cutoff)

    quantity = Decimal("0")
    average_cost = Decimal("0")
    for movement in qs.order_by("movement_date", "sequence"):
        if movement.movement_type in INBOUND_MOVEMENT_TYPES:
            incoming_cost = movement.unit_cost if movement.unit_cost is not None else average_cost
            total_value = quantity * average_cost + movement.quantity * incoming_cost
            quantity += movement.quantity
            average_cost = (total_value / quantity) if quantity > 0 else Decimal("0")
        else:
            quantity -= movement.quantity
    return quantity, average_cost


def get_low_stock_items(*, organization, warehouse=None):
    """Deterministic low-stock detection — no AI, no heuristics."""
    from items.models.item import Item

    candidates = Item.objects.filter(
        organization=organization, track_inventory=True, is_active=True, reorder_level__gt=Decimal("0")
    )
    results = []
    for item in candidates:
        on_hand = get_stock_on_hand(item=item, warehouse=warehouse)
        if on_hand <= item.reorder_level:
            results.append({"item": item, "on_hand": on_hand, "reorder_level": item.reorder_level})
    return results
