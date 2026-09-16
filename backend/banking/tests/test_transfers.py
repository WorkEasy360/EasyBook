"""Transfers between own accounts, and detecting the ones nobody recorded."""

import datetime
from decimal import Decimal

from accounting.models.journal import JournalStatus
from accounting.selectors import get_trial_balance
from banking.models.statement import BankTransactionStatus
from banking.models.transfer import BankTransferStatus
from banking.services.matching import find_transfer_counterparts, unmatch
from banking.services.transfers import confirm_detected_transfer, record_transfer, void_transfer
from banking.tests.base import DAY, BankingTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context


class RecordTransferTests(BankingTestsBase):
    def test_a_transfer_is_net_zero_and_touches_no_income_or_expense(self):
        """Booking a transfer as income is the most common way a self-serve
        ledger overstates revenue."""
        with tenant_context(organization_id=self.org_a.id):
            transfer = record_transfer(
                organization=self.org_a, from_bank_account=self.bank, to_bank_account=self.savings,
                amount=Decimal("5000.00"), transfer_date=DAY, actor=self.user_a,
            )
            self.assertEqual(transfer.status, BankTransferStatus.POSTED)
            self.assertTrue(transfer.transfer_number.startswith("TRF-"))

            journal = transfer.accounting_journal
            self.assertEqual(journal.status, JournalStatus.POSTED)
            accounts = {line.account_id for line in journal.lines.all()}
            self.assertEqual(accounts, {self.gl_bank.id, self.gl_bank_two.id})
            self.assertEqual(journal.lines.get(account=self.gl_bank_two).debit, Decimal("5000.00"))
            self.assertEqual(journal.lines.get(account=self.gl_bank).credit, Decimal("5000.00"))

            self.assertTrue(get_trial_balance(organization=self.org_a, as_of_date=DAY)["is_balanced"])

    def test_a_transfer_needs_two_different_accounts(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                record_transfer(
                    organization=self.org_a, from_bank_account=self.bank, to_bank_account=self.bank,
                    amount=Decimal("100.00"), transfer_date=DAY,
                )
            self.assertEqual(ctx.exception.get_codes(), "transfer_same_account")

    def test_cross_currency_transfers_are_refused_rather_than_guessed(self):
        """The gap between the two legs is an FX gain or loss needing an
        account this phase has no convention for."""
        from accounting.models.account import AccountType
        from accounting.services.accounts import create_account
        from banking.services.bank_accounts import create_bank_account
        from core.tests.factories import make_currency

        with tenant_context(organization_id=self.org_a.id):
            usd = make_currency("USD")
            gl_usd = create_account(
                organization=self.org_a, code="1005", name="USD Bank", account_type=AccountType.ASSET
            )
            usd_bank = create_bank_account(
                organization=self.org_a, name="USD Account", account=gl_usd, currency=usd
            )
            with self.assertRaises(ApplicationError) as ctx:
                record_transfer(
                    organization=self.org_a, from_bank_account=self.bank, to_bank_account=usd_bank,
                    amount=Decimal("100.00"), transfer_date=DAY,
                )
            self.assertEqual(ctx.exception.get_codes(), "transfer_currency_mismatch")

    def test_financial_fields_are_frozen_after_creation(self):
        with tenant_context(organization_id=self.org_a.id):
            transfer = record_transfer(
                organization=self.org_a, from_bank_account=self.bank, to_bank_account=self.savings,
                amount=Decimal("5000.00"), transfer_date=DAY,
            )
            transfer.amount = Decimal("9999.00")
            with self.assertRaises(ValueError) as ctx:
                transfer.save()
            self.assertIn("immutable", str(ctx.exception))

    def test_voiding_reverses_the_journal(self):
        with tenant_context(organization_id=self.org_a.id):
            transfer = record_transfer(
                organization=self.org_a, from_bank_account=self.bank, to_bank_account=self.savings,
                amount=Decimal("5000.00"), transfer_date=DAY,
            )
            journal_id = transfer.accounting_journal_id
            voided = void_transfer(
                transfer_id=transfer.id, organization=self.org_a, actor=self.user_a, reason="Wrong accounts"
            )
            self.assertEqual(voided.status, BankTransferStatus.VOID)

            from accounting.models.journal import JournalEntry

            self.assertEqual(JournalEntry.objects.get(pk=journal_id).status, JournalStatus.REVERSED)

    def test_voiding_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            transfer = record_transfer(
                organization=self.org_a, from_bank_account=self.bank, to_bank_account=self.savings,
                amount=Decimal("5000.00"), transfer_date=DAY,
            )
            void_transfer(transfer_id=transfer.id, organization=self.org_a, reason="x")
            again = void_transfer(transfer_id=transfer.id, organization=self.org_a, reason="x")
            self.assertEqual(again.status, BankTransferStatus.VOID)

    def test_a_matched_transfer_cannot_be_voided_underneath_its_statement_lines(self):
        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00", description="TRANSFER TO SAVINGS")
            into = self._txn(amount="5000.00", bank_account=self.savings, description="TRANSFER IN")
            transfer = confirm_detected_transfer(
                organization=self.org_a, outflow_transaction_id=out.id,
                inflow_transaction_id=into.id, actor=self.user_a,
            )
            with self.assertRaises(ApplicationError) as ctx:
                void_transfer(transfer_id=transfer.id, organization=self.org_a, reason="oops")
            self.assertEqual(ctx.exception.get_codes(), "transfer_has_matches")


class TransferDetectionTests(BankingTestsBase):
    def test_the_opposite_leg_in_another_account_is_detected(self):
        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00", description="TO SAVINGS")
            self._txn(
                amount="5000.00", bank_account=self.savings,
                transaction_date=DAY + datetime.timedelta(days=1), description="FROM CURRENT",
            )
            candidates = find_transfer_counterparts(transaction=out, organization=self.org_a)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].bank_account_id, self.savings.id)

    def test_a_line_in_the_same_account_is_never_a_transfer_counterpart(self):
        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00", description="ONE")
            self._txn(amount="5000.00", description="TWO")
            self.assertEqual(find_transfer_counterparts(transaction=out, organization=self.org_a), [])

    def test_detection_uses_a_narrow_window(self):
        """A wide window over a business with several accounts turns every
        round-number payment into a transfer candidate."""
        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00")
            self._txn(
                amount="5000.00", bank_account=self.savings,
                transaction_date=DAY + datetime.timedelta(days=20),
            )
            self.assertEqual(find_transfer_counterparts(transaction=out, organization=self.org_a), [])

    def test_confirming_a_pair_posts_exactly_one_journal_for_both_legs(self):
        """Two lines are two views of one movement. Booking each separately
        would double it and leave both accounts wrong in opposite directions
        — which nets to zero on the trial balance and is therefore invisible
        until someone reads the cash flow statement."""
        from accounting.models.journal import JournalEntry

        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00", description="TO SAVINGS")
            into = self._txn(
                amount="5000.00", bank_account=self.savings,
                transaction_date=DAY + datetime.timedelta(days=1), description="FROM CURRENT",
            )
            before = JournalEntry.objects.count()

            transfer = confirm_detected_transfer(
                organization=self.org_a, outflow_transaction_id=out.id,
                inflow_transaction_id=into.id, actor=self.user_a,
            )
            self.assertEqual(JournalEntry.objects.count(), before + 1)
            self.assertEqual(transfer.transfer_date, DAY, "the date the money left")

            out.refresh_from_db()
            into.refresh_from_db()
            self.assertEqual(out.status, BankTransactionStatus.MATCHED)
            self.assertEqual(into.status, BankTransactionStatus.MATCHED)

    def test_both_legs_of_one_transfer_can_be_matched(self):
        """A transfer's capacity is one full amount PER LEG. Asking the
        unqualified 'is this already matched?' question would refuse the
        second leg and leave every internal movement half-reconciled."""
        with tenant_context(organization_id=self.org_a.id):
            transfer = record_transfer(
                organization=self.org_a, from_bank_account=self.bank, to_bank_account=self.savings,
                amount=Decimal("5000.00"), transfer_date=DAY, actor=self.user_a,
            )
            out = self._txn(amount="-5000.00", description="TO SAVINGS")
            into = self._txn(amount="5000.00", bank_account=self.savings, description="FROM CURRENT")

            from banking.services.matching import auto_match

            self.assertIsNotNone(
                auto_match(transaction_id=out.id, organization=self.org_a, actor=self.user_a)
            )
            self.assertIsNotNone(
                auto_match(transaction_id=into.id, organization=self.org_a, actor=self.user_a)
            )
            self.assertEqual(transfer.bank_matches.filter(is_confirmed=True).count(), 2)

    def test_a_pair_must_be_one_in_and_one_out(self):
        with tenant_context(organization_id=self.org_a.id):
            first = self._txn(amount="-5000.00")
            second = self._txn(amount="-5000.00", bank_account=self.savings)
            with self.assertRaises(ApplicationError) as ctx:
                confirm_detected_transfer(
                    organization=self.org_a, outflow_transaction_id=first.id,
                    inflow_transaction_id=second.id,
                )
            self.assertEqual(ctx.exception.get_codes(), "transfer_direction_invalid")

    def test_a_pair_must_be_the_same_amount(self):
        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00")
            into = self._txn(amount="4900.00", bank_account=self.savings)
            with self.assertRaises(ApplicationError) as ctx:
                confirm_detected_transfer(
                    organization=self.org_a, outflow_transaction_id=out.id,
                    inflow_transaction_id=into.id,
                )
            self.assertEqual(ctx.exception.get_codes(), "transfer_amount_mismatch")

    def test_an_unmatched_leg_returns_to_the_queue(self):
        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00")
            into = self._txn(amount="5000.00", bank_account=self.savings)
            transfer = confirm_detected_transfer(
                organization=self.org_a, outflow_transaction_id=out.id,
                inflow_transaction_id=into.id, actor=self.user_a,
            )
            match = transfer.bank_matches.get(transaction=out)
            unmatch(match_id=match.id, organization=self.org_a, actor=self.user_a)

            out.refresh_from_db()
            into.refresh_from_db()
            self.assertEqual(out.status, BankTransactionStatus.UNMATCHED)
            self.assertEqual(into.status, BankTransactionStatus.MATCHED, "the other leg is unaffected")
