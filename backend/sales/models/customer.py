from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel


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

    # Freeform metadata placeholders only — no GST/PAN validation or
    # compliance logic here (root CLAUDE.md: don't invent compliance rules).
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)

    # Structured address fields aren't required yet (no GST place-of-supply
    # logic exists in this phase); a JSON blob avoids inventing a schema
    # prematurely while still being queryable if a future phase needs it.
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
                check=models.Q(credit_limit__isnull=True) | models.Q(credit_limit__gte=Decimal("0")),
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
