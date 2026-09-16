from decimal import Decimal

from core.tenancy import tenant_context
from projects.selectors import (
    get_project_expense_cost,
    get_project_hours,
    get_project_labour_cost,
    get_project_profitability,
    get_project_revenue,
    get_project_unbilled_value,
)
from projects.services.billing import invoice_project_time
from projects.services.projects import update_project
from projects.services.time_entries import reject_time_entry, submit_time_entry
from projects.tests.base import DAY, DUE_DATE, ProjectsTestsBase
from purchases.services.expenses import create_expense, post_expense
from sales.services.invoices import post_invoice, void_invoice


class ProjectHoursTests(ProjectsTestsBase):
    def test_hours_split_by_billability_and_status(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("5"))                         # billable, approved
            self._log(hours=Decimal("3"))                              # billable, draft
            self._approved(task=self.admin_task, hours=Decimal("2"))   # non-billable, approved
            pending = self._log(hours=Decimal("1"))
            submit_time_entry(entry_id=pending.id, organization=self.org_a, actor=self.staff_user)

            hours = get_project_hours(project=self.project)
            self.assertEqual(hours["total_hours"], Decimal("11.00"))
            self.assertEqual(hours["billable_hours"], Decimal("9.00"))
            self.assertEqual(hours["non_billable_hours"], Decimal("2.00"))
            self.assertEqual(hours["approved_hours"], Decimal("7.00"))
            self.assertEqual(hours["pending_approval_hours"], Decimal("1.00"))
            self.assertEqual(hours["invoiced_hours"], Decimal("0"))


class ProjectCostTests(ProjectsTestsBase):
    def test_labour_cost_includes_non_billable_time(self):
        """Non-billable work still costs money — that is why it is tracked."""
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("5"))                        # 5 * 60 = 300
            self._approved(task=self.admin_task, hours=Decimal("2"))  # 2 * 60 = 120
            self.assertEqual(get_project_labour_cost(project=self.project), Decimal("420.00"))

    def test_rejected_time_is_excluded_from_cost(self):
        with tenant_context(organization_id=self.org_a.id):
            keep = self._approved(hours=Decimal("5"))
            drop = self._log(hours=Decimal("4"))
            submit_time_entry(entry_id=drop.id, organization=self.org_a, actor=self.staff_user)
            reject_time_entry(entry_id=drop.id, organization=self.org_a, actor=self.manager_user)

            self.assertEqual(get_project_labour_cost(project=self.project), Decimal("300.00"))
            self.assertIsNotNone(keep.id)

    def test_time_without_a_cost_rate_costs_nothing_rather_than_guessing(self):
        from projects.services.projects import update_project_member

        with tenant_context(organization_id=self.org_a.id):
            update_project_member(member=self.staff_member, cost_rate=None)
            self._log(hours=Decimal("5"))
            self.assertEqual(get_project_labour_cost(project=self.project), Decimal("0"))

    def test_expense_cost_uses_net_amount_not_tax_inclusive_total(self):
        """Recoverable input tax is not a cost to the business — including it
        would overstate every project's cost by the tax rate."""
        with tenant_context(organization_id=self.org_a.id):
            expense = create_expense(
                organization=self.org_a, expense_date=DAY, amount=Decimal("1000.00"),
                expense_account=self.expense_account, paid_through_account=self.bank_account,
                currency=self.currency, tax_rate=Decimal("18"),
                tax_recoverable_account=self.ar_account, project=self.project,
            )
            self.assertEqual(expense.total, Decimal("1180.00"))
            post_expense(expense_id=expense.id, organization=self.org_a)
            self.assertEqual(get_project_expense_cost(project=self.project), Decimal("1000.00"))

    def test_draft_expenses_are_not_yet_a_cost(self):
        with tenant_context(organization_id=self.org_a.id):
            create_expense(
                organization=self.org_a, expense_date=DAY, amount=Decimal("500.00"),
                expense_account=self.expense_account, paid_through_account=self.bank_account,
                currency=self.currency, project=self.project,
            )
            self.assertEqual(get_project_expense_cost(project=self.project), Decimal("0"))

    def test_expenses_on_another_project_are_not_counted(self):
        from projects.services.projects import activate_project, create_project

        with tenant_context(organization_id=self.org_a.id):
            other = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-O",
                name="Other", currency=self.currency,
            )
            activate_project(project_id=other.id, organization=self.org_a)
            expense = create_expense(
                organization=self.org_a, expense_date=DAY, amount=Decimal("700.00"),
                expense_account=self.expense_account, paid_through_account=self.bank_account,
                currency=self.currency, project=other,
            )
            post_expense(expense_id=expense.id, organization=self.org_a)
            self.assertEqual(get_project_expense_cost(project=self.project), Decimal("0"))
            self.assertEqual(get_project_expense_cost(project=other), Decimal("700.00"))


class ProjectRevenueTests(ProjectsTestsBase):
    def test_revenue_comes_from_the_invoice_not_the_time_entries(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("10"))  # 10 * 150 = 1500
            self.assertEqual(get_project_unbilled_value(project=self.project), Decimal("1500.00"))
            self.assertEqual(get_project_revenue(project=self.project), Decimal("0"))

            invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            # Now billed: revenue recognised, pipeline emptied.
            self.assertEqual(get_project_revenue(project=self.project), Decimal("1500.00"))
            self.assertEqual(get_project_unbilled_value(project=self.project), Decimal("0"))

    def test_revenue_excludes_tax(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("10"))
            invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
                tax_rate=Decimal("18"), tax_payable_account=self.tax_account,
            )
            # 1500 net, not the 1770 the customer pays.
            self.assertEqual(get_project_revenue(project=self.project), Decimal("1500.00"))

    def test_voided_invoice_revenue_disappears(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("10"))
            invoice = invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            post_invoice(invoice_id=invoice.id, organization=self.org_a, actor=self.user_a)
            self.assertEqual(get_project_revenue(project=self.project), Decimal("1500.00"))

            void_invoice(invoice_id=invoice.id, organization=self.org_a, actor=self.user_a)
            self.assertEqual(get_project_revenue(project=self.project), Decimal("0"))

    def test_unbilled_value_ignores_rejected_and_non_billable_time(self):
        with tenant_context(organization_id=self.org_a.id):
            self._log(hours=Decimal("4"))                            # billable draft: counts
            self._log(task=self.admin_task, hours=Decimal("3"))      # non-billable: does not
            rejected = self._log(hours=Decimal("2"))
            submit_time_entry(entry_id=rejected.id, organization=self.org_a, actor=self.staff_user)
            reject_time_entry(entry_id=rejected.id, organization=self.org_a, actor=self.manager_user)

            self.assertEqual(get_project_unbilled_value(project=self.project), Decimal("600.00"))


class ProjectProfitabilityTests(ProjectsTestsBase):
    def test_full_margin_calculation(self):
        with tenant_context(organization_id=self.org_a.id):
            # 10 billable hours at 150 charged / 60 cost.
            self._approved(hours=Decimal("10"))
            # 5 non-billable hours, cost only.
            self._approved(task=self.admin_task, hours=Decimal("5"))
            invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            expense = create_expense(
                organization=self.org_a, expense_date=DAY, amount=Decimal("200.00"),
                expense_account=self.expense_account, paid_through_account=self.bank_account,
                currency=self.currency, project=self.project,
            )
            post_expense(expense_id=expense.id, organization=self.org_a)

            report = get_project_profitability(project=self.project)

        self.assertEqual(report["revenue"], Decimal("1500.00"))
        self.assertEqual(report["labour_cost"], Decimal("900.00"))    # 15h * 60
        self.assertEqual(report["expense_cost"], Decimal("200.00"))
        self.assertEqual(report["total_cost"], Decimal("1100.00"))
        self.assertEqual(report["margin"], Decimal("400.00"))
        self.assertEqual(report["margin_percent"], Decimal("26.67"))
        self.assertEqual(report["total_hours"], Decimal("15.00"))
        self.assertEqual(report["billable_hours"], Decimal("10.00"))

    def test_margin_percent_is_none_when_nothing_has_been_billed(self):
        """Undefined, not zero — rendering 0% would read as break-even."""
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("5"))
            report = get_project_profitability(project=self.project)
        self.assertEqual(report["revenue"], Decimal("0"))
        self.assertIsNone(report["margin_percent"])
        self.assertEqual(report["unbilled_value"], Decimal("750.00"))

    def test_negative_margin_is_reported_as_such(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("2"))  # revenue 300, cost 120
            invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            expense = create_expense(
                organization=self.org_a, expense_date=DAY, amount=Decimal("1000.00"),
                expense_account=self.expense_account, paid_through_account=self.bank_account,
                currency=self.currency, project=self.project,
            )
            post_expense(expense_id=expense.id, organization=self.org_a)
            report = get_project_profitability(project=self.project)

        self.assertEqual(report["margin"], Decimal("-820.00"))
        self.assertLess(report["margin_percent"], Decimal("0"))

    def test_hours_over_budget_is_reported(self):
        with tenant_context(organization_id=self.org_a.id):
            update_project(project=self.project, budget_hours=Decimal("6"))
            self.project.refresh_from_db()
            self._approved(hours=Decimal("10"))
            report = get_project_profitability(project=self.project)
        self.assertEqual(report["hours_over_budget"], Decimal("4.00"))

    def test_no_overage_reported_when_within_budget(self):
        with tenant_context(organization_id=self.org_a.id):
            update_project(project=self.project, budget_hours=Decimal("40"))
            self.project.refresh_from_db()
            self._approved(hours=Decimal("10"))
            report = get_project_profitability(project=self.project)
        self.assertEqual(report["hours_over_budget"], Decimal("0"))

    def test_profitability_is_derived_never_stored(self):
        with tenant_context(organization_id=self.org_a.id):
            first = get_project_profitability(project=self.project)
            self.assertEqual(first["total_hours"], Decimal("0"))
            self._approved(hours=Decimal("3"))
            second = get_project_profitability(project=self.project)
        self.assertEqual(second["total_hours"], Decimal("3.00"))
