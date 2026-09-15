from django.contrib import admin

from inventory.models.settings import InventorySettings
from inventory.models.stock_adjustment import StockAdjustment, StockAdjustmentLine
from inventory.models.stock_movement import StockMovement
from inventory.models.warehouse import Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "organization", "is_active", "is_default"]
    list_filter = ["is_active", "is_default"]
    search_fields = ["code", "name"]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["item", "warehouse", "movement_type", "quantity", "unit_cost", "movement_date", "organization"]
    list_filter = ["movement_type"]


class StockAdjustmentLineInline(admin.TabularInline):
    model = StockAdjustmentLine
    extra = 0


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ["id", "warehouse", "adjustment_date", "reason", "status", "organization"]
    list_filter = ["status", "reason"]
    inlines = [StockAdjustmentLineInline]


@admin.register(InventorySettings)
class InventorySettingsAdmin(admin.ModelAdmin):
    list_display = ["organization", "allow_negative_stock"]
