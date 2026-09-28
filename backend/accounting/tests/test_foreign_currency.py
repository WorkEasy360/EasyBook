"""Foreign-currency transactions are refused until multi-currency accounting exists.

Regression (phase 12 P0 remediation): documents and journals accepted any
currency and exchange rate, but nothing downstream implemented multi-currency
accounting — no rate source, no revaluation, no realised/unrealised FX
gain/loss. Worse, invoice/bill/expense/payment posting never even passed the
document's exchange rate to the journal (it defaulted to 1), so a USD 1,000
invoice posted as 1,000 in the organization's base currency: authoritative
balances, GST and reports silently wrong.

Refused at two layers: the ledger itself (create_draft_journal/post_journal —
the one path every posting takes, so nothing can reach the books) and each
document/party service, so the user gets a clear error when entering it
rather than when posting it.
"""

import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalEntry, JournalStatus
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from banking.services.bank_accounts import create_bank_account
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from purchases.services.vendors import create_vendor
from sales.models.invoice import Invoice
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice


class ForeignCurrencyRefusalTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "fx-owner@example.com")
        self.inr = make_currency("INR")
        self.usd = make_currency("USD")
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.bank = create_account(organization=self.org, code="1000", name="Bank", account_type=AccountType.ASSET)
            self.ar = create_account(organization=self.org, code="1100", name="AR", account_type=AccountType.ASSET)
            self.sales = create_account(organization=self.org, code="4000", name="Sales", account_type=AccountType.INCOME)
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.item = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales,
            )
            self.customer = create_customer(
                organization=self.org, customer_code="CUST-1", display_name="Acme Buyer", currency=self.inr
            )

    def _assert_refused(self, code, fn, **kwargs):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError) as ctx:
                fn(**kwargs)
        self.assertEqual(ctx.exception.get_codes(), code)

    def _journal_lines(self):
        return [
            {"account_id": self.bank.id, "debit": Decimal("1000.00")},
            {"account_id": self.sales.id, "credit": Decimal("1000.00")},
        ]

    # --- ledger boundary ------------------------------------------------------

    def test_journal_in_a_foreign_currency_is_refused(self):
        self._assert_refused(
            "foreign_currency_not_supported", create_draft_journal,
            organization=self.org, posting_date="2026-04-10", currency=self.usd, lines=self._journal_lines(),
        )

    def test_journal_with_a_non_unit_exchange_rate_is_refused(self):
        self._assert_refused(
            "exchange_rate_not_supported", create_draft_journal,
            organization=self.org, posting_date="2026-04-10", currency=self.inr,
            exchange_rate=Decimal("83.10"), lines=self._journal_lines(),
        )

    def test_an_existing_foreign_currency_draft_cannot_be_posted(self):
        with tenant_context(organization_id=self.org.id):
            draft = create_draft_journal(
                organization=self.org, posting_date="2026-04-10", currency=self.inr, lines=self._journal_lines()
            )
        JournalEntry.all_objects.filter(pk=draft.pk).update(currency=self.usd)
        self._assert_refused("foreign_currency_not_supported", post_journal, journal_id=draft.id, organization=self.org)
        with tenant_context(organization_id=self.org.id):
            draft.refresh_from_db()
        self.assertEqual(draft.status, JournalStatus.DRAFT)

    # --- documents and parties --------------------------------------------------

    def test_invoice_in_a_foreign_currency_is_refused(self):
        self._assert_refused(
            "foreign_currency_not_supported", create_invoice,
            organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 10), receivable_account=self.ar, currency=self.usd,
            lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("1000.00")}],
        )

    def test_invoice_with_a_non_unit_exchange_rate_is_refused(self):
        self._assert_refused(
            "exchange_rate_not_supported", create_invoice,
            organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 10), receivable_account=self.ar, exchange_rate=Decimal("83.10"),
            lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("1000.00")}],
        )

    def test_existing_foreign_currency_invoice_cannot_post_to_the_ledger(self):
        with tenant_context(organization_id=self.org.id):
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("1000.00")}],
            )
        Invoice.all_objects.filter(pk=invoice.pk).update(currency=self.usd, exchange_rate=Decimal("83.10"))
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.user)
            self.assertFalse(JournalEntry.objects.filter(source_id=str(invoice.id)).exists())

    def test_customer_in_a_foreign_currency_is_refused(self):
        self._assert_refused(
            "foreign_currency_not_supported", create_customer,
            organization=self.org, customer_code="CUST-US", display_name="US Buyer", currency=self.usd,
        )

    def test_vendor_in_a_foreign_currency_is_refused(self):
        self._assert_refused(
            "foreign_currency_not_supported", create_vendor,
            organization=self.org, vendor_code="VEN-US", display_name="US Supplier", currency=self.usd,
        )

    def test_bank_account_in_a_foreign_currency_is_refused(self):
        self._assert_refused(
            "foreign_currency_not_supported", create_bank_account,
            organization=self.org, name="USD Account", account=self.bank, currency=self.usd,
        )

    def test_base_currency_transactions_still_work(self):
        with tenant_context(organization_id=self.org.id):
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar, currency=self.inr,
                exchange_rate=Decimal("1"),
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("1000.00")}],
            )
            posted = post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.user)
        self.assertEqual(posted.status, "sent")
