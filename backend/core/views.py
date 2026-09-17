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


class LivenessCheckView(APIView):
    """Is the process itself alive — no dependency checks.

    Used for the ECS/ALB liveness probe: it must never depend on the
    database or Redis, or a DB/Redis blip would make the orchestrator kill
    and restart otherwise-healthy containers, turning a brief dependency
    outage into a full application outage (phase 12 section 46).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # A burst of health probes (e.g. several new ECS tasks starting up at
    # once, all hit by the ALB within the same window) must never trip the
    # global AnonRateThrottle baseline (config/settings/base.py) — this is a
    # liveness signal, not user traffic, regardless of what numeric limit
    # is configured there.
    throttle_classes = []

    def get(self, request):
        return Response({"status": "ok"}, status=200)


class HealthCheckView(APIView):
    """Readiness check: can this instance actually serve traffic right now."""

    permission_classes = [AllowAny]
    throttle_classes = []  # see LivenessCheckView's comment above
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
