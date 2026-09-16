"""Reconciliation rules: first match wins, and auto-posting is opt-in."""

from decimal import Decimal

from accounting.models.journal import JournalEntry
from banking.models.rule import RuleAction, RuleDirection
from banking.models.statement import BankTransactionStatus
from banking.services.rules import (
    apply_rules_to_statement_import,
    apply_rules_to_transaction,
    create_rule,
    find_matching_rule,
    rule_matches,
)
from banking.tests.base import SIGNED_MAPPING, BankingTestsBase
from core.exceptions import ApplicationError
from core.tenancy import tenant_context


class RuleConditionTests(BankingTestsBase):
    def test_description_matching_is_case_insensitive_substring(self):
        with tenant_context(organization_id=self.org_a.id):
            rule = create_rule(
                organization=self.org_a, name="Cloud hosting", description_contains="aws",
                target_account=self.gl_office,
            )
            self.assertTrue(
                rule_matches(rule=rule, transaction=self._txn(amount="-4500.00", description="AWS EMEA SARL"))
            )
            self.assertFalse(
                rule_matches(rule=rule, transaction=self._txn(amount="-4500.00", description="AZURE"))
            )

    def test_direction_and_amount_conditions(self):
        with tenant_context(organization_id=self.org_a.id):
            rule = create_rule(
                organization=self.org_a, name="Large payments out", direction=RuleDirection.OUTFLOW,
                amount_min=Decimal("1000.00"), target_account=self.gl_office,
            )
            self.assertTrue(rule_matches(rule=rule, transaction=self._txn(amount="-5000.00")))
            # Compared on the ABSOLUTE amount, so a user writing "over 1000"
            # does not have to reason about the sign convention.
            self.assertFalse(rule_matches(rule=rule, transaction=self._txn(amount="-500.00")))
            self.assertFalse(rule_matches(rule=rule, transaction=self._txn(amount="5000.00")))

    def test_a_rule_can_be_scoped_to_one_account(self):
        with tenant_context(organization_id=self.org_a.id):
            rule = create_rule(
                organization=self.org_a, name="Card fees", bank_account=self.card,
                description_contains="fee", target_account=self.gl_charges,
            )
            on_card = self._txn(amount="-99.00", bank_account=self.card, description="ANNUAL FEE")
            on_bank = self._txn(amount="-99.00", bank_account=self.bank, description="ANNUAL FEE")
            self.assertTrue(rule_matches(rule=rule, transaction=on_card))
            self.assertFalse(rule_matches(rule=rule, transaction=on_bank))

    def test_a_rule_with_no_conditions_is_refused(self):
        """It would match every line on the account and categorize an entire
        statement to one account on import."""
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_rule(organization=self.org_a, name="Catch all", target_account=self.gl_office)
            self.assertEqual(ctx.exception.get_codes(), "rule_has_no_conditions")

    def test_a_categorizing_rule_needs_a_target_account(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_rule(
                    organization=self.org_a, name="No target", description_contains="x"
                )
            self.assertEqual(ctx.exception.get_codes(), "rule_target_account_required")

    def test_lowest_priority_number_wins_and_rules_do_not_compose(self):
        with tenant_context(organization_id=self.org_a.id):
            create_rule(
                organization=self.org_a, name="General outflow", priority=50,
                direction=RuleDirection.OUTFLOW, target_account=self.gl_office,
            )
            create_rule(
                organization=self.org_a, name="Specific AWS", priority=10,
                description_contains="aws", target_account=self.gl_charges,
            )
            transaction = self._txn(amount="-4500.00", description="AWS EMEA")
            winner = find_matching_rule(organization=self.org_a, transaction=transaction)
            self.assertEqual(winner.name, "Specific AWS")

    def test_an_inactive_rule_never_fires(self):
        with tenant_context(organization_id=self.org_a.id):
            rule = create_rule(
                organization=self.org_a, name="Off", description_contains="aws",
                target_account=self.gl_office,
            )
            rule.is_active = False
            rule.save(update_fields=["is_active"])
            transaction = self._txn(amount="-4500.00", description="AWS")
            self.assertIsNone(find_matching_rule(organization=self.org_a, transaction=transaction))


class RuleApplicationTests(BankingTestsBase):
    def test_without_auto_confirm_a_rule_posts_nothing(self):
        """The default. A rule that fires with auto_confirm posts a journal
        unattended; that must be something the user switched on, not
        something they discover afterwards in their P&L."""
        with tenant_context(organization_id=self.org_a.id):
            create_rule(
                organization=self.org_a, name="AWS", description_contains="aws",
                target_account=self.gl_office,
            )
            transaction = self._txn(amount="-4500.00", description="AWS EMEA")
            before = JournalEntry.objects.count()

            outcome = apply_rules_to_transaction(
                transaction_id=transaction.id, organization=self.org_a, actor=self.user_a
            )
            self.assertFalse(outcome["applied"])
            self.assertEqual(outcome["reason"], "awaiting_confirmation")
            self.assertEqual(JournalEntry.objects.count(), before)
            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.UNMATCHED)

    def test_with_auto_confirm_the_rule_categorizes_and_posts(self):
        with tenant_context(organization_id=self.org_a.id):
            create_rule(
                organization=self.org_a, name="AWS", description_contains="aws",
                target_account=self.gl_office, auto_confirm=True,
            )
            transaction = self._txn(amount="-4500.00", description="AWS EMEA")

            outcome = apply_rules_to_transaction(
                transaction_id=transaction.id, organization=self.org_a, actor=self.user_a
            )
            self.assertTrue(outcome["applied"])
            self.assertEqual(outcome["reason"], "categorized")

            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.MATCHED)
            match = transaction.matches.get()
            self.assertEqual(match.journal_entry.lines.get(account=self.gl_office).debit, Decimal("4500.00"))

    def test_an_excluding_rule_excludes_with_its_own_name_as_the_reason(self):
        with tenant_context(organization_id=self.org_a.id):
            create_rule(
                organization=self.org_a, name="Ignore balance rows", action=RuleAction.EXCLUDE,
                description_contains="balance brought forward",
            )
            transaction = self._txn(amount="-1.00", description="BALANCE BROUGHT FORWARD")
            outcome = apply_rules_to_transaction(
                transaction_id=transaction.id, organization=self.org_a, actor=self.user_a
            )
            self.assertEqual(outcome["reason"], "excluded")
            transaction.refresh_from_db()
            self.assertEqual(transaction.status, BankTransactionStatus.EXCLUDED)
            self.assertIn("Ignore balance rows", transaction.excluded_reason)

    def test_no_rule_matching_is_the_ordinary_case_not_an_error(self):
        with tenant_context(organization_id=self.org_a.id):
            transaction = self._txn(amount="-4500.00", description="SOMETHING NEW")
            outcome = apply_rules_to_transaction(
                transaction_id=transaction.id, organization=self.org_a
            )
            self.assertEqual(outcome["reason"], "no_rule_matched")
            self.assertIsNone(outcome["rule"])

    def test_rules_run_across_a_whole_import(self):
        content = (
            "Date,Narration,Ref,Amount\n"
            "2026-04-10,AWS EMEA SARL,R1,-4500.00\n"
            "2026-04-11,UPI SWIGGY,R2,-450.50\n"
            "2026-04-12,AWS EMEA SARL,R3,-1200.00\n"
        )
        with tenant_context(organization_id=self.org_a.id):
            create_rule(
                organization=self.org_a, name="AWS", description_contains="aws",
                target_account=self.gl_office, auto_confirm=True,
            )
            statement = self._import_csv(content, mapping=SIGNED_MAPPING)
            counts = apply_rules_to_statement_import(
                statement_import=statement, organization=self.org_a, actor=self.user_a
            )
            self.assertEqual(counts["categorized"], 2)
            self.assertEqual(counts["no_rule_matched"], 1)


class RuleTenantTests(BankingTestsBase):
    def test_a_target_account_from_another_organization_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_rule(
                    organization=self.org_a, name="Foreign", description_contains="x",
                    target_account=self.gl_bank_b,
                )
            self.assertEqual(ctx.exception.get_codes(), "cross_org_reference")
