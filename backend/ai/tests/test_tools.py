"""Structured tools: exact pass-through of existing selectors, authorization
at every call, strict input validation, cross-tenant rejection, read-only."""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import Membership
from ai.tests.base import add_member
from ai.tools.base import Tool, ToolContext, ToolResult
from ai.tools.executor import execute_tool, shape_result
from ai.tools.registry import _REGISTRY, all_tools, register
from ai.tools.schemas import EmptyArgs
from audit.models import AuditLog
from authz.roles import Permission, Role
from banking.services.bank_accounts import create_bank_account
from compliance.tests.base import PERIOD_END, PERIOD_START, ComplianceFixtureMixin
from core.tenancy import tenant_context
from inventory.models.stock_movement import MovementType
from inventory.services.movements import record_stock_movement
from items.models.item import ItemType
from items.services.items import create_item
from reports.selectors.balance_sheet import get_balance_sheet
from reports.selectors.cash_flow import get_cash_flow_statement
from reports.selectors.inventory import get_inventory_valuation
from reports.selectors.params import to_json_safe
from reports.selectors.payables import get_ap_ageing
from reports.selectors.pnl import get_profit_and_loss
from reports.selectors.receivables import get_ar_ageing
from reports.selectors.tax import get_gst_summary
from sales.selectors import get_invoice_amount_due

AS_OF = datetime.date(2026, 6, 30)


class ToolFixtureMixin(ComplianceFixtureMixin):
    def setUp(self):
        super().setUp()
        self.invoice = self.make_invoice(self.customer_mh)
        self.invoice_ka = self.make_invoice(self.customer_ka, unit_price=Decimal("2500.00"))
        self.bill = self.make_bill()
        with tenant_context(organization_id=self.org_a.id):
            self.bank_gl = create_account(organization=self.org_a, code="1010", name="Bank", account_type=AccountType.ASSET)
            create_bank_account(organization=self.org_a, name="Current Account", account=self.bank_gl, currency=self.currency)
            self.widget = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit, sku="WID-1",
                track_inventory=True, reorder_level=Decimal("50"),
            )
            record_stock_movement(
                organization=self.org_a, item=self.widget, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
                quantity=Decimal("12"), unit_cost=Decimal("7.50"), movement_date=timezone.now(),
            )

    def ctx(self, user=None, organization=None):
        return ToolContext(user=user or self.user_a, organization=organization or self.org_a, today=AS_OF)

    def run_tool(self, name, arguments, user=None):
        with tenant_context(organization_id=self.org_a.id):
            return execute_tool(self.ctx(user), name, arguments)


class StructuredToolResultTests(ToolFixtureMixin, TestCase):
    """Each tool's figures must equal the existing selector's figures exactly."""

    def test_profit_and_loss_preserves_report_values(self):
        result = self.run_tool("get_profit_and_loss", {"from_date": "2026-04-01", "to_date": "2026-04-30"})
        with tenant_context(organization_id=self.org_a.id):
            expected = get_profit_and_loss(organization=self.org_a, from_date=PERIOD_START, to_date=PERIOD_END)["totals"]
        self.assertEqual(result.status, "ok")
        payload = result.to_payload()
        for key, value in expected.items():
            self.assertEqual(payload["summary"][key], str(value), key)
        self.assertEqual(Decimal(payload["summary"]["revenue"]), Decimal("3500.00"))
        self.assertEqual(result.sources[0].type, "report")
        self.assertIn("/api/v1/reports/profit-loss/", result.sources[0].route)

    def test_balance_sheet_preserves_report_values(self):
        result = self.run_tool("get_balance_sheet", {"as_of_date": "2026-06-30"})
        with tenant_context(organization_id=self.org_a.id):
            expected = get_balance_sheet(organization=self.org_a, as_of_date=AS_OF)
        summary = result.to_payload()["summary"]
        for key, value in expected["totals"].items():
            self.assertEqual(summary[key], str(value))
        self.assertEqual(summary["is_balanced"], expected["is_balanced"])

    def test_cash_flow_preserves_report_values(self):
        result = self.run_tool("get_cash_flow", {"from_date": "2026-04-01", "to_date": "2026-06-30"})
        with tenant_context(organization_id=self.org_a.id):
            expected = get_cash_flow_statement(organization=self.org_a, from_date=PERIOD_START, to_date=AS_OF)
        summary = result.to_payload()["summary"]
        self.assertEqual(summary["closing_cash"], str(expected["closing_cash"]))
        self.assertEqual(summary["net_change_in_cash"], str(expected["net_change_in_cash"]))

    def test_ar_and_ap_ageing_preserve_report_values(self):
        ar = self.run_tool("get_ar_ageing", {"as_of_date": "2026-06-30"}).to_payload()["summary"]
        ap = self.run_tool("get_ap_ageing", {"as_of_date": "2026-06-30"}).to_payload()["summary"]
        with tenant_context(organization_id=self.org_a.id):
            expected_ar = get_ar_ageing(organization=self.org_a, as_of=AS_OF)
            expected_ap = get_ap_ageing(organization=self.org_a, as_of=AS_OF)
        self.assertEqual(ar["grand_total"], str(expected_ar["grand_total"]))
        self.assertEqual(ap["grand_total"], str(expected_ap["grand_total"]))
        self.assertEqual(ar["bucket_31-60"], str(expected_ar["totals"]["31-60"]))

    def test_overdue_invoices_filters_by_days_without_summing(self):
        result = self.run_tool("get_overdue_invoices", {"as_of_date": "2026-06-30", "min_days_overdue": 30}).to_payload()
        self.assertEqual(result["summary"]["matching_invoice_count"], 2)
        self.assertNotIn("total_amount_due", result["summary"])
        self.assertTrue(all(row["days_overdue"] >= 30 for row in result["data"]["invoices"]))
        none = self.run_tool("get_overdue_invoices", {"as_of_date": "2026-06-30", "min_days_overdue": 365}).to_payload()
        self.assertEqual(none["summary"]["matching_invoice_count"], 0)

    def test_customer_balances_largest_first(self):
        rows = self.run_tool("get_customer_balances", {"limit": 5, "order": "balance_desc"}).to_payload()["data"]["balances"]
        self.assertEqual(rows[0]["customer_name"], "Bengaluru Co")
        self.assertGreater(Decimal(rows[0]["balance"]), Decimal(rows[1]["balance"]))

    def test_inventory_valuation_preserves_selector_values(self):
        summary = self.run_tool("get_inventory_valuation", {}).to_payload()["summary"]
        with tenant_context(organization_id=self.org_a.id):
            expected = get_inventory_valuation(organization=self.org_a)
        self.assertEqual(summary["total_value"], str(expected["total_value"]))
        self.assertEqual(Decimal(summary["total_value"]), Decimal("90.00"))
        low = self.run_tool("get_low_stock", {}).to_payload()
        self.assertEqual(low["data"]["items"][0]["item_sku"], "WID-1")

    def test_gst_summary_preserves_compliance_values_and_invents_no_net_payable(self):
        summary = self.run_tool("get_gst_summary", {"from_date": "2026-04-01", "to_date": "2026-04-30"}).to_payload()["summary"]
        with tenant_context(organization_id=self.org_a.id):
            expected = to_json_safe(get_gst_summary(organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END))
        self.assertEqual(summary["output_tax"], expected["output_tax"])
        self.assertEqual(summary["input_tax"], expected["input_tax"])
        self.assertIn("not computed", summary["net_gst_payable"])

    def test_invoice_lookup_by_number_and_id(self):
        by_number = self.run_tool("get_invoice", {"invoice_number": self.invoice.invoice_number.lower()})
        by_id = self.run_tool("get_invoice", {"invoice_id": str(self.invoice.pk)})
        with tenant_context(organization_id=self.org_a.id):
            amount_due = get_invoice_amount_due(invoice=self.invoice)
        for result in (by_number, by_id):
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.to_payload()["summary"]["amount_due"], str(amount_due))
            self.assertEqual(result.sources[0].source_id, f"invoice:{self.invoice.pk}")

    def test_bill_lookup(self):
        result = self.run_tool("get_bill", {"bill_number": self.bill.bill_number})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.to_payload()["summary"]["total"], str(self.bill.total))
        self.assertEqual(self.run_tool("get_bill", {"bill_number": "NOPE-1"}).status, "not_found")

    def test_cash_flow_without_bank_account_fails_safely(self):
        with tenant_context(organization_id=self.org_b.id):
            result = execute_tool(self.ctx(self.user_b, self.org_b), "get_cash_flow", {"from_date": "2026-04-01", "to_date": "2026-04-30"})
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "cash_accounts_not_configured")


class ToolSecurityTests(ToolFixtureMixin, TestCase):
    def test_every_tool_enforces_its_declared_permission(self):
        staff = add_member(self.org_a, "ai-staff@example.com", Role.STAFF)
        result = self.run_tool("get_project_profitability", {}, user=staff)  # needs VIEW_ALL_TIMESHEETS
        self.assertEqual((result.status, result.error_code), ("error", "forbidden"))
        self.assertEqual(result.data, {})
        self.assertEqual(self.run_tool("get_invoice", {"invoice_id": str(self.invoice.pk)}, user=staff).status, "ok")

    def test_inactive_membership_is_rejected_at_the_tool(self):
        viewer = add_member(self.org_a, "ai-viewer@example.com", Role.VIEWER)
        with tenant_context(organization_id=self.org_a.id):
            Membership.objects.filter(user=viewer).update(is_active=False)
        self.assertEqual(self.run_tool("get_balance_sheet", {}, user=viewer).error_code, "forbidden")

    def test_user_from_another_organization_is_rejected(self):
        self.assertEqual(self.run_tool("get_balance_sheet", {}, user=self.user_b).error_code, "forbidden")

    def test_cross_tenant_record_ids_resolve_to_not_found(self):
        with tenant_context(organization_id=self.org_b.id):
            result = execute_tool(self.ctx(self.user_b, self.org_b), "get_invoice", {"invoice_id": str(self.invoice.pk)})
        self.assertEqual(result.status, "not_found")
        self.assertNotIn(self.invoice.invoice_number, str(result.to_payload()))

    def test_missing_or_mismatched_tenant_context_fails_closed(self):
        self.assertEqual(execute_tool(self.ctx(), "get_balance_sheet", {}).error_code, "tenant_context_mismatch")
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(execute_tool(self.ctx(), "get_balance_sheet", {}).error_code, "tenant_context_mismatch")

    def test_invalid_parameters_are_rejected(self):
        cases = [
            ("get_profit_and_loss", {"from_date": "2026-13-01", "to_date": "2026-04-30"}),
            ("get_profit_and_loss", {"from_date": "2026-05-01", "to_date": "2026-04-30"}),
            ("get_profit_and_loss", {"from_date": "2000-01-01", "to_date": "2026-04-30"}),
            ("get_profit_and_loss", {"from_date": "2026-04-01"}),
            ("get_balance_sheet", {"organization_id": str(self.org_b.id)}),
            ("get_customer_balances", {"limit": 10_000}),
            ("get_customer_balances", {"order": "DROP TABLE"}),
            ("get_invoice", {"invoice_number": "INV-1", "invoice_id": str(self.invoice.pk)}),
            ("get_invoice", {"invoice_id": "not-a-uuid"}),
            ("get_balance_sheet", "not an object"),
        ]
        for name, arguments in cases:
            result = self.run_tool(name, arguments)
            self.assertEqual((result.status, result.error_code), ("error", "invalid_arguments"), (name, arguments))
            self.assertNotIn("DROP TABLE", result.message)

    def test_unknown_tool_is_rejected(self):
        self.assertEqual(self.run_tool("run_sql", {"query": "select 1"}).error_code, "unknown_tool")

    def test_registry_is_read_only_and_has_no_sql_or_mutation_tools(self):
        forbidden_words = ("post", "create", "record", "pay", "reconcile", "submit", "file", "send", "delete",
                           "update", "void", "sql", "query", "execute", "adjust", "transfer", "issue")
        for tool in all_tools():
            self.assertTrue(tool.read_only, tool.name)
            self.assertTrue(tool.required_permissions, tool.name)
            self.assertTrue(tool.name.startswith(("get_", "search_")), tool.name)
            for word in forbidden_words:
                self.assertNotIn(word, tool.name.split("_")[1:], tool.name)
            schema = tool.definition().input_schema
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("organization_id", schema["properties"])
        with self.assertRaises(ValueError):
            register(Tool(name="post_journal", description="", required_permissions=("x",), input_serializer=EmptyArgs,
                          handler=lambda ctx, args: None, read_only=False))

    def test_handler_writes_are_always_rolled_back(self):
        def sneaky_handler(ctx, args):
            AuditLog.objects.create(organization=ctx.organization, action="update", object_type="x", object_id="1")
            return ToolResult(tool="get_sneaky", status="ok")

        tool = Tool(name="get_sneaky", description="", required_permissions=(Permission.VIEW_REPORTS,),
                    input_serializer=EmptyArgs, handler=sneaky_handler)
        _REGISTRY[tool.name] = tool
        try:
            self.assertEqual(self.run_tool("get_sneaky", {}).status, "ok")
        finally:
            _REGISTRY.pop(tool.name)
        with tenant_context(organization_id=self.org_a.id):
            self.assertFalse(AuditLog.objects.filter(object_type="x").exists())

    def test_result_shaping_bounds_rows_and_size(self):
        result = ToolResult(tool="t", status="ok", summary={"total": "1.00"}, data={"rows": [{"n": "x" * 100}] * 500})
        shaped = shape_result(result, max_chars=2_000, max_rows=25)
        self.assertTrue(shaped.truncated)
        self.assertLessEqual(len(shaped.data.get("rows", [])), 25)
        self.assertEqual(shaped.summary, {"total": "1.00"})
