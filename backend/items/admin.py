from django.contrib import admin

from items.models.hsn_sac import HsnSacCode
from items.models.item import Item
from items.models.unit import UnitOfMeasure


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "organization", "is_active", "is_system"]
    list_filter = ["is_active", "is_system"]
    search_fields = ["code", "name"]


@admin.register(HsnSacCode)
class HsnSacCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "classification", "organization", "is_active"]
    list_filter = ["classification", "is_active"]
    search_fields = ["code", "description"]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["sku", "name", "item_type", "organization", "is_active", "track_inventory"]
    list_filter = ["item_type", "is_active", "track_inventory"]
    search_fields = ["sku", "name"]
