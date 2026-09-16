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
    VIEW_VENDORS = "purchases.view_vendors"
    MANAGE_VENDORS = "purchases.manage_vendors"
    VIEW_PURCHASE_ORDERS = "purchases.view_purchase_orders"
    MANAGE_PURCHASE_ORDERS = "purchases.manage_purchase_orders"
    VIEW_GOODS_RECEIPTS = "purchases.view_goods_receipts"
    MANAGE_GOODS_RECEIPTS = "purchases.manage_goods_receipts"
    VIEW_BILLS = "purchases.view_bills"
    CREATE_BILL = "purchases.create_bill"
    POST_BILL = "purchases.post_bill"
    VOID_BILL = "purchases.void_bill"
    VIEW_EXPENSES = "purchases.view_expenses"
    MANAGE_EXPENSES = "purchases.manage_expenses"
    VIEW_VENDOR_PAYMENTS = "purchases.view_vendor_payments"
    RECORD_VENDOR_PAYMENT = "purchases.record_vendor_payment"
    VIEW_VENDOR_CREDITS = "purchases.view_vendor_credits"
    ISSUE_VENDOR_CREDIT = "purchases.issue_vendor_credit"
    VIEW_RECURRING_BILLS = "purchases.view_recurring_bills"
    MANAGE_RECURRING_BILLS = "purchases.manage_recurring_bills"
    VIEW_PROJECTS = "projects.view_projects"
    MANAGE_PROJECTS = "projects.manage_projects"
    VIEW_ALL_TIMESHEETS = "projects.view_all_timesheets"
    LOG_TIME = "projects.log_time"
    APPROVE_TIME = "projects.approve_time"
    INVOICE_TIME = "projects.invoice_time"
    VIEW_BANK_ACCOUNTS = "banking.view_bank_accounts"
    MANAGE_BANK_ACCOUNTS = "banking.manage_bank_accounts"
    VIEW_BANK_TRANSACTIONS = "banking.view_transactions"
    IMPORT_BANK_STATEMENT = "banking.import_statement"
    RECONCILE_BANK = "banking.reconcile"
    MANAGE_BANK_RULES = "banking.manage_rules"
    RECORD_BANK_TRANSFER = "banking.record_transfer"
    VIEW_TAX_SETTINGS = "tax.view_settings"
    MANAGE_TAX_SETTINGS = "tax.manage_settings"
    VIEW_TAX_RATES = "tax.view_rates"
    MANAGE_TAX_RATES = "tax.manage_rates"
    VIEW_RETURNS = "compliance.view_returns"
    GENERATE_EINVOICE = "compliance.generate_einvoice"
    CANCEL_EINVOICE = "compliance.cancel_einvoice"
    GENERATE_EWAYBILL = "compliance.generate_ewaybill"
    CANCEL_EWAYBILL = "compliance.cancel_ewaybill"


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
    Permission.VIEW_VENDORS,
    Permission.MANAGE_VENDORS,
    Permission.VIEW_PURCHASE_ORDERS,
    Permission.MANAGE_PURCHASE_ORDERS,
    Permission.VIEW_GOODS_RECEIPTS,
    Permission.MANAGE_GOODS_RECEIPTS,
    Permission.VIEW_BILLS,
    Permission.CREATE_BILL,
    Permission.POST_BILL,
    Permission.VOID_BILL,
    Permission.VIEW_EXPENSES,
    Permission.MANAGE_EXPENSES,
    Permission.VIEW_VENDOR_PAYMENTS,
    Permission.RECORD_VENDOR_PAYMENT,
    Permission.VIEW_VENDOR_CREDITS,
    Permission.ISSUE_VENDOR_CREDIT,
    Permission.VIEW_RECURRING_BILLS,
    Permission.MANAGE_RECURRING_BILLS,
    Permission.VIEW_PROJECTS,
    Permission.MANAGE_PROJECTS,
    Permission.VIEW_ALL_TIMESHEETS,
    Permission.LOG_TIME,
    Permission.APPROVE_TIME,
    Permission.INVOICE_TIME,
    Permission.VIEW_BANK_ACCOUNTS,
    Permission.MANAGE_BANK_ACCOUNTS,
    Permission.VIEW_BANK_TRANSACTIONS,
    Permission.IMPORT_BANK_STATEMENT,
    Permission.RECONCILE_BANK,
    Permission.MANAGE_BANK_RULES,
    Permission.RECORD_BANK_TRANSFER,
    Permission.VIEW_TAX_SETTINGS,
    Permission.MANAGE_TAX_SETTINGS,
    Permission.VIEW_TAX_RATES,
    Permission.MANAGE_TAX_RATES,
    Permission.VIEW_RETURNS,
    Permission.GENERATE_EINVOICE,
    Permission.CANCEL_EINVOICE,
    Permission.GENERATE_EWAYBILL,
    Permission.CANCEL_EWAYBILL,
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
        Permission.VIEW_VENDORS,
        Permission.MANAGE_VENDORS,
        Permission.VIEW_PURCHASE_ORDERS,
        Permission.MANAGE_PURCHASE_ORDERS,
        Permission.VIEW_GOODS_RECEIPTS,
        Permission.MANAGE_GOODS_RECEIPTS,
        Permission.VIEW_BILLS,
        Permission.CREATE_BILL,
        Permission.POST_BILL,
        Permission.VOID_BILL,
        Permission.VIEW_EXPENSES,
        Permission.MANAGE_EXPENSES,
        Permission.VIEW_VENDOR_PAYMENTS,
        Permission.RECORD_VENDOR_PAYMENT,
        Permission.VIEW_VENDOR_CREDITS,
        Permission.ISSUE_VENDOR_CREDIT,
        Permission.VIEW_RECURRING_BILLS,
        Permission.MANAGE_RECURRING_BILLS,
        Permission.VIEW_PROJECTS,
        Permission.MANAGE_PROJECTS,
        Permission.VIEW_ALL_TIMESHEETS,
        Permission.LOG_TIME,
        Permission.APPROVE_TIME,
        Permission.INVOICE_TIME,
        Permission.VIEW_BANK_ACCOUNTS,
        Permission.MANAGE_BANK_ACCOUNTS,
        Permission.VIEW_BANK_TRANSACTIONS,
        Permission.IMPORT_BANK_STATEMENT,
        Permission.RECONCILE_BANK,
        Permission.MANAGE_BANK_RULES,
        Permission.RECORD_BANK_TRANSFER,
        # The Accountant holds every tax/compliance permission: configuring
        # the GST profile and rate table, reading the registers and returns,
        # and generating/cancelling government filings are all the same kind
        # of act as MANAGE_ACCOUNTING/POST_INVOICE above — an authoritative
        # financial or filing decision, not a day-to-day sales/purchase task.
        Permission.VIEW_TAX_SETTINGS,
        Permission.MANAGE_TAX_SETTINGS,
        Permission.VIEW_TAX_RATES,
        Permission.MANAGE_TAX_RATES,
        Permission.VIEW_RETURNS,
        Permission.GENERATE_EINVOICE,
        Permission.CANCEL_EINVOICE,
        Permission.GENERATE_EWAYBILL,
        Permission.CANCEL_EWAYBILL,
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
        Permission.VIEW_VENDORS,
        Permission.MANAGE_VENDORS,
        Permission.VIEW_PURCHASE_ORDERS,
        Permission.MANAGE_PURCHASE_ORDERS,
        Permission.VIEW_GOODS_RECEIPTS,
        Permission.MANAGE_GOODS_RECEIPTS,
        Permission.VIEW_BILLS,
        Permission.CREATE_BILL,
        Permission.POST_BILL,
        Permission.VOID_BILL,
        Permission.VIEW_EXPENSES,
        Permission.MANAGE_EXPENSES,
        Permission.VIEW_VENDOR_PAYMENTS,
        # NOT Permission.RECORD_VENDOR_PAYMENT — the one deliberate
        # asymmetry between the sales and purchases grants for this role.
        # Staff may record an incoming CustomerPayment (money arriving is
        # self-evidencing: the funds are already in the account) but not an
        # outgoing VendorPayment, which authorises money LEAVING the
        # organization. That is the classic segregation-of-duties line and
        # the single highest-value privilege in this module, so it stops at
        # Accountant. Asserted by authz/tests/test_roles.py — if you widen
        # it, do so deliberately and update that test.
        Permission.VIEW_VENDOR_CREDITS,
        Permission.ISSUE_VENDOR_CREDIT,
        Permission.VIEW_RECURRING_BILLS,
        Permission.MANAGE_RECURRING_BILLS,
        Permission.VIEW_PROJECTS,
        Permission.MANAGE_PROJECTS,
        Permission.LOG_TIME,
        # NOT VIEW_ALL_TIMESHEETS: staff see their OWN time entries, which the
        # queryset scopes by user (see projects/api/views.py). Someone's hours
        # are a record of what they personally did and, via cost_rate, an
        # indirect read on what they are paid.
        # NOT APPROVE_TIME: nobody approves their own timesheet. That is the
        # oldest control in time-and-materials billing, and making it a
        # permission rather than a self-check keeps it enforceable at the API.
        # NOT INVOICE_TIME: billing a customer is a financial act, like
        # RECORD_VENDOR_PAYMENT.
        Permission.VIEW_BANK_ACCOUNTS,
        Permission.VIEW_BANK_TRANSACTIONS,
        # Statement lines are visible to this role for a reason worth stating,
        # because the instinct is to withhold them: Staff already holds
        # VIEW_ACCOUNTING and VIEW_TRANSACTIONS, so they can read the General
        # Ledger — which contains every bank journal already. Hiding the
        # statement while leaving the ledger open would be theatre, not a
        # control, and it would break the ordinary case of a bookkeeper
        # asking "is this the payment you meant?".
        # NOT IMPORT_BANK_STATEMENT / RECONCILE_BANK / MANAGE_BANK_RULES:
        # those decide what the books SAY, not merely what they show.
        # Reconciling is the act that certifies a balance, and an
        # auto-confirming rule posts journals unattended.
        # NOT RECORD_BANK_TRANSFER: it moves money, so it sits with
        # RECORD_VENDOR_PAYMENT on the Accountant side of the same line.
        Permission.VIEW_TAX_SETTINGS,
        Permission.VIEW_TAX_RATES,
        Permission.VIEW_RETURNS,
        Permission.GENERATE_EWAYBILL,
        # Generating an e-way bill is a shipping act — the same class as
        # MANAGE_DELIVERIES above, which Staff already holds — so it stays
        # here rather than moving to the Accountant line.
        # NOT GENERATE_EINVOICE / CANCEL_EINVOICE: reporting an invoice to the
        # IRP is a government FILING act, not a shipping one — it creates a
        # legal record (an IRN) that is expensive to unwind (cancellation is
        # only possible within a limited window on the portal). That puts it
        # with RECORD_VENDOR_PAYMENT and RECORD_BANK_TRANSFER on the
        # Accountant side of the line, not with MANAGE_DELIVERIES.
        # NOT CANCEL_EWAYBILL: unlike voiding an invoice or bill (an internal
        # accounting reversal this system fully controls), cancelling an
        # e-way bill reports the cancellation to the same government portal
        # generation does, and is only possible within a short window — a
        # filing correction, not a shipping one, so it stays with
        # CANCEL_EINVOICE on the Accountant side.
        # NOT MANAGE_TAX_SETTINGS / MANAGE_TAX_RATES: configuring the GST
        # profile, the chart-of-accounts tax mapping, and the rate table
        # decides how every future document is taxed — the same weight as
        # MANAGE_ACCOUNTING, which this role also does not hold.
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
        Permission.VIEW_VENDORS,
        Permission.VIEW_PURCHASE_ORDERS,
        Permission.VIEW_GOODS_RECEIPTS,
        Permission.VIEW_BILLS,
        Permission.VIEW_EXPENSES,
        Permission.VIEW_VENDOR_PAYMENTS,
        Permission.VIEW_VENDOR_CREDITS,
        Permission.VIEW_RECURRING_BILLS,
        Permission.VIEW_PROJECTS,
        Permission.VIEW_ALL_TIMESHEETS,
        Permission.VIEW_BANK_ACCOUNTS,
        Permission.VIEW_BANK_TRANSACTIONS,
        Permission.VIEW_TAX_SETTINGS,
        Permission.VIEW_TAX_RATES,
        Permission.VIEW_RETURNS,
    },
}


def role_has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())
