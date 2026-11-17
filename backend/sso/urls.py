from django.urls import path
from workeasy360_sso import views as connector_views
from workeasy360_sso.urls import namespaced

from sso.views import (
    AuthMethodsView,
    Workeasy360CallbackView,
    Workeasy360EndSessionView,
    Workeasy360StartView,
)

urlpatterns = [
    path("auth/methods/", AuthMethodsView.as_view(), name="auth-methods"),
    path("auth/workeasy360/start/", Workeasy360StartView.as_view(), name="auth-workeasy360-start"),
    path("auth/workeasy360/callback/", Workeasy360CallbackView.as_view(), name="auth-workeasy360-callback"),
    path("auth/workeasy360/end-session/", Workeasy360EndSessionView.as_view(), name="auth-workeasy360-end-session"),
    # OpenID Connect Back-Channel Logout: the connector's own receiver (it verifies the logout token, refuses
    # replays and ends the sessions it recorded; sso/backchannel.py adds EasyBook's part). Under /api/v1/, the
    # only prefix the production edge routes to Django (infrastructure/terraform/alb.tf), on the redirect
    # URI's origin, as Workeasy360 requires. Registered there as the integration's back-channel logout URI:
    # https://<EasyBook host>/api/v1/auth/workeasy360/backchannel-logout/ (`manage.py w360sso_manifest`).
    namespaced(
        path(
            "auth/workeasy360/backchannel-logout/",
            connector_views.backchannel_logout,
            name="backchannel_logout",
        ),
    ),
]
