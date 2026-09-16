import datetime
from decimal import Decimal

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from projects.models.project import BillingMethod, Project, ProjectStatus, Task
from projects.services.projects import (
    activate_project,
    add_project_member,
    cancel_project,
    complete_project,
    create_project,
    create_task,
    hold_project,
    resolve_service_item,
    update_project,
    update_project_member,
)
from projects.tests.base import PROJECT_START, ProjectsTestsBase


class ProjectCreationTests(ProjectsTestsBase):
    def test_create_starts_as_draft_and_posts_nothing(self):
        with tenant_context(organization_id=self.org_a.id):
            before = JournalEntry.objects.count()
            project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-2",
                name="Second", currency=self.currency,
            )
            self.assertEqual(project.status, ProjectStatus.DRAFT)
            # A project is not a financial event.
            self.assertEqual(JournalEntry.objects.count(), before)

    def test_project_code_unique_per_organization(self):
        from django.db.utils import IntegrityError

        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(IntegrityError):
                create_project(
                    organization=self.org_a, customer=self.customer, project_code="PRJ-1",
                    name="Duplicate", currency=self.currency,
                )

    def test_fixed_fee_requires_an_amount(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_project(
                    organization=self.org_a, customer=self.customer, project_code="PRJ-3",
                    name="Fixed", billing_method=BillingMethod.FIXED_FEE, currency=self.currency,
                )
        self.assertEqual(ctx.exception.detail.code, "fixed_fee_amount_required")

    def test_fixed_fee_amount_rejected_on_an_hourly_project(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_project(
                    organization=self.org_a, customer=self.customer, project_code="PRJ-4",
                    name="Hourly", billing_method=BillingMethod.HOURLY,
                    fixed_fee_amount=Decimal("500"), currency=self.currency,
                )
        self.assertEqual(ctx.exception.detail.code, "fixed_fee_amount_unexpected")

    def test_service_item_must_be_a_service_not_a_product(self):
        """Billing hours as a stocked product would make posting the invoice
        try to issue stock for work done."""
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_project(
                    organization=self.org_a, customer=self.customer, project_code="PRJ-5",
                    name="Bad item", service_item=self.product_item, currency=self.currency,
                )
        self.assertEqual(ctx.exception.detail.code, "service_item_required")

    def test_cross_org_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_project(
                    organization=self.org_a, customer=self.customer_b, project_code="PRJ-6",
                    name="Foreign", currency=self.currency,
                )
        self.assertEqual(ctx.exception.detail.code, "customer_cross_org")

    def test_end_date_before_start_date_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_project(
                    organization=self.org_a, customer=self.customer, project_code="PRJ-7",
                    name="Backwards", start_date=PROJECT_START,
                    end_date=PROJECT_START - datetime.timedelta(days=1), currency=self.currency,
                )
        self.assertEqual(ctx.exception.detail.code, "project_end_before_start")


class ProjectLifecycleTests(ProjectsTestsBase):
    def test_draft_to_active_to_completed(self):
        with tenant_context(organization_id=self.org_a.id):
            project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-L",
                name="Lifecycle", currency=self.currency,
            )
            project = activate_project(project_id=project.id, organization=self.org_a)
            self.assertEqual(project.status, ProjectStatus.ACTIVE)
            project = hold_project(project_id=project.id, organization=self.org_a)
            self.assertEqual(project.status, ProjectStatus.ON_HOLD)
            project = activate_project(project_id=project.id, organization=self.org_a)
            project = complete_project(project_id=project.id, organization=self.org_a)
            self.assertEqual(project.status, ProjectStatus.COMPLETED)

    def test_a_completed_project_cannot_be_reopened(self):
        """A follow-on engagement is a new project, not a resurrected one."""
        with tenant_context(organization_id=self.org_a.id):
            complete_project(project_id=self.project.id, organization=self.org_a)
            with self.assertRaises(ApplicationError) as ctx:
                activate_project(project_id=self.project.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "project_invalid_status")

    def test_cannot_cancel_a_project_with_invoiced_time(self):
        from projects.services.billing import invoice_project_time
        from projects.tests.base import DAY, DUE_DATE

        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("4"))
            invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            with self.assertRaises(ApplicationError) as ctx:
                cancel_project(project_id=self.project.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "project_has_invoiced_time")

    def test_billing_method_cannot_change_after_creation(self):
        """Existing entries carry billability and rates resolved under the
        old method; flipping it would leave them disagreeing with the rules."""
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                update_project(project=self.project, billing_method=BillingMethod.NON_BILLABLE)
        self.assertEqual(ctx.exception.detail.code, "billing_method_immutable")

    def test_updating_to_the_same_billing_method_is_a_no_op(self):
        with tenant_context(organization_id=self.org_a.id):
            updated = update_project(
                project=self.project, billing_method=BillingMethod.HOURLY, name="Renamed"
            )
        self.assertEqual(updated.name, "Renamed")

    def test_time_cannot_be_logged_against_a_non_active_project(self):
        with tenant_context(organization_id=self.org_a.id):
            hold_project(project_id=self.project.id, organization=self.org_a)
            self.project.refresh_from_db()
            with self.assertRaises(ApplicationError) as ctx:
                self._log()
        self.assertEqual(ctx.exception.detail.code, "project_not_active")


class ProjectMemberTests(ProjectsTestsBase):
    def test_cannot_assign_a_user_outside_the_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                add_project_member(project=self.project, user=self.user_b)
        self.assertEqual(ctx.exception.detail.code, "user_not_org_member")

    def test_cannot_assign_the_same_user_twice(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                add_project_member(project=self.project, user=self.staff_user)
        self.assertEqual(ctx.exception.detail.code, "member_already_assigned")

    def test_negative_rates_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                update_project_member(member=self.staff_member, billable_rate=Decimal("-1"))
        self.assertEqual(ctx.exception.detail.code, "rate_invalid")

    def test_rate_change_does_not_restate_existing_entries(self):
        """The whole point of freezing rates onto the entry."""
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log(user=self.staff_user, hours=Decimal("2"))
            self.assertEqual(entry.billable_rate, Decimal("150.00"))

            update_project_member(member=self.staff_member, billable_rate=Decimal("999.00"))
            entry.refresh_from_db()
            self.assertEqual(entry.billable_rate, Decimal("150.00"))
            self.assertEqual(entry.billable_amount, Decimal("300.00"))

            # New time picks up the new rate.
            later = self._log(user=self.staff_user, hours=Decimal("1"))
            self.assertEqual(later.billable_rate, Decimal("999.00"))


class TaskTests(ProjectsTestsBase):
    def test_task_name_unique_per_project(self):
        from django.db.utils import IntegrityError

        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(IntegrityError):
                create_task(project=self.project, name="Development")

    def test_service_item_resolution_prefers_the_task(self):
        with tenant_context(organization_id=self.org_a.id):
            other_item = create_task(project=self.project, name="Specialist work")
            self.assertEqual(
                resolve_service_item(project=self.project, task=other_item), self.service_item
            )

            task_with_item = create_task(
                project=self.project, name="Design", service_item=self.service_item
            )
            self.assertEqual(
                resolve_service_item(project=self.project, task=task_with_item), self.service_item
            )

    def test_task_service_item_must_be_a_service(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_task(project=self.project, name="Bad", service_item=self.product_item)
        self.assertEqual(ctx.exception.detail.code, "service_item_required")


class ProjectTenantIsolationTests(ProjectsTestsBase):
    def test_other_org_sees_nothing(self):
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(Project.objects.count(), 0)
            self.assertEqual(Task.objects.count(), 0)

    def test_fails_closed_with_no_tenant_context(self):
        clear_tenant_context()
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(Project.all_objects.count(), 0)

    def test_other_org_cannot_transition(self):
        with tenant_context(organization_id=self.org_b.id):
            with self.assertRaises(ApplicationError) as ctx:
                complete_project(project_id=self.project.id, organization=self.org_b)
        self.assertEqual(ctx.exception.detail.code, "project_not_found")
