from django.core.cache import cache
from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Membership
from core.exceptions import ApplicationError
from core.tenancy import set_current_organization_id, set_current_user_id


def resolve_membership(request) -> Membership:
    """Resolve the active Membership for the request's `X-Organization-Id` header.

    Fails closed: any missing/invalid/foreign organization raises rather than
    falling back to an unscoped or best-guess organization.
    """
    organization_id = request.headers.get("X-Organization-Id")
    if not organization_id:
        raise ApplicationError(
            "X-Organization-Id header is required.", code="organization_required", status_code=400
        )
    try:
        membership = Membership.all_objects.select_related("organization").get(
            organization_id=organization_id,
            user_id=request.user.id,
            is_active=True,
        )
    except (Membership.DoesNotExist, ValueError):
        raise ApplicationError(
            "You do not have access to this organization.", code="organization_forbidden", status_code=403
        )
    return membership


class OrganizationScopedMixin:
    """Mixin for views operating on organization-owned data.

    Resolves and enforces the tenant BEFORE the view body runs, and sets both
    the app-level context (core.managers.TenantManager) and the PostgreSQL
    session GUCs that RLS policies key off (core.tenancy).

    Tenant resolution happens in `check_permissions`, not only in `initial`,
    because DRF's `APIView.initial()` runs `check_permissions()` before
    returning — a permission class that needs `request.membership` (e.g.
    `authz.permissions.HasOrgPermission`) would otherwise always see it unset.
    `initial()` still calls the same resolution as a no-op safety net for any
    view that overrides `check_permissions` without calling super().
    """

    def _resolve_tenant(self, request) -> None:
        if getattr(request, "membership", None) is not None:
            return
        if not request.user or not request.user.is_authenticated:
            # Let permission_classes (e.g. IsAuthenticated) reject with 401
            # first — resolving membership for an anonymous user would
            # otherwise surface as a misleading 403.
            return
        set_current_user_id(request.user.id)
        membership = resolve_membership(request)
        request.membership = membership
        request.organization = membership.organization
        set_current_organization_id(membership.organization_id)

    def check_permissions(self, request):
        self._resolve_tenant(request)
        super().check_permissions(request)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._resolve_tenant(request)


class AuthenticatedAPIView(APIView):
    """Base for endpoints that need the authenticated user's GUC set but are
    not scoped to a single organization (e.g. "list my organizations")."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user and request.user.is_authenticated:
            set_current_user_id(request.user.id)


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        checks = {"database": self._check_database(), "cache": self._check_cache()}
        healthy = all(checks.values())
        return Response({"status": "ok" if healthy else "degraded", "checks": checks}, status=200 if healthy else 503)

    def _check_database(self) -> bool:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _check_cache(self) -> bool:
        try:
            cache.set("health_check", "1", timeout=5)
            return cache.get("health_check") == "1"
        except Exception:
            return False
