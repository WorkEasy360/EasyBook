from decimal import Decimal

from django.test import TestCase

from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import archive_item, create_item
from items.services.units import create_unit
from sales.models.quote import Quote, QuoteStatus
from sales.services.customers import archive_customer, create_customer
from sales.services.quotes import (
    accept_quote,
    cancel_quote,
    create_quote,
    reject_quote,
    replace_quote_lines,
    send_quote,
)


class QuoteTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "quote-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "quote-owner-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="WID-1",
            )
            self.service_item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
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

    def test_create_draft_quote_with_correct_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01",
                lines=self._lines(tax_rate=Decimal("18")),
            )
            self.assertEqual(quote.lines.count(), 1)
        self.assertEqual(quote.status, QuoteStatus.DRAFT)
        self.assertTrue(quote.quote_number.startswith("QUO-"))
        self.assertEqual(quote.subtotal, Decimal("200.00"))
        self.assertEqual(quote.tax_total, Decimal("36.00"))
        self.assertEqual(quote.total, Decimal("236.00"))

    def test_quote_creates_no_accounting_or_inventory_effect(self):
        from accounting.models.journal import JournalEntry
        from inventory.models.stock_movement import StockMovement

        with tenant_context(organization_id=self.org_a.id):
            create_quote(organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines())
            self.assertEqual(JournalEntry.objects.count(), 0)
            self.assertEqual(StockMovement.objects.count(), 0)

    def test_service_item_allowed_on_quote(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01",
                lines=self._lines(item=self.service_item, quantity=Decimal("1")),
            )
            self.assertEqual(quote.lines.first().item_id, self.service_item.id)

    def test_quote_requires_at_least_one_line(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_quote(organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=[])

    def test_inactive_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            archive_customer(customer=self.customer)
            with self.assertRaises(ApplicationError):
                create_quote(
                    organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
                )

    def test_inactive_item_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            archive_item(item=self.item)
            with self.assertRaises(ApplicationError):
                create_quote(
                    organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
                )

    def test_cross_org_item_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_quote(
                    organization=self.org_a, customer=self.customer, issue_date="2026-04-01",
                    lines=self._lines(item=self.item_b),
                )

    def test_cross_org_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_quote(
                    organization=self.org_a, customer=self.customer_b, issue_date="2026-04-01",
                    lines=self._lines(),
                )

    def test_replace_lines_recomputes_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            updated = replace_quote_lines(
                quote=quote, lines=self._lines(quantity=Decimal("1"), unit_price=Decimal("50.00"))
            )
            self.assertEqual(updated.lines.count(), 1)
        self.assertEqual(updated.total, Decimal("50.00"))

    def test_cannot_replace_lines_on_non_draft_quote(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            send_quote(quote_id=quote.id, organization=self.org_a)
            quote.refresh_from_db()
            with self.assertRaises(ApplicationError):
                replace_quote_lines(quote=quote, lines=self._lines())

    def test_send_then_accept(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            sent = send_quote(quote_id=quote.id, organization=self.org_a)
            self.assertEqual(sent.status, QuoteStatus.SENT)
            accepted = accept_quote(quote_id=quote.id, organization=self.org_a)
            self.assertEqual(accepted.status, QuoteStatus.ACCEPTED)

    def test_send_then_reject(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            send_quote(quote_id=quote.id, organization=self.org_a)
            rejected = reject_quote(quote_id=quote.id, organization=self.org_a)
            self.assertEqual(rejected.status, QuoteStatus.REJECTED)

    def test_cancel_draft_quote(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            cancelled = cancel_quote(quote_id=quote.id, organization=self.org_a)
            self.assertEqual(cancelled.status, QuoteStatus.CANCELLED)

    def test_cannot_accept_a_draft_quote(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            with self.assertRaises(ApplicationError):
                accept_quote(quote_id=quote.id, organization=self.org_a)

    def test_cannot_send_already_sent_quote(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            send_quote(quote_id=quote.id, organization=self.org_a)
            with self.assertRaises(ApplicationError):
                send_quote(quote_id=quote.id, organization=self.org_a)

    def test_cannot_transition_from_terminal_status(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            send_quote(quote_id=quote.id, organization=self.org_a)
            accept_quote(quote_id=quote.id, organization=self.org_a)
            with self.assertRaises(ApplicationError):
                cancel_quote(quote_id=quote.id, organization=self.org_a)

    def test_posted_quote_lines_are_immutable_at_model_layer(self):
        with tenant_context(organization_id=self.org_a.id):
            quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines()
            )
            send_quote(quote_id=quote.id, organization=self.org_a)
            line = quote.lines.first()
            line.quantity = Decimal("99")
            with self.assertRaises(ValueError):
                line.save()

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_quote(organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines())
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(Quote.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_quote(organization=self.org_a, customer=self.customer, issue_date="2026-04-01", lines=self._lines())
        clear_tenant_context()
        self.assertEqual(list(Quote.objects.all()), [])
