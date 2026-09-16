"""Banking API: auth, the RBAC split, and that the full account number never
crosses the wire in either direction."""

from decimal import Decimal

from rest_framework.test import APIClient

from banking.models.bank_account import BankAccount
from banking.models.statement import BankTransactionStatus
from banking.services.matching import create_match
from banking.tests.base import DAY, PERIOD_END, PERIOD_START, BankingTestsBase
from core.tenancy import tenant_context

CSV = """Date,Narration,Ref,Amount
2026-04-10,NEFT ACME LTD,REF001,1000.00
2026-04-11,BANK CHARGES,REF002,-118.00
"""

MAPPING = {
    "date_column": "Date",
    "amount_mode": "signed",
    "amount_column": "Amount",
    "description_column": "Narration",
    "reference_column": "Ref",
}


class BankingApiTestsBase(BankingTestsBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user_a)
        self.accountant_client = APIClient()
        self.accountant_client.force_authenticate(user=self.accountant_user)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(user=self.staff_user)

    def _headers(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.org_a).id)}


class BankAccountApiTests(BankingApiTestsBase):
    def test_requires_authentication_and_an_organization(self):
        self.assertEqual(APIClient().get("/api/v1/bank-accounts/", **self._headers()).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/bank-accounts/").status_code, 400)

    def test_create_stores_only_the_last_four_digits_and_never_echoes_the_number(self):
        from accounting.models.account import AccountType
        from accounting.services.accounts import create_account

        with tenant_context(organization_id=self.org_a.id):
            account = create_account(
                organization=self.org_a, code="1006", name="New Bank", account_type=AccountType.ASSET
            )

        response = self.client.post(
            "/api/v1/bank-accounts/",
            {
                "name": "ICICI Current",
                "account_id": str(account.id),
                "account_number": "5555444433332222",
                "bank_name": "ICICI",
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["masked_number"], "••••2222")
        self.assertNotIn("account_number", response.data)
        self.assertNotIn("5555444433332222", str(response.data))

        with tenant_context(organization_id=self.org_a.id):
            created = BankAccount.objects.get(pk=response.data["id"])
            self.assertEqual(created.account_number_last4, "2222")

    def test_staff_may_read_but_not_create(self):
        self.assertEqual(
            self.staff_client.get("/api/v1/bank-accounts/", **self._headers()).status_code, 200
        )
        response = self.staff_client.post(
            "/api/v1/bank-accounts/", {"name": "X", "account_id": str(self.gl_ar.id)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_summary_shows_all_three_balances(self):
        with tenant_context(organization_id=self.org_a.id):
            self._txn(amount="-118.00")
        response = self.client.get(
            f"/api/v1/bank-accounts/{self.bank.id}/summary/", **self._headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["statement_balance"]), Decimal("-118.00"))
        self.assertEqual(Decimal(response.data["cleared_balance"]), Decimal("0.00"))
        self.assertEqual(response.data["open_transaction_count"], 1)


class StatementImportApiTests(BankingApiTestsBase):
    def _import(self, client=None, content=CSV, file_name="stmt.csv"):
        return (client or self.client).post(
            f"/api/v1/bank-accounts/{self.bank.id}/statement-imports/",
            {"content": content, "file_name": file_name, "mapping": MAPPING},
            format="json", **self._headers(),
        )

    def test_import_reports_what_it_did(self):
        response = self._import()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["rows_read"], 2)
        self.assertEqual(response.data["rows_imported"], 2)
        self.assertEqual(response.data["rows_skipped_duplicate"], 0)

    def test_reimporting_the_same_file_is_rejected_with_a_usable_message(self):
        self._import()
        response = self._import()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "statement_already_imported")

    def test_a_mapping_is_required(self):
        response = self.client.post(
            f"/api/v1/bank-accounts/{self.bank.id}/statement-imports/",
            {"content": CSV},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_staff_cannot_import_a_statement(self):
        """Importing decides what the books are reconciled against."""
        self.assertEqual(self._import(client=self.staff_client).status_code, 403)

    def test_an_accountant_can_import(self):
        self.assertEqual(self._import(client=self.accountant_client).status_code, 201)

    def test_transactions_are_listable_and_filterable(self):
        self._import()
        response = self.client.get(
            f"/api/v1/bank-transactions/?bank_account={self.bank.id}&status=unmatched",
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_staff_can_read_statement_lines(self):
        """Staff already hold VIEW_ACCOUNTING and can read the General
        Ledger, which contains every bank journal — hiding the statement
        would be theatre, not a control."""
        self._import()
        response = self.staff_client.get("/api/v1/bank-transactions/", **self._headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)


class MatchingApiTests(BankingApiTestsBase):
    def test_suggest_then_confirm(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00", description="NEFT ACME LTD")

        suggested = self.client.post(
            f"/api/v1/bank-transactions/{transaction.id}/suggestions/", **self._headers()
        )
        self.assertEqual(suggested.status_code, 200)
        self.assertEqual(len(suggested.data), 1)
        self.assertFalse(suggested.data[0]["is_confirmed"])
        self.assertEqual(str(suggested.data[0]["customer_payment"]), str(payment.id))

        confirmed = self.client.post(
            f"/api/v1/bank-matches/{suggested.data[0]['id']}/confirm/", **self._headers()
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.data["is_confirmed"])

        with tenant_context(organization_id=self.org_a.id):
            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.MATCHED)

    def test_auto_match_reports_when_it_declines(self):
        with tenant_context(organization_id=self.org_a.id):
            self._customer_payment(amount=Decimal("25000.00"))
            self._customer_payment(amount=Decimal("25000.00"))
            transaction = self._txn(amount="25000.00", description="NEFT ACME LTD")

        response = self.client.post(
            f"/api/v1/bank-transactions/{transaction.id}/auto-match/", **self._headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["matched"])
        self.assertIsNone(response.data["match"])

    def test_manual_match_by_counterpart_type(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._posted_expense(amount=Decimal("250.00"))
            transaction = self._txn(amount="-250.00")

        response = self.client.post(
            f"/api/v1/bank-transactions/{transaction.id}/matches/",
            {"counterpart_type": "expense", "counterpart_id": str(expense.id)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["counterpart_type"], "expense")

    def test_categorize_posts_and_uncategorize_reverses(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-118.00", description="BANK CHARGES")

        categorized = self.client.post(
            f"/api/v1/bank-transactions/{transaction.id}/categorize/",
            {"account_id": str(self.gl_charges.id), "description": "Quarterly charges"},
            format="json", **self._headers(),
        )
        self.assertEqual(categorized.status_code, 201)
        self.assertIsNotNone(categorized.data["journal_entry"])

        reversed_response = self.client.post(
            f"/api/v1/bank-matches/{categorized.data['id']}/uncategorize/", **self._headers()
        )
        self.assertEqual(reversed_response.status_code, 200)
        self.assertEqual(reversed_response.data["status"], BankTransactionStatus.UNMATCHED)

    def test_staff_cannot_reconcile(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-118.00")
        response = self.staff_client.post(
            f"/api/v1/bank-transactions/{transaction.id}/categorize/",
            {"account_id": str(self.gl_charges.id)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_exclude_requires_a_reason_through_the_api(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-1.00")
        response = self.client.post(
            f"/api/v1/bank-transactions/{transaction.id}/exclude/", {}, format="json", **self._headers()
        )
        self.assertEqual(response.status_code, 400)


class TransferApiTests(BankingApiTestsBase):
    def test_record_and_void_a_transfer(self):
        created = self.client.post(
            "/api/v1/bank-transfers/",
            {
                "from_bank_account_id": str(self.bank.id),
                "to_bank_account_id": str(self.savings.id),
                "transfer_date": str(DAY),
                "amount": "5000.00",
            },
            format="json", **self._headers(),
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["status"], "posted")

        voided = self.client.post(
            f"/api/v1/bank-transfers/{created.data['id']}/void/",
            {"reason": "Wrong account"}, format="json", **self._headers(),
        )
        self.assertEqual(voided.data["status"], "void")

    def test_staff_cannot_move_money(self):
        """Same segregation-of-duties line as RECORD_VENDOR_PAYMENT."""
        response = self.staff_client.post(
            "/api/v1/bank-transfers/",
            {
                "from_bank_account_id": str(self.bank.id),
                "to_bank_account_id": str(self.savings.id),
                "transfer_date": str(DAY),
                "amount": "5000.00",
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_transfer_candidates_then_confirm_the_pair(self):
        with tenant_context(organization_id=self.org_a.id):
            out = self._txn(amount="-5000.00", description="TO SAVINGS")
            into = self._txn(amount="5000.00", bank_account=self.savings, description="FROM CURRENT")

        candidates = self.client.get(
            f"/api/v1/bank-transactions/{out.id}/transfer-candidates/", **self._headers()
        )
        self.assertEqual(candidates.status_code, 200)
        self.assertEqual(len(candidates.data), 1)
        self.assertEqual(candidates.data[0]["bank_account_name"], "HDFC Savings")

        confirmed = self.client.post(
            "/api/v1/bank-transfers/confirm-pair/",
            {"outflow_transaction_id": str(out.id), "inflow_transaction_id": str(into.id)},
            format="json", **self._headers(),
        )
        self.assertEqual(confirmed.status_code, 201)


class RuleApiTests(BankingApiTestsBase):
    def test_create_and_apply_a_rule(self):
        created = self.client.post(
            "/api/v1/bank-rules/",
            {
                "name": "AWS", "description_contains": "aws",
                "target_account_id": str(self.gl_office.id), "auto_confirm": True,
            },
            format="json", **self._headers(),
        )
        self.assertEqual(created.status_code, 201)

        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-4500.00", description="AWS EMEA SARL")

        applied = self.client.post(
            f"/api/v1/bank-transactions/{transaction.id}/apply-rules/", **self._headers()
        )
        self.assertTrue(applied.data["applied"])
        self.assertEqual(applied.data["reason"], "categorized")

    def test_a_rule_with_no_conditions_is_rejected(self):
        response = self.client.post(
            "/api/v1/bank-rules/",
            {"name": "Everything", "target_account_id": str(self.gl_office.id)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "rule_has_no_conditions")

    def test_staff_cannot_manage_rules(self):
        response = self.staff_client.post(
            "/api/v1/bank-rules/",
            {"name": "X", "description_contains": "x", "target_account_id": str(self.gl_office.id)},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)


class ReconciliationApiTests(BankingApiTestsBase):
    def test_start_summary_and_complete(self):
        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))
            transaction = self._txn(amount="1000.00", description="NEFT ACME LTD")
            create_match(
                transaction_id=transaction.id, organization=self.org_a,
                counterpart_field="customer_payment", counterpart=payment, actor=self.user_a,
            )

        started = self.client.post(
            "/api/v1/bank-reconciliations/",
            {
                "bank_account_id": str(self.bank.id),
                "statement_start_date": str(PERIOD_START),
                "statement_end_date": str(PERIOD_END),
                "statement_closing_balance": "1000.00",
            },
            format="json", **self._headers(),
        )
        self.assertEqual(started.status_code, 201)
        self.assertEqual(started.data["status"], "in_progress")

        summary = self.client.get(
            f"/api/v1/bank-reconciliations/{started.data['id']}/summary/", **self._headers()
        )
        self.assertTrue(summary.data["can_complete"])
        self.assertEqual(Decimal(summary.data["difference"]), Decimal("0.00"))

        completed = self.client.post(
            f"/api/v1/bank-reconciliations/{started.data['id']}/complete/", **self._headers()
        )
        self.assertEqual(completed.data["status"], "completed")
        self.assertEqual(Decimal(completed.data["cleared_balance"]), Decimal("1000.00"))

    def test_completing_out_of_balance_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            self._txn(amount="-118.00", description="UNEXPLAINED")

        started = self.client.post(
            "/api/v1/bank-reconciliations/",
            {
                "bank_account_id": str(self.bank.id),
                "statement_start_date": str(PERIOD_START),
                "statement_end_date": str(PERIOD_END),
                "statement_closing_balance": "-118.00",
            },
            format="json", **self._headers(),
        )
        response = self.client.post(
            f"/api/v1/bank-reconciliations/{started.data['id']}/complete/", **self._headers()
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "reconciliation_has_open_transactions")

    def test_staff_cannot_complete_a_reconciliation(self):
        started = self.client.post(
            "/api/v1/bank-reconciliations/",
            {
                "bank_account_id": str(self.bank.id),
                "statement_start_date": str(PERIOD_START),
                "statement_end_date": str(PERIOD_END),
                "statement_closing_balance": "0.00",
            },
            format="json", **self._headers(),
        )
        response = self.staff_client.post(
            f"/api/v1/bank-reconciliations/{started.data['id']}/complete/", **self._headers()
        )
        self.assertEqual(response.status_code, 403)


class CrossTenantApiTests(BankingApiTestsBase):
    def test_cannot_reach_another_organizations_bank_account(self):
        other = APIClient()
        other.force_authenticate(user=self.user_b)
        response = other.get(
            f"/api/v1/bank-accounts/{self.bank.id}/", **self._headers(self.org_b)
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_list_another_organizations_statement(self):
        with tenant_context(organization_id=self.org_a.id):
            self._txn(amount="-100.00", description="CONFIDENTIAL")

        other = APIClient()
        other.force_authenticate(user=self.user_b)
        response = other.get("/api/v1/bank-transactions/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
