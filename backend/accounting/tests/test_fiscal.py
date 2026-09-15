import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalStatus
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class FiscalControlTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "fiscal-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            self.open_year = FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.closed_year = FiscalYear.objects.create(
                organization=self.org,
                start_date=datetime.date(2025, 4, 1),
                end_date=datetime.date(2026, 3, 31),
                is_closed=True,
            )

    def _draft(self, posting_date):
        with tenant_context(organization_id=self.org.id):
            return create_draft_journal(
                organization=self.org,
                posting_date=posting_date,
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("10.00")},
                    {"account_id": self.revenue.id, "credit": Decimal("10.00")},
                ],
                created_by=self.user,
            )

    def test_posting_to_open_period_works(self):
        journal = self._draft("2026-05-01")
        with tenant_context(organization_id=self.org.id):
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
        self.assertEqual(posted.status, JournalStatus.POSTED)

    def test_posting_to_closed_period_fails(self):
        journal = self._draft("2025-06-01")
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
