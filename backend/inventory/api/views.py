from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from authz.permissions import HasOrgPermission
from authz.roles import Permission
from core.exceptions import ApplicationError
from core.views import OrganizationScopedMixin
from inventory.api.serializers import (
    StockAdjustmentCreateSerializer,
    StockAdjustmentLineInputSerializer,
    StockAdjustmentSerializer,
    StockMovementSerializer,
    StockTransferSerializer,
    WarehouseSerializer,
)
from inventory.models.stock_adjustment import AdjustmentStatus, StockAdjustment
from inventory.models.stock_movement import StockMovement
from inventory.models.warehouse import Warehouse
from inventory.selectors import get_low_stock_items, get_stock_on_hand
from inventory.services.adjustments import post_stock_adjustment, replace_draft_adjustment_lines
from items.models.item import Item


class WarehouseListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = WarehouseSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_INVENTORY if self.request.method == "GET" else Permission.MANAGE_WAREHOUSES

    def get_queryset(self):
        # Not a class-level `queryset = ...` attribute — see core/CLAUDE.md /
        # the note at the top of this file.
        return Warehouse.objects.all()


class WarehouseDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = WarehouseSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_INVENTORY if self.request.method == "GET" else Permission.MANAGE_WAREHOUSES

    def get_queryset(self):
        return Warehouse.objects.all()


class StockSummaryView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVENTORY

    def get(self, request):
        low_stock_only = request.query_params.get("low_stock_only") == "true"
        warehouse = None
        warehouse_id = request.query_params.get("warehouse_id")
        if warehouse_id:
            warehouse = _get_or_404(Warehouse, warehouse_id, "Warehouse")

        if low_stock_only:
            rows = get_low_stock_items(organization=request.organization, warehouse=warehouse)
            return Response(
                [
                    {
                        "item_id": row["item"].id,
                        "item_name": row["item"].name,
                        "on_hand": str(row["on_hand"]),
                        "reorder_level": str(row["reorder_level"]),
                    }
                    for row in rows
                ]
            )

        items = Item.objects.filter(track_inventory=True)
        item_id = request.query_params.get("item_id")
        if item_id:
            items = items.filter(id=item_id)

        results = []
        for item in items:
            on_hand = get_stock_on_hand(item=item, warehouse=warehouse)
            results.append(
                {
                    "item_id": item.id,
                    "item_name": item.name,
                    "warehouse_id": warehouse.id if warehouse else None,
                    "on_hand": str(on_hand),
                    "reorder_level": str(item.reorder_level),
                    "is_low_stock": item.reorder_level > 0 and on_hand <= item.reorder_level,
                }
            )
        return Response(results)


class StockMovementListView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_INVENTORY
    serializer_class = StockMovementSerializer

    def get_queryset(self):
        qs = StockMovement.objects.select_related("item", "warehouse")
        for param, field in (("item_id", "item_id"), ("warehouse_id", "warehouse_id"), ("movement_type", "movement_type")):
            value = self.request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})
        return qs


class StockAdjustmentListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_INVENTORY if self.request.method == "GET" else Permission.ADJUST_INVENTORY

    def get_serializer_class(self):
        return StockAdjustmentCreateSerializer if self.request.method == "POST" else StockAdjustmentSerializer

    def get_queryset(self):
        qs = StockAdjustment.objects.prefetch_related("lines")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        adjustment = serializer.save()
        return Response(StockAdjustmentSerializer(adjustment).data, status=201)


class StockAdjustmentDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = StockAdjustmentSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_INVENTORY if self.request.method == "GET" else Permission.ADJUST_INVENTORY

    def get_queryset(self):
        return StockAdjustment.objects.prefetch_related("lines")

    def update(self, request, *args, **kwargs):
        adjustment = self.get_object()
        if adjustment.status != AdjustmentStatus.DRAFT:
            raise ApplicationError("Only draft stock adjustments can be modified.", code="adjustment_not_draft")

        if "lines" in request.data:
            lines_serializer = StockAdjustmentLineInputSerializer(data=request.data["lines"], many=True)
            lines_serializer.is_valid(raise_exception=True)
            item_ids = {line["item_id"] for line in lines_serializer.validated_data}
            items_by_id = {i.id: i for i in Item.objects.filter(id__in=item_ids)}
            if len(items_by_id) != len(item_ids):
                raise ApplicationError("One or more items were not found.", code="item_not_found", status_code=404)
            lines = [
                {**line, "item": items_by_id[line["item_id"]]} for line in lines_serializer.validated_data
            ]
            replace_draft_adjustment_lines(adjustment=adjustment, lines=lines)

        header_data = {k: v for k, v in request.data.items() if k in ("adjustment_date", "memo", "reason")}
        if header_data:
            header_serializer = self.get_serializer(adjustment, data=header_data, partial=True)
            header_serializer.is_valid(raise_exception=True)
            header_serializer.save()

        adjustment.refresh_from_db()
        return Response(StockAdjustmentSerializer(adjustment).data)


class StockAdjustmentPostView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.ADJUST_INVENTORY

    def post(self, request, pk):
        adjustment = post_stock_adjustment(adjustment_id=pk, organization=request.organization, actor=request.user)
        return Response(StockAdjustmentSerializer(adjustment).data)


class StockTransferView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.TRANSFER_INVENTORY

    def post(self, request):
        serializer = StockTransferSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(
            {
                "transfer_id": result["transfer_id"],
                "out_movement": StockMovementSerializer(result["out_movement"]).data,
                "in_movement": StockMovementSerializer(result["in_movement"]).data,
            },
            status=201,
        )


def _get_or_404(model, pk, label: str):
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist:
        raise ApplicationError(f"{label} not found.", code=f"{label.lower()}_not_found", status_code=404)
