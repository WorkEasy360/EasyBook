from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import LoginView, MeView, MembershipListView, OrganizationListCreateView, RegisterView

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("organizations/", OrganizationListCreateView.as_view(), name="organization-list-create"),
    path("organizations/members/", MembershipListView.as_view(), name="organization-members"),
]
