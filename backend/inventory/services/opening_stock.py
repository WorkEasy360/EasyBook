import uuid

from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from inventory.models.stock_movement import MovementType
from inventory.services.accounting_bridge import group_net_amount_by_inventory_account, post_inventory_journal
from inventory.services.movements import record_stock_movement


@transaction.atomic
def post_opening_stock(*, organization, warehouse, currency, lines: list[dict], opening_date, contra_account=None, actor=None):
    """`lines`: [{"item": Item, "quantity": Decimal, "unit_cost": Decimal}].

    Creates one OPENING StockMovement per line (never a bare quantity field —
    see inventory/CLAUDE.md) and, only if `contra_account` is supplied, a
    single balancing journal posted through Phase 1's accounting engine.
    Omitting `contra_account` records a quantity-only opening balance with no
    financial effect — a deliberate choice, not an oversight (see
    inventory/CLAUDE.md).
    """
    if not lines:
        raise ApplicationError("Opening stock requires at least one line.", code="opening_stock_empty")

    batch_id = str(uuid.uuid4())
    movements = []
    value_entries = []
    for line in lines:
        item = line["item"]
        quantity = line["quantity"]
        unit_cost = line["unit_cost"]
        movement = record_stock_movement(
            organization=organization,
            item=item,
            warehouse=warehouse,
            movement_type=MovementType.OPENING,
            quantity=quantity,
            unit_cost=unit_cost,
            movement_date=opening_date,
            source_type="opening_stock",
            source_id=batch_id,
            created_by=actor,
        )
        movements.append(movement)
        value_entries.append({"item": item, "signed_value": quantity * unit_cost})

    journal = None
    if contra_account is not None:
        net_by_account = group_net_amount_by_inventory_account(value_entries)
        journal = post_inventory_journal(
            organization=organization,
            posting_date=opening_date,
            currency=currency,
            net_amount_by_inventory_account=net_by_account,
            contra_account=contra_account,
            memo=f"Opening stock — {warehouse.code}",
            source_type="opening_stock",
            source_id=batch_id,
            actor=actor,
        )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="inventory.OpeningStock",
        object_id=batch_id,
        changes={
            "warehouse": str(warehouse.id),
            "line_count": len(lines),
            "journal_id": str(journal.id) if journal else None,
        },
    )
    return {"movements": movements, "journal": journal}
