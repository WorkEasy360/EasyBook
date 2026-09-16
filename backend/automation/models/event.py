import uuid

from django.db import models

from core.models import TenantScopedModel


class AutomationEvent(TenantScopedModel):
    """A durable outbox row for one domain occurrence (phase sections 9-12).

    Domain services emit these in the SAME transaction as their own state
    change (e.g. sales.services.invoices.post_invoice), so a mutation that
    commits successfully can never silently fail to notify automation — the
    classic "transaction commits, message publish fails" outbox problem.

    Carries identifiers/minimal metadata only (`payload`), never a copy of
    the whole business object (phase section 10) — a rule evaluation or
    action handler reloads authoritative data under its own tenant context.
    """

    event_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    event_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    payload = models.JSONField(default=dict, blank=True)

    # Loop-prevention lineage (phase sections 49-51): causation_id is the
    # AutomationExecution.id that caused this event, if any (null for an
    # event that originated from a direct user/API action, not a chained
    # automation effect). correlation_id ties an entire causal chain
    # together; depth is causation depth, enforced against
    # settings.AUTOMATION_MAX_DEPTH by the dispatcher.
    causation_id = models.UUIDField(null=True, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    depth = models.PositiveIntegerField(default=0)

    # Set once the outbox dispatcher has turned this event into (zero or
    # more) AutomationExecution rows for every currently-ACTIVE matching
    # rule. Claimed via SELECT ... FOR UPDATE SKIP LOCKED so two dispatcher
    # workers can never double-process the same row (phase section 85).
    dispatched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "dispatched_at"]),
            models.Index(fields=["organization", "event_type"]),
        ]
        ordering = ["occurred_at"]

    def __str__(self):
        return f"{self.event_type}:{self.entity_id}@{self.occurred_at}"
