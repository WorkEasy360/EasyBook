"""Database-level Row Level Security across every documents table — mirrors
purchases/tests/test_rls.py. Walks EVERY TenantScopedModel in the app so a
model added later without an entry in migrations/0002_enable_rls.py fails
here rather than shipping unprotected."""

from django.apps import apps
from django.db import connection
from django.test import TestCase

from core.models import TenantScopedModel
from core.tests.factories import make_org_with_owner
from documents.models.document import Document, UploadStatus


def _documents_tenant_models():
    return [
        model
        for model in apps.get_app_config("documents").get_models()
        if issubclass(model, TenantScopedModel)
    ]


class DocumentsRlsCoverageTests(TestCase):
    def test_every_documents_model_is_tenant_scoped(self):
        models = _documents_tenant_models()
        self.assertGreater(len(models), 0)
        all_models = list(apps.get_app_config("documents").get_models())
        self.assertEqual(
            sorted(m.__name__ for m in models),
            sorted(m.__name__ for m in all_models),
            "every documents model must inherit TenantScopedModel",
        )

    def test_every_documents_table_has_rls_enabled_and_forced(self):
        tables = [model._meta.db_table for model in _documents_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(%s)",
                [tables],
            )
            state = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

        missing = [table for table in tables if table not in state]
        self.assertEqual(missing, [], f"tables not found in pg_class: {missing}")

        unprotected = [table for table, (enabled, forced) in state.items() if not (enabled and forced)]
        self.assertEqual(
            unprotected, [],
            f"these documents tables lack ENABLE/FORCE ROW LEVEL SECURITY: {unprotected}",
        )

    def test_every_documents_table_has_a_policy(self):
        tables = [model._meta.db_table for model in _documents_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(
            sorted(set(tables) - with_policy), [],
            "every documents table needs a tenant isolation policy",
        )


class DocumentsFailClosedTests(TestCase):
    def test_queries_return_nothing_when_no_tenant_context_is_set(self):
        org, _, _ = make_org_with_owner("Org", "docs-rls@example.com")
        from core.tenancy import tenant_context

        with tenant_context(organization_id=org.id):
            Document.objects.create(
                organization=org, title="A", original_filename="a.pdf", storage_key="rls-key",
                storage_backend="local", mime_type="application/pdf", file_size=1, checksum_sha256="a" * 64,
                upload_status=UploadStatus.READY,
            )
        # No tenant context set at all now.
        self.assertEqual(Document.objects.count(), 0)

    def test_raw_sql_is_blocked_without_tenant_guc(self):
        org, _, _ = make_org_with_owner("Org2", "docs-rls-2@example.com")
        from core.tenancy import tenant_context

        with tenant_context(organization_id=org.id):
            Document.objects.create(
                organization=org, title="B", original_filename="b.pdf", storage_key="rls-key-2",
                storage_backend="local", mime_type="application/pdf", file_size=1, checksum_sha256="b" * 64,
                upload_status=UploadStatus.READY,
            )
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL app.current_organization_id = ''")
            cursor.execute("SELECT count(*) FROM documents_document WHERE storage_key = %s", ["rls-key-2"])
            count = cursor.fetchone()[0]
        self.assertEqual(count, 0)
