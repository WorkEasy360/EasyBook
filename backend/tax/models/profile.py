from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel
from tax.enums import RegistrationType


class TaxProfile(TenantScopedModel):
    """One row per organization: who this tenant is to the tax authority.

    Lives here rather than as more columns on `accounts.Organization` for the
    same reason `inventory.InventorySettings` does - Phase 0 models are not
    reopened by later phases, and this row will keep growing as compliance
    surfaces are added.

    Every threshold on this model is CONFIGURATION, not a constant. The
    e-invoicing turnover threshold has moved four times since 2020 and the
    reporting window is 3 days for large taxpayers and 30 for the rest; the
    e-way bill threshold varies by state for intra-State movement. Hard-coding
    any of them would bake a notification into the source and make this file
    wrong on a date nobody controls (root CLAUDE.md rule 5). The defaults below
    are the all-India baseline; an organization overrides what applies to it.
    """

    registration_type = models.CharField(
        max_length=20, choices=RegistrationType.choices, default=RegistrationType.REGULAR
    )
    gstin = models.CharField(max_length=15, blank=True)

    # The supplier's own state - the left-hand side of every place-of-supply
    # comparison. Nullable because an organization may be configured before it
    # is registered; `determination.determine_supply_nature` fails closed on
    # None rather than guessing.
    state = models.ForeignKey(
        "tax.StateCode", on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )

    composition_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="Only meaningful when registration_type is COMPOSITION.",
    )

    einvoice_enabled = models.BooleanField(default=False)
    # 3 or 30 days depending on aggregate annual turnover (GSTN advisory dated
    # 2024-11-05, effective 2025-04-01). Null = no window enforced by us.
    einvoice_reporting_window_days = models.PositiveSmallIntegerField(null=True, blank=True)

    ewaybill_threshold = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("50000.00"),
        help_text="Consignment value at or above which an e-way bill is required.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization"], name="uniq_tax_profile_per_org"),
            models.CheckConstraint(
                condition=models.Q(composition_rate__gte=Decimal("0")),
                name="tax_profile_composition_rate_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(ewaybill_threshold__gte=Decimal("0")),
                name="tax_profile_ewaybill_threshold_nonnegative",
            ),
        ]

    def __str__(self):
        return f"TaxProfile({self.organization_id}: {self.gstin or 'unregistered'})"

    @property
    def is_registered(self) -> bool:
        return bool(self.gstin) and self.registration_type != RegistrationType.UNREGISTERED
