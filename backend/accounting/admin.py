from django.contrib import admin

from accounting.models.account import Account
from accounting.models.journal import JournalEntry, JournalLine


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "account_type", "organization", "is_active", "is_system"]
    list_filter = ["account_type", "is_active", "is_system"]
    search_fields = ["code", "name"]


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ["journal_number", "organization", "posting_date", "status", "reference"]
    list_filter = ["status", "source_type"]
    search_fields = ["journal_number", "reference"]
    inlines = [JournalLineInline]
