import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalStatus
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from audit.models import AuditLog
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.models.stock_adjustment import AdjustmentLineDirection, AdjustmentReason, AdjustmentStatus
from inventory.selectors import get_stock_on_hand
from inventory.services.adjustments import create_draft_adjustment, post_stock_adjustment, reverse_stock_adjustment
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class StockAdjustmentTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "adjustment-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.inventory_account = create_account(
                organization=self.org, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.shrinkage_expense = create_account(
                organization=self.org, code="5500", name="Inventory Shrinkage", account_type=AccountType.EXPENSE
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

    def _draft(self, direction, quantity, unit_cost=None, contra_account=None):
        with tenant_context(organization_id=self.org.id):
            return create_draft_adjustment(
                organization=self.org, warehouse=self.warehouse, adjustment_date=datetime.date(2026, 4, 10),
                reason=AdjustmentReason.PHYSICAL_COUNT,
                lines=[{"item": self.product, "direction": direction, "quantity": quantity, "unit_cost": unit_cost}],
                contra_account=contra_account, created_by=self.user,
            )

    def test_adjustment_in_increases_stock(self):
        adjustment = self._draft(AdjustmentLineDirection.IN, Decimal("20"), unit_cost=Decimal("12.00"))
        with tenant_context(organization_id=self.org.id):
            post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org, actor=self.user)
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("120"))

    def test_adjustment_out_decreases_stock(self):
        adjustment = self._draft(AdjustmentLineDirection.OUT, Decimal("15"))
        with tenant_context(organization_id=self.org.id):
            post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org, actor=self.user)
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("85"))

    def test_adjustment_in_requires_unit_cost(self):
        with self.assertRaises(ApplicationError):
            self._draft(AdjustmentLineDirection.IN, Decimal("20"), unit_cost=None)

    def test_adjustment_with_contra_account_posts_journal(self):
        adjustment = self._draft(AdjustmentLineDirection.OUT, Decimal("10"), contra_account=self.shrinkage_expense)
        with tenant_context(organization_id=self.org.id):
            posted = post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org, actor=self.user)
        self.assertIsNotNone(posted.accounting_journal)
        self.assertEqual(posted.accounting_journal.status, JournalStatus.POSTED)
        with tenant_context(organization_id=self.org.id):
            expense_line = posted.accounting_journal.lines.get(account=self.shrinkage_expense)
            inventory_line = posted.accounting_journal.lines.get(account=self.inventory_account)
        self.assertEqual(expense_line.debit, Decimal("100.00"))
        self.assertEqual(inventory_line.credit, Decimal("100.00"))

    def test_posted_adjustment_immutable(self):
        adjustment = self._draft(AdjustmentLineDirection.OUT, Decimal("10"))
        with tenant_context(organization_id=self.org.id):
            posted = post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org, actor=self.user)
            posted.memo = "tampered"
            with self.assertRaises(ValueError):
                posted.save()
            with self.assertRaises(ValueError):
                posted.delete()
            line = posted.lines.first()
            line.quantity = Decimal("999")
            with self.assertRaises(ValueError):
                line.save()

    def test_duplicate_post_is_idempotent(self):
        adjustment = self._draft(AdjustmentLineDirection.OUT, Decimal("10"))
        with tenant_context(organization_id=self.org.id):
            first = post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org, actor=self.user)
            second = post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org, actor=self.user)
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(first.id, second.id)
        self.assertEqual(on_hand, Decimal("90"))

    def test_audit_entry_created_on_post(self):
        adjustment = self._draft(AdjustmentLineDirection.OUT, Decimal("10"))
        with tenant_context(organization_id=self.org.id):
            post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org, actor=self.user)
            entries = AuditLog.objects.filter(action=AuditLog.Action.POST, object_id=str(adjustment.id))
        self.assertEqual(entries.count(), 1)

    def test_rollback_leaves_no_partial_movements_on_invalid_item(self):
        with tenant_context(organization_id=self.org.id):
            service_item = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
            )
        with self.assertRaises(ApplicationError):
            with tenant_context(organization_id=self.org.id):
                create_draft_adjustment(
                    organization=self.org, warehouse=self.warehouse, adjustment_date=datetime.date(2026, 4, 10),
                    reason=AdjustmentReason.PHYSICAL_COUNT,
                    lines=[
                        {"item": self.product, "direction": AdjustmentLineDirection.OUT, "quantity": Decimal("5")},
                        {"item": service_item, "direction": AdjustmentLineDirection.OUT, "quantity": Decimal("1")},
                    ],
                    created_by=self.user,
                )
        with tenant_context(organization_id=self.org.id):
            from inventory.models.stock_adjustment import StockAdjustment

            self.assertEqual(StockAdjustment.objects.count(), 0)
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("100"))


class StockAdjustmentReversalTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "adjustment-reversal-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.inventory_account = create_account(
                organization=self.org, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.shrinkage_expense = create_account(
                organization=self.org, code="5500", name="Inventory Shrinkage", account_type=AccountType.EXPENSE
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
                reason=AdjustmentReason.SHRINKAGE,
                lines=[{"item": self.product, "direction": AdjustmentLineDirection.OUT, "quantity": Decimal("10")}],
                contra_account=self.shrinkage_expense, created_by=self.user,
            )
            self.posted = post_stock_adjustment(adjustment_id=self.adjustment.id, organization=self.org, actor=self.user)

    def test_reversal_restores_stock(self):
        with tenant_context(organization_id=self.org.id):
            reverse_stock_adjustment(adjustment_id=self.posted.id, organization=self.org, actor=self.user)
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("100"))

    def test_original_marked_reversed(self):
        with tenant_context(organization_id=self.org.id):
            reverse_stock_adjustment(adjustment_id=self.posted.id, organization=self.org, actor=self.user)
            self.posted.refresh_from_db()
        self.assertEqual(self.posted.status, AdjustmentStatus.REVERSED)

    def test_reversal_reverses_accounting_journal_too(self):
        with tenant_context(organization_id=self.org.id):
            reversal = reverse_stock_adjustment(adjustment_id=self.posted.id, organization=self.org, actor=self.user)
        self.assertIsNotNone(reversal.accounting_journal)
        self.assertEqual(reversal.accounting_journal.status, JournalStatus.POSTED)

    def test_duplicate_reversal_prevented(self):
        with tenant_context(organization_id=self.org.id):
            reverse_stock_adjustment(adjustment_id=self.posted.id, organization=self.org, actor=self.user)
            with self.assertRaises(ApplicationError):
                reverse_stock_adjustment(adjustment_id=self.posted.id, organization=self.org, actor=self.user)
