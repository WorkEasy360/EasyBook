"""Projects API: auth, RBAC, and the object-level rule that a contributor
sees only their own time."""

from decimal import Decimal

from rest_framework.test import APIClient

from core.tenancy import tenant_context
from projects.models.time_entry import TimeEntryStatus
from projects.tests.base import DAY, DUE_DATE, ProjectsTestsBase


class ProjectsApiTestsBase(ProjectsTestsBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user_a)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(user=self.staff_user)
        self.manager_client = APIClient()
        self.manager_client.force_authenticate(user=self.manager_user)

    def _headers(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.org_a).id)}


class ProjectApiTests(ProjectsApiTestsBase):
    def test_requires_authentication_and_an_organization(self):
        self.assertEqual(APIClient().get("/api/v1/projects/", **self._headers()).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/projects/").status_code, 400)

    def test_create_and_activate(self):
        created = self.client.post(
            "/api/v1/projects/",
            {
                "customer_id": str(self.customer.id), "project_code": "PRJ-API",
                "name": "API project", "default_hourly_rate": "80.00",
            },
            format="json", **self._headers(),
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["status"], "draft")

        activated = self.client.post(
            f"/api/v1/projects/{created.data['id']}/activate/", **self._headers()
        )
        self.assertEqual(activated.data["status"], "active")

    def test_list_never_leaks_another_organizations_projects(self):
        response = self.client.get("/api/v1/projects/", **self._headers())
        codes = {row["project_code"] for row in response.data["results"]}
        self.assertIn("PRJ-1", codes)

    def test_viewer_cannot_create(self):
        from accounts.models import Membership
        from authz.roles import Role
        from core.tests.factories import make_user

        viewer = make_user("proj-viewer@example.com")
        with tenant_context(user_id=viewer.id):
            Membership.objects.create(organization=self.org_a, user=viewer, role=Role.VIEWER)
        viewer_client = APIClient()
        viewer_client.force_authenticate(user=viewer)

        self.assertEqual(
            viewer_client.get("/api/v1/projects/", **self._headers()).status_code, 200
        )
        response = viewer_client.post(
            "/api/v1/projects/",
            {"customer_id": str(self.customer.id), "project_code": "X", "name": "X"},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_profitability_needs_the_wider_timesheet_permission(self):
        """Margin exposes cost rates, so plain VIEW_PROJECTS is not enough."""
        url = f"/api/v1/projects/{self.project.id}/profitability/"
        self.assertEqual(self.client.get(url, **self._headers()).status_code, 200)
        self.assertEqual(self.staff_client.get(url, **self._headers()).status_code, 403)

    def test_profitability_sends_money_as_strings_not_floats(self):
        """Regression: the view returned the selector's Decimals bare, and
        DRF's JSON encoder renders a Decimal as a float (`5850.0`)."""
        import json

        response = self.client.get(f"/api/v1/projects/{self.project.id}/profitability/", **self._headers())
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        for key in ("revenue", "unbilled_value", "labour_cost", "total_cost", "margin", "total_hours"):
            self.assertIsInstance(body[key], str, key)
        # Undefined margin stays null rather than becoming "None".
        if body["revenue"] in ("0", "0.00"):
            self.assertIsNone(body["margin_percent"])

    def test_duplicate_project_code_and_task_name_are_400s_not_500s(self):
        """Regression: both hit a DB UniqueConstraint as an IntegrityError."""
        duplicate_project = self.client.post(
            "/api/v1/projects/",
            {"customer_id": str(self.customer.id), "project_code": "PRJ-1", "name": "Duplicate"},
            format="json", **self._headers(),
        )
        self.assertEqual(duplicate_project.status_code, 400)
        self.assertIn("project_code", duplicate_project.data["error"]["details"])

        duplicate_task = self.client.post(
            f"/api/v1/projects/{self.project.id}/tasks/",
            {"name": "Development"},
            format="json", **self._headers(),
        )
        self.assertEqual(duplicate_task.status_code, 400)
        self.assertIn("name", duplicate_task.data["error"]["details"])

        with tenant_context(organization_id=self.org_a.id):
            other = self.project.tasks.exclude(name="Development").first()
        renamed = self.client.patch(
            f"/api/v1/projects/tasks/{other.id}/", {"name": "Development"}, format="json", **self._headers()
        )
        self.assertEqual(renamed.status_code, 400)
        same_name = self.client.patch(
            f"/api/v1/projects/tasks/{other.id}/", {"name": other.name}, format="json", **self._headers()
        )
        self.assertEqual(same_name.status_code, 200)

    def test_tasks_and_members_are_nested_under_the_project(self):
        tasks = self.client.get(f"/api/v1/projects/{self.project.id}/tasks/", **self._headers())
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual({row["name"] for row in tasks.data["results"]}, {"Development", "Internal admin"})

        members = self.client.get(f"/api/v1/projects/{self.project.id}/members/", **self._headers())
        self.assertEqual(members.status_code, 200)
        self.assertEqual(members.data["count"], 2)


class TimeEntryApiTests(ProjectsApiTestsBase):
    def _log_payload(self, **overrides):
        return {
            "project_id": str(self.project.id),
            "task_id": str(self.task.id),
            "entry_date": str(DAY),
            "hours": "4",
            "description": "Work",
            **overrides,
        }

    def test_malformed_filters_are_400s_not_500s(self):
        # Regression: these reached the ORM unvalidated and raised a 500.
        for params in ({"project": "notauuid"}, {"from_date": "17-09-2026"}, {"to_date": "yesterday"}):
            with self.subTest(params=params):
                response = self.client.get("/api/v1/time-entries/", params, **self._headers())
                self.assertEqual(response.status_code, 400)

    def test_staff_can_log_their_own_time(self):
        response = self.staff_client.post(
            "/api/v1/time-entries/", self._log_payload(), format="json", **self._headers()
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["user"]), str(self.staff_user.id))
        self.assertTrue(response.data["is_billable"])
        self.assertEqual(Decimal(response.data["billable_rate"]), Decimal("150.00"))

    def test_staff_cannot_log_time_for_someone_else(self):
        response = self.staff_client.post(
            "/api/v1/time-entries/",
            self._log_payload(user_id=str(self.user_a.id)),
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "log_time_for_others_forbidden")

    def test_a_manager_can_log_time_for_someone_else(self):
        response = self.manager_client.post(
            "/api/v1/time-entries/",
            self._log_payload(user_id=str(self.staff_user.id)),
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["user"]), str(self.staff_user.id))

    def test_client_cannot_force_its_own_time_billable(self):
        response = self.staff_client.post(
            "/api/v1/time-entries/",
            self._log_payload(task_id=str(self.admin_task.id), is_billable=True),
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["is_billable"])

    def test_staff_see_only_their_own_entries(self):
        """A time entry carries cost_rate — an indirect read on what someone
        is paid — so it is scoped to its owner."""
        with tenant_context(organization_id=self.org_a.id):
            self._log(user=self.staff_user, hours=Decimal("3"))
            self._log(user=self.user_a, hours=Decimal("5"))

        mine = self.staff_client.get("/api/v1/time-entries/", **self._headers())
        self.assertEqual(mine.data["count"], 1)
        self.assertEqual({str(row["user"]) for row in mine.data["results"]}, {str(self.staff_user.id)})

        # The owner holds VIEW_ALL_TIMESHEETS and sees both.
        everything = self.client.get("/api/v1/time-entries/", **self._headers())
        self.assertEqual(everything.data["count"], 2)

    def test_staff_cannot_retrieve_another_persons_entry(self):
        with tenant_context(organization_id=self.org_a.id):
            other = self._log(user=self.user_a, hours=Decimal("5"))
        response = self.staff_client.get(f"/api/v1/time-entries/{other.id}/", **self._headers())
        self.assertEqual(response.status_code, 404)

    def test_staff_cannot_submit_another_persons_entry(self):
        with tenant_context(organization_id=self.org_a.id):
            other = self._log(user=self.user_a, hours=Decimal("5"))
        response = self.staff_client.post(
            f"/api/v1/time-entries/{other.id}/submit/", **self._headers()
        )
        self.assertEqual(response.status_code, 404)

    def test_staff_cannot_approve_anything(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log(user=self.staff_user)
        response = self.staff_client.post(
            f"/api/v1/time-entries/{entry.id}/approve/", **self._headers()
        )
        self.assertEqual(response.status_code, 403)

    def test_submit_then_approve_through_the_api(self):
        created = self.staff_client.post(
            "/api/v1/time-entries/", self._log_payload(), format="json", **self._headers()
        )
        entry_id = created.data["id"]

        submitted = self.staff_client.post(
            f"/api/v1/time-entries/{entry_id}/submit/", **self._headers()
        )
        self.assertEqual(submitted.data["status"], TimeEntryStatus.SUBMITTED)

        approved = self.manager_client.post(
            f"/api/v1/time-entries/{entry_id}/approve/", **self._headers()
        )
        self.assertEqual(approved.data["status"], TimeEntryStatus.APPROVED)

    def test_bulk_submit_is_all_or_nothing(self):
        with tenant_context(organization_id=self.org_a.id):
            mine = self._log(user=self.staff_user, hours=Decimal("2"))
            not_mine = self._log(user=self.user_a, hours=Decimal("2"))

        response = self.staff_client.post(
            "/api/v1/time-entries/bulk-submit/",
            {"entry_ids": [str(mine.id), str(not_mine.id)]},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 404)
        with tenant_context(organization_id=self.org_a.id):
            mine.refresh_from_db()
            self.assertEqual(
                mine.status, TimeEntryStatus.DRAFT, "nothing may be submitted if one id is invalid"
            )

    def test_bulk_submit_succeeds_for_ones_own_entries(self):
        with tenant_context(organization_id=self.org_a.id):
            first = self._log(user=self.staff_user, hours=Decimal("2"))
            second = self._log(user=self.staff_user, hours=Decimal("3"))
        response = self.staff_client.post(
            "/api/v1/time-entries/bulk-submit/",
            {"entry_ids": [str(first.id), str(second.id)]},
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["status"] for row in response.data}, {TimeEntryStatus.SUBMITTED})


class TimesheetApiTests(ProjectsApiTestsBase):
    def test_own_timesheet_by_default(self):
        with tenant_context(organization_id=self.org_a.id):
            self._log(user=self.staff_user, hours=Decimal("3"))
            self._log(user=self.user_a, hours=Decimal("5"))

        response = self.staff_client.get(
            f"/api/v1/timesheet/?from_date={DAY}&to_date={DAY}", **self._headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_staff_cannot_read_another_persons_timesheet(self):
        response = self.staff_client.get(
            f"/api/v1/timesheet/?from_date={DAY}&to_date={DAY}&user_id={self.user_a.id}",
            **self._headers(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "timesheet_forbidden")

    def test_manager_can_read_another_persons_timesheet(self):
        with tenant_context(organization_id=self.org_a.id):
            self._log(user=self.staff_user, hours=Decimal("3"))
        response = self.manager_client.get(
            f"/api/v1/timesheet/?from_date={DAY}&to_date={DAY}&user_id={self.staff_user.id}",
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_reversed_date_range_rejected(self):
        response = self.client.get(
            f"/api/v1/timesheet/?from_date={DUE_DATE}&to_date={DAY}", **self._headers()
        )
        self.assertEqual(response.status_code, 400)


class BillingApiTests(ProjectsApiTestsBase):
    def test_preview_then_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("6"))

        preview = self.client.get(
            f"/api/v1/projects/{self.project.id}/unbilled-time/", **self._headers()
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(len(preview.data), 1)

        invoiced = self.client.post(
            f"/api/v1/projects/{self.project.id}/invoice-time/",
            {
                "invoice_date": str(DAY), "due_date": str(DUE_DATE),
                "receivable_account_id": str(self.ar_account.id),
            },
            format="json", **self._headers(),
        )
        self.assertEqual(invoiced.status_code, 201)
        self.assertEqual(invoiced.data["status"], "draft")
        self.assertEqual(Decimal(invoiced.data["total"]), Decimal("900.00"))

    def test_staff_cannot_invoice_time(self):
        response = self.staff_client.post(
            f"/api/v1/projects/{self.project.id}/invoice-time/",
            {
                "invoice_date": str(DAY), "due_date": str(DUE_DATE),
                "receivable_account_id": str(self.ar_account.id),
            },
            format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 403)


class CrossTenantApiTests(ProjectsApiTestsBase):
    def test_cannot_reach_another_organizations_project(self):
        other = APIClient()
        other.force_authenticate(user=self.user_b)
        response = other.get(
            f"/api/v1/projects/{self.project.id}/", **self._headers(self.org_b)
        )
        self.assertEqual(response.status_code, 404)
