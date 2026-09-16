"""Outbox event recording + causation/correlation propagation (phase
sections 9-12, 49-51).

`record_event` is called from the SAME transaction as the domain mutation it
describes — either via a `post_save` receiver (automation/receivers.py,
mirroring ai/rag/signals.py's relationship with `documents`: lower layers
never import `automation`) or, once a later slice adds a domain-mutating
action, directly from the execution engine while `causation_context` is
active so the new event correctly records what caused it.
"""

import contextvars
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.utils import timezone

from automation.models.event import AutomationEvent
from core.exceptions import ApplicationError

_current_causation_execution_id = contextvars.ContextVar("automation_causation_execution_id", default=None)
_current_causation_correlation_id = contextvars.ContextVar("automation_causation_correlation_id", default=None)
_current_causation_depth = contextvars.ContextVar("automation_causation_depth", default=0)


@contextmanager
def causation_context(*, execution_id, correlation_id, depth: int):
    """Entered by the execution engine around a domain-mutating action call
    so any AutomationEvent that action's own domain service triggers (via a
    post_save receiver) inherits the correct causation_id/correlation_id/
    depth — the mechanism phase section 51's MAX_AUTOMATION_DEPTH enforces
    against."""
    exec_token = _current_causation_execution_id.set(execution_id)
    corr_token = _current_causation_correlation_id.set(correlation_id)
    depth_token = _current_causation_depth.set(depth)
    try:
        yield
    finally:
        _current_causation_execution_id.reset(exec_token)
        _current_causation_correlation_id.reset(corr_token)
        _current_causation_depth.reset(depth_token)


def record_event(*, organization, event_type: str, entity_id, occurred_at=None, payload=None) -> AutomationEvent:
    depth = _current_causation_depth.get()
    max_depth = getattr(settings, "AUTOMATION_MAX_DEPTH", 5)
    if depth > max_depth:
        raise ApplicationError(
            f"Automation causation chain exceeded the maximum depth ({max_depth}).",
            code="automation_max_depth_exceeded",
        )
    return AutomationEvent.objects.create(
        organization=organization,
        event_type=event_type,
        entity_id=str(entity_id),
        occurred_at=occurred_at or timezone.now(),
        payload=payload or {},
        causation_id=_current_causation_execution_id.get(),
        correlation_id=_current_causation_correlation_id.get() or uuid.uuid4(),
        depth=depth,
    )
