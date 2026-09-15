import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalStatus
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from audit.models import AuditLog
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.selectors import get_stock_on_hand
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class OpeningStockTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "opening-stock-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.inventory_account = create_account(
                organization=self.org, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.equity = create_account(
                organization=self.org, code="3900", name="Opening Balance Equity",
                account_type=AccountType.EQUITY, is_system=True,
            )
            self.product = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                track_inventory=True, inventory_account=self.inventory_account,
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )

    def test_opening_stock_creates_movement_with_correct_quantity_and_value(self):
        with tenant_context(organization_id=self.org.id):
            result = post_opening_stock(
                organization=self.org, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product, "quantity": Decimal("50"), "unit_cost": Decimal("20.00")}],
                opening_date=datetime.date(2026, 4, 1), contra_account=self.equity, actor=self.user,
            )
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("50"))
        self.assertEqual(len(result["movements"]), 1)
        self.assertEqual(result["movements"][0].unit_cost, Decimal("20.00"))

    def test_opening_stock_posts_balanced_accounting_journal(self):
        with tenant_context(organization_id=self.org.id):
            result = post_opening_stock(
                organization=self.org, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product, "quantity": Decimal("50"), "unit_cost": Decimal("20.00")}],
                opening_date=datetime.date(2026, 4, 1), contra_account=self.equity, actor=self.user,
            )
        journal = result["journal"]
        self.assertIsNotNone(journal)
        self.assertEqual(journal.status, JournalStatus.POSTED)
        with tenant_context(organization_id=self.org.id):
            inventory_line = journal.lines.get(account=self.inventory_account)
            equity_line = journal.lines.get(account=self.equity)
        self.assertEqual(inventory_line.debit, Decimal("1000.00"))
        self.assertEqual(equity_line.credit, Decimal("1000.00"))

    def test_opening_stock_without_contra_account_is_quantity_only(self):
        with tenant_context(organization_id=self.org.id):
            result = post_opening_stock(
                organization=self.org, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product, "quantity": Decimal("10"), "unit_cost": Decimal("5.00")}],
                opening_date=datetime.date(2026, 4, 1), actor=self.user,
            )
        self.assertIsNone(result["journal"])

    def test_opening_stock_audit_entry_created(self):
        with tenant_context(organization_id=self.org.id):
            post_opening_stock(
                organization=self.org, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product, "quantity": Decimal("10"), "unit_cost": Decimal("5.00")}],
                opening_date=datetime.date(2026, 4, 1), contra_account=self.equity, actor=self.user,
            )
            entries = AuditLog.objects.filter(object_type="inventory.OpeningStock")
        self.assertEqual(entries.count(), 1)
