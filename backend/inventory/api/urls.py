from django.urls import path

from inventory.api.views import (
    StockAdjustmentDetailView,
    StockAdjustmentListCreateView,
    StockAdjustmentPostView,
    StockMovementListView,
    StockSummaryView,
    StockTransferView,
    WarehouseDetailView,
    WarehouseListCreateView,
)

urlpatterns = [
    path("inventory/warehouses/", WarehouseListCreateView.as_view(), name="inventory-warehouse-list-create"),
    path("inventory/warehouses/<uuid:pk>/", WarehouseDetailView.as_view(), name="inventory-warehouse-detail"),
    path("inventory/stock-summary/", StockSummaryView.as_view(), name="inventory-stock-summary"),
    path("inventory/movements/", StockMovementListView.as_view(), name="inventory-movement-list"),
    path("inventory/adjustments/", StockAdjustmentListCreateView.as_view(), name="inventory-adjustment-list-create"),
    path("inventory/adjustments/<uuid:pk>/", StockAdjustmentDetailView.as_view(), name="inventory-adjustment-detail"),
    path("inventory/adjustments/<uuid:pk>/post/", StockAdjustmentPostView.as_view(), name="inventory-adjustment-post"),
    path("inventory/transfers/", StockTransferView.as_view(), name="inventory-transfer"),
]
