"""The two halves of a Workeasy360 sign-in, as the BFF drives them, on the workeasy360-sso connector.

The connector does the protocol (``workeasy360_sso.api``): it keeps each sign-in in flight server-side -
state, nonce and PKCE verifier, single use, ten minutes - and binds it to the browser that started it with a
random value it would normally set as a cookie. The browser never talks to Django here (frontend/CLAUDE.md,
THE BFF): the BFF is the connector's browser. So the binding value travels in the signed, time-limited
``flow`` blob the BFF keeps in its httpOnly cookie, and the callback hands it back to the connector as the
cookie that browser would have sent. A blob from one browser is useless in another: the state Workeasy360
echoes back to that browser must name a flow bound to that blob's value.

A sign-in signs nobody in to a Django session. EasyBook's session is the access/refresh pair the BFF keeps;
the connector records the sign-in (its ``ProductSession``, the back-channel logout target) against a session
that is never stored (``UnstoredSession``), and the token pair carries that record's id (sso/sessions.py).
"""

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.base import SessionBase
from django.core import signing
from django.http import HttpRequest, QueryDict
from workeasy360_sso import api
from workeasy360_sso.errors import SignInRefused
from workeasy360_sso.flow import binding_cookie_name

from sso import provisioning, sessions
from sso.errors import SSOError, from_refusal

_SALT = "sso.workeasy360.flow"
#: Long enough to sign in at Workeasy360 (including a second factor), short
#: enough that an abandoned flow is soon worthless. Equal to the connector's FLOW_TTL
#: (sso/connector_settings.py) and the BFF's SSO_FLOW_MAX_AGE_SECONDS.
FLOW_MAX_AGE_SECONDS = 600


class UnstoredSession(SessionBase):
    """A Django session that is never stored anywhere.

    The connector's ``complete_sign_in`` logs the person in to the request's session and records that
    session's key for back-channel logout. EasyBook keeps no Django session for a signed-in person (the BFF
    holds the token pair), so the request it builds for the connector carries this one: it has a key, so the
    sign-in can be recorded, but nothing is written, and no session cookie or row exists that could ever
    authenticate anyone.
    """

    def exists(self, session_key):
        return False

    def create(self):
        self._session_key = self._get_new_session_key()
        self.modified = True

    def save(self, must_create=False):
        if self.session_key is None:
            self.create()

    def delete(self, session_key=None):
        """Nothing is stored, so there is nothing to delete."""

    def load(self):
        return {}


def _relay(*, binding: str = "", code: str = "", state: str = "") -> HttpRequest:
    """The request the browser would have sent the connector, from what the BFF relayed."""
    request = HttpRequest()
    request.method = "GET"
    query = QueryDict(mutable=True)
    if code:
        query["code"] = code
    if state:
        query["state"] = state
    request.GET = query
    if binding:
        request.COOKIES[binding_cookie_name(request)] = binding
    request.session = UnstoredSession()
    request.user = AnonymousUser()
    return request


def start(*, organization_id: str = "") -> dict:
    """The authorization URL to send the browser to, and the ``flow`` the BFF keeps until the callback.

    The launcher's ?organization= is forwarded to Workeasy360 only when it is a UUID (the connector drops
    anything else, and Workeasy360 shows its chooser); the callback then insists the token is for it."""
    relay = _relay()
    try:
        response = api.begin_sign_in(relay, next_path="", organization=organization_id or None)
    except SignInRefused as refusal:
        raise from_refusal(refusal) from None
    binding = response.cookies[binding_cookie_name(relay)].value
    flow = signing.dumps({"b": binding}, salt=_SALT, compress=True)
    return {"authorization_url": response.url, "flow": flow}


def complete(*, code: str, state: str, flow: str) -> dict:
    try:
        kept = signing.loads(flow, salt=_SALT, max_age=FLOW_MAX_AGE_SECONDS)
    except signing.BadSignature:
        # Covers SignatureExpired too: a forged, altered or stale flow.
        raise SSOError("flow_expired") from None
    binding = kept.get("b") if isinstance(kept, dict) else None
    if not isinstance(binding, str) or not binding:
        # A flow started by the release before the connector (it carried the state itself): start again.
        raise SSOError("flow_expired", "flow without a binding")
    try:
        result = api.complete_sign_in(_relay(binding=binding, code=code, state=state))
    except SignInRefused as refusal:
        raise from_refusal(refusal) from None
    signed_in: provisioning.SignedIn = result.binding
    # The connector's login() has already updated last_login (Django's user_logged_in receiver), as
    # SIMPLE_JWT.UPDATE_LAST_LOGIN does for a password sign-in.
    provisioning.record_sign_in(signed_in, result.claims)
    tokens = sessions.issue_tokens(signed_in.user, result.product_session)
    return {
        **tokens,
        "organization_id": str(signed_in.organization.id),
        "organization_created": signed_in.organization_created,
    }
