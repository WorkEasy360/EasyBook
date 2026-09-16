from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class QuoteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    # No automatic expiry job exists yet in this phase (only recurring-invoice
    # generation is Celery-backed per sales/CLAUDE.md) — EXPIRED is reachable
    # only by a future scheduled task, deliberately not built here. Mirrors
    # items.ItemType.COMPOSITE: the choice exists, the behavior doesn't yet.
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class Quote(TenantScopedModel):
    """A non-binding sales quote. Creates NO accounting entries and NO
    inventory movements — see sales/CLAUDE.md. Numbered at creation (unlike
    JournalEntry/Invoice, which number at posting) since a quote is never an
    authoritative financial record and the user needs a reference to quote
    it by immediately.
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="quotes")
    quote_number = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=QuoteStatus.choices, default=QuoteStatus.DRAFT)

    issue_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)

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

    # Recalculated server-side from lines on every create/replace — never
    # client-writable directly (root CLAUDE.md: never trust client totals).
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    discount_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "quote_number"], name="uniq_quote_number_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "issue_date"]),
            models.Index(fields=["organization", "customer"]),
        ]
        ordering = ["-issue_date", "-created_at"]

    def __str__(self):
        return self.quote_number

    # Once a quote leaves DRAFT it stops being an editable work-in-progress —
    # only status (and the timestamps Django auto-manages) may change,
    # mirroring accounting.JournalEntry's immutability guard so "state
    # transitions only through services" (sales/CLAUDE.md) is enforced at the
    # model layer too, not just by convention.
    _MUTABLE_AFTER_DRAFT_FIELDS = {"status", "updated_at"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = Quote.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != QuoteStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError("Quotes that have left DRAFT are immutable except through quote services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != QuoteStatus.DRAFT:
            raise ValueError("Only draft quotes can be deleted.")
        super().delete(*args, **kwargs)


class QuoteLine(TenantScopedModel):
    """Line fields are a snapshot at quote time (item name/description, unit,
    price, discount, tax) — see sales/CLAUDE.md snapshot principle. Rendering
    a quote must never fall back to the live Item for these values."""

    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    line_number = models.PositiveIntegerField()

    description = models.CharField(max_length=255, blank=True)
    hsn_sac_snapshot = models.CharField(max_length=16, blank=True)
    # Freeform label snapshot, mirrors items.Item.tax_category — no GST rate
    # table/compliance logic exists yet (root CLAUDE.md).
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
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="quote_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["quote", "line_number"]),
        ]
        ordering = ["quote", "line_number"]

    def __str__(self):
        return f"{self.quote_id}#{self.line_number}"

    def _parent_status(self):
        return Quote.all_objects.filter(pk=self.quote_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != QuoteStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a quote that has left DRAFT.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != QuoteStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a quote that has left DRAFT.")
        super().delete(*args, **kwargs)
