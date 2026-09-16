from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import TenantScopedModel

# Hard ceiling enforced in services/ocr.py before this row is ever written —
# "do not store huge provider responses blindly" (phase section 12/20).
MAX_RAW_TEXT_CHARS = 200_000


class OCRResult(TenantScopedModel):
    """The single authoritative OCR outcome for a Document. One row per
    document (re-running OCR overwrites it under a row lock — see
    services/ocr.py) rather than an append-only history: nothing downstream
    needs to compare successive OCR attempts, only the latest one a reviewer
    can act on.

    Every value here is UNTRUSTED input from a third-party engine or, in the
    manual provider's case, not yet populated at all — see root CLAUDE.md
    ("AI must NEVER independently determine authoritative ... balances") and
    documents/CLAUDE.md ("OCR output untrusted").
    """

    document = models.OneToOneField("documents.Document", on_delete=models.CASCADE, related_name="ocr_result")

    provider = models.CharField(max_length=50)
    provider_version = models.CharField(max_length=50, blank=True)

    raw_text = models.TextField(blank=True)
    # {"field_name": {"value": ..., "confidence": 0.0-1.0}, ...} — normalized
    # shape produced by ocr/providers/*, never the provider's native response
    # verbatim (phase section 12: "normalize or limit size").
    structured_payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    # Overall confidence across all extracted fields, used only to route to
    # NEEDS_REVIEW vs COMPLETED (services/ocr.py) — never to auto-post
    # anything (root CLAUDE.md, phase section 14/16).
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)

    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.CharField(max_length=500, blank=True)

    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "provider"])]

    def __str__(self):
        return f"OCRResult({self.document_id})"
