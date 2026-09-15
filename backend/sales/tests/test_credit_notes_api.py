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
from sales.services.credit_notes import create_credit_note
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice


class CreditNotesAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-cn-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-cn-owner-b@example.com")
        self.viewer = make_user("api-cn-viewer@example.com")
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
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
            )
            self.invoice = post_invoice(invoice_id=invoice.id, organization=self.org_a)
            self.invoice_line = self.invoice.lines.first()
            self.credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=self.invoice,
                lines=[{
                    "item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("10.00"),
                    "source_invoice_line": self.invoice_line,
                }],
            )
        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_create_and_issue_credit_note(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/sales/credit-notes/",
            {
                "customer_id": str(self.customer.id), "credit_note_date": "2026-04-15",
                "source_invoice_id": str(self.invoice.id),
                "lines": [{
                    "item_id": str(self.item.id), "quantity": "1", "unit_price": "40.00",
                    "source_invoice_line_id": str(self.invoice_line.id),
                }],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        credit_note_id = create_response.data["id"]

        issue_response = self.client.post(
            f"/api/v1/sales/credit-notes/{credit_note_id}/issue/", **self._headers(self.org_a)
        )
        self.assertEqual(issue_response.status_code, 200, issue_response.data)
        self.assertEqual(issue_response.data["status"], "issued")
        self.assertTrue(issue_response.data["credit_note_number"].startswith("CN-"))

    def test_viewer_cannot_create_credit_note(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/sales/credit-notes/",
            {
                "customer_id": str(self.customer.id), "credit_note_date": "2026-04-15",
                "source_invoice_id": str(self.invoice.id),
                "lines": [{
                    "item_id": str(self.item.id), "quantity": "1", "unit_price": "40.00",
                    "source_invoice_line_id": str(self.invoice_line.id),
                }],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_credit_notes(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/sales/credit-notes/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)

    def test_viewer_can_retrieve_credit_note_detail(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get(
            f"/api/v1/sales/credit-notes/{self.credit_note.id}/", **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_viewer_cannot_patch_credit_note(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.patch(
            f"/api/v1/sales/credit-notes/{self.credit_note.id}/", {"reference": "x"}, format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_access_other_org_credit_notes(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/credit-notes/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_invalid_transition_rejected_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/sales/credit-notes/",
            {
                "customer_id": str(self.customer.id), "credit_note_date": "2026-04-15",
                "source_invoice_id": str(self.invoice.id),
                "lines": [{
                    "item_id": str(self.item.id), "quantity": "1", "unit_price": "40.00",
                    "source_invoice_line_id": str(self.invoice_line.id),
                }],
            },
            format="json", **self._headers(self.org_a),
        )
        credit_note_id = create_response.data["id"]
        response = self.client.post(
            f"/api/v1/sales/credit-notes/{credit_note_id}/void/", **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 400)
