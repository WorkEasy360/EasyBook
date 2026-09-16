from decimal import Decimal

from rest_framework.test import APITestCase

from core.tenancy import tenant_context
from projects.tests.base import ProjectsFixtureMixin, ProjectsTestsBase
from reports.selectors.projects import get_project_profitability_report


class ProjectProfitabilityReportTests(ProjectsTestsBase):
    def test_report_includes_billable_and_labour_cost(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("8"))  # staff_user: billable_rate 150, cost_rate 60
            rows = get_project_profitability_report(organization=self.org_a)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["project_id"], self.project.id)
        self.assertEqual(row["billable_hours"], Decimal("8"))
        self.assertEqual(row["labour_cost"], Decimal("480.00"))  # 8 * 60
        self.assertEqual(row["unbilled_value"], Decimal("1200.00"))  # 8 * 150

    def test_report_is_tenant_scoped(self):
        with tenant_context(organization_id=self.org_b.id):
            rows = get_project_profitability_report(organization=self.org_b)
        self.assertEqual(rows, [])

    def test_report_filters_by_status(self):
        with tenant_context(organization_id=self.org_a.id):
            rows = get_project_profitability_report(organization=self.org_a, status="active")
        self.assertEqual(len(rows), 1)
        rows_completed = get_project_profitability_report(organization=self.org_a, status="completed")
        self.assertEqual(rows_completed, [])


class ProjectProfitabilityAPITests(ProjectsFixtureMixin, APITestCase):
    def _headers(self):
        return {"HTTP_X_ORGANIZATION_ID": str(self.org_a.id)}

    def test_owner_with_view_all_timesheets_can_view(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/api/v1/reports/projects/profitability/", **self._headers())
        self.assertEqual(response.status_code, 200)

    def test_staff_without_view_all_timesheets_forbidden(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get("/api/v1/reports/projects/profitability/", **self._headers())
        self.assertEqual(response.status_code, 403)
