"""Django system checks for the Workeasy360 sign-in.

They run on ``manage.py check``, ``runserver``, ``migrate`` and the test runner in every environment, so a
configuration that would quietly weaken the sign-in stops the process instead of being discovered later.
(Production additionally refuses at import: config/settings/preflight.py.)
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, register


@register()
def product_code_is_set(app_configs, **kwargs):
    """The sign-in compares the ID token's ``product_code`` with this one (the connector, through
    sso/connector_settings.py). An empty value would leave that comparison with nothing to hold a token
    against (the connector then refuses every sign-in as unconfigured)."""
    if (settings.WORKEASY360_PRODUCT_CODE or "").strip():
        return []
    return [
        Error(
            "WORKEASY360_PRODUCT_CODE is empty.",
            hint="Set it to this product's Workeasy360 catalog code (the default is 'easybook'), or unset it.",
            id="sso.E001",
        )
    ]


#: The registered redirect URI's path: the BFF route frontend/src/app/api/auth/callback/workeasy360/route.ts,
#: which relays the code and state to Django's own callback view. It stands in for the connector's
#: workeasy360_sso.E005 (silenced in settings), which expects Django itself to serve the redirect URI.
BFF_CALLBACK_PATH = "/api/auth/callback/workeasy360"


@register()
def redirect_uri_is_the_bff_callback(app_configs, **kwargs):
    """Redirect URIs are matched by exact string, and only the BFF's callback route relays a sign-in here."""
    redirect_uri = (settings.WORKEASY360_REDIRECT_URI or "").strip()
    if not redirect_uri or urlsplit(redirect_uri).path == BFF_CALLBACK_PATH:
        return []
    return [
        Error(
            f"WORKEASY360_REDIRECT_URI must end in {BFF_CALLBACK_PATH} (the BFF's callback route).",
            hint="It is registered in Workeasy360 exactly, e.g. https://book.workeasy360.com" + BFF_CALLBACK_PATH,
            id="sso.E002",
        )
    ]
