"""Shared fixture for purchases tests.

Builds one organization with a full chart of accounts, a warehouse, items
(inventoried product, service, and a product with no inventory_account), and
a vendor — plus a second organization with its own vendor/item, so every
test file can assert cross-tenant rejection without rebuilding the world.
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
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from purchases.services.vendors import create_vendor

ORDER_DATE = datetime.date(2026, 4, 10)
DUE_DATE = datetime.date(2026, 5, 10)


class PurchasesFixtureMixin:
    """The fixture itself, deliberately separate from the Django test base
    class it is mixed into.

    This is not cosmetic. `TestCase` SUBCLASSES `TransactionTestCase`, so a
    test written as `class T(PurchasesTestsBase, TransactionTestCase)` has an
    MRO of (..., TestCase, TransactionTestCase) and silently runs with
    TestCase's wrapping transaction — exactly the semantics the recurring
    sweeps must NOT run under (see purchases/tests/test_recurring.py for why
    a `SET LOCAL` GUC leaks across a savepoint). Splitting the fixture out
    means a transaction-level test inherits from
    `PurchasesTransactionTestsBase` and genuinely gets one.
    """

    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "purch-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "purch-owner-b@example.com")
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a,
                start_date=datetime.date(2026, 4, 1),
                end_date=datetime.date(2027, 3, 31),
            )
            self.ap_account = create_account(
                organization=self.org_a, code="2000", name="Accounts Payable", account_type=AccountType.LIABILITY
            )
            self.bank_account = create_account(
                organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            self.inventory_account = create_account(
                organization=self.org_a, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.input_tax_account = create_account(
                organization=self.org_a, code="1300", name="Input Tax Credit", account_type=AccountType.ASSET
            )
            self.vendor_advance_account = create_account(
                organization=self.org_a, code="1400", name="Vendor Advances", account_type=AccountType.ASSET
            )
            self.purchase_expense_account = create_account(
                organization=self.org_a, code="5100", name="Purchases", account_type=AccountType.EXPENSE
            )
            self.price_variance_account = create_account(
                organization=self.org_a, code="5200", name="Purchase Price Variance",
                account_type=AccountType.EXPENSE,
            )
            self.office_expense_account = create_account(
                organization=self.org_a, code="5300", name="Office Expenses", account_type=AccountType.EXPENSE
            )

            self.warehouse = create_warehouse(organization=self.org_a, code="MAIN", name="Main")
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")

            self.product = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="WID-1", track_inventory=True,
                purchase_account=self.purchase_expense_account,
                inventory_account=self.inventory_account,
            )
            self.service = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                purchase_account=self.purchase_expense_account,
            )
            # A product that does NOT track inventory — expensed on purchase,
            # never capitalised. Exercises the non-inventoried product branch,
            # which is distinct from a service item.
            self.untracked_product = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Stationery", unit=self.unit,
                sku="STA-1", track_inventory=False, purchase_account=self.office_expense_account,
            )

            self.vendor = create_vendor(
                organization=self.org_a, vendor_code="VEN-1", display_name="Acme Supplies",
                currency=self.currency,
            )

        with tenant_context(organization_id=self.org_b.id):
            self.vendor_b = create_vendor(
                organization=self.org_b, vendor_code="VEN-1", display_name="Beta Supplies",
                currency=self.currency,
            )
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.item_b = create_item(
                organization=self.org_b, item_type=ItemType.SERVICE, name="Other", unit=self.unit_b
            )

    # -------------------------------------------------------- helpers

    def _product_lines(self, quantity=Decimal("10"), unit_price=Decimal("50.00"), **kwargs):
        return [{"item": self.product, "quantity": quantity, "unit_price": unit_price, **kwargs}]

    def _service_lines(self, quantity=Decimal("2"), unit_price=Decimal("100.00"), **kwargs):
        return [{"item": self.service, "quantity": quantity, "unit_price": unit_price, **kwargs}]

    def _make_approved_po(self, lines=None, **kwargs):
        from purchases.services.purchase_orders import approve_purchase_order, create_purchase_order

        order = create_purchase_order(
            organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
            warehouse=self.warehouse, lines=lines if lines is not None else self._product_lines(), **kwargs,
        )
        return approve_purchase_order(order_id=order.id, organization=self.org_a)

    def _make_received_gr(self, order=None, quantity=Decimal("10"), unit_cost=None):
        """Creates and receives a goods receipt, returning (receipt, line)."""
        from purchases.services.goods_receipts import create_goods_receipt, receive_goods

        order = order or self._make_approved_po()
        order_line = order.lines.first()
        line = {"item": self.product, "quantity": quantity, "source_order_line": order_line}
        if unit_cost is not None:
            line["unit_cost"] = unit_cost
        receipt = create_goods_receipt(
            organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
            receipt_date=ORDER_DATE, source_purchase_order=order, lines=[line],
        )
        receipt = receive_goods(receipt_id=receipt.id, organization=self.org_a)
        return receipt, receipt.lines.first()

    def assert_journal_balanced(self, journal):
        """Every posted journal must satisfy the system's central invariant."""
        debits = sum((line.debit for line in journal.lines.all()), Decimal("0"))
        credits = sum((line.credit for line in journal.lines.all()), Decimal("0"))
        self.assertEqual(debits, credits, f"Journal {journal.id} is unbalanced: {debits} != {credits}")
        return debits

    def account_movement(self, journal, account):
        """Net (debit - credit) posted to one account by one journal."""
        lines = [line for line in journal.lines.all() if line.account_id == account.id]
        return sum((line.debit - line.credit for line in lines), Decimal("0"))


class PurchasesTestsBase(PurchasesFixtureMixin, TestCase):
    """Default base: fast, wrapped in a transaction rolled back per test."""


class PurchasesTransactionTestsBase(PurchasesFixtureMixin, TransactionTestCase):
    """For tests that need real transaction boundaries — the recurring
    sweeps (per-organization `SET LOCAL` tenant GUCs) and the concurrency
    races, which need committed rows visible to a second connection."""
