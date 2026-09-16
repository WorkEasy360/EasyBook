from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class AutomationNotification(TenantScopedModel):
    """Minimal in-app notification foundation (phase section 35) — no email/
    SMS transport exists in this codebase yet, so `send_notification` only
    ever creates one of these; wiring a real outbound channel is a future
    decision, not built pre-emptively (root CLAUDE.md)."""

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    message = models.CharField(max_length=1000)
    source_execution = models.ForeignKey(
        "automation.AutomationExecution", null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications"
    )
    is_read = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["organization", "recipient", "is_read"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.recipient_id}:{self.message[:40]}"
