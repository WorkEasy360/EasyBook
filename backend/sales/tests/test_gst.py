"""GST on the sell side, end to end: determination -> split -> journal.

Builds on `InvoiceTestsBase` rather than a new fixture so that the ONLY
difference from the pre-Phase-7 tests is the GST configuration. Whatever these
tests prove about component posting, they prove against the same chart of
accounts and the same items the legacy tests use - which is what makes the
comparison with `InvoiceJournalTests` meaningful.
"""

import datetime
from decimal import Decimal

from accounting.models.account import AccountType
from accounting.models.journal import JournalLine
from accounting.services.accounts import create_account
from core.tenancy import tenant_context
from sales.models.invoice import InvoiceStatus
from sales.services.credit_notes import create_credit_note, issue_credit_note
from sales.services.customers import create_customer, update_customer
from sales.services.invoices import create_invoice, post_invoice
from sales.tests.test_invoices import InvoiceTestsBase
from tax.enums import (
    SupplyNature,
    SupplyType,
    TaxComponent,
    TaxDirection,
    TaxTreatment,
    WithholdingAppliesTo,
    WithholdingKind,
)
from tax.services.profile import set_tax_account_mapping, set_tax_profile
from tax.services.withholding import create_withholding_section
from tax.testing import ensure_state_codes

INVOICE_DATE = datetime.date(2026, 4, 10)
DUE_DATE = datetime.date(2026, 5, 10)
MAHARASHTRA = "27"
KARNATAKA = "29"


class SalesGstTestsBase(InvoiceTestsBase):
    def setUp(self):
        super().setUp()
        ensure_state_codes()
        with tenant_context(organization_id=self.org_a.id):
            self.output_cgst = create_account(
                organization=self.org_a, code="2110", name="Output CGST",
                account_type=AccountType.LIABILITY,
            )
            self.output_sgst = create_account(
                organization=self.org_a, code="2111", name="Output SGST",
                account_type=AccountType.LIABILITY,
            )
            self.output_igst = create_account(
                organization=self.org_a, code="2112", name="Output IGST",
                account_type=AccountType.LIABILITY,
            )
            self.output_cess = create_account(
                organization=self.org_a, code="2113", name="Output Cess",
                account_type=AccountType.LIABILITY,
            )
            # The supplier sits in Maharashtra.
            set_tax_profile(
                organization=self.org_a, state_code=MAHARASHTRA, gstin="27AAPFU0939F1ZV"
            )

    def map_components(self):
        with tenant_context(organization_id=self.org_a.id):
            for component, account in (
                (TaxComponent.CGST, self.output_cgst),
                (TaxComponent.SGST_UTGST, self.output_sgst),
                (TaxComponent.IGST, self.output_igst),
                (TaxComponent.CESS, self.output_cess),
            ):
                set_tax_account_mapping(
                    organization=self.org_a, component=component,
                    direction=TaxDirection.OUTPUT, account=account,
                )

    def make_customer(self, code, state, treatment=TaxTreatment.REGISTERED, gstin=""):
        with tenant_context(organization_id=self.org_a.id):
            return create_customer(
                organization=self.org_a, customer_code=code, display_name=code,
                currency=self.currency, tax_treatment=treatment, gstin=gstin,
                place_of_supply_state_code=state,
            )

    def make_invoice(self, customer, post=True, **kwargs):
        with tenant_context(organization_id=self.org_a.id):
            kwargs.setdefault("lines", self._service_lines(tax_rate=Decimal("18")))
            invoice = create_invoice(
                organization=self.org_a, customer=customer, invoice_date=INVOICE_DATE,
                due_date=DUE_DATE, receivable_account=self.ar_account,
                tax_payable_account=self.tax_account, **kwargs,
            )
            if post:
                invoice = post_invoice(
                    invoice_id=invoice.id, organization=self.org_a, actor=self.user_a
                )
            return invoice

    def journal_movements(self, invoice, organization=None):
        """{account_id: net debit - credit} for the document's journal.

        Runs inside a tenant context deliberately: `JournalLine.objects` is a
        TenantManager and returns nothing at all without one (core/CLAUDE.md
        fail-closed rule), so a helper that queried outside would silently
        assert against an empty ledger.
        """
        organization = organization or self.org_a
        movements = {}
        with tenant_context(organization_id=organization.id):
            for line in JournalLine.objects.filter(journal_entry=invoice.accounting_journal):
                movements[line.account_id] = (
                    movements.get(line.account_id, Decimal("0")) + line.debit - line.credit
                )
        return movements

    def assert_balanced(self, invoice, organization=None):
        organization = organization or self.org_a
        with tenant_context(organization_id=organization.id):
            lines = list(
                JournalLine.objects.filter(journal_entry=invoice.accounting_journal)
            )
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )


class SupplyDeterminationOnDocumentsTests(SalesGstTestsBase):
    def test_same_state_customer_gets_cgst_and_sgst(self):
        customer = self.make_customer("MH-1", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer, post=False)

        self.assertEqual(invoice.supply_nature, SupplyNature.INTRA_STATE)
        self.assertEqual(invoice.place_of_supply_id, MAHARASHTRA)
        self.assertEqual(invoice.tax_total, Decimal("36.00"))
        self.assertEqual(invoice.cgst_total, Decimal("18.00"))
        self.assertEqual(invoice.sgst_total, Decimal("18.00"))
        self.assertEqual(invoice.igst_total, Decimal("0"))

    def test_other_state_customer_gets_igst(self):
        customer = self.make_customer("KA-1", KARNATAKA, gstin="29AAGCB7383J1Z4")
        invoice = self.make_invoice(customer, post=False)

        self.assertEqual(invoice.supply_nature, SupplyNature.INTER_STATE)
        self.assertEqual(invoice.igst_total, Decimal("36.00"))
        self.assertEqual(invoice.cgst_total, Decimal("0"))
        self.assertEqual(invoice.sgst_total, Decimal("0"))

    def test_sez_customer_in_the_same_state_still_gets_igst(self):
        # The s.8(1) proviso, carried all the way through to a document: a bare
        # state comparison would have charged CGST+SGST here.
        customer = self.make_customer("SEZ-1", MAHARASHTRA, treatment=TaxTreatment.SEZ)
        invoice = self.make_invoice(customer, post=False)

        self.assertEqual(invoice.supply_nature, SupplyNature.SEZ_WITH_TAX)
        self.assertEqual(invoice.supply_type, SupplyType.SEZWP)
        self.assertEqual(invoice.igst_total, Decimal("36.00"))
        self.assertEqual(invoice.cgst_total, Decimal("0"))

    def test_overseas_customer_is_an_export(self):
        customer = self.make_customer("EXP-1", None, treatment=TaxTreatment.OVERSEAS)
        invoice = self.make_invoice(customer, post=False)

        self.assertEqual(invoice.supply_nature, SupplyNature.EXPORT_WITH_TAX)
        self.assertEqual(invoice.supply_type, SupplyType.EXPWP)
        self.assertEqual(invoice.igst_total, Decimal("36.00"))

    def test_document_place_of_supply_overrides_the_customer_default(self):
        # The document's field is what determination reads; the customer only
        # supplies the default (IGST Act ss.10-13 are not inferred).
        customer = self.make_customer("MH-2", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer, post=False, place_of_supply=KARNATAKA)

        self.assertEqual(invoice.place_of_supply_id, KARNATAKA)
        self.assertEqual(invoice.supply_nature, SupplyNature.INTER_STATE)
        self.assertEqual(invoice.igst_total, Decimal("36.00"))

    def test_line_components_sum_to_the_line_tax(self):
        customer = self.make_customer("MH-3", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer, post=False)
        for line in invoice.lines.all():
            self.assertEqual(
                line.cgst_amount + line.sgst_amount + line.igst_amount + line.cess_amount,
                line.tax_amount,
            )


class GstJournalTests(SalesGstTestsBase):
    def test_mapped_components_post_to_their_own_accounts(self):
        self.map_components()
        customer = self.make_customer("MH-4", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer)

        movements = self.journal_movements(invoice)
        self.assertEqual(movements[self.output_cgst.id], Decimal("-18.00"))
        self.assertEqual(movements[self.output_sgst.id], Decimal("-18.00"))
        # The old catch-all account is untouched once components are mapped.
        self.assertNotIn(self.tax_account.id, movements)
        self.assert_balanced(invoice)

    def test_inter_state_posts_only_igst(self):
        self.map_components()
        customer = self.make_customer("KA-2", KARNATAKA, gstin="29AAGCB7383J1Z4")
        invoice = self.make_invoice(customer)

        movements = self.journal_movements(invoice)
        self.assertEqual(movements[self.output_igst.id], Decimal("-36.00"))
        self.assertNotIn(self.output_cgst.id, movements)
        self.assert_balanced(invoice)

    def test_unmapped_organization_still_posts_the_single_legacy_line(self):
        # THE backward-compatibility assertion. No mappings configured, so the
        # whole tax goes to the document's own account exactly as it did before
        # this phase - which is why 215 pre-existing sales tests needed no edit.
        customer = self.make_customer("MH-5", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer)

        movements = self.journal_movements(invoice)
        self.assertEqual(movements[self.tax_account.id], Decimal("-36.00"))
        self.assertNotIn(self.output_cgst.id, movements)
        self.assert_balanced(invoice)

    def test_partial_mapping_falls_back_rather_than_splitting_the_ledger(self):
        # Posting some components to their own accounts and the rest to a
        # catch-all would reconcile to neither. All-or-nothing is the only
        # internally consistent choice.
        with tenant_context(organization_id=self.org_a.id):
            set_tax_account_mapping(
                organization=self.org_a, component=TaxComponent.CGST,
                direction=TaxDirection.OUTPUT, account=self.output_cgst,
            )
        customer = self.make_customer("MH-6", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer)

        movements = self.journal_movements(invoice)
        self.assertEqual(movements[self.tax_account.id], Decimal("-36.00"))
        self.assertNotIn(self.output_cgst.id, movements)
        self.assert_balanced(invoice)

    def test_zero_rated_export_posts_no_tax_line_at_all(self):
        self.map_components()
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="EXP-2", display_name="Overseas",
                currency=self.currency, tax_treatment=TaxTreatment.OVERSEAS,
            )
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=customer, invoice_date=INVOICE_DATE,
                due_date=DUE_DATE, receivable_account=self.ar_account,
                tax_payable_account=self.tax_account,
                lines=self._service_lines(tax_rate=Decimal("18")),
                # Under LUT/bond: zero-rated.
                tax_treatment={
                    "place_of_supply": None,
                    "supply_nature": SupplyNature.EXPORT_WITHOUT_TAX,
                    "supply_type": SupplyType.EXPWOP,
                },
            )
            invoice = post_invoice(invoice_id=invoice.id, organization=self.org_a)

        self.assertEqual(invoice.tax_total, Decimal("0"))
        movements = self.journal_movements(invoice)
        self.assertNotIn(self.output_igst.id, movements)
        self.assertNotIn(self.tax_account.id, movements)
        self.assert_balanced(invoice)

    def test_cess_posts_to_its_own_account(self):
        self.map_components()
        customer = self.make_customer("KA-3", KARNATAKA, gstin="29AAGCB7383J1Z4")
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=customer, invoice_date=INVOICE_DATE,
                due_date=DUE_DATE, receivable_account=self.ar_account,
                tax_payable_account=self.tax_account,
                lines=self._service_lines(
                    tax_rate=Decimal("18"), cess_rate=Decimal("12")
                ),
            )
            invoice = post_invoice(invoice_id=invoice.id, organization=self.org_a)

        # 200 base -> 36 IGST + 24 cess. The line total must carry both, or the
        # customer is billed less than what posts to the tax accounts.
        self.assertEqual(invoice.tax_total, Decimal("60.00"))
        self.assertEqual(invoice.total, Decimal("260.00"))
        movements = self.journal_movements(invoice)
        self.assertEqual(movements[self.output_igst.id], Decimal("-36.00"))
        self.assertEqual(movements[self.output_cess.id], Decimal("-24.00"))
        self.assert_balanced(invoice)

    def test_credit_note_reverses_the_same_component_accounts(self):
        self.map_components()
        customer = self.make_customer("MH-7", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer)

        with tenant_context(organization_id=self.org_a.id):
            credit_account = create_account(
                organization=self.org_a, code="2200", name="Customer Advances",
                account_type=AccountType.LIABILITY,
            )
            note = create_credit_note(
                organization=self.org_a, customer=customer,
                credit_note_date=INVOICE_DATE, source_invoice=invoice,
                receivable_account=self.ar_account, unapplied_credit_account=credit_account,
                lines=self._service_lines(tax_rate=Decimal("18")),
            )
            note = issue_credit_note(credit_note_id=note.id, organization=self.org_a)

        # Inherited from the invoice, not re-determined.
        self.assertEqual(note.supply_nature, SupplyNature.INTRA_STATE)
        self.assertEqual(note.cgst_total, Decimal("18.00"))

        movements = self.journal_movements(note)
        # Debits here - the mirror of the invoice's credits.
        self.assertEqual(movements[self.output_cgst.id], Decimal("18.00"))
        self.assertEqual(movements[self.output_sgst.id], Decimal("18.00"))


class TcsTests(SalesGstTestsBase):
    def test_tcs_is_added_to_the_receivable_and_held_as_a_liability(self):
        self.map_components()
        with tenant_context(organization_id=self.org_a.id):
            tcs_account = create_account(
                organization=self.org_a, code="2160", name="TCS Payable",
                account_type=AccountType.LIABILITY,
            )
            section = create_withholding_section(
                organization=self.org_a, code="206C(1H)", kind=WithholdingKind.TCS,
                applies_to=WithholdingAppliesTo.SALES, rate=Decimal("0.10"),
                account=tcs_account,
            )
        customer = self.make_customer("MH-8", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        invoice = self.make_invoice(customer, withholding_section=section)

        # 200 taxable -> 0.10% = 0.20 collected on top.
        self.assertEqual(invoice.withholding_amount, Decimal("0.20"))
        movements = self.journal_movements(invoice)
        # The customer owes the invoice total PLUS the TCS.
        self.assertEqual(movements[self.ar_account.id], invoice.total + Decimal("0.20"))
        self.assertEqual(movements[tcs_account.id], Decimal("-0.20"))
        self.assert_balanced(invoice)


class UnconfiguredOrganizationTests(SalesGstTestsBase):
    def test_an_organization_with_no_tax_profile_is_untouched_by_gst(self):
        # org_b has no TaxProfile at all. Nothing about GST may be imposed on
        # it: no determination, no components, no new required configuration.
        with tenant_context(organization_id=self.org_b.id):
            from accounting.services.accounts import create_account as mk

            ar_b = mk(
                organization=self.org_b, code="1100", name="AR", account_type=AccountType.ASSET
            )
            revenue_b = mk(
                organization=self.org_b, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            tax_b = mk(
                organization=self.org_b, code="2100", name="Tax",
                account_type=AccountType.LIABILITY,
            )
            from items.services.items import update_item

            update_item(item=self.item_b, sales_account=revenue_b)
            invoice = create_invoice(
                organization=self.org_b, customer=self.customer_b,
                invoice_date=INVOICE_DATE, due_date=DUE_DATE,
                receivable_account=ar_b, tax_payable_account=tax_b,
                lines=[{
                    "item": self.item_b, "quantity": Decimal("2"),
                    "unit_price": Decimal("100.00"), "tax_rate": Decimal("18"),
                }],
            )

        self.assertEqual(invoice.supply_nature, SupplyNature.UNSPECIFIED)
        self.assertEqual(invoice.supply_type, SupplyType.UNSPECIFIED)
        self.assertIsNone(invoice.place_of_supply_id)
        self.assertEqual(invoice.tax_total, Decimal("36.00"))
        self.assertEqual(invoice.cgst_total, Decimal("0"))
        self.assertEqual(invoice.status, InvoiceStatus.DRAFT)

    def test_a_registered_customer_without_a_gstin_is_refused(self):
        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="NOGST", display_name="No GSTIN",
                currency=self.currency,
            )
            from core.exceptions import ApplicationError

            with self.assertRaises(ApplicationError) as ctx:
                update_customer(customer=customer, tax_treatment=TaxTreatment.REGISTERED)
            self.assertEqual(ctx.exception.get_codes(), "gstin_required_for_registered")
