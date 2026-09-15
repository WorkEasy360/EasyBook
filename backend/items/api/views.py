from rest_framework import generics

from authz.permissions import HasOrgPermission
from authz.roles import Permission
from core.views import OrganizationScopedMixin
from items.api.serializers import ItemSerializer, UnitOfMeasureSerializer
from items.models.item import Item
from items.models.unit import UnitOfMeasure

# NOTE: every queryset here is built in get_queryset(), never as a bare
# `queryset = Model.objects.all()` class attribute. TenantManager decides
# `.none()` vs a real filter the instant `.all()`/`.filter()` runs — a class
# attribute evaluates once at import time, before any request has set tenant
# context, permanently baking in `.none()`. See core/CLAUDE.md.


class UnitOfMeasureListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = UnitOfMeasureSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ITEMS if self.request.method == "GET" else Permission.MANAGE_ITEMS

    def get_queryset(self):
        return UnitOfMeasure.objects.all()


class UnitOfMeasureDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = UnitOfMeasureSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ITEMS if self.request.method == "GET" else Permission.MANAGE_ITEMS

    def get_queryset(self):
        return UnitOfMeasure.objects.all()


class ItemListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = ItemSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ITEMS if self.request.method == "GET" else Permission.MANAGE_ITEMS

    def get_queryset(self):
        qs = Item.objects.all()
        item_type = self.request.query_params.get("item_type")
        if item_type:
            qs = qs.filter(item_type=item_type)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        return qs


class ItemDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = ItemSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_ITEMS if self.request.method == "GET" else Permission.MANAGE_ITEMS

    def get_queryset(self):
        return Item.objects.all()
