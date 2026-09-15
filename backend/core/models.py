import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.managers import TenantManager


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantScopedModel(TimeStampedModel):
    """Base for every model that stores organization-owned data.

    `organization` MUST be included in the corresponding PostgreSQL RLS policy
    (see the 00xx_enable_rls migrations) — the manager-level filter here is
    defense in depth, not the primary control.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.PROTECT, related_name="+"
    )

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True


class IdempotencyKey(TenantScopedModel):
    """Records the outcome of a client-supplied Idempotency-Key so a retried
    mutation (e.g. a dropped-connection invoice POST) replays the original
    response instead of double-posting. See core.idempotency.IdempotentCreateMixin."""

    key = models.CharField(max_length=255)
    request_path = models.CharField(max_length=255)
    request_body_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    # DjangoJSONEncoder — not the plain json encoder — because a stored API
    # response commonly contains UUID/Decimal/date values straight from
    # Response.data (before the DRF renderer stringifies them). Plain
    # json.dumps raises TypeError on those; this is the first real caller of
    # IdempotentCreateMixin/IdempotencyKey, which is what surfaced it.
    response_body = models.JSONField(encoder=DjangoJSONEncoder)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "key", "request_path"],
                name="uniq_idempotency_key_per_org_path",
            )
        ]
