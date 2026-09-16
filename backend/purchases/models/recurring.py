"""Recurring bills and recurring expenses.

Both live in one module because they share a schedule shape and one Celery
entry point (services/recurring.py). `RecurringFrequency` comes from
core.enums, shared with sales — "monthly" means the same thing on both sides.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from core.enums import RecurringFrequency
from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class RecurringBillTemplate(TenantScopedModel):
    """A schedule for generating DRAFT bills — the mirror of
    `sales.RecurringInvoiceTemplate`. Generation is a Celery task
    (services/recurring.py + tasks.py), never inline in a web request.

    Generated bills are always DRAFT: a human reviews the vendor's actual
    figures and calls post_bill. This matters more on the buy side than the
    sell side — a recurring invoice restates a price WE set, whereas a
    recurring bill only anticipates what a vendor will charge, and the real
    bill routinely differs. The template never posts accounting or stock.

    Deliberately no `warehouse` / inventory handling: a recurring purchase
    is a subscription or retainer, not a goods delivery, and a generated
    DRAFT bill with a source_goods_receipt_line link would be meaningless
    (the goods receipt cannot exist before the goods arrive). A recurring
    template line is therefore expensed, never inventoried — enforced in
    services/recurring.py, not merely documented here.
    """

    vendor = models.ForeignKey("purchases.Vendor", on_delete=models.PROTECT, related_name="recurring_bill_templates")
    frequency = models.CharField(max_length=16, choices=RecurringFrequency.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    next_run_at = models.DateField()
    is_active = models.BooleanField(default=True)
    due_days = models.PositiveIntegerField(default=0)

    # --- GST treatment (Phase 7) ------------------------------------------
    # Chosen once per document. `place_of_supply` defaults from the party but
    # is the DOCUMENT's own field: the statutory answer (IGST Act ss.10-13)
    # turns on facts about the individual supply that master data cannot know,
    # and tax/services/determination.py deliberately does not infer it.
    place_of_supply = models.ForeignKey(
        "tax.StateCode", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    supply_nature = models.CharField(
        max_length=24, choices=SupplyNature.choices, default=SupplyNature.UNSPECIFIED
    )
    supply_type = models.CharField(
        max_length=16, choices=SupplyType.choices, default=SupplyType.UNSPECIFIED
    )
    is_reverse_charge = models.BooleanField(default=False)

    payable_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    tax_recoverable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_active", "next_run_at"]),
            models.Index(fields=["organization", "vendor"]),
        ]
        ordering = ["next_run_at"]

    def __str__(self):
        return f"{self.vendor_id}:{self.frequency}"


class RecurringBillTemplateLine(TenantScopedModel):
    template = models.ForeignKey(RecurringBillTemplate, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    expense_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    line_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="recurring_bill_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["template", "line_number"]),
        ]
        ordering = ["template", "line_number"]

    def __str__(self):
        return f"{self.template_id}#{self.line_number}"


class RecurringBillRun(TenantScopedModel):
    """Append-only idempotency ledger: one row per (template, occurrence),
    enforced by a unique constraint — the authoritative guard against
    generating the same occurrence twice under concurrent or retried task
    execution. Same design as sales.RecurringInvoiceRun."""

    template = models.ForeignKey(RecurringBillTemplate, on_delete=models.PROTECT, related_name="runs")
    occurrence_date = models.DateField()
    bill = models.ForeignKey("purchases.Bill", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["template", "occurrence_date"], name="uniq_recurring_bill_run_per_occurrence"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "template"]),
        ]

    def __str__(self):
        return f"{self.template_id}@{self.occurrence_date}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Recurring bill runs are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Recurring bill runs are append-only and cannot be deleted.")


class RecurringExpenseTemplate(TenantScopedModel):
    """A schedule for generating DRAFT expenses (rent, subscriptions).

    Separate from RecurringBillTemplate for the same reason Expense is
    separate from Bill: the generated document's credit side is a cash/bank
    account rather than AP, and an expense has no lines. Merging them would
    mean a bill template whose payable_account is sometimes a bank account.
    """

    vendor = models.ForeignKey(
        "purchases.Vendor", null=True, blank=True, on_delete=models.PROTECT,
        related_name="recurring_expense_templates",
    )
    frequency = models.CharField(max_length=16, choices=RecurringFrequency.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    next_run_at = models.DateField()
    is_active = models.BooleanField(default=True)

    # --- GST treatment (Phase 7) ------------------------------------------
    # Chosen once per document. `place_of_supply` defaults from the party but
    # is the DOCUMENT's own field: the statutory answer (IGST Act ss.10-13)
    # turns on facts about the individual supply that master data cannot know,
    # and tax/services/determination.py deliberately does not infer it.
    place_of_supply = models.ForeignKey(
        "tax.StateCode", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    supply_nature = models.CharField(
        max_length=24, choices=SupplyNature.choices, default=SupplyNature.UNSPECIFIED
    )
    supply_type = models.CharField(
        max_length=16, choices=SupplyType.choices, default=SupplyType.UNSPECIFIED
    )
    is_reverse_charge = models.BooleanField(default=False)

    expense_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    paid_through_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    tax_recoverable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    amount = models.DecimalField(max_digits=18, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="recurring_expense_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active", "next_run_at"]),
            models.Index(fields=["organization", "vendor"]),
        ]
        ordering = ["next_run_at"]

    def __str__(self):
        return f"{self.expense_account_id}:{self.frequency}"


class RecurringExpenseRun(TenantScopedModel):
    """Append-only idempotency ledger for RecurringExpenseTemplate — same
    design and guarantee as RecurringBillRun."""

    template = models.ForeignKey(RecurringExpenseTemplate, on_delete=models.PROTECT, related_name="runs")
    occurrence_date = models.DateField()
    expense = models.ForeignKey("purchases.Expense", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["template", "occurrence_date"], name="uniq_recurring_expense_run_per_occurrence"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "template"]),
        ]

    def __str__(self):
        return f"{self.template_id}@{self.occurrence_date}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Recurring expense runs are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Recurring expense runs are append-only and cannot be deleted.")
