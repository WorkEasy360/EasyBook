from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_org_with_owner
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_stock_on_hand
from inventory.services.movements import record_stock_movement
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class StockMovementTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "movement-owner@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "movement-owner-b@example.com")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.product = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                track_inventory=True,
            )
            self.service = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
            )
            self.non_tracked = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Gadget", unit=self.unit,
                track_inventory=False,
            )

    def _receive(self, quantity, unit_cost=Decimal("10.00")):
        return record_stock_movement(
            organization=self.org, item=self.product, warehouse=self.warehouse,
            movement_type=MovementType.RECEIPT, quantity=quantity, unit_cost=unit_cost,
            movement_date=timezone.now(), created_by=self.user,
        )

    def test_inbound_movement_increases_stock(self):
        with tenant_context(organization_id=self.org.id):
            self._receive(Decimal("10"))
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("10"))

    def test_outbound_movement_decreases_stock(self):
        with tenant_context(organization_id=self.org.id):
            self._receive(Decimal("10"))
            record_stock_movement(
                organization=self.org, item=self.product, warehouse=self.warehouse,
                movement_type=MovementType.ISSUE, quantity=Decimal("4"), movement_date=timezone.now(),
            )
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("6"))

    def test_service_item_movement_rejected(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                record_stock_movement(
                    organization=self.org, item=self.service, warehouse=self.warehouse,
                    movement_type=MovementType.RECEIPT, quantity=Decimal("1"), movement_date=timezone.now(),
                )

    def test_non_tracked_item_movement_rejected(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                record_stock_movement(
                    organization=self.org, item=self.non_tracked, warehouse=self.warehouse,
                    movement_type=MovementType.RECEIPT, quantity=Decimal("1"), movement_date=timezone.now(),
                )

    def test_zero_quantity_rejected(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                record_stock_movement(
                    organization=self.org, item=self.product, warehouse=self.warehouse,
                    movement_type=MovementType.RECEIPT, quantity=Decimal("0"), movement_date=timezone.now(),
                )

    def test_negative_quantity_rejected(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                record_stock_movement(
                    organization=self.org, item=self.product, warehouse=self.warehouse,
                    movement_type=MovementType.RECEIPT, quantity=Decimal("-1"), movement_date=timezone.now(),
                )

    def test_issue_beyond_available_blocked_by_default_policy(self):
        with tenant_context(organization_id=self.org.id):
            self._receive(Decimal("5"))
            with self.assertRaises(ApplicationError):
                record_stock_movement(
                    organization=self.org, item=self.product, warehouse=self.warehouse,
                    movement_type=MovementType.ISSUE, quantity=Decimal("6"), movement_date=timezone.now(),
                )

    def test_negative_stock_allowed_when_policy_enabled(self):
        from inventory.services.settings import get_or_create_inventory_settings

        with tenant_context(organization_id=self.org.id):
            settings = get_or_create_inventory_settings(self.org)
            settings.allow_negative_stock = True
            settings.save(update_fields=["allow_negative_stock"])
            self._receive(Decimal("5"))
            record_stock_movement(
                organization=self.org, item=self.product, warehouse=self.warehouse,
                movement_type=MovementType.ISSUE, quantity=Decimal("6"), movement_date=timezone.now(),
            )
            on_hand = get_stock_on_hand(item=self.product, warehouse=self.warehouse)
        self.assertEqual(on_hand, Decimal("-1"))

    def test_cross_tenant_item_rejected(self):
        with tenant_context(organization_id=self.org_b.id):
            warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main")
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                record_stock_movement(
                    organization=self.org, item=self.product, warehouse=warehouse_b,
                    movement_type=MovementType.RECEIPT, quantity=Decimal("1"), movement_date=timezone.now(),
                )

    def test_movement_is_append_only(self):
        with tenant_context(organization_id=self.org.id):
            movement = self._receive(Decimal("10"))
            movement.quantity = Decimal("99")
            with self.assertRaises(ValueError):
                movement.save()
            with self.assertRaises(ValueError):
                movement.delete()

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org.id):
            self._receive(Decimal("10"))
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(StockMovement.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org.id):
            self._receive(Decimal("10"))
        clear_tenant_context()
        self.assertEqual(list(StockMovement.objects.all()), [])
