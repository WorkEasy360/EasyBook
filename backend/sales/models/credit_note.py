from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class CreditNoteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    VOID = "void", "Void"


class CreditNoteReason(models.TextChoices):
    RETURN = "return", "Return"
    PRICING_ERROR = "pricing_error", "Pricing Error"
    DISCOUNT = "discount", "Discount"
    GOODWILL = "goodwill", "Goodwill"
    OTHER = "other", "Other"


class CreditNote(TenantScopedModel):
    """A financial correction against a customer — either reducing a specific
    Invoice's balance (`source_invoice` set) or a standalone customer credit
    (`source_invoice` null, e.g. goodwill). Never mutates the original
    invoice — see sales/CLAUDE.md snapshot/immutability principles.

    Numbered at ISSUE time (not creation), like Invoice — it's an
    authoritative financial record only once issued.
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="credit_notes")
    credit_note_number = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=CreditNoteStatus.choices, default=CreditNoteStatus.DRAFT)
    reason = models.CharField(max_length=16, choices=CreditNoteReason.choices, default=CreditNoteReason.OTHER)

    # Not a OneToOneField, unlike Quote->SalesOrder/Invoice — one invoice may
    # receive several partial credit notes over time.
    source_invoice = models.ForeignKey(
        "sales.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="credit_notes"
    )

    credit_note_date = models.DateField()
    reference = models.CharField(max_length=255, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    # Only used when NOT linked to source_invoice (there Cr AR is derived
    # from source_invoice.receivable_account instead) — see services/credit_notes.py.
    receivable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Required at issue time if tax_total > 0 and not inherited from source_invoice.
    tax_payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Required at issue time whenever any amount can't net against the
    # source invoice's remaining balance (root CLAUDE.md §27 pattern reused
    # here: excess becomes customer credit, never a negative AR balance).
    unapplied_credit_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Required at issue time if any line has restock=True.
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
    # Set once, at issue time, by services/invoices.py::issue_credit_note —
    # the split between "netted against source_invoice's remaining balance"
    # and "unapplied customer credit" is a historical decision fixed at that
    # moment (get_invoice_amount_due at issue time), not something safe to
    # recompute later the way Invoice.amount_paid/amount_due are (those stay
    # live because nothing else freezes their inputs). See selectors.py.
    amount_applied_to_invoice = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

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
                fields=["organization", "credit_note_number"], name="uniq_credit_note_number_per_org",
                condition=~models.Q(credit_note_number=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "credit_note_date"]),
            models.Index(fields=["organization", "customer"]),
            models.Index(fields=["organization", "source_invoice"]),
        ]
        ordering = ["-credit_note_date", "-created_at"]

    def __str__(self):
        return self.credit_note_number or f"draft:{self.id}"

    _MUTABLE_AFTER_DRAFT_FIELDS = {
        "status", "credit_note_number", "issued_by", "issued_by_id", "issued_at", "accounting_journal",
        "accounting_journal_id", "amount_applied_to_invoice", "voided_by", "voided_by_id", "voided_at", "updated_at",
    }

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = CreditNote.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != CreditNoteStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError("Issued credit notes are immutable except through credit note services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != CreditNoteStatus.DRAFT:
            raise ValueError("Only draft credit notes can be deleted.")
        super().delete(*args, **kwargs)


class CreditNoteLine(TenantScopedModel):
    """Snapshot at credit-note time — same principle as InvoiceLine.
    `restock=True` is the only trigger for a stock movement (root CLAUDE.md:
    never restock automatically) — always False for SERVICE items, enforced
    in services/credit_notes.py."""

    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    source_invoice_line = models.ForeignKey(
        "sales.InvoiceLine", null=True, blank=True, on_delete=models.PROTECT, related_name="credit_note_lines"
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

    # Physical-return fields — only meaningful (and only usable) when
    # restock=True on a PRODUCT/track_inventory item.
    restock = models.BooleanField(default=False)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="credit_note_line_quantity_positive"),
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
                name="credit_note_line_tax_components_sum_to_tax_amount",
            ),
        ]
        indexes = [
            models.Index(fields=["credit_note", "line_number"]),
        ]
        ordering = ["credit_note", "line_number"]

    def __str__(self):
        return f"{self.credit_note_id}#{self.line_number}"

    def _parent_status(self):
        return CreditNote.all_objects.filter(pk=self.credit_note_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != CreditNoteStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a credit note that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != CreditNoteStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a credit note that has left DRAFT.")
        super().delete(*args, **kwargs)
