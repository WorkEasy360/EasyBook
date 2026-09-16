import datetime
from decimal import Decimal

from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice


class CSVExportTests(APITestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Acme", "csv-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.item = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org, customer_code="CUST-1", display_name="Alpha Co", currency=self.currency
            )
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 5),
                due_date=datetime.date(2026, 5, 5), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100")}],
            )
            post_invoice(invoice_id=invoice.id, organization=self.org)

    def _headers(self):
        return {"HTTP_X_ORGANIZATION_ID": str(self.org.id)}

    def test_customer_balances_csv_export(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/v1/reports/receivables/customer-balances/", {"export": "csv"}, **self._headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode()
        self.assertIn("customer_id,customer_name,balance", body)
        self.assertIn("Alpha Co", body)
        self.assertIn("100", body)

    def test_customer_balances_json_by_default(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/v1/reports/receivables/customer-balances/", **self._headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_sales_by_item_csv_export_respects_same_filters(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/v1/reports/sales/by-item/",
            {"export": "csv", "from_date": "2026-04-01", "to_date": "2026-04-30"},
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Consulting", body)

    def test_csv_export_requires_same_authorization(self):
        _other_org, other_owner, _ = make_org_with_owner("Other Org", "csv-other@example.com")
        self.client.force_authenticate(user=other_owner)
        response = self.client.get(
            "/api/v1/reports/receivables/customer-balances/",
            {"export": "csv"},
            HTTP_X_ORGANIZATION_ID=str(self.org.id),
        )
        self.assertEqual(response.status_code, 403)
