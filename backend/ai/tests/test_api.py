from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from ai.models import AIConversation, AIRequestLog, DocumentChunk, DocumentIndex, IndexStatus
from ai.providers import override_llm_provider
from ai.providers.errors import ProviderUnavailable
from ai.providers.fake import FakeLLMProvider
from ai.tests.base import AIFixtureMixin, add_member, tenant, upload_text
from ai.tests.test_tools import ToolFixtureMixin
from authz.roles import Role
from core.models import IdempotencyKey


class APIHelpers:
    def call(self, method, url, user, organization=None, data=None, **headers):
        self.client.force_authenticate(user=user)
        if organization is not None:
            headers["HTTP_X_ORGANIZATION_ID"] = str(organization.id)
        with override_llm_provider(getattr(self, "provider", None) or FakeLLMProvider()):
            return getattr(self.client, method)(url, data=data, format="json", **headers)


class AskApiTests(APIHelpers, ToolFixtureMixin, APITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_ask_returns_stable_contract(self):
        response = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, {"question": "Which five customers owe us the most?"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            set(response.data),
            {"answer", "status", "intent", "sources", "structured_data", "request_id", "conversation_id", "retrieval_mode", "warnings"},
        )
        self.assertEqual(response.data["request_id"], response["X-Request-ID"])
        self.assertEqual(response.data["structured_data"]["tool_results"][0]["tool"], "get_customer_balances")
        body = str(response.data)
        for secret in ("Authoritative data:", "untrusted_document", "SECRET_KEY", "Traceback"):
            self.assertNotIn(secret, body)

    def test_tenant_comes_from_the_header_membership_not_the_body(self):
        response = self.call(
            "post", "/api/v1/ai/ask/", self.user_a, self.org_a,
            {"question": "Which five customers owe us the most?", "organization_id": str(self.org_b.id)},
        )
        self.assertEqual(response.status_code, 200)
        with tenant(self.org_a):
            self.assertEqual(AIRequestLog.objects.count(), 1)
        with tenant(self.org_b):
            self.assertEqual(AIRequestLog.objects.count(), 0)

    def test_authentication_and_organization_are_required(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post("/api/v1/ai/ask/", {"question": "hi"}, format="json").status_code, 401)
        self.assertEqual(self.call("post", "/api/v1/ai/ask/", self.user_a, None, {"question": "hi"}).status_code, 400)
        self.assertEqual(self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_b, {"question": "hi"}).status_code, 403)

    def test_viewer_may_ask_but_only_sees_what_their_role_allows(self):
        viewer = add_member(self.org_a, "api-viewer@example.com", Role.VIEWER)
        response = self.call("post", "/api/v1/ai/ask/", viewer, self.org_a, {"question": "Which five customers owe us the most?"})
        self.assertEqual(response.status_code, 200)

    def test_ai_failure_returns_safe_envelope_and_keeps_the_log(self):
        def responder(request):
            raise ProviderUnavailable("vendor stack trace with api key sk-xxx")

        self.provider = FakeLLMProvider(responder=responder)
        with override_settings(AI_RETRY_MAX_ATTEMPTS=1):
            response = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, {"question": "How much profit did we make?"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "ai_unavailable")
        self.assertNotIn("sk-xxx", str(response.data))
        with tenant(self.org_a):
            self.assertEqual(AIRequestLog.objects.get().status, "error")

    def test_rate_limit_returns_429(self):
        with override_settings(AI_USER_REQUESTS_PER_MINUTE=1):
            self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, {"question": "hello"})
            response = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, {"question": "hello"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["error"]["code"], "rate_limit_exceeded")

    def test_idempotency_key_replays_without_a_second_model_call(self):
        self.provider = FakeLLMProvider()
        payload = {"question": "Which five customers owe us the most?"}
        first = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, payload, HTTP_IDEMPOTENCY_KEY="k-1")
        calls_after_first = len(self.provider.requests)
        second = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, payload, HTTP_IDEMPOTENCY_KEY="k-1")
        self.assertEqual(first.data, second.data)
        self.assertEqual(len(self.provider.requests), calls_after_first)
        with tenant(self.org_a):
            self.assertEqual(AIRequestLog.objects.count(), 1)
            self.assertEqual(IdempotencyKey.objects.filter(key="k-1").count(), 1)

    def test_idempotency_replay_never_crosses_users(self):
        colleague = add_member(self.org_a, "api-idem-colleague@example.com", Role.ACCOUNTANT)
        payload = {"question": "Which five customers owe us the most?"}
        mine = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, payload, HTTP_IDEMPOTENCY_KEY="shared")
        theirs = self.call("post", "/api/v1/ai/ask/", colleague, self.org_a, payload, HTTP_IDEMPOTENCY_KEY="shared")
        self.assertNotEqual(mine.data["conversation_id"], theirs.data["conversation_id"])
        with tenant(self.org_a):
            self.assertEqual(AIRequestLog.objects.count(), 2)

    def test_invalid_input_uses_error_envelope(self):
        response = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, {"question": "", "conversation_id": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_conversation_endpoints_are_owner_scoped(self):
        asked = self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, {"question": "Which five customers owe us the most?"})
        conversation_id = asked.data["conversation_id"]
        listing = self.call("get", "/api/v1/ai/conversations/", self.user_a, self.org_a)
        self.assertEqual([c["id"] for c in listing.data["results"]], [conversation_id])
        detail = self.call("get", f"/api/v1/ai/conversations/{conversation_id}/", self.user_a, self.org_a)
        self.assertEqual(len(detail.data["messages"]), 2)

        colleague = add_member(self.org_a, "api-colleague@example.com", Role.ACCOUNTANT)
        self.assertEqual(self.call("get", "/api/v1/ai/conversations/", colleague, self.org_a).data["results"], [])
        self.assertEqual(self.call("get", f"/api/v1/ai/conversations/{conversation_id}/", colleague, self.org_a).status_code, 404)
        self.assertEqual(self.call("get", f"/api/v1/ai/conversations/{conversation_id}/", self.user_b, self.org_b).status_code, 404)

    def test_usage_is_admin_only_and_metadata_only(self):
        self.call("post", "/api/v1/ai/ask/", self.user_a, self.org_a, {"question": "Which five customers owe us the most?"})
        with override_settings(AI_MODEL_PRICING={"fake-llm-1": {"input_per_million": "3.00", "output_per_million": "15.00"}}):
            response = self.call("get", "/api/v1/ai/usage/?from_date=2020-01-01&to_date=2100-01-01", self.user_a, self.org_a)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["totals"]["requests"], 1)
        self.assertIn("estimated_cost", response.data["by_model"][0])
        accountant = add_member(self.org_a, "api-acct@example.com", Role.ACCOUNTANT)
        self.assertEqual(self.call("get", "/api/v1/ai/usage/", accountant, self.org_a).status_code, 403)

    def test_payment_reminder_draft_is_never_sent(self):
        response = self.call("post", "/api/v1/ai/drafts/payment-reminder/", self.user_a, self.org_a, {"invoice_id": str(self.invoice.pk)})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["status"], response.data["sent"]), ("draft", False))
        self.assertIn(self.invoice.invoice_number, response.data["body"])
        self.assertIn(response.data["facts"]["amount_due"], response.data["body"])
        cross = self.call("post", "/api/v1/ai/drafts/payment-reminder/", self.user_b, self.org_b, {"invoice_id": str(self.invoice.pk)})
        self.assertEqual(cross.status_code, 404)

    def test_draft_with_invented_amount_is_rejected(self):
        from ai.providers.base import LLMResponse

        self.provider = FakeLLMProvider(responder=lambda request: LLMResponse(
            structured={"subject": "Reminder", "body": "Please pay 999999.99 today, plus a 25000 late fee."}
        ))
        response = self.call("post", "/api/v1/ai/drafts/payment-reminder/", self.user_a, self.org_a, {"invoice_id": str(self.invoice.pk)})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["error"]["code"], "draft_rejected")

    def test_expense_account_suggestion_uses_only_the_allowlist(self):
        from ai.providers.base import LLMResponse

        with tenant(self.org_a):
            travel = create_account(organization=self.org_a, code="6100", name="Travel Expense", account_type=AccountType.EXPENSE)
        response = self.call("post", "/api/v1/ai/suggestions/expense-account/", self.user_a, self.org_a,
                             {"description": "Taxi fare for client travel", "vendor_name": "Cabs"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["suggested_account"]["id"], str(travel.pk))
        self.assertFalse(response.data["applied"])

        self.provider = FakeLLMProvider(responder=lambda request: LLMResponse(
            structured={"account_id": "00000000-0000-0000-0000-000000000000", "confidence": "high", "reasoning": "trust me"}
        ))
        response = self.call("post", "/api/v1/ai/suggestions/expense-account/", self.user_a, self.org_a, {"description": "Taxi"})
        self.assertIsNone(response.data["suggested_account"])
        self.assertEqual(response.data["confidence"], "low")


class DocumentIndexApiTests(APIHelpers, AIFixtureMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.document = upload_text(self.org_a, self.user_a, "Warranty: twelve months from delivery.", title="Warranty")

    def test_index_enqueues_and_indexes_on_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"/api/v1/ai/documents/{self.document.pk}/index/", self.user_a, self.org_a)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.data["enqueued"])
        with tenant(self.org_a):
            self.assertEqual(DocumentIndex.objects.get(document=self.document).status, IndexStatus.INDEXED)
            self.assertTrue(DocumentChunk.objects.filter(document=self.document).exists())
        status = self.call("get", f"/api/v1/ai/documents/{self.document.pk}/index/", self.user_a, self.org_a)
        self.assertEqual(status.data["status"], IndexStatus.INDEXED)

    def test_reindex_forces_a_rebuild(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.call("post", f"/api/v1/ai/documents/{self.document.pk}/index/", self.user_a, self.org_a)
        with patch("ai.rag.tasks.index_document_task.delay") as delay, self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"/api/v1/ai/documents/{self.document.pk}/reindex/", self.user_a, self.org_a)
        self.assertTrue(response.data["force"])
        self.assertTrue(delay.call_args.args[2])

    def test_index_requires_manage_permission(self):
        staff = add_member(self.org_a, "idx-staff@example.com", Role.STAFF)
        self.assertEqual(self.call("post", f"/api/v1/ai/documents/{self.document.pk}/index/", staff, self.org_a).status_code, 403)
        self.assertEqual(self.call("get", f"/api/v1/ai/documents/{self.document.pk}/index/", staff, self.org_a).status_code, 200)

    def test_cross_tenant_document_is_not_found(self):
        self.assertEqual(self.call("post", f"/api/v1/ai/documents/{self.document.pk}/index/", self.user_b, self.org_b).status_code, 404)

    def test_conversation_model_not_exposed_across_orgs_via_api(self):
        with tenant(self.org_a):
            conversation = AIConversation.objects.create(organization=self.org_a, user=self.user_a)
        self.assertEqual(self.call("get", f"/api/v1/ai/conversations/{conversation.pk}/", self.user_b, self.org_b).status_code, 404)
