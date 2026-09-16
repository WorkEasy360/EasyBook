"""Building the GST INV-01 payload for IRN generation.

SCHEMA: `Version` "1.1", core APIs v1.03 / vital v1.04, verified against
einv-apisandbox.nic.in on 2026-09-15. See docs/gst-research.md for the full
provenance. Group and field names below are NIC's, reproduced exactly -
`TranDtls`, `DocDtls`, `SellerDtls`, `BuyerDtls`, `ItemList`, `ValDtls` - which
is why they break this codebase's snake_case convention. Renaming them to look
Pythonic would produce a payload the IRP rejects.

Only fields this system actually holds are emitted. The schema has around 130
optional fields covering dispatch addresses, export shipping bills, payment
terms and batch details; emitting empty or invented values for them would be
worse than omitting them, and root CLAUDE.md rule 5 forbids inventing the parts
we cannot source.

This module builds and validates. It does not transmit - see
`compliance/providers/base.py` for why there is no NIC client.
"""

from decimal import Decimal

from core.exceptions import ApplicationError
from tax.enums import SupplyType
from tax.selectors import get_tax_profile

SCHEMA_VERSION = "1.1"

# DocDtls.Typ - the schema admits exactly these three.
DOC_TYPE_INVOICE = "INV"
DOC_TYPE_CREDIT_NOTE = "CRN"
DOC_TYPE_DEBIT_NOTE = "DBN"

_REPORTABLE_SUPPLY_TYPES = {
    SupplyType.B2B,
    SupplyType.SEZWP,
    SupplyType.SEZWOP,
    SupplyType.EXPWP,
    SupplyType.EXPWOP,
    SupplyType.DEXP,
}

ZERO = Decimal("0")


def _money(value) -> float:
    """The schema's numeric fields are JSON numbers, not strings.

    Decimal is used everywhere inside this system and money never becomes a
    float for arithmetic (root CLAUDE.md rule 3). This conversion happens at
    the very last step, on an already-rounded 2dp value being serialized for
    transport, where the value is only ever read back by the IRP.
    """
    return float(value or ZERO)


def _party(*, gstin, legal_name, state_code, address="", location="", pincode=None, trade_name=""):
    party = {
        "Gstin": gstin or "URP",  # URP = unregistered person, the schema's own token
        "LglNm": legal_name,
        "Addr1": address or legal_name,
        "Loc": location or "NA",
        "Pos": state_code,
    }
    if trade_name:
        party["TrdNm"] = trade_name
    if state_code:
        party["Stcd"] = state_code
    if pincode:
        party["Pin"] = int(pincode)
    return party


def _address_bits(party) -> dict:
    """Pulls what it can out of the untyped billing_address JSON blob.

    That blob has no enforced schema (see sales/models/customer.py), so this
    reads defensively and falls back rather than raising: a missing address
    line should not stop an invoice being reported when the mandatory fields
    are all present.
    """
    address = getattr(party, "billing_address", None) or {}
    if not isinstance(address, dict):
        return {}
    return {
        "address": address.get("line1") or address.get("address1") or "",
        "location": address.get("city") or address.get("location") or "",
        "pincode": address.get("pincode") or address.get("postal_code"),
    }


def _validate_reportable(document, profile) -> None:
    if profile is None or profile.state_id is None:
        raise ApplicationError(
            "The organization's GST profile and state must be configured before an "
            "invoice can be reported to the IRP.",
            code="tax_profile_incomplete",
        )
    if not profile.gstin:
        raise ApplicationError(
            "The organization needs a GSTIN before reporting to the IRP.",
            code="supplier_gstin_missing",
        )
    if document.supply_type not in _REPORTABLE_SUPPLY_TYPES:
        raise ApplicationError(
            f"A document with supply type '{document.supply_type}' cannot be reported to the "
            "IRP. Set the place of supply so its GST treatment is determined first.",
            code="supply_type_not_reportable",
        )
    if not document.lines.exists():
        raise ApplicationError("Cannot report a document with no lines.", code="document_no_lines")


def build_item(line, *, index: int) -> dict:
    """One entry of `ItemList`.

    `IsServc` is required and derived from the item type rather than the HSN
    code: an SAC is a service code, but the item master already records the
    answer authoritatively and a blank HSN would otherwise force a guess.
    """
    from items.models.item import ItemType

    return {
        "SlNo": str(index),
        "PrdDesc": line.description or line.item.name,
        "IsServc": "Y" if line.item.item_type == ItemType.SERVICE else "N",
        "HsnCd": line.hsn_sac_snapshot,
        "Qty": float(line.quantity),
        "Unit": line.item.unit.code if line.item.unit_id else "OTH",
        "UnitPrice": _money(line.unit_price),
        "TotAmt": _money(line.line_base),
        "Discount": _money(line.discount_amount),
        "AssAmt": _money(line.taxable_amount),
        "GstRt": float(line.tax_rate),
        "IgstAmt": _money(line.igst_amount),
        "CgstAmt": _money(line.cgst_amount),
        "SgstAmt": _money(line.sgst_amount),
        "CesRt": float(line.cess_rate),
        "CesAmt": _money(line.cess_amount),
        "TotItemVal": _money(line.line_total),
    }


def build_irn_payload(document, *, doc_type: str = DOC_TYPE_INVOICE) -> dict:
    """Builds the INV-01 payload for an Invoice or Credit Note.

    Validates BEFORE building. A payload the IRP would reject is worse than no
    payload: it consumes a reporting attempt, and the error comes back as an
    opaque NIC code rather than something a user can act on.
    """
    organization = document.organization
    profile = get_tax_profile(organization=organization)
    _validate_reportable(document, profile)

    party = document.customer
    lines = list(document.lines.select_related("item", "item__unit").all())

    supplier_state = profile.state.code
    place_of_supply = document.place_of_supply.code if document.place_of_supply_id else supplier_state

    buyer_bits = _address_bits(party)
    document_number = getattr(document, "invoice_number", "") or getattr(
        document, "credit_note_number", ""
    )
    if not document_number:
        raise ApplicationError(
            "A document must be posted (and numbered) before it can be reported to the IRP.",
            code="document_not_numbered",
        )

    document_date = getattr(document, "invoice_date", None) or getattr(
        document, "credit_note_date", None
    )

    return {
        "Version": SCHEMA_VERSION,
        "TranDtls": {
            "TaxSch": "GST",
            "SupTyp": document.supply_type,
            "RegRev": "Y" if document.is_reverse_charge else "N",
            # "IGST on intra-state supply": the s.8 case where supplier and
            # place of supply share a state but IGST is nonetheless due - an
            # SEZ supply. The schema has a dedicated flag precisely because
            # the state codes alone cannot express it.
            "IgstOnIntra": (
                "Y"
                if (
                    supplier_state == place_of_supply
                    and document.igst_total > 0
                )
                else "N"
            ),
        },
        "DocDtls": {
            "Typ": doc_type,
            "No": document_number,
            # The schema's date format is DD/MM/YYYY, not ISO.
            "Dt": document_date.strftime("%d/%m/%Y"),
        },
        "SellerDtls": _party(
            gstin=profile.gstin,
            legal_name=organization.legal_name or organization.name,
            state_code=supplier_state,
            trade_name=organization.name,
        ),
        "BuyerDtls": {
            **_party(
                gstin=party.gstin,
                legal_name=party.legal_name or party.display_name,
                state_code=place_of_supply,
                trade_name=party.display_name,
                **buyer_bits,
            ),
            "Pos": place_of_supply,
        },
        "ItemList": [build_item(line, index=index) for index, line in enumerate(lines, start=1)],
        "ValDtls": {
            "AssVal": _money(document.subtotal - document.discount_total),
            "CgstVal": _money(document.cgst_total),
            "SgstVal": _money(document.sgst_total),
            "IgstVal": _money(document.igst_total),
            "CesVal": _money(document.cess_total),
            "Discount": _money(document.discount_total),
            "TotInvVal": _money(document.total),
        },
    }
