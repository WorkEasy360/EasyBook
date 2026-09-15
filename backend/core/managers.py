from django.db import models

from core.tenancy import get_current_organization_id


class TenantManager(models.Manager):
    """Scopes queries to the current organization. Fails closed: no tenant in
    context means an empty queryset, never an unscoped one. PostgreSQL RLS
    (see core/CLAUDE.md) enforces the same boundary independently."""

    def get_queryset(self):
        organization_id = get_current_organization_id()
        queryset = super().get_queryset()
        if organization_id is None:
            return queryset.none()
        return queryset.filter(organization_id=organization_id)
