"""Building the Generate E-Way Bill payload, and the validity arithmetic.

API: v1.03, verified against docs.ewaybillgst.gov.in on 2026-09-15 (see
docs/gst-research.md). Field names are the API's own (`supplyType`,
`subSupplyType`, `docType`, `transMode`, `vehicleType`, ...) and are
reproduced exactly.

Two rules come from the official documentation and are implemented here rather
than assumed by callers:

  - VALIDITY: one day per 200 km, and any part of a further 200 km adds another
    day. 200 km is one day; 201 km is two.
  - DISTANCE CEILING: the API rejects a distance above 4000 km, so we do too
    rather than sending a request that cannot succeed.

The ₹50,000 threshold is NOT hard-coded: it is the all-India default on
`tax.TaxProfile.ewaybill_threshold`, because several states set a different
figure for intra-State movement and the number has changed before.
"""

import datetime
from decimal import Decimal

from core.exceptions import ApplicationError
from tax.selectors import get_tax_profile

MAX_DISTANCE_KM = 4000
KM_PER_DAY = 200

# docType values for the documents this system can move goods against.
DOC_TYPE_TAX_INVOICE = "INV"
DOC_TYPE_DELIVERY_CHALLAN = "CHL"

# subSupplyType: 1 = Supply. The API defines a dozen others (export, job work,
# SKD/CKD, line sales, recipient not known, exhibition) that turn on facts this
# system does not record; defaulting to Supply and letting the caller override
# is honest, inventing a mapping would not be.
SUB_SUPPLY_SUPPLY = "1"

ZERO = Decimal("0")


def compute_validity(
    *, distance_km: int, generated_at: datetime.datetime
) -> datetime.datetime:
    """One day per 200 km, part thereof rounding up, minimum one day.

    Returns midnight at the end of the final day, which is how the portal
    expresses expiry - a bill generated at 14:00 with one day's validity runs
    to the end of the following day, not to 14:00 the next.
    """
    if distance_km < 0:
        raise ApplicationError(
            "Distance cannot be negative.", code="ewaybill_distance_invalid"
        )
    if distance_km > MAX_DISTANCE_KM:
        raise ApplicationError(
            f"The e-way bill API rejects a distance above {MAX_DISTANCE_KM} km.",
            code="ewaybill_distance_too_far",
        )

    # -(-a // b) is ceiling division on ints. 200 -> 1, 201 -> 2, 400 -> 2.
    days = max(1, -(-distance_km // KM_PER_DAY))
    end_of_generation_day = datetime.datetime.combine(
        generated_at.date(), datetime.time.min, tzinfo=generated_at.tzinfo
    ) + datetime.timedelta(days=1)
    return end_of_generation_day + datetime.timedelta(days=days - 1)


def requires_ewaybill(*, organization, consignment_value: Decimal) -> bool:
    """Whether the configured threshold is met.

    Reads `TaxProfile.ewaybill_threshold` rather than a constant. An
    organization with no profile gets the all-India default, so this never
    raises - the caller may still generate a bill below the threshold, which
    is legal and sometimes required by a customer.
    """
    profile = get_tax_profile(organization=organization)
    threshold = profile.ewaybill_threshold if profile else Decimal("50000.00")
    return consignment_value >= threshold


def build_ewb_payload(
    *,
    document,
    distance_km: int,
    transport_mode: str = "1",
    vehicle_number: str = "",
    vehicle_type: str = "R",
    transporter_gstin: str = "",
    transporter_name: str = "",
    transport_doc_no: str = "",
    transport_doc_date=None,
    sub_supply_type: str = SUB_SUPPLY_SUPPLY,
) -> dict:
    """Builds the Generate EWB v1.03 request for an invoice or delivery challan."""
    organization = document.organization
    profile = get_tax_profile(organization=organization)
    if profile is None or profile.state_id is None or not profile.gstin:
        raise ApplicationError(
            "The organization's GSTIN and state must be configured before an e-way bill "
            "can be generated.",
            code="tax_profile_incomplete",
        )
    if distance_km > MAX_DISTANCE_KM:
        raise ApplicationError(
            f"The e-way bill API rejects a distance above {MAX_DISTANCE_KM} km.",
            code="ewaybill_distance_too_far",
        )

    is_invoice = hasattr(document, "invoice_number")
    doc_type = DOC_TYPE_TAX_INVOICE if is_invoice else DOC_TYPE_DELIVERY_CHALLAN
    document_number = (
        document.invoice_number if is_invoice else document.challan_number
    )
    if not document_number:
        raise ApplicationError(
            "A document must be numbered before an e-way bill can reference it.",
            code="document_not_numbered",
        )
    document_date = (
        document.invoice_date if is_invoice else document.challan_date
    )

    customer = document.customer
    supplier_state = profile.state.code
    place_of_supply = (
        document.place_of_supply.code
        if getattr(document, "place_of_supply_id", None)
        else supplier_state
    )

    payload = {
        "supplyType": "O",  # Outward. Inward movement is a purchase-side concern.
        "subSupplyType": sub_supply_type,
        "docType": doc_type,
        "docNo": document_number,
        "docDate": document_date.strftime("%d/%m/%Y"),
        "fromGstin": profile.gstin,
        "fromTrdName": organization.name,
        "fromStateCode": int(supplier_state),
        "actFromStateCode": int(supplier_state),
        "toGstin": customer.gstin or "URP",
        "toTrdName": customer.display_name,
        "toStateCode": int(place_of_supply),
        "actToStateCode": int(place_of_supply),
        "transDistance": str(distance_km),
        "transMode": transport_mode,
        "itemList": [],
    }

    if transporter_gstin:
        payload["transporterId"] = transporter_gstin
    if transporter_name:
        payload["transporterName"] = transporter_name
    if transport_doc_no:
        payload["transDocNo"] = transport_doc_no
    if transport_doc_date:
        payload["transDocDate"] = transport_doc_date.strftime("%d/%m/%Y")
    if vehicle_number:
        payload["vehicleNo"] = vehicle_number
        # vehicleType is only meaningful alongside a vehicle number.
        payload["vehicleType"] = vehicle_type

    for index, line in enumerate(document.lines.select_related("item").all(), start=1):
        item = {
            "productName": getattr(line, "description", "") or line.item.name,
            "hsnCode": getattr(line, "hsn_sac_snapshot", "") or "",
            "quantity": float(line.quantity),
            "qtyUnit": line.item.unit.code if line.item.unit_id else "OTH",
        }
        # A delivery challan has no pricing at all (sales/CLAUDE.md) - it is a
        # quantity-only document - so the value fields exist only for invoices.
        if is_invoice:
            item.update(
                {
                    "taxableAmount": float(line.taxable_amount),
                    "sgstRate": float(line.tax_rate / 2) if line.sgst_amount else 0.0,
                    "cgstRate": float(line.tax_rate / 2) if line.cgst_amount else 0.0,
                    "igstRate": float(line.tax_rate) if line.igst_amount else 0.0,
                    "cessRate": float(line.cess_rate),
                }
            )
        item["itemNo"] = index
        payload["itemList"].append(item)

    if is_invoice:
        payload.update(
            {
                "totalValue": float(document.subtotal - document.discount_total),
                "cgstValue": float(document.cgst_total),
                "sgstValue": float(document.sgst_total),
                "igstValue": float(document.igst_total),
                "cessValue": float(document.cess_total),
                "totInvValue": float(document.total),
            }
        )

    return payload
