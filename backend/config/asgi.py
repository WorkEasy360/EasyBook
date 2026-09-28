"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

application = get_asgi_application()

# Fail the worker at boot, not a tenant at runtime: an API process connected as
# a superuser/BYPASSRLS role would silently disable RLS (core/db_preflight.py).
if settings.DB_ROLE_PREFLIGHT:
    from core.db_preflight import assert_runtime_db_role_is_restricted

    assert_runtime_db_role_is_restricted()
