from django.urls import path

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
]
