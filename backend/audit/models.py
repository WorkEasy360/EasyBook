from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import TenantScopedModel


class AuditLog(TenantScopedModel):
    """Append-only record of every financial/security-relevant mutation.
    Never updated or deleted after creation — see audit/CLAUDE.md."""

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        POST = "post", "Post"
        REVERSE = "reverse", "Reverse"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="audit_logs")
    action = models.CharField(max_length=20, choices=Action.choices)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    # DjangoJSONEncoder - not the plain json encoder - because services
    # routinely record Decimal money amounts and date/UUID values in
    # `changes`, none of which stdlib json can serialize. The plain
    # encoder made `record(changes={"amount": Decimal("1.00")})` raise
    # TypeError, which would have turned an audit write - the thing that
    # must never be the reason a financial mutation fails - into a
    # transaction-aborting error. Same fix, same reasoning, as
    # core.models.IdempotencyKey.response_body.
    changes = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    request_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "object_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.action}:{self.object_type}:{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.all_objects.filter(pk=self.pk).exists():
            raise ValueError("AuditLog entries are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog entries are append-only and cannot be deleted.")
