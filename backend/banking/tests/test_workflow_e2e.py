"""The Phase 6 acceptance gate, end to end:

    Statement import -> Rules -> Suggestions -> Matching -> Transfers
                     -> Categorization -> Reconciliation -> Sign-off

Asserted at every hop: importing posts nothing, matching an existing document
posts nothing, only categorization and transfers post, the ledger balances
throughout, and the period cannot be closed until every line is accounted for.
"""

from decimal import Decimal

from accounting.models.journal import JournalEntry
from accounting.selectors import get_trial_balance
from banking.models.reconciliation import ReconciliationStatus
from banking.models.statement import BankTransactionStatus
from banking.selectors import get_bank_account_summary, get_reconciliation_summary
from banking.services.matching import auto_match, categorize_transaction, suggest_matches
from banking.services.reconciliation import complete_reconciliation, start_reconciliation
from banking.services.rules import apply_rules_to_statement_import, create_rule
from banking.services.transactions import exclude_transaction
from banking.services.transfers import confirm_detected_transfer
from banking.tests.base import PERIOD_END, PERIOD_START, BankingTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context

# One month on the current account. Every line has a different resolution:
#   1000.00  a customer payment already in the books   -> matched
#   -4500.00 cloud hosting, no document                -> rule categorizes
#   -118.00  bank charges, no document                 -> categorized by hand
#   -5000.00 moved to savings                          -> transfer pair
#   -1.00    a header row the parser read as data      -> excluded
CURRENT_ACCOUNT_CSV = """Date,Narration,Ref,Amount
2026-04-10,NEFT ACME LTD PAYMENT,REF001,1000.00
2026-04-12,AWS EMEA SARL,REF002,-4500.00
2026-04-15,BANK CHARGES QTR,REF003,-118.00
2026-04-20,TRANSFER TO SAVINGS,REF004,-5000.00
2026-04-30,BALANCE BROUGHT FORWARD,,-1.00
"""

SAVINGS_CSV = """Date,Narration,Ref,Amount
2026-04-20,TRANSFER FROM CURRENT,REF004,5000.00
"""


class BankingWorkflowE2ETests(BankingTestsBase):
    def test_full_statement_to_signed_off_reconciliation(self):
        with tenant_context(organization_id=self.org_a.id):
            # 0. A payment already recorded in the books, before any statement
            #    arrives. It posted its own journal when it was recorded.
            payment = self._customer_payment(amount=Decimal("1000.00"), reference="REF001")
            journals_after_payment = JournalEntry.objects.count()

            create_rule(
                organization=self.org_a, name="Cloud hosting", description_contains="aws",
                target_account=self.gl_office, auto_confirm=True, priority=10, actor=self.user_a,
            )

            # 1. Import. Statement lines are evidence, not accounting.
            statement = self._import_csv(CURRENT_ACCOUNT_CSV)
            self.assertEqual(statement.rows_imported, 5)
            self.assertEqual(
                JournalEntry.objects.count(), journals_after_payment,
                "importing a statement must post nothing",
            )
            self._import_csv(SAVINGS_CSV, bank_account=self.savings, file_name="savings.csv")

            # 2. Rules run. Only the auto-confirming AWS rule fires.
            counts = apply_rules_to_statement_import(
                statement_import=statement, organization=self.org_a, actor=self.user_a
            )
            self.assertEqual(counts["categorized"], 1)
            self.assertEqual(counts["no_rule_matched"], 4)
            self.assertEqual(JournalEntry.objects.count(), journals_after_payment + 1)

            lines = {t.bank_reference: t for t in statement.transactions.all()}

            # 3. The deposit matches the payment already in the books —
            #    deterministically, and without posting anything.
            suggestions = suggest_matches(
                transaction_id=lines["REF001"].id, organization=self.org_a
            )
            self.assertEqual(len(suggestions), 1)
            self.assertEqual(suggestions[0].customer_payment_id, payment.id)

            before_match = JournalEntry.objects.count()
            matched = auto_match(
                transaction_id=lines["REF001"].id, organization=self.org_a, actor=self.user_a
            )
            self.assertIsNotNone(matched)
            self.assertEqual(
                JournalEntry.objects.count(), before_match,
                "matching a document that already posted must not post again",
            )

            # 4. Bank charges have no document, so categorizing them is the
            #    first time the fact enters the books.
            categorize_transaction(
                transaction_id=lines["REF003"].id, organization=self.org_a,
                account=self.gl_charges, description="Quarterly charges", actor=self.user_a,
            )

            # 5. The transfer pair: two statement lines, ONE journal.
            savings_line = self.savings.transactions.get(bank_reference="REF004")
            before_transfer = JournalEntry.objects.count()
            transfer = confirm_detected_transfer(
                organization=self.org_a,
                outflow_transaction_id=lines["REF004"].id,
                inflow_transaction_id=savings_line.id,
                actor=self.user_a,
            )
            self.assertEqual(
                JournalEntry.objects.count(), before_transfer + 1,
                "two views of one movement must raise one journal, not two",
            )
            self.assertEqual(transfer.amount, Decimal("5000.00"))

            # 6. The parser artefact is excluded, with a reason on the record.
            #    Excluding is only ever right for a line that is not a real
            #    transaction — everything else above was matched or posted.
            artefact = statement.transactions.get(description="BALANCE BROUGHT FORWARD")
            exclude_transaction(
                transaction_id=artefact.id,
                organization=self.org_a,
                reason="Balance row the parser read as a transaction",
                actor=self.user_a,
            )

            # 7. Everything is now accounted for.
            summary = get_bank_account_summary(bank_account=self.bank, as_of=PERIOD_END)
            self.assertEqual(summary["open_transaction_count"], 0)
            # 1000 - 4500 - 118 - 5000, with the excluded row left out.
            self.assertEqual(summary["statement_balance"], Decimal("-8618.00"))
            self.assertEqual(summary["cleared_balance"], Decimal("-8618.00"))
            self.assertEqual(summary["book_balance"], Decimal("-8618.00"))
            self.assertEqual(summary["uncleared_book_amount"], Decimal("0.00"))

            # 8. Reconcile and sign off.
            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("-8618.00"), actor=self.user_a,
            )
            self.assertTrue(get_reconciliation_summary(reconciliation=reconciliation)["can_complete"])

            completed = complete_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a, actor=self.user_a
            )
            self.assertEqual(completed.status, ReconciliationStatus.COMPLETED)
            self.assertEqual(completed.cleared_balance, Decimal("-8618.00"))

            # 9. The ledger balances, and the period's lines are locked.
            self.assertTrue(
                get_trial_balance(organization=self.org_a, as_of_date=PERIOD_END)["is_balanced"]
            )
            for transaction in statement.transactions.all():
                self.assertEqual(transaction.reconciliation_id, reconciliation.id)

    def test_a_period_with_one_unexplained_line_cannot_be_signed_off(self):
        """The control that gives the whole exercise its value."""
        with tenant_context(organization_id=self.org_a.id):
            self._import_csv(CURRENT_ACCOUNT_CSV)
            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.bank,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("-8619.00"), actor=self.user_a,
            )
            summary = get_reconciliation_summary(reconciliation=reconciliation)
            self.assertEqual(summary["open_transaction_count"], 5)
            self.assertFalse(summary["can_complete"])

            with self.assertRaises(ApplicationError) as ctx:
                complete_reconciliation(
                    reconciliation_id=reconciliation.id, organization=self.org_a, actor=self.user_a
                )
            self.assertEqual(ctx.exception.get_codes(), "reconciliation_has_open_transactions")

    def test_a_credit_card_reconciles_on_the_same_arithmetic(self):
        """No special case anywhere: the card's closing balance is simply
        negative, because money owed is negative on the statement
        convention."""
        card_csv = (
            "Date,Narration,Ref,Amount\n"
            "2026-04-05,AWS EMEA SARL,C1,-4500.00\n"
            "2026-04-18,OFFICE SUPPLIES,C2,-1500.00\n"
            "2026-04-25,PAYMENT RECEIVED THANK YOU,C3,2000.00\n"
        )
        with tenant_context(organization_id=self.org_a.id):
            statement = self._import_csv(card_csv, bank_account=self.card, file_name="card.csv")
            for transaction in statement.transactions.all():
                account = self.gl_bank if transaction.is_inflow else self.gl_office
                categorize_transaction(
                    transaction_id=transaction.id, organization=self.org_a,
                    account=account, actor=self.user_a,
                )

            summary = get_bank_account_summary(bank_account=self.card, as_of=PERIOD_END)
            self.assertEqual(summary["statement_balance"], Decimal("-4000.00"))
            self.assertEqual(summary["book_balance"], Decimal("-4000.00"))

            reconciliation = start_reconciliation(
                organization=self.org_a, bank_account=self.card,
                statement_start_date=PERIOD_START, statement_end_date=PERIOD_END,
                statement_closing_balance=Decimal("-4000.00"), actor=self.user_a,
            )
            completed = complete_reconciliation(
                reconciliation_id=reconciliation.id, organization=self.org_a, actor=self.user_a
            )
            self.assertEqual(completed.status, ReconciliationStatus.COMPLETED)
            self.assertTrue(
                get_trial_balance(organization=self.org_a, as_of_date=PERIOD_END)["is_balanced"]
            )

    def test_the_statuses_the_product_exposes_all_occur_in_one_period(self):
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("1000.00"))
            statement = self._import_csv(CURRENT_ACCOUNT_CSV)
            lines = {t.bank_reference: t for t in statement.transactions.all()}

            suggest_matches(transaction_id=lines["REF001"].id, organization=self.org_a)
            categorize_transaction(
                transaction_id=lines["REF003"].id, organization=self.org_a,
                account=self.gl_charges, actor=self.user_a,
            )
            exclude_transaction(
                transaction_id=statement.transactions.get(
                    description="BALANCE BROUGHT FORWARD"
                ).id,
                organization=self.org_a, reason="Parser artefact", actor=self.user_a,
            )

            statuses = {
                t.bank_reference or "artefact": t.status for t in statement.transactions.all()
            }
            self.assertEqual(statuses["REF001"], BankTransactionStatus.SUGGESTED)
            self.assertEqual(statuses["REF002"], BankTransactionStatus.UNMATCHED)
            self.assertEqual(statuses["REF003"], BankTransactionStatus.MATCHED)
            self.assertEqual(statuses["artefact"], BankTransactionStatus.EXCLUDED)
