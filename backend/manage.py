#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    # Bare `runserver` binds 8001, not Django's 8000 (another local project
    # holds 8000). The frontend's API_BASE_URL defaults to 8001, so the two
    # agree without anyone remembering a port. DJANGO_RUNSERVER_PORT or an
    # explicit `runserver <port>` still overrides it.
    from django.core.management.commands import runserver

    runserver.Command.default_port = os.environ.get("DJANGO_RUNSERVER_PORT", "8001")
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
