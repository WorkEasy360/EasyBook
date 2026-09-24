from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.models import Membership
from accounts.serializers import (
    MembershipSerializer,
    OrganizationCreateSerializer,
    OrganizationSerializer,
    RegisterSerializer,
    UserSerializer,
)
from core.views import AuthenticatedAPIView, OrganizationScopedMixin


class LoginView(TokenObtainPairView):
    throttle_scope = "auth"


# A SimpleJWT refresh token is a few hundred bytes; anything far larger is not
# one, and is not worth decoding.
_MAX_REFRESH_TOKEN_LENGTH = 4096


class LogoutView(APIView):
    """Revokes the presented refresh token. Always 204.

    The refresh token itself is the credential being retired, so no access
    token is required (it has usually expired by the time a user signs out).
    The response is identical whether the token was valid, expired, already
    revoked or garbage — logout is idempotent and never reveals token state.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        raw = request.data.get("refresh") if isinstance(request.data, dict) else None
        if isinstance(raw, str) and 0 < len(raw) <= _MAX_REFRESH_TOKEN_LENGTH:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass  # invalid, expired or already blacklisted: nothing left to revoke
        return Response(status=204)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=201)


class MeView(AuthenticatedAPIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class OrganizationListCreateView(AuthenticatedAPIView):
    def get(self, request):
        memberships = Membership.all_objects.filter(user=request.user, is_active=True).select_related("organization")
        organizations = []
        for membership in memberships:
            org = membership.organization
            org._requesting_membership = membership
            organizations.append(org)
        return Response(OrganizationSerializer(organizations, many=True).data)

    def post(self, request):
        serializer = OrganizationCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        organization._requesting_membership = Membership.all_objects.get(organization=organization, user=request.user)
        return Response(OrganizationSerializer(organization).data, status=201)


class MembershipListView(OrganizationScopedMixin, APIView):
    def get(self, request):
        members = Membership.all_objects.filter(organization=request.organization, is_active=True).select_related("user")
        return Response(MembershipSerializer(members, many=True).data)
