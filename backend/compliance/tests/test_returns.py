"""Registers and return summaries, derived from genuinely posted documents.

Every figure here comes out of the real posting services - no fixture rows are
written straight into the tables - so these tests assert what an organization's
return would actually say, not what a hand-built row says.
"""

import datetime
from decimal import Decimal

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from compliance.selectors import (
    get_gstr1_summary,
    get_gstr3b_summary,
    get_input_tax_register,
    get_output_tax_register,
)
from compliance.tests.base import PERIOD_END, PERIOD_START, ComplianceTestsBase
from core.tenancy import tenant_context
from sales.services.credit_notes import create_credit_note, issue_credit_note
from sales.services.invoices import void_invoice

ZERO = Decimal("0")


class OutputRegisterTests(ComplianceTestsBase):
    def _register(self):
        with tenant_context(organization_id=self.org_a.id):
            return get_output_tax_register(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )

    def test_posted_invoices_appear_with_their_component_split(self):
        self.make_invoice(self.customer_mh)
        self.make_invoice(self.customer_ka)

        rows = self._register()
        self.assertEqual(len(rows), 2)
        intra = next(row for row in rows if row["cgst"] > 0)
        inter = next(row for row in rows if row["igst"] > 0)
        self.assertEqual(intra["cgst"], Decimal("90.00"))
        self.assertEqual(intra["sgst"], Decimal("90.00"))
        self.assertEqual(inter["igst"], Decimal("180.00"))

    def test_draft_invoices_are_not_in_the_register(self):
        # A draft is not a supply. Including it would report tax on a document
        # that has not been issued to anyone.
        self.make_invoice(self.customer_mh, post=False)
        self.assertEqual(self._register(), [])

    def test_voided_invoices_drop_out(self):
        invoice = self.make_invoice(self.customer_mh)
        self.assertEqual(len(self._register()), 1)
        with tenant_context(organization_id=self.org_a.id):
            void_invoice(invoice_id=invoice.id, organization=self.org_a, reason="error")
        self.assertEqual(self._register(), [])

    def test_credit_notes_appear_as_negatives_in_the_same_list(self):
        # A register exists to total to the period's liability; a reader who
        # must remember to subtract a second list will eventually forget.
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            unapplied = create_account(
                organization=self.org_a, code="2200", name="Customer Advances",
                account_type=AccountType.LIABILITY,
            )
            note = create_credit_note(
                organization=self.org_a, customer=self.customer_mh,
                credit_note_date=datetime.date(2026, 4, 12), source_invoice=invoice,
                receivable_account=self.ar_account, unapplied_credit_account=unapplied,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"),
                    "unit_price": Decimal("400.00"), "tax_rate": Decimal("18"),
                }],
            )
            issue_credit_note(credit_note_id=note.id, organization=self.org_a)

        rows = self._register()
        self.assertEqual(len(rows), 2)
        credit_row = next(row for row in rows if row["document_type"] == "credit_note")
        self.assertEqual(credit_row["taxable_value"], Decimal("-400.00"))
        self.assertEqual(credit_row["cgst"], Decimal("-36.00"))
        # The period nets correctly without the reader doing anything.
        self.assertEqual(sum(row["cgst"] for row in rows), Decimal("54.00"))

    def test_documents_outside_the_period_are_excluded(self):
        self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            rows = get_output_tax_register(
                organization=self.org_a,
                date_from=datetime.date(2026, 5, 1),
                date_to=datetime.date(2026, 5, 31),
            )
        self.assertEqual(rows, [])


class InputRegisterTests(ComplianceTestsBase):
    def test_posted_bills_appear_with_their_split(self):
        self.make_bill()
        with tenant_context(organization_id=self.org_a.id):
            rows = get_input_tax_register(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cgst"], Decimal("45.00"))
        self.assertEqual(rows[0]["sgst"], Decimal("45.00"))
        self.assertFalse(rows[0]["is_reverse_charge"])

    def test_reverse_charge_is_flagged_rather_than_hidden(self):
        # Those bills create a liability as well as a credit; a register that
        # hid the distinction would overstate the net credit.
        self.make_bill(is_reverse_charge=True)
        with tenant_context(organization_id=self.org_a.id):
            rows = get_input_tax_register(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )
        self.assertTrue(rows[0]["is_reverse_charge"])


class Gstr1Tests(ComplianceTestsBase):
    def _summary(self):
        with tenant_context(organization_id=self.org_a.id):
            return get_gstr1_summary(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )

    def test_b2b_is_keyed_by_the_customers_gstin(self):
        self.make_invoice(self.customer_ka)
        summary = self._summary()
        self.assertIn("29AAGCB7383J1Z4", summary["b2b"])
        self.assertEqual(summary["b2b"]["29AAGCB7383J1Z4"]["igst"], Decimal("180.00"))

    def test_an_unregistered_consumer_files_as_b2c_not_b2b(self):
        self.make_invoice(self.consumer)
        summary = self._summary()
        self.assertEqual(summary["b2b"], {})
        self.assertEqual(summary["b2c_small"]["27"]["cgst"], Decimal("90.00"))

    def test_inter_state_consumer_supply_files_as_b2c_large(self):
        from sales.services.customers import create_customer
        from tax.enums import TaxTreatment

        with tenant_context(organization_id=self.org_a.id):
            consumer_ka = create_customer(
                organization=self.org_a, customer_code="B2C-KA", display_name="KA walk-in",
                currency=self.currency, tax_treatment=TaxTreatment.CONSUMER,
                place_of_supply_state_code="29",
            )
        self.make_invoice(consumer_ka)
        summary = self._summary()
        self.assertEqual(summary["b2c_large"]["igst"], Decimal("180.00"))

    def test_exports_file_separately_from_domestic_supplies(self):
        from sales.services.customers import create_customer
        from tax.enums import TaxTreatment

        with tenant_context(organization_id=self.org_a.id):
            overseas = create_customer(
                organization=self.org_a, customer_code="EXP", display_name="Overseas Ltd",
                currency=self.currency, tax_treatment=TaxTreatment.OVERSEAS,
            )
        self.make_invoice(overseas)
        summary = self._summary()
        self.assertEqual(summary["exports"]["with_payment"]["igst"], Decimal("180.00"))
        self.assertEqual(summary["b2b"], {})

    def test_hsn_summary_is_bifurcated_into_b2b_and_b2c(self):
        # Table 12 has required the split since the 2025 Phase-III change.
        self.make_invoice(self.customer_mh)
        self.make_invoice(self.consumer)
        summary = self._summary()

        self.assertEqual(set(summary["hsn_summary"]), {"b2b", "b2c"})
        self.assertIn("998311|18.00", summary["hsn_summary"]["b2b"])
        self.assertIn("998311|18.00", summary["hsn_summary"]["b2c"])
        self.assertEqual(
            summary["hsn_summary"]["b2b"]["998311|18.00"]["taxable_value"], Decimal("1000.00")
        )

    def test_hsn_summary_keys_on_rate_as_well_as_code(self):
        # The form reports a line per rate within an HSN, not one per HSN.
        self.make_invoice(self.customer_mh, tax_rate=Decimal("18"))
        self.make_invoice(self.customer_mh, tax_rate=Decimal("5"))
        summary = self._summary()
        self.assertEqual(len(summary["hsn_summary"]["b2b"]), 2)

    def test_credit_notes_are_reported_in_their_own_table(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            unapplied = create_account(
                organization=self.org_a, code="2200", name="Customer Advances",
                account_type=AccountType.LIABILITY,
            )
            note = create_credit_note(
                organization=self.org_a, customer=self.customer_mh,
                credit_note_date=datetime.date(2026, 4, 12), source_invoice=invoice,
                receivable_account=self.ar_account, unapplied_credit_account=unapplied,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"),
                    "unit_price": Decimal("400.00"), "tax_rate": Decimal("18"),
                }],
            )
            issue_credit_note(credit_note_id=note.id, organization=self.org_a)

        summary = self._summary()
        self.assertEqual(
            summary["credit_debit_notes"]["registered"]["taxable_value"], Decimal("400.00")
        )


class Gstr3bTests(ComplianceTestsBase):
    def _summary(self):
        with tenant_context(organization_id=self.org_a.id):
            return get_gstr3b_summary(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )

    def test_outward_taxable_and_itc(self):
        self.make_invoice(self.customer_mh)
        self.make_bill()
        summary = self._summary()

        self.assertEqual(summary["outward"]["taxable"]["cgst"], Decimal("90.00"))
        self.assertEqual(summary["itc"]["all_other"]["cgst"], Decimal("45.00"))

    def test_zero_rated_supplies_are_reported_apart_from_taxable_ones(self):
        from sales.services.customers import create_customer
        from tax.enums import TaxTreatment

        with tenant_context(organization_id=self.org_a.id):
            overseas = create_customer(
                organization=self.org_a, customer_code="EXP", display_name="Overseas Ltd",
                currency=self.currency, tax_treatment=TaxTreatment.OVERSEAS,
            )
        self.make_invoice(overseas)
        summary = self._summary()
        self.assertEqual(summary["outward"]["zero_rated"]["igst"], Decimal("180.00"))
        self.assertEqual(summary["outward"]["taxable"]["igst"], ZERO)

    def test_reverse_charge_appears_as_both_a_liability_and_a_credit(self):
        # Counted twice on purpose - that is exactly what reverse charge does.
        self.make_bill(is_reverse_charge=True)
        summary = self._summary()
        self.assertEqual(summary["outward"]["inward_reverse_charge"]["cgst"], Decimal("45.00"))
        self.assertEqual(summary["itc"]["reverse_charge"]["cgst"], Decimal("45.00"))

    def test_credit_notes_reduce_the_outward_figure(self):
        invoice = self.make_invoice(self.customer_mh)
        with tenant_context(organization_id=self.org_a.id):
            unapplied = create_account(
                organization=self.org_a, code="2200", name="Customer Advances",
                account_type=AccountType.LIABILITY,
            )
            note = create_credit_note(
                organization=self.org_a, customer=self.customer_mh,
                credit_note_date=datetime.date(2026, 4, 12), source_invoice=invoice,
                receivable_account=self.ar_account, unapplied_credit_account=unapplied,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"),
                    "unit_price": Decimal("400.00"), "tax_rate": Decimal("18"),
                }],
            )
            issue_credit_note(credit_note_id=note.id, organization=self.org_a)

        summary = self._summary()
        # 1000 taxable less a 400 credit.
        self.assertEqual(summary["outward"]["taxable"]["taxable_value"], Decimal("600.00"))
        self.assertEqual(summary["outward"]["taxable"]["cgst"], Decimal("54.00"))

    def test_inter_state_supplies_to_unregistered_persons_are_broken_out(self):
        from sales.services.customers import create_customer
        from tax.enums import TaxTreatment

        with tenant_context(organization_id=self.org_a.id):
            consumer_ka = create_customer(
                organization=self.org_a, customer_code="B2C-KA", display_name="KA walk-in",
                currency=self.currency, tax_treatment=TaxTreatment.CONSUMER,
                place_of_supply_state_code="29",
            )
        self.make_invoice(consumer_ka)
        summary = self._summary()
        self.assertEqual(summary["inter_state_unregistered"]["29"]["igst"], Decimal("180.00"))

    def test_an_empty_period_totals_to_zero_rather_than_failing(self):
        summary = self._summary()
        self.assertEqual(summary["outward"]["taxable"]["cgst"], ZERO)
        self.assertEqual(summary["itc"]["all_other"]["igst"], ZERO)
