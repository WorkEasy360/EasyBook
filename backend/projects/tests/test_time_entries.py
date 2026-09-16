import datetime
from decimal import Decimal

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from projects.models.project import BillingMethod
from projects.models.time_entry import TimeEntry, TimeEntryStatus
from projects.services.projects import activate_project, create_project, create_task, update_project_member
from projects.services.rates import resolve_billable_rate, resolve_cost_rate, resolve_is_billable
from projects.services.time_entries import (
    approve_time_entry,
    delete_time_entry,
    log_time,
    reject_time_entry,
    submit_time_entry,
    update_time_entry,
)
from projects.tests.base import DAY, PROJECT_START, ProjectsTestsBase


class RateResolutionTests(ProjectsTestsBase):
    def test_member_rate_beats_task_rate_and_project_default(self):
        """A negotiated rate for a named person is the more specific
        agreement — it holds whichever task they touch."""
        with tenant_context(organization_id=self.org_a.id):
            task = create_task(
                project=self.project, name="Rated task", hourly_rate=Decimal("120.00")
            )
            rate = resolve_billable_rate(project=self.project, task=task, user=self.staff_user)
        self.assertEqual(rate, Decimal("150.00"))

    def test_task_rate_beats_project_default_when_member_has_none(self):
        with tenant_context(organization_id=self.org_a.id):
            task = create_task(
                project=self.project, name="Rated task", hourly_rate=Decimal("120.00")
            )
            rate = resolve_billable_rate(project=self.project, task=task, user=self.user_a)
        self.assertEqual(rate, Decimal("120.00"))

    def test_project_default_is_the_final_fallback(self):
        with tenant_context(organization_id=self.org_a.id):
            rate = resolve_billable_rate(project=self.project, task=self.task, user=self.user_a)
        self.assertEqual(rate, Decimal("100.00"))

    def test_explicit_rate_beats_everything(self):
        with tenant_context(organization_id=self.org_a.id):
            rate = resolve_billable_rate(
                project=self.project, task=self.task, user=self.staff_user,
                explicit_rate=Decimal("222.00"),
            )
        self.assertEqual(rate, Decimal("222.00"))

    def test_cost_rate_has_no_project_or_task_fallback(self):
        """What a person costs is a fact about the person. Guessing would put
        a fabricated number in a margin report."""
        with tenant_context(organization_id=self.org_a.id):
            update_project_member(member=self.staff_member, cost_rate=None)
            self.assertIsNone(resolve_cost_rate(project=self.project, user=self.staff_user))

    def test_non_billable_project_makes_every_task_non_billable(self):
        with tenant_context(organization_id=self.org_a.id):
            project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-NB",
                name="Internal", billing_method=BillingMethod.NON_BILLABLE, currency=self.currency,
            )
            task = create_task(project=project, name="Work", is_billable=True)
            self.assertFalse(resolve_is_billable(project=project, task=task))

    def test_fixed_fee_project_tracks_hours_but_they_are_not_chargeable(self):
        with tenant_context(organization_id=self.org_a.id):
            project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-FF",
                name="Fixed", billing_method=BillingMethod.FIXED_FEE,
                fixed_fee_amount=Decimal("5000"), currency=self.currency,
            )
            task = create_task(project=project, name="Work", is_billable=True)
            self.assertFalse(resolve_is_billable(project=project, task=task))


class TimeEntryLoggingTests(ProjectsTestsBase):
    def test_log_freezes_billability_and_both_rates(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log(user=self.staff_user, hours=Decimal("7.5"))
        self.assertEqual(entry.status, TimeEntryStatus.DRAFT)
        self.assertTrue(entry.is_billable)
        self.assertEqual(entry.billable_rate, Decimal("150.00"))
        self.assertEqual(entry.cost_rate, Decimal("60.00"))
        self.assertEqual(entry.billable_amount, Decimal("1125.00"))
        self.assertEqual(entry.cost_amount, Decimal("450.00"))

    def test_logging_posts_no_accounting(self):
        with tenant_context(organization_id=self.org_a.id):
            before = JournalEntry.objects.count()
            self._log()
            self.assertEqual(JournalEntry.objects.count(), before)

    def test_non_billable_task_records_cost_but_no_billable_rate(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log(task=self.admin_task, hours=Decimal("2"))
        self.assertFalse(entry.is_billable)
        self.assertIsNone(entry.billable_rate)
        self.assertEqual(entry.billable_amount, Decimal("0"))
        # Non-billable time still costs us — that is why it is tracked.
        self.assertEqual(entry.cost_amount, Decimal("120.00"))

    def test_client_cannot_mark_its_own_time_billable(self):
        """`is_billable` is resolved, never accepted. A non-billable task
        stays non-billable however the caller asks."""
        with tenant_context(organization_id=self.org_a.id):
            entry = log_time(
                organization=self.org_a, project=self.project, task=self.admin_task,
                user=self.staff_user, entry_date=DAY, hours=Decimal("1"),
            )
        self.assertFalse(entry.is_billable)

    def test_zero_and_negative_hours_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            for bad in (Decimal("0"), Decimal("-1")):
                with self.assertRaises(ApplicationError) as ctx:
                    self._log(hours=bad)
                self.assertEqual(ctx.exception.detail.code, "hours_invalid")

    def test_more_than_24_hours_in_one_entry_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._log(hours=Decimal("25"))
        self.assertEqual(ctx.exception.detail.code, "hours_exceed_day")

    def test_task_from_another_project_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            other = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-X",
                name="Other", currency=self.currency,
            )
            other = activate_project(project_id=other.id, organization=self.org_a)
            other_task = create_task(project=other, name="Elsewhere")
            with self.assertRaises(ApplicationError) as ctx:
                self._log(task=other_task)
        self.assertEqual(ctx.exception.detail.code, "task_project_mismatch")

    def test_time_before_the_project_start_date_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._log(entry_date=PROJECT_START - datetime.timedelta(days=1))
        self.assertEqual(ctx.exception.detail.code, "entry_date_before_project_start")


class TimeEntryApprovalTests(ProjectsTestsBase):
    def test_submit_then_approve(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log()
            entry = submit_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.staff_user)
            self.assertEqual(entry.status, TimeEntryStatus.SUBMITTED)
            self.assertIsNotNone(entry.submitted_at)

            entry = approve_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.manager_user)
            self.assertEqual(entry.status, TimeEntryStatus.APPROVED)
            self.assertEqual(entry.approved_by_id, self.manager_user.id)

    def test_nobody_approves_their_own_time(self):
        """Enforced in the service as well as the role matrix, because an
        owner holds both LOG_TIME and APPROVE_TIME and the role check alone
        would wave it through."""
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log(user=self.user_a)
            submit_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.user_a)
            with self.assertRaises(ApplicationError) as ctx:
                approve_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.user_a)
        self.assertEqual(ctx.exception.detail.code, "self_approval_forbidden")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_approve_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._approved()
            again = approve_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.manager_user)
        self.assertEqual(again.status, TimeEntryStatus.APPROVED)

    def test_rejected_entry_returns_to_draft_when_edited(self):
        """The hours were really worked; the argument is about how they are
        recorded. Deleting them would lose the cost side too."""
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log(hours=Decimal("8"))
            submit_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.staff_user)
            entry = reject_time_entry(
                entry_id=entry.id, organization=self.org_a, actor=self.manager_user, reason="wrong task"
            )
            self.assertEqual(entry.status, TimeEntryStatus.REJECTED)
            self.assertEqual(entry.rejection_reason, "wrong task")

            entry = update_time_entry(
                entry_id=entry.id, organization=self.org_a, actor=self.staff_user, hours=Decimal("6")
            )
            self.assertEqual(entry.status, TimeEntryStatus.DRAFT)
            self.assertEqual(entry.hours, Decimal("6.00"))
            self.assertEqual(entry.rejection_reason, "")

    def test_approved_entry_cannot_be_edited(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._approved()
            with self.assertRaises(ApplicationError) as ctx:
                update_time_entry(
                    entry_id=entry.id, organization=self.org_a, actor=self.staff_user, hours=Decimal("1")
                )
        self.assertEqual(ctx.exception.detail.code, "time_entry_not_editable")

    def test_approval_can_be_withdrawn_while_unbilled(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._approved()
            entry = reject_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.manager_user)
        self.assertEqual(entry.status, TimeEntryStatus.REJECTED)
        self.assertIsNone(entry.approved_at)

    def test_changing_task_re_resolves_billability(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log(task=self.task)
            self.assertTrue(entry.is_billable)
            entry = update_time_entry(
                entry_id=entry.id, organization=self.org_a, actor=self.staff_user, task=self.admin_task
            )
        self.assertFalse(entry.is_billable)
        self.assertIsNone(entry.billable_rate)

    def test_draft_entry_can_be_deleted(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log()
            delete_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.staff_user)
            self.assertEqual(TimeEntry.objects.filter(pk=entry.id).count(), 0)


class TimeEntryTenantIsolationTests(ProjectsTestsBase):
    def test_other_org_cannot_see_or_approve(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._log()
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(TimeEntry.objects.count(), 0)
            with self.assertRaises(ApplicationError) as ctx:
                approve_time_entry(entry_id=entry.id, organization=self.org_b, actor=self.user_b)
        self.assertEqual(ctx.exception.detail.code, "time_entry_not_found")
