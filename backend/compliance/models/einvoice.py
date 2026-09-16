from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import TenantScopedModel


class EInvoiceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    GENERATED = "generated", "Generated"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class EInvoiceDocument(TenantScopedModel):
    """The IRP's response to reporting one document, and the payload we sent.

    Points at EITHER an Invoice or a Credit Note, never both and never neither
    (`CheckConstraint` below) - the e-Invoice schema's `DocDtls.Typ` admits INV,
    CRN and DBN, and this codebase has the first two. Two nullable FKs rather
    than a generic relation, matching how `banking.BankTransactionMatch` models
    its counterparts: explicit columns keep the queries and the RLS policy
    simple.

    THE PROVIDER-RETURNED FIELDS ARE FROZEN after they are first written, the
    same treatment `banking.BankTransaction` gives bank-reported data. An IRN is
    a government-issued fact about a document: if ours stops matching theirs,
    the reconciliation that depends on it is worthless. What stays mutable is
    our own interpretation - `status` on cancellation, and the cancel metadata.
    """

    invoice = models.ForeignKey(
        "sales.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="einvoices"
    )
    credit_note = models.ForeignKey(
        "sales.CreditNote", null=True, blank=True, on_delete=models.PROTECT, related_name="einvoices"
    )

    status = models.CharField(
        max_length=16, choices=EInvoiceStatus.choices, default=EInvoiceStatus.PENDING
    )
    provider_key = models.CharField(max_length=32, default="manual")

    # --- as returned by the IRP -------------------------------------------
    # Names follow the NIC response fields (Irn, AckNo, AckDt, SignedInvoice,
    # SignedQRCode) so the mapping to and from a payload stays obvious.
    irn = models.CharField(max_length=64, blank=True)
    ack_no = models.CharField(max_length=32, blank=True)
    ack_date = models.DateTimeField(null=True, blank=True)
    signed_invoice = models.TextField(blank=True)
    signed_qr_code = models.TextField(blank=True)

    # DjangoJSONEncoder, not the plain encoder: the payload is full of Decimal
    # money and date values and plain json.dumps raises TypeError on both. Same
    # fix and reasoning as core.models.IdempotencyKey.response_body and
    # audit.AuditLog.changes.
    payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    error_message = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    _IRP_REPORTED_FIELDS = (
        "irn",
        "ack_no",
        "ack_date",
        "signed_invoice",
        "signed_qr_code",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(invoice__isnull=False, credit_note__isnull=True)
                    | models.Q(invoice__isnull=True, credit_note__isnull=False)
                ),
                name="einvoice_targets_exactly_one_document",
            ),
            models.UniqueConstraint(
                fields=["organization", "irn"],
                condition=~models.Q(irn=""),
                name="uniq_einvoice_irn_per_org",
            ),
            # One live e-invoice per document. A cancelled one may be followed
            # by a fresh attempt, which is why the condition excludes them.
            models.UniqueConstraint(
                fields=["organization", "invoice"],
                condition=models.Q(invoice__isnull=False) & ~models.Q(status="cancelled"),
                name="uniq_live_einvoice_per_invoice",
            ),
            models.UniqueConstraint(
                fields=["organization", "credit_note"],
                condition=models.Q(credit_note__isnull=False) & ~models.Q(status="cancelled"),
                name="uniq_live_einvoice_per_credit_note",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"IRN {self.irn or '(pending)'}"

    @property
    def document(self):
        return self.invoice or self.credit_note

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values(*self._IRP_REPORTED_FIELDS)
                .first()
            )
            if previous:
                changed = [
                    field
                    for field in self._IRP_REPORTED_FIELDS
                    if previous[field] not in ("", None) and previous[field] != getattr(self, field)
                ]
                if changed:
                    raise ValueError(
                        "IRP-reported e-invoice fields are immutable once set: "
                        f"{', '.join(changed)}."
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "E-invoice records are never deleted - cancel the IRN instead, so the "
            "cancellation itself stays on the record."
        )
