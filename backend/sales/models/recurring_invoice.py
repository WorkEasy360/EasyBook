from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class RecurringFrequency(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"


class RecurringInvoiceTemplate(TenantScopedModel):
    """A schedule for generating DRAFT invoices. Generation is a Celery task
    (services/recurring_invoices.py + tasks.py), never inline in a web
    request (root CLAUDE.md). Generated invoices are always DRAFT — a human
    still reviews and calls post_invoice; this template never posts
    accounting/stock on its own (see sales/CLAUDE.md).
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="recurring_invoice_templates")
    frequency = models.CharField(max_length=16, choices=RecurringFrequency.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    # The next occurrence date to generate for — advanced by
    # services/recurring_invoices.py after each successful generation.
    # Initialized to start_date at creation.
    next_run_at = models.DateField()
    is_active = models.BooleanField(default=True)
    due_days = models.PositiveIntegerField(default=0)

    receivable_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    tax_payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_active", "next_run_at"]),
            models.Index(fields=["organization", "customer"]),
        ]
        ordering = ["next_run_at"]

    def __str__(self):
        return f"{self.customer_id}:{self.frequency}"


class RecurringInvoiceTemplateLine(TenantScopedModel):
    template = models.ForeignKey(RecurringInvoiceTemplate, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    line_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="recurring_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["template", "line_number"]),
        ]
        ordering = ["template", "line_number"]

    def __str__(self):
        return f"{self.template_id}#{self.line_number}"


class RecurringInvoiceRun(TenantScopedModel):
    """Append-only idempotency ledger: one row per (template, occurrence),
    enforced by a unique constraint — the same scheduled occurrence can
    never generate two invoices, even under concurrent/retried task
    execution (root CLAUDE.md: generation must be idempotent)."""

    template = models.ForeignKey(RecurringInvoiceTemplate, on_delete=models.PROTECT, related_name="runs")
    occurrence_date = models.DateField()
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["template", "occurrence_date"], name="uniq_recurring_run_per_occurrence"),
        ]
        indexes = [
            models.Index(fields=["organization", "template"]),
        ]

    def __str__(self):
        return f"{self.template_id}@{self.occurrence_date}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Recurring invoice runs are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Recurring invoice runs are append-only and cannot be deleted.")
