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

    def test_viewer_is_read_only_across_purchases(self):
        for view_permission, write_permission in [
            (Permission.VIEW_VENDORS, Permission.MANAGE_VENDORS),
            (Permission.VIEW_PURCHASE_ORDERS, Permission.MANAGE_PURCHASE_ORDERS),
            (Permission.VIEW_GOODS_RECEIPTS, Permission.MANAGE_GOODS_RECEIPTS),
            (Permission.VIEW_BILLS, Permission.CREATE_BILL),
            (Permission.VIEW_EXPENSES, Permission.MANAGE_EXPENSES),
            (Permission.VIEW_VENDOR_PAYMENTS, Permission.RECORD_VENDOR_PAYMENT),
            (Permission.VIEW_VENDOR_CREDITS, Permission.ISSUE_VENDOR_CREDIT),
            (Permission.VIEW_RECURRING_BILLS, Permission.MANAGE_RECURRING_BILLS),
        ]:
            self.assertTrue(role_has_permission(Role.VIEWER, view_permission), view_permission)
            self.assertFalse(role_has_permission(Role.VIEWER, write_permission), write_permission)
        self.assertFalse(role_has_permission(Role.VIEWER, Permission.POST_BILL))
        self.assertFalse(role_has_permission(Role.VIEWER, Permission.VOID_BILL))

    def test_staff_may_not_pay_a_vendor(self):
        """The one deliberate asymmetry between the sales and purchases
        grants for this role. Staff may record an incoming CustomerPayment
        (money arriving is self-evidencing - the funds are already in the
        account) but not an outgoing VendorPayment, which authorises money
        LEAVING the organization. That is the segregation-of-duties line and
        the single highest-value privilege in the purchases module, so it
        stops at Accountant. If this assertion is ever relaxed, that must be
        a deliberate decision, not a drive-by edit to ROLE_PERMISSIONS."""
        self.assertTrue(role_has_permission(Role.STAFF, Permission.RECORD_PAYMENT))
        self.assertFalse(role_has_permission(Role.STAFF, Permission.RECORD_VENDOR_PAYMENT))
        self.assertTrue(role_has_permission(Role.ACCOUNTANT, Permission.RECORD_VENDOR_PAYMENT))

    def test_staff_retains_every_other_purchase_permission(self):
        for permission in [
            Permission.MANAGE_VENDORS,
            Permission.MANAGE_PURCHASE_ORDERS,
            Permission.MANAGE_GOODS_RECEIPTS,
            Permission.CREATE_BILL,
            Permission.POST_BILL,
            Permission.VOID_BILL,
            Permission.MANAGE_EXPENSES,
            Permission.ISSUE_VENDOR_CREDIT,
            Permission.MANAGE_RECURRING_BILLS,
        ]:
            self.assertTrue(role_has_permission(Role.STAFF, permission), permission)

    def test_accountant_has_the_full_purchase_set(self):
        for permission in [
            Permission.MANAGE_VENDORS,
            Permission.MANAGE_PURCHASE_ORDERS,
            Permission.MANAGE_GOODS_RECEIPTS,
            Permission.CREATE_BILL,
            Permission.POST_BILL,
            Permission.VOID_BILL,
            Permission.MANAGE_EXPENSES,
            Permission.RECORD_VENDOR_PAYMENT,
            Permission.ISSUE_VENDOR_CREDIT,
            Permission.MANAGE_RECURRING_BILLS,
        ]:
            self.assertTrue(role_has_permission(Role.ACCOUNTANT, permission), permission)

    def test_staff_log_their_own_time_but_neither_approve_nor_bill_it(self):
        """Three separate controls, each deliberate:

        LOG_TIME  - staff record hours (object-level scoping in
                    projects/api/views.py limits that to their own).
        APPROVE_TIME - withheld: nobody approves their own timesheet, the
                    oldest control in time-and-materials billing.
        INVOICE_TIME - withheld: billing a customer is a financial act, in
                    the same class as RECORD_VENDOR_PAYMENT.
        VIEW_ALL_TIMESHEETS - withheld: an entry carries cost_rate, an
                    indirect read on what a colleague is paid.
        """
        self.assertTrue(role_has_permission(Role.STAFF, Permission.LOG_TIME))
        self.assertFalse(role_has_permission(Role.STAFF, Permission.APPROVE_TIME))
        self.assertFalse(role_has_permission(Role.STAFF, Permission.INVOICE_TIME))
        self.assertFalse(role_has_permission(Role.STAFF, Permission.VIEW_ALL_TIMESHEETS))
        # They can still run projects.
        self.assertTrue(role_has_permission(Role.STAFF, Permission.MANAGE_PROJECTS))

    def test_accountant_has_the_full_project_set(self):
        for permission in [
            Permission.VIEW_PROJECTS,
            Permission.MANAGE_PROJECTS,
            Permission.VIEW_ALL_TIMESHEETS,
            Permission.LOG_TIME,
            Permission.APPROVE_TIME,
            Permission.INVOICE_TIME,
        ]:
            self.assertTrue(role_has_permission(Role.ACCOUNTANT, permission), permission)

    def test_viewer_reads_projects_and_timesheets_but_writes_nothing(self):
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_PROJECTS))
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_ALL_TIMESHEETS))
        for permission in [
            Permission.MANAGE_PROJECTS,
            Permission.LOG_TIME,
            Permission.APPROVE_TIME,
            Permission.INVOICE_TIME,
        ]:
            self.assertFalse(role_has_permission(Role.VIEWER, permission), permission)


class BankingPermissionTests(SimpleTestCase):
    """Phase 6. The line drawn here is between SEEING the bank and DECIDING
    what the books say about it."""

    def test_staff_read_the_bank_but_do_not_reconcile_or_move_money(self):
        """Statement visibility follows the existing VIEW_ACCOUNTING grant
        rather than inventing a stricter tier. Staff already read the General
        Ledger, which contains every bank journal, so withholding the
        statement would be theatre — while reconciling (which certifies a
        balance), importing, rule management (an auto-confirming rule posts
        journals unattended) and transfers (which move money) are genuine
        authority and stop here.
        """
        self.assertTrue(role_has_permission(Role.STAFF, Permission.VIEW_BANK_ACCOUNTS))
        self.assertTrue(role_has_permission(Role.STAFF, Permission.VIEW_BANK_TRANSACTIONS))
        for permission in [
            Permission.MANAGE_BANK_ACCOUNTS,
            Permission.IMPORT_BANK_STATEMENT,
            Permission.RECONCILE_BANK,
            Permission.MANAGE_BANK_RULES,
            Permission.RECORD_BANK_TRANSFER,
        ]:
            self.assertFalse(role_has_permission(Role.STAFF, permission), permission)

    def test_recording_a_transfer_sits_with_recording_a_vendor_payment(self):
        """Both authorise money leaving an account, so both stop at
        Accountant. Widening either must be a deliberate decision."""
        for role in (Role.OWNER, Role.ADMIN, Role.ACCOUNTANT):
            self.assertTrue(role_has_permission(role, Permission.RECORD_BANK_TRANSFER), role)
            self.assertTrue(role_has_permission(role, Permission.RECORD_VENDOR_PAYMENT), role)
        for role in (Role.STAFF, Role.VIEWER):
            self.assertFalse(role_has_permission(role, Permission.RECORD_BANK_TRANSFER), role)
            self.assertFalse(role_has_permission(role, Permission.RECORD_VENDOR_PAYMENT), role)

    def test_accountant_has_the_full_banking_set(self):
        for permission in [
            Permission.VIEW_BANK_ACCOUNTS,
            Permission.MANAGE_BANK_ACCOUNTS,
            Permission.VIEW_BANK_TRANSACTIONS,
            Permission.IMPORT_BANK_STATEMENT,
            Permission.RECONCILE_BANK,
            Permission.MANAGE_BANK_RULES,
            Permission.RECORD_BANK_TRANSFER,
        ]:
            self.assertTrue(role_has_permission(Role.ACCOUNTANT, permission), permission)

    def test_viewer_reads_the_bank_and_writes_nothing(self):
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_BANK_ACCOUNTS))
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_BANK_TRANSACTIONS))
        for permission in [
            Permission.MANAGE_BANK_ACCOUNTS,
            Permission.IMPORT_BANK_STATEMENT,
            Permission.RECONCILE_BANK,
            Permission.MANAGE_BANK_RULES,
            Permission.RECORD_BANK_TRANSFER,
        ]:
            self.assertFalse(role_has_permission(Role.VIEWER, permission), permission)


class TaxCompliancePermissionTests(SimpleTestCase):
    """Phase 7. The line drawn here is between SHIPPING (staff can) and
    FILING with the government (only the Accountant can) — the e-way bill
    split is the clearest case: generating one moves goods, cancelling one
    corrects a government filing."""

    def test_staff_read_tax_configuration_and_returns_but_do_not_manage_them(self):
        self.assertTrue(role_has_permission(Role.STAFF, Permission.VIEW_TAX_SETTINGS))
        self.assertTrue(role_has_permission(Role.STAFF, Permission.VIEW_TAX_RATES))
        self.assertTrue(role_has_permission(Role.STAFF, Permission.VIEW_RETURNS))
        for permission in [
            Permission.MANAGE_TAX_SETTINGS,
            Permission.MANAGE_TAX_RATES,
        ]:
            self.assertFalse(role_has_permission(Role.STAFF, permission), permission)

    def test_staff_may_generate_an_ewaybill_but_not_report_to_the_irp(self):
        """Generating an e-way bill is a shipping act — the same class as
        MANAGE_DELIVERIES, which Staff already holds. Reporting an invoice to
        the IRP is a government filing act that creates a legal record (an
        IRN) expensive to unwind, so it stops at Accountant along with
        RECORD_VENDOR_PAYMENT and RECORD_BANK_TRANSFER."""
        self.assertTrue(role_has_permission(Role.STAFF, Permission.GENERATE_EWAYBILL))
        for permission in [
            Permission.GENERATE_EINVOICE,
            Permission.CANCEL_EINVOICE,
            Permission.CANCEL_EWAYBILL,
        ]:
            self.assertFalse(role_has_permission(Role.STAFF, permission), permission)

    def test_accountant_has_the_full_tax_compliance_set(self):
        for permission in [
            Permission.VIEW_TAX_SETTINGS,
            Permission.MANAGE_TAX_SETTINGS,
            Permission.VIEW_TAX_RATES,
            Permission.MANAGE_TAX_RATES,
            Permission.VIEW_RETURNS,
            Permission.GENERATE_EINVOICE,
            Permission.CANCEL_EINVOICE,
            Permission.GENERATE_EWAYBILL,
            Permission.CANCEL_EWAYBILL,
        ]:
            self.assertTrue(role_has_permission(Role.ACCOUNTANT, permission), permission)

    def test_viewer_reads_tax_configuration_and_returns_but_writes_and_files_nothing(self):
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_TAX_SETTINGS))
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_TAX_RATES))
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_RETURNS))
        for permission in [
            Permission.MANAGE_TAX_SETTINGS,
            Permission.MANAGE_TAX_RATES,
            Permission.GENERATE_EINVOICE,
            Permission.CANCEL_EINVOICE,
            Permission.GENERATE_EWAYBILL,
            Permission.CANCEL_EWAYBILL,
        ]:
            self.assertFalse(role_has_permission(Role.VIEWER, permission), permission)


class DocumentsPermissionTests(SimpleTestCase):
    """Phase 9. Approving an OCR review can auto-complete an extraction the
    same way POST_INVOICE finalizes a document, and deleting/archiving a
    document may remove the only evidence behind a posted transaction — both
    stay with the Accountant, the same line as RECORD_VENDOR_PAYMENT."""

    def test_staff_can_upload_and_download_but_not_manage_review_or_delete(self):
        self.assertTrue(role_has_permission(Role.STAFF, Permission.VIEW_DOCUMENTS))
        self.assertTrue(role_has_permission(Role.STAFF, Permission.UPLOAD_DOCUMENT))
        self.assertTrue(role_has_permission(Role.STAFF, Permission.DOWNLOAD_DOCUMENT))
        for permission in [
            Permission.MANAGE_DOCUMENTS,
            Permission.REVIEW_DOCUMENT_OCR,
            Permission.DELETE_DOCUMENT,
        ]:
            self.assertFalse(role_has_permission(Role.STAFF, permission), permission)

    def test_accountant_has_the_full_document_set(self):
        for permission in [
            Permission.VIEW_DOCUMENTS,
            Permission.UPLOAD_DOCUMENT,
            Permission.DOWNLOAD_DOCUMENT,
            Permission.MANAGE_DOCUMENTS,
            Permission.REVIEW_DOCUMENT_OCR,
            Permission.DELETE_DOCUMENT,
        ]:
            self.assertTrue(role_has_permission(Role.ACCOUNTANT, permission), permission)

    def test_viewer_reads_and_downloads_but_writes_nothing(self):
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.VIEW_DOCUMENTS))
        self.assertTrue(role_has_permission(Role.VIEWER, Permission.DOWNLOAD_DOCUMENT))
        for permission in [
            Permission.UPLOAD_DOCUMENT,
            Permission.MANAGE_DOCUMENTS,
            Permission.REVIEW_DOCUMENT_OCR,
            Permission.DELETE_DOCUMENT,
        ]:
            self.assertFalse(role_has_permission(Role.VIEWER, permission), permission)
