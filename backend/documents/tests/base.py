"""Shared fixture for documents tests. Builds two organizations so every
test file can assert cross-tenant rejection without rebuilding the world —
same shape as purchases/tests/base.py."""

from django.test import TestCase, TransactionTestCase

from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner


class DocumentsFixtureMixin:
    def setUp(self):
        self.org_a, self.user_a, self.membership_a = make_org_with_owner("Org A", "docs-owner-a@example.com")
        self.org_b, self.user_b, self.membership_b = make_org_with_owner("Org B", "docs-owner-b@example.com")



class DocumentsTestsBase(DocumentsFixtureMixin, TestCase):
    """Default base: fast, wrapped in a transaction rolled back per test."""


class DocumentsTransactionTestsBase(DocumentsFixtureMixin, TransactionTestCase):
    """For concurrency races that need committed rows visible to a second
    connection, and RLS raw-SQL checks — same rationale as
    purchases/tests/base.py::PurchasesTransactionTestsBase."""


def tenant(organization):
    return tenant_context(organization_id=organization.id)
