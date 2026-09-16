from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    PARTIALLY_PAID = "partially_paid", "Partially Paid"
    PAID = "paid", "Paid"
    # Not actively transitioned into by any service yet — no scheduled sweep
    # exists in this phase (only recurring-invoice generation is Celery-backed,
    # a separate concern). "Overdue" is computed on demand by
    # selectors.get_overdue_invoices() instead. Same deferred-choice pattern
    # as QuoteStatus.EXPIRED / items.ItemType.COMPOSITE.
    OVERDUE = "overdue", "Overdue"
    VOID = "void", "Void"


class Invoice(TenantScopedModel):
    """The authoritative, invoiced sale. Unlike Quote/SalesOrder, an Invoice
    IS a financial record: posting (DRAFT -> SENT) allocates its number,
    posts the accounting journal, and issues stock where not already issued
    by a Delivery Challan — all in one atomic transaction. See
    sales/CLAUDE.md and services/invoices.py.
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="invoices")
    invoice_number = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT)

    # A quote may convert to at most one invoice — mirrors
    # SalesOrder.source_quote / JournalEntry.reverses.
    source_quote = models.OneToOneField(
        "sales.Quote", null=True, blank=True, on_delete=models.PROTECT, related_name="invoice"
    )

    invoice_date = models.DateField()
    due_date = models.DateField()
    reference = models.CharField(max_length=255, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    # Explicit, not defaulted — same "no implicit account is invented"
    # principle as inventory.StockAdjustment.contra_account. There is no
    # "is_ar"/"is_tax_payable" flag on accounting.Account to auto-detect
    # these from, and inventing one would be a compliance/chart-of-accounts
    # decision outside this phase's scope.
    receivable_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="+"
    )
    # Only required (validated at post time) if tax_total > 0.
    tax_payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Only required (validated at post time) if a line needs stock issued
    # directly (no source_delivery_challan_line) — see services/invoices.py.
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
    # amount_paid/amount_due are deliberately NOT columns here — see
    # sales/CLAUDE.md and selectors.py::get_invoice_amount_paid. Always
    # derived from PaymentAllocation, same never-cache-a-cross-document-sum
    # principle as SalesOrder fulfillment / inventory stock-on-hand / GL.

    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)

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
                fields=["organization", "invoice_number"], name="uniq_invoice_number_per_org",
                condition=~models.Q(invoice_number=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "invoice_date"]),
            models.Index(fields=["organization", "customer"]),
            models.Index(fields=["organization", "due_date"]),
            models.Index(fields=["customer", "status"]),
        ]
        ordering = ["-invoice_date", "-created_at"]

    def __str__(self):
        return self.invoice_number or f"draft:{self.id}"

    # Fields the posting/void/payment services are allowed to touch on an
    # invoice that has left DRAFT — everything else (lines, dates, accounts,
    # amounts) is frozen once sent. Mirrors accounting.JournalEntry exactly.
    _MUTABLE_AFTER_DRAFT_FIELDS = {
        "status", "invoice_number", "posted_by", "posted_by_id", "posted_at", "accounting_journal",
        "accounting_journal_id", "voided_by", "voided_by_id", "voided_at", "void_reason", "updated_at",
    }

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = Invoice.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != InvoiceStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError("Posted invoices are immutable except through invoice services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != InvoiceStatus.DRAFT:
            raise ValueError("Only draft invoices can be deleted.")
        super().delete(*args, **kwargs)


class InvoiceLine(TenantScopedModel):
    """Snapshot at invoice time — same principle as QuoteLine/SalesOrderLine.
    `source_delivery_challan_line`, if set, means stock for this line was
    already issued by that challan — invoice posting must NOT issue it again
    (see services/invoices.py and sales/CLAUDE.md)."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    source_delivery_challan_line = models.ForeignKey(
        "sales.DeliveryChallanLine", null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines"
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
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="invoice_line_quantity_positive"),
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
                name="invoice_line_tax_components_sum_to_tax_amount",
            ),
        ]
        indexes = [
            models.Index(fields=["invoice", "line_number"]),
        ]
        ordering = ["invoice", "line_number"]

    def __str__(self):
        return f"{self.invoice_id}#{self.line_number}"

    def _parent_status(self):
        return Invoice.all_objects.filter(pk=self.invoice_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != InvoiceStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to an invoice that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != InvoiceStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to an invoice that has left DRAFT.")
        super().delete(*args, **kwargs)
