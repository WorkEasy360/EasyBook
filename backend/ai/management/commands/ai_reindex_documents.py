"""Batch (re)indexing — e.g. after changing the embedding model, whose new
config key makes every stored vector non-comparable until rebuilt.

    manage.py ai_reindex_documents [--organization <uuid>] [--force] [--inline]

Enqueues one Celery task per READY document, organization by organization,
each under its own tenant context. Safe to run repeatedly: indexing is
idempotent per content hash + embedding config (ai/rag/indexing.py).
"""

from django.core.management.base import BaseCommand

from accounts.models import Organization
from ai.rag.tasks import index_document_task
from core.tenancy import tenant_context
from documents.models.document import Document, UploadStatus


class Command(BaseCommand):
    help = "Enqueue RAG indexing for READY documents (all organizations, or one)."

    def add_arguments(self, parser):
        parser.add_argument("--organization", help="Only this organization id.")
        parser.add_argument("--force", action="store_true", help="Rebuild even if content is unchanged.")
        parser.add_argument("--inline", action="store_true", help="Run in this process instead of enqueueing.")

    def handle(self, *args, organization=None, force=False, inline=False, **options):
        organizations = Organization.objects.all()
        if organization:
            organizations = organizations.filter(pk=organization)
        total = 0
        for organization_id in organizations.values_list("id", flat=True):
            with tenant_context(organization_id=organization_id):
                document_ids = list(
                    Document.objects.filter(upload_status=UploadStatus.READY).values_list("id", flat=True)
                )
            for document_id in document_ids:
                if inline:
                    index_document_task(str(document_id), str(organization_id), force)
                else:
                    index_document_task.delay(str(document_id), str(organization_id), force)
                total += 1
        self.stdout.write(f"{'Indexed' if inline else 'Enqueued'} {total} document(s).")
