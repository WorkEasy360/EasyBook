"""Workeasy360 sign-in configuration, read from settings at call time (so ``override_settings`` works).

The protocol - discovery, the key set, client authentication, the authorization code flow, ID and logout
token verification, entitlement HTTP and its cache - is the workeasy360-sso connector's. It reads its
configuration from ``settings.WORKEASY360_SSO``, which ``sso.connector_settings`` derives from the
``WORKEASY360_*`` settings; a change to any of them (a test's ``override_settings``) resets the connector's
cached configuration and provider metadata.
"""

from typing import Any

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver
from workeasy360_sso import discovery
from workeasy360_sso.conf import SSOConfig, get_config, reset_config


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


def issuer() -> str:
    return str(settings.WORKEASY360_ISSUER)


def password_sign_in_allowed() -> bool:
    return not settings.WORKEASY360_SSO_ONLY


def post_logout_redirect_uri() -> str:
    """Where Workeasy360 returns the browser after "sign out of Workeasy360 too" - registered for
    this client in Workeasy360 exactly. Empty: the option is not offered."""
    return str(getattr(settings, "WORKEASY360_POST_LOGOUT_REDIRECT_URI", "") or "")


def sign_out_of_workeasy360_available() -> bool:
    return is_configured() and bool(post_logout_redirect_uri())


def connector() -> SSOConfig:
    """The connector's configuration (built from the settings above, cached until one changes). Raises
    ``workeasy360_sso.conf.ConfigurationError`` when it cannot be built (an unreadable key file, say)."""
    return get_config()


@receiver(setting_changed, dispatch_uid="sso.config.reset_connector")
def _reset_connector(*, setting: str, **_: Any) -> None:
    if setting.startswith("WORKEASY360_"):
        reset_config()
        discovery.reset_cache()
