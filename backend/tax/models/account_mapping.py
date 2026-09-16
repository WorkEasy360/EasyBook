from django.db import models

from core.models import TenantScopedModel
from tax.enums import TaxComponent, TaxDirection


class TaxAccountMapping(TenantScopedModel):
    """Which GL account each (component, direction) pair posts to.

    A table rather than a dozen FK columns on `TaxProfile`, because the set of
    components is open at the edges (cess today, TDS and TCS alongside it, a
    seventh tomorrow) and a column-per-component profile would need a schema
    migration each time. It also makes the chart-of-accounts wiring queryable:
    "which accounts are tax accounts" is one filter, not a union of columns.

    Absence is meaningful and must stay cheap. When a mapping is missing, the
    posting services fall back to the document's own
    `tax_payable_account`/`tax_recoverable_account`, which is how every
    organization that predates this phase keeps posting exactly as it did. A
    NOT NULL column set per component would have forced those organizations to
    configure six accounts before they could post an invoice again.
    """

    component = models.CharField(max_length=16, choices=TaxComponent.choices)
    direction = models.CharField(max_length=16, choices=TaxDirection.choices)
    account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "component", "direction"],
                name="uniq_tax_account_mapping_per_org",
            ),
        ]
        indexes = [models.Index(fields=["organization", "direction"])]
        ordering = ["direction", "component"]

    def __str__(self):
        return f"{self.direction}:{self.component} -> {self.account_id}"
