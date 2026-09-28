from django.db.utils import IntegrityError
from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from audit.models import AuditLog
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from purchases.models.vendor import Vendor
from purchases.services.vendors import (
    archive_vendor,
    assert_vendor_usable_for_new_transaction,
    create_vendor,
    update_vendor,
)


class VendorTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "vendor-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "vendor-b@example.com")
        self.currency = make_currency("INR")

    def _vendor(self, organization=None, code="VEN-1", **kwargs):
        organization = organization or self.org_a
        return create_vendor(
            organization=organization, vendor_code=code, display_name="Acme Supplies",
            currency=self.currency, **kwargs,
        )

    def test_create_vendor(self):
        with tenant_context(organization_id=self.org_a.id):
            vendor = self._vendor(email="ap@acme.test", payment_terms_days=30)
        self.assertEqual(vendor.vendor_code, "VEN-1")
        self.assertEqual(vendor.payment_terms_days, 30)
        self.assertTrue(vendor.is_active)

    def test_create_vendor_records_audit(self):
        with tenant_context(organization_id=self.org_a.id):
            vendor = self._vendor()
            entries = AuditLog.objects.filter(object_type="purchases.Vendor", object_id=str(vendor.id))
            self.assertEqual(entries.count(), 1)
            self.assertEqual(entries.first().action, AuditLog.Action.CREATE)

    def test_vendor_code_unique_per_organization(self):
        # The service refuses a re-used code with a domain error (it used to
        # surface as an IntegrityError and a 500 at the API); the database
        # constraint stays the backstop for a write that bypasses the service.
        with tenant_context(organization_id=self.org_a.id):
            existing = self._vendor()
            with self.assertRaises(ApplicationError) as raised:
                self._vendor()
            self.assertEqual(raised.exception.get_codes(), "duplicate_vendor_code")
            with self.assertRaises(IntegrityError):
                Vendor.objects.create(
                    organization=self.org_a, vendor_code=existing.vendor_code, display_name="Bypass",
                    currency=self.currency,
                )

    def test_same_vendor_code_allowed_in_another_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            self._vendor()
        with tenant_context(organization_id=self.org_b.id):
            other = self._vendor(organization=self.org_b)
        self.assertEqual(other.vendor_code, "VEN-1")

    def test_currency_required(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_vendor(
                    organization=self.org_a, vendor_code="VEN-2", display_name="No Currency", currency=None
                )
        self.assertEqual(ctx.exception.detail.code, "currency_required")

    def test_default_payable_account_must_be_liability(self):
        with tenant_context(organization_id=self.org_a.id):
            asset = create_account(
                organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            with self.assertRaises(ApplicationError) as ctx:
                self._vendor(default_payable_account=asset)
        self.assertEqual(ctx.exception.detail.code, "invalid_account_type")

    def test_default_payable_account_must_be_same_organization(self):
        with tenant_context(organization_id=self.org_b.id):
            foreign_ap = create_account(
                organization=self.org_b, code="2000", name="AP", account_type=AccountType.LIABILITY
            )
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._vendor(default_payable_account=foreign_ap)
        self.assertEqual(ctx.exception.detail.code, "cross_org_reference")

    def test_update_vendor_records_only_changed_fields(self):
        with tenant_context(organization_id=self.org_a.id):
            vendor = self._vendor()
            update_vendor(vendor=vendor, display_name="Acme Ltd", phone="123")
            entries = AuditLog.objects.filter(
                object_type="purchases.Vendor", object_id=str(vendor.id), action=AuditLog.Action.UPDATE
            )
            self.assertEqual(entries.count(), 1)
            self.assertEqual(set(entries.first().changes), {"display_name", "phone"})

    def test_update_with_no_actual_change_writes_no_audit(self):
        with tenant_context(organization_id=self.org_a.id):
            vendor = self._vendor(email="ap@acme.test")
            update_vendor(vendor=vendor, email="ap@acme.test")
            self.assertFalse(
                AuditLog.objects.filter(
                    object_type="purchases.Vendor", object_id=str(vendor.id), action=AuditLog.Action.UPDATE
                ).exists()
            )

    def test_archive_and_reject_for_new_transactions(self):
        with tenant_context(organization_id=self.org_a.id):
            vendor = self._vendor()
            assert_vendor_usable_for_new_transaction(vendor=vendor)  # active: no raise
            archive_vendor(vendor=vendor)
            self.assertFalse(vendor.is_active)
            with self.assertRaises(ApplicationError) as ctx:
                assert_vendor_usable_for_new_transaction(vendor=vendor)
        self.assertEqual(ctx.exception.detail.code, "vendor_inactive")


class VendorTenantIsolationTests(TestCase):
    """Proves the two-layer guarantee for this app's master data: the app-level
    TenantManager AND the Postgres RLS policy (core/CLAUDE.md)."""

    def setUp(self):
        self.org_a, _, _ = make_org_with_owner("Org A", "vendoriso-a@example.com")
        self.org_b, _, _ = make_org_with_owner("Org B", "vendoriso-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            self.vendor_a = create_vendor(
                organization=self.org_a, vendor_code="A-1", display_name="A Supplies", currency=self.currency
            )

    def test_other_org_cannot_see_vendor(self):
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(Vendor.objects.count(), 0)
            self.assertIsNone(Vendor.objects.filter(pk=self.vendor_a.pk).first())

    def test_fails_closed_with_no_tenant_context(self):
        clear_tenant_context()
        self.assertEqual(Vendor.objects.count(), 0)

    def test_rls_blocks_even_the_unscoped_manager(self):
        # all_objects skips the TenantManager filter but NOT the database
        # policy — RLS is the control, the manager is defense in depth.
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(Vendor.all_objects.filter(pk=self.vendor_a.pk).count(), 0)
