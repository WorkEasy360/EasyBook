"""Ask Books orchestration: routing, grounding, citations, limits, failures."""

import datetime
import json
import threading
from decimal import Decimal

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from ai.models import AIMessage, AIRequestLog
from ai.orchestration import router
from ai.orchestration.citations import SourceRegistry, strip_unknown_inline_ids, validate_citations
from ai.orchestration.errors import AskBooksError
from ai.orchestration.guards import leaks_prompt, sanitize_markdown, ungrounded_numbers
from ai.orchestration.service import AskBooksService, resolve_periods
from ai.prompts import SYSTEM_POLICY
from ai.providers import override_llm_provider
from ai.providers.base import LLMResponse, ToolCall
from ai.providers.errors import (
    ProviderContextLimitExceeded,
    ProviderMalformedOutput,
    ProviderTimeout,
    ProviderUnavailable,
)
from ai.providers.fake import FakeLLMProvider
from ai.sources import Source
from ai.tests.base import AIFixtureMixin, add_member, index, tenant, upload_text
from ai.tests.test_tools import AS_OF, ToolFixtureMixin
from authz.roles import Role
from reports.selectors.pnl import get_profit_and_loss

TODAY = datetime.date(2026, 4, 30)


def scripted(route_intent=None, plan=None, synthesis=None):
    """A FakeLLMProvider whose route/plan/synthesis steps can each be
    overridden; unspecified steps use the default deterministic fake."""
    default = FakeLLMProvider()

    def responder(request):
        if request.purpose == "route" and route_intent is not None:
            return LLMResponse(structured={"intent": route_intent})
        if request.purpose == "tool_planning" and plan is not None:
            return plan(request)
        if request.purpose == "synthesis" and synthesis is not None:
            return synthesis(request)
        default.responder = None
        return default.generate(request)

    return FakeLLMProvider(responder=responder)


class AskFixture(ToolFixtureMixin):
    def setUp(self):
        super().setUp()
        cache.clear()

    def ask(self, question, provider=None, user=None, organization=None, today=AS_OF, **kwargs):
        # Errors are caught INSIDE the tenant block and re-raised outside it,
        # mirroring the API view (which returns the error response): letting
        # the exception cross tenant_context's atomic block would roll back
        # the AIRequestLog row that records the failure.
        organization = organization or self.org_a
        error = None
        with tenant(organization, user or self.user_a), override_llm_provider(provider or FakeLLMProvider()):
            try:
                return AskBooksService(today=today).answer(
                    user=user or self.user_a, organization=organization, question=question, request_id="req-test", **kwargs
                )
            except AskBooksError as exc:
                error = exc
        raise error


class RoutingGuardrailTests(SimpleTestCase):
    def test_numeric_questions_are_never_rag_only(self):
        self.assertEqual(router.apply_guardrails(router.RAG, question="How much profit did we make?", has_document_scope=False)[0], router.BOTH)
        self.assertEqual(router.apply_guardrails(router.CONVERSATIONAL, question="What is our cash position?", has_document_scope=False)[0], router.STRUCTURED)

    def test_model_failure_falls_back_to_signals(self):
        self.assertEqual(router.apply_guardrails(None, question="What does the contract say about termination?", has_document_scope=False), (router.RAG, "fallback"))
        self.assertEqual(router.apply_guardrails("nonsense", question="hello there", has_document_scope=False)[0], router.CONVERSATIONAL)

    def test_document_scope_forces_retrieval(self):
        self.assertEqual(router.apply_guardrails(router.STRUCTURED, question="Summarize", has_document_scope=True)[0], router.BOTH)

    def test_model_classification_is_respected_when_consistent(self):
        self.assertEqual(router.apply_guardrails(router.STRUCTURED, question="How are we doing?", has_document_scope=False), (router.STRUCTURED, "model"))

    def test_document_signals_upgrade_a_conversational_classification(self):
        self.assertEqual(router.apply_guardrails(router.CONVERSATIONAL, question="What does the lease say about parking?", has_document_scope=False), (router.RAG, "guardrail"))


class GuardUnitTests(SimpleTestCase):
    def test_ungrounded_numbers(self):
        allowed = [json.dumps({"net_profit": "1180.00", "as_of": "2026-06-30"})]
        self.assertEqual(ungrounded_numbers("Net profit was 1,180.00 as of 2026-06-30 for 3 invoices.", allowed), [])
        self.assertEqual(ungrounded_numbers("Net profit was 2,360.00.", allowed), ["2360"])
        self.assertEqual(ungrounded_numbers("Up 12.5% on last year.", allowed), ["12.5"])

    def test_prompt_leak_detection(self):
        self.assertTrue(leaks_prompt("Sure: " + SYSTEM_POLICY[:400], [SYSTEM_POLICY]))
        self.assertFalse(leaks_prompt("Your net profit is shown in the report.", [SYSTEM_POLICY]))

    def test_markdown_sanitization(self):
        self.assertEqual(sanitize_markdown('Hi <script>alert(1)</script>[x](javascript:alert(1))'), "Hi alert(1)[x]()")

    def test_citation_validation_rejects_fabricated_ids(self):
        registry = SourceRegistry()
        registry.add(Source(source_id="report:pnl:1", type="report", label="P&L"))
        valid, rejected = validate_citations(["report:pnl:1", "invoice:00000000-0000-0000-0000-000000000000", 7], registry)
        self.assertEqual([s.source_id for s in valid], ["report:pnl:1"])
        self.assertEqual(len(rejected), 2)
        text, removed = strip_unknown_inline_ids("See [report:pnl:1] and [chunk:fake-id].", registry)
        self.assertEqual(text, "See [report:pnl:1] and .")
        self.assertEqual(removed, ["chunk:fake-id"])


class PeriodTests(TestCase):
    def test_periods(self):
        from core.tests.factories import make_org_with_owner

        org, _, _ = make_org_with_owner("P", "periods@example.com")
        with tenant(org):
            periods = resolve_periods(datetime.date(2026, 5, 15), org)
        self.assertEqual(periods["this_month"], {"from_date": "2026-05-01", "to_date": "2026-05-15"})
        self.assertEqual(periods["last_month"], {"from_date": "2026-04-01", "to_date": "2026-04-30"})
        self.assertEqual(periods["this_quarter"]["from_date"], "2026-04-01")
        self.assertEqual(periods["last_quarter"], {"from_date": "2026-01-01", "to_date": "2026-03-31"})


class AskBooksStructuredTests(AskFixture, TestCase):
    def test_numeric_question_uses_structured_tool_and_preserves_values(self):
        result = self.ask("How much profit did we make this month?", today=TODAY)
        self.assertEqual(result.intent, "structured")
        self.assertEqual(result.status, "answered")
        tool = result.structured_data["tool_results"][0]
        self.assertEqual(tool["tool"], "get_profit_and_loss")
        with tenant(self.org_a):
            expected = get_profit_and_loss(organization=self.org_a, from_date=datetime.date(2026, 4, 1), to_date=TODAY)
        self.assertEqual(Decimal(tool["summary"]["net_profit"]), expected["totals"]["net_profit"])
        self.assertEqual([s["type"] for s in result.sources], ["report"])
        self.assertIn(tool["summary"]["net_profit"], result.answer)

    def test_numeric_question_does_not_touch_documents(self):
        document = upload_text(self.org_a, self.user_a, "Our profit was 999999 according to this memo.", title="Memo")
        index(self.org_a, document)
        provider = FakeLLMProvider()
        result = self.ask("How much profit did we make this month?", provider=provider, today=TODAY)
        self.assertEqual(result.intent, "structured")
        self.assertFalse(any(message.documents for request in provider.requests for message in request.messages))
        self.assertNotIn("999999", result.answer)

    def test_top_customers_question(self):
        result = self.ask("Which five customers owe us the most?")
        rows = result.structured_data["tool_results"][0]["data"]["balances"]
        self.assertEqual(rows[0]["customer_name"], "Bengaluru Co")

    def test_invoice_lookup_question_cites_the_invoice(self):
        result = self.ask(f"Find invoice {self.invoice.invoice_number}")
        self.assertEqual(result.sources[0]["type"], "invoice")
        self.assertEqual(result.sources[0]["id"], str(self.invoice.pk))

    def test_missing_data_is_reported_not_invented(self):
        result = self.ask("Find invoice INV-DOES-NOT-EXIST")
        self.assertEqual(result.status, "no_data")
        self.assertEqual(result.sources, [])
        self.assertIn("could not find", result.answer)

    def test_fabricated_citation_is_rejected_and_real_sources_attached(self):
        def synthesis(request):
            return LLMResponse(structured={"answer": "Here you go [invoice:11111111-1111-1111-1111-111111111111].",
                                           "citations": ["report:made:up", "invoice:11111111-1111-1111-1111-111111111111"]})

        result = self.ask("How much profit did we make this month?", provider=scripted(synthesis=synthesis), today=TODAY)
        self.assertIn("unverified_citations_removed", result.warnings)
        self.assertNotIn("11111111", result.answer)
        self.assertTrue(all(s["source_id"].startswith("report:profit_and_loss") for s in result.sources))

    def test_model_arithmetic_is_replaced_by_authoritative_figures(self):
        def synthesis(request):
            return LLMResponse(structured={"answer": "Net profit is 123456.78, up 17.3% year on year.", "citations": []})

        result = self.ask("How much profit did we make this month?", provider=scripted(synthesis=synthesis), today=TODAY)
        self.assertEqual(result.status, "partial")
        self.assertIn("ungrounded_figures_replaced", result.warnings)
        self.assertNotIn("123456.78", result.answer)
        net_profit = result.structured_data["tool_results"][0]["summary"]["net_profit"]
        self.assertIn(net_profit, result.answer)
        with tenant(self.org_a):
            self.assertEqual(AIRequestLog.objects.get().error_code, "ungrounded_numbers")

    def test_system_prompt_disclosure_is_blocked(self):
        def synthesis(request):
            return LLMResponse(structured={"answer": request.system, "citations": []})

        result = self.ask("How much profit did we make this month?", provider=scripted(synthesis=synthesis), today=TODAY)
        self.assertEqual(result.status, "refused")
        self.assertNotIn("Authoritative data", result.answer)

    def test_tool_loop_is_bounded(self):
        calls = {"n": 0}

        def plan(request):
            calls["n"] += 1
            return LLMResponse(tool_calls=tuple(
                ToolCall(id=f"c{calls['n']}-{i}", name="get_balance_sheet", arguments={"as_of_date": f"2026-06-{10 + (calls['n'] * 3 + i) % 18:02d}"})
                for i in range(3)
            ))

        with override_settings(AI_MAX_TOOL_CALLS_PER_REQUEST=4, AI_MAX_LLM_ROUNDS=10):
            result = self.ask("Show the balance sheet", provider=scripted(route_intent="structured", plan=plan))
        self.assertLessEqual(len(result.structured_data["tool_results"]), 4)
        self.assertLessEqual(calls["n"], 2)
        with tenant(self.org_a):
            self.assertLessEqual(AIRequestLog.objects.get().tool_call_count, 4)

    def test_rounds_are_bounded_even_with_repeated_identical_calls(self):
        calls = {"n": 0}

        def plan(request):
            calls["n"] += 1
            return LLMResponse(tool_calls=(ToolCall(id=f"c{calls['n']}", name="get_balance_sheet", arguments={}),))

        with override_settings(AI_MAX_LLM_ROUNDS=3):
            result = self.ask("Show the balance sheet", provider=scripted(route_intent="structured", plan=plan))
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(result.structured_data["tool_results"]), 1)

    def test_tools_the_user_cannot_use_are_not_offered_or_executed(self):
        staff = add_member(self.org_a, "ask-staff@example.com", Role.STAFF)
        seen = {}

        def plan(request):
            seen["tools"] = {tool.name for tool in request.tools}
            return LLMResponse(tool_calls=(ToolCall(id="x", name="get_project_profitability", arguments={}),))

        result = self.ask("Project margins?", provider=scripted(route_intent="structured", plan=plan), user=staff)
        self.assertNotIn("get_project_profitability", seen["tools"])
        self.assertNotIn("search_documents", seen["tools"])
        self.assertEqual(result.status, "no_data")
        self.assertIsNone(result.structured_data)

    def test_conversation_history_is_owner_scoped_and_persisted(self):
        first = self.ask("How much profit did we make this month?", today=TODAY)
        second = self.ask("And the balance sheet?", conversation_id=first.conversation_id, today=TODAY)
        self.assertEqual(first.conversation_id, second.conversation_id)
        with tenant(self.org_a):
            self.assertEqual(AIMessage.objects.filter(conversation_id=first.conversation_id).count(), 4)
        colleague = add_member(self.org_a, "ask-colleague@example.com", Role.ACCOUNTANT)
        with self.assertRaises(AskBooksError) as caught:
            self.ask("Continue", user=colleague, conversation_id=first.conversation_id)
        self.assertEqual(caught.exception.status_code, 404)

    def test_telemetry_records_metadata_but_no_content(self):
        self.ask("How much profit did we make this month?", today=TODAY)
        with tenant(self.org_a):
            log = AIRequestLog.objects.get()
        self.assertEqual((log.feature, log.status, log.intent), ("ask", "ok", "structured"))
        self.assertEqual(log.tool_names, ["get_profit_and_loss"])
        self.assertEqual(log.request_id, "req-test")
        self.assertGreater(log.llm_calls, 1)
        self.assertIsNotNone(log.input_tokens)
        stored = json.dumps({f.name: str(getattr(log, f.name)) for f in log._meta.fields})
        self.assertNotIn("profit did we make", stored)


class AskBooksFailureTests(AskFixture, TestCase):
    def assert_fails(self, error, code, status):
        def synthesis(request):
            raise error

        with self.assertRaises(AskBooksError) as caught:
            self.ask("How much profit did we make this month?", provider=scripted(synthesis=synthesis), today=TODAY)
        self.assertEqual((caught.exception.code, caught.exception.status_code), (code, status))
        self.assertNotIn("secret-internal", caught.exception.message)
        with tenant(self.org_a):
            self.assertEqual(AIRequestLog.objects.order_by("created_at").last().error_code, code)

    def test_provider_timeout(self):
        self.assert_fails(ProviderTimeout("secret-internal"), "ai_timeout", 504)

    def test_provider_unavailable(self):
        self.assert_fails(ProviderUnavailable("secret-internal"), "ai_unavailable", 503)

    def test_token_limit_exceeded(self):
        self.assert_fails(ProviderContextLimitExceeded("secret-internal"), "context_limit_exceeded", 413)

    def test_malformed_output(self):
        def synthesis(request):
            return LLMResponse(structured={"answer": 42})

        with self.assertRaises(AskBooksError) as caught:
            self.ask("How much profit did we make this month?", provider=scripted(synthesis=synthesis), today=TODAY)
        self.assertEqual(caught.exception.code, "ai_unavailable")
        self.assert_fails(ProviderMalformedOutput("secret-internal"), "ai_unavailable", 503)

    def test_hung_provider_times_out(self):
        release = threading.Event()

        def synthesis(request):
            release.wait(5)
            return LLMResponse(structured={"answer": "late", "citations": []})

        with override_settings(AI_LLM_TIMEOUT_SECONDS=0.2, AI_RETRY_MAX_ATTEMPTS=1), self.assertRaises(AskBooksError) as caught:
            self.ask("How much profit did we make this month?", provider=scripted(synthesis=synthesis), today=TODAY)
        release.set()
        self.assertEqual(caught.exception.code, "ai_timeout")

    def test_transient_failure_is_retried_boundedly(self):
        attempts = {"n": 0}

        def synthesis(request):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ProviderUnavailable("blip")
            return LLMResponse(structured={"answer": "Done.", "citations": []})

        result = self.ask("How much profit did we make this month?", provider=scripted(synthesis=synthesis), today=TODAY)
        self.assertEqual(result.answer, "Done.")
        self.assertEqual(attempts["n"], 3)

    def test_routing_failure_falls_back_instead_of_failing(self):
        def responder(request):
            if request.purpose == "route":
                raise ProviderUnavailable("router down")
            fallback = FakeLLMProvider()
            return fallback.generate(request)

        with override_settings(AI_RETRY_MAX_ATTEMPTS=1):
            result = self.ask("How much profit did we make this month?", provider=FakeLLMProvider(responder=responder), today=TODAY)
        self.assertEqual(result.intent, "structured")

    def test_question_too_long(self):
        with override_settings(AI_MAX_QUESTION_CHARS=10), self.assertRaises(AskBooksError) as caught:
            self.ask("How much profit did we make this month?")
        self.assertEqual(caught.exception.code, "context_limit_exceeded")

    def test_user_rate_limit(self):
        with override_settings(AI_USER_REQUESTS_PER_MINUTE=2):
            self.ask("hello")
            self.ask("hello")
            with self.assertRaises(AskBooksError) as caught:
                self.ask("hello")
        self.assertEqual((caught.exception.code, caught.exception.status_code), ("rate_limit_exceeded", 429))
        with tenant(self.org_a):
            self.assertEqual(AIRequestLog.objects.filter(status="rate_limited").count(), 1)

    def test_org_monthly_token_limit(self):
        with override_settings(AI_ORG_MONTHLY_TOKEN_LIMIT=1):
            self.ask("hello")
            with self.assertRaises(AskBooksError) as caught:
                self.ask("hello")
        self.assertEqual(caught.exception.code, "ai_quota_exceeded")

    def test_disabled_feature_and_missing_permission(self):
        with override_settings(AI_ASK_BOOKS_ENABLED=False), self.assertRaises(AskBooksError) as caught:
            self.ask("hello")
        self.assertEqual(caught.exception.code, "ai_disabled")
        with self.assertRaises(AskBooksError) as caught:
            self.ask("hello", user=self.user_b)  # not a member of org A
        self.assertEqual(caught.exception.code, "forbidden")

    def test_missing_tenant_context_fails_closed(self):
        with override_llm_provider(FakeLLMProvider()), self.assertRaises(AskBooksError) as caught:
            AskBooksService().answer(user=self.user_a, organization=self.org_a, question="profit?")
        self.assertEqual(caught.exception.code, "organization_required")
        with tenant(self.org_b), override_llm_provider(FakeLLMProvider()), self.assertRaises(AskBooksError):
            AskBooksService().answer(user=self.user_a, organization=self.org_a, question="profit?")


class AskBooksDocumentTests(AIFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.contract = upload_text(
            self.org_a, self.user_a,
            "ABC SUPPLY AGREEMENT\n\nPayment is due within 45 days.\f"
            "CANCELLATION\n\nABC may cancel any purchase order with fifteen days written notice.",
            title="ABC Supply Agreement",
        )
        index(self.org_a, self.contract)

    def ask(self, question, provider=None, **kwargs):
        error = None
        with tenant(self.org_a, self.user_a), override_llm_provider(provider or FakeLLMProvider()):
            try:
                return AskBooksService(today=TODAY).answer(user=self.user_a, organization=self.org_a, question=question, request_id="r", **kwargs)
            except AskBooksError as exc:
                error = exc
        raise error

    def test_document_question_uses_rag_and_cites_page(self):
        result = self.ask("What does the contract with ABC say about cancellation?")
        self.assertEqual(result.intent, "rag")
        self.assertIsNone(result.structured_data)
        self.assertEqual(result.sources[0]["type"], "document")
        self.assertEqual(result.sources[0]["document_id"], str(self.contract.pk))
        self.assertEqual(result.sources[0]["page"], 2)
        self.assertEqual(result.sources[0]["section"], "CANCELLATION")
        self.assertIn("fifteen days", result.answer)

    def test_no_relevant_document_is_not_forced(self):
        result = self.ask("What does the lease say about parking bays for bicycles?")
        self.assertEqual(result.status, "no_data")
        self.assertEqual(result.sources, [])

    def test_document_scoped_summary_uses_excerpts(self):
        result = self.ask("Summarize this", document_id=self.contract.pk)
        self.assertIn(result.intent, ("rag", "both"))
        self.assertTrue(all(s["document_id"] == str(self.contract.pk) for s in result.sources))

    def test_combined_question_runs_tools_then_documents(self):
        provider = FakeLLMProvider()
        result = self.ask("What does the ABC agreement say about payment and what are our overdue invoices?", provider=provider)
        self.assertEqual(result.intent, "both")
        self.assertEqual(result.structured_data["tool_results"][0]["tool"], "get_overdue_invoices")
        self.assertTrue(any(s["type"] == "document" for s in result.sources))
        planning = [r for r in provider.requests if r.purpose == "tool_planning"]
        synthesis = [r for r in provider.requests if r.purpose == "synthesis"]
        self.assertTrue(planning and synthesis)
        self.assertFalse(any(m.documents for r in planning for m in r.messages))
        self.assertTrue(all(r.tools == () for r in synthesis))

    def test_cross_tenant_document_scope_is_not_found(self):
        other = upload_text(self.org_b, self.user_b, "Org B secret contract", title="B")
        with self.assertRaises(AskBooksError) as caught:
            self.ask("Summarize this", document_id=other.pk)
        self.assertEqual(caught.exception.status_code, 404)
