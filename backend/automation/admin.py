from django.contrib import admin

from automation.models.rule import AutomationRule


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "trigger_type", "status", "version", "priority"]
    list_filter = ["status", "trigger_type"]
    search_fields = ["name"]
