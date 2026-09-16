from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class BillStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    # The buy-side counterpart of InvoiceStatus.SENT: posted, authoritative,
    # money owed. Named OPEN rather than "sent" because we receive a bill,
    # we do not send it.
    OPEN = "open", "Open"
    PARTIALLY_PAID = "partially_paid", "Partially Paid"
    PAID = "paid", "Paid"
    # Not actively transitioned into by any service — computed on demand by
    # selectors.get_overdue_bills(), same deferred-choice pattern as
    # sales.InvoiceStatus.OVERDUE.
    OVERDUE = "overdue", "Overdue"
    VOID = "void", "Void"


class Bill(TenantScopedModel):
    """A vendor's authoritative demand for payment — the buy-side mirror of
    `sales.Invoice`. Numbered at POST time (not creation), like Invoice and
    JournalEntry.

    Carries BOTH numbers a purchase has and a sale does not: `bill_number`
    is OURS (allocated from our NumberSequence at posting), while
    `vendor_bill_number` is the vendor's own reference off the paper bill.
    A sales invoice needs only the former because we are the issuer.

    No `amount_paid`/`amount_due` columns — see selectors.py, same
    never-cache-a-cross-document-sum principle as sales.Invoice.
    """

    vendor = models.ForeignKey("purchases.Vendor", on_delete=models.PROTECT, related_name="bills")
    bill_number = models.CharField(max_length=32, blank=True)
    vendor_bill_number = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=BillStatus.choices, default=BillStatus.DRAFT)

    # Plain FK, NOT OneToOne: one PO is commonly billed across several
    # partial bills (unlike Quote -> Invoice on the sales side, which
    # converts at most once). Receipt/billing progress is derived from
    # lines, never from this header link.
    source_purchase_order = models.ForeignKey(
        "purchases.PurchaseOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="bills"
    )

    bill_date = models.DateField()
    due_date = models.DateField()
    reference = models.CharField(max_length=255, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    # Explicit, never defaulted from Vendor.default_payable_account — same
    # "no implicit account is invented" principle as sales.Invoice.
    payable_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    # Input tax / ITC is an ASSET (recoverable from the tax authority),
    # the mirror of sales' tax_payable_account LIABILITY. Only required
    # (validated at post time) if tax_total > 0. Phase 7 owns whether a
    # given tax is actually recoverable; this phase only records where the
    # caller says it goes.
    tax_recoverable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Only required (validated at post time) when a line linked to a goods
    # receipt is billed at a different price than the receipt recorded —
    # see services/bills.py. Conditional exactly like tax_recoverable_account.
    price_variance_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Only required (validated at post time) if a line needs stock received
    # directly (no source_goods_receipt_line) — see services/bills.py.
    warehouse = models.ForeignKey(
        "inventory.Warehouse", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    accounting_journal = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

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
    # GST component totals. Sums of the already-rounded per-line component
    # amounts, never a second rounding of the header - so line and header
    # totals agree to the cent by construction (same rule as
    # core.money.calculate_document_totals).
    cgst_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    sgst_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    igst_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    cess_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    # TDS/TCS is income tax riding alongside the trade transaction, not GST:
    # it is taken OUT of the settlement rather than added on top, so it is a
    # separate field with its own account rather than another tax component.
    withholding_section = models.ForeignKey(
        "tax.WithholdingSection", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    withholding_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0")
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "bill_number"], name="uniq_bill_number_per_org",
                condition=~models.Q(bill_number=""),
            ),
            # Duplicate-bill defence: the same vendor cannot bill us twice
            # under one document number. Partial (skips blanks) because
            # vendor_bill_number is optional, exactly like bill_number's own
            # constraint. This is a database-level backstop for the
            # service-layer check, same belt-and-braces pattern as
            # sales.RecurringInvoiceRun's (template, occurrence_date).
            models.UniqueConstraint(
                fields=["organization", "vendor", "vendor_bill_number"],
                name="uniq_vendor_bill_number_per_vendor",
                condition=~models.Q(vendor_bill_number=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "bill_date"]),
            models.Index(fields=["organization", "vendor"]),
            models.Index(fields=["organization", "due_date"]),
            models.Index(fields=["vendor", "status"]),
        ]
        ordering = ["-bill_date", "-created_at"]

    def __str__(self):
        return self.bill_number or f"draft:{self.id}"

    _MUTABLE_AFTER_DRAFT_FIELDS = {
        "status", "bill_number", "posted_by", "posted_by_id", "posted_at", "accounting_journal",
        "accounting_journal_id", "voided_by", "voided_by_id", "voided_at", "void_reason", "updated_at",
    }

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = Bill.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != BillStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError("Posted bills are immutable except through bill services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != BillStatus.DRAFT:
            raise ValueError("Only draft bills can be deleted.")
        super().delete(*args, **kwargs)


class BillLine(TenantScopedModel):
    """Snapshot at bill time — same principle as sales.InvoiceLine.

    `source_goods_receipt_line`, if set, means the stock for this line
    ALREADY arrived via that receipt: posting must not receive it again.
    This is the purchase-side double-receipt guard, the exact counterpart of
    `sales.InvoiceLine.source_delivery_challan_line` (see
    purchases/services/bills.py and purchases/CLAUDE.md).

    `expense_account` overrides the item's own `purchase_account` for this
    one line — nullable, and only consulted for a line that is expensed
    rather than inventoried. It exists because the same item is legitimately
    coded to different expense accounts on different bills (a laptop bought
    for resale vs for internal use), which is not true of the sales side's
    revenue account, hence no mirror field on InvoiceLine.
    """

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    source_goods_receipt_line = models.ForeignKey(
        "purchases.GoodsReceiptLine", null=True, blank=True, on_delete=models.PROTECT, related_name="bill_lines"
    )
    # Recorded for the three-way match even when the goods were received
    # without going through a receipt document.
    source_order_line = models.ForeignKey(
        "purchases.PurchaseOrderLine", null=True, blank=True, on_delete=models.PROTECT, related_name="bill_lines"
    )
    expense_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    line_number = models.PositiveIntegerField()

    description = models.CharField(max_length=255, blank=True)
    hsn_sac_snapshot = models.CharField(max_length=16, blank=True)
    tax_label = models.CharField(max_length=64, blank=True)

    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    cess_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    # The GST split of this line's `tax_amount`. Stored rather than derived at
    # read time because GSTR-1 Table 12 reports taxable value and each
    # component HSN-wise, and re-deriving them in the report would put a second
    # copy of the rounding policy there. The invariant
    # `cgst + sgst + igst + cess == tax_amount` is enforced by a check
    # constraint below and guaranteed by tax.services.computation.split_tax.
    cgst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    sgst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    igst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    cess_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    line_base = models.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2)
    taxable_amount = models.DecimalField(max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2)
    line_total = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="bill_line_quantity_positive"),
            # The component invariant: a line either carries NO GST split
            # (legacy rows and documents with no determined supply nature) or
            # its components sum EXACTLY to its own tax_amount. Anything
            # between the two is a line whose journal cannot balance, so it is
            # refused by the database rather than caught downstream.
            models.CheckConstraint(
                condition=(
                    models.Q(cgst_amount=0, sgst_amount=0, igst_amount=0, cess_amount=0)
                    | models.Q(
                        tax_amount=models.F("cgst_amount")
                        + models.F("sgst_amount")
                        + models.F("igst_amount")
                        + models.F("cess_amount")
                    )
                ),
                name="bill_line_tax_components_sum_to_tax_amount",
            ),
        ]
        # Deliberately NO unique constraint on `source_goods_receipt_line`.
        # It looks like the natural structural double-billing guard, but it
        # is wrong twice over: one receipt line of 10 units may legitimately
        # be billed across two bills of 5, and a uniqueness rule would also
        # permanently burn a receipt line whose only bill was later VOIDED,
        # leaving genuinely received goods unbillable forever. The guard is
        # instead a derived quantity check over non-void bills —
        # selectors.py::get_billed_quantity_for_receipt_line — the same
        # derive-don't-store approach as sales' over-fulfillment check.
        indexes = [
            models.Index(fields=["bill", "line_number"]),
            models.Index(fields=["source_order_line"]),
            models.Index(fields=["source_goods_receipt_line"]),
        ]
        ordering = ["bill", "line_number"]

    def __str__(self):
        return f"{self.bill_id}#{self.line_number}"

    def _parent_status(self):
        return Bill.all_objects.filter(pk=self.bill_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != BillStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a bill that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != BillStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a bill that has left DRAFT.")
        super().delete(*args, **kwargs)
