"""Tax fields shared by every counterparty master record.

`sales.Customer` and `purchases.Vendor` carry an identical block of tax fields -
gstin, pan, tax_treatment, place_of_supply_state - and need identical
validation for them. Neither may import the other (peer modules, see
backend/CLAUDE.md), and copying the validation into both is how the two
silently drift apart.

So it lives here, one layer below both, for the same reason `core.money` and
`core.recurrence` were promoted when `purchases` needed what `sales` had. It
sits in `tax` rather than `core` because it is entirely domain logic - GSTIN
structure and place-of-supply defaults are GST concepts, and `core` owns no
business domain.
"""

from core.exceptions import ApplicationError
from tax.enums import TaxTreatment
from tax.models import StateCode
from tax.selectors import known_state_codes
from tax.services.validation import validate_gstin, validate_pan

PARTY_TAX_FIELDS = ("gstin", "pan", "tax_treatment", "place_of_supply_state")


def resolve_state(state_code) -> StateCode | None:
    """Accepts a code string, a `StateCode`, or None."""
    if state_code is None or state_code == "":
        return None
    if isinstance(state_code, StateCode):
        return state_code
    state = StateCode.objects.filter(code=str(state_code)).first()
    if state is None:
        raise ApplicationError(
            f"'{state_code}' is not a known GST state code.", code="state_code_unknown"
        )
    return state


def clean_party_tax_fields(fields: dict, *, state_code=None) -> dict:
    """Normalizes and validates the tax block of a counterparty record.

    Mutates nothing: returns a new dict carrying only the keys it was given,
    so a caller doing a partial update does not accidentally write fields the
    user never touched.

    A registered party with no GSTIN is refused. That combination is not a
    harmless omission - it is what makes `determine_supply_nature` treat a B2B
    supply as B2C, file it in the wrong GSTR-1 table, and deny the customer
    their input credit.
    """
    cleaned = dict(fields)

    if "gstin" in cleaned:
        cleaned["gstin"] = validate_gstin(
            cleaned["gstin"] or "", known_state_codes=known_state_codes()
        )
    if "pan" in cleaned:
        cleaned["pan"] = validate_pan(cleaned["pan"] or "")

    if state_code is not None:
        cleaned["place_of_supply_state"] = resolve_state(state_code)

    treatment = cleaned.get("tax_treatment")
    gstin = cleaned.get("gstin")
    if treatment == TaxTreatment.REGISTERED and gstin == "":
        raise ApplicationError(
            "A registered counterparty must have a GSTIN.", code="gstin_required_for_registered"
        )

    return cleaned


def default_place_of_supply(party, supplier_state: StateCode | None) -> StateCode | None:
    """The place of supply to pre-fill on a new document for this party.

    Falls back to the supplier's own state, which is the correct default for
    the overwhelmingly common case - an unregistered walk-in customer buying
    over the counter, where the supply happens where the supplier is. It is
    only ever a default: the document's own field is what determination reads,
    and the user may change it.
    """
    return getattr(party, "place_of_supply_state", None) or supplier_state
