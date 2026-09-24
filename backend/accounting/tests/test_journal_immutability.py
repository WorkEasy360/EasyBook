"""Posted journals are immutable and always balanced — at every layer.

A journal that has left DRAFT is an accounting fact. These tests pin that no
application path (service, model instance, queryset bulk op) and no
interleaving of concurrent requests can change its lines or header, and that
the database itself refuses a non-draft journal that does not balance.
"""
import datetime
import threading
from decimal import Decimal

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalEntry, JournalLine, JournalStatus
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal, replace_draft_lines
from accounting.services.posting import post_journal, reverse_journal
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner

UNBALANCED_NON_DRAFT_SQL = """
    SELECT je.id
    FROM accounting_journalentry je
    LEFT JOIN accounting_journalline jl ON jl.journal_entry_id = je.id
    WHERE je.status <> 'draft'
    GROUP BY je.id
    HAVING COALESCE(SUM(jl.base_debit), 0) <> COALESCE(SUM(jl.base_credit), 0)
        OR COALESCE(SUM(jl.base_debit), 0) = 0
"""


class _JournalFixture:
    def _setup_ledger(self, email):
        self.org, self.user, _ = make_org_with_owner("Immutability Org", email)
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )

    def _draft(self, amount="100.00"):
        with tenant_context(organization_id=self.org.id):
            return create_draft_journal(
                organization=self.org,
                posting_date=datetime.date(2026, 5, 1),
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal(amount)},
                    {"account_id": self.revenue.id, "credit": Decimal(amount)},
                ],
                created_by=self.user,
            )

    def _lines(self, journal_id):
        with tenant_context(organization_id=self.org.id):
            return list(
                JournalLine.objects.filter(journal_entry_id=journal_id)
                .order_by("line_number")
                .values_list("debit", "credit")
            )

    def _unbalanced_non_draft_ids(self):
        with tenant_context(organization_id=self.org.id), connection.cursor() as cursor:
            cursor.execute(UNBALANCED_NON_DRAFT_SQL)
            return [row[0] for row in cursor.fetchall()]

    def _replacement(self, debit="999.00", credit="1.00"):
        return [
            {"account_id": self.cash.id, "debit": Decimal(debit)},
            {"account_id": self.revenue.id, "credit": Decimal(credit)},
        ]


class StaleDraftReplaceTests(_JournalFixture, TransactionTestCase):
    def setUp(self):
        self._setup_ledger("immut-stale@example.com")

    def test_stale_draft_copy_cannot_change_a_posted_journal(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            stale = JournalEntry.objects.get(pk=journal.pk)  # loaded while still a draft
            post_journal(journal_id=journal.pk, organization=self.org, actor=self.user)
            before = self._lines(journal.pk)

            with self.assertRaises(ApplicationError) as ctx:
                replace_draft_lines(journal=stale, lines=self._replacement())
        self.assertEqual(ctx.exception.detail.code, "journal_not_draft")
        self.assertEqual(self._lines(journal.pk), before)
        self.assertEqual(self._unbalanced_non_draft_ids(), [])

    def test_draft_lines_can_still_be_replaced(self):
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            replace_draft_lines(journal=journal, lines=self._replacement("250.00", "250.00"))
        self.assertEqual(self._lines(journal.pk), [(Decimal("250.00"), Decimal("0")), (Decimal("0"), Decimal("250.00"))])


class ConcurrentReplaceVersusPostTests(_JournalFixture, TransactionTestCase):
    """Real Postgres, real threads, real row locks."""

    def setUp(self):
        self._setup_ledger("immut-race@example.com")

    def test_replace_waits_for_an_in_flight_post_then_refuses(self):
        # Deterministic interleaving: the poster holds the journal row lock
        # (exactly what post_journal takes first) while the replacer starts
        # from a copy it read while the journal was still a draft.
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            stale = JournalEntry.objects.get(pk=journal.pk)

        poster_has_lock = threading.Event()
        outcome = {}

        def poster():
            try:
                with tenant_context(organization_id=self.org.id), transaction.atomic():
                    JournalEntry.objects.select_for_update().get(pk=journal.pk)
                    poster_has_lock.set()
                    # Give the replacer time to reach its own lock attempt.
                    threading.Event().wait(0.5)
                    post_journal(journal_id=journal.pk, organization=self.org, actor=self.user)
            except Exception as exc:
                outcome["poster"] = exc
            finally:
                poster_has_lock.set()
                connection.close()

        def replacer():
            poster_has_lock.wait(5)
            try:
                with tenant_context(organization_id=self.org.id):
                    replace_draft_lines(journal=stale, lines=self._replacement())
                outcome["replacer"] = "replaced"
            except ApplicationError as exc:
                outcome["replacer"] = exc.detail.code
            except Exception as exc:
                outcome["replacer"] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=poster), threading.Thread(target=replacer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)

        self.assertNotIn("poster", outcome)
        self.assertEqual(outcome.get("replacer"), "journal_not_draft")
        with tenant_context(organization_id=self.org.id):
            self.assertEqual(JournalEntry.objects.get(pk=journal.pk).status, JournalStatus.POSTED)
        self.assertEqual(self._lines(journal.pk), [(Decimal("100.00"), Decimal("0")), (Decimal("0"), Decimal("100.00"))])
        self.assertEqual(self._unbalanced_non_draft_ids(), [])

    def test_free_running_race_never_leaves_an_unbalanced_posted_journal(self):
        for _ in range(5):
            journal = self._draft()
            with tenant_context(organization_id=self.org.id):
                stale = JournalEntry.objects.get(pk=journal.pk)
            barrier = threading.Barrier(2)
            errors = []

            def post(journal=journal, barrier=barrier, errors=errors):
                try:
                    barrier.wait(5)
                    with tenant_context(organization_id=self.org.id):
                        post_journal(journal_id=journal.pk, organization=self.org, actor=self.user)
                except ApplicationError:
                    pass  # e.g. the replacer's unbalanced lines landed first: posting is refused, correctly
                except Exception as exc:
                    errors.append(exc)
                finally:
                    connection.close()

            def replace(stale=stale, barrier=barrier, errors=errors):
                try:
                    barrier.wait(5)
                    with tenant_context(organization_id=self.org.id):
                        replace_draft_lines(journal=stale, lines=self._replacement())
                except ApplicationError:
                    pass
                except Exception as exc:
                    errors.append(exc)
                finally:
                    connection.close()

            threads = [threading.Thread(target=post), threading.Thread(target=replace)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(15)
            self.assertEqual(errors, [])
        self.assertEqual(self._unbalanced_non_draft_ids(), [])


class PostedLinesRefuseEveryWritePathTests(_JournalFixture, TransactionTestCase):
    def setUp(self):
        self._setup_ledger("immut-paths@example.com")
        journal = self._draft()
        with tenant_context(organization_id=self.org.id):
            self.posted = post_journal(journal_id=journal.pk, organization=self.org, actor=self.user)
        self.before = self._lines(self.posted.pk)

    def _assert_refused(self, fn, exc_types=(DatabaseError, ValueError)):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(exc_types):
                with transaction.atomic():
                    fn()
        self.assertEqual(self._lines(self.posted.pk), self.before)
        self.assertEqual(self._unbalanced_non_draft_ids(), [])

    def test_queryset_update_of_posted_lines_is_refused(self):
        self._assert_refused(
            lambda: JournalLine.objects.filter(journal_entry=self.posted, debit__gt=0).update(
                debit=Decimal("5"), base_debit=Decimal("5")
            ),
            DatabaseError,
        )

    def test_queryset_delete_of_posted_lines_is_refused(self):
        self._assert_refused(lambda: JournalLine.objects.filter(journal_entry=self.posted).delete(), DatabaseError)

    def test_related_manager_delete_of_posted_lines_is_refused(self):
        self._assert_refused(lambda: self.posted.lines.all().delete(), DatabaseError)

    def test_adding_a_line_to_a_posted_journal_is_refused(self):
        self._assert_refused(
            lambda: JournalLine.objects.create(
                organization=self.org,
                journal_entry=self.posted,
                account=self.cash,
                line_number=3,
                debit=Decimal("1"),
                base_debit=Decimal("1"),
            )
        )

    def test_bulk_create_on_a_posted_journal_is_refused(self):
        self._assert_refused(
            lambda: JournalLine.objects.bulk_create(
                [
                    JournalLine(
                        organization=self.org,
                        journal_entry=self.posted,
                        account=self.cash,
                        line_number=3,
                        debit=Decimal("1"),
                        base_debit=Decimal("1"),
                    )
                ]
            ),
            DatabaseError,
        )

    def test_instance_save_of_a_posted_line_is_refused(self):
        def mutate():
            line = JournalLine.objects.filter(journal_entry=self.posted).first()
            line.debit = line.debit + 1 if line.debit else line.debit
            line.credit = line.credit + 1 if line.credit else line.credit
            line.save()

        self._assert_refused(mutate)

    def test_queryset_update_of_posted_header_is_refused(self):
        self._assert_refused(
            lambda: JournalEntry.objects.filter(pk=self.posted.pk).update(posting_date=datetime.date(2026, 6, 1)),
            DatabaseError,
        )

    def test_posted_journal_cannot_go_back_to_draft(self):
        self._assert_refused(
            lambda: JournalEntry.objects.filter(pk=self.posted.pk).update(status=JournalStatus.DRAFT), DatabaseError
        )

    def test_queryset_delete_of_posted_header_is_refused(self):
        self._assert_refused(lambda: JournalEntry.objects.filter(pk=self.posted.pk).delete(), DatabaseError)

    def test_reversal_workflow_still_works(self):
        with tenant_context(organization_id=self.org.id):
            reversal = reverse_journal(journal_id=self.posted.pk, organization=self.org, actor=self.user)
            self.assertEqual(JournalEntry.objects.get(pk=self.posted.pk).status, JournalStatus.REVERSED)
            self.assertEqual(reversal.status, JournalStatus.POSTED)
        self.assertEqual(self._lines(self.posted.pk), self.before)
        self.assertEqual(self._unbalanced_non_draft_ids(), [])


class DatabaseRefusesUnbalancedNonDraftJournalsTests(_JournalFixture, TransactionTestCase):
    """The database, not only post_journal, guarantees debits == credits."""

    def setUp(self):
        self._setup_ledger("immut-balance@example.com")

    def _unbalanced_draft(self):
        with tenant_context(organization_id=self.org.id):
            return create_draft_journal(
                organization=self.org,
                posting_date=datetime.date(2026, 5, 1),
                currency=self.currency,
                lines=self._replacement(),
                created_by=self.user,
            )

    def test_bypassing_post_journal_cannot_post_an_unbalanced_journal(self):
        draft = self._unbalanced_draft()
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(DatabaseError), transaction.atomic():
                JournalEntry.objects.filter(pk=draft.pk).update(status=JournalStatus.POSTED)
            self.assertEqual(JournalEntry.objects.get(pk=draft.pk).status, JournalStatus.DRAFT)

    def test_a_journal_cannot_be_inserted_already_posted(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(DatabaseError), transaction.atomic():
                JournalEntry.objects.create(
                    organization=self.org,
                    posting_date=datetime.date(2026, 5, 1),
                    currency=self.currency,
                    status=JournalStatus.POSTED,
                )

    def test_post_journal_still_rejects_unbalanced_with_its_own_error(self):
        draft = self._unbalanced_draft()
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError) as ctx:
                post_journal(journal_id=draft.pk, organization=self.org, actor=self.user)
        self.assertEqual(ctx.exception.detail.code, "journal_unbalanced")
