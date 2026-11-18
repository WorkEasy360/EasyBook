"""``settings.WORKEASY360_SSO`` for the workeasy360-sso connector, derived from EasyBook's own settings.

EasyBook's ``WORKEASY360_*`` settings (and the environment variables behind them) stay what they always were.
The connector reads its configuration from this mapping, which is computed from them each time the connector
(re)loads it, so ``override_settings(WORKEASY360_ISSUER=...)`` reaches the connector too (``sso.config``
resets the connector's cached configuration when a ``WORKEASY360_*`` setting changes). Every key is given,
so nothing falls through to the connector's own ``WORKEASY360_*`` environment lookup.

Imported by the settings module: no app imports here.
"""

from collections.abc import Iterator, Mapping
from typing import Any

#: Refusal codes EasyBook's own provisioning rules raise (``sso/provisioning.py``) that the connector's
#: vocabulary has no word for. The BFF never shows these messages (Django answers it with a code, and
#: frontend/src/lib/auth/workeasy360.ts maps codes to its own messages); the connector wants one per code.
ERROR_MESSAGES = {
    "email_required": "WorkEasy360 did not share an email address for this account.",
    "provider_misconfigured": "EasyBook cannot complete WorkEasy360 sign-in right now. Contact support.",
}


def connector_settings() -> dict[str, Any]:
    from django.conf import settings

    return {
        "ISSUER": settings.WORKEASY360_ISSUER,
        "CLIENT_ID": settings.WORKEASY360_CLIENT_ID,
        "REDIRECT_URI": settings.WORKEASY360_REDIRECT_URI,
        "PRODUCT_CODE": settings.WORKEASY360_PRODUCT_CODE,
        "KEY_ID": settings.WORKEASY360_KEY_ID,
        # The file wins when both are set, as it always has here (the connector would prefer the inline key).
        "PRIVATE_KEY": "" if settings.WORKEASY360_PRIVATE_KEY_FILE else settings.WORKEASY360_PRIVATE_KEY,
        "PRIVATE_KEY_FILE": settings.WORKEASY360_PRIVATE_KEY_FILE,
        # private_key_jwt only; the signing algorithm follows the key (RS256 for RSA, ES256 for EC P-256).
        "CLIENT_SECRET": "",  # nosec B105 - empty: EasyBook has no client secret, it uses private_key_jwt
        "SIGNING_ALGORITHM": "",
        "SCOPES": settings.WORKEASY360_SCOPES,
        "POST_LOGOUT_REDIRECT_URI": settings.WORKEASY360_POST_LOGOUT_REDIRECT_URI,
        "SSO_ONLY": settings.WORKEASY360_SSO_ONLY,
        # The BFF starts sign-ins itself (src/proxy.ts, the launcher); Django serves only /api/v1/ and gates
        # requests itself (core.access), so the connector's middleware is not installed.
        "AUTO_START": False,
        "ENFORCE_ENTITLEMENT": True,
        "BACKCHANNEL_LOGOUT": settings.WORKEASY360_BACKCHANNEL_LOGOUT,
        "API_PATH_PREFIXES": ("/api/",),
        "PUBLIC_PATHS": (),
        "LOGIN_URL": "/login",
        "LOGIN_REDIRECT_URL": "/dashboard",
        "LOGOUT_REDIRECT_URL": "/login",
        "ERROR_REDIRECT_URL": "/login",
        "PURCHASE_URL": "",
        # A person watches the sign-in; a request (inside its transaction) or a job waits on entitlement.
        "HTTP_TIMEOUT": 10,
        "ENTITLEMENT_TIMEOUT": 10,
        "LEEWAY": 30,
        # Equal to sso.flow.FLOW_MAX_AGE_SECONDS and the BFF's SSO_FLOW_MAX_AGE_SECONDS.
        "FLOW_TTL": 600,
        "DISCOVERY_TTL": 3600,
        "USER_ADAPTER": "sso.provisioning.UserAdapter",
        "ORGANIZATION_ADAPTER": "sso.provisioning.OrganizationAdapter",
        # EasyBook's adapters apply EasyBook's linking and profile rules themselves.
        "LINK_BY_VERIFIED_EMAIL": False,
        "SYNC_PROFILE": False,
        "CACHE_ALIAS": "default",
        "ALLOW_INSECURE_HTTP": settings.WORKEASY360_ALLOW_INSECURE_HTTP,
        # The connector's status page is not mounted (only /api/v1/ reaches Django, and EasyBook serves its own
        # routes); `manage.py w360sso_manifest` prints the same facts.
        "EXPOSE_STATUS": False,
        "HTTP_TRANSPORT": getattr(settings, "WORKEASY360_HTTP_TRANSPORT", None),
        # The sign-in is recorded in a session that is never stored (sso/flow.py): EasyBook's session is the
        # token pair the BFF keeps. ModelBackend is the backend Django lists by default.
        "LOGIN_BACKEND": "django.contrib.auth.backends.ModelBackend",
        "ERROR_MESSAGES": ERROR_MESSAGES,
        # Celery's "time is up" is not an outage of WorkEasy360, and not the connector's to swallow.
        "PROPAGATE_EXCEPTIONS": ("celery.exceptions.SoftTimeLimitExceeded",),
    }


class ConnectorSettings(Mapping[str, Any]):
    """A read-only view of :func:`connector_settings`, evaluated when the connector reads it."""

    def __getitem__(self, key: str) -> Any:
        return connector_settings()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(connector_settings())

    def __len__(self) -> int:
        return len(connector_settings())

    def __repr__(self) -> str:
        return "<ConnectorSettings derived from WORKEASY360_* (secrets not shown)>"
