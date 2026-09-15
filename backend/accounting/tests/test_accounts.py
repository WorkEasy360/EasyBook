from django.db import IntegrityError
from django.test import TestCase

from accounting.models.account import Account, AccountType
from accounting.services.accounts import create_account, update_account
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_org_with_owner


class AccountModelTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "acct-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "acct-owner-b@example.com")

    def test_create_account(self):
        with tenant_context(organization_id=self.org_a.id):
            account = create_account(
                organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET
            )
        self.assertEqual(account.code, "1000")
        self.assertTrue(account.is_debit_normal)

    def test_code_unique_per_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            create_account(organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET)
            with self.assertRaises(IntegrityError):
                create_account(
                    organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
                )

    def test_same_code_allowed_across_organizations(self):
        with tenant_context(organization_id=self.org_a.id):
            create_account(organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET)
        with tenant_context(organization_id=self.org_b.id):
            account_b = create_account(
                organization=self.org_b, code="1000", name="Cash", account_type=AccountType.ASSET
            )
        self.assertEqual(account_b.code, "1000")

    def test_hierarchy_parent_child(self):
        with tenant_context(organization_id=self.org_a.id):
            assets = create_account(organization=self.org_a, code="1000", name="Assets", account_type=AccountType.ASSET)
            cash = create_account(
                organization=self.org_a, code="1010", name="Cash", account_type=AccountType.ASSET, parent=assets
            )
            self.assertEqual(cash.parent_id, assets.id)
            self.assertIn(cash, assets.children.all())

    def test_parent_must_belong_to_same_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            assets_a = create_account(organization=self.org_a, code="1000", name="Assets", account_type=AccountType.ASSET)
        with tenant_context(organization_id=self.org_b.id):
            with self.assertRaises(ApplicationError):
                create_account(
                    organization=self.org_b, code="1010", name="Cash", account_type=AccountType.ASSET, parent=assets_a
                )

    def test_prevent_circular_hierarchy(self):
        with tenant_context(organization_id=self.org_a.id):
            grandparent = create_account(organization=self.org_a, code="1000", name="Assets", account_type=AccountType.ASSET)
            parent = create_account(
                organization=self.org_a, code="1010", name="Current Assets", account_type=AccountType.ASSET, parent=grandparent
            )
            with self.assertRaises(ApplicationError):
                update_account(account=grandparent, parent=parent)

    def test_system_account_protected_fields_cannot_change(self):
        with tenant_context(organization_id=self.org_a.id):
            system_account = create_account(
                organization=self.org_a, code="3900", name="Opening Balance Equity",
                account_type=AccountType.EQUITY, is_system=True,
            )
            with self.assertRaises(ApplicationError):
                update_account(account=system_account, code="3901")
            updated = update_account(account=system_account, name="Opening Balances")
            self.assertEqual(updated.name, "Opening Balances")

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_account(organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET)
        with tenant_context(organization_id=self.org_b.id):
            visible = list(Account.objects.all())
        self.assertEqual(visible, [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_account(organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET)
        clear_tenant_context()
        self.assertEqual(list(Account.objects.all()), [])
