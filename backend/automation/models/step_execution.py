from django.db import models

from core.models import TenantScopedModel


class StepStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    RETRYING = "retrying", "Retrying"
    SKIPPED = "skipped", "Skipped"
    CANCELLED = "cancelled", "Cancelled"


class FailureCategory(models.TextChoices):
    """Stable categories (phase section 78) driving retry decisions and the
    UI — never a free-form string."""

    VALIDATION_ERROR = "validation_error", "Validation error"
    PERMISSION_ERROR = "permission_error", "Permission error"
    TARGET_MISSING = "target_missing", "Target missing"
    TRANSIENT_EXTERNAL_ERROR = "transient_external_error", "Transient external error"
    TIMEOUT = "timeout", "Timeout"
    RATE_LIMITED = "rate_limited", "Rate limited"
    DOMAIN_CONFLICT = "domain_conflict", "Domain conflict"
    SAFETY_LIMIT = "safety_limit", "Safety limit"
    UNEXPECTED_ERROR = "unexpected_error", "Unexpected error"


# Categories a retry can plausibly fix — never permission/validation/target/
# safety_limit (phase section 28: "do not retry ... permission denied,
# invalid rule, invalid target, validation failure, unsupported action").
RETRYABLE_CATEGORIES = frozenset(
    {
        FailureCategory.TRANSIENT_EXTERNAL_ERROR,
        FailureCategory.TIMEOUT,
        FailureCategory.RATE_LIMITED,
    }
)


class AutomationStepExecution(TenantScopedModel):
    """One action run within an AutomationExecution. `idempotency_key` is
    unique and stable across retries (derived from execution id + action id
    + order, never regenerated) — a retried Celery task reuses the SAME row
    rather than creating a second effect (phase section 24)."""

    execution = models.ForeignKey(
        "automation.AutomationExecution", on_delete=models.CASCADE, related_name="steps"
    )
    order = models.PositiveIntegerField()
    action_id = models.CharField(max_length=64)
    config_snapshot = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=StepStatus.choices, default=StepStatus.PENDING)
    attempt = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    failure_category = models.CharField(max_length=32, choices=FailureCategory.choices, blank=True)
    error_message = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["execution", "order"])]
        ordering = ["execution", "order"]

    def __str__(self):
        return f"{self.action_id}#{self.order}:{self.status}"
