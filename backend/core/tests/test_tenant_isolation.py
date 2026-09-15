"""
Acceptance gate for Phase 0: Organization A must never be able to read or
write Organization B's records, at both the application layer (TenantManager)
and the database layer (PostgreSQL RLS), and the system must fail closed
when no tenant context is set at all.
"""

import datetime

from django.db import connection
from django.test import TestCase
from rest_framework.test import APITestCase

from accounts.models import FiscalYear
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_org_with_owner


class AppLevelTenantIsolationTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "owner-b@example.com")

        with tenant_context(organization_id=self.org_a.id):
            self.fy_a = FiscalYear.objects.create(
                organization=self.org_a,
                start_date=datetime.date(2025, 4, 1),
                end_date=datetime.date(2026, 3, 31),
            )
        with tenant_context(organization_id=self.org_b.id):
            self.fy_b = FiscalYear.objects.create(
                organization=self.org_b,
                start_date=datetime.date(2025, 4, 1),
                end_date=datetime.date(2026, 3, 31),
            )

    def test_scoped_manager_only_returns_current_org_rows(self):
        with tenant_context(organization_id=self.org_a.id):
            visible = list(FiscalYear.objects.all())
        self.assertEqual(visible, [self.fy_a])

    def test_scoped_manager_never_leaks_other_org_rows(self):
        with tenant_context(organization_id=self.org_a.id):
            leaked = FiscalYear.objects.filter(pk=self.fy_b.pk).first()
        self.assertIsNone(leaked)

    def test_no_tenant_context_fails_closed(self):
        clear_tenant_context()
        self.assertEqual(list(FiscalYear.objects.all()), [])

    def test_rls_blocks_rows_even_if_app_level_filter_is_bypassed(self):
        """Defense in depth: even querying via the UNSCOPED manager
        (`all_objects`, e.g. a developer mistake) must not leak cross-tenant
        rows once the database session is scoped to a single organization,
        because PostgreSQL RLS enforces it independently of the ORM."""
        with tenant_context(organization_id=self.org_a.id):
            visible = list(FiscalYear.all_objects.all())
        self.assertEqual(visible, [self.fy_a])
        self.assertNotIn(self.fy_b, visible)

    def test_rls_blocks_direct_sql_without_tenant_context(self):
        clear_tenant_context()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM accounts_fiscalyear")
            rows = cursor.fetchall()
        self.assertEqual(rows, [])

    def test_rls_direct_sql_scoped_to_org_a_cannot_see_org_b(self):
        with tenant_context(organization_id=self.org_a.id):
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM accounts_fiscalyear")
                ids = {row[0] for row in cursor.fetchall()}
        self.assertEqual(ids, {self.fy_a.id})


class ApiLevelTenantIsolationTests(APITestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "api-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "api-owner-b@example.com")

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def test_member_can_list_own_org_members(self):
        self._auth(self.user_a)
        response = self.client.get(
            "/api/v1/organizations/members/", HTTP_X_ORGANIZATION_ID=str(self.org_a.id)
        )
        self.assertEqual(response.status_code, 200)
        emails = {m["user"]["email"] for m in response.data}
        self.assertEqual(emails, {"api-owner-a@example.com"})

    def test_non_member_cannot_access_other_org(self):
        self._auth(self.user_a)
        response = self.client.get(
            "/api/v1/organizations/members/", HTTP_X_ORGANIZATION_ID=str(self.org_b.id)
        )
        self.assertEqual(response.status_code, 403)

    def test_missing_organization_header_is_rejected(self):
        self._auth(self.user_a)
        response = self.client.get("/api/v1/organizations/members/")
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(
            "/api/v1/organizations/members/", HTTP_X_ORGANIZATION_ID=str(self.org_a.id)
        )
        self.assertEqual(response.status_code, 401)

    def test_listing_my_organizations_excludes_others(self):
        self._auth(self.user_a)
        response = self.client.get("/api/v1/organizations/")
        self.assertEqual(response.status_code, 200)
        names = {org["name"] for org in response.data}
        self.assertEqual(names, {"Org A"})
