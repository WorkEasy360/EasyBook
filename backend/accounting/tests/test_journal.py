from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalStatus
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal, replace_draft_lines
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class JournalDraftTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "journal-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )

    def _make_journal(self, lines):
        with tenant_context(organization_id=self.org.id):
            return create_draft_journal(
                organization=self.org,
                posting_date="2026-04-10",
                currency=self.currency,
                lines=lines,
                created_by=self.user,
            )

    def test_balanced_draft_accepted(self):
        journal = self._make_journal(
            [
                {"account_id": self.cash.id, "debit": Decimal("100.00")},
                {"account_id": self.revenue.id, "credit": Decimal("100.00")},
            ]
        )
        self.assertEqual(journal.status, JournalStatus.DRAFT)
        with tenant_context(organization_id=self.org.id):
            self.assertEqual(journal.lines.count(), 2)

    def test_unbalanced_draft_is_allowed(self):
        journal = self._make_journal(
            [
                {"account_id": self.cash.id, "debit": Decimal("100.00")},
                {"account_id": self.revenue.id, "credit": Decimal("50.00")},
            ]
        )
        self.assertEqual(journal.status, JournalStatus.DRAFT)

    def test_line_with_both_debit_and_credit_rejected(self):
        with self.assertRaises(ApplicationError):
            self._make_journal(
                [
                    {"account_id": self.cash.id, "debit": Decimal("100.00"), "credit": Decimal("50.00")},
                    {"account_id": self.revenue.id, "credit": Decimal("100.00")},
                ]
            )

    def test_line_with_zero_both_sides_rejected(self):
        with self.assertRaises(ApplicationError):
            self._make_journal(
                [
                    {"account_id": self.cash.id, "debit": Decimal("0")},
                    {"account_id": self.revenue.id, "credit": Decimal("100.00")},
                ]
            )

    def test_single_line_rejected(self):
        with self.assertRaises(ApplicationError):
            self._make_journal([{"account_id": self.cash.id, "debit": Decimal("100.00")}])

    def test_inactive_account_rejected(self):
        with tenant_context(organization_id=self.org.id):
            from accounting.services.accounts import update_account

            update_account(account=self.revenue, is_active=False)
        with self.assertRaises(ApplicationError):
            self._make_journal(
                [
                    {"account_id": self.cash.id, "debit": Decimal("100.00")},
                    {"account_id": self.revenue.id, "credit": Decimal("100.00")},
                ]
            )

    def test_replace_draft_lines_updates_content(self):
        journal = self._make_journal(
            [
                {"account_id": self.cash.id, "debit": Decimal("100.00")},
                {"account_id": self.revenue.id, "credit": Decimal("100.00")},
            ]
        )
        with tenant_context(organization_id=self.org.id):
            replace_draft_lines(
                journal=journal,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("200.00")},
                    {"account_id": self.revenue.id, "credit": Decimal("200.00")},
                ],
            )
            self.assertEqual(journal.lines.get(account=self.cash).debit, Decimal("200.00"))
