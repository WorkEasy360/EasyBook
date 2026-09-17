from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from sales.models.customer import Customer
from sales.services.customers import (
    archive_customer,
    assert_customer_usable_for_new_transaction,
    create_customer,
    update_customer,
)


class CustomerTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "customer-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "customer-owner-b@example.com")
        self.currency = make_currency("INR")

    def test_create_customer(self):
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme Retail",
                currency=self.currency,
            )
        self.assertEqual(customer.customer_code, "CUST-1")
        self.assertTrue(customer.is_active)

    def test_customer_code_unique_per_organization(self):
        # The service refuses a re-used code with a domain error (it used to
        # surface as an IntegrityError and a 500 at the API); the database
        # constraint stays the backstop for a write that bypasses the service.
        with tenant_context(organization_id=self.org_a.id):
            create_customer(organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency)
            with self.assertRaises(ApplicationError) as raised:
                create_customer(
                    organization=self.org_a, customer_code="CUST-1", display_name="B", currency=self.currency
                )
            self.assertEqual(raised.exception.get_codes(), "duplicate_customer_code")
            with self.assertRaises(IntegrityError):
                Customer.objects.create(
                    organization=self.org_a, customer_code="CUST-1", display_name="Bypass", currency=self.currency
                )

    def test_customer_code_can_repeat_across_organizations(self):
        with tenant_context(organization_id=self.org_a.id):
            create_customer(organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency)
        with tenant_context(organization_id=self.org_b.id):
            create_customer(organization=self.org_b, customer_code="CUST-1", display_name="B", currency=self.currency)

    def test_negative_credit_limit_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_customer(
                    organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency,
                    credit_limit=Decimal("-1"),
                )

    def test_db_constraint_blocks_negative_credit_limit(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(IntegrityError):
                Customer.objects.create(
                    organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency,
                    credit_limit=Decimal("-1"),
                )

    def test_update_customer(self):
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency
            )
            updated = update_customer(customer=customer, display_name="A Renamed")
        self.assertEqual(updated.display_name, "A Renamed")

    def test_archive_customer_deactivates(self):
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency
            )
            archived = archive_customer(customer=customer)
        self.assertFalse(archived.is_active)

    def test_archived_customer_rejected_for_new_transaction(self):
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency
            )
            archive_customer(customer=customer)
            with self.assertRaises(ApplicationError):
                assert_customer_usable_for_new_transaction(customer=customer)

    def test_active_customer_usable_for_new_transaction(self):
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency
            )
            assert_customer_usable_for_new_transaction(customer=customer)  # should not raise

    def test_archived_customer_remains_accessible(self):
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency
            )
            archive_customer(customer=customer)
            fetched = Customer.objects.get(pk=customer.pk)
        self.assertEqual(fetched.customer_code, "CUST-1")

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_customer(organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency)
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(Customer.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_customer(organization=self.org_a, customer_code="CUST-1", display_name="A", currency=self.currency)
        clear_tenant_context()
        self.assertEqual(list(Customer.objects.all()), [])
