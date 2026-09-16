"""One GST period, end to end.

The hop chain: configure GST -> invoice intra-State, inter-State and B2C ->
bill under reverse charge -> credit-note one invoice -> report to the IRP ->
generate an e-way bill -> read the registers and both returns.

The invariant re-asserted at every hop is the system's central one: the ledger
balances, and the tax accounts agree with the return. A return summary that
does not reconcile to the general ledger is the single most expensive way this
module could be wrong, because both numbers look authoritative and only one of
them is filed.
"""

import datetime
from decimal import Decimal

from accounting.models.account import AccountType
from accounting.selectors import get_trial_balance
from accounting.services.accounts import create_account
from compliance.selectors import (
    get_gstr1_summary,
    get_gstr3b_summary,
    get_input_tax_register,
    get_output_tax_register,
)
from compliance.services.einvoice import record_irn
from compliance.services.ewaybill import record_ewaybill
from compliance.tests.base import PERIOD_END, PERIOD_START, ComplianceTestsBase
from core.tenancy import tenant_context
from sales.services.credit_notes import create_credit_note, issue_credit_note

ZERO = Decimal("0")


class GstPeriodE2ETests(ComplianceTestsBase):
    def _balance_for(self, account):
        """Signed closing balance for one account, debit-positive."""
        with tenant_context(organization_id=self.org_a.id):
            trial_balance = get_trial_balance(organization=self.org_a, as_of_date=PERIOD_END)
        for row in trial_balance["rows"]:
            if row["account"].id == account.id:
                return row["closing_debit"] - row["closing_credit"]
        return ZERO

    def test_a_full_gst_period_reconciles_ledger_to_return(self):
        # --- hop 1: three outward supplies of different shapes -------------
        intra = self.make_invoice(self.customer_mh)          # 1000 @ 18% -> 90 + 90
        inter = self.make_invoice(self.customer_ka)          # 1000 @ 18% -> 180 IGST
        self.make_invoice(self.consumer)                     # 1000 @ 18% -> 90 + 90 (B2C small)

        self.assertEqual(intra.cgst_total, Decimal("90.00"))
        self.assertEqual(inter.igst_total, Decimal("180.00"))

        # --- hop 2: an inward supply under reverse charge ------------------
        bill = self.make_bill(is_reverse_charge=True)        # 500 @ 18% -> 45 + 45

        # --- hop 3: credit part of one invoice -----------------------------
        with tenant_context(organization_id=self.org_a.id):
            unapplied = create_account(
                organization=self.org_a, code="2200", name="Customer Advances",
                account_type=AccountType.LIABILITY,
            )
            note = create_credit_note(
                organization=self.org_a, customer=self.customer_mh,
                credit_note_date=datetime.date(2026, 4, 20), source_invoice=intra,
                receivable_account=self.ar_account, unapplied_credit_account=unapplied,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"),
                    "unit_price": Decimal("200.00"), "tax_rate": Decimal("18"),
                }],
            )
            issue_credit_note(credit_note_id=note.id, organization=self.org_a)

        # --- hop 4: report one invoice and move goods ----------------------
        with tenant_context(organization_id=self.org_a.id):
            filing = record_irn(
                document=inter, irn="a" * 64, ack_no="112010000123", actor=self.user_a
            )
            waybill = record_ewaybill(
                document=inter, ewb_no="123456789012", distance_km=850,
                vehicle_number="MH12AB1234", actor=self.user_a,
            )
        self.assertEqual(filing.status, "generated")
        self.assertEqual(waybill.status, "generated")
        # 850 km -> 5 days.
        self.assertEqual(
            (waybill.valid_until.date() - waybill.ewb_date.date()).days, 5
        )

        # --- hop 5: the registers ------------------------------------------
        with tenant_context(organization_id=self.org_a.id):
            output_rows = get_output_tax_register(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )
            input_rows = get_input_tax_register(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )
        self.assertEqual(len(output_rows), 4)   # three invoices + one credit note
        self.assertEqual(len(input_rows), 1)

        # --- hop 6: the return, reconciled against the general ledger ------
        with tenant_context(organization_id=self.org_a.id):
            gstr1 = get_gstr1_summary(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )
            gstr3b = get_gstr3b_summary(
                organization=self.org_a, date_from=PERIOD_START, date_to=PERIOD_END
            )

        # Output CGST: 90 (intra) + 90 (b2c) less 18 credited = 162, and the
        # GL liability account must say exactly the same thing. This is the
        # assertion the whole module exists to make true.
        expected_output_cgst = Decimal("162.00")
        self.assertEqual(sum(row["cgst"] for row in output_rows), expected_output_cgst)
        self.assertEqual(-self._balance_for(self.out_cgst), expected_output_cgst)
        self.assertEqual(gstr3b["outward"]["taxable"]["cgst"], expected_output_cgst)

        expected_output_igst = Decimal("180.00")
        self.assertEqual(sum(row["igst"] for row in output_rows), expected_output_igst)
        self.assertEqual(-self._balance_for(self.out_igst), expected_output_igst)

        # Input CGST from the reverse-charge bill, likewise.
        expected_input_cgst = Decimal("45.00")
        self.assertEqual(self._balance_for(self.in_cgst), expected_input_cgst)
        self.assertEqual(gstr3b["itc"]["all_other"]["cgst"], expected_input_cgst)

        # Reverse charge is a liability AND a credit - both legs present.
        self.assertEqual(gstr3b["outward"]["inward_reverse_charge"]["cgst"], Decimal("45.00"))
        self.assertEqual(gstr3b["itc"]["reverse_charge"]["cgst"], Decimal("45.00"))
        self.assertEqual(-self._balance_for(self.rcm_payable), bill.tax_total)

        # GSTR-1 splits the three outward shapes into their three tables.
        self.assertIn(self.customer_ka.gstin, gstr1["b2b"])
        self.assertEqual(gstr1["b2c_small"]["27"]["cgst"], Decimal("90.00"))
        self.assertEqual(
            gstr1["credit_debit_notes"]["registered"]["taxable_value"], Decimal("200.00")
        )

        # --- and the ledger balances throughout ----------------------------
        with tenant_context(organization_id=self.org_a.id):
            trial_balance = get_trial_balance(organization=self.org_a, as_of_date=PERIOD_END)
        self.assertTrue(trial_balance["is_balanced"])
        self.assertEqual(
            trial_balance["total_closing_debit"], trial_balance["total_closing_credit"]
        )

    def test_an_unconfigured_organization_files_an_empty_return_rather_than_failing(self):
        # org_b has no TaxProfile. Asking for its return must produce zeroes,
        # not an exception - nothing about GST is imposed on an organization
        # that has not opted in.
        with tenant_context(organization_id=self.org_b.id):
            summary = get_gstr3b_summary(
                organization=self.org_b, date_from=PERIOD_START, date_to=PERIOD_END
            )
        self.assertEqual(summary["outward"]["taxable"]["cgst"], ZERO)
        self.assertEqual(summary["itc"]["all_other"]["cgst"], ZERO)
