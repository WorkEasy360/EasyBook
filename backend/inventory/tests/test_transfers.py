import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.selectors import get_stock_on_hand
from inventory.services.opening_stock import post_opening_stock
from inventory.services.transfers import transfer_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class StockTransferTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "transfer-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.main = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.branch = create_warehouse(organization=self.org, code="BRANCH", name="Branch")
            self.inventory_account = create_account(
                organization=self.org, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.equity = create_account(
                organization=self.org, code="3900", name="Opening Balance Equity", account_type=AccountType.EQUITY
            )
            self.product = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                track_inventory=True, inventory_account=self.inventory_account,
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            post_opening_stock(
                organization=self.org, warehouse=self.main, currency=self.currency,
                lines=[{"item": self.product, "quantity": Decimal("50"), "unit_cost": Decimal("10.00")}],
                opening_date=datetime.date(2026, 4, 1), contra_account=self.equity, actor=self.user,
            )

    def test_transfer_moves_stock_between_warehouses(self):
        with tenant_context(organization_id=self.org.id):
            transfer_stock(
                organization=self.org, item=self.product, from_warehouse=self.main, to_warehouse=self.branch,
                quantity=Decimal("20"), actor=self.user,
            )
            main_on_hand = get_stock_on_hand(item=self.product, warehouse=self.main)
            branch_on_hand = get_stock_on_hand(item=self.product, warehouse=self.branch)
        self.assertEqual(main_on_hand, Decimal("30"))
        self.assertEqual(branch_on_hand, Decimal("20"))

    def test_organization_total_stock_unchanged_by_transfer(self):
        with tenant_context(organization_id=self.org.id):
            total_before = get_stock_on_hand(item=self.product)
            transfer_stock(
                organization=self.org, item=self.product, from_warehouse=self.main, to_warehouse=self.branch,
                quantity=Decimal("15"), actor=self.user,
            )
            total_after = get_stock_on_hand(item=self.product)
        self.assertEqual(total_before, Decimal("50"))
        self.assertEqual(total_after, Decimal("50"))

    def test_transfer_carries_cost_basis(self):
        with tenant_context(organization_id=self.org.id):
            from inventory.selectors import get_weighted_average_cost

            transfer_stock(
                organization=self.org, item=self.product, from_warehouse=self.main, to_warehouse=self.branch,
                quantity=Decimal("20"), actor=self.user,
            )
            _, branch_cost = get_weighted_average_cost(item=self.product, warehouse=self.branch)
        self.assertEqual(branch_cost, Decimal("10.00"))

    def test_transfer_exceeding_available_stock_rejected(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                transfer_stock(
                    organization=self.org, item=self.product, from_warehouse=self.main, to_warehouse=self.branch,
                    quantity=Decimal("999"), actor=self.user,
                )
            main_on_hand = get_stock_on_hand(item=self.product, warehouse=self.main)
        self.assertEqual(main_on_hand, Decimal("50"))

    def test_transfer_to_same_warehouse_rejected(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                transfer_stock(
                    organization=self.org, item=self.product, from_warehouse=self.main, to_warehouse=self.main,
                    quantity=Decimal("1"), actor=self.user,
                )
