"""Shared fixture for projects tests.

One organization with a customer, a sellable service item, an hourly project
with two members on different rates, and a second organization for
cross-tenant assertions.
"""

import datetime
from decimal import Decimal

from django.test import TestCase, TransactionTestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear, Membership
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner, make_user
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from projects.models.project import BillingMethod
from projects.services.projects import (
    activate_project,
    add_project_member,
    create_project,
    create_task,
)
from sales.services.customers import create_customer

PROJECT_START = datetime.date(2026, 4, 1)
DAY = datetime.date(2026, 4, 10)
DUE_DATE = datetime.date(2026, 5, 10)


class ProjectsFixtureMixin:
    """Split from the Django base class for the same reason
    `purchases.tests.base` is: `TestCase` subclasses `TransactionTestCase`, so
    mixing them directly silently yields `TestCase` semantics."""

    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "proj-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "proj-owner-b@example.com")
        self.currency = make_currency("INR")

        # A second person in org A, so "someone else's timesheet" is testable.
        self.staff_user = make_user("proj-staff@example.com")
        with tenant_context(user_id=self.staff_user.id):
            Membership.objects.create(organization=self.org_a, user=self.staff_user, role=Role.STAFF)

        # A dedicated approver. Needed because nobody may approve their own
        # time: a fixture that always approved as the owner could not create
        # approved time for the OWNER, which several billing tests need in
        # order to exercise two different rates on one task.
        self.manager_user = make_user("proj-manager@example.com")
        with tenant_context(user_id=self.manager_user.id):
            Membership.objects.create(organization=self.org_a, user=self.manager_user, role=Role.ADMIN)

        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=PROJECT_START, end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="Accounts Receivable", account_type=AccountType.ASSET
            )
            self.revenue_account = create_account(
                organization=self.org_a, code="4000", name="Services Revenue", account_type=AccountType.INCOME
            )
            self.tax_account = create_account(
                organization=self.org_a, code="2100", name="Output Tax", account_type=AccountType.LIABILITY
            )
            self.expense_account = create_account(
                organization=self.org_a, code="5300", name="Project Costs", account_type=AccountType.EXPENSE
            )
            self.bank_account = create_account(
                organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
            )

            self.unit = create_unit(organization=self.org_a, code="HR", name="Hour")
            self.service_item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.revenue_account,
            )
            # A stocked product, to prove time cannot be billed as one.
            self.product_item = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="W-1", track_inventory=True, sales_account=self.revenue_account,
            )

            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )

            self.project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-1",
                name="Website Rebuild", billing_method=BillingMethod.HOURLY,
                default_hourly_rate=Decimal("100.00"), service_item=self.service_item,
                start_date=PROJECT_START, currency=self.currency,
            )
            self.project = activate_project(project_id=self.project.id, organization=self.org_a)

            self.task = create_task(project=self.project, name="Development", is_billable=True)
            self.admin_task = create_task(project=self.project, name="Internal admin", is_billable=False)

            # owner: no member rate, so the project default (100) applies.
            self.owner_member = add_project_member(
                project=self.project, user=self.user_a, cost_rate=Decimal("40.00")
            )
            # staff: a negotiated member rate that must beat the project default.
            self.staff_member = add_project_member(
                project=self.project, user=self.staff_user,
                billable_rate=Decimal("150.00"), cost_rate=Decimal("60.00"),
            )

        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    # -------------------------------------------------------- helpers

    def _log(self, *, user=None, task=None, hours=Decimal("8"), entry_date=DAY, **kwargs):
        from projects.services.time_entries import log_time

        return log_time(
            organization=self.org_a, project=self.project, task=task or self.task,
            user=user or self.staff_user, entry_date=entry_date, hours=hours, **kwargs,
        )

    def _approved(self, *, user=None, hours=Decimal("8"), task=None, entry_date=DAY):
        """Logs, submits and approves one entry.

        Always approved by `manager_user`, never by the entry's own author —
        the service refuses self-approval, so a fixture that approved as the
        author would be untestable for that author's own time.
        """
        from projects.services.time_entries import approve_time_entry, submit_time_entry

        entry = self._log(user=user or self.staff_user, task=task, hours=hours, entry_date=entry_date)
        submit_time_entry(entry_id=entry.id, organization=self.org_a, actor=entry.user)
        return approve_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.manager_user)


class ProjectsTestsBase(ProjectsFixtureMixin, TestCase):
    """Default base: wrapped in a transaction, rolled back per test."""


class ProjectsTransactionTestsBase(ProjectsFixtureMixin, TransactionTestCase):
    """For tests needing real transaction boundaries (concurrency races)."""
