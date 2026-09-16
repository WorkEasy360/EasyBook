from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel
from tax.enums import SupplyNature, SupplyType


class ExpenseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    VOID = "void", "Void"


class Expense(TenantScopedModel):
    """A direct cost with no item, no stock and usually no purchase order —
    taxi fares, utility bills, software subscriptions.

    Deliberately NOT a Bill with one line. A Bill models a vendor's demand
    for payment sitting in Accounts Payable until settled; an Expense is
    normally already settled at the moment it is recorded (paid by card or
    cash), so its credit side is a cash/bank account, not AP. Forcing both
    through one model would mean a Bill whose payable_account is sometimes
    a bank account — exactly the kind of overloaded field that makes an AP
    ageing report silently wrong.

    `is_billable` marks an expense to be on-charged to a customer. Phase 5
    (Projects) owns the actual re-invoicing; this phase only records the
    intent and the customer it is attributable to, so no re-billing logic
    is invented here ahead of the module that owns it.
    """

    vendor = models.ForeignKey(
        "purchases.Vendor", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses"
    )
    expense_number = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=ExpenseStatus.choices, default=ExpenseStatus.DRAFT)

    expense_date = models.DateField()
    reference = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=255, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("1"))

    # The expense category. Must be an EXPENSE account — validated by the
    # service, not assumed.
    expense_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    # What funded it. A bank/cash ASSET account for an already-paid expense,
    # or an AP LIABILITY account for one to be settled later — the service
    # accepts either and validates accordingly, which is why this field is
    # named for its role in the journal rather than for a payment method.
    paid_through_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    tax_recoverable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    accounting_journal = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

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

    # Server-computed from amount/tax_rate at post time — the client never
    # supplies `total` (root CLAUDE.md: never trust frontend totals).
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    cess_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    # An Expense is its own line - it has no line model - so the component
    # split lives on the header alongside the amounts it splits.
    cgst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    sgst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    igst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    cess_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    is_billable = models.BooleanField(default=False)
    customer = models.ForeignKey(
        "sales.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="billable_expenses"
    )
    # Attributes this cost to a project for profitability (Phase 5). A string
    # reference, so `purchases` never imports `projects` — the dependency is
    # database-level only, and `projects.selectors` reads Expense rather than
    # `purchases` reaching into projects. Reusing Expense here, rather than
    # inventing a parallel ProjectExpense, keeps one definition of "a cost we
    # incurred" and one place it posts from (root CLAUDE.md: reuse existing
    # abstractions).
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses"
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
                fields=["organization", "expense_number"], name="uniq_expense_number_per_org",
                condition=~models.Q(expense_number=""),
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="expense_amount_positive"),
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
                name="expense_tax_components_sum_to_tax_amount",
            ),
            # A billable expense must say who it is billable to — otherwise
            # Phase 5 inherits rows it cannot act on.
            models.CheckConstraint(
                condition=models.Q(is_billable=False) | models.Q(customer__isnull=False),
                name="billable_expense_requires_customer",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "expense_date"]),
            models.Index(fields=["organization", "vendor"]),
            models.Index(fields=["organization", "is_billable", "customer"]),
        ]
        ordering = ["-expense_date", "-created_at"]

    def __str__(self):
        return self.expense_number or f"draft:{self.id}"

    _MUTABLE_AFTER_DRAFT_FIELDS = {
        "status", "expense_number", "posted_by", "posted_by_id", "posted_at", "accounting_journal",
        "accounting_journal_id", "voided_by", "voided_by_id", "voided_at", "void_reason", "updated_at",
    }

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = Expense.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != ExpenseStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError("Posted expenses are immutable except through expense services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != ExpenseStatus.DRAFT:
            raise ValueError("Only draft expenses can be deleted.")
        super().delete(*args, **kwargs)
