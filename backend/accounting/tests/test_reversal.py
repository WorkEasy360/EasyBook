import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalStatus
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal, reverse_journal
from accounts.models import FiscalYear
from audit.models import AuditLog
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class ReversalTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "reversal-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.expense = create_account(
                organization=self.org, code="5000", name="Expense", account_type=AccountType.EXPENSE
            )
            self.bank = create_account(organization=self.org, code="1010", name="Bank", account_type=AccountType.ASSET)
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-04-10",
                currency=self.currency,
                lines=[
                    {"account_id": self.expense.id, "debit": Decimal("1000.00")},
                    {"account_id": self.bank.id, "credit": Decimal("1000.00")},
                ],
                created_by=self.user,
            )
            self.original = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

    def test_reversal_mirrors_financial_impact(self):
        with tenant_context(organization_id=self.org.id):
            reversal = reverse_journal(journal_id=self.original.id, organization=self.org, actor=self.user)
            expense_line = reversal.lines.get(account=self.expense)
            bank_line = reversal.lines.get(account=self.bank)
        self.assertEqual(expense_line.credit, Decimal("1000.00"))
        self.assertEqual(bank_line.debit, Decimal("1000.00"))

    def test_original_retained_and_marked_reversed(self):
        with tenant_context(organization_id=self.org.id):
            reverse_journal(journal_id=self.original.id, organization=self.org, actor=self.user)
            self.original.refresh_from_db()
        self.assertEqual(self.original.status, JournalStatus.REVERSED)
        self.assertTrue(self.original.pk)

    def test_reversal_links_original(self):
        with tenant_context(organization_id=self.org.id):
            reversal = reverse_journal(journal_id=self.original.id, organization=self.org, actor=self.user)
        self.assertEqual(reversal.reverses_id, self.original.id)

    def test_reversal_balances_and_is_posted(self):
        with tenant_context(organization_id=self.org.id):
            reversal = reverse_journal(journal_id=self.original.id, organization=self.org, actor=self.user)
        self.assertEqual(reversal.status, JournalStatus.POSTED)
        total_debit = sum(line.debit for line in reversal.lines.all())
        total_credit = sum(line.credit for line in reversal.lines.all())
        self.assertEqual(total_debit, total_credit)

    def test_reversal_audit_entry_generated(self):
        with tenant_context(organization_id=self.org.id):
            reverse_journal(journal_id=self.original.id, organization=self.org, actor=self.user)
            entries = AuditLog.objects.filter(action=AuditLog.Action.REVERSE, object_id=str(self.original.id))
        self.assertEqual(entries.count(), 1)

    def test_duplicate_reversal_prevented(self):
        with tenant_context(organization_id=self.org.id):
            reverse_journal(journal_id=self.original.id, organization=self.org, actor=self.user)
            with self.assertRaises(ApplicationError):
                reverse_journal(journal_id=self.original.id, organization=self.org, actor=self.user)

    def test_cannot_reverse_a_draft_journal(self):
        with tenant_context(organization_id=self.org.id):
            draft = create_draft_journal(
                organization=self.org,
                posting_date="2026-04-11",
                currency=self.currency,
                lines=[
                    {"account_id": self.expense.id, "debit": Decimal("10.00")},
                    {"account_id": self.bank.id, "credit": Decimal("10.00")},
                ],
            )
            with self.assertRaises(ApplicationError):
                reverse_journal(journal_id=draft.id, organization=self.org, actor=self.user)
