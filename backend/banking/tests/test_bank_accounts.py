"""Bank account setup: the GL pairing and the account-number rule."""

import datetime
from decimal import Decimal

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from banking.models.bank_account import BankAccount, BankAccountKind
from banking.services.bank_accounts import create_bank_account, update_bank_account
from banking.tests.base import BankingTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context


class BankAccountCreationTests(BankingTestsBase):
    def test_a_bank_account_must_be_linked_to_an_asset_account(self):
        with tenant_context(organization_id=self.org_a.id):
            liability = create_account(
                organization=self.org_a, code="2900", name="Loan", account_type=AccountType.LIABILITY
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bank_account(
                    organization=self.org_a, name="Wrong", account=liability, currency=self.currency
                )
            self.assertEqual(ctx.exception.get_codes(), "invalid_account_type")

    def test_a_credit_card_must_be_linked_to_a_liability_account(self):
        """A card is money owed. Pairing it with an asset account would make
        every balance sheet overstate cash by the card balance."""
        with tenant_context(organization_id=self.org_a.id):
            asset = create_account(
                organization=self.org_a, code="1900", name="Petty Cash", account_type=AccountType.ASSET
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bank_account(
                    organization=self.org_a, name="Card", account=asset,
                    kind=BankAccountKind.CREDIT_CARD, currency=self.currency,
                )
            self.assertEqual(ctx.exception.get_codes(), "invalid_account_type")

    def test_one_ledger_account_cannot_back_two_bank_accounts(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_bank_account(
                    organization=self.org_a, name="Duplicate", account=self.gl_bank, currency=self.currency
                )
            self.assertEqual(ctx.exception.get_codes(), "account_already_linked")

    def test_only_the_last_four_digits_of_an_account_number_are_stored(self):
        """Root CLAUDE.md: never store raw card credentials. The full number
        is truncated on the way in rather than rejected, so a paste cannot
        end up persisted anywhere — including in an error message."""
        with tenant_context(organization_id=self.org_a.id):
            self.bank.refresh_from_db()
            self.assertEqual(self.bank.account_number_last4, "3456")
            self.card.refresh_from_db()
            self.assertEqual(self.card.account_number_last4, "1111")

            stored = BankAccount.objects.filter(pk=self.card.pk).values().first()
            for value in stored.values():
                self.assertNotIn("4111111111111111", str(value))

    def test_masked_number_is_what_is_exposed(self):
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(self.bank.masked_number, "••••3456")
            # __str__ lands in logs and admin lists, so it carries no digits.
            self.assertEqual(str(self.bank), "HDFC Current")

    def test_an_opening_balance_needs_the_date_it_applied_on(self):
        with tenant_context(organization_id=self.org_a.id):
            account = create_account(
                organization=self.org_a, code="1002", name="Third", account_type=AccountType.ASSET
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bank_account(
                    organization=self.org_a, name="Third", account=account,
                    currency=self.currency, opening_balance=Decimal("500.00"),
                )
            self.assertEqual(ctx.exception.get_codes(), "opening_balance_date_required")

    def test_unknown_provider_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            account = create_account(
                organization=self.org_a, code="1003", name="Fourth", account_type=AccountType.ASSET
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bank_account(
                    organization=self.org_a, name="Fourth", account=account,
                    currency=self.currency, provider_key="plaid",
                )
            self.assertEqual(ctx.exception.get_codes(), "provider_unknown")


class BankAccountUpdateTests(BankingTestsBase):
    def test_the_ledger_account_cannot_be_repointed(self):
        """Re-pointing would orphan every reconciliation already closed
        against the old account."""
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                update_bank_account(bank_account=self.bank, account=self.gl_bank_two)
            self.assertEqual(ctx.exception.get_codes(), "bank_account_field_immutable")

    def test_descriptive_fields_can_change(self):
        with tenant_context(organization_id=self.org_a.id):
            updated = update_bank_account(
                bank_account=self.bank, bank_name="HDFC Bank Ltd", notes="Primary operating account"
            )
            self.assertEqual(updated.bank_name, "HDFC Bank Ltd")

    def test_opening_balance_locks_once_statement_lines_exist(self):
        """Moving the anchor after import retrospectively changes balances
        that may already have been reconciled and signed off."""
        with tenant_context(organization_id=self.org_a.id):
            update_bank_account(
                bank_account=self.bank,
                opening_balance=Decimal("100.00"),
                opening_balance_date=datetime.date(2026, 4, 1),
            )
            self._txn(amount="250.00")
            with self.assertRaises(ApplicationError) as ctx:
                update_bank_account(bank_account=self.bank, opening_balance=Decimal("999.00"))
            self.assertEqual(ctx.exception.get_codes(), "opening_balance_locked")

    def test_updating_an_account_number_records_no_digits_in_the_audit_trail(self):
        from audit.models import AuditLog

        with tenant_context(organization_id=self.org_a.id):
            update_bank_account(bank_account=self.bank, account_number="9999888877776543")
            self.bank.refresh_from_db()
            self.assertEqual(self.bank.account_number_last4, "6543")

            entry = AuditLog.objects.filter(
                object_type="banking.BankAccount", object_id=str(self.bank.id)
            ).order_by("-created_at").first()
            self.assertEqual(entry.changes["account_number_last4"], "[redacted]")
            self.assertNotIn("6543", str(entry.changes))


class BankAccountTenantTests(BankingTestsBase):
    def test_a_ledger_account_from_another_organization_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_bank_account(
                    organization=self.org_a, name="Foreign", account=self.gl_bank_b, currency=self.currency
                )
            self.assertEqual(ctx.exception.get_codes(), "cross_org_reference")

    def test_accounts_do_not_leak_across_organizations(self):
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(BankAccount.objects.count(), 3)
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(BankAccount.objects.count(), 1)


class BankAccountNameValidationTests(BankingTestsBase):
    """Regression: a reused name hit uniq_bank_account_name_per_org as an
    IntegrityError and the API answered 500."""

    def test_a_duplicate_name_is_refused_with_a_code(self):
        with tenant_context(organization_id=self.org_a.id):
            spare = create_account(
                organization=self.org_a, code="1950", name="Spare Bank", account_type=AccountType.ASSET
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bank_account(
                    organization=self.org_a, name="HDFC Current", account=spare, currency=self.currency
                )
            self.assertEqual(ctx.exception.get_codes(), "bank_account_name_taken")

    def test_renaming_onto_another_account_is_refused_but_keeping_the_name_is_not(self):
        with tenant_context(organization_id=self.org_a.id):
            update_bank_account(bank_account=self.savings, name="HDFC Savings", notes="unchanged name")
            with self.assertRaises(ApplicationError) as ctx:
                update_bank_account(bank_account=self.savings, name="HDFC Current")
            self.assertEqual(ctx.exception.get_codes(), "bank_account_name_taken")

    def test_the_name_is_unique_per_organization_only(self):
        with tenant_context(organization_id=self.org_b.id):
            spare = create_account(
                organization=self.org_b, code="1950", name="Spare Bank", account_type=AccountType.ASSET
            )
            created = create_bank_account(
                organization=self.org_b, name="HDFC Current", account=spare, currency=self.currency
            )
            self.assertEqual(created.name, "HDFC Current")
