from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from core.db_preflight import assert_runtime_db_role_is_restricted


class Command(BaseCommand):
    help = "Fails unless the configured database role is non-superuser, non-BYPASSRLS (run before migrate)."

    def handle(self, *args, **options):
        try:
            role = assert_runtime_db_role_is_restricted()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"database role {role!r} is restricted: RLS applies to it")
