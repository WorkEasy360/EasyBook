"""Period reconciliation: the three balances, and the refusal to close over
a difference."""

import datetime
from decimal import Decimal

from banking.models.reconciliation import ReconciliationStatus
from banking.models.statement import BankTransactionStatus
from banking.selectors import (
    get_bank_account_summary,
    get_book_balance,
    get_cleared_balance,
    get_reconciliation_summary,
    get_statement_balance,
)
from banking.services.bank_accounts import update_bank_account
from banking.services.matching import categorize_transaction, create_match, uncategorize_transaction, unmatch
from banking.services.reconciliation import (
    abandon_reconciliation,
    complete_reconciliation,
    reopen_reconciliation,
    start_reconciliation,
)
from banking.services.transactions import exclude_transaction, restore_transaction
from banking.tests.base import PERIOD_END, PERIOD_START, BankingTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context


class BalanceTests(BankingTestsBase):
    def test_statement_balance_runs_from_the_opening_anchor(self):
        with tenant_context(organization_id=self.org_a.id):
            update_bank_account(
                bank_account=self.bank, opening_balance=Decimal("10000.00"),
                opening_balance_date=datetime.date(2026, 3, 31),
            )
            self.bank.refresh_from_db()
            self._txn(amount="1000.00")
            self._txn(amount="-250.00")
            self.assertEqual(
                get_statement_balance(bank_account=self.bank), Decimal("10750.00")
            )

    def test_transactions_on_or_before_the_anchor_date_are_not_counted_twice(self):
        """They are already inside the opening balance."""
        with tenant_context(organization_id=self.org_a.id):
            update_bank_account(
                bank_account=self.bank, opening_balance=Decimal("10000.00"),
                opening_balance_date=datetime.date(2026, 4, 5),
            )
            self.bank.refresh_from_db()
            self._txn(amount="500.00", transaction_date=datetime.date(2026, 4, 5))
            self._txn(amount="1000.00", transaction_date=datetime.date(2026, 4, 6))
            self.assertEqual(get_statement_balance(bank_account=self.bank), Decimal("11000.00"))

    def test_cleared_balance_counts_only_matched_lines(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            matched = self._txn(amount="1000.00", description="NEFT ACME LTD")
            create_match(
                transaction_id=matched.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
            )
            self._txn(amount="-250.00", description="UNEXPLAINED")

            self.assertEqual(get_statement_balance(bank_account=self.bank), Decimal("750.00"))
            self.assertEqual(get_cleared_balance(bank_account=self.bank), Decimal("1000.00"))

    def test_book_balance_is_signed_as_a_statement_would_show_it(self):
        """Not by normal balance — a credit card's book balance has to come
        out negative so it compares against a card statement with no special
        case."""
        with tenant_context(organization_id=self.org_a.id):
            charge = self._txn(amount="-4500.00", bank_account=self.card, description="AWS")
            categorize_transaction(
                transaction_id=charge.id, organization=self.org_a, account=self.gl_office,
                actor=self.user_a,
            )
            self.assertEqual(get_book_balance(bank_account=self.card), Decimal("-4500.00"))
            self.assertEqual(get_statement_balance(bank_account=self.card), Decimal("-4500.00"))

    def test_book_balance_returns_to_zero_after_a_categorization_is_reversed(self):
        with tenant_context(organization_id=self.org_a.id):
            charge = self._txn(amount="-4500.00", bank_account=self.card, description="AWS")
            match = categorize_transaction(
                transaction_id=charge.id, organization=self.org_a, account=self.gl_office,
                actor=self.user_a,
            )
            uncategorize_transaction(match_id=match.id, organization=self.org_a, actor=self.user_a)
            self.assertEqual(get_book_balance(bank_account=self.card), Decimal("0"))

    def test_excluded_lines_leave_the_statement_balance(self):
        with tenant_context(organization_id=self.org_a.id):
            self._txn(amount="1000.00")
            artefact = self._txn(amount="-1.00", description="BALANCE ROW")
            self.assertEqual(get_statement_balance(bank_account=self.bank), Decimal("999.00"))

            exclude_transaction(
                transaction_id=artefact.id, organization=self.org_a,
                reason="Header row the parser read as data", actor=self.user_a,
            )
            self.assertEqual(get_statement_balance(bank_account=self.bank), Decimal("1000.00"))

    def test_the_summary_separates_unexplained_statement_from_uncleared_book(self):
        """Book entries the bank has not shown yet — uncleared cheques — are
        normal, and are reported rather than treated as errors."""
        with tenant_context(organization_id=self.org_a.id):
            # A payment in the books with no statement line yet.
            self._customer_payment(amount=Decimal("2000.00"))
            # A statement line with nothing in the books.
            self._txn(amount="-118.00", description="BANK CHARGES")

            summary = get_bank_account_summary(bank_account=self.bank)
            self.assertEqual(summary["book_balance"], Decimal("2000.00"))
            self.assertEqual(summary["statement_balance"], Decimal("-118.00"))
            self.assertEqual(summary["cleared_balance"], Decimal("0"))
            self.assertEqual(summary["unexplained_statement_amount"], Decimal("-118.00"))
            self.assertEqual(summary["uncleared_book_amount"], Decimal("2000.00"))
            self.assertEqual(summary["open_transaction_count"], 1)


class ExclusionTests(BankingTestsBase):
    def test_excluding_requires_a_reason(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-100.00")
            with self.assertRaises(ApplicationError) as ctx:
                exclude_transaction(
                    transaction_id=transaction.id, organization=self.org_a, reason="  "
                )
            self.assertEqual(ctx.exception.get_codes(), "exclusion_reason_required")

    def test_a_matched_transaction_cannot_be_excluded(self):
        """If it is matched to a document, it demonstrably IS real."""
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00")
            create_match(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
            )
            with self.assertRaises(ApplicationError) as ctx:
                exclude_transaction(
                    transaction_id=transaction.id, organization=self.org_a, reason="tidying up"
                )
            self.assertEqual(ctx.exception.get_codes(), "cannot_exclude_matched_transaction")

    def test_restoring_returns_a_line_to_the_queue(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-100.00")
            exclude_transaction(
                transaction_id=transaction.id, organization=self.org_a, reason="artefact"
            )
            restored = restore_transaction(transaction_id=transaction.id, organization=self.org_a)
            self.assertEqual(restored.status, BankTransactionStatus.UNMATCHED)
            self.assertEqual(restored.excluded_reason, "")


class ReconciliationLifecycleTests(BankingTestsBase):
    def _explained_period(self):
        """One matched deposit and one categorized charge: 1000 - 118 = 882."""
        payment = self._customer_payment(amount=Decimal("1000.00"))
        deposit = self._txn(amount="1000.00", description="NEFT ACME LTD")
        create_match(
            transaction_id=deposit.id, organization=self.org_a,
            counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
        )
        charge = self._txn(amount="-118.00", description="BANK CHARGES")
        categorize_transaction(
            transaction_id=charge.id, organization=self.org_a, account=self.gl_charges,
            actor=self.user_a,
        )
        return deposit, charge

    def test_a_balanced_fully_explained_period_completes(self):
        with tenant_context(organization_id=self.org_a.id):
            deposit, _charge = self._explained_period()
            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("882.00"), actor=self.user_a,
            )
            summary = get_reconciliation_summary(reconciliation=reconciliation)
            self.assertTrue(summary["can_complete"])
            self.assertEqual(summary["difference"], Decimal("0"))

            completed = complete_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a, actor=self.user_a
            )
            self.assertEqual(completed.status, ReconciliationStatus.COMPLETED)
            self.assertEqual(completed.cleared_balance, Decimal("882.00"))
            self.assertEqual(completed.completed_by_id, self.user_a.id)

            deposit.refresh_from_db()
            self.assertEqual(deposit.reconciliation_id, reconciliation.id)

    def test_an_unmatched_line_blocks_completion(self):
        with tenant_context(organization_id=self.org_a.id):
            self._explained_period()
            self._txn(amount="-500.00", description="WHAT WAS THIS")
            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("382.00"),
            )
            with self.assertRaises(ApplicationError) as ctx:
                complete_reconciliation(
                    reconciliation_id=reconciliation.id, organization=self.org_a
                )
            self.assertEqual(ctx.exception.get_codes(), "reconciliation_has_open_transactions")

    def test_a_difference_blocks_completion_and_there_is_no_override(self):
        """A reconciliation that can be forced closed certifies nothing."""
        with tenant_context(organization_id=self.org_a.id):
            self._explained_period()
            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("900.00"),
            )
            summary = get_reconciliation_summary(reconciliation=reconciliation)
            self.assertEqual(summary["difference"], Decimal("18.00"))
            self.assertFalse(summary["can_complete"])

            with self.assertRaises(ApplicationError) as ctx:
                complete_reconciliation(
                    reconciliation_id=reconciliation.id, organization=self.org_a
                )
            self.assertEqual(ctx.exception.get_codes(), "reconciliation_out_of_balance")

    def test_completing_twice_is_a_no_op(self):
        with tenant_context(organization_id=self.org_a.id):
            self._explained_period()
            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("882.00"),
            )
            first = complete_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a, actor=self.user_a
            )
            again = complete_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a, actor=self.user_a
            )
            self.assertEqual(again.completed_at, first.completed_at)

    def test_only_one_reconciliation_may_be_open_per_account(self):
        with tenant_context(organization_id=self.org_a.id):
            start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("0.00"),
            )
            with self.assertRaises(ApplicationError) as ctx:
                start_reconciliation(
                    organization=self.org_a, bank_account=self.bank,
                    statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                    statement_closing_balance=Decimal("0.00"),
                )
            self.assertEqual(ctx.exception.get_codes(), "reconciliation_already_open")

    def test_a_period_overlapping_a_completed_one_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            self._explained_period()
            first = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("882.00"),
            )
            complete_reconciliation(reconciliation_id=first.id, organization=self.org_a)

            with self.assertRaises(ApplicationError) as ctx:
                start_reconciliation(
                    organization=self.org_a, bank_account=self.bank,
                    statement_start_date=datetime.date(2026, 4, 15),
                    statement_end_date=datetime.date(2026, 5, 15),
                    statement_closing_balance=Decimal("882.00"),
                )
            self.assertEqual(ctx.exception.get_codes(), "reconciliation_period_overlap")

    def test_an_abandoned_reconciliation_locks_nothing(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-100.00")
            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("-100.00"),
            )
            abandoned = abandon_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a, reason="wrong statement"
            )
            self.assertEqual(abandoned.status, ReconciliationStatus.ABANDONED)
            transaction.refresh_from_db()
            self.assertIsNone(transaction.reconciliation_id)


class ReconciliationLockTests(BankingTestsBase):
    def _completed(self):
        payment = self._customer_payment(amount=Decimal("1000.00"))
        deposit = self._txn(amount="1000.00", description="NEFT ACME LTD")
        match = create_match(
            transaction_id=deposit.id, organization=self.org_a,
            counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
        )
        reconciliation = start_reconciliation(
            organization=self.org_a, bank_account=self.bank,
            statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
            statement_closing_balance=Decimal("1000.00"), actor=self.user_a,
        )
        complete_reconciliation(
            reconciliation_id=reconciliation.id, organization=self.org_a, actor=self.user_a
        )
        return reconciliation, deposit, match

    def test_a_line_in_a_completed_period_cannot_be_rematched(self):
        """Editing underneath a signed-off balance would change it silently."""
        with tenant_context(organization_id=self.org_a.id):
            _, _deposit, match = self._completed()
            with self.assertRaises(ApplicationError) as ctx:
                unmatch(match_id=match.id, organization=self.org_a, actor=self.user_a)
            self.assertEqual(ctx.exception.get_codes(), "transaction_reconciled")

    def test_a_line_in_a_completed_period_cannot_be_excluded(self):
        with tenant_context(organization_id=self.org_a.id):
            _, deposit, _ = self._completed()
            with self.assertRaises(ApplicationError) as ctx:
                exclude_transaction(
                    transaction_id=deposit.id, organization=self.org_a, reason="tidy"
                )
            self.assertEqual(ctx.exception.get_codes(), "transaction_reconciled")

    def test_reopening_requires_a_reason_and_unlocks_the_period(self):
        with tenant_context(organization_id=self.org_a.id):
            reconciliation, deposit, match = self._completed()

            with self.assertRaises(ApplicationError) as ctx:
                reopen_reconciliation(
                    reconciliation_id=reconciliation.id, organization=self.org_a, reason=""
                )
            self.assertEqual(ctx.exception.get_codes(), "reopen_reason_required")

            reopened = reopen_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a,
                reason="Matched to the wrong customer", actor=self.user_a,
            )
            self.assertEqual(reopened.status, ReconciliationStatus.IN_PROGRESS)
            self.assertIn("Matched to the wrong customer", reopened.notes)
            # The certified figure stays on the record until it is re-earned.
            self.assertEqual(reopened.cleared_balance, Decimal("1000.00"))

            unmatch(match_id=match.id, organization=self.org_a, actor=self.user_a)
            deposit.refresh_from_db()
            self.assertEqual(deposit.status, BankTransactionStatus.UNMATCHED)

    def test_reopening_is_audited(self):
        from audit.models import AuditLog

        with tenant_context(organization_id=self.org_a.id):
            reconciliation, _, _ = self._completed()
            reopen_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a,
                reason="Bank reissued the statement", actor=self.user_a,
            )
            # Selected by action, not by "the most recent row": two audit
            # entries written in the same clock tick can share a created_at
            # to the microsecond, so ordering alone does not identify one.
            entry = AuditLog.objects.get(
                object_type="banking.BankReconciliation",
                object_id=str(reconciliation.id),
                action=AuditLog.Action.UPDATE,
            )
            self.assertEqual(entry.changes["reason"], "Bank reissued the statement")
            self.assertEqual(entry.actor_id, self.user_a.id)
