"""Row Level Security for the compliance app.

Walks every `TenantScopedModel` in the app and asserts the Postgres policy is
really on its table, so a model added later without an entry in
`migrations/0002_enable_rls.py` fails here rather than shipping unprotected.

The stakes are specific: an IRN and an e-way bill number are government-issued
identifiers tied to one taxpayer's filings, and the payload beside them carries
the customer list, the amounts and the GSTINs. This is among the most sensitive
data in the system.
"""


from django.apps import apps
from django.db import connection
from django.test import TestCase

from compliance.models import EInvoiceDocument
from compliance.selectors import get_gstr1_summary, get_output_tax_register
from compliance.services.einvoice import record_irn
from compliance.tests.base import PERIOD_END, PERIOD_START, ComplianceTestsBase
from core.models import TenantScopedModel
from core.tenancy import clear_tenant_context, tenant_context


def _compliance_tenant_models():
    return [
        model
        for model in apps.get_app_config("compliance").get_models()
        if issubclass(model, TenantScopedModel)
    ]


class ComplianceRlsCoverageTests(TestCase):
    def test_every_compliance_model_is_tenant_scoped(self):
        tenant_scoped = {model.__name__ for model in _compliance_tenant_models()}
        everything = {model.__name__ for model in apps.get_app_config("compliance").get_models()}
        self.assertEqual(everything, tenant_scoped)

    def test_every_compliance_table_has_rls_enabled_and_forced(self):
        tables = [model._meta.db_table for model in _compliance_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(%s)",
                [tables],
            )
            rows = {name: (enabled, forced) for name, enabled, forced in cursor.fetchall()}

        self.assertEqual(set(rows) ^ set(tables), set())
        unprotected = [name for name, (enabled, forced) in rows.items() if not (enabled and forced)]
        self.assertEqual(unprotected, [])

    def test_every_compliance_table_has_a_policy(self):
        tables = [model._meta.db_table for model in _compliance_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(set(tables) - with_policy, set())


class ComplianceFailClosedTests(ComplianceTestsBase):
    def test_no_tenant_context_yields_nothing(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record_irn(document=invoice, irn="a" * 64)

        clear_tenant_context()
        # The unscoped manager skips the application filter but not the DB
        # policy, so this second assertion is what proves the database is
        # doing the work.
        self.assertEqual(EInvoiceDocument.objects.count(), 0)
        self.assertEqual(EInvoiceDocument.all_objects.count(), 0)

    def test_one_organization_cannot_read_anothers_filings(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            record_irn(document=invoice, irn="a" * 64)

        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(EInvoiceDocument.objects.count(), 0)
            self.assertEqual(EInvoiceDocument.all_objects.count(), 0)

    def test_a_return_summary_cannot_reach_across_organizations(self):
        # The register reads sales and purchases, so it inherits their RLS -
        # but a selector that filtered only in Python would leak. Assert the
        # figures, not just the row count.
        self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_b.id):
            rows = get_output_tax_register(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )
            summary = get_gstr1_summary(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )
        self.assertEqual(rows, [])
        self.assertEqual(summary["b2b"], {})
        self.assertEqual(summary["hsn_summary"]["b2b"], {})
