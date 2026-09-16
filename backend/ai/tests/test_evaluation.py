"""Evaluation gates (phase sections 58-60, 83).

Retrieval: the curated corpus through real ingestion + retrieval.
Answers: financial grounding compares STRUCTURED values returned by Ask Books
against the authoritative selector — never words in prose.
"""

import datetime
import json
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase

from ai.evaluations.runner import evaluate_retrieval, load_corpus
from ai.orchestration.service import AskBooksService
from ai.providers import override_llm_provider
from ai.providers.fake import FakeLLMProvider
from ai.tests.base import AITestsBase, tenant
from ai.tests.test_tools import AS_OF, ToolFixtureMixin
from compliance.tests.base import PERIOD_END, PERIOD_START
from reports.selectors.balance_sheet import get_balance_sheet
from reports.selectors.inventory import get_inventory_valuation
from reports.selectors.params import to_json_safe
from reports.selectors.payables import get_ap_ageing
from reports.selectors.pnl import get_profit_and_loss
from reports.selectors.receivables import get_ar_ageing
from reports.selectors.tax import get_gst_summary


class RetrievalEvaluationTests(AITestsBase):
    def test_retrieval_quality_gates(self):
        organizations = {"A": self.org_a, "B": self.org_b}
        keys = load_corpus(organizations=organizations, users={"A": self.user_a, "B": self.user_b})
        report = evaluate_retrieval(organizations=organizations, document_keys=keys, k=5)
        detail = json.dumps(report, indent=1)

        # Hard gates: zero tolerance.
        self.assertEqual(report["forbidden_hits"], [], detail)
        self.assertEqual(report["negative_leaks"], [], detail)
        self.assertEqual(report["hit_rate_by_category"]["exact"], 1.0, detail)
        self.assertEqual(report["hit_rate_by_category"]["tenant"], 1.0, detail)
        # Baseline floor with the fake embedding; raise when re-baselined.
        self.assertGreaterEqual(report["recall_at_k"], 0.9, detail)


class FinancialGroundingEvaluationTests(ToolFixtureMixin, TestCase):
    """Ask in natural language; assert the structured value equals the
    deterministic service value exactly."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def ask(self, question, today):
        with tenant(self.org_a, self.user_a), override_llm_provider(FakeLLMProvider()):
            return AskBooksService(today=today).answer(user=self.user_a, organization=self.org_a, question=question)

    def tool_summary(self, result, tool):
        matches = [r for r in result.structured_data["tool_results"] if r["tool"] == tool]
        self.assertTrue(matches, f"{tool} was not used: {result.structured_data}")
        return matches[0]["summary"]

    def test_net_profit_equals_reports_service(self):
        result = self.ask("What is net profit this month?", today=PERIOD_END)
        summary = self.tool_summary(result, "get_profit_and_loss")
        with tenant(self.org_a):
            expected = get_profit_and_loss(organization=self.org_a, from_date=PERIOD_START, to_date=PERIOD_END)
        self.assertEqual(Decimal(summary["net_profit"]), expected["totals"]["net_profit"])
        self.assertEqual(result.sources[0]["source_id"], f"report:profit_and_loss:{PERIOD_START}:{PERIOD_END}")

    def test_gst_figures_equal_compliance_service(self):
        result = self.ask("What is GST payable this month?", today=PERIOD_END)
        summary = self.tool_summary(result, "get_gst_summary")
        with tenant(self.org_a):
            expected = to_json_safe(get_gst_summary(organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END))
        self.assertEqual(summary["output_tax"], expected["output_tax"])
        self.assertEqual(summary["input_tax"], expected["input_tax"])
        # EasyBook computes no net payable figure, so none may appear.
        self.assertIn("not computed", summary["net_gst_payable"])

    def test_stock_value_equals_inventory_selector(self):
        result = self.ask("How much stock do we have?", today=AS_OF)
        summary = self.tool_summary(result, "get_inventory_valuation")
        with tenant(self.org_a):
            expected = get_inventory_valuation(organization=self.org_a)
        self.assertEqual(Decimal(summary["total_value"]), expected["total_value"])

    def test_receivables_and_payables_equal_selectors(self):
        ap = self.tool_summary(self.ask("How much do we owe vendors?", today=AS_OF), "get_ap_ageing")
        overdue = self.ask("Show invoices overdue more than 30 days", today=AS_OF)
        with tenant(self.org_a):
            expected_ap = get_ap_ageing(organization=self.org_a, as_of=AS_OF)
            expected_ar = get_ar_ageing(organization=self.org_a, as_of=AS_OF)
        self.assertEqual(Decimal(ap["grand_total"]), expected_ap["grand_total"])
        rows = overdue.structured_data["tool_results"][0]["data"]["invoices"]
        expected_rows = {r["invoice_number"]: r["amount_due"] for b in expected_ar["buckets"].values() for r in b if r["days_overdue"] >= 30}
        self.assertEqual({r["invoice_number"]: Decimal(r["amount_due"]) for r in rows}, expected_rows)

    def test_balance_sheet_equals_reports_service(self):
        result = self.ask("Explain my Balance Sheet", today=AS_OF)
        summary = self.tool_summary(result, "get_balance_sheet")
        with tenant(self.org_a):
            expected = get_balance_sheet(organization=self.org_a, as_of_date=AS_OF)
        self.assertEqual(Decimal(summary["total_assets"]), expected["totals"]["total_assets"])
        self.assertEqual(summary["is_balanced"], expected["is_balanced"])

    def test_quarter_comparison_uses_report_variance(self):
        result = self.ask("Compare revenue this quarter with last quarter.", today=datetime.date(2026, 6, 30))
        summary = self.tool_summary(result, "get_profit_and_loss")
        with tenant(self.org_a):
            expected = get_profit_and_loss(
                organization=self.org_a, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 6, 30),
                comparison_from=datetime.date(2026, 1, 1), comparison_to=datetime.date(2026, 3, 31),
            )
        self.assertEqual(Decimal(summary["revenue"]), expected["totals"]["revenue"])
        self.assertEqual(summary["revenue_variance_percent"], expected["variance"]["revenue"]["variance_percent"])

    def test_missing_data_is_not_fabricated(self):
        result = self.ask("Find invoice INV-424242", today=AS_OF)
        self.assertEqual(result.status, "no_data")
        self.assertNotIn("424242", "".join(s["label"] for s in result.sources))
