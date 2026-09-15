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
from sales.services.invoices import create_invoice, post_invoice


class PaymentsAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-payment-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-payment-owner-b@example.com")
        self.viewer = make_user("api-payment-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.bank_account = create_account(
                organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
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
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
            )
            self.invoice = post_invoice(invoice_id=invoice.id, organization=self.org_a)
        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_record_payment(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/sales/payments/",
            {
                "customer_id": str(self.customer.id), "payment_date": "2026-04-15", "amount": "100.00",
                "destination_account_id": str(self.bank_account.id),
                "allocations": [{"invoice_id": str(self.invoice.id), "amount": "100.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["payment_number"].startswith("PAY-"))

    def test_viewer_cannot_record_payment(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/sales/payments/",
            {
                "customer_id": str(self.customer.id), "payment_date": "2026-04-15", "amount": "100.00",
                "destination_account_id": str(self.bank_account.id),
                "allocations": [{"invoice_id": str(self.invoice.id), "amount": "100.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_payments(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/sales/payments/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)

    def test_duplicate_request_with_idempotency_key_returns_same_payment(self):
        self.client.force_authenticate(user=self.owner_a)
        body = {
            "customer_id": str(self.customer.id), "payment_date": "2026-04-15", "amount": "40.00",
            "destination_account_id": str(self.bank_account.id),
            "allocations": [{"invoice_id": str(self.invoice.id), "amount": "40.00"}],
        }
        headers = {**self._headers(self.org_a), "HTTP_IDEMPOTENCY_KEY": "test-key-1"}
        first = self.client.post("/api/v1/sales/payments/", body, format="json", **headers)
        second = self.client.post("/api/v1/sales/payments/", body, format="json", **headers)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(first.data["payment_number"], second.data["payment_number"])

    def test_over_allocation_rejected_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/sales/payments/",
            {
                "customer_id": str(self.customer.id), "payment_date": "2026-04-15", "amount": "200.00",
                "destination_account_id": str(self.bank_account.id),
                "allocations": [{"invoice_id": str(self.invoice.id), "amount": "200.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 400)

    def test_non_member_cannot_access_other_org_payments(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/payments/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)
