import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalStatus
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from audit.models import AuditLog
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class PostingTestCase(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "posting-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            self.fiscal_year = FiscalYear.objects.create(
                organization=self.org,
                start_date=datetime.date(2026, 4, 1),
                end_date=datetime.date(2027, 3, 31),
            )

    def _draft(self, debit=Decimal("100.00"), credit=None, posting_date="2026-04-10"):
        credit = credit if credit is not None else debit
        with tenant_context(organization_id=self.org.id):
            return create_draft_journal(
                organization=self.org,
                posting_date=posting_date,
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": debit},
                    {"account_id": self.revenue.id, "credit": credit},
                ],
                created_by=self.user,
            )


class BalancedJournalPostingTests(PostingTestCase):
    def test_balanced_journal_posts(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
        self.assertEqual(posted.status, JournalStatus.POSTED)
        self.assertTrue(posted.journal_number)

    def test_posted_timestamp_and_user_set(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
        self.assertEqual(posted.posted_by_id, self.user.id)
        self.assertIsNotNone(posted.posted_at)

    def test_audit_entry_created_on_post(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            entries = AuditLog.objects.filter(action=AuditLog.Action.POST, object_id=str(journal.id))
        self.assertEqual(entries.count(), 1)

    def test_unbalanced_journal_rejected_on_posting(self):
        journal = self._draft(debit=Decimal("100.00"), credit=Decimal("50.00"))
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            journal.refresh_from_db()
        self.assertEqual(journal.status, JournalStatus.DRAFT)

    def test_posting_closed_period_rejected(self):
        self.fiscal_year.is_closed = True
        self.fiscal_year.save(update_fields=["is_closed"])
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

    def test_posting_without_fiscal_year_rejected(self):
        journal = self._draft(posting_date="2020-01-01")
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

    def test_duplicate_post_request_is_idempotent(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            first = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            second = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            entries = AuditLog.objects.filter(action=AuditLog.Action.POST, object_id=str(journal.id))
        self.assertEqual(first.journal_number, second.journal_number)
        self.assertEqual(entries.count(), 1)


class PostedJournalImmutabilityTests(PostingTestCase):
    def test_posted_journal_cannot_be_edited_directly(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            posted.memo = "tampered"
            with self.assertRaises(ValueError):
                posted.save()

    def test_posted_lines_cannot_be_modified(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            line = posted.lines.first()
            line.debit = Decimal("999.00")
            with self.assertRaises(ValueError):
                line.save()

    def test_posted_journal_cannot_be_deleted(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            with self.assertRaises(ValueError):
                posted.delete()

    def test_posted_lines_cannot_be_deleted(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            line = posted.lines.first()
            with self.assertRaises(ValueError):
                line.delete()

    def test_draft_journal_can_be_deleted(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            journal.delete()


class PostingRollbackTests(PostingTestCase):
    def test_failure_during_posting_leaves_no_partial_state(self):
        journal = self._draft(debit=Decimal("100.00"), credit=Decimal("50.00"))
        with tenant_context(organization_id=self.org.id):
            try:
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            except ApplicationError:
                pass
            journal.refresh_from_db()
            self.assertEqual(journal.status, JournalStatus.DRAFT)
            self.assertEqual(journal.journal_number, "")
            self.assertFalse(AuditLog.objects.filter(object_id=str(journal.id)).exists())
