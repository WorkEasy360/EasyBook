"""The Phase 5 acceptance-gate workflow, end to end:

    Project -> Task -> Timesheet -> Approval -> Billable Time -> Invoice
            -> Accounting -> Profitability

Asserted at every hop: projects post no journals of their own, the same hour
can never be billed twice, the invoice posts through `sales`, and the margin
report reconciles with the documents behind it.
"""

import datetime
from decimal import Decimal

from accounting.models.journal import JournalEntry
from accounting.selectors import get_trial_balance
from core.tenancy import tenant_context
from projects.models.project import ProjectStatus
from projects.models.time_entry import TimeEntryStatus
from projects.selectors import get_project_profitability
from projects.services.billing import get_unbilled_time, invoice_project_time
from projects.services.projects import complete_project
from projects.services.time_entries import approve_time_entry, submit_time_entry
from projects.tests.base import DAY, DUE_DATE, ProjectsTestsBase
from purchases.services.expenses import create_expense, post_expense
from sales.models.invoice import InvoiceStatus
from sales.selectors import get_invoice_amount_due
from sales.services.invoices import post_invoice
from sales.services.payments import record_payment


class ProjectWorkflowE2ETests(ProjectsTestsBase):
    def test_full_time_to_cash_cycle(self):
        with tenant_context(organization_id=self.org_a.id):
            # 1. Time is logged. Nothing financial has happened.
            before_journals = JournalEntry.objects.count()
            entries = [
                self._log(user=self.staff_user, hours=Decimal("8"), entry_date=DAY),
                self._log(user=self.staff_user, hours=Decimal("6"),
                          entry_date=DAY + datetime.timedelta(days=1)),
                self._log(user=self.staff_user, task=self.admin_task, hours=Decimal("2"),
                          entry_date=DAY),
            ]
            self.assertEqual(JournalEntry.objects.count(), before_journals)

            # 2. Submitted and approved by someone other than the author.
            for entry in entries:
                submit_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.staff_user)
                approve_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.manager_user)

            # Only the billable hours are invoiceable; the admin time is not.
            self.assertEqual(get_unbilled_time(project=self.project).count(), 2)

            # 3. A project expense, posted through purchases.
            expense = create_expense(
                organization=self.org_a, expense_date=DAY, amount=Decimal("300.00"),
                expense_account=self.expense_account, paid_through_account=self.bank_account,
                currency=self.currency, project=self.project, description="Stock photography",
            )
            post_expense(expense_id=expense.id, organization=self.org_a)

            # 4. Bill the approved time. 14 billable hours at 150 = 2100.
            invoice = invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
                tax_rate=Decimal("18"), tax_payable_account=self.tax_account,
            )
            self.assertEqual(invoice.status, InvoiceStatus.DRAFT)
            self.assertEqual(invoice.subtotal, Decimal("2100.00"))
            self.assertEqual(invoice.total, Decimal("2478.00"))

            for entry in entries[:2]:
                entry.refresh_from_db()
                self.assertEqual(entry.status, TimeEntryStatus.INVOICED)
            entries[2].refresh_from_db()
            self.assertEqual(entries[2].status, TimeEntryStatus.APPROVED, "non-billable time is never billed")

            # 5. The invoice posts through sales — projects never posts.
            posted = post_invoice(invoice_id=invoice.id, organization=self.org_a, actor=self.user_a)
            self.assertEqual(posted.status, InvoiceStatus.SENT)
            journal = posted.accounting_journal
            debits = sum((jl.debit for jl in journal.lines.all()), Decimal("0"))
            credits = sum((jl.credit for jl in journal.lines.all()), Decimal("0"))
            self.assertEqual(debits, credits)

            # 6. The customer pays.
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=DUE_DATE,
                amount=Decimal("2478.00"), destination_account=self.bank_account,
                allocations=[{"invoice": posted, "amount": Decimal("2478.00")}],
            )
            posted.refresh_from_db()
            self.assertEqual(posted.status, InvoiceStatus.PAID)
            self.assertEqual(get_invoice_amount_due(invoice=posted), Decimal("0.00"))

            # 7. Profitability reconciles with the documents.
            report = get_project_profitability(project=self.project)
            self.assertEqual(report["total_hours"], Decimal("16.00"))
            self.assertEqual(report["billable_hours"], Decimal("14.00"))
            self.assertEqual(report["invoiced_hours"], Decimal("14.00"))
            # Revenue excludes tax — the 2100 net, not the 2478 collected.
            self.assertEqual(report["revenue"], Decimal("2100.00"))
            self.assertEqual(report["labour_cost"], Decimal("960.00"))   # 16h * 60
            self.assertEqual(report["expense_cost"], Decimal("300.00"))
            self.assertEqual(report["margin"], Decimal("840.00"))
            self.assertEqual(report["unbilled_value"], Decimal("0"))

            # 8. The ledger balances, and the project can be closed.
            trial_balance = get_trial_balance(organization=self.org_a, as_of_date=DUE_DATE)
            self.assertTrue(trial_balance["is_balanced"])

            completed = complete_project(project_id=self.project.id, organization=self.org_a)
            self.assertEqual(completed.status, ProjectStatus.COMPLETED)

    def test_unbilled_approved_time_survives_project_completion(self):
        """Quietly writing off billable work at completion is how revenue
        goes missing — it stays visible instead."""
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("5"))
            complete_project(project_id=self.project.id, organization=self.org_a)

            report = get_project_profitability(project=self.project)
            self.assertEqual(report["unbilled_value"], Decimal("750.00"))
            self.assertEqual(get_unbilled_time(project=self.project).count(), 1)

    def test_a_second_billing_run_picks_up_only_newly_approved_time(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("4"), entry_date=DAY)
            first = invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            self.assertEqual(first.total, Decimal("600.00"))

            self._approved(hours=Decimal("3"), entry_date=DAY + datetime.timedelta(days=7))
            second = invoice_project_time(
                organization=self.org_a, project=self.project,
                invoice_date=DAY + datetime.timedelta(days=7),
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            self.assertEqual(second.total, Decimal("450.00"))
            self.assertNotEqual(first.id, second.id)

            report = get_project_profitability(project=self.project)
            self.assertEqual(report["revenue"], Decimal("1050.00"))
            self.assertEqual(report["invoiced_hours"], Decimal("7.00"))
