import datetime
from decimal import Decimal

from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_membership, make_org_with_owner, make_user
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice
from sales.services.quotes import accept_quote, create_quote, send_quote


class InvoicesAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-invoice-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-invoice-owner-b@example.com")
        self.viewer = make_user("api-invoice-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
            self.invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
            )
            self.quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01",
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("40.00")}],
            )
            send_quote(quote_id=self.quote.id, organization=self.org_a)
            accept_quote(quote_id=self.quote.id, organization=self.org_a)
        with tenant_context(organization_id=self.org_b.id):
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )
            self.ar_account_b = create_account(
                organization=self.org_b, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.sales_account_b = create_account(
                organization=self.org_b, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.item_b = create_item(
                organization=self.org_b, item_type=ItemType.SERVICE, name="Other", unit=self.unit_b,
                sales_account=self.sales_account_b,
            )
            self.invoice_b = create_invoice(
                organization=self.org_b, customer=self.customer_b, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account_b,
                lines=[{"item": self.item_b, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_create_invoice(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/sales/invoices/",
            {
                "customer_id": str(self.customer.id), "invoice_date": "2026-04-10", "due_date": "2026-05-10",
                "receivable_account_id": str(self.ar_account.id),
                "lines": [{"item_id": str(self.item.id), "quantity": "2", "unit_price": "50.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total"], "100.00")
        self.assertEqual(response.data["status"], "draft")

    def test_viewer_cannot_create_invoice(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/sales/invoices/",
            {
                "customer_id": str(self.customer.id), "invoice_date": "2026-04-10", "due_date": "2026-05-10",
                "receivable_account_id": str(self.ar_account.id),
                "lines": [{"item_id": str(self.item.id), "quantity": "1", "unit_price": "10.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_invoices(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/sales/invoices/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_post_invoice(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(f"/api/v1/sales/invoices/{self.invoice.id}/post/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 403)

    def test_owner_can_post_invoice(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(f"/api/v1/sales/invoices/{self.invoice.id}/post/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "sent")
        self.assertTrue(response.data["invoice_number"].startswith("INV-"))

    def test_invoice_list_is_tenant_scoped(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/invoices/", **self._headers(self.org_a))
        ids = {i["id"] for i in response.data["results"]}
        self.assertIn(str(self.invoice.id), ids)
        self.assertNotIn(str(self.invoice_b.id), ids)

    def test_void_after_post(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/sales/invoices/{self.invoice.id}/post/", **self._headers(self.org_a))
        response = self.client.post(
            f"/api/v1/sales/invoices/{self.invoice.id}/void/", {"reason": "test"}, format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "void")

    def test_viewer_cannot_void_invoice(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/sales/invoices/{self.invoice.id}/post/", **self._headers(self.org_a))
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(f"/api/v1/sales/invoices/{self.invoice.id}/void/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 403)

    def test_convert_quote_to_invoice_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            f"/api/v1/sales/quotes/{self.quote.id}/convert/",
            {"target": "invoice", "due_date": "2026-05-01", "receivable_account_id": str(self.ar_account.id)},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total"], "40.00")
        self.assertEqual(str(response.data["source_quote"]), str(self.quote.id))

    def test_convert_quote_to_invoice_without_receivable_account_rejected(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            f"/api/v1/sales/quotes/{self.quote.id}/convert/",
            {"target": "invoice", "due_date": "2026-05-01"},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 400)

    def test_non_member_cannot_access_other_org_invoices(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/invoices/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_cannot_patch_lines_on_posted_invoice(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/sales/invoices/{self.invoice.id}/post/", **self._headers(self.org_a))
        response = self.client.patch(
            f"/api/v1/sales/invoices/{self.invoice.id}/",
            {"lines": [{"item_id": str(self.item.id), "quantity": "5", "unit_price": "20.00"}]},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 400)
