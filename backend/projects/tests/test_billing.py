from decimal import Decimal

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from projects.models.project import BillingMethod
from projects.models.time_entry import TimeEntryStatus
from projects.services.billing import get_unbilled_time, invoice_project_time, release_invoiced_time
from projects.services.projects import activate_project, create_project, create_task, update_project
from projects.tests.base import DAY, DUE_DATE, ProjectsTestsBase
from sales.models.invoice import InvoiceStatus
from sales.services.invoices import post_invoice, void_invoice


class InvoiceProjectTimeTests(ProjectsTestsBase):
    def _invoice(self, **kwargs):
        defaults = {
            "organization": self.org_a, "project": self.project, "invoice_date": DAY,
            "due_date": DUE_DATE, "receivable_account": self.ar_account,
        }
        return invoice_project_time(**{**defaults, **kwargs})

    def test_only_approved_billable_time_is_billed(self):
        with tenant_context(organization_id=self.org_a.id):
            approved = self._approved(hours=Decimal("5"))          # billable, approved
            self._log(hours=Decimal("3"))                           # billable, still draft
            self._approved(task=self.admin_task, hours=Decimal("2"))  # approved, non-billable

            unbilled = list(get_unbilled_time(project=self.project))
            self.assertEqual([e.id for e in unbilled], [approved.id])

            invoice = self._invoice()
            self.assertEqual(invoice.status, InvoiceStatus.DRAFT)
            self.assertEqual(invoice.total, Decimal("750.00"))  # 5h @ 150

    def test_generated_invoice_is_a_draft_and_posts_no_journal_itself(self):
        with tenant_context(organization_id=self.org_a.id):
            before = JournalEntry.objects.count()
            invoice = self._invoice_with_time()
            self.assertEqual(invoice.status, InvoiceStatus.DRAFT)
            self.assertEqual(invoice.invoice_number, "")
            self.assertIsNone(invoice.accounting_journal)
            self.assertEqual(JournalEntry.objects.count(), before)

            # Posting goes through sales, as for any other invoice.
            posted = post_invoice(invoice_id=invoice.id, organization=self.org_a, actor=self.user_a)
            self.assertEqual(posted.status, InvoiceStatus.SENT)
            self.assertEqual(JournalEntry.objects.count(), before + 1)

    def _invoice_with_time(self, hours=Decimal("5")):
        self._approved(hours=hours)
        return self._invoice()

    def test_entries_are_marked_invoiced_and_linked_to_their_line(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._approved(hours=Decimal("5"))
            invoice = self._invoice()
            entry.refresh_from_db()
            self.assertEqual(entry.status, TimeEntryStatus.INVOICED)
            self.assertIsNotNone(entry.invoice_line_id)
            self.assertEqual(entry.invoice_line.invoice_id, invoice.id)

    def test_the_same_hours_can_never_be_billed_twice(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("5"))
            self._invoice()
            with self.assertRaises(ApplicationError) as ctx:
                self._invoice()
        self.assertEqual(ctx.exception.detail.code, "no_billable_time")

    def test_lines_group_by_task_and_rate(self):
        """Grouping by task alone would merge hours billed at different rates
        into one line at whichever rate came last."""
        with tenant_context(organization_id=self.org_a.id):
            # Same task, two different people, two different rates.
            self._approved(user=self.staff_user, hours=Decimal("4"))   # 150
            self._approved(user=self.user_a, hours=Decimal("2"))       # 100 (project default)

            invoice = self._invoice()
            lines = list(invoice.lines.order_by("unit_price"))
            self.assertEqual(len(lines), 2)
            self.assertEqual(
                {(line.unit_price, line.quantity) for line in lines},
                {(Decimal("100.00"), Decimal("2.0000")), (Decimal("150.00"), Decimal("4.0000"))},
            )
            self.assertEqual(invoice.total, Decimal("800.00"))

    def test_hours_on_the_same_task_and_rate_collapse_into_one_line(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(user=self.staff_user, hours=Decimal("4"))
            self._approved(user=self.staff_user, hours=Decimal("3"))
            invoice = self._invoice()
            line = invoice.lines.get()
            self.assertEqual(line.quantity, Decimal("7.0000"))
            self.assertEqual(line.unit_price, Decimal("150.00"))

    def test_up_to_date_limits_what_is_billed(self):
        import datetime

        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("4"), entry_date=DAY)
            self._approved(hours=Decimal("3"), entry_date=DAY + datetime.timedelta(days=5))
            invoice = self._invoice(up_to_date=DAY)
            self.assertEqual(invoice.lines.get().quantity, Decimal("4.0000"))
            # The later entry is still available to bill.
            self.assertEqual(get_unbilled_time(project=self.project).count(), 1)

    def test_tax_is_applied_through_the_sales_engine(self):
        with tenant_context(organization_id=self.org_a.id):
            self._approved(hours=Decimal("10"))  # 10 * 150 = 1500
            invoice = self._invoice(
                tax_rate=Decimal("18"), tax_payable_account=self.tax_account
            )
        self.assertEqual(invoice.subtotal, Decimal("1500.00"))
        self.assertEqual(invoice.tax_total, Decimal("270.00"))
        self.assertEqual(invoice.total, Decimal("1770.00"))

    def test_non_billable_project_refuses_to_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-NB",
                name="Internal", billing_method=BillingMethod.NON_BILLABLE, currency=self.currency,
            )
            project = activate_project(project_id=project.id, organization=self.org_a)
            with self.assertRaises(ApplicationError) as ctx:
                self._invoice(project=project)
        self.assertEqual(ctx.exception.detail.code, "project_not_billable")

    def test_fixed_fee_project_refuses_to_invoice_its_hours(self):
        """Billing the hours on top of the agreed fee would charge the
        customer twice for the same work."""
        with tenant_context(organization_id=self.org_a.id):
            project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-FF",
                name="Fixed", billing_method=BillingMethod.FIXED_FEE,
                fixed_fee_amount=Decimal("5000"), currency=self.currency,
            )
            project = activate_project(project_id=project.id, organization=self.org_a)
            with self.assertRaises(ApplicationError) as ctx:
                self._invoice(project=project)
        self.assertEqual(ctx.exception.detail.code, "project_fixed_fee")

    def test_missing_service_item_is_an_explicit_error(self):
        with tenant_context(organization_id=self.org_a.id):
            update_project(project=self.project, service_item=None)
            self.project.refresh_from_db()
            self._approved(hours=Decimal("2"))
            with self.assertRaises(ApplicationError) as ctx:
                self._invoice()
        self.assertEqual(ctx.exception.detail.code, "service_item_required")

    def test_missing_billable_rate_is_an_explicit_error_not_a_zero_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            project = create_project(
                organization=self.org_a, customer=self.customer, project_code="PRJ-NR",
                name="No rates", billing_method=BillingMethod.HOURLY,
                service_item=self.service_item, currency=self.currency,
            )
            project = activate_project(project_id=project.id, organization=self.org_a)
            task = create_task(project=project, name="Work")

            from projects.services.time_entries import approve_time_entry, log_time, submit_time_entry

            entry = log_time(
                organization=self.org_a, project=project, task=task, user=self.staff_user,
                entry_date=DAY, hours=Decimal("3"),
            )
            self.assertIsNone(entry.billable_rate)
            submit_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.staff_user)
            approve_time_entry(entry_id=entry.id, organization=self.org_a, actor=self.manager_user)

            with self.assertRaises(ApplicationError) as ctx:
                self._invoice(project=project)
        self.assertEqual(ctx.exception.detail.code, "billable_rate_missing")

    def test_cross_org_project_rejected(self):
        with tenant_context(organization_id=self.org_b.id):
            with self.assertRaises(ApplicationError) as ctx:
                invoice_project_time(
                    organization=self.org_b, project=self.project, invoice_date=DAY,
                    due_date=DUE_DATE, receivable_account=self.ar_account,
                )
        self.assertEqual(ctx.exception.detail.code, "project_cross_org")


class ReleaseInvoicedTimeTests(ProjectsTestsBase):
    def test_voiding_an_invoice_releases_the_time_to_be_rebilled(self):
        with tenant_context(organization_id=self.org_a.id):
            entry = self._approved(hours=Decimal("5"))
            invoice = invoice_project_time(
                organization=self.org_a, project=self.project, invoice_date=DAY,
                due_date=DUE_DATE, receivable_account=self.ar_account,
            )
            post_invoice(invoice_id=invoice.id, organization=self.org_a, actor=self.user_a)
            void_invoice(invoice_id=invoice.id, organization=self.org_a, actor=self.user_a)
            invoice.refresh_from_db()

            released = release_invoiced_time(
                organization=self.org_a, invoice=invoice, actor=self.user_a
            )
            self.assertEqual(released, 1)

            entry.refresh_from_db()
            self.assertEqual(entry.status, TimeEntryStatus.APPROVED)
            self.assertIsNone(entry.invoice_line_id)
            # And it can be billed again.
            self.assertEqual(get_unbilled_time(project=self.project).count(), 1)
