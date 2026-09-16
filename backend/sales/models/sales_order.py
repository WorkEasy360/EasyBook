from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class SalesOrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    # Reached only via the Delivery Challan fulfillment service (Slice 4) —
    # fulfilled quantity is derived from DeliveryChallanLine records linked
    # to each SalesOrderLine, never stored on the line itself (same
    # never-cache-a-balance principle as inventory.StockMovement/GL).
    PARTIALLY_FULFILLED = "partially_fulfilled", "Partially Fulfilled"
    FULFILLED = "fulfilled", "Fulfilled"
    CANCELLED = "cancelled", "Cancelled"


class SalesOrder(TenantScopedModel):
    """A confirmed intent to sell, created directly or from an accepted Quote.
    Creates NO accounting entries — see sales/CLAUDE.md. Numbered at creation,
    same rationale as Quote: never an authoritative financial record itself.
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="sales_orders")
    order_number = models.CharField(max_length=32)
    status = models.CharField(max_length=24, choices=SalesOrderStatus.choices, default=SalesOrderStatus.DRAFT)

    # A quote may convert to at most one sales order — mirrors
    # accounting.JournalEntry.reverses (a journal reversed at most once).
    source_quote = models.OneToOneField(
        "sales.Quote", null=True, blank=True, on_delete=models.PROTECT, related_name="sales_order"
    )

    order_date = models.DateField()

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    # --- GST treatment (Phase 7) ------------------------------------------
    # Chosen once per document and carried through conversion, so a quote
    # accepted in one quarter and invoiced in the next cannot silently change
    # its tax treatment when the customer's master data is edited in between.
    #
    # `place_of_supply` defaults from the party but is the DOCUMENT's own
    # field: the statutory answer (IGST Act ss.10-13) turns on facts about the
    # individual supply that master data cannot know, and
    # tax/services/determination.py deliberately does not infer it.
    place_of_supply = models.ForeignKey(
        "tax.StateCode", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # What tax applies - the computed answer to IGST Act ss.7/8, and the only
    # field the component split branches on.
    supply_nature = models.CharField(
        max_length=24, choices=SupplyNature.choices, default=SupplyNature.UNSPECIFIED
    )
    # The e-Invoice schema's TranDtls.SupTyp, snapshotted at document time
    # rather than re-derived at payload time. Deriving it later would read the
    # counterparty's CURRENT tax treatment, which can have changed since the
    # document was issued - the same reason this file already snapshots
    # hsn_sac_snapshot and tax_label instead of following the FK.
    supply_type = models.CharField(
        max_length=16, choices=SupplyType.choices, default=SupplyType.UNSPECIFIED
    )
    is_reverse_charge = models.BooleanField(default=False)

    # Recalculated server-side from lines — never client-writable directly.
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    discount_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "order_number"], name="uniq_sales_order_number_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "order_date"]),
            models.Index(fields=["organization", "customer"]),
        ]
        ordering = ["-order_date", "-created_at"]

    def __str__(self):
        return self.order_number

    # Once an order leaves DRAFT it is immutable except through order
    # services — mirrors Quote/JournalEntry. `status` stays mutable after
    # DRAFT because CONFIRMED -> PARTIALLY_FULFILLED -> FULFILLED are later,
    # non-DRAFT transitions driven by the fulfillment service (Slice 4).
    _MUTABLE_AFTER_DRAFT_FIELDS = {"status", "updated_at"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = SalesOrder.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != SalesOrderStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError("Sales orders that have left DRAFT are immutable except through order services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != SalesOrderStatus.DRAFT:
            raise ValueError("Only draft sales orders can be deleted.")
        super().delete(*args, **kwargs)


class SalesOrderLine(TenantScopedModel):
    """Snapshot at order time — same principle as QuoteLine (sales/CLAUDE.md)."""

    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    line_number = models.PositiveIntegerField()

    description = models.CharField(max_length=255, blank=True)
    hsn_sac_snapshot = models.CharField(max_length=16, blank=True)
    tax_label = models.CharField(max_length=64, blank=True)

    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    line_base = models.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2)
    taxable_amount = models.DecimalField(max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2)
    line_total = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="sales_order_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["order", "line_number"]),
        ]
        ordering = ["order", "line_number"]

    def __str__(self):
        return f"{self.order_id}#{self.line_number}"

    def _parent_status(self):
        return SalesOrder.all_objects.filter(pk=self.order_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != SalesOrderStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a sales order that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != SalesOrderStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a sales order that has left DRAFT.")
        super().delete(*args, **kwargs)
