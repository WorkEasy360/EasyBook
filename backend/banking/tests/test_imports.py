"""Statement parsing and the duplicate-import problem.

The multiset tests below are the heart of this file. They encode the one
decision that separates a reconciliation feature that works from one that
quietly loses transactions: two identical rows can be a duplicate OR a real
pair, and which one it is depends on how many the database already holds.
"""

import datetime
from decimal import Decimal

from banking.models.statement import BankTransaction, StatementImportStatus
from banking.services.imports import compute_fingerprint, normalize_narration
from banking.services.parsers import (
    AmountMode,
    CsvColumnMapping,
    parse_csv_statement,
    suggest_column_mapping,
)
from banking.tests.base import DEBIT_CREDIT_MAPPING, SIGNED_MAPPING, BankingTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context

SIGNED_CSV = """Date,Narration,Ref,Amount
2026-04-10,NEFT ACME LTD,REF001,1000.00
2026-04-11,UPI SWIGGY ORDER,REF002,-450.50
2026-04-12,BANK CHARGES,REF003,-118.00
"""

DEBIT_CREDIT_CSV = """Txn Date,Narration,Chq No,Withdrawal Amt.,Deposit Amt.
10/04/2026,NEFT ACME LTD,REF001,,"1,000.00"
11/04/2026,UPI SWIGGY,REF002,450.50,
12/04/2026,OPENING BALANCE,,,
"""


class CsvParsingTests(BankingTestsBase):
    def test_signed_amount_column(self):
        rows = parse_csv_statement(content=SIGNED_CSV, mapping=SIGNED_MAPPING)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].amount, Decimal("1000.00"))
        self.assertEqual(rows[1].amount, Decimal("-450.50"))
        self.assertEqual(rows[0].transaction_date, datetime.date(2026, 4, 10))
        self.assertEqual(rows[0].description, "NEFT ACME LTD")

    def test_separate_debit_and_credit_columns_with_thousands_separators(self):
        rows = parse_csv_statement(content=DEBIT_CREDIT_CSV, mapping=DEBIT_CREDIT_MAPPING)
        # The blank third row is dropped: a zero-amount statement line carries
        # no financial information and would match any zero counterpart.
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].amount, Decimal("1000.00"))
        self.assertEqual(rows[1].amount, Decimal("-450.50"))

    def test_a_row_with_both_debit_and_credit_is_refused_not_guessed(self):
        content = (
            "Txn Date,Narration,Chq No,Withdrawal Amt.,Deposit Amt.\n"
            "10/04/2026,ODD,R1,100.00,200.00\n"
        )
        with self.assertRaises(ApplicationError) as ctx:
            parse_csv_statement(content=content, mapping=DEBIT_CREDIT_MAPPING)
        self.assertEqual(ctx.exception.get_codes(), "statement_row_ambiguous_amount")

    def test_indicator_mode(self):
        content = "Date,Particulars,Amount,Type\n10-04-2026,SALARY,25000.00,CR\n11-04-2026,RENT,8000.00,DR\n"
        mapping = CsvColumnMapping(
            date_column="Date", amount_mode=AmountMode.INDICATOR,
            amount_column="Amount", indicator_column="Type", description_column="Particulars",
        )
        rows = parse_csv_statement(content=content, mapping=mapping)
        self.assertEqual(rows[0].amount, Decimal("25000.00"))
        self.assertEqual(rows[1].amount, Decimal("-8000.00"))

    def test_parenthesised_negatives(self):
        content = "Date,Narration,Ref,Amount\n2026-04-10,FEE,R1,(250.00)\n"
        rows = parse_csv_statement(content=content, mapping=SIGNED_MAPPING)
        self.assertEqual(rows[0].amount, Decimal("-250.00"))

    def test_an_unreadable_amount_names_the_row(self):
        content = "Date,Narration,Ref,Amount\n2026-04-10,GOOD,R1,100.00\n2026-04-11,BAD,R2,not-a-number\n"
        with self.assertRaises(ApplicationError) as ctx:
            parse_csv_statement(content=content, mapping=SIGNED_MAPPING)
        self.assertIn("Row 3", str(ctx.exception.detail))

    def test_a_mapping_naming_a_missing_column_is_refused_up_front(self):
        mapping = CsvColumnMapping(date_column="Date", amount_column="Missing")
        with self.assertRaises(ApplicationError) as ctx:
            parse_csv_statement(content=SIGNED_CSV, mapping=mapping)
        self.assertEqual(ctx.exception.get_codes(), "statement_column_missing")

    def test_column_suggestion_is_advisory_and_separate(self):
        suggestion = suggest_column_mapping(
            header_row=["Txn Date", "Narration", "Withdrawal Amt.", "Deposit Amt."]
        )
        self.assertEqual(suggestion["amount_mode"], AmountMode.DEBIT_CREDIT)
        self.assertEqual(suggestion["date_column"], "Txn Date")
        self.assertEqual(suggestion["description_column"], "Narration")


class FingerprintTests(BankingTestsBase):
    def test_narration_whitespace_and_case_do_not_change_identity(self):
        a = compute_fingerprint(
            transaction_date=datetime.date(2026, 4, 10), amount=Decimal("100.00"),
            description="NEFT  ACME   LTD",
        )
        b = compute_fingerprint(
            transaction_date=datetime.date(2026, 4, 10), amount=Decimal("100.00"),
            description="neft acme ltd",
        )
        self.assertEqual(a, b)
        self.assertEqual(normalize_narration(" a  b "), "A B")

    def test_a_bank_supplied_id_overrides_the_derived_fingerprint(self):
        """An institution-issued id is the stronger guard: a bank that
        reissues a statement with a tidied narration must not look like a
        different transaction."""
        a = compute_fingerprint(
            transaction_date=datetime.date(2026, 4, 10), amount=Decimal("100.00"),
            description="ONE", external_id="FITID-9",
        )
        b = compute_fingerprint(
            transaction_date=datetime.date(2026, 4, 11), amount=Decimal("999.00"),
            description="COMPLETELY DIFFERENT", external_id="FITID-9",
        )
        self.assertEqual(a, b)


class StatementImportTests(BankingTestsBase):
    def test_a_successful_import_creates_transactions_and_posts_nothing(self):
        """Importing a statement is the bank telling us what happened. It is
        not our accounting record of it — no journal is raised here."""
        from accounting.models.journal import JournalEntry

        with tenant_context(organization_id=self.org_a.id):
            before = JournalEntry.objects.count()
            statement = self._import_csv(SIGNED_CSV)

            self.assertEqual(statement.status, StatementImportStatus.COMPLETED)
            self.assertEqual(statement.rows_read, 3)
            self.assertEqual(statement.rows_imported, 3)
            self.assertEqual(statement.rows_skipped_duplicate, 0)
            self.assertEqual(statement.statement_start_date, datetime.date(2026, 4, 10))
            self.assertEqual(statement.statement_end_date, datetime.date(2026, 4, 12))
            self.assertEqual(BankTransaction.objects.count(), 3)
            self.assertEqual(JournalEntry.objects.count(), before)

    def test_reimporting_the_identical_file_is_refused_by_the_hash(self):
        with tenant_context(organization_id=self.org_a.id):
            self._import_csv(SIGNED_CSV)
            with self.assertRaises(ApplicationError) as ctx:
                self._import_csv(SIGNED_CSV)
            self.assertEqual(ctx.exception.get_codes(), "statement_already_imported")

    def test_an_overlapping_export_imports_only_the_new_rows(self):
        """The realistic duplicate: 1-31 March downloaded on the 31st, then
        15 March-15 April a fortnight later. Different bytes, same rows."""
        second = (
            "Date,Narration,Ref,Amount\n"
            "2026-04-11,UPI SWIGGY ORDER,REF002,-450.50\n"
            "2026-04-12,BANK CHARGES,REF003,-118.00\n"
            "2026-04-13,NEFT REFUND,REF004,75.00\n"
        )
        with tenant_context(organization_id=self.org_a.id):
            self._import_csv(SIGNED_CSV, file_name="first.csv")
            statement = self._import_csv(second, file_name="second.csv")

            self.assertEqual(statement.rows_read, 3)
            self.assertEqual(statement.rows_skipped_duplicate, 2)
            self.assertEqual(statement.rows_imported, 1)
            self.assertEqual(BankTransaction.objects.count(), 4)

    def test_two_genuinely_identical_withdrawals_both_import(self):
        """The case a naive unique constraint gets wrong, and gets wrong in
        the direction that loses money."""
        content = (
            "Date,Narration,Ref,Amount\n"
            "2026-04-10,ATM WITHDRAWAL,,-500.00\n"
            "2026-04-10,ATM WITHDRAWAL,,-500.00\n"
        )
        with tenant_context(organization_id=self.org_a.id):
            statement = self._import_csv(content)
            self.assertEqual(statement.rows_imported, 2)
            self.assertEqual(statement.rows_skipped_duplicate, 0)
            ordinals = sorted(
                BankTransaction.objects.values_list("duplicate_ordinal", flat=True)
            )
            self.assertEqual(ordinals, [0, 1])

    def test_reimporting_a_file_with_a_genuine_pair_adds_nothing(self):
        content = (
            "Date,Narration,Ref,Amount\n"
            "2026-04-10,ATM WITHDRAWAL,,-500.00\n"
            "2026-04-10,ATM WITHDRAWAL,,-500.00\n"
        )
        overlapping = content + "2026-04-11,NEFT IN,,1000.00\n"
        with tenant_context(organization_id=self.org_a.id):
            self._import_csv(content, file_name="a.csv")
            statement = self._import_csv(overlapping, file_name="b.csv")
            self.assertEqual(statement.rows_skipped_duplicate, 2)
            self.assertEqual(statement.rows_imported, 1)
            self.assertEqual(BankTransaction.objects.count(), 3)

    def test_a_bank_that_splits_a_pair_across_two_exports_ends_up_with_both(self):
        first = "Date,Narration,Ref,Amount\n2026-04-10,ATM WITHDRAWAL,,-500.00\n"
        second = (
            "Date,Narration,Ref,Amount\n"
            "2026-04-10,ATM WITHDRAWAL,,-500.00\n"
            "2026-04-10,ATM WITHDRAWAL,,-500.00\n"
        )
        with tenant_context(organization_id=self.org_a.id):
            self._import_csv(first, file_name="a.csv")
            statement = self._import_csv(second, file_name="b.csv")
            self.assertEqual(statement.rows_imported, 1)
            self.assertEqual(statement.rows_skipped_duplicate, 1)
            self.assertEqual(BankTransaction.objects.count(), 2)

    def test_the_same_file_can_be_imported_into_two_different_accounts(self):
        """The file-hash guard is scoped to the bank account, not the org."""
        with tenant_context(organization_id=self.org_a.id):
            self._import_csv(SIGNED_CSV, bank_account=self.bank)
            statement = self._import_csv(SIGNED_CSV, bank_account=self.savings)
            self.assertEqual(statement.rows_imported, 3)

    def test_an_inactive_account_refuses_imports(self):
        from banking.services.bank_accounts import update_bank_account

        with tenant_context(organization_id=self.org_a.id):
            update_bank_account(bank_account=self.bank, is_active=False)
            with self.assertRaises(ApplicationError) as ctx:
                self._import_csv(SIGNED_CSV)
            self.assertEqual(ctx.exception.get_codes(), "bank_account_inactive")


class BankTransactionImmutabilityTests(BankingTestsBase):
    def test_bank_reported_fields_cannot_be_edited(self):
        """The bank's assertion about what happened is the only independent
        record we hold against our own books."""
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-100.00", description="ORIGINAL")
            transaction.amount = Decimal("-200.00")
            with self.assertRaises(ValueError) as ctx:
                transaction.save()
            self.assertIn("immutable", str(ctx.exception))

    def test_interpretation_fields_can_change(self):
        from banking.models.statement import BankTransactionStatus

        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-100.00")
            transaction.status = BankTransactionStatus.SUGGESTED
            transaction.save(update_fields=["status", "updated_at"])
            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.SUGGESTED)

    def test_deleting_evidence_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-100.00")
            with self.assertRaises(ValueError) as ctx:
                transaction.delete()
            self.assertIn("exclude", str(ctx.exception))

    def test_a_manual_transaction_gets_the_next_ordinal(self):
        """The deliberate escape hatch from the deduplicator: the bank really
        did charge the same amount on the same day again."""
        with tenant_context(organization_id=self.org_a.id):
            first = self._txn(amount="-500.00", description="ATM")
            second = self._txn(amount="-500.00", description="ATM")
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertEqual(first.duplicate_ordinal, 0)
            self.assertEqual(second.duplicate_ordinal, 1)

    def test_a_zero_amount_transaction_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._txn(amount="0")
            self.assertEqual(ctx.exception.get_codes(), "transaction_amount_invalid")
