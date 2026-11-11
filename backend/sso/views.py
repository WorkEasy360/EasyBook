import logging

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ApplicationError
from core.throttling import FailOpenScopedRateThrottle
from sso import flow
from sso.config import (
    get_client,
    is_configured,
    password_sign_in_allowed,
    post_logout_redirect_uri,
    sign_out_of_workeasy360_available,
)
from sso.oidc import SSOError

logger = logging.getLogger(__name__)

# Bounds on what the BFF relays from the browser's callback URL. A real
# authorization code or state is well under these; anything larger is not one.
_MAX_CODE = 2048
_MAX_STATE = 512
_MAX_FLOW = 4096

#: SSOError codes that mean "Workeasy360 could not be reached or answered
#: oddly" (retry later) rather than "this sign-in is refused".
_UNAVAILABLE = {"provider_unavailable", "provider_misconfigured"}


class SignInUnavailable(ApplicationError):
    status_code = 503
    default_code = "workeasy360_unavailable"
    default_detail = "Workeasy360 sign-in is not available right now. Please try again shortly."


class AuthMethodsView(APIView):
    """Which sign-in methods the login page should offer. Public: it says
    nothing about any account."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response(
            {
                "password": password_sign_in_allowed(),
                "workeasy360": is_configured(),
                # "Sign out of EasyBook and Workeasy360" (RP-initiated logout) is offered.
                "workeasy360_sign_out": sign_out_of_workeasy360_available(),
            }
        )


class Workeasy360EndSessionView(APIView):
    """Where the BFF sends the browser for "sign out of EasyBook and Workeasy360": Workeasy360's
    end-session endpoint, naming our client and our registered post-logout URI. Workeasy360 asks the
    person before signing anyone out. Changes nothing here - EasyBook's own session is ended by the
    BFF's logout route first - so it needs no authentication; ``url`` is "" when unavailable."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        if not sign_out_of_workeasy360_available():
            return Response({"url": ""})
        try:
            url = get_client().end_session_url(post_logout_redirect_uri=post_logout_redirect_uri())
        except SSOError as exc:
            logger.warning("sso.end_session_failed", extra={"code": exc.code, "detail": exc.detail})
            url = ""
        return Response({"url": url})


class Workeasy360StartView(APIView):
    """Called by the BFF, which sends the browser to `authorization_url` and
    keeps `flow` in an httpOnly cookie until the callback.

    Its own throttle scope, not the "auth" bucket the callback (and password
    sign-in) share. A direct visit starts a sign-in by itself (frontend
    src/proxy.ts), so starts are routine - an office behind one address, an
    uptime check - and must not use up the 20/min that protects the callback,
    where a code is exchanged. The scoped throttle is the only one here: the
    blanket anonymous bucket would count the same request a second time."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [FailOpenScopedRateThrottle]
    throttle_scope = "sso_start"

    def post(self, request):
        if not is_configured():
            raise SignInUnavailable()
        organization = request.data.get("organization") if isinstance(request.data, dict) else ""
        try:
            return Response(flow.start(organization_id=organization if isinstance(organization, str) else ""))
        except SSOError as exc:
            logger.warning("sso.start_failed", extra={"code": exc.code, "detail": exc.detail})
            raise SignInUnavailable() from None


class Workeasy360CallbackView(APIView):
    """Finishes a sign-in: the BFF relays the callback's code and state with
    the flow it kept, and gets back the same token pair a password sign-in
    returns."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        if not is_configured():
            raise SignInUnavailable()
        data = request.data if isinstance(request.data, dict) else {}
        code, state, kept = (data.get(key) for key in ("code", "state", "flow"))
        if not all(isinstance(value, str) for value in (code, state, kept)) or not (
            0 < len(code) <= _MAX_CODE and 0 < len(state) <= _MAX_STATE and 0 < len(kept) <= _MAX_FLOW
        ):
            raise ApplicationError("Invalid sign-in response.", code="invalid_request")
        try:
            return Response(flow.complete(code=code, state=state, flow=kept))
        except SSOError as exc:
            # The detail is for the log only; the browser gets the fixed code.
            logger.warning("sso.callback_refused", extra={"code": exc.code, "detail": exc.detail})
            if exc.code in _UNAVAILABLE:
                raise SignInUnavailable() from None
            raise ApplicationError("Workeasy360 sign-in could not be completed.", code=exc.code,
                                   status_code=403) from None
