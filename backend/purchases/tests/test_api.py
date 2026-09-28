"""Purchases API surface: auth, tenant enforcement, RBAC, and the rule that
server-computed totals are never taken from the client.
"""

import json
from decimal import Decimal

from rest_framework.test import APIClient

from accounts.models import Membership
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_user
from purchases.services.bills import create_bill, post_bill
from purchases.tests.base import DUE_DATE, ORDER_DATE, PurchasesTestsBase


class PurchasesApiTestsBase(PurchasesTestsBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user_a)

    def _headers(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.org_a).id)}

    def _as_role(self, role, email):
        """Returns a client authenticated as a fresh user holding `role` in
        org A."""
        user = make_user(email)
        with tenant_context(user_id=user.id):
            Membership.objects.create(organization=self.org_a, user=user, role=role)
        client = APIClient()
        client.force_authenticate(user=user)
        return client


class VendorApiTests(PurchasesApiTestsBase):
    def test_list_requires_authentication(self):
        anonymous = APIClient()
        response = anonymous.get("/api/v1/purchases/vendors/", **self._headers())
        self.assertEqual(response.status_code, 401)

    def test_organization_header_is_required(self):
        response = self.client.get("/api/v1/purchases/vendors/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "organization_required")

    def test_cannot_use_an_organization_you_do_not_belong_to(self):
        response = self.client.get("/api/v1/purchases/vendors/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_create_and_list(self):
        response = self.client.post(
            "/api/v1/purchases/vendors/",
            {"vendor_code": "VEN-API", "display_name": "API Supplies", "currency": str(self.currency.pk)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201)
        listing = self.client.get("/api/v1/purchases/vendors/", **self._headers())
        codes = {row["vendor_code"] for row in listing.data["results"]}
        self.assertIn("VEN-API", codes)

    def test_create_vendor_accepts_is_active(self):
        """VendorSerializer lists is_active as writable, so a client may send
        it. create_vendor() had no such parameter, which made an ordinary
        create raise TypeError and return 500 rather than 201 — the same
        defect as create_customer."""
        response = self.client.post(
            "/api/v1/purchases/vendors/",
            {
                "vendor_code": "VEN-INACTIVE",
                "display_name": "Dormant Supplier",
                "currency": str(self.currency.pk),
                "is_active": False,
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["is_active"])

    def test_create_vendor_defaults_to_active(self):
        response = self.client.post(
            "/api/v1/purchases/vendors/",
            {"vendor_code": "VEN-ACTIVE", "display_name": "Active Supplier", "currency": str(self.currency.pk)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_active"])

    def test_list_never_leaks_another_organizations_vendors(self):
        response = self.client.get("/api/v1/purchases/vendors/", **self._headers())
        names = {row["display_name"] for row in response.data["results"]}
        self.assertIn("Acme Supplies", names)
        self.assertNotIn("Beta Supplies", names)

    def test_viewer_can_read_but_not_write(self):
        viewer = self._as_role(Role.VIEWER, "purch-viewer@example.com")
        self.assertEqual(viewer.get("/api/v1/purchases/vendors/", **self._headers()).status_code, 200)
        response = viewer.post(
            "/api/v1/purchases/vendors/",
            {"vendor_code": "X", "display_name": "X", "currency": str(self.currency.pk)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_retrieve_a_single_vendor(self):
        """Regression guard for the detail-view permission split: a detail
        view that hardcodes the write permission silently blocks viewers."""
        viewer = self._as_role(Role.VIEWER, "purch-viewer-detail@example.com")
        response = viewer.get(f"/api/v1/purchases/vendors/{self.vendor.id}/", **self._headers())
        self.assertEqual(response.status_code, 200)

    def test_duplicate_vendor_code_returns_a_domain_error(self):
        """uniq_vendor_code_per_org used to surface as an IntegrityError and a
        500 on an ordinary re-used code; it must be a 400 the form can show."""
        payload = {"vendor_code": "VEN-DUP", "display_name": "First", "currency": str(self.currency.pk)}
        first = self.client.post("/api/v1/purchases/vendors/", payload, format="json", **self._headers())
        self.assertEqual(first.status_code, 201, first.data)

        again = self.client.post(
            "/api/v1/purchases/vendors/", {**payload, "display_name": "Second"}, format="json", **self._headers()
        )
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data["error"]["code"], "duplicate_vendor_code")

        renamed = self.client.patch(
            f"/api/v1/purchases/vendors/{self.vendor.id}/", {"vendor_code": "VEN-DUP"},
            format="json", **self._headers(),
        )
        self.assertEqual(renamed.status_code, 400)
        self.assertEqual(renamed.data["error"]["code"], "duplicate_vendor_code")

        # Re-saving a vendor with its own unchanged code is not a duplicate.
        unchanged = self.client.patch(
            f"/api/v1/purchases/vendors/{first.data['id']}/", {"vendor_code": "VEN-DUP", "notes": "kept"},
            format="json", **self._headers(),
        )
        self.assertEqual(unchanged.status_code, 200, unchanged.data)


class PurchaseOrderApiTests(PurchasesApiTestsBase):
    def _create_order(self, client=None, **overrides):
        payload = {
            "vendor_id": str(self.vendor.id),
            "order_date": str(ORDER_DATE),
            "warehouse_id": str(self.warehouse.id),
            "lines": [{
                "item_id": str(self.product.id), "quantity": "10", "unit_price": "50.00", "tax_rate": "18",
            }],
            **overrides,
        }
        return (client or self.client).post(
            "/api/v1/purchases/orders/", payload, format="json", **self._headers()
        )

    def test_create_computes_totals_server_side(self):
        response = self._create_order()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.data["subtotal"]), Decimal("500.00"))
        self.assertEqual(Decimal(response.data["tax_total"]), Decimal("90.00"))
        self.assertEqual(Decimal(response.data["total"]), Decimal("590.00"))

    def test_client_supplied_total_is_ignored(self):
        response = self._create_order(total="1.00", subtotal="1.00")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.data["total"]), Decimal("590.00"))

    def test_approve_then_reject_further_edits(self):
        order_id = self._create_order().data["id"]
        approve = self.client.post(f"/api/v1/purchases/orders/{order_id}/approve/", **self._headers())
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.data["status"], "approved")

        patch = self.client.patch(
            f"/api/v1/purchases/orders/{order_id}/", {"notes": "late edit"}, format="json", **self._headers()
        )
        self.assertEqual(patch.status_code, 400)
        self.assertEqual(patch.data["error"]["code"], "purchase_order_not_draft")

    def test_match_renders_quantities_and_prices_as_decimal_strings(self):
        """response.data holds Decimals, so only the rendered body shows what
        the client receives: DRF's JSONEncoder turns a bare Decimal into a
        float. Amounts must cross the wire as strings, as everywhere else."""
        order_id = self._create_order().data["id"]
        response = self.client.get(f"/api/v1/purchases/orders/{order_id}/match/", **self._headers())
        self.assertEqual(response.status_code, 200)
        line = json.loads(response.content)["lines"][0]
        self.assertEqual(line["ordered_quantity"], "10.0000")
        self.assertEqual(line["ordered_unit_price"], "50.00")
        self.assertIsInstance(line["received_quantity"], str)
        self.assertIsInstance(line["max_price_variance"], str)

    def test_match_endpoint_is_readable_by_a_viewer(self):
        order_id = self._create_order().data["id"]
        self.client.post(f"/api/v1/purchases/orders/{order_id}/approve/", **self._headers())
        viewer = self._as_role(Role.VIEWER, "purch-viewer-match@example.com")
        response = viewer.get(f"/api/v1/purchases/orders/{order_id}/match/", **self._headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn("matched", response.data)
        self.assertIn("lines", response.data)

    def test_unknown_item_returns_404(self):
        import uuid

        response = self._create_order(lines=[{
            "item_id": str(uuid.uuid4()), "quantity": "1", "unit_price": "1.00",
        }])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "item_not_found")

    def test_item_from_another_organization_is_not_found(self):
        """Tenant scoping, not a 403: to this organization the row does not
        exist at all."""
        response = self._create_order(lines=[{
            "item_id": str(self.item_b.id), "quantity": "1", "unit_price": "1.00",
        }])
        self.assertEqual(response.status_code, 404)


class BillApiTests(PurchasesApiTestsBase):
    def _create_bill(self, client=None, **overrides):
        payload = {
            "vendor_id": str(self.vendor.id),
            "bill_date": str(ORDER_DATE),
            "due_date": str(DUE_DATE),
            "payable_account_id": str(self.ap_account.id),
            "lines": [{"item_id": str(self.service.id), "quantity": "2", "unit_price": "100.00"}],
            **overrides,
        }
        return (client or self.client).post(
            "/api/v1/purchases/bills/", payload, format="json", **self._headers()
        )

    def test_create_post_and_derived_amounts(self):
        created = self._create_bill()
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["status"], "draft")
        self.assertEqual(created.data["bill_number"], "")

        posted = self.client.post(
            f"/api/v1/purchases/bills/{created.data['id']}/post/", **self._headers()
        )
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(posted.data["status"], "open")
        self.assertTrue(posted.data["bill_number"].startswith("BILL-"))
        # amount_paid/amount_due are derived on read, never stored.
        self.assertEqual(Decimal(posted.data["amount_paid"]), Decimal("0"))
        self.assertEqual(Decimal(posted.data["amount_due"]), Decimal("200.00"))

    def test_derived_amounts_and_match_are_rendered_as_decimal_strings(self):
        """amount_paid/amount_due are SerializerMethodFields. Returning a bare
        Decimal rendered them as floats (200.0) — asserted on the rendered
        body, because response.data still holds the Decimal."""
        created = self._create_bill()
        posted = self.client.post(f"/api/v1/purchases/bills/{created.data['id']}/post/", **self._headers())
        body = json.loads(posted.content)
        self.assertEqual(body["amount_paid"], "0")
        self.assertEqual(body["amount_due"], "200.00")

        match = self.client.get(f"/api/v1/purchases/bills/{created.data['id']}/match/", **self._headers())
        self.assertEqual(match.status_code, 200)
        line = json.loads(match.content)["lines"][0]
        self.assertEqual(line["billed_quantity"], "2.0000")
        self.assertEqual(line["billed_unit_price"], "100.00")
        self.assertIsInstance(line["price_variance"], str)

    def test_void_requires_the_void_permission(self):
        created = self._create_bill()
        bill_id = created.data["id"]
        self.client.post(f"/api/v1/purchases/bills/{bill_id}/post/", **self._headers())

        accountant = self._as_role(Role.ACCOUNTANT, "purch-acct-void@example.com")
        response = accountant.post(
            f"/api/v1/purchases/bills/{bill_id}/void/", {"reason": "duplicate"}, format="json", **self._headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "void")

    def test_viewer_cannot_post_a_bill(self):
        created = self._create_bill()
        viewer = self._as_role(Role.VIEWER, "purch-viewer-bill@example.com")
        response = viewer.post(
            f"/api/v1/purchases/bills/{created.data['id']}/post/", **self._headers()
        )
        self.assertEqual(response.status_code, 403)

    def test_duplicate_vendor_bill_number_returns_a_domain_error(self):
        self._create_bill(vendor_bill_number="INV-1")
        response = self._create_bill(vendor_bill_number="INV-1")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "duplicate_vendor_bill_number")

    def test_filter_by_status_and_vendor(self):
        created = self._create_bill()
        self.client.post(f"/api/v1/purchases/bills/{created.data['id']}/post/", **self._headers())
        self._create_bill(vendor_bill_number="INV-2")

        open_only = self.client.get("/api/v1/purchases/bills/?status=open", **self._headers())
        self.assertEqual(open_only.data["count"], 1)
        by_vendor = self.client.get(
            f"/api/v1/purchases/bills/?vendor={self.vendor.id}", **self._headers()
        )
        self.assertEqual(by_vendor.data["count"], 2)


class VendorPaymentApiTests(PurchasesApiTestsBase):
    def _open_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            return post_bill(bill_id=bill.id, organization=self.org_a)

    def _payload(self, bill):
        return {
            "vendor_id": str(self.vendor.id),
            "payment_date": str(ORDER_DATE),
            "amount": "200.00",
            "source_account_id": str(self.bank_account.id),
            "allocations": [{"bill_id": str(bill.id), "amount": "200.00"}],
        }

    def test_record_payment(self):
        bill = self._open_bill()
        response = self.client.post(
            "/api/v1/purchases/payments/", self._payload(bill), format="json", **self._headers()
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["payment_number"].startswith("VPAY-"))
        self.assertEqual(len(response.data["allocations"]), 1)

    def test_idempotency_key_replays_instead_of_double_paying(self):
        bill = self._open_bill()
        payload = self._payload(bill)
        headers = {**self._headers(), "HTTP_IDEMPOTENCY_KEY": "vendor-pay-once"}

        first = self.client.post("/api/v1/purchases/payments/", payload, format="json", **headers)
        second = self.client.post("/api/v1/purchases/payments/", payload, format="json", **headers)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.data["id"], second.data["id"], "the replay must return the original payment")
        listing = self.client.get("/api/v1/purchases/payments/", **self._headers())
        self.assertEqual(listing.data["count"], 1)

    def test_staff_cannot_record_a_vendor_payment(self):
        """The one deliberate asymmetry with sales: staff may take money in,
        but authorising money OUT stops at accountant."""
        bill = self._open_bill()
        staff = self._as_role(Role.STAFF, "purch-staff-pay@example.com")
        response = staff.post(
            "/api/v1/purchases/payments/", self._payload(bill), format="json", **self._headers()
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_can_record_a_vendor_payment(self):
        bill = self._open_bill()
        accountant = self._as_role(Role.ACCOUNTANT, "purch-acct-pay@example.com")
        response = accountant.post(
            "/api/v1/purchases/payments/", self._payload(bill), format="json", **self._headers()
        )
        self.assertEqual(response.status_code, 201)

    def test_payments_are_read_only_after_creation(self):
        bill = self._open_bill()
        created = self.client.post(
            "/api/v1/purchases/payments/", self._payload(bill), format="json", **self._headers()
        )
        detail = f"/api/v1/purchases/payments/{created.data['id']}/"
        self.assertEqual(self.client.get(detail, **self._headers()).status_code, 200)
        self.assertEqual(self.client.patch(detail, {}, format="json", **self._headers()).status_code, 405)
        self.assertEqual(self.client.delete(detail, **self._headers()).status_code, 405)


class GoodsReceiptApiTests(PurchasesApiTestsBase):
    def test_receive_then_convert_to_bill(self):
        order = self.client.post(
            "/api/v1/purchases/orders/",
            {
                "vendor_id": str(self.vendor.id), "order_date": str(ORDER_DATE),
                "warehouse_id": str(self.warehouse.id),
                "lines": [{"item_id": str(self.product.id), "quantity": "10", "unit_price": "50.00"}],
            },
            format="json", **self._headers(),
        )
        order_id = order.data["id"]
        order_line_id = order.data["lines"][0]["id"]
        self.client.post(f"/api/v1/purchases/orders/{order_id}/approve/", **self._headers())

        receipt = self.client.post(
            "/api/v1/purchases/goods-receipts/",
            {
                "vendor_id": str(self.vendor.id), "warehouse_id": str(self.warehouse.id),
                "receipt_date": str(ORDER_DATE), "source_purchase_order_id": order_id,
                "lines": [{
                    "item_id": str(self.product.id), "quantity": "10", "source_order_line_id": order_line_id,
                }],
            },
            format="json", **self._headers(),
        )
        self.assertEqual(receipt.status_code, 201)
        receipt_id = receipt.data["id"]

        received = self.client.post(
            f"/api/v1/purchases/goods-receipts/{receipt_id}/receive/", **self._headers()
        )
        self.assertEqual(received.status_code, 200)
        self.assertEqual(received.data["status"], "received")

        bill = self.client.post(
            f"/api/v1/purchases/goods-receipts/{receipt_id}/convert-to-bill/",
            {
                "bill_date": str(ORDER_DATE), "due_date": str(DUE_DATE),
                "payable_account_id": str(self.ap_account.id),
            },
            format="json", **self._headers(),
        )
        self.assertEqual(bill.status_code, 201)
        self.assertIsNotNone(bill.data["lines"][0]["source_goods_receipt_line"])

    def test_convert_to_bill_requires_the_create_bill_permission(self):
        """The thing being created is a bill, so MANAGE_GOODS_RECEIPTS alone
        must not be enough."""
        viewer = self._as_role(Role.VIEWER, "purch-viewer-convert@example.com")
        import uuid

        response = viewer.post(
            f"/api/v1/purchases/goods-receipts/{uuid.uuid4()}/convert-to-bill/",
            {
                "bill_date": str(ORDER_DATE), "due_date": str(DUE_DATE),
                "payable_account_id": str(self.ap_account.id),
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)


class ExpenseApiTests(PurchasesApiTestsBase):
    def _create(self, **overrides):
        payload = {
            "expense_date": str(ORDER_DATE),
            "amount": "100.00",
            "expense_account_id": str(self.office_expense_account.id),
            "paid_through_account_id": str(self.bank_account.id),
            **overrides,
        }
        return self.client.post("/api/v1/purchases/expenses/", payload, format="json", **self._headers())

    def test_create_post_and_void(self):
        created = self._create(tax_rate="18", tax_recoverable_account_id=str(self.input_tax_account.id))
        self.assertEqual(created.status_code, 201)
        self.assertEqual(Decimal(created.data["total"]), Decimal("118.00"))

        posted = self.client.post(
            f"/api/v1/purchases/expenses/{created.data['id']}/post/", **self._headers()
        )
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(posted.data["status"], "posted")

        voided = self.client.post(
            f"/api/v1/purchases/expenses/{created.data['id']}/void/",
            {"reason": "personal"}, format="json", **self._headers(),
        )
        self.assertEqual(voided.status_code, 200)
        self.assertEqual(voided.data["status"], "void")

    def test_client_supplied_total_is_ignored_on_update(self):
        created = self._create()
        response = self.client.patch(
            f"/api/v1/purchases/expenses/{created.data['id']}/",
            {"amount": "250.00", "total": "1.00"}, format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["total"]), Decimal("250.00"))

    def test_billable_without_customer_is_rejected(self):
        response = self._create(is_billable=True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "billable_customer_required")


class VendorCreditApiTests(PurchasesApiTestsBase):
    def test_create_issue_and_void(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)

        created = self.client.post(
            "/api/v1/purchases/vendor-credits/",
            {
                "vendor_id": str(self.vendor.id), "credit_date": str(ORDER_DATE),
                "source_bill_id": str(bill.id), "reason": "return",
                "lines": [{"item_id": str(self.service.id), "quantity": "1", "unit_price": "100.00"}],
            },
            format="json", **self._headers(),
        )
        self.assertEqual(created.status_code, 201)
        credit_id = created.data["id"]

        issued = self.client.post(
            f"/api/v1/purchases/vendor-credits/{credit_id}/issue/", **self._headers()
        )
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(issued.data["status"], "issued")
        self.assertEqual(Decimal(issued.data["amount_applied_to_bill"]), Decimal("100.00"))

        voided = self.client.post(
            f"/api/v1/purchases/vendor-credits/{credit_id}/void/", **self._headers()
        )
        self.assertEqual(voided.status_code, 200)
        self.assertEqual(voided.data["status"], "void")

    def test_create_rejects_a_reason_outside_the_choices(self):
        # Regression: the create serializer accepted any string as the reason.
        response = self.client.post(
            "/api/v1/purchases/vendor-credits/",
            {
                "vendor_id": str(self.vendor.id), "credit_date": str(ORDER_DATE), "reason": "bogus",
                "unapplied_credit_account_id": str(self.vendor_advance_account.id),
                "lines": [{"item_id": str(self.service.id), "quantity": "1", "unit_price": "10.00"}],
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", str(response.data))

    def test_viewer_can_retrieve_but_not_issue(self):
        with tenant_context(organization_id=self.org_a.id):
            from purchases.services.vendor_credits import create_vendor_credit

            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE,
                unapplied_credit_account=self.vendor_advance_account,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("10.00")),
            )
        viewer = self._as_role(Role.VIEWER, "purch-viewer-credit@example.com")
        self.assertEqual(
            viewer.get(f"/api/v1/purchases/vendor-credits/{credit.id}/", **self._headers()).status_code, 200
        )
        self.assertEqual(
            viewer.post(f"/api/v1/purchases/vendor-credits/{credit.id}/issue/", **self._headers()).status_code,
            403,
        )


class RecurringApiTests(PurchasesApiTestsBase):
    def test_create_and_toggle_a_recurring_bill_template(self):
        created = self.client.post(
            "/api/v1/purchases/recurring-bills/",
            {
                "vendor_id": str(self.vendor.id), "frequency": "monthly", "start_date": str(ORDER_DATE),
                "payable_account_id": str(self.ap_account.id), "due_days": 15,
                "lines": [{"item_id": str(self.service.id), "quantity": "1", "unit_price": "100.00"}],
            },
            format="json", **self._headers(),
        )
        self.assertEqual(created.status_code, 201)
        template_id = created.data["id"]

        off = self.client.post(
            f"/api/v1/purchases/recurring-bills/{template_id}/deactivate/", **self._headers()
        )
        self.assertFalse(off.data["is_active"])
        on = self.client.post(
            f"/api/v1/purchases/recurring-bills/{template_id}/activate/", **self._headers()
        )
        self.assertTrue(on.data["is_active"])

    def test_inventoried_item_rejected_on_a_recurring_template(self):
        response = self.client.post(
            "/api/v1/purchases/recurring-bills/",
            {
                "vendor_id": str(self.vendor.id), "frequency": "monthly", "start_date": str(ORDER_DATE),
                "payable_account_id": str(self.ap_account.id),
                "lines": [{"item_id": str(self.product.id), "quantity": "1", "unit_price": "50.00"}],
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "recurring_item_inventoried")

    def test_there_is_no_generate_now_endpoint(self):
        """Generation is Celery-only, exactly as for recurring invoices."""
        created = self.client.post(
            "/api/v1/purchases/recurring-bills/",
            {
                "vendor_id": str(self.vendor.id), "frequency": "monthly", "start_date": str(ORDER_DATE),
                "payable_account_id": str(self.ap_account.id),
                "lines": [{"item_id": str(self.service.id), "quantity": "1", "unit_price": "100.00"}],
            },
            format="json", **self._headers(),
        )
        response = self.client.post(
            f"/api/v1/purchases/recurring-bills/{created.data['id']}/generate/", **self._headers()
        )
        self.assertEqual(response.status_code, 404)

    def test_create_a_recurring_expense_template(self):
        response = self.client.post(
            "/api/v1/purchases/recurring-expenses/",
            {
                "frequency": "monthly", "start_date": str(ORDER_DATE), "amount": "500.00",
                "expense_account_id": str(self.office_expense_account.id),
                "paid_through_account_id": str(self.bank_account.id),
                "description": "Office rent",
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["next_run_at"], str(ORDER_DATE))


class CrossTenantApiTests(PurchasesApiTestsBase):
    def test_cannot_reach_another_organizations_bill_by_id(self):
        with tenant_context(organization_id=self.org_b.id):
            import datetime

            from accounting.models.account import AccountType
            from accounting.services.accounts import create_account
            from accounts.models import FiscalYear

            FiscalYear.objects.create(
                organization=self.org_b, start_date=datetime.date(2026, 4, 1),
                end_date=datetime.date(2027, 3, 31),
            )
            ap_b = create_account(
                organization=self.org_b, code="2000", name="AP", account_type=AccountType.LIABILITY
            )
            expense_b = create_account(
                organization=self.org_b, code="5100", name="Purchases", account_type=AccountType.EXPENSE
            )
            from items.services.items import update_item

            update_item(item=self.item_b, purchase_account=expense_b)
            bill_b = create_bill(
                organization=self.org_b, vendor=self.vendor_b, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=ap_b,
                lines=[{"item": self.item_b, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )

        # Authenticated as org A's owner, asking for org B's bill.
        self.assertEqual(
            self.client.get(f"/api/v1/purchases/bills/{bill_b.id}/", **self._headers()).status_code, 404
        )
        self.assertEqual(
            self.client.post(f"/api/v1/purchases/bills/{bill_b.id}/post/", **self._headers()).status_code, 404
        )

    def test_cannot_attach_another_organizations_vendor(self):
        response = self.client.post(
            "/api/v1/purchases/orders/",
            {
                "vendor_id": str(self.vendor_b.id), "order_date": str(ORDER_DATE),
                "lines": [{"item_id": str(self.service.id), "quantity": "1", "unit_price": "10.00"}],
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 404)
