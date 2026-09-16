from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class RequestStatus(models.TextChoices):
    OK = "ok", "OK"
    NO_DATA = "no_data", "No data found"
    REFUSED = "refused", "Refused"
    ERROR = "error", "Error"
    RATE_LIMITED = "rate_limited", "Rate limited"


class AIRequestLog(TenantScopedModel):
    """Minimal per-request telemetry (phase sections 37/42/61).

    Metadata only — never the prompt, the question, retrieved text, tool
    payloads or the answer. Token counts are what the provider reported
    (None when it reports nothing); pricing is deliberately NOT stored here,
    because prices change — the usage API applies the currently configured
    AI_MODEL_PRICING at read time and labels the result an estimate.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    conversation = models.ForeignKey(
        "ai.AIConversation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    request_id = models.CharField(max_length=64, blank=True)
    feature = models.CharField(max_length=40)
    intent = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=RequestStatus.choices)
    error_code = models.CharField(max_length=50, blank=True)

    provider = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    embedding_config_key = models.CharField(max_length=200, blank=True)

    tool_names = models.JSONField(default=list, blank=True)
    tool_call_count = models.PositiveIntegerField(default=0)
    retrieval_count = models.PositiveIntegerField(default=0)
    llm_calls = models.PositiveIntegerField(default=0)

    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    cached_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    embedding_tokens = models.PositiveIntegerField(null=True, blank=True)

    latency_ms = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["organization", "user", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"AIRequestLog({self.feature}, {self.status})"
