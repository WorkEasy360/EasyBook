from django.apps import AppConfig


class SsoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sso"
    verbose_name = "Workeasy360 sign-in"

    def ready(self):
        # Workeasy360 entitlement, re-checked on every organization-scoped request (sso/entitlement.py).
        # Registered here so core never imports sso.
        from core import access
        from sso import (
            checks,  # noqa: F401 - registers the system checks
            entitlement,
        )

        access.register(entitlement.gate)
        # ... and before background work produces a customer effect (design section 5, class A).
        access.register_background(entitlement.background_gate)
