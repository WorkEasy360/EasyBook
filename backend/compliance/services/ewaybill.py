"""Generating e-way bills and recording what the portal returned.

Same idempotency mechanism as `services/einvoice.py`: status plus
`select_for_update()`, no Idempotency-Key layer.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from compliance.models import EWayBill, EWayBillStatus
from compliance.providers.base import get_ewaybill_provider
from compliance.services.ewaybill_payload import build_ewb_payload, compute_validity
from core.exceptions import ApplicationError

TRANSPORT_FIELDS = (
    "distance_km",
    "transport_mode",
    "transporter_gstin",
    "transporter_name",
    "transport_doc_no",
    "vehicle_number",
    "vehicle_type",
)


def _document_kwargs(document) -> dict:
    from sales.models.delivery import DeliveryChallan
    from sales.models.invoice import Invoice

    if isinstance(document, Invoice):
        return {"invoice": document}
    if isinstance(document, DeliveryChallan):
        return {"delivery_challan": document}
    raise ApplicationError(
        "Only invoices and delivery challans can carry an e-way bill.",
        code="ewaybill_document_unsupported",
    )


def _for_document(document):
    return EWayBill.objects.select_for_update().filter(
        organization=document.organization, **_document_kwargs(document)
    )


def _existing_live(document) -> EWayBill | None:
    """The record a new generation would collide with. Excludes cancelled ones,
    because a cancelled bill is exactly when a fresh one is legitimate."""
    return _for_document(document).exclude(status=EWayBillStatus.CANCELLED).first()


def _latest(document) -> EWayBill | None:
    """The most recent record whatever its status - what a CANCELLATION looks
    at, so cancelling twice returns the same row rather than reporting nothing
    to cancel."""
    return _for_document(document).order_by("-created_at").first()


@transaction.atomic
def prepare_ewaybill(*, document, distance_km: int, provider_key: str = "manual",
                     actor=None, **transport) -> EWayBill:
    """Builds and stores the payload without contacting the portal."""
    existing = _existing_live(document)
    if existing is not None and existing.status == EWayBillStatus.GENERATED:
        return existing

    payload = build_ewb_payload(document=document, distance_km=distance_km, **transport)
    consignment_value = getattr(document, "total", None) or 0

    fields = {
        "distance_km": distance_km,
        "consignment_value": consignment_value,
        "payload": payload,
        "provider_key": provider_key,
        "transport_mode": transport.get("transport_mode", "1"),
        "transporter_gstin": transport.get("transporter_gstin", ""),
        "transporter_name": transport.get("transporter_name", ""),
        "transport_doc_no": transport.get("transport_doc_no", ""),
        "vehicle_number": transport.get("vehicle_number", ""),
        "vehicle_type": transport.get("vehicle_type", "R"),
    }

    if existing is not None:
        for field, value in fields.items():
            setattr(existing, field, value)
        existing.error_message = ""
        existing.save(update_fields=[*fields.keys(), "error_message", "updated_at"])
        return existing

    # See the matching comment in services/einvoice.py: select_for_update()
    # locks nothing when no row exists, so concurrent first-time callers race
    # to INSERT and the unique index picks a winner. The loser re-reads it.
    try:
        with transaction.atomic():
            record = EWayBill.objects.create(
                organization=document.organization,
                status=EWayBillStatus.PENDING,
                **_document_kwargs(document),
                **fields,
            )
    except IntegrityError:
        winner = _existing_live(document)
        if winner is None:
            raise
        return winner
    record_audit(
        organization_id=document.organization_id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="compliance.EWayBill",
        object_id=record.id,
        changes={"document": str(document.id), "distance_km": distance_km},
    )
    return record


@transaction.atomic
def record_ewaybill(
    *, document, ewb_no: str, ewb_date=None, valid_until=None, distance_km: int = 0,
    provider_key: str = "manual", actor=None, **transport
) -> EWayBill:
    """Records an e-way bill number obtained from the portal.

    `valid_until` is taken from the portal when given and only PREDICTED by
    `compute_validity` when it is not: what governs the consignment is the
    validity the portal actually issued, and a vehicle on the road depends on
    that date rather than on our arithmetic.
    """
    if not ewb_no:
        raise ApplicationError("An e-way bill number is required.", code="ewb_no_required")

    record = _existing_live(document)
    if record is None:
        record = prepare_ewaybill(
            document=document, distance_km=distance_km, provider_key=provider_key,
            actor=actor, **transport,
        )
    if record.status == EWayBillStatus.GENERATED:
        if record.ewb_no == ewb_no:
            return record
        raise ApplicationError(
            f"This document already carries e-way bill {record.ewb_no}.",
            code="ewaybill_already_generated",
        )

    generated_at = ewb_date or timezone.now()
    record.ewb_no = ewb_no
    record.ewb_date = generated_at
    record.valid_until = valid_until or compute_validity(
        distance_km=record.distance_km, generated_at=generated_at
    )
    record.status = EWayBillStatus.GENERATED
    record.error_message = ""
    record.save(
        update_fields=["ewb_no", "ewb_date", "valid_until", "status", "error_message", "updated_at"]
    )
    record_audit(
        organization_id=document.organization_id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="compliance.EWayBill",
        object_id=record.id,
        changes={"ewb_no": ewb_no, "valid_until": record.valid_until},
    )
    return record


@transaction.atomic
def generate_ewaybill(*, document, distance_km: int, provider_key: str = "manual",
                      actor=None, **transport) -> EWayBill:
    provider = get_ewaybill_provider(provider_key)
    if provider is None:
        raise ApplicationError(
            f"Unknown e-way bill provider '{provider_key}'.", code="provider_unknown"
        )

    record = prepare_ewaybill(
        document=document, distance_km=distance_km, provider_key=provider_key,
        actor=actor, **transport,
    )
    if record.status == EWayBillStatus.GENERATED:
        return record

    if not provider.supports_generate():
        raise ApplicationError(
            f"The '{provider_key}' provider cannot reach the e-way bill portal. Generate it "
            "on the government portal and record it with record_ewaybill().",
            code="provider_cannot_generate",
        )

    try:
        result = provider.generate_ewaybill(payload=record.payload)
    except Exception as exc:
        record.status = EWayBillStatus.FAILED
        record.error_message = str(exc)
        record.save(update_fields=["status", "error_message", "updated_at"])
        raise

    return record_ewaybill(
        document=document, ewb_no=result.ewb_no, ewb_date=result.ewb_date,
        valid_until=result.valid_until, provider_key=provider_key, actor=actor,
    )


@transaction.atomic
def record_ewaybill_cancellation(*, document, reason: str, actor=None) -> EWayBill:
    record = _latest(document)
    if record is None:
        raise ApplicationError(
            "This document has no e-way bill to cancel.", code="ewaybill_not_found",
            status_code=404,
        )
    if record.status == EWayBillStatus.CANCELLED:
        return record
    if record.status != EWayBillStatus.GENERATED:
        raise ApplicationError(
            f"Cannot cancel an e-way bill in status '{record.status}'.",
            code="ewaybill_invalid_status",
        )

    record.status = EWayBillStatus.CANCELLED
    record.cancelled_at = timezone.now()
    record.cancel_reason = reason
    record.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
    record_audit(
        organization_id=document.organization_id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="compliance.EWayBill",
        object_id=record.id,
        changes={"ewb_no": record.ewb_no, "reason": reason},
    )
    return record
