from rest_framework import serializers

from core.exceptions import ApplicationError
from inventory.models.stock_adjustment import StockAdjustment, StockAdjustmentLine
from inventory.models.stock_movement import StockMovement
from inventory.models.warehouse import Warehouse
from inventory.services.adjustments import create_draft_adjustment
from inventory.services.transfers import transfer_stock
from inventory.services.warehouses import create_warehouse, update_warehouse
from items.models.item import Item


def _get_or_404(model, pk, label: str):
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist:
        raise ApplicationError(f"{label} not found.", code=f"{label.lower()}_not_found", status_code=404)


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ["id", "code", "name", "address", "is_active", "is_default", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        request = self.context["request"]
        return create_warehouse(organization=request.organization, actor=request.user, **validated_data)

    def update(self, instance, validated_data):
        request = self.context["request"]
        return update_warehouse(warehouse=instance, actor=request.user, **validated_data)


class StockMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockMovement
        fields = [
            "id", "item", "warehouse", "movement_type", "quantity", "unit_cost", "movement_date",
            "source_type", "source_id", "notes", "created_by", "created_at",
        ]
        read_only_fields = fields


class StockAdjustmentLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockAdjustmentLine
        fields = ["id", "item", "line_number", "direction", "quantity", "unit_cost", "notes"]
        read_only_fields = fields


class StockAdjustmentLineInputSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    direction = serializers.ChoiceField(choices=["adjustment_in", "adjustment_out"])
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class StockAdjustmentSerializer(serializers.ModelSerializer):
    lines = StockAdjustmentLineSerializer(many=True, read_only=True)

    class Meta:
        model = StockAdjustment
        fields = [
            "id", "warehouse", "adjustment_date", "reason", "memo", "status", "contra_account",
            "accounting_journal", "reverses", "created_by", "posted_by", "posted_at", "lines",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "accounting_journal", "reverses", "created_by", "posted_by", "posted_at",
            "lines", "created_at", "updated_at",
        ]


class StockAdjustmentCreateSerializer(serializers.Serializer):
    warehouse_id = serializers.UUIDField()
    adjustment_date = serializers.DateField()
    reason = serializers.ChoiceField(choices=["physical_count", "damaged", "shrinkage", "found", "correction", "other"])
    memo = serializers.CharField(required=False, allow_blank=True, default="")
    contra_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    lines = StockAdjustmentLineInputSerializer(many=True)

    def create(self, validated_data):
        from accounting.models.account import Account

        request = self.context["request"]
        warehouse = _get_or_404(Warehouse, validated_data.pop("warehouse_id"), "Warehouse")
        contra_account_id = validated_data.pop("contra_account_id")
        contra_account = _get_or_404(Account, contra_account_id, "Account") if contra_account_id else None

        raw_lines = validated_data.pop("lines")
        item_ids = {line["item_id"] for line in raw_lines}
        items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
        if len(items_by_id) != len(item_ids):
            raise ApplicationError("One or more items were not found in this organization.", code="item_not_found", status_code=404)
        lines = [
            {
                "item": items_by_id[line["item_id"]],
                "direction": line["direction"],
                "quantity": line["quantity"],
                "unit_cost": line["unit_cost"],
                "notes": line.get("notes", ""),
            }
            for line in raw_lines
        ]

        return create_draft_adjustment(
            organization=request.organization, warehouse=warehouse, contra_account=contra_account,
            created_by=request.user, lines=lines, **validated_data,
        )


class StockTransferSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    from_warehouse_id = serializers.UUIDField()
    to_warehouse_id = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, **kwargs):
        request = self.context["request"]
        data = self.validated_data
        item = _get_or_404(Item, data["item_id"], "Item")
        from_warehouse = _get_or_404(Warehouse, data["from_warehouse_id"], "Warehouse")
        to_warehouse = _get_or_404(Warehouse, data["to_warehouse_id"], "Warehouse")
        return transfer_stock(
            organization=request.organization, item=item, from_warehouse=from_warehouse,
            to_warehouse=to_warehouse, quantity=data["quantity"], notes=data.get("notes", ""), actor=request.user,
        )
