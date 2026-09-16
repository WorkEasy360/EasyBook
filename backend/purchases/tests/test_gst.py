"""GST on the buy side: input tax, reverse charge, and TDS.

Builds on `PurchasesTestsBase` so the only difference from the pre-Phase-7
tests is the GST configuration - the same chart of accounts, the same vendor,
the same items.

The buy side has two mechanics the sell side does not:

  - REVERSE CHARGE (IGST Act s.5(3)/(4)) creates BOTH an input credit and an
    output liability from one bill. Getting only half of it would claim a
    credit for tax nobody paid.
  - TDS is withheld FROM the vendor, so it REDUCES what is payable - the
    opposite direction from TCS on the sell side, which increases what is
    receivable.
"""

import datetime
from decimal import Decimal

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from purchases.services.bills import create_bill, post_bill
from purchases.services.expenses import create_expense, post_expense
from purchases.services.vendors import create_vendor
from purchases.tests.base import ORDER_DATE, PurchasesTestsBase
from tax.enums import (
    SupplyNature,
    TaxComponent,
    TaxDirection,
    TaxTreatment,
    WithholdingAppliesTo,
    WithholdingKind,
)
from tax.services.profile import set_tax_account_mapping, set_tax_profile
from tax.services.withholding import create_withholding_section
from tax.testing import ensure_state_codes

DUE_DATE = datetime.date(2026, 5, 10)
MAHARASHTRA = "27"
KARNATAKA = "29"


class PurchasesGstTestsBase(PurchasesTestsBase):
    def setUp(self):
        super().setUp()
        ensure_state_codes()
        with tenant_context(organization_id=self.org_a.id):
            self.in_cgst = create_account(
                organization=self.org_a, code="1320", name="Input CGST",
                account_type=AccountType.ASSET,
            )
            self.in_sgst = create_account(
                organization=self.org_a, code="1321", name="Input SGST",
                account_type=AccountType.ASSET,
            )
            self.in_igst = create_account(
                organization=self.org_a, code="1322", name="Input IGST",
                account_type=AccountType.ASSET,
            )
            self.rcm_payable = create_account(
                organization=self.org_a, code="2170", name="RCM Payable",
                account_type=AccountType.LIABILITY,
            )
            self.tds_payable = create_account(
                organization=self.org_a, code="2180", name="TDS Payable",
                account_type=AccountType.LIABILITY,
            )
            set_tax_profile(
                organization=self.org_a, state_code=MAHARASHTRA, gstin="27AAPFU0939F1ZV"
            )

    def map_input_components(self):
        with tenant_context(organization_id=self.org_a.id):
            for component, account in (
                (TaxComponent.CGST, self.in_cgst),
                (TaxComponent.SGST_UTGST, self.in_sgst),
                (TaxComponent.IGST, self.in_igst),
            ):
                set_tax_account_mapping(
                    organization=self.org_a, component=component,
                    direction=TaxDirection.INPUT, account=account,
                )

    def map_rcm(self):
        with tenant_context(organization_id=self.org_a.id):
            set_tax_account_mapping(
                organization=self.org_a, component=TaxComponent.IGST,
                direction=TaxDirection.RCM_PAYABLE, account=self.rcm_payable,
            )

    def make_vendor(self, code, state, treatment=TaxTreatment.REGISTERED, gstin=""):
        with tenant_context(organization_id=self.org_a.id):
            return create_vendor(
                organization=self.org_a, vendor_code=code, display_name=code,
                currency=self.currency, tax_treatment=treatment, gstin=gstin,
                place_of_supply_state_code=state,
            )

    def make_bill(self, vendor, post=True, **kwargs):
        with tenant_context(organization_id=self.org_a.id):
            kwargs.setdefault("lines", self._service_lines(tax_rate=Decimal("18")))
            bill = create_bill(
                organization=self.org_a, vendor=vendor, bill_date=ORDER_DATE,
                due_date=DUE_DATE, payable_account=self.ap_account,
                tax_recoverable_account=self.input_tax_account, **kwargs,
            )
            if post:
                bill = post_bill(bill_id=bill.id, organization=self.org_a, actor=self.user_a)
            return bill


class InputTaxTests(PurchasesGstTestsBase):
    def test_same_state_vendor_splits_into_cgst_and_sgst(self):
        vendor = self.make_vendor("MH-V", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        bill = self.make_bill(vendor, post=False)

        self.assertEqual(bill.supply_nature, SupplyNature.INTRA_STATE)
        self.assertEqual(bill.cgst_total, Decimal("18.00"))
        self.assertEqual(bill.sgst_total, Decimal("18.00"))

    def test_mapped_components_are_debited_to_their_own_accounts(self):
        # Input tax is an ASSET, so it is DEBITED - the mirror of output tax.
        self.map_input_components()
        vendor = self.make_vendor("MH-V2", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        bill = self.make_bill(vendor)

        with tenant_context(organization_id=self.org_a.id):
            journal = bill.accounting_journal
            self.assertEqual(self.account_movement(journal, self.in_cgst), Decimal("18.00"))
            self.assertEqual(self.account_movement(journal, self.in_sgst), Decimal("18.00"))
            self.assertEqual(
                self.account_movement(journal, self.input_tax_account), Decimal("0")
            )
            self.assert_journal_balanced(journal)

    def test_inter_state_vendor_debits_only_igst(self):
        self.map_input_components()
        vendor = self.make_vendor("KA-V", KARNATAKA, gstin="29AAGCB7383J1Z4")
        bill = self.make_bill(vendor)

        with tenant_context(organization_id=self.org_a.id):
            journal = bill.accounting_journal
            self.assertEqual(self.account_movement(journal, self.in_igst), Decimal("36.00"))
            self.assertEqual(self.account_movement(journal, self.in_cgst), Decimal("0"))
            self.assert_journal_balanced(journal)

    def test_unmapped_organization_still_posts_the_single_legacy_line(self):
        # The backward-compatibility assertion: without mappings the whole tax
        # goes to the bill's own account, exactly as before this phase.
        vendor = self.make_vendor("MH-V3", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        bill = self.make_bill(vendor)

        with tenant_context(organization_id=self.org_a.id):
            journal = bill.accounting_journal
            self.assertEqual(
                self.account_movement(journal, self.input_tax_account), Decimal("36.00")
            )
            self.assertEqual(self.account_movement(journal, self.in_cgst), Decimal("0"))
            self.assert_journal_balanced(journal)

    def test_input_tax_is_never_capitalised_into_the_cost_of_goods(self):
        # The pre-existing invariant, re-asserted under the component split:
        # the expense/inventory debit is the taxable amount, tax sits apart.
        self.map_input_components()
        vendor = self.make_vendor("MH-V4", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        bill = self.make_bill(vendor)

        with tenant_context(organization_id=self.org_a.id):
            journal = bill.accounting_journal
            self.assertEqual(
                self.account_movement(journal, self.purchase_expense_account), Decimal("200.00")
            )


class ReverseChargeTests(PurchasesGstTestsBase):
    def test_reverse_charge_posts_both_the_credit_and_the_liability(self):
        self.map_input_components()
        self.map_rcm()
        vendor = self.make_vendor("URD-V", MAHARASHTRA, treatment=TaxTreatment.UNREGISTERED)
        bill = self.make_bill(vendor, is_reverse_charge=True)

        with tenant_context(organization_id=self.org_a.id):
            journal = bill.accounting_journal
            # Input credit claimed...
            self.assertEqual(self.account_movement(journal, self.in_cgst), Decimal("18.00"))
            self.assertEqual(self.account_movement(journal, self.in_sgst), Decimal("18.00"))
            # ...and the matching liability we now owe the government.
            self.assertEqual(
                self.account_movement(journal, self.rcm_payable), Decimal("-36.00")
            )
            # And the vendor is owed only the taxable amount: under reverse
            # charge they never charged the tax, so it is owed to the
            # government, not to them.
            self.assertEqual(
                self.account_movement(journal, self.ap_account), Decimal("-200.00")
            )
            self.assertEqual(bill.total, Decimal("236.00"))
            self.assert_journal_balanced(journal)

    def test_reverse_charge_without_an_rcm_account_refuses_to_post(self):
        # Posting the credit without the liability would be a journal that
        # balances arithmetically but lies: better to refuse.
        self.map_input_components()
        vendor = self.make_vendor("URD-V2", MAHARASHTRA, treatment=TaxTreatment.UNREGISTERED)
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=vendor, bill_date=ORDER_DATE,
                due_date=DUE_DATE, payable_account=self.ap_account,
                tax_recoverable_account=self.input_tax_account,
                lines=self._service_lines(tax_rate=Decimal("18")),
                is_reverse_charge=True,
            )
            with self.assertRaises(ApplicationError) as ctx:
                post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(ctx.exception.get_codes(), "rcm_account_required")

    def test_an_ordinary_bill_posts_no_rcm_leg(self):
        self.map_input_components()
        self.map_rcm()
        vendor = self.make_vendor("MH-V5", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        bill = self.make_bill(vendor)

        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(
                self.account_movement(bill.accounting_journal, self.rcm_payable), Decimal("0")
            )


class TdsTests(PurchasesGstTestsBase):
    def test_tds_reduces_the_payable_and_is_held_for_remittance(self):
        self.map_input_components()
        with tenant_context(organization_id=self.org_a.id):
            section = create_withholding_section(
                organization=self.org_a, code="194Q", kind=WithholdingKind.TDS,
                applies_to=WithholdingAppliesTo.PURCHASE, rate=Decimal("1"),
                account=self.tds_payable,
            )
        vendor = self.make_vendor("MH-V6", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        bill = self.make_bill(vendor, withholding_section=section)

        # 200 taxable at 1% = 2.00 withheld.
        self.assertEqual(bill.withholding_amount, Decimal("2.00"))
        with tenant_context(organization_id=self.org_a.id):
            journal = bill.accounting_journal
            # The vendor is owed the bill total LESS what we keep back...
            self.assertEqual(
                self.account_movement(journal, self.ap_account),
                -(bill.total - Decimal("2.00")),
            )
            # ...which we now owe the tax authority instead.
            self.assertEqual(self.account_movement(journal, self.tds_payable), Decimal("-2.00"))
            self.assert_journal_balanced(journal)

    def test_no_section_means_the_payable_is_the_full_total(self):
        vendor = self.make_vendor("MH-V7", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        bill = self.make_bill(vendor)

        self.assertEqual(bill.withholding_amount, Decimal("0"))
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(
                self.account_movement(bill.accounting_journal, self.ap_account), -bill.total
            )


class ExpenseGstTests(PurchasesGstTestsBase):
    def test_an_expense_splits_its_tax_on_the_header(self):
        # An Expense has no line model, so the split lands on the header -
        # through the same split_tax the line-based documents use.
        self.map_input_components()
        vendor = self.make_vendor("MH-V8", MAHARASHTRA, gstin="27AAPFU0939F1ZV")
        with tenant_context(organization_id=self.org_a.id):
            expense = create_expense(
                organization=self.org_a, expense_date=ORDER_DATE, amount=Decimal("1000.00"),
                expense_account=self.office_expense_account,
                paid_through_account=self.bank_account,
                tax_recoverable_account=self.input_tax_account,
                vendor=vendor, tax_rate=Decimal("18"),
            )
            expense = post_expense(expense_id=expense.id, organization=self.org_a)

            self.assertEqual(expense.supply_nature, SupplyNature.INTRA_STATE)
            self.assertEqual(expense.cgst_amount, Decimal("90.00"))
            self.assertEqual(expense.sgst_amount, Decimal("90.00"))
            self.assertEqual(expense.tax_amount, Decimal("180.00"))

            journal = expense.accounting_journal
            self.assertEqual(self.account_movement(journal, self.in_cgst), Decimal("90.00"))
            self.assertEqual(self.account_movement(journal, self.in_sgst), Decimal("90.00"))
            self.assert_journal_balanced(journal)

    def test_an_expense_with_cess_carries_it_into_the_total(self):
        vendor = self.make_vendor("KA-V2", KARNATAKA, gstin="29AAGCB7383J1Z4")
        with tenant_context(organization_id=self.org_a.id):
            expense = create_expense(
                organization=self.org_a, expense_date=ORDER_DATE, amount=Decimal("1000.00"),
                expense_account=self.office_expense_account,
                paid_through_account=self.bank_account,
                tax_recoverable_account=self.input_tax_account,
                vendor=vendor, tax_rate=Decimal("18"), cess_rate=Decimal("12"),
            )
        self.assertEqual(expense.igst_amount, Decimal("180.00"))
        self.assertEqual(expense.cess_amount, Decimal("120.00"))
        self.assertEqual(expense.tax_amount, Decimal("300.00"))
        self.assertEqual(expense.total, Decimal("1300.00"))
