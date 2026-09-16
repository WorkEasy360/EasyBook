from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel
from tax.enums import TaxTreatment


class Customer(TenantScopedModel):
    """Sales-side customer master data. Deliberately not a shared CRM
    Contact/Company entity — no such reusable model exists yet elsewhere in
    the codebase (see sales/CLAUDE.md).

    Inactive customers are rejected for NEW transactions by each downstream
    sales service (quotes/orders/invoices) at creation time — this model
    only carries the flag, it does not enforce that rule itself.
    """

    customer_code = models.CharField(max_length=32)
    display_name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    # Validated from Phase 7 onward by `tax.services.validation.validate_gstin`,
    # applied in `sales/services/customers.py` — structure and check digit only,
    # never a claim that the registration exists or is active (only the GST
    # portal can say that, and this codebase has no credentials for it).
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)

    # What this customer IS for tax purposes, and where supplies to them land.
    # Both are INPUTS to `tax.services.determination.determine_supply_nature`,
    # never outputs of it: `tax_treatment` is what makes an SEZ customer attract
    # IGST even in the supplier's own state.
    tax_treatment = models.CharField(
        max_length=20, choices=TaxTreatment.choices, default=TaxTreatment.UNREGISTERED
    )
    # The default place of supply for this customer. A DEFAULT, not a rule:
    # every document carries its own `place_of_supply`, because the statutory
    # answer (IGST Act ss.10-13) turns on facts about the individual supply
    # that master data cannot know. See tax/services/determination.py.
    place_of_supply_state = models.ForeignKey(
        "tax.StateCode", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    # Still a JSON blob: place of supply is now a typed field above, so the
    # address does not need to carry tax meaning and no schema is being
    # invented for it prematurely.
    billing_address = models.JSONField(default=dict, blank=True)
    shipping_address = models.JSONField(default=dict, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    payment_terms_days = models.PositiveIntegerField(default=0)  # 0 = due on receipt
    credit_limit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "customer_code"], name="uniq_customer_code_per_org"),
            models.CheckConstraint(
                condition=models.Q(credit_limit__isnull=True) | models.Q(credit_limit__gte=Decimal("0")),
                name="customer_credit_limit_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"]),
            models.Index(fields=["organization", "display_name"]),
        ]
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name
