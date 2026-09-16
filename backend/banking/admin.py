from django.contrib import admin

from banking.models.bank_account import BankAccount
from banking.models.match import BankTransactionMatch
from banking.models.reconciliation import BankReconciliation
from banking.models.rule import BankRule
from banking.models.statement import BankTransaction, StatementImport
from banking.models.transfer import BankTransfer


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    # `account_number_last4` is deliberately absent from list_display and
    # search_fields: the admin changelist is the widest-audience view of this
    # data in the product, and the masked digits add nothing there that
    # `name` does not already give.
    list_display = ["name", "kind", "organization", "account", "currency", "is_active"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "bank_name"]


@admin.register(StatementImport)
class StatementImportAdmin(admin.ModelAdmin):
    list_display = [
        "created_at", "bank_account", "source_format", "status",
        "rows_read", "rows_imported", "rows_skipped_duplicate",
    ]
    list_filter = ["source_format", "status"]


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ["transaction_date", "bank_account", "amount", "status", "counterparty_name"]
    list_filter = ["status"]
    search_fields = ["description", "bank_reference", "counterparty_name"]
    date_hierarchy = "transaction_date"


@admin.register(BankTransactionMatch)
class BankTransactionMatchAdmin(admin.ModelAdmin):
    list_display = ["transaction", "match_type", "suggestion_source", "amount", "confidence", "is_confirmed"]
    list_filter = ["match_type", "suggestion_source", "is_confirmed"]


@admin.register(BankTransfer)
class BankTransferAdmin(admin.ModelAdmin):
    list_display = [
        "transfer_number", "transfer_date", "from_bank_account", "to_bank_account", "amount", "status",
    ]
    list_filter = ["status"]
    search_fields = ["transfer_number", "reference"]


@admin.register(BankRule)
class BankRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "priority", "organization", "action", "direction", "auto_confirm", "is_active"]
    list_filter = ["action", "direction", "auto_confirm", "is_active"]
    search_fields = ["name", "description_contains"]


@admin.register(BankReconciliation)
class BankReconciliationAdmin(admin.ModelAdmin):
    list_display = [
        "bank_account", "statement_start_date", "statement_end_date",
        "statement_closing_balance", "cleared_balance", "status",
    ]
    list_filter = ["status"]
