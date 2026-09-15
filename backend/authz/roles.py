"""Fixed role/permission catalog for v1.

Custom, per-organization roles are a Phase 12 (scale features) concern — see
authz/CLAUDE.md. For now roles are a closed set stored on Membership.role and
permissions are code-defined constants, not database rows, since neither
changes without a code deploy at this stage.
"""

from django.db import models


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    ACCOUNTANT = "accountant", "Accountant"
    STAFF = "staff", "Staff"
    VIEWER = "viewer", "Viewer"


class Permission:
    MANAGE_ORGANIZATION = "organization.manage"
    MANAGE_MEMBERS = "members.manage"
    MANAGE_ACCOUNTING = "accounting.manage"
    VIEW_ACCOUNTING = "accounting.view"
    MANAGE_TRANSACTIONS = "transactions.manage"
    VIEW_TRANSACTIONS = "transactions.view"
    VIEW_REPORTS = "reports.view"
    VIEW_ITEMS = "items.view"
    MANAGE_ITEMS = "items.manage"
    VIEW_INVENTORY = "inventory.view"
    ADJUST_INVENTORY = "inventory.adjust"
    TRANSFER_INVENTORY = "inventory.transfer"
    MANAGE_WAREHOUSES = "inventory.manage_warehouses"
    VIEW_CUSTOMERS = "sales.view_customers"
    MANAGE_CUSTOMERS = "sales.manage_customers"
    VIEW_QUOTES = "sales.view_quotes"
    MANAGE_QUOTES = "sales.manage_quotes"
    VIEW_ORDERS = "sales.view_orders"
    MANAGE_ORDERS = "sales.manage_orders"
    VIEW_DELIVERIES = "sales.view_deliveries"
    MANAGE_DELIVERIES = "sales.manage_deliveries"
    VIEW_INVOICES = "sales.view_invoices"
    CREATE_INVOICE = "sales.create_invoice"
    POST_INVOICE = "sales.post_invoice"
    VOID_INVOICE = "sales.void_invoice"
    VIEW_PAYMENTS = "sales.view_payments"
    RECORD_PAYMENT = "sales.record_payment"
    VIEW_CREDIT_NOTES = "sales.view_credit_notes"
    ISSUE_CREDIT_NOTE = "sales.issue_credit_note"
    VIEW_RECURRING_INVOICES = "sales.view_recurring_invoices"
    MANAGE_RECURRING_INVOICES = "sales.manage_recurring_invoices"


_ALL_PERMISSIONS = {
    Permission.MANAGE_ORGANIZATION,
    Permission.MANAGE_MEMBERS,
    Permission.MANAGE_ACCOUNTING,
    Permission.VIEW_ACCOUNTING,
    Permission.MANAGE_TRANSACTIONS,
    Permission.VIEW_TRANSACTIONS,
    Permission.VIEW_REPORTS,
    Permission.VIEW_ITEMS,
    Permission.MANAGE_ITEMS,
    Permission.VIEW_INVENTORY,
    Permission.ADJUST_INVENTORY,
    Permission.TRANSFER_INVENTORY,
    Permission.MANAGE_WAREHOUSES,
    Permission.VIEW_CUSTOMERS,
    Permission.MANAGE_CUSTOMERS,
    Permission.VIEW_QUOTES,
    Permission.MANAGE_QUOTES,
    Permission.VIEW_ORDERS,
    Permission.MANAGE_ORDERS,
    Permission.VIEW_DELIVERIES,
    Permission.MANAGE_DELIVERIES,
    Permission.VIEW_INVOICES,
    Permission.CREATE_INVOICE,
    Permission.POST_INVOICE,
    Permission.VOID_INVOICE,
    Permission.VIEW_PAYMENTS,
    Permission.RECORD_PAYMENT,
    Permission.VIEW_CREDIT_NOTES,
    Permission.ISSUE_CREDIT_NOTE,
    Permission.VIEW_RECURRING_INVOICES,
    Permission.MANAGE_RECURRING_INVOICES,
}

ROLE_PERMISSIONS = {
    Role.OWNER: _ALL_PERMISSIONS,
    Role.ADMIN: _ALL_PERMISSIONS - {Permission.MANAGE_ORGANIZATION},
    Role.ACCOUNTANT: {
        Permission.MANAGE_ACCOUNTING,
        Permission.VIEW_ACCOUNTING,
        Permission.MANAGE_TRANSACTIONS,
        Permission.VIEW_TRANSACTIONS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_ITEMS,
        Permission.VIEW_INVENTORY,
        Permission.VIEW_CUSTOMERS,
        Permission.MANAGE_CUSTOMERS,
        Permission.VIEW_QUOTES,
        Permission.MANAGE_QUOTES,
        Permission.VIEW_ORDERS,
        Permission.MANAGE_ORDERS,
        Permission.VIEW_DELIVERIES,
        Permission.MANAGE_DELIVERIES,
        Permission.VIEW_INVOICES,
        Permission.CREATE_INVOICE,
        Permission.POST_INVOICE,
        Permission.VOID_INVOICE,
        Permission.VIEW_PAYMENTS,
        Permission.RECORD_PAYMENT,
        Permission.VIEW_CREDIT_NOTES,
        Permission.ISSUE_CREDIT_NOTE,
        Permission.VIEW_RECURRING_INVOICES,
        Permission.MANAGE_RECURRING_INVOICES,
    },
    Role.STAFF: {
        Permission.VIEW_ACCOUNTING,
        Permission.MANAGE_TRANSACTIONS,
        Permission.VIEW_TRANSACTIONS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_ITEMS,
        Permission.VIEW_INVENTORY,
        Permission.ADJUST_INVENTORY,
        Permission.TRANSFER_INVENTORY,
        Permission.VIEW_CUSTOMERS,
        Permission.MANAGE_CUSTOMERS,
        Permission.VIEW_QUOTES,
        Permission.MANAGE_QUOTES,
        Permission.VIEW_ORDERS,
        Permission.MANAGE_ORDERS,
        Permission.VIEW_DELIVERIES,
        Permission.MANAGE_DELIVERIES,
        Permission.VIEW_INVOICES,
        Permission.CREATE_INVOICE,
        Permission.POST_INVOICE,
        Permission.VOID_INVOICE,
        Permission.VIEW_PAYMENTS,
        Permission.RECORD_PAYMENT,
        Permission.VIEW_CREDIT_NOTES,
        Permission.ISSUE_CREDIT_NOTE,
        Permission.VIEW_RECURRING_INVOICES,
        Permission.MANAGE_RECURRING_INVOICES,
    },
    Role.VIEWER: {
        Permission.VIEW_ACCOUNTING,
        Permission.VIEW_TRANSACTIONS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_ITEMS,
        Permission.VIEW_INVENTORY,
        Permission.VIEW_CUSTOMERS,
        Permission.VIEW_QUOTES,
        Permission.VIEW_ORDERS,
        Permission.VIEW_DELIVERIES,
        Permission.VIEW_INVOICES,
        Permission.VIEW_PAYMENTS,
        Permission.VIEW_CREDIT_NOTES,
        Permission.VIEW_RECURRING_INVOICES,
    },
}


def role_has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())
