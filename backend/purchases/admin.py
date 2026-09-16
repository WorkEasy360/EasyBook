from django.contrib import admin

from purchases.models.bill import Bill
from purchases.models.expense import Expense
from purchases.models.goods_receipt import GoodsReceipt
from purchases.models.payment import VendorPayment
from purchases.models.purchase_order import PurchaseOrder
from purchases.models.recurring import RecurringBillTemplate, RecurringExpenseTemplate
from purchases.models.vendor import Vendor
from purchases.models.vendor_credit import VendorCredit


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ["vendor_code", "display_name", "organization", "is_active", "currency"]
    list_filter = ["is_active"]
    search_fields = ["vendor_code", "display_name", "email"]


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "vendor", "organization", "status", "order_date", "total"]
    list_filter = ["status"]
    search_fields = ["order_number"]


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ["receipt_number", "vendor", "organization", "status", "receipt_date", "warehouse"]
    list_filter = ["status"]
    search_fields = ["receipt_number", "vendor_document_number"]


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ["bill_number", "vendor_bill_number", "vendor", "organization", "status", "bill_date", "total"]
    list_filter = ["status"]
    search_fields = ["bill_number", "vendor_bill_number"]


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ["expense_number", "vendor", "organization", "status", "expense_date", "total", "is_billable"]
    list_filter = ["status", "is_billable"]
    search_fields = ["expense_number", "reference"]


@admin.register(VendorPayment)
class VendorPaymentAdmin(admin.ModelAdmin):
    list_display = ["payment_number", "vendor", "organization", "payment_date", "amount", "payment_method"]
    search_fields = ["payment_number", "reference"]


@admin.register(VendorCredit)
class VendorCreditAdmin(admin.ModelAdmin):
    list_display = ["credit_number", "vendor", "organization", "status", "credit_date", "total"]
    list_filter = ["status", "reason"]
    search_fields = ["credit_number", "vendor_credit_number"]


@admin.register(RecurringBillTemplate)
class RecurringBillTemplateAdmin(admin.ModelAdmin):
    list_display = ["id", "vendor", "organization", "frequency", "next_run_at", "is_active"]
    list_filter = ["frequency", "is_active"]


@admin.register(RecurringExpenseTemplate)
class RecurringExpenseTemplateAdmin(admin.ModelAdmin):
    list_display = ["id", "vendor", "organization", "frequency", "next_run_at", "amount", "is_active"]
    list_filter = ["frequency", "is_active"]
