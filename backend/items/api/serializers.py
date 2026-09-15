from rest_framework import serializers

from items.models.item import Item
from items.models.unit import UnitOfMeasure
from items.services.items import create_item, update_item
from items.services.units import create_unit, update_unit


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = ["id", "code", "name", "symbol", "is_active", "is_system", "created_at", "updated_at"]
        read_only_fields = ["id", "is_system", "created_at", "updated_at"]

    def create(self, validated_data):
        request = self.context["request"]
        return create_unit(organization=request.organization, actor=request.user, **validated_data)

    def update(self, instance, validated_data):
        request = self.context["request"]
        return update_unit(unit=instance, actor=request.user, **validated_data)


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = [
            "id", "item_type", "name", "sku", "description", "unit", "is_active", "is_sellable",
            "is_purchasable", "track_inventory", "sales_price", "sales_account", "purchase_price",
            "purchase_account", "inventory_account", "cogs_account", "reorder_level", "hsn_sac_code",
            "tax_category", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        request = self.context["request"]
        return create_item(organization=request.organization, actor=request.user, **validated_data)

    def update(self, instance, validated_data):
        request = self.context["request"]
        return update_item(item=instance, actor=request.user, **validated_data)
