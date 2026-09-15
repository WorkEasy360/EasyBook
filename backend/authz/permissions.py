from rest_framework.permissions import BasePermission

from authz.roles import role_has_permission


class HasOrgPermission(BasePermission):
    """Requires `view.required_permission` to be granted by the caller's role
    in the organization resolved by OrganizationScopedMixin (request.membership)."""

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        required = getattr(view, "required_permission", None)
        if membership is None or required is None:
            return False
        return role_has_permission(membership.role, required)
