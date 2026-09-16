from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class VendorCreditStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    VOID = "void", "Void"


class VendorCreditReason(models.TextChoices):
    RETURN = "return", "Return"
    PRICING_ERROR = "pricing_error", "Pricing Error"
    DISCOUNT = "discount", "Discount"
    DAMAGED = "damaged", "Damaged Goods"
    OTHER = "other", "Other"


class VendorCredit(TenantScopedModel):
    """A financial correction in our favour from a vendor — the mirror of
    `sales.CreditNote`. Either reduces a specific Bill's balance
    (`source_bill` set) or stands alone as vendor credit we hold
    (`source_bill` null). Never mutates the original bill.

    Numbered at ISSUE time, like Bill and CreditNote.

    Unlike the sales side, `VendorCreditReason` includes DAMAGED: goods
    arriving damaged is a purchase-side event with no sales-side mirror
    (we would issue a credit note for a return, but the damage happened to
    someone else's shipment).
    """

    vendor = models.ForeignKey("purchases.Vendor", on_delete=models.PROTECT, related_name="vendor_credits")
    credit_number = models.CharField(max_length=32, blank=True)
    vendor_credit_number = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=VendorCreditStatus.choices, default=VendorCreditStatus.DRAFT)
    reason = models.CharField(max_length=16, choices=VendorCreditReason.choices, default=VendorCreditReason.OTHER)

    # Plain FK, not OneToOne — one bill may receive several partial credits.
    source_bill = models.ForeignKey(
        "purchases.Bill", null=True, blank=True, on_delete=models.PROTECT, related_name="vendor_credits"
    )

    credit_date = models.DateField()
    reference = models.CharField(max_length=255, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    # Only used when NOT linked to source_bill (there Dr AP is derived from
    # source_bill.payable_account instead) — see services/vendor_credits.py.
    payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Required at issue time if tax_total > 0 and not inherited from
    # source_bill. Reverses input tax previously claimed.
    tax_recoverable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Required at issue time whenever any amount can't net against the
    # source bill's remaining balance — the excess is credit we hold with
    # the vendor (an ASSET), the exact mirror of sales' unapplied customer
    # credit being a LIABILITY.
    unapplied_credit_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Required at issue time if any line has return_stock=True.
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
    # Frozen at issue time — a historical decision, not a live balance.
    # Same rationale as sales.CreditNote.amount_applied_to_invoice.
    amount_applied_to_bill = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "credit_number"], name="uniq_vendor_credit_number_per_org",
                condition=~models.Q(credit_number=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "credit_date"]),
            models.Index(fields=["organization", "vendor"]),
            models.Index(fields=["organization", "source_bill"]),
        ]
        ordering = ["-credit_date", "-created_at"]

    def __str__(self):
        return self.credit_number or f"draft:{self.id}"

    _MUTABLE_AFTER_DRAFT_FIELDS = {
        "status", "credit_number", "issued_by", "issued_by_id", "issued_at", "accounting_journal",
        "accounting_journal_id", "amount_applied_to_bill", "voided_by", "voided_by_id", "voided_at", "updated_at",
    }

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = VendorCredit.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != VendorCreditStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError("Issued vendor credits are immutable except through vendor credit services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != VendorCreditStatus.DRAFT:
            raise ValueError("Only draft vendor credits can be deleted.")
        super().delete(*args, **kwargs)


class VendorCreditLine(TenantScopedModel):
    """Snapshot at credit time — same principle as BillLine.

    `return_stock=True` is the ONLY trigger for a stock movement (the mirror
    of sales.CreditNoteLine.restock): a pricing-error credit must not
    silently move goods that never left the warehouse. Rejected outright for
    a SERVICE item rather than silently ignored.
    """

    vendor_credit = models.ForeignKey(VendorCredit, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    source_bill_line = models.ForeignKey(
        "purchases.BillLine", null=True, blank=True, on_delete=models.PROTECT, related_name="vendor_credit_lines"
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

    # Physical-return fields — only meaningful when return_stock=True on a
    # PRODUCT/track_inventory item.
    return_stock = models.BooleanField(default=False)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="vendor_credit_line_quantity_positive"),
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
                name="vendor_credit_line_tax_components_sum_to_tax_amount",
            ),
        ]
        indexes = [
            models.Index(fields=["vendor_credit", "line_number"]),
        ]
        ordering = ["vendor_credit", "line_number"]

    def __str__(self):
        return f"{self.vendor_credit_id}#{self.line_number}"

    def _parent_status(self):
        return VendorCredit.all_objects.filter(pk=self.vendor_credit_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != VendorCreditStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a vendor credit that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != VendorCreditStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a vendor credit that has left DRAFT.")
        super().delete(*args, **kwargs)
