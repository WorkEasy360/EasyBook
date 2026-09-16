"""Supply-nature determination: IGST Act sections 7 and 8, and nothing else.

This is the only file in the codebase that interprets the statute, and it
interprets exactly two sections:

  s.7  A supply is INTER-State where the location of the supplier and the place
       of supply are in two different States, two different Union territories,
       or a State and a Union territory.
  s.8  A supply is INTRA-State where they are in the same State or Union
       territory, EXCEPT - and this exception is the whole reason this function
       exists rather than a `==` in the caller - supplies to or by an SEZ
       developer or unit, goods imported until they cross the customs
       frontiers, and supplies to a tourist. Those remain inter-State and
       attract IGST even when both parties sit in one state.

Verified against the CBIC IGST Act text, 2026-09-15 (docs/gst-research.md).

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not derive the PLACE OF SUPPLY. Sections 10 to 13 are a clause tree
turning on facts this system does not hold and cannot infer - whether goods
moved, who directed the delivery, where an immovable property sits, where a
passenger embarked, where a service was actually performed. Automating that
would mean shipping a compliance interpretation the code cannot justify, which
root CLAUDE.md rule 5 forbids and which the same reasoning already keeps
`purchases.services.three_way_match` read-only and `items.HsnSacCode` unseeded.

So place of supply is a FIELD on the document, defaulted from the party's
registered state and overridable by the user, and this module answers only the
question that follows from it: given a supplier state and a place of supply,
which tax applies. The user's answer is recorded; ours is computed.
"""

from core.exceptions import ApplicationError
from tax.enums import SupplyNature, SupplyType, TaxTreatment


def determine_supply_nature(
    *,
    supplier_state,
    place_of_supply,
    party_treatment: str,
    with_payment_of_tax: bool = True,
) -> str:
    """Returns a `SupplyNature` for one document.

    `supplier_state` and `place_of_supply` are `StateCode` instances (or None).
    `with_payment_of_tax` distinguishes the two zero-rated routes: an exporter
    shipping under LUT or bond charges no tax (False), one claiming a refund of
    IGST paid charges it (True). Nothing in the data can tell these apart, so
    it is an input.

    Fails closed. Every branch that cannot be answered raises rather than
    guessing a nature, because guessing produces a document that posts to the
    wrong tax account and files in the wrong GSTR-1 table - a silent, expensive
    kind of wrong.
    """
    if party_treatment == TaxTreatment.OVERSEAS:
        # Sections 7(5)/16: an export is inter-State and zero-rated. The state
        # codes are irrelevant here and are not consulted - an overseas party
        # has no Indian state.
        return (
            SupplyNature.EXPORT_WITH_TAX if with_payment_of_tax
            else SupplyNature.EXPORT_WITHOUT_TAX
        )

    if party_treatment == TaxTreatment.SEZ:
        # s.8(1) first proviso / s.7(5)(b): a supply to an SEZ developer or unit
        # is inter-State REGARDLESS of the states involved. This is the case an
        # ordinary state comparison gets wrong.
        return (
            SupplyNature.SEZ_WITH_TAX if with_payment_of_tax
            else SupplyNature.SEZ_WITHOUT_TAX
        )

    if party_treatment == TaxTreatment.DEEMED_EXPORT:
        return SupplyNature.DEEMED_EXPORT

    if supplier_state is None:
        raise ApplicationError(
            "The organization's own GST state must be set before tax can be determined. "
            "Set it on the tax profile.",
            code="supplier_state_missing",
        )
    if place_of_supply is None:
        raise ApplicationError(
            "Place of supply is required to determine whether GST is intra-State or inter-State.",
            code="place_of_supply_missing",
        )

    if getattr(supplier_state, "is_special", False):
        # 96/97/99 are reporting buckets, not places. A supplier cannot be
        # located in one, and treating one as a state would silently make every
        # supply intra-State with itself.
        raise ApplicationError(
            f"'{supplier_state.code}' is a reporting bucket code, not a state a supplier can be in.",
            code="supplier_state_invalid",
        )

    # s.8(1) / s.7(1): the whole remaining rule is one comparison.
    if supplier_state.code == place_of_supply.code:
        return SupplyNature.INTRA_STATE
    return SupplyNature.INTER_STATE


def is_inter_state(supply_nature: str) -> bool:
    """True when the supply attracts IGST rather than the CGST/SGST pair.

    Zero-rated natures answer True: they are inter-State supplies whose rate
    happens to be nil, not intra-State ones. Keeping that straight matters for
    GSTR-1, where they file as inter-State.
    """
    return supply_nature in {
        SupplyNature.INTER_STATE,
        SupplyNature.EXPORT_WITH_TAX,
        SupplyNature.EXPORT_WITHOUT_TAX,
        SupplyNature.SEZ_WITH_TAX,
        SupplyNature.SEZ_WITHOUT_TAX,
        SupplyNature.DEEMED_EXPORT,
    }


def is_zero_rated(supply_nature: str) -> bool:
    """True for the two LUT/bond routes, where tax is charged at nil despite the
    supply being taxable. Not the same as exempt or nil-rated goods."""
    return supply_nature in {
        SupplyNature.EXPORT_WITHOUT_TAX,
        SupplyNature.SEZ_WITHOUT_TAX,
    }


def supply_type_for(supply_nature: str, party_treatment: str) -> str:
    """Maps a determined nature onto the e-Invoice schema's `TranDtls.SupTyp`.

    Kept here, beside the determination it mirrors, rather than in the payload
    builder: the two vocabularies must move together, and a mapping that lives
    next to the thing it maps from is one that gets updated when that thing
    changes.
    """
    mapping = {
        SupplyNature.EXPORT_WITH_TAX: SupplyType.EXPWP,
        SupplyNature.EXPORT_WITHOUT_TAX: SupplyType.EXPWOP,
        SupplyNature.SEZ_WITH_TAX: SupplyType.SEZWP,
        SupplyNature.SEZ_WITHOUT_TAX: SupplyType.SEZWOP,
        SupplyNature.DEEMED_EXPORT: SupplyType.DEXP,
    }
    if supply_nature in mapping:
        return mapping[supply_nature]
    if party_treatment == TaxTreatment.CONSUMER:
        # B2C has no SupTyp of its own - such invoices are not reported to the
        # IRP at all below the applicability threshold, and above it they are
        # still B2B-shaped. The caller decides whether to report; we record the
        # schema value that would apply.
        return SupplyType.B2B
    return SupplyType.B2B
