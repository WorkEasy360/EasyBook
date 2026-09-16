"""The vocabulary of Indian GST, in one place.

These are `TextChoices` rather than free strings because every one of them
either drives a branch in `services/determination.py` or is written verbatim
into a government payload (`compliance/services/einvoice_payload.py`), where a
typo is a rejected filing rather than a cosmetic bug.

`SupplyType` in particular is NOT ours to extend: its members are the exact
`TranDtls.SupTyp` values the NIC e-Invoice schema accepts (verified against
einv-apisandbox.nic.in, 2026-09-15 - see docs/gst-research.md). Adding a member
here that the IRP does not know is how you ship an invoice that cannot be
reported.
"""

from django.db import models


class SupplyType(models.TextChoices):
    """`TranDtls.SupTyp` in the e-Invoice schema. UNSPECIFIED is ours, not
    NIC's: it is the state every document created before this phase is in, and
    the one value that must never reach a payload builder."""

    UNSPECIFIED = "unspecified", "Not specified"
    B2B = "B2B", "Business to business"
    SEZWP = "SEZWP", "SEZ with payment of tax"
    SEZWOP = "SEZWOP", "SEZ without payment of tax"
    EXPWP = "EXPWP", "Export with payment of tax"
    EXPWOP = "EXPWOP", "Export without payment of tax"
    DEXP = "DEXP", "Deemed export"


class SupplyNature(models.TextChoices):
    """The computed answer to "which tax applies", derived from IGST Act
    sections 7 and 8. This is what `computation.split_tax` branches on - never
    the raw state codes, so the statute is interpreted in exactly one place."""

    UNSPECIFIED = "unspecified", "Not specified"
    INTRA_STATE = "intra_state", "Intra-State (CGST + SGST/UTGST)"
    INTER_STATE = "inter_state", "Inter-State (IGST)"
    EXPORT_WITH_TAX = "export_with_tax", "Export with payment of tax (IGST)"
    EXPORT_WITHOUT_TAX = "export_without_tax", "Export under LUT/bond (zero-rated)"
    SEZ_WITH_TAX = "sez_with_tax", "SEZ supply with payment of tax (IGST)"
    SEZ_WITHOUT_TAX = "sez_without_tax", "SEZ supply under LUT/bond (zero-rated)"
    DEEMED_EXPORT = "deemed_export", "Deemed export"


class TaxTreatment(models.TextChoices):
    """What a counterparty IS, for tax purposes. Set on Customer/Vendor; an
    input to determination, never an output of it."""

    REGISTERED = "registered", "Registered business"
    UNREGISTERED = "unregistered", "Unregistered business"
    CONSUMER = "consumer", "Consumer (B2C)"
    OVERSEAS = "overseas", "Overseas"
    SEZ = "sez", "SEZ unit or developer"
    DEEMED_EXPORT = "deemed_export", "Deemed export"


class TaxComponent(models.TextChoices):
    """SGST and UTGST are ONE member, not two. Which label a given supply wears
    is a property of the destination state (`StateCode.uses_utgst`), not a
    separate tax: the rate, the base and the ledger treatment are identical, and
    splitting them would mean every caller carrying a branch that changes
    nothing but a string."""

    CGST = "cgst", "Central GST"
    SGST_UTGST = "sgst_utgst", "State / Union Territory GST"
    IGST = "igst", "Integrated GST"
    CESS = "cess", "Compensation cess"
    TDS = "tds", "Tax deducted at source"
    TCS = "tcs", "Tax collected at source"


class TaxDirection(models.TextChoices):
    """OUTPUT tax is a liability (we owe it), INPUT tax an asset (we reclaim
    it). RCM_PAYABLE is the liability leg of a reverse-charge purchase, where
    the same transaction creates both an input asset and an output liability -
    see tax/CLAUDE.md."""

    OUTPUT = "output", "Output tax (liability)"
    INPUT = "input", "Input tax (asset)"
    RCM_PAYABLE = "rcm_payable", "Reverse-charge liability"


class RegistrationType(models.TextChoices):
    REGULAR = "regular", "Regular"
    COMPOSITION = "composition", "Composition scheme"
    UNREGISTERED = "unregistered", "Unregistered"
    SEZ_UNIT = "sez_unit", "SEZ unit"
    SEZ_DEVELOPER = "sez_developer", "SEZ developer"
    INPUT_SERVICE_DISTRIBUTOR = "isd", "Input service distributor"
    NON_RESIDENT = "non_resident", "Non-resident taxable person"
    UIN = "uin", "UIN holder"


class WithholdingKind(models.TextChoices):
    TDS = "tds", "Tax deducted at source"
    TCS = "tcs", "Tax collected at source"


class WithholdingAppliesTo(models.TextChoices):
    PURCHASE = "purchase", "Purchases (we deduct)"
    SALES = "sales", "Sales (we collect)"
