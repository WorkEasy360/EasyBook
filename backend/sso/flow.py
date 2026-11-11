"""The two halves of a Workeasy360 sign-in, as the BFF drives them.

The browser never talks to Django (frontend/CLAUDE.md, THE BFF), so what a
sign-in must remember between leaving for Workeasy360 and coming back -
state, nonce and the PKCE verifier - travels as a signed, time-limited blob
that the BFF keeps in an httpOnly cookie. Signed rather than stored server
side so no table or cache entry is needed, and so a blob from one browser is
useless in another: the state inside it must match the state Workeasy360
echoes back to that browser.
"""

import uuid

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.core import signing
from rest_framework_simplejwt.tokens import RefreshToken

from sso.config import get_client
from sso.oidc import SSOError
from sso.provisioning import provision

_SALT = "sso.workeasy360.flow"
#: Long enough to sign in at Workeasy360 (including a second factor), short
#: enough that an abandoned flow is soon worthless.
FLOW_MAX_AGE_SECONDS = 600


def start(*, organization_id: str = "") -> dict:
    organization_id = _uuid_or_blank(organization_id)
    request = get_client().authorization_request(organization_id=organization_id)
    flow = signing.dumps(
        {"s": request.state, "n": request.nonce, "v": request.code_verifier, "o": organization_id},
        salt=_SALT,
        compress=True,
    )
    return {"authorization_url": request.url, "flow": flow}


def complete(*, code: str, state: str, flow: str) -> dict:
    try:
        kept = signing.loads(flow, salt=_SALT, max_age=FLOW_MAX_AGE_SECONDS)
    except signing.BadSignature:
        # Covers SignatureExpired too: a forged, altered or stale flow.
        raise SSOError("flow_expired") from None
    client = get_client()
    claims = client.complete(
        code=code, returned_state=state, expected_state=kept["s"], nonce=kept["n"], code_verifier=kept["v"],
    )
    expected_product = (settings.WORKEASY360_PRODUCT_CODE or "").strip()
    if not expected_product or claims.get("product_code") != expected_product:
        # The audience check already ties the token to our client id; this
        # catches a client id registered to the wrong product. With no product
        # code configured nothing can match: the check fails closed rather
        # than being skipped (sso/checks.py refuses to start in that state).
        raise SSOError("wrong_product", f"token is for {claims.get('product_code')!r}")
    if kept.get("o") and str(claims["organization_id"]) != kept["o"]:
        # The launcher asked for one organization and the provider answered
        # for another: land nowhere rather than in the wrong books.
        raise SSOError("wrong_organization", "organization_id differs from the launcher's")
    signed_in = provision(claims, issuer=client.issuer)
    update_last_login(None, signed_in.user)  # as SIMPLE_JWT.UPDATE_LAST_LOGIN does for passwords
    refresh = RefreshToken.for_user(signed_in.user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "organization_id": str(signed_in.organization.id),
        "organization_created": signed_in.organization_created,
    }


def _uuid_or_blank(value: str) -> str:
    """The launcher's ?organization= is forwarded to Workeasy360 only when it
    is a UUID; anything else is dropped and Workeasy360 shows its chooser."""
    try:
        return str(uuid.UUID(str(value))) if value else ""
    except ValueError:
        return ""
