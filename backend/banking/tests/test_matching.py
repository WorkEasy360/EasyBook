"""The matcher: what it finds, what it refuses to decide, and the invariant
that matching posts no accounting."""

import datetime
from decimal import Decimal

from accounting.models.journal import JournalEntry, JournalStatus
from banking.models.match import BankTransactionMatch, MatchType, SuggestionSource
from banking.models.statement import BankTransactionStatus
from banking.services.matching import (
    auto_match,
    categorize_transaction,
    confirm_match,
    create_match,
    find_candidates,
    record_ai_suggestion,
    suggest_matches,
    uncategorize_transaction,
    unmatch,
)
from banking.tests.base import DAY, BankingTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context


class CandidateDiscoveryTests(BankingTestsBase):
    def test_an_inbound_line_finds_the_customer_payment_that_created_it(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00", description="NEFT ACME LTD")

            candidates = find_candidates(transaction=transaction, organization=self.org_a)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["counterpart_field"], "customer_payment")
            self.assertEqual(candidates[0]["counterpart"].id, payment.id)

    def test_an_outbound_line_finds_a_vendor_payment(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._vendor_payment(amount=Decimal("500.00"))
            transaction = self._txn(amount="-500.00", description="NEFT BETA SUPPLIES")
            candidates = find_candidates(transaction=transaction, organization=self.org_a)
            self.assertEqual([c["counterpart"].id for c in candidates], [payment.id])

    def test_an_outbound_line_finds_a_posted_expense(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._posted_expense(amount=Decimal("250.00"))
            transaction = self._txn(amount="-250.00")
            candidates = find_candidates(transaction=transaction, organization=self.org_a)
            self.assertIn(expense.id, [c["counterpart"].id for c in candidates])

    def test_money_in_never_offers_a_payment_made(self):
        """Direction is a hard filter, not a scoring penalty."""
        with tenant_context(organization_id=self.org_a.id):
            self._vendor_payment(amount=Decimal("500.00"))
            transaction = self._txn(amount="500.00")
            candidates = find_candidates(transaction=transaction, organization=self.org_a)
            self.assertEqual(candidates, [])

    def test_a_payment_into_a_different_account_is_not_a_candidate(self):
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00", bank_account=self.savings)
            self.assertEqual(find_candidates(transaction=transaction, organization=self.org_a), [])

    def test_candidates_outside_the_window_are_not_offered(self):
        """Past a month an amount coincidence is likelier than a settlement
        delay, and offering it trains users to click through suggestions."""
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"), payment_date=DAY)
            far = self._txn(amount="1000.00", transaction_date=DAY + datetime.timedelta(days=45))
            self.assertEqual(find_candidates(transaction=far, organization=self.org_a), [])

    def test_a_reference_in_the_narration_raises_confidence(self):
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"), reference="INV-77")
            precise = self._txn(amount="1000.00", description="NEFT ACME LTD INV-77")
            vague = self._txn(amount="1000.00", description="NEFT CREDIT", bank_account=self.savings)

            strong = find_candidates(transaction=precise, organization=self.org_a)[0]
            self.assertEqual(strong["match_type"], MatchType.EXACT)
            self.assertGreater(strong["confidence"], Decimal("0.9"))
            self.assertEqual(find_candidates(transaction=vague, organization=self.org_a), [])

    def test_a_document_already_matched_is_not_offered_again(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            first = self._txn(amount="1000.00", description="ONE")
            create_match(
                transaction_id=first.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
            )
            second = self._txn(amount="1000.00", description="TWO")
            self.assertEqual(find_candidates(transaction=second, organization=self.org_a), [])

    def test_a_manually_posted_journal_is_a_candidate_but_a_document_journal_is_not(self):
        """The document is the better candidate and carries more context;
        offering its journal too would let one event be matched twice."""
        from accounting.services.journals import create_draft_journal
        from accounting.services.posting import post_journal

        with tenant_context(organization_id=self.org_a.id):
            # A document-raised journal (from a customer payment).
            self._customer_payment(amount=Decimal("1000.00"))
            # A journal posted directly to the bank account.
            draft = create_draft_journal(
                organization=self.org_a, posting_date=DAY, currency=self.currency,
                lines=[
                    {"account_id": self.gl_charges.id, "debit": Decimal("118.00")},
                    {"account_id": self.gl_bank.id, "credit": Decimal("118.00")},
                ],
                memo="Quarterly bank charges",
            )
            journal = post_journal(journal_id=draft.id, organization=self.org_a)

            charge_line = self._txn(amount="-118.00", description="BANK CHARGES")
            candidates = find_candidates(transaction=charge_line, organization=self.org_a)
            self.assertEqual([c["counterpart"].id for c in candidates], [journal.id])

            deposit = self._txn(amount="1000.00", description="NEFT IN")
            inbound = find_candidates(transaction=deposit, organization=self.org_a)
            self.assertEqual([c["counterpart_field"] for c in inbound], ["customer_payment"])


class SuggestionTests(BankingTestsBase):
    def test_suggesting_writes_unconfirmed_rows_and_confirms_nothing(self):
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00")

            matches = suggest_matches(transaction_id=transaction.id, organization=self.org_a)
            self.assertEqual(len(matches), 1)
            self.assertFalse(matches[0].is_confirmed)
            self.assertEqual(matches[0].suggestion_source, SuggestionSource.SYSTEM)

            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.SUGGESTED)

    def test_re_suggesting_replaces_stale_suggestions(self):
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00")
            suggest_matches(transaction_id=transaction.id, organization=self.org_a)
            suggest_matches(transaction_id=transaction.id, organization=self.org_a)
            self.assertEqual(BankTransactionMatch.objects.filter(transaction=transaction).count(), 1)

    def test_scoring_is_deterministic(self):
        """Run twice on the same data, get the same numbers — the property
        that makes this a deterministic matcher rather than a heuristic."""
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"), reference="REF-1")
            transaction = self._txn(amount="1000.00", description="NEFT REF-1")
            first = [c["confidence"] for c in find_candidates(transaction=transaction, organization=self.org_a)]
            second = [c["confidence"] for c in find_candidates(transaction=transaction, organization=self.org_a)]
            self.assertEqual(first, second)


class AutoMatchTests(BankingTestsBase):
    def test_one_strong_unambiguous_candidate_is_confirmed(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00", description="NEFT ACME LTD")

            match = auto_match(
                transaction_id=transaction.id, organization=self.org_a, actor=self.user_a
            )
            self.assertIsNotNone(match)
            self.assertTrue(match.is_confirmed)
            self.assertEqual(match.customer_payment_id, payment.id)

            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.MATCHED)

    def test_two_equally_strong_candidates_are_never_auto_confirmed(self):
        """Two customers each paying 25,000 on the same day is the normal
        case, not an edge case. Breaking the tie by id order would attribute
        a payment to the wrong customer and send the wrong dunning letter."""
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("25000.00"))
            self._customer_payment(amount=Decimal("25000.00"))
            transaction = self._txn(amount="25000.00", description="NEFT ACME LTD")

            self.assertEqual(
                len(find_candidates(transaction=transaction, organization=self.org_a)), 2
            )
            self.assertIsNone(
                auto_match(transaction_id=transaction.id, organization=self.org_a, actor=self.user_a)
            )
            transaction.refresh_from_db()
            self.assertNotEqual(transaction.status, BankTransactionStatus.MATCHED)

    def test_a_weak_candidate_is_not_auto_confirmed(self):
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"), payment_date=DAY)
            distant = self._txn(
                amount="1000.00", transaction_date=DAY + datetime.timedelta(days=20),
                description="UNRELATED NARRATION",
            )
            self.assertIsNone(
                auto_match(transaction_id=distant.id, organization=self.org_a, actor=self.user_a)
            )


class MatchingPostsNothingTests(BankingTestsBase):
    def test_matching_an_existing_payment_raises_no_journal(self):
        """The payment posted its own journal when it was recorded. A second
        one here would double the revenue."""
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00")
            before = JournalEntry.objects.count()

            create_match(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
            )
            self.assertEqual(JournalEntry.objects.count(), before)

    def test_unmatching_leaves_the_counterpart_untouched(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00")
            match = create_match(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
            )
            journal_id = payment.accounting_journal_id

            unmatch(match_id=match.id, organization=self.org_a, actor=self.user_a)
            payment.refresh_from_db()
            self.assertEqual(payment.accounting_journal_id, journal_id)
            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.UNMATCHED)


class OverMatchingTests(BankingTestsBase):
    def test_matches_cannot_explain_more_than_the_line(self):
        with tenant_context(organization_id=self.org_a.id):
            first = self._customer_payment(amount=Decimal("600.00"))
            second = self._customer_payment(amount=Decimal("600.00"))
            transaction = self._txn(amount="1000.00")

            create_match(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=first,
                amount=Decimal("600.00"), actor=self.user_a,
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_match(
                    transaction_id=transaction.id, organization=self.org_a,
                    counterpart_field="customer_payment", counterpart=second,
                    amount=Decimal("600.00"), actor=self.user_a,
                )
            self.assertEqual(ctx.exception.get_codes(), "over_matched_transaction")

    def test_one_payment_cannot_explain_two_deposits(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            first = self._txn(amount="1000.00", description="ONE")
            second = self._txn(amount="1000.00", description="TWO")

            create_match(
                transaction_id=first.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_match(
                    transaction_id=second.id, organization=self.org_a,
                    counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
                )
            self.assertEqual(ctx.exception.get_codes(), "over_matched_counterpart")

    def test_a_split_deposit_matches_two_payments(self):
        with tenant_context(organization_id=self.org_a.id):
            first = self._customer_payment(amount=Decimal("400.00"))
            second = self._customer_payment(amount=Decimal("600.00"))
            transaction = self._txn(amount="1000.00", description="BATCH CREDIT")

            create_match(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=first,
                amount=Decimal("400.00"), actor=self.user_a,
            )
            transaction.refresh_from_db()
            self.assertEqual(
                transaction.status, BankTransactionStatus.SUGGESTED,
                "a partly explained line still needs a decision",
            )

            create_match(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=second,
                amount=Decimal("600.00"), actor=self.user_a,
            )
            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.MATCHED)

    def test_a_counterpart_from_another_account_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            on_savings = self._txn(amount="1000.00", bank_account=self.savings)
            with self.assertRaises(ApplicationError) as ctx:
                create_match(
                    transaction_id=on_savings.id, organization=self.org_a,
                    counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
                )
            self.assertEqual(ctx.exception.get_codes(), "match_account_mismatch")


class CategorizationTests(BankingTestsBase):
    def test_categorizing_posts_a_balanced_journal(self):
        """The one path in this module that posts, because the statement line
        is the first place the fact becomes known."""
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-118.00", description="BANK CHARGES")
            match = categorize_transaction(
                transaction_id=transaction.id, organization=self.org_a,
                account=self.gl_charges, description="Quarterly charges", actor=self.user_a,
            )
            self.assertEqual(match.match_type, MatchType.CATEGORIZATION)
            self.assertTrue(match.is_confirmed)

            journal = match.journal_entry
            self.assertEqual(journal.status, JournalStatus.POSTED)
            debits = sum((line.debit for line in journal.lines.all()), Decimal("0"))
            credits = sum((line.credit for line in journal.lines.all()), Decimal("0"))
            self.assertEqual(debits, credits)
            self.assertEqual(debits, Decimal("118.00"))

            charge_line = journal.lines.get(account=self.gl_charges)
            self.assertEqual(charge_line.debit, Decimal("118.00"))
            bank_line = journal.lines.get(account=self.gl_bank)
            self.assertEqual(bank_line.credit, Decimal("118.00"))

            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.MATCHED)

    def test_categorizing_money_in_reverses_the_journal_sides(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="2500.00", description="INTEREST CREDITED")
            match = categorize_transaction(
                transaction_id=transaction.id, organization=self.org_a,
                account=self.gl_revenue, actor=self.user_a,
            )
            self.assertEqual(match.journal_entry.lines.get(account=self.gl_bank).debit, Decimal("2500.00"))
            self.assertEqual(
                match.journal_entry.lines.get(account=self.gl_revenue).credit, Decimal("2500.00")
            )

    def test_a_credit_card_charge_needs_no_special_case(self):
        """The sign convention's whole justification: positive debits the GL
        account, negative credits it, and that is correct for a liability
        account too."""
        with tenant_context(organization_id=self.org_a.id):
            charge = self._txn(amount="-4500.00", bank_account=self.card, description="AWS")
            match = categorize_transaction(
                transaction_id=charge.id, organization=self.org_a, account=self.gl_office,
                actor=self.user_a,
            )
            journal = match.journal_entry
            # Expense up, card liability up.
            self.assertEqual(journal.lines.get(account=self.gl_office).debit, Decimal("4500.00"))
            self.assertEqual(journal.lines.get(account=self.gl_card).credit, Decimal("4500.00"))

            repayment = self._txn(amount="4500.00", bank_account=self.card, description="PAYMENT RECEIVED")
            repay_match = categorize_transaction(
                transaction_id=repayment.id, organization=self.org_a, account=self.gl_bank,
                actor=self.user_a,
            )
            # Card liability down, bank down.
            self.assertEqual(
                repay_match.journal_entry.lines.get(account=self.gl_card).debit, Decimal("4500.00")
            )

    def test_a_transaction_cannot_be_categorized_to_its_own_bank_account(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-118.00")
            with self.assertRaises(ApplicationError) as ctx:
                categorize_transaction(
                    transaction_id=transaction.id, organization=self.org_a, account=self.gl_bank
                )
            self.assertEqual(ctx.exception.get_codes(), "category_account_invalid")

    def test_uncategorizing_reverses_rather_than_deletes(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-118.00")
            match = categorize_transaction(
                transaction_id=transaction.id, organization=self.org_a,
                account=self.gl_charges, actor=self.user_a,
            )
            journal_id = match.journal_entry_id

            uncategorize_transaction(match_id=match.id, organization=self.org_a, actor=self.user_a)

            original = JournalEntry.objects.get(pk=journal_id)
            self.assertEqual(original.status, JournalStatus.REVERSED)
            self.assertTrue(JournalEntry.objects.filter(reverses_id=journal_id).exists())
            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.UNMATCHED)

    def test_plain_unmatch_refuses_a_categorization(self):
        """Dropping the match without reversing would leave the journal
        posted and the line looking unexplained."""
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-118.00")
            match = categorize_transaction(
                transaction_id=transaction.id, organization=self.org_a, account=self.gl_charges
            )
            with self.assertRaises(ApplicationError) as ctx:
                unmatch(match_id=match.id, organization=self.org_a)
            self.assertEqual(ctx.exception.get_codes(), "categorization_needs_reversal")


class AiBoundaryTests(BankingTestsBase):
    """Root CLAUDE.md: AI may suggest; deterministic logic performs the
    authoritative act. Reconciliation status IS an authoritative fact."""

    def test_an_ai_suggestion_lands_unconfirmed(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00")

            match = record_ai_suggestion(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment,
                reason="Narration resembles this customer", confidence=Decimal("0.99"),
            )
            self.assertFalse(match.is_confirmed)
            self.assertEqual(match.suggestion_source, SuggestionSource.AI)
            transaction.refresh_from_db()
            self.assertNotEqual(transaction.status, BankTransactionStatus.MATCHED)

    def test_an_ai_suggestion_cannot_be_confirmed_without_a_person(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00")
            match = record_ai_suggestion(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment,
            )
            with self.assertRaises(ApplicationError) as ctx:
                confirm_match(match_id=match.id, organization=self.org_a, actor=None)
            self.assertEqual(ctx.exception.get_codes(), "ai_match_requires_human")

            confirmed = confirm_match(match_id=match.id, organization=self.org_a, actor=self.user_a)
            self.assertTrue(confirmed.is_confirmed)
            self.assertEqual(confirmed.confirmed_by_id, self.user_a.id)

    def test_auto_match_never_picks_up_an_ai_suggestion(self):
        """auto_match re-derives its own candidates deterministically; a
        stored AI row is not an input to it."""
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"), payment_date=DAY)
            transaction = self._txn(
                amount="1000.00", transaction_date=DAY + datetime.timedelta(days=25),
                description="AMBIGUOUS",
            )
            record_ai_suggestion(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, confidence=Decimal("1.0"),
            )
            self.assertIsNone(
                auto_match(transaction_id=transaction.id, organization=self.org_a, actor=self.user_a)
            )
