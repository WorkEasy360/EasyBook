from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel


class TaxRate(TenantScopedModel):
    """A named GST rate an organization actually uses, e.g. "GST 18%".

    `rate` is the COMBINED rate, not a component: 18 means 18%, which becomes
    CGST 9 + SGST 9 on an intra-State supply or IGST 18 on an inter-State one.
    Storing the halves would mean storing a fact that is already derivable and
    could therefore disagree with itself.

    No slab whitelist. The slabs were 0/5/12/18/28 until 22 September 2025 and
    are 0/5/18 plus a 40% demerit rate after it; a `choices` list or a check
    constraint naming today's slabs would have to be migrated by every future
    rate notification, and would reject correct historical data in the meantime.

    Effective dating exists for exactly that reason: an organization needs "the
    rate for this item on this date", and the answer changed twice in the last
    year. Posted documents snapshot their rate anyway (see the `tax_rate`
    column on every line model), so this window only ever drives DEFAULTING -
    it can never retroactively alter a posted journal.
    """

    name = models.CharField(max_length=64)
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    cess_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    is_active = models.BooleanField(default=True)

    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="uniq_tax_rate_name_per_org"),
            models.CheckConstraint(
                condition=models.Q(rate__gte=Decimal("0")), name="tax_rate_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(cess_rate__gte=Decimal("0")), name="tax_rate_cess_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_from__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="tax_rate_effective_window_ordered",
            ),
        ]
        indexes = [models.Index(fields=["organization", "is_active"])]
        ordering = ["rate", "name"]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"

    def is_effective_on(self, on_date) -> bool:
        if self.effective_from and on_date < self.effective_from:
            return False
        if self.effective_to and on_date > self.effective_to:
            return False
        return True
