from django.contrib import admin

from sales.models.credit_note import CreditNote
from sales.models.customer import Customer
from sales.models.delivery import DeliveryChallan
from sales.models.invoice import Invoice
from sales.models.payment import CustomerPayment
from sales.models.quote import Quote
from sales.models.recurring_invoice import RecurringInvoiceTemplate
from sales.models.sales_order import SalesOrder


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["customer_code", "display_name", "organization", "is_active", "currency"]
    list_filter = ["is_active"]
    search_fields = ["customer_code", "display_name", "email"]


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ["quote_number", "customer", "organization", "status", "issue_date", "total"]
    list_filter = ["status"]
    search_fields = ["quote_number"]


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "customer", "organization", "status", "order_date", "total"]
    list_filter = ["status"]
    search_fields = ["order_number"]


@admin.register(DeliveryChallan)
class DeliveryChallanAdmin(admin.ModelAdmin):
    list_display = ["challan_number", "customer", "organization", "status", "challan_date", "warehouse"]
    list_filter = ["status"]
    search_fields = ["challan_number"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["invoice_number", "customer", "organization", "status", "invoice_date", "due_date", "total"]
    list_filter = ["status"]
    search_fields = ["invoice_number"]


@admin.register(CustomerPayment)
class CustomerPaymentAdmin(admin.ModelAdmin):
    list_display = ["payment_number", "customer", "organization", "payment_date", "amount", "payment_method"]
    list_filter = ["payment_method"]
    search_fields = ["payment_number"]


@admin.register(CreditNote)
class CreditNoteAdmin(admin.ModelAdmin):
    list_display = ["credit_note_number", "customer", "organization", "status", "credit_note_date", "total"]
    list_filter = ["status", "reason"]
    search_fields = ["credit_note_number"]


@admin.register(RecurringInvoiceTemplate)
class RecurringInvoiceTemplateAdmin(admin.ModelAdmin):
    list_display = ["customer", "organization", "frequency", "next_run_at", "is_active"]
    list_filter = ["frequency", "is_active"]
