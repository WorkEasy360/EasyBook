"""Celery tasks for RAG indexing and AI data retention.

Embedding/re-indexing never runs inline in a web request (phase section 46)
— the index API endpoints only enqueue these. Duplicate or concurrent
execution is safe by construction (ai/rag/indexing.py).
"""

import datetime

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from ai.config import get_ai_config
from core import access
from core.tenancy import tenant_context


@shared_task
def index_document_task(document_id: str, organization_id: str, force: bool = False, actor_id: str | None = None):
    from accounts.models import User
    from ai.rag.indexing import index_document

    # Embedding calls the (paid) AI provider. Denied: skipped, and the document is marked so
    # resume_skipped_indexing_task indexes it after a renewal (docs/workeasy360-sso.md).
    if not access.background_allowed_for_id(organization_id, task="ai.index_document"):
        from ai.rag.indexing import mark_skipped_for_access

        with tenant_context(organization_id=organization_id):
            mark_skipped_for_access(document_id)
        return "skipped"
    with tenant_context(organization_id=organization_id, user_id=actor_id):
        actor = User.objects.filter(pk=actor_id).first() if actor_id else None
        index = index_document(document_id=document_id, force=force, actor=actor)
        return index.status if index is not None else "not_found"


RESUME_BATCH_SIZE = 200


@shared_task
def resume_skipped_indexing_task():
    """Indexing that was skipped while an organization's Workeasy360 access had ended (see
    ``index_document_task``) is enqueued again once access is back. An organization with nothing marked
    costs one indexed query and no Workeasy360 question."""
    from accounts.models import Organization
    from ai.models import DocumentIndex, IndexStatus
    from ai.rag.indexing import SKIPPED_FOR_ACCESS

    resumed = 0
    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            document_ids = [
                str(document_id)
                for document_id in DocumentIndex.objects.filter(
                    status=IndexStatus.PENDING, reason_code=SKIPPED_FOR_ACCESS
                )
                .order_by("updated_at")
                .values_list("document_id", flat=True)[:RESUME_BATCH_SIZE]
            ]
        if not document_ids or not access.background_allowed(organization, task="ai.resume_skipped_indexing"):
            continue
        with tenant_context(organization_id=organization.id):
            for document_id in document_ids:
                transaction.on_commit(
                    lambda document_id=document_id, organization_id=str(organization.id): index_document_task.delay(
                        document_id, organization_id
                    )
                )
        resumed += len(document_ids)
    return {"resumed": resumed}


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
