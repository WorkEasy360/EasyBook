from decimal import Decimal

from django.test import TestCase

from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import archive_item, create_item
from items.services.units import create_unit
from sales.models.sales_order import SalesOrder, SalesOrderStatus
from sales.services.customers import archive_customer, create_customer
from sales.services.quotes import accept_quote, create_quote, reject_quote, send_quote
from sales.services.sales_orders import (
    cancel_order,
    confirm_order,
    convert_quote_to_sales_order,
    create_sales_order,
    replace_order_lines,
)


class SalesOrderTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "so-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "so-owner-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit, sku="WID-1"
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
        with tenant_context(organization_id=self.org_b.id):
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.item_b = create_item(
                organization=self.org_b, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit_b
            )
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    def _lines(self, item=None, quantity=Decimal("2"), unit_price=Decimal("100.00"), **kwargs):
        return [{"item": item or self.item, "quantity": quantity, "unit_price": unit_price, **kwargs}]

    def _make_accepted_quote(self):
        quote = create_quote(
            organization=self.org_a, customer=self.customer, issue_date="2026-04-01",
            lines=self._lines(tax_rate=Decimal("18")),
        )
        send_quote(quote_id=quote.id, organization=self.org_a)
        return accept_quote(quote_id=quote.id, organization=self.org_a)

    def test_create_draft_order_with_correct_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01",
                lines=self._lines(tax_rate=Decimal("18")),
            )
            self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.status, SalesOrderStatus.DRAFT)
        self.assertTrue(order.order_number.startswith("SO-"))
        self.assertEqual(order.total, Decimal("236.00"))

    def test_order_creates_no_accounting_effect(self):
        from accounting.models.journal import JournalEntry

        with tenant_context(organization_id=self.org_a.id):
            create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            self.assertEqual(JournalEntry.objects.count(), 0)

    def test_order_requires_at_least_one_line(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_sales_order(
                    organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=[]
                )

    def test_inactive_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            archive_customer(customer=self.customer)
            with self.assertRaises(ApplicationError):
                create_sales_order(
                    organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
                )

    def test_inactive_item_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            archive_item(item=self.item)
            with self.assertRaises(ApplicationError):
                create_sales_order(
                    organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
                )

    def test_cross_org_item_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_sales_order(
                    organization=self.org_a, customer=self.customer, order_date="2026-04-01",
                    lines=self._lines(item=self.item_b),
                )

    def test_cross_org_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_sales_order(
                    organization=self.org_a, customer=self.customer_b, order_date="2026-04-01", lines=self._lines()
                )

    def test_replace_lines_recomputes_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            updated = replace_order_lines(
                order=order, lines=self._lines(quantity=Decimal("1"), unit_price=Decimal("50.00"))
            )
            self.assertEqual(updated.lines.count(), 1)
        self.assertEqual(updated.total, Decimal("50.00"))

    def test_cannot_replace_lines_on_non_draft_order(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            order.refresh_from_db()
            with self.assertRaises(ApplicationError):
                replace_order_lines(order=order, lines=self._lines())

    def test_confirm_order(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            confirmed = confirm_order(order_id=order.id, organization=self.org_a)
        self.assertEqual(confirmed.status, SalesOrderStatus.CONFIRMED)

    def test_cannot_confirm_empty_order_lines_replaced_away(self):
        # Guards against confirming an order whose lines were emptied via a
        # path that bypasses replace_order_lines' own no-lines check.
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            order.lines.all().delete()
            with self.assertRaises(ApplicationError):
                confirm_order(order_id=order.id, organization=self.org_a)

    def test_cancel_draft_order(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            cancelled = cancel_order(order_id=order.id, organization=self.org_a)
        self.assertEqual(cancelled.status, SalesOrderStatus.CANCELLED)

    def test_cancel_confirmed_order(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            cancelled = cancel_order(order_id=order.id, organization=self.org_a)
        self.assertEqual(cancelled.status, SalesOrderStatus.CANCELLED)

    def test_invalid_transition_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            cancel_order(order_id=order.id, organization=self.org_a)
            with self.assertRaises(ApplicationError):
                confirm_order(order_id=order.id, organization=self.org_a)

    def test_posted_order_lines_immutable_at_model_layer(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            line = order.lines.first()
            line.quantity = Decimal("99")
            with self.assertRaises(ValueError):
                line.save()

    def test_convert_accepted_quote_to_sales_order(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = self._make_accepted_quote()
            order = convert_quote_to_sales_order(quote=quote, order_date="2026-04-05")
            self.assertEqual(order.source_quote_id, quote.id)
            self.assertEqual(order.total, quote.total)
            self.assertEqual(order.customer_id, quote.customer_id)
            self.assertEqual(order.lines.count(), quote.lines.count())

    def test_convert_quote_twice_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = self._make_accepted_quote()
            convert_quote_to_sales_order(quote=quote, order_date="2026-04-05")
            with self.assertRaises(ApplicationError):
                convert_quote_to_sales_order(quote=quote, order_date="2026-04-05")

    def test_convert_non_accepted_quote_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            with self.assertRaises(ApplicationError):
                convert_quote_to_sales_order(quote=quote, order_date="2026-04-05")

    def test_convert_rejected_quote_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            send_quote(quote_id=quote.id, organization=self.org_a)
            reject_quote(quote_id=quote.id, organization=self.org_a)
            with self.assertRaises(ApplicationError):
                convert_quote_to_sales_order(quote=quote, order_date="2026-04-05")

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(SalesOrder.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01", lines=self._lines()
            )
        clear_tenant_context()
        self.assertEqual(list(SalesOrder.objects.all()), [])
