"""Reporting documents to the IRP, and recording what came back.

Idempotent by construction, on `status` under `select_for_update()` - the same
mechanism `accounting.services.posting.post_journal` and
`sales.services.invoices.post_invoice` use. No Idempotency-Key layer is needed
because there is a status to exploit: a second attempt to report an invoice
that already has a record finds it locked and GENERATED, and returns it.

That covers every case EXCEPT the first one. A row that does not exist yet
cannot be locked, so concurrent first-time callers all reach the INSERT
together and the partial unique indexes on `EInvoiceDocument` are what actually
arbitrate; the losers re-read the winner's row rather than failing. The two
mechanisms together are what make "report this invoice" safe to call twice at
once - see `prepare_einvoice`.

It matters more here than almost anywhere else in the system. A duplicate
invoice row can be voided; a duplicate IRN is a second government FILING
against one invoice, undone only by cancelling on the portal inside a limited
window.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from compliance.models import EInvoiceDocument, EInvoiceStatus
from compliance.providers.base import get_einvoice_provider
from compliance.services.einvoice_payload import (
    DOC_TYPE_CREDIT_NOTE,
    DOC_TYPE_INVOICE,
    build_irn_payload,
)
from core.exceptions import ApplicationError


def _document_kwargs(document) -> dict:
    """Which FK column this document occupies, and its schema doc type."""
    from sales.models.credit_note import CreditNote
    from sales.models.invoice import Invoice

    if isinstance(document, Invoice):
        return {"invoice": document, "doc_type": DOC_TYPE_INVOICE}
    if isinstance(document, CreditNote):
        return {"credit_note": document, "doc_type": DOC_TYPE_CREDIT_NOTE}
    raise ApplicationError(
        "Only invoices and credit notes can be reported to the IRP.",
        code="einvoice_document_unsupported",
    )


def _for_document(document):
    kwargs = _document_kwargs(document)
    kwargs.pop("doc_type")
    return EInvoiceDocument.objects.select_for_update().filter(
        organization=document.organization, **kwargs
    )


def _existing_live(document) -> EInvoiceDocument | None:
    """The record a new filing would collide with. Excludes cancelled ones,
    because a cancelled IRN is exactly the case where a fresh attempt is
    legitimate."""
    return _for_document(document).exclude(status=EInvoiceStatus.CANCELLED).first()


def _latest(document) -> EInvoiceDocument | None:
    """The most recent record whatever its status - what a CANCELLATION looks
    at, so that cancelling twice returns the same row instead of reporting
    nothing to cancel."""
    return _for_document(document).order_by("-created_at").first()


@transaction.atomic
def prepare_einvoice(*, document, provider_key: str = "manual", actor=None) -> EInvoiceDocument:
    """Builds and stores the payload without reporting it.

    Useful on its own: with the manual provider this is exactly what the
    operator needs - a validated payload to upload - and it surfaces a
    configuration error before anyone opens the portal.
    """
    existing = _existing_live(document)
    if existing is not None and existing.status == EInvoiceStatus.GENERATED:
        return existing

    kwargs = _document_kwargs(document)
    doc_type = kwargs.pop("doc_type")
    payload = build_irn_payload(document, doc_type=doc_type)

    if existing is not None:
        existing.payload = payload
        existing.provider_key = provider_key
        existing.error_message = ""
        existing.save(update_fields=["payload", "provider_key", "error_message", "updated_at"])
        return existing

    # `select_for_update()` above locks nothing when no row exists yet, so
    # concurrent first-time callers all reach this INSERT and the unique index
    # arbitrates between them. The loser is not an error: the winner created
    # exactly the record it wanted, so re-read and return that. A savepoint is
    # required because an IntegrityError otherwise poisons the surrounding
    # atomic block.
    try:
        with transaction.atomic():
            record = EInvoiceDocument.objects.create(
                organization=document.organization,
                status=EInvoiceStatus.PENDING,
                provider_key=provider_key,
                payload=payload,
                **kwargs,
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
        object_type="compliance.EInvoiceDocument",
        object_id=record.id,
        changes={"document": str(document.id), "provider": provider_key},
    )
    return record


@transaction.atomic
def record_irn(
    *,
    document,
    irn: str,
    ack_no: str = "",
    ack_date=None,
    signed_invoice: str = "",
    signed_qr_code: str = "",
    provider_key: str = "manual",
    actor=None,
) -> EInvoiceDocument:
    """Records an IRN obtained from the portal (the manual path).

    Idempotent: reporting the same IRN twice returns the existing record.
    Reporting a DIFFERENT IRN for a document that already has one is refused -
    that is either a duplicate filing or a typo, and both need a human.
    """
    if not irn:
        raise ApplicationError("An IRN is required.", code="irn_required")

    record = _existing_live(document) or prepare_einvoice(
        document=document, provider_key=provider_key, actor=actor
    )
    if record.status == EInvoiceStatus.GENERATED:
        if record.irn == irn:
            return record
        raise ApplicationError(
            f"This document already carries IRN {record.irn}. A document cannot hold two.",
            code="einvoice_already_generated",
        )

    record.irn = irn
    record.ack_no = ack_no
    record.ack_date = ack_date or timezone.now()
    record.signed_invoice = signed_invoice
    record.signed_qr_code = signed_qr_code
    record.status = EInvoiceStatus.GENERATED
    record.error_message = ""
    record.save(
        update_fields=[
            "irn", "ack_no", "ack_date", "signed_invoice", "signed_qr_code",
            "status", "error_message", "updated_at",
        ]
    )
    record_audit(
        organization_id=document.organization_id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="compliance.EInvoiceDocument",
        object_id=record.id,
        changes={"irn": irn, "ack_no": ack_no},
    )
    return record


@transaction.atomic
def generate_einvoice(*, document, provider_key: str = "manual", actor=None) -> EInvoiceDocument:
    """Reports the document through its provider.

    With the `manual` provider this raises a message telling the caller to use
    the portal and `record_irn` - deliberately, rather than silently doing
    nothing, so "not integrated" never looks like "filed".
    """
    provider = get_einvoice_provider(provider_key)
    if provider is None:
        raise ApplicationError(
            f"Unknown e-invoice provider '{provider_key}'.", code="provider_unknown"
        )

    record = prepare_einvoice(document=document, provider_key=provider_key, actor=actor)
    if record.status == EInvoiceStatus.GENERATED:
        return record

    if not provider.supports_generate():
        raise ApplicationError(
            f"The '{provider_key}' provider cannot reach the IRP. Generate the IRN on the "
            "government portal and record it with record_irn().",
            code="provider_cannot_generate",
        )

    try:
        result = provider.generate_irn(payload=record.payload)
    except Exception as exc:
        record.status = EInvoiceStatus.FAILED
        record.error_message = str(exc)
        record.save(update_fields=["status", "error_message", "updated_at"])
        raise

    return record_irn(
        document=document,
        irn=result.irn,
        ack_no=result.ack_no,
        ack_date=result.ack_date,
        signed_invoice=result.signed_invoice,
        signed_qr_code=result.signed_qr_code,
        provider_key=provider_key,
        actor=actor,
    )


@transaction.atomic
def record_irn_cancellation(*, document, reason: str, actor=None) -> EInvoiceDocument:
    """Marks a reported IRN cancelled. Idempotent.

    The record is never deleted (see `EInvoiceDocument.delete`): a cancelled
    filing is itself a fact the audit trail needs.
    """
    record = _latest(document)
    if record is None:
        raise ApplicationError(
            "This document has no e-invoice to cancel.", code="einvoice_not_found",
            status_code=404,
        )
    if record.status == EInvoiceStatus.CANCELLED:
        return record
    if record.status != EInvoiceStatus.GENERATED:
        raise ApplicationError(
            f"Cannot cancel an e-invoice in status '{record.status}'.",
            code="einvoice_invalid_status",
        )

    record.status = EInvoiceStatus.CANCELLED
    record.cancelled_at = timezone.now()
    record.cancel_reason = reason
    record.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
    record_audit(
        organization_id=document.organization_id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="compliance.EInvoiceDocument",
        object_id=record.id,
        changes={"irn": record.irn, "reason": reason},
    )
    return record
