from django.test import SimpleTestCase

from authz.roles import ROLE_PERMISSIONS, Permission, Role, role_has_permission


class RolePermissionMatrixTests(SimpleTestCase):
    def test_owner_has_every_permission(self):
        all_permissions = {v for k, v in vars(Permission).items() if not k.startswith("_")}
        self.assertEqual(ROLE_PERMISSIONS[Role.OWNER], all_permissions)

    def test_admin_lacks_only_manage_organization(self):
        self.assertNotIn(Permission.MANAGE_ORGANIZATION, ROLE_PERMISSIONS[Role.ADMIN])
        self.assertTrue(role_has_permission(Role.ADMIN, Permission.MANAGE_ITEMS))
        self.assertTrue(role_has_permission(Role.ADMIN, Permission.MANAGE_WAREHOUSES))

    def test_viewer_is_read_only_for_items_and_inventory(self):
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_ITEMS))
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_INVENTORY))
        self.assertFalse(role_has_permission(Role.VIEWER, Permission.MANAGE_ITEMS))
        self.assertFalse(role_has_permission(Role.VIEWER, Permission.ADJUST_INVENTORY))
        self.assertFalse(role_has_permission(Role.VIEWER, Permission.MANAGE_WAREHOUSES))

    def test_staff_can_adjust_and_transfer_but_not_manage_setup(self):
        self.assertTrue(role_has_permission(Role.STAFF, Permission.ADJUST_INVENTORY))
        self.assertTrue(role_has_permission(Role.STAFF, Permission.TRANSFER_INVENTORY))
        self.assertFalse(role_has_permission(Role.STAFF, Permission.MANAGE_ITEMS))
        self.assertFalse(role_has_permission(Role.STAFF, Permission.MANAGE_WAREHOUSES))

    def test_accountant_has_view_only_items_and_inventory(self):
        self.assertTrue(role_has_permission(Role.ACCOUNTANT, Permission.VIEW_ITEMS))
        self.assertTrue(role_has_permission(Role.ACCOUNTANT, Permission.VIEW_INVENTORY))
        self.assertFalse(role_has_permission(Role.ACCOUNTANT, Permission.ADJUST_INVENTORY))
        self.assertFalse(role_has_permission(Role.ACCOUNTANT, Permission.MANAGE_ITEMS))
