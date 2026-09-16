from django.db import models

from core.models import TenantScopedModel
from tax.enums import TaxTreatment


class Vendor(TenantScopedModel):
    """Purchase-side supplier master data. The buy-side mirror of
    `sales.Customer` — deliberately a separate model rather than a shared
    "Contact" entity with a role flag: a vendor and a customer diverge on
    defaults (payable vs receivable account, purchase vs sales terms) and
    nothing in the codebase needs one row to be both (see purchases/CLAUDE.md).

    Inactive vendors are rejected for NEW transactions by each downstream
    purchase service at creation time — this model only carries the flag.
    """

    vendor_code = models.CharField(max_length=32)
    display_name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    # Validated from Phase 7 onward by `tax.services.validation.validate_gstin`,
    # applied in `purchases/services/vendors.py` — structure and check digit
    # only, never a claim that the registration exists or is active.
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)

    # Inputs to `tax.services.determination.determine_supply_nature`, mirroring
    # sales.Customer. On the buy side `tax_treatment` also carries the
    # reverse-charge signal: an UNREGISTERED vendor is the s.5(4) case where the
    # liability moves to us. The flag still lives per-BILL rather than here,
    # because whether a given supply is notified under s.5(3) is a property of
    # what was supplied, not of who supplied it.
    tax_treatment = models.CharField(
        max_length=20, choices=TaxTreatment.choices, default=TaxTreatment.UNREGISTERED
    )
    place_of_supply_state = models.ForeignKey(
        "tax.StateCode", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    billing_address = models.JSONField(default=dict, blank=True)
    shipping_address = models.JSONField(default=dict, blank=True)

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    payment_terms_days = models.PositiveIntegerField(default=0)  # 0 = due on receipt

    # The vendor's default AP account. Nullable and never silently applied —
    # every Bill still carries its own explicit `payable_account` (same "no
    # implicit account is invented" principle as sales.Invoice). This is a
    # convenience default a caller may read, not something a service reaches
    # for on its own.
    default_payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "vendor_code"], name="uniq_vendor_code_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"]),
            models.Index(fields=["organization", "display_name"]),
        ]
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

# Deliberately no `credit_limit` field (unlike sales.Customer): a credit limit
# is something WE extend to a customer, not something we track against a
# supplier. Vendor-side exposure limits are a Phase 12 concern if ever needed.
