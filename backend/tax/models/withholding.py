from decimal import Decimal

from django.db import models

from core.models import TenantScopedModel
from tax.enums import WithholdingAppliesTo, WithholdingKind


class WithholdingSection(TenantScopedModel):
    """A TDS or TCS section an organization operates under, e.g. 194Q or
    206C(1H).

    Withholding is not GST: it is income-tax collected or deducted alongside a
    trade transaction, which is why it is a separate model with its own account
    rather than another `TaxComponent` rate. The two behave differently in the
    ledger - GST sits on top of the invoice value, withholding is taken OUT of
    the settlement - and conflating them would make the AR/AP arithmetic wrong.

    `rate` and `threshold_amount` are organization data, deliberately not
    seeded. Section rates change by Finance Act and the thresholds are
    per-payee and cumulative across a financial year; shipping a table of them
    would be inventing compliance rules (root CLAUDE.md rule 5) and would go
    stale every April. `threshold_amount` is recorded here for reference and
    reporting - this phase does not itself track cumulative payee turnover to
    decide when the threshold is crossed, which is a stateful judgement the
    organization makes.
    """

    code = models.CharField(max_length=16)
    name = models.CharField(max_length=128, blank=True)
    kind = models.CharField(max_length=8, choices=WithholdingKind.choices)
    applies_to = models.CharField(max_length=16, choices=WithholdingAppliesTo.choices)
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    threshold_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0"),
        help_text="Reference only - this phase does not track cumulative payee turnover.",
    )
    account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uniq_withholding_section_per_org"
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gte=Decimal("0")) & models.Q(rate__lte=Decimal("100")),
                name="withholding_rate_between_0_and_100",
            ),
            models.CheckConstraint(
                condition=models.Q(threshold_amount__gte=Decimal("0")),
                name="withholding_threshold_nonnegative",
            ),
        ]
        indexes = [models.Index(fields=["organization", "is_active"])]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.rate}%)"
