"""Django system checks for the Workeasy360 sign-in.

They run on ``manage.py check``, ``runserver``, ``migrate`` and the test runner in every environment, so a
configuration that would quietly weaken the sign-in stops the process instead of being discovered later.
(Production additionally refuses at import: config/settings/preflight.py.)
"""

from django.conf import settings
from django.core.checks import Error, register


@register()
def product_code_is_set(app_configs, **kwargs):
    """The sign-in compares the ID token's ``product_code`` with this one (sso/flow.py). An empty value would
    leave that comparison with nothing to hold a token against."""
    if (settings.WORKEASY360_PRODUCT_CODE or "").strip():
        return []
    return [
        Error(
            "WORKEASY360_PRODUCT_CODE is empty.",
            hint="Set it to this product's Workeasy360 catalog code (the default is 'easybook'), or unset it.",
            id="sso.E001",
        )
    ]
