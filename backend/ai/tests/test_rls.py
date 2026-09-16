"""Tenant isolation for every AI table — ORM, retrieval service and raw SQL
under RLS (phase sections 49/50/78). Hard production gate."""

from django.apps import apps
from django.db import connection
from django.test import TestCase, TransactionTestCase

from ai.models import AIConversation, AIRequestLog, DocumentChunk
from ai.orchestration.conversations import get_conversation
from ai.orchestration.errors import AskBooksError
from ai.retrieval.search import hybrid_search
from ai.tests.base import AIFixtureMixin, index, tenant, upload_text
from core.models import TenantScopedModel

TWIN_TEXT = "Master agreement INV-7788: either party may terminate with sixty days written notice."


def _ai_models():
    return list(apps.get_app_config("ai").get_models())


class AIRlsCoverageTests(TestCase):
    def test_every_ai_model_is_tenant_scoped(self):
        for model in _ai_models():
            self.assertTrue(issubclass(model, TenantScopedModel), model.__name__)

    def test_every_ai_table_has_forced_rls_and_a_policy(self):
        tables = [model._meta.db_table for model in _ai_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = ANY(%s)", [tables])
            state = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
            cursor.execute("SELECT DISTINCT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(sorted(state), sorted(tables))
        self.assertEqual([t for t, (enabled, forced) in state.items() if not (enabled and forced)], [])
        self.assertEqual(sorted(set(tables) - with_policy), [])

    def test_database_role_is_not_a_superuser(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            self.assertFalse(cursor.fetchone()[0], "RLS tests are meaningless under a superuser role")


class CrossTenantRetrievalTests(AIFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        # IDENTICAL text in both organizations => identical embeddings.
        self.doc_a = upload_text(self.org_a, self.user_a, TWIN_TEXT, title="A agreement")
        self.doc_b = upload_text(self.org_b, self.user_b, TWIN_TEXT + " Org B only clause.", title="B agreement")
        index(self.org_a, self.doc_a)
        index(self.org_b, self.doc_b)

    def test_semantic_search_never_returns_the_other_tenants_twin_chunk(self):
        with tenant(self.org_a):
            result = hybrid_search(organization=self.org_a, query="terminate with sixty days notice")
        self.assertTrue(result.chunks)
        self.assertEqual({c.document_id for c in result.chunks}, {str(self.doc_a.pk)})

    def test_exact_identifier_search_never_crosses_tenants(self):
        with tenant(self.org_a):
            result = hybrid_search(organization=self.org_a, query="INV-7788 Org B only clause")
        self.assertEqual({c.document_id for c in result.chunks}, {str(self.doc_a.pk)})

    def test_organization_argument_cannot_widen_the_tenant(self):
        # Even if a caller passes org B while the GUC says org A, RLS and the
        # tenant manager keep the query inside org A — which has no B rows.
        with tenant(self.org_a):
            result = hybrid_search(organization=self.org_b, query="terminate with sixty days notice")
        self.assertEqual(result.chunks, [])

    def test_orm_chunk_access_is_scoped(self):
        with tenant(self.org_a):
            self.assertEqual(set(DocumentChunk.objects.values_list("document_id", flat=True)), {self.doc_a.pk})
            self.assertFalse(DocumentChunk.all_objects.filter(document_id=self.doc_b.pk).exists())
        self.assertFalse(DocumentChunk.objects.exists())  # no tenant => fail closed

    def test_conversation_isolation_across_organizations(self):
        with tenant(self.org_b):
            conversation = AIConversation.objects.create(organization=self.org_b, user=self.user_b, title="B")
        with tenant(self.org_a), self.assertRaises(AskBooksError):
            get_conversation(user=self.user_b, conversation_id=conversation.pk)


class RawSqlRlsTests(AIFixtureMixin, TransactionTestCase):
    """Raw SQL bypasses the Django manager entirely: only RLS stands between
    the query and the other tenant's rows."""

    def setUp(self):
        super().setUp()
        self.doc_a = upload_text(self.org_a, self.user_a, TWIN_TEXT, title="A")
        self.doc_b = upload_text(self.org_b, self.user_b, TWIN_TEXT, title="B")
        index(self.org_a, self.doc_a)
        index(self.org_b, self.doc_b)
        with tenant(self.org_b):
            AIRequestLog.objects.create(organization=self.org_b, feature="ask", status="ok")

    def _count(self, table, organization=None):
        context = tenant(organization) if organization else tenant_context_none()
        with context, connection.cursor() as cursor:
            cursor.execute(f"SELECT organization_id FROM {table}")  # noqa: S608 — fixed table names from this test
            return {row[0] for row in cursor.fetchall()}

    def test_raw_sql_sees_only_the_current_tenant(self):
        for table in ("ai_documentchunk", "ai_documentindex"):
            self.assertEqual(self._count(table, self.org_a), {self.org_a.id}, table)
        self.assertEqual(self._count("ai_airequestlog", self.org_a), set())

    def test_raw_sql_vector_query_without_tenant_returns_nothing(self):
        self.assertEqual(self._count("ai_documentchunk"), set())

    def test_raw_insert_into_another_tenant_is_refused(self):
        from django.db.utils import ProgrammingError

        with self.assertRaises(ProgrammingError), tenant(self.org_a), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ai_airequestlog (id, organization_id, created_at, updated_at, request_id, feature, intent, status, "
                "error_code, provider, model, prompt_version, embedding_config_key, tool_names, tool_call_count, "
                "retrieval_count, llm_calls, latency_ms) VALUES (gen_random_uuid(), %s, now(), now(), '', 'ask', '', 'ok', "
                "'', '', '', '', '', '[]', 0, 0, 0, 0)",
                [str(self.org_b.id)],
            )


def tenant_context_none():
    from core.tenancy import tenant_context

    return tenant_context()
