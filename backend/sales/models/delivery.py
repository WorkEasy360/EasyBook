from django.db import models

from core.models import TenantScopedModel


class DeliveryChallanStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    DISPATCHED = "dispatched", "Dispatched"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"


class DeliveryChallan(TenantScopedModel):
    """Represents physical dispatch/delivery of goods. Creates a quantity-only
    StockMovement at dispatch — NEVER an accounting journal (COGS/Inventory
    Asset posts at Invoice time instead, aligned with revenue recognition —
    see sales/CLAUDE.md and INVOICE INVENTORY in the phase spec). Numbered at
    creation, same rationale as Quote/SalesOrder.
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="delivery_challans")
    challan_number = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=DeliveryChallanStatus.choices, default=DeliveryChallanStatus.DRAFT)

    # Nullable — a challan may fulfill a confirmed SalesOrder, or dispatch
    # directly with no order behind it. NOT unique: one order can be
    # fulfilled by several partial-delivery challans.
    source_sales_order = models.ForeignKey(
        "sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="delivery_challans"
    )
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    challan_date = models.DateField()
    notes = models.TextField(blank=True)

    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "challan_number"], name="uniq_delivery_challan_number_per_org"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "challan_date"]),
            models.Index(fields=["organization", "customer"]),
            models.Index(fields=["organization", "source_sales_order"]),
        ]
        ordering = ["-challan_date", "-created_at"]

    def __str__(self):
        return self.challan_number

    # DISPATCHED/DELIVERED/CANCELLED are all "left DRAFT" — only status and
    # the two milestone timestamps stay mutable, mirroring Quote/SalesOrder.
    _MUTABLE_AFTER_DRAFT_FIELDS = {"status", "dispatched_at", "delivered_at", "updated_at"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = DeliveryChallan.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != DeliveryChallanStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError(
                        "Delivery challans that have left DRAFT are immutable except through delivery services."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != DeliveryChallanStatus.DRAFT:
            raise ValueError("Only draft delivery challans can be deleted.")
        super().delete(*args, **kwargs)


class DeliveryChallanLine(TenantScopedModel):
    """No pricing/tax fields — a delivery challan has no revenue impact, it
    only records what physically moved (see sales/CLAUDE.md)."""

    challan = models.ForeignKey(DeliveryChallan, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    # Links back to the order line being fulfilled, if any — the only record
    # SalesOrder fulfillment status is derived from (never a stored quantity
    # on SalesOrderLine itself). See sales/selectors.py::get_fulfilled_quantity.
    source_order_line = models.ForeignKey(
        "sales.SalesOrderLine", null=True, blank=True, on_delete=models.PROTECT, related_name="delivery_lines"
    )
    line_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="delivery_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["challan", "line_number"]),
            models.Index(fields=["source_order_line"]),
        ]
        ordering = ["challan", "line_number"]

    def __str__(self):
        return f"{self.challan_id}#{self.line_number}"

    def _parent_status(self):
        return DeliveryChallan.all_objects.filter(pk=self.challan_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != DeliveryChallanStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a delivery challan that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != DeliveryChallanStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a delivery challan that has left DRAFT.")
        super().delete(*args, **kwargs)
