from django.urls import path

from items.api.views import (
    ItemDetailView,
    ItemListCreateView,
    UnitOfMeasureDetailView,
    UnitOfMeasureListCreateView,
)

urlpatterns = [
    path("items/units/", UnitOfMeasureListCreateView.as_view(), name="items-unit-list-create"),
    path("items/units/<uuid:pk>/", UnitOfMeasureDetailView.as_view(), name="items-unit-detail"),
    path("items/", ItemListCreateView.as_view(), name="items-item-list-create"),
    path("items/<uuid:pk>/", ItemDetailView.as_view(), name="items-item-detail"),
]
