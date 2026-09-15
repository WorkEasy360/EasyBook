import datetime
import threading
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.models.stock_adjustment import AdjustmentLineDirection, AdjustmentReason
from inventory.models.stock_movement import MovementType
from inventory.selectors import get_stock_on_hand
from inventory.services.adjustments import create_draft_adjustment, post_stock_adjustment
from inventory.services.movements import record_stock_movement
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class ConcurrentStockIssueTests(TransactionTestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "inv-concurrency-owner@example.com")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.product = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                track_inventory=True,
            )
            record_stock_movement(
                organization=self.org, item=self.product, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("10"), unit_cost=Decimal("5.00"),
                movement_date=timezone.now(),
            )

    def test_concurrent_issue_cannot_push_stock_negative(self):
        """Two concurrent ISSUEs of 8 units each against 10 in stock: exactly
        one must succeed, the other must be rejected — never both, which
        would silently take stock to -6 under the default BLOCK policy."""
        results = []
        errors = []
        lock = threading.Lock()

        def do_issue():
            try:
                with tenant_context(organization_id=self.org.id):
                    record_stock_movement(
                        organization=self.org, item=self.product, warehouse=self.warehouse,
                        movement_type=MovementType.ISSUE, quantity=Decimal("8"), movement_date=timezone.now(),
                    )
                with lock:
                    results.append("ok")
            except ApplicationError as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_issue) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 1, "exactly one concurrent issue should succeed")
        self.assertEqual(len(errors), 4)

        with tenant_context(organization_id=self.org.id):
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertGreaterEqual(on_hand, Decimal("0"))
        self.assertEqual(on_hand, Decimal("2"))


class ConcurrentAdjustmentTests(TransactionTestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "inv-adj-concurrency-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
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
                organization=self.org, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product, "quantity": Decimal("100"), "unit_cost": Decimal("10.00")}],
                opening_date=datetime.date(2026, 4, 1), contra_account=self.equity, actor=self.user,
            )
            self.adjustment = create_draft_adjustment(
                organization=self.org, warehouse=self.warehouse, adjustment_date=datetime.date(2026, 4, 10),
                reason=AdjustmentReason.PHYSICAL_COUNT,
                lines=[{"item": self.product, "direction": AdjustmentLineDirection.OUT, "quantity": Decimal("10")}],
                created_by=self.user,
            )

    def test_concurrent_posting_of_same_adjustment_yields_one_result(self):
        results = []
        errors = []
        lock = threading.Lock()

        def do_post():
            try:
                with tenant_context(organization_id=self.org.id):
                    posted = post_stock_adjustment(adjustment_id=self.adjustment.id, organization=self.org, actor=self.user)
                with lock:
                    results.append(posted.id)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_post) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)

        with tenant_context(organization_id=self.org.id):
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("90"), "concurrent duplicate posting must not double-apply the movement")


class RawSQLRLSTests(TransactionTestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "inv-rls-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "inv-rls-b@example.com")
        with tenant_context(organization_id=self.org_a.id):
            self.warehouse_a = create_warehouse(organization=self.org_a, code="MAIN", name="Main")
        with tenant_context(organization_id=self.org_b.id):
            self.warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main")

    def test_rls_blocks_direct_sql_without_tenant_context(self):
        clear_tenant_context()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM inventory_warehouse")
            rows = cursor.fetchall()
        self.assertEqual(rows, [])

    def test_rls_direct_sql_scoped_to_org_a_cannot_see_org_b(self):
        with tenant_context(organization_id=self.org_a.id):
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM inventory_warehouse")
                ids = {row[0] for row in cursor.fetchall()}
        self.assertEqual(ids, {self.warehouse_a.id})

    def test_rls_blocks_stock_movement_table_without_context(self):
        clear_tenant_context()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM inventory_stockmovement")
            rows = cursor.fetchall()
        self.assertEqual(rows, [])
