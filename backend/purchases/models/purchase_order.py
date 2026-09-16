from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class PurchaseOrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    # An approved PO is a commitment to buy, but still creates NO accounting
    # entries and NO stock (see the class docstring). APPROVED is the
    # buy-side counterpart of SalesOrderStatus.CONFIRMED.
    APPROVED = "approved", "Approved"
    # Reached only via the Goods Receipt service — received quantity is
    # derived from GoodsReceiptLine rows linked to each PurchaseOrderLine,
    # never stored on the line itself (same never-cache-a-balance principle
    # as inventory.StockMovement/GL).
    PARTIALLY_RECEIVED = "partially_received", "Partially Received"
    RECEIVED = "received", "Received"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseOrder(TenantScopedModel):
    """A commitment to buy, the buy-side mirror of `sales.SalesOrder`.
    Creates NO accounting entries and NO stock movements — goods arrive via
    GoodsReceipt, money is owed via Bill. Numbered at creation, same
    rationale as SalesOrder: never an authoritative financial record itself.

    `expected_date` has no sales-side counterpart: a purchase commitment has
    a promised delivery date the three-way-match report reads, whereas a
    sales order's fulfillment timing is ours to choose.
    """

    vendor = models.ForeignKey("purchases.Vendor", on_delete=models.PROTECT, related_name="purchase_orders")
    order_number = models.CharField(max_length=32)
    status = models.CharField(max_length=24, choices=PurchaseOrderStatus.choices, default=PurchaseOrderStatus.DRAFT)

    order_date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=255, blank=True)

    # Where goods are expected to land. Nullable because a PO may contain
    # only service/expense lines, which never touch inventory. Validated at
    # Goods Receipt time, not here.
    warehouse = models.ForeignKey(
        "inventory.Warehouse", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

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
            models.UniqueConstraint(fields=["organization", "order_number"], name="uniq_purchase_order_number_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "order_date"]),
            models.Index(fields=["organization", "vendor"]),
            models.Index(fields=["organization", "expected_date"]),
        ]
        ordering = ["-order_date", "-created_at"]

    def __str__(self):
        return self.order_number

    # Once a PO leaves DRAFT it is immutable except through PO services —
    # mirrors SalesOrder/JournalEntry. `status` stays mutable after DRAFT
    # because APPROVED -> PARTIALLY_RECEIVED -> RECEIVED are later,
    # non-DRAFT transitions driven by the goods-receipt service.
    _MUTABLE_AFTER_DRAFT_FIELDS = {"status", "updated_at"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = PurchaseOrder.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != PurchaseOrderStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError(
                        "Purchase orders that have left DRAFT are immutable except through purchase order services."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != PurchaseOrderStatus.DRAFT:
            raise ValueError("Only draft purchase orders can be deleted.")
        super().delete(*args, **kwargs)


class PurchaseOrderLine(TenantScopedModel):
    """Snapshot at order time — same principle as SalesOrderLine. `unit_price`
    here is the AGREED price, which the three-way match later compares
    against what the vendor actually billed (see services/three_way_match.py).
    """

    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
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
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="purchase_order_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["order", "line_number"]),
        ]
        ordering = ["order", "line_number"]

    def __str__(self):
        return f"{self.order_id}#{self.line_number}"

    def _parent_status(self):
        return PurchaseOrder.all_objects.filter(pk=self.order_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != PurchaseOrderStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a purchase order that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != PurchaseOrderStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a purchase order that has left DRAFT.")
        super().delete(*args, **kwargs)
