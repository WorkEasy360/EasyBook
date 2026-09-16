from django.db import models


class StateCode(models.Model):
    """The GST state/UT code master - GLOBAL reference data, not org-owned.

    Modelled like `accounts.Currency` (plain `models.Model`, natural primary
    key, no `organization` column, and therefore deliberately NO entry in
    `migrations/0002_enable_rls.py`): the code for Maharashtra is 27 for every
    tenant in the system, so scoping it per organization would mean 38 rows per
    tenant that can never legitimately differ.

    This is the ONE piece of compliance reference data this phase seeds. The
    rest - rate slabs, the HSN catalog, e-invoice turnover thresholds, TDS/TCS
    rates - is left for organizations to enter, following the precedent set by
    `items.HsnSacCode` and root CLAUDE.md rule 5. The state list earns its
    exception because place-of-supply determination is arithmetic on these
    codes: without them `services/determination.py` cannot run at all.

    Source: NIC e-Invoice master codes (einvoice1.gst.gov.in/Others/MasterCodes),
    checked 2026-09-15. See docs/gst-research.md.
    """

    code = models.CharField(max_length=2, primary_key=True)
    name = models.CharField(max_length=64)
    is_union_territory = models.BooleanField(default=False)

    # Whether the state half of an intra-State supply is called UTGST rather
    # than SGST. True only for Union Territories WITHOUT a legislature.
    # Delhi, Puducherry and Jammu & Kashmir have legislatures and levy SGST,
    # which is why this is a per-row fact and not `is_union_territory`.
    uses_utgst = models.BooleanField(default=False)

    # 96/97/99 in the NIC master are not places: they are bucket codes used for
    # "Other Country" / "Other Territory" reporting. They must never be treated
    # as a supplier's own state - see `determination.determine_supply_nature`.
    is_special = models.BooleanField(default=False)

    class Meta:
        ordering = ["code"]
        verbose_name = "GST state code"
        verbose_name_plural = "GST state codes"

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def state_component_label(self) -> str:
        """"UTGST" or "SGST" - display only. The ledger treatment is identical
        (see `tax.enums.TaxComponent.SGST_UTGST`)."""
        return "UTGST" if self.uses_utgst else "SGST"
