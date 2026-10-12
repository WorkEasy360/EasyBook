"""The configured Workeasy360 client, built once per process from settings."""

from functools import lru_cache
from pathlib import Path

from django.conf import settings

from sso.oidc import Workeasy360Client

#: A PEM pasted into a one-line environment variable usually arrives with
#: literal backslash-n sequences instead of newlines.
_ESCAPED_NEWLINE = chr(92) + "n"


def private_key_pem() -> str:
    path = settings.WORKEASY360_PRIVATE_KEY_FILE
    if path:
        return Path(path).read_text(encoding="utf-8")
    return settings.WORKEASY360_PRIVATE_KEY.replace(_ESCAPED_NEWLINE, "\n")


def is_configured() -> bool:
    """Everything a sign-in needs is present. Says nothing about whether the
    provider is reachable - that is only known by trying."""
    return bool(
        settings.WORKEASY360_ISSUER
        and settings.WORKEASY360_CLIENT_ID
        and settings.WORKEASY360_REDIRECT_URI
        and settings.WORKEASY360_KEY_ID
        and (settings.WORKEASY360_PRIVATE_KEY or settings.WORKEASY360_PRIVATE_KEY_FILE)
    )


def password_sign_in_allowed() -> bool:
    return not settings.WORKEASY360_SSO_ONLY


def post_logout_redirect_uri() -> str:
    """Where Workeasy360 returns the browser after "sign out of Workeasy360 too" - registered for
    this client in Workeasy360 exactly. Empty: the option is not offered."""
    return str(getattr(settings, "WORKEASY360_POST_LOGOUT_REDIRECT_URI", "") or "")


def sign_out_of_workeasy360_available() -> bool:
    return is_configured() and bool(post_logout_redirect_uri())


@lru_cache(maxsize=1)
def get_client() -> Workeasy360Client:
    """One client per process, so discovery and the key set are cached
    across requests. Tests call `get_client.cache_clear()` after changing
    settings."""
    return Workeasy360Client(
        issuer=settings.WORKEASY360_ISSUER,
        client_id=settings.WORKEASY360_CLIENT_ID,
        redirect_uri=settings.WORKEASY360_REDIRECT_URI,
        private_key_pem=private_key_pem(),
        key_id=settings.WORKEASY360_KEY_ID,
        scopes=tuple(settings.WORKEASY360_SCOPES),
    )
