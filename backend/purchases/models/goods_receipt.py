from django.db import models

from core.models import TenantScopedModel


class GoodsReceiptStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    RECEIVED = "received", "Received"
    CANCELLED = "cancelled", "Cancelled"


class GoodsReceipt(TenantScopedModel):
    """Physical arrival of goods from a vendor — the inbound mirror of
    `sales.DeliveryChallan`.

    ACCOUNTING: a Goods Receipt creates a quantity-and-cost StockMovement
    (RECEIPT) but posts NO accounting journal, exactly as DeliveryChallan
    moves stock without posting one. The Inventory Asset / Accounts Payable
    journal is posted at BILL time, against the cost basis this receipt
    recorded (see purchases/services/bills.py and purchases/CLAUDE.md
    "GOODS RECEIPT vs BILL"). This is a deliberate, documented choice over
    the alternative GRNI (Goods Received Not Invoiced) accrual design, which
    would post Dr Inventory / Cr GRNI here and clear GRNI at bill time: GRNI
    requires inventing an accrual account no organization has configured
    (root CLAUDE.md: no implicit account is invented), and the symmetric
    "physical movement now, journal at the invoicing document" rule already
    holds on the sales side. The trade-off is that goods received but not
    yet billed are on hand physically without sitting in the Inventory Asset
    GL balance until the bill posts.

    DOUBLE-RECEIPT: because this receipt already moved the stock, a Bill line
    that links to a GoodsReceiptLine must NOT move it a second time — see
    `BillLine.source_goods_receipt_line` and the bill posting service.
    """

    vendor = models.ForeignKey("purchases.Vendor", on_delete=models.PROTECT, related_name="goods_receipts")
    receipt_number = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=GoodsReceiptStatus.choices, default=GoodsReceiptStatus.DRAFT)

    # Nullable and NOT unique — a receipt may fulfil an approved PO, or
    # record an unordered delivery; one PO may be received in several
    # partial shipments. Mirrors DeliveryChallan.source_sales_order.
    source_purchase_order = models.ForeignKey(
        "purchases.PurchaseOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="goods_receipts"
    )
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    receipt_date = models.DateField()
    # The vendor's own delivery-note/challan identifier, for reconciliation.
    vendor_document_number = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "receipt_number"], name="uniq_goods_receipt_number_per_org"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "receipt_date"]),
            models.Index(fields=["organization", "vendor"]),
            models.Index(fields=["organization", "source_purchase_order"]),
        ]
        ordering = ["-receipt_date", "-created_at"]

    def __str__(self):
        return self.receipt_number

    _MUTABLE_AFTER_DRAFT_FIELDS = {"status", "received_at", "updated_at"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = GoodsReceipt.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != GoodsReceiptStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError(
                        "Goods receipts that have left DRAFT are immutable except through goods receipt services."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != GoodsReceiptStatus.DRAFT:
            raise ValueError("Only draft goods receipts can be deleted.")
        super().delete(*args, **kwargs)


class GoodsReceiptLine(TenantScopedModel):
    """What physically arrived. Carries `unit_cost` — unlike its outbound
    mirror `sales.DeliveryChallanLine`, which needs no cost because an issue
    is valued from stock already on hand. An inbound movement has no prior
    basis to draw on, so the receipt must state one: the PO's agreed price
    when received against a PO, otherwise a caller-supplied cost.

    This recorded cost is the cost basis the Bill posts Inventory Asset at,
    so the GL inventory balance and the movement-derived valuation can never
    drift apart (see purchases/services/bills.py). A vendor billing a
    different price produces an explicit purchase price variance, never a
    silent re-valuation.
    """

    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    # Links back to the PO line being received, if any — the only record PO
    # receipt status is derived from (never a stored quantity on
    # PurchaseOrderLine). See purchases/selectors.py::get_received_quantity.
    source_order_line = models.ForeignKey(
        "purchases.PurchaseOrderLine", null=True, blank=True, on_delete=models.PROTECT, related_name="receipt_lines"
    )
    line_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    # 4dp to match inventory.StockMovement.unit_cost exactly — a receipt cost
    # is a per-unit valuation input, not a money amount presented to a user.
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="goods_receipt_line_quantity_positive"),
            models.CheckConstraint(condition=models.Q(unit_cost__gte=0), name="goods_receipt_line_unit_cost_nonnegative"),
        ]
        indexes = [
            models.Index(fields=["receipt", "line_number"]),
            models.Index(fields=["source_order_line"]),
        ]
        ordering = ["receipt", "line_number"]

    def __str__(self):
        return f"{self.receipt_id}#{self.line_number}"

    def _parent_status(self):
        return GoodsReceipt.all_objects.filter(pk=self.receipt_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != GoodsReceiptStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a goods receipt that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != GoodsReceiptStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a goods receipt that has left DRAFT.")
        super().delete(*args, **kwargs)
