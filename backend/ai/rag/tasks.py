"""Celery tasks for RAG indexing and AI data retention.

Embedding/re-indexing never runs inline in a web request (phase section 46)
— the index API endpoints only enqueue these. Duplicate or concurrent
execution is safe by construction (ai/rag/indexing.py).
"""

import datetime

from celery import shared_task
from django.utils import timezone

from ai.config import get_ai_config
from core.tenancy import tenant_context


@shared_task
def index_document_task(document_id: str, organization_id: str, force: bool = False, actor_id: str | None = None):
    from accounts.models import User
    from ai.rag.indexing import index_document

    with tenant_context(organization_id=organization_id, user_id=actor_id):
        actor = User.objects.filter(pk=actor_id).first() if actor_id else None
        index = index_document(document_id=document_id, force=force, actor=actor)
        return index.status if index is not None else "not_found"


@shared_task
def purge_expired_ai_data():
    """Retention (phase section 88): conversations idle longer than
    AI_CONVERSATION_RETENTION_DAYS and request telemetry older than
    AI_REQUEST_LOG_RETENTION_DAYS are deleted, organization by organization
    (each under its own tenant context, so RLS applies to every DELETE).
    Schedule with celery beat; see ai/CLAUDE.md."""
    from accounts.models import Organization
    from ai.models import AIConversation, AIRequestLog

    config = get_ai_config()
    now = timezone.now()
    conversation_cutoff = now - datetime.timedelta(days=config.conversation_retention_days)
    log_cutoff = now - datetime.timedelta(days=config.request_log_retention_days)
    totals = {"conversations": 0, "request_logs": 0}
    for organization_id in Organization.objects.values_list("id", flat=True):
        with tenant_context(organization_id=organization_id):
            stale = AIConversation.objects.filter(last_message_at__lt=conversation_cutoff)
            totals["conversations"] += stale.count()
            stale.delete()
            totals["request_logs"] += AIRequestLog.objects.filter(created_at__lt=log_cutoff).delete()[0]
    return totals
