"""Database-level Row Level Security across every banking table.

Walks EVERY TenantScopedModel in the app and asserts the Postgres policy is
actually on its table, so a model added later without an entry in
`migrations/0002_enable_rls.py` fails here rather than shipping unprotected.
Same gate as purchases/tests/test_rls.py and projects/tests/test_rls.py.

This app matters more than most: a bank statement discloses every
counterparty an organization deals with and every amount it moved.
"""

from decimal import Decimal

from django.apps import apps
from django.db import connection
from django.test import TestCase

from banking.models.bank_account import BankAccount
from banking.models.statement import BankTransaction
from banking.tests.base import BankingTestsBase
from core.models import TenantScopedModel
from core.tenancy import clear_tenant_context, tenant_context


def _banking_tenant_models():
    return [
        model
        for model in apps.get_app_config("banking").get_models()
        if issubclass(model, TenantScopedModel)
    ]


class BankingRlsCoverageTests(TestCase):
    def test_every_banking_model_is_tenant_scoped(self):
        models = _banking_tenant_models()
        all_models = list(apps.get_app_config("banking").get_models())
        self.assertGreater(len(models), 0)
        self.assertEqual(
            sorted(m.__name__ for m in models),
            sorted(m.__name__ for m in all_models),
            "every banking model must inherit TenantScopedModel",
        )

    def test_every_banking_table_has_rls_enabled_and_forced(self):
        tables = [model._meta.db_table for model in _banking_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(%s)",
                [tables],
            )
            state = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

        self.assertEqual([t for t in tables if t not in state], [])
        unprotected = [table for table, (enabled, forced) in state.items() if not (enabled and forced)]
        self.assertEqual(
            unprotected, [],
            f"these banking tables lack ENABLE/FORCE ROW LEVEL SECURITY: {unprotected}",
        )

    def test_every_banking_table_has_a_policy(self):
        tables = [model._meta.db_table for model in _banking_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(sorted(set(tables) - with_policy), [])


class BankingFailClosedTests(BankingTestsBase):
    def test_queries_return_nothing_with_no_tenant_context(self):
        with tenant_context(organization_id=self.org_a.id):
            self._txn(amount="-100.00")
            self.assertEqual(BankAccount.objects.count(), 3)
            self.assertEqual(BankTransaction.objects.count(), 1)

        clear_tenant_context()
        self.assertEqual(BankAccount.objects.count(), 0)
        self.assertEqual(BankTransaction.objects.count(), 0)
        # The unscoped manager skips the app filter but not the DB policy.
        self.assertEqual(BankAccount.all_objects.count(), 0)
        self.assertEqual(BankTransaction.all_objects.count(), 0)

    def test_one_organization_cannot_read_anothers_statement(self):
        with tenant_context(organization_id=self.org_a.id):
            self._txn(amount="-100.00", description="CONFIDENTIAL SUPPLIER")
            self._txn(amount="5000.00", description="CUSTOMER RECEIPT")

        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(BankTransaction.objects.count(), 0)
            self.assertEqual(BankTransaction.all_objects.count(), 0)

    def test_matching_cannot_reach_across_organizations(self):
        from banking.services.matching import create_match
        from core.exceptions import ApplicationError

        with tenant_context(organization_id=self.org_a.id):
            payment = self._customer_payment(amount=Decimal("1000.00"))

        with tenant_context(organization_id=self.org_b.id):
            from banking.services.transactions import add_manual_transaction

            foreign = add_manual_transaction(
                organization=self.org_b, bank_account=self.bank_b,
                transaction_date=payment.payment_date, amount=Decimal("1000.00"),
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_match(
                    transaction_id=foreign.id, organization=self.org_b,
                    counterpart_field="customer_payment", counterpart=payment, actor=self.user_b,
                )
            # Fails closed either on the tenant check or because the payment
            # is simply invisible — never by succeeding.
            self.assertIn(
                ctx.exception.get_codes(),
                {"cross_org_reference", "match_account_mismatch", "customer_payment_not_found"},
            )
