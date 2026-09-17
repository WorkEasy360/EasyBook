"""Deployment preflight for the pgvector extension the `ai` app's migrations
require (see ai/CLAUDE.md and infrastructure/CLAUDE.md). The app's own
non-superuser DB role cannot create the extension itself, so a deploy that
skips the manual `CREATE EXTENSION vector;` step must fail with a clear
message here rather than a cryptic "type vector does not exist" error part
way through a migration.
"""

from django.core.exceptions import ImproperlyConfigured

PGVECTOR_MISSING_MESSAGE = (
    "The 'ai' app's migrations require the PostgreSQL 'vector' extension "
    "(pgvector), which is not installed in this database. A superuser must "
    "run `CREATE EXTENSION vector;` before migrating — the application's "
    "non-superuser role cannot. See ai/CLAUDE.md and infrastructure/CLAUDE.md."
)


def check_pgvector_extension_installed(sender, app_config, **kwargs):
    """`pre_migrate` receiver: only acts when the app about to be migrated is `ai`."""
    if app_config.label != "ai":
        return

    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = %s", ["vector"])
        if cursor.fetchone() is None:
            raise ImproperlyConfigured(PGVECTOR_MISSING_MESSAGE)
