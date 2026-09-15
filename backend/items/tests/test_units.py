from django.db import IntegrityError
from django.test import TestCase

from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_org_with_owner
from items.models.unit import UnitOfMeasure
from items.services.units import create_unit, seed_default_units, update_unit


class UnitOfMeasureTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "unit-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "unit-owner-b@example.com")

    def test_create_unit(self):
        with tenant_context(organization_id=self.org_a.id):
            unit = create_unit(organization=self.org_a, code="KG", name="Kilogram", symbol="kg")
        self.assertEqual(unit.code, "KG")

    def test_code_unique_per_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            create_unit(organization=self.org_a, code="KG", name="Kilogram")
            with self.assertRaises(IntegrityError):
                create_unit(organization=self.org_a, code="KG", name="Kilogram again")

    def test_same_code_allowed_across_organizations(self):
        with tenant_context(organization_id=self.org_a.id):
            create_unit(organization=self.org_a, code="KG", name="Kilogram")
        with tenant_context(organization_id=self.org_b.id):
            unit_b = create_unit(organization=self.org_b, code="KG", name="Kilogram")
        self.assertEqual(unit_b.code, "KG")

    def test_seed_default_units_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            first = seed_default_units(organization=self.org_a)
            second = seed_default_units(organization=self.org_a)
        self.assertEqual(len(first), 9)
        self.assertEqual(len(second), 0)
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(UnitOfMeasure.objects.count(), 9)

    def test_system_unit_protected_fields_cannot_change(self):
        with tenant_context(organization_id=self.org_a.id):
            unit = create_unit(organization=self.org_a, code="KG", name="Kilogram", is_system=True)
            with self.assertRaises(ApplicationError):
                update_unit(unit=unit, code="KGM")
            updated = update_unit(unit=unit, symbol="kilo")
            self.assertEqual(updated.symbol, "kilo")

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_unit(organization=self.org_a, code="KG", name="Kilogram")
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(UnitOfMeasure.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_unit(organization=self.org_a, code="KG", name="Kilogram")
        clear_tenant_context()
        self.assertEqual(list(UnitOfMeasure.objects.all()), [])
