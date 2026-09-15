import uuid

from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from inventory.models.stock_movement import MovementType
from inventory.selectors import get_weighted_average_cost
from inventory.services.movements import record_stock_movement

# Transfers have no separate header table: a matched TRANSFER_OUT/TRANSFER_IN
# pair of StockMovements sharing source_type/source_id IS the transfer record
# — nothing beyond that pairing is needed at this phase. See inventory/CLAUDE.md.
TRANSFER_SOURCE_TYPE = "stock_transfer"


@transaction.atomic
def transfer_stock(*, organization, item, from_warehouse, to_warehouse, quantity, transfer_date=None, notes: str = "", actor=None) -> dict:
    if from_warehouse.id == to_warehouse.id:
        raise ApplicationError("Source and destination warehouse must differ.", code="transfer_same_warehouse")

    # Carries the source's current weighted-average cost across so the
    # destination warehouse's valuation stays consistent — a transfer moves
    # cost basis along with quantity, it doesn't create or destroy value.
    _, unit_cost = get_weighted_average_cost(item=item, warehouse=from_warehouse)

    transfer_id = str(uuid.uuid4())
    out_movement = record_stock_movement(
        organization=organization, item=item, warehouse=from_warehouse, movement_type=MovementType.TRANSFER_OUT,
        quantity=quantity, unit_cost=unit_cost, movement_date=transfer_date, source_type=TRANSFER_SOURCE_TYPE,
        source_id=transfer_id, notes=notes, created_by=actor,
    )
    in_movement = record_stock_movement(
        organization=organization, item=item, warehouse=to_warehouse, movement_type=MovementType.TRANSFER_IN,
        quantity=quantity, unit_cost=unit_cost, movement_date=transfer_date, source_type=TRANSFER_SOURCE_TYPE,
        source_id=transfer_id, notes=notes, created_by=actor,
    )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="inventory.StockTransfer",
        object_id=transfer_id,
        changes={
            "item": str(item.id), "quantity": str(quantity),
            "from_warehouse": str(from_warehouse.id), "to_warehouse": str(to_warehouse.id),
        },
    )
    return {"transfer_id": transfer_id, "out_movement": out_movement, "in_movement": in_movement}
