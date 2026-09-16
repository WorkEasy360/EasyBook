"""Resolving one document's GST treatment from the organization and the party.

Called by `sales` and `purchases` alike at document-creation time. It lives
here because both need it and neither may import the other, and because the
answer it produces is snapshotted onto the document - after this runs, nothing
downstream re-reads the counterparty's master data.

THE BACKWARD-COMPATIBILITY SEAM
-------------------------------
An organization with no `TaxProfile`, or one whose profile has no state set,
gets `UNSPECIFIED` for everything and no place of supply. That is not a
degraded mode bolted on afterwards - it is the entire reason every
organization that predates this phase keeps working untouched. Their documents
carry a flat `tax_rate` and a single `tax_payable_account`, exactly as before;
`split_tax` leaves the components at zero; the posting services fall back to
the document's own tax account; and the check constraint's "all components
zero" branch admits the row.

Nothing about GST is imposed on an organization that has not asked for it.
"""

from tax.enums import SupplyNature, SupplyType
from tax.selectors import get_tax_profile
from tax.services.determination import determine_supply_nature, supply_type_for
from tax.services.party import resolve_state

UNSPECIFIED_TREATMENT = {
    "place_of_supply": None,
    "supply_nature": SupplyNature.UNSPECIFIED,
    "supply_type": SupplyType.UNSPECIFIED,
}


def resolve_document_tax(
    *,
    organization,
    party,
    place_of_supply=None,
    with_payment_of_tax: bool = True,
) -> dict:
    """Returns the GST header fields for one document.

    `place_of_supply` may be a `StateCode`, a code string, or None. None means
    "use the default": the party's own state, falling back to the supplier's -
    which is the right answer for the common over-the-counter case, where the
    supply happens where the supplier is.

    Raises only when the organization HAS configured GST and the answer is
    genuinely undeterminable. An unconfigured organization never raises.
    """
    profile = get_tax_profile(organization=organization)
    if profile is None or profile.state_id is None:
        return dict(UNSPECIFIED_TREATMENT)

    resolved_pos = resolve_state(place_of_supply) if place_of_supply is not None else None
    if resolved_pos is None:
        resolved_pos = getattr(party, "place_of_supply_state", None) or profile.state

    party_treatment = getattr(party, "tax_treatment", None)
    nature = determine_supply_nature(
        supplier_state=profile.state,
        place_of_supply=resolved_pos,
        party_treatment=party_treatment,
        with_payment_of_tax=with_payment_of_tax,
    )
    return {
        "place_of_supply": resolved_pos,
        "supply_nature": nature,
        "supply_type": supply_type_for(nature, party_treatment),
    }


def carry_forward_tax(document) -> dict:
    """The GST header fields of an existing document, for conversion.

    Quote -> Sales Order -> Invoice must not silently re-determine the
    treatment: the customer's master data may have changed between acceptance
    and invoicing, and the tax the customer was quoted is the tax they should
    be billed. Conversion copies; it does not recompute.
    """
    return {
        "place_of_supply": document.place_of_supply,
        "supply_nature": document.supply_nature,
        "supply_type": document.supply_type,
        "is_reverse_charge": document.is_reverse_charge,
    }
