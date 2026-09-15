import datetime
import threading
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalStatus
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal, reverse_journal
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class ConcurrentPostingTests(TransactionTestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "concurrency-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-04-10",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("100.00")},
                    {"account_id": self.revenue.id, "credit": Decimal("100.00")},
                ],
                created_by=self.user,
            )

    def test_concurrent_posting_of_same_journal_yields_one_result(self):
        results = []
        errors = []
        lock = threading.Lock()

        def do_post():
            try:
                with tenant_context(organization_id=self.org.id):
                    posted = post_journal(journal_id=self.journal.id, organization=self.org, actor=self.user)
                with lock:
                    results.append(posted.journal_number)
            except Exception as exc:  # surface thread failures instead of losing them silently
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_post) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1, "concurrent posting must converge on a single journal number")

        with tenant_context(organization_id=self.org.id):
            self.journal.refresh_from_db()
        self.assertEqual(self.journal.status, JournalStatus.POSTED)

    def test_concurrent_posting_distinct_journals_get_unique_numbers(self):
        journals = []
        with tenant_context(organization_id=self.org.id):
            for _ in range(8):
                journals.append(
                    create_draft_journal(
                        organization=self.org,
                        posting_date="2026-04-11",
                        currency=self.currency,
                        lines=[
                            {"account_id": self.cash.id, "debit": Decimal("10.00")},
                            {"account_id": self.revenue.id, "credit": Decimal("10.00")},
                        ],
                        created_by=self.user,
                    )
                )

        numbers = []
        errors = []
        lock = threading.Lock()

        def do_post(journal_id):
            try:
                with tenant_context(organization_id=self.org.id):
                    posted = post_journal(journal_id=journal_id, organization=self.org, actor=self.user)
                with lock:
                    numbers.append(posted.journal_number)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_post, args=(j.id,)) for j in journals]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(numbers), 8)
        self.assertEqual(len(numbers), len(set(numbers)), "journal numbers must remain unique under concurrency")

    def test_concurrent_reversal_of_same_journal_yields_one_reversal(self):
        with tenant_context(organization_id=self.org.id):
            post_journal(journal_id=self.journal.id, organization=self.org, actor=self.user)

        results = []
        errors = []
        lock = threading.Lock()

        def do_reverse():
            try:
                with tenant_context(organization_id=self.org.id):
                    reversal = reverse_journal(journal_id=self.journal.id, organization=self.org, actor=self.user)
                with lock:
                    results.append(reversal.id)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_reverse) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread should succeed; the rest must fail with a
        # domain error (already reversed), never create a second reversal.
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 4)


class RawSQLRLSTests(TransactionTestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "rls-acct-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "rls-acct-b@example.com")
        with tenant_context(organization_id=self.org_a.id):
            self.account_a = create_account(
                organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET
            )
        with tenant_context(organization_id=self.org_b.id):
            self.account_b = create_account(
                organization=self.org_b, code="1000", name="Cash", account_type=AccountType.ASSET
            )

    def test_rls_blocks_direct_sql_without_tenant_context(self):
        from core.tenancy import clear_tenant_context

        clear_tenant_context()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM accounting_account")
            rows = cursor.fetchall()
        self.assertEqual(rows, [])

    def test_rls_direct_sql_scoped_to_org_a_cannot_see_org_b(self):
        with tenant_context(organization_id=self.org_a.id):
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM accounting_account")
                ids = {row[0] for row in cursor.fetchall()}
        self.assertEqual(ids, {self.account_a.id})
