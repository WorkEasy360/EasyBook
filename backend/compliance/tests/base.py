"""Shared fixtures for the compliance suite.

Three-class split for the reason `purchases/tests/base.py` documents: `TestCase`
SUBCLASSES `TransactionTestCase`, so `class T(ComplianceTestsBase,
TransactionTestCase)` resolves by MRO to `TestCase` and silently runs inside a
wrapping transaction - defeating exactly the tests that need committed rows
visible to a second connection.

Builds a genuinely posted sales and purchase history rather than fixture rows
poked straight into the tables, because every selector here derives its figures
from posted documents. A register test against hand-made rows would prove
nothing about what the posting services actually write.
"""

import datetime
from decimal import Decimal

from django.test import TestCase, TransactionTestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.services.warehouses import create_warehouse
from items.models.hsn_sac import HsnSacClassification
from items.models.item import ItemType
from items.services.hsn_sac import create_hsn_sac_code
from items.services.items import create_item
from items.services.units import create_unit
from purchases.services.bills import create_bill, post_bill
from purchases.services.vendors import create_vendor
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice
from tax.enums import TaxComponent, TaxDirection, TaxTreatment
from tax.services.profile import set_tax_account_mapping, set_tax_profile
from tax.testing import ensure_state_codes

PERIOD_START = datetime.date(2026, 4, 1)
INVOICE_DATE = datetime.date(2026, 4, 10)
DUE_DATE = datetime.date(2026, 5, 10)
PERIOD_END = datetime.date(2026, 4, 30)

MAHARASHTRA = "27"
KARNATAKA = "29"

# A real, well-formed GSTIN whose published check character this codebase
# reproduces independently (see tax/tests/test_gstin.py).
SUPPLIER_GSTIN = "27AAPFU0939F1ZV"
CUSTOMER_MH_GSTIN = "27AAPFU0939F1ZV"
CUSTOMER_KA_GSTIN = "29AAGCB7383J1Z4"


class ComplianceFixtureMixin:
    """Plain mixin, not a TestCase - see the module docstring."""

    def setUp(self):
        super().setUp()
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "comp-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "comp-b@example.com")
        self.currency = make_currency("INR")
        ensure_state_codes()

        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a,
                start_date=datetime.date(2026, 4, 1),
                end_date=datetime.date(2027, 3, 31),
            )
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="Accounts Receivable",
                account_type=AccountType.ASSET,
            )
            self.ap_account = create_account(
                organization=self.org_a, code="2000", name="Accounts Payable",
                account_type=AccountType.LIABILITY,
            )
            self.sales_account = create_account(
                organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME,
            )
            self.purchase_account = create_account(
                organization=self.org_a, code="5100", name="Purchases",
                account_type=AccountType.EXPENSE,
            )
            self.out_cgst = create_account(
                organization=self.org_a, code="2110", name="Output CGST",
                account_type=AccountType.LIABILITY,
            )
            self.out_sgst = create_account(
                organization=self.org_a, code="2111", name="Output SGST",
                account_type=AccountType.LIABILITY,
            )
            self.out_igst = create_account(
                organization=self.org_a, code="2112", name="Output IGST",
                account_type=AccountType.LIABILITY,
            )
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

            self.warehouse = create_warehouse(
                organization=self.org_a, code="MAIN", name="Main"
            )
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            # A real SAC, because GSTR-1 Table 12 reports HSN-wise and a blank
            # code would make that summary untestable.
            self.sac = create_hsn_sac_code(
                organization=self.org_a, code="998311",
                classification=HsnSacClassification.SAC, description="Management consulting",
            )
            self.service = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting",
                unit=self.unit, sales_account=self.sales_account,
                purchase_account=self.purchase_account, hsn_sac_code=self.sac,
            )

            self.profile = set_tax_profile(
                organization=self.org_a, state_code=MAHARASHTRA, gstin=SUPPLIER_GSTIN,
            )
            for component, account in (
                (TaxComponent.CGST, self.out_cgst),
                (TaxComponent.SGST_UTGST, self.out_sgst),
                (TaxComponent.IGST, self.out_igst),
            ):
                set_tax_account_mapping(
                    organization=self.org_a, component=component,
                    direction=TaxDirection.OUTPUT, account=account,
                )
            for component, account in (
                (TaxComponent.CGST, self.in_cgst),
                (TaxComponent.SGST_UTGST, self.in_sgst),
                (TaxComponent.IGST, self.in_igst),
            ):
                set_tax_account_mapping(
                    organization=self.org_a, component=component,
                    direction=TaxDirection.INPUT, account=account,
                )
            set_tax_account_mapping(
                organization=self.org_a, component=TaxComponent.IGST,
                direction=TaxDirection.RCM_PAYABLE, account=self.rcm_payable,
            )

            # Three counterparty shapes, because they file differently:
            # same-state B2B, other-state B2B, and an unregistered consumer.
            self.customer_mh = create_customer(
                organization=self.org_a, customer_code="MH", display_name="Mumbai Co",
                currency=self.currency, tax_treatment=TaxTreatment.REGISTERED,
                gstin=CUSTOMER_MH_GSTIN, place_of_supply_state_code=MAHARASHTRA,
            )
            self.customer_ka = create_customer(
                organization=self.org_a, customer_code="KA", display_name="Bengaluru Co",
                currency=self.currency, tax_treatment=TaxTreatment.REGISTERED,
                gstin=CUSTOMER_KA_GSTIN, place_of_supply_state_code=KARNATAKA,
            )
            self.consumer = create_customer(
                organization=self.org_a, customer_code="B2C", display_name="Walk-in",
                currency=self.currency, tax_treatment=TaxTreatment.CONSUMER,
                place_of_supply_state_code=MAHARASHTRA,
            )
            self.vendor = create_vendor(
                organization=self.org_a, vendor_code="V1", display_name="Supplier",
                currency=self.currency, tax_treatment=TaxTreatment.REGISTERED,
                gstin=CUSTOMER_MH_GSTIN, place_of_supply_state_code=MAHARASHTRA,
            )

    # ------------------------------------------------------------ helpers

    def make_invoice(self, customer, *, unit_price=Decimal("1000.00"), tax_rate=Decimal("18"),
                     post=True, **kwargs):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=customer, invoice_date=INVOICE_DATE,
                due_date=DUE_DATE, receivable_account=self.ar_account,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"),
                    "unit_price": unit_price, "tax_rate": tax_rate,
                }],
                **kwargs,
            )
            if post:
                invoice = post_invoice(
                    invoice_id=invoice.id, organization=self.org_a, actor=self.user_a
                )
            return invoice

    def make_bill(self, *, unit_price=Decimal("500.00"), tax_rate=Decimal("18"),
                  post=True, **kwargs):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=INVOICE_DATE,
                due_date=DUE_DATE, payable_account=self.ap_account,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"),
                    "unit_price": unit_price, "tax_rate": tax_rate,
                    "expense_account": self.purchase_account,
                }],
                **kwargs,
            )
            if post:
                bill = post_bill(bill_id=bill.id, organization=self.org_a, actor=self.user_a)
            return bill


class ComplianceTestsBase(ComplianceFixtureMixin, TestCase):
    """Default base: fast, wrapped in a transaction rolled back per test."""


class ComplianceTransactionTestsBase(ComplianceFixtureMixin, TransactionTestCase):
    """For the concurrency races, which need committed rows visible to a
    second connection."""
