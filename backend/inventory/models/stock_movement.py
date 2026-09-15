from django.conf import settings
from django.db import connection, models

from core.models import TenantScopedModel

# Backs StockMovement.sequence — see the field's docstring for why a random
# UUID can't be used as the chronological tiebreaker. Created via raw SQL in
# migration 0007 because Django's AutoField/BigAutoField requires
# primary_key=True (fields.E100), and this deliberately isn't the PK.
_SEQUENCE_NAME = "inventory_stockmovement_seq"


class MovementType(models.TextChoices):
    OPENING = "opening", "Opening"
    RECEIPT = "receipt", "Receipt"
    ISSUE = "issue", "Issue"
    ADJUSTMENT_IN = "adjustment_in", "Adjustment In"
    ADJUSTMENT_OUT = "adjustment_out", "Adjustment Out"
    TRANSFER_IN = "transfer_in", "Transfer In"
    TRANSFER_OUT = "transfer_out", "Transfer Out"


INBOUND_MOVEMENT_TYPES = {
    MovementType.OPENING,
    MovementType.RECEIPT,
    MovementType.ADJUSTMENT_IN,
    MovementType.TRANSFER_IN,
}
OUTBOUND_MOVEMENT_TYPES = {
    MovementType.ISSUE,
    MovementType.ADJUSTMENT_OUT,
    MovementType.TRANSFER_OUT,
}


class StockMovement(TenantScopedModel):
    """Authoritative, append-only record of every quantity change.

    Never updated or deleted after creation (see inventory/CLAUDE.md) — the
    only way application code affects "stock on hand" is by inserting a new
    movement through inventory.services; there is no mutable quantity field
    anywhere else to drift out of sync.
    """

    # Insertion-order tiebreaker for chronological replay (get_weighted_average_cost,
    # running-ledger queries) when two movements share the same movement_date
    # and even the same created_at timestamp — which happens more often than
    # it sounds, since Python/Windows clock resolution can repeat within a
    # tight loop. The UUID primary key is NOT safe to use for this: it's
    # random, so falling back to it as a tiebreaker silently makes valuation
    # non-deterministic. `default=0` is a placeholder only — save() always
    # overwrites it with a real value from the DB sequence before insert.
    sequence = models.BigIntegerField(unique=True, editable=False, default=0)

    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="stock_movements")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="stock_movements")
    movement_type = models.CharField(max_length=16, choices=MovementType.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    movement_date = models.DateTimeField()

    source_type = models.CharField(max_length=64, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="stock_movement_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "item", "warehouse"]),
            models.Index(fields=["organization", "movement_date"]),
            models.Index(fields=["organization", "source_type", "source_id"]),
        ]
        ordering = ["movement_date", "sequence"]

    def __str__(self):
        return f"{self.movement_type}:{self.item_id}@{self.warehouse_id}:{self.quantity}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Stock movements are append-only and cannot be modified.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT nextval(%s)", [_SEQUENCE_NAME])
            self.sequence = cursor.fetchone()[0]
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Stock movements are append-only and cannot be deleted.")

    @property
    def is_inbound(self) -> bool:
        return self.movement_type in INBOUND_MOVEMENT_TYPES
