"""EasyBook sessions opened by a Workeasy360 sign-in, and ending them from Workeasy360.

An EasyBook session is the SimpleJWT access/refresh pair the BFF keeps in httpOnly cookies; a refresh rotates
the refresh token but keeps every claim. A Workeasy360 sign-in also records the session in the connector
(``workeasy360_sso.models.ProductSession``: which Workeasy360 sign-in - its ``sid`` - and which person opened
it), and the pair it issues carries that record's id in ``SESSION_CLAIM``, so every token of that session,
however often it was refreshed, names it.

OpenID Connect Back-Channel Logout (sso/backchannel.py, the connector's receiver) ends records: the ones a
``sid`` names, or every one of a person when the token names only ``sub``. From then on a token naming an
ended record is refused on every request (``Workeasy360JWTAuthentication``) and cannot be refreshed
(``TokenRefreshSerializer``): the session is over at once, not when its access token expires.

A token without the claim (a password sign-in, or a Workeasy360 sign-in from before the connector) is
treated exactly as SimpleJWT treats it.
"""

import uuid

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer as SimpleJWTTokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from workeasy360_sso.models import ProductSession

#: The id of the connector's ProductSession a token's session was opened as. Copied into every access token
#: and kept by every refresh (SimpleJWT copies the claims it does not own).
SESSION_CLAIM = "workeasy360_session"


def issue_tokens(user, product_session: ProductSession | None) -> dict:
    """The access/refresh pair a Workeasy360 sign-in returns, naming its session record."""
    refresh = RefreshToken.for_user(user)
    if product_session is not None:
        refresh[SESSION_CLAIM] = str(product_session.id)
        # for_user() stored the token as it was before the claim; keep the outstanding record identical to
        # what the BFF holds.
        OutstandingToken.objects.filter(jti=refresh[api_settings.JTI_CLAIM]).update(token=str(refresh))
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def session_ended(token) -> bool:
    """Has Workeasy360 ended the session this token belongs to?"""
    value = token.get(SESSION_CLAIM)
    if value is None:
        return False
    try:
        session_id = uuid.UUID(str(value))
    except ValueError:
        return True  # a claim EasyBook never wrote
    # A record that no longer exists was purged long after its session ended or lapsed (sso/tasks.py keeps
    # ended records longer than a refresh token lives), so only an ended one refuses.
    return ProductSession.objects.filter(pk=session_id, ended_at__isnull=False).exists()


class Workeasy360JWTAuthentication(JWTAuthentication):
    """SimpleJWT's JWTAuthentication, refusing an access token whose Workeasy360 session has ended."""

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        if session_ended(token):
            raise InvalidToken({"detail": "This session was ended in Workeasy360.", "code": "token_not_valid"})
        return token


class TokenRefreshSerializer(SimpleJWTTokenRefreshSerializer):
    """SimpleJWT's refresh, refusing a refresh token whose Workeasy360 session has ended."""

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])  # invalid, expired or blacklisted: TokenError
        if session_ended(refresh):
            raise TokenError("This session was ended in Workeasy360.")
        return super().validate(attrs)
