from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APITestCase

from compliance.tests.base import PERIOD_END, PERIOD_START, ComplianceFixtureMixin
from core.tenancy import tenant_context
from reports.selectors.tax import (
    get_gst_summary,
    get_gstr1_summary,
    get_gstr3b_summary,
    get_input_tax_register,
    get_output_tax_register,
)


class TaxReportsTests(ComplianceFixtureMixin, TestCase):
    def test_output_tax_register_matches_posted_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            self.make_invoice(self.customer_mh, unit_price=Decimal("1000.00"), tax_rate=Decimal("18"))
            rows = get_output_tax_register(organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["taxable_value"], Decimal("1000.00"))

    def test_input_tax_register_matches_posted_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            self.make_bill(unit_price=Decimal("500.00"), tax_rate=Decimal("18"))
            rows = get_input_tax_register(organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["taxable_value"], Decimal("500.00"))

    def test_gstr1_summary_reuses_compliance_selector_unmodified(self):
        with tenant_context(organization_id=self.org_a.id):
            self.make_invoice(self.customer_mh, unit_price=Decimal("1000.00"), tax_rate=Decimal("18"))
            result = get_gstr1_summary(organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END)
        self.assertIn("b2b", result)
        self.assertIn(self.customer_mh.gstin, result["b2b"])

    def test_gstr3b_summary_reuses_compliance_selector_unmodified(self):
        with tenant_context(organization_id=self.org_a.id):
            self.make_bill(unit_price=Decimal("500.00"), tax_rate=Decimal("18"))
            result = get_gstr3b_summary(organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END)
        self.assertEqual(result["itc"]["all_other"]["taxable_value"], Decimal("500.00"))

    def test_gst_summary_combines_registers_and_returns(self):
        with tenant_context(organization_id=self.org_a.id):
            self.make_invoice(self.customer_mh, unit_price=Decimal("1000.00"), tax_rate=Decimal("18"))
            self.make_bill(unit_price=Decimal("500.00"), tax_rate=Decimal("18"))
            result = get_gst_summary(organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END)
        self.assertEqual(result["output_tax"]["taxable_value"], Decimal("1000.00"))
        self.assertEqual(result["input_tax"]["taxable_value"], Decimal("500.00"))
        self.assertIn("gstr1", result)
        self.assertIn("gstr3b", result)

    def test_tax_reports_are_tenant_scoped(self):
        with tenant_context(organization_id=self.org_a.id):
            self.make_invoice(self.customer_mh, unit_price=Decimal("1000.00"))
        with tenant_context(organization_id=self.org_b.id):
            rows = get_output_tax_register(organization=self.org_b, date_from=PERIOD_START, date_to=PERIOD_END)
        self.assertEqual(rows, [])


class TaxReportsAPITests(ComplianceFixtureMixin, APITestCase):
    def _headers(self):
        return {"HTTP_X_ORGANIZATION_ID": str(self.org_a.id)}

    def test_gst_summary_endpoint(self):
        with tenant_context(organization_id=self.org_a.id):
            self.make_invoice(self.customer_mh, unit_price=Decimal("1000.00"), tax_rate=Decimal("18"))
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(
            "/api/v1/reports/tax/gst-summary/",
            {"from_date": str(PERIOD_START), "to_date": str(PERIOD_END)},
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["output_tax"]["taxable_value"], "1000.00")

    def test_gstr1_summary_endpoint_requires_date_range(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/api/v1/reports/tax/gstr1-summary/", **self._headers())
        self.assertEqual(response.status_code, 400)

    def test_output_tax_register_cross_org_isolated(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(
            "/api/v1/reports/tax/output-register/",
            {"from_date": str(PERIOD_START), "to_date": str(PERIOD_END)},
            HTTP_X_ORGANIZATION_ID=str(self.org_b.id),
        )
        self.assertEqual(response.status_code, 403)
