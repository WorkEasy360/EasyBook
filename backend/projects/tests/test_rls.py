"""Database-level Row Level Security across every projects table.

Walks EVERY TenantScopedModel in the app and asserts the Postgres policy is
actually on its table, so a model added later without an entry in
`migrations/0002_enable_rls.py` fails here rather than shipping unprotected.
Same gate as purchases/tests/test_rls.py.
"""

from django.apps import apps
from django.db import connection
from django.test import TestCase

from core.models import TenantScopedModel
from core.tenancy import clear_tenant_context, tenant_context
from projects.tests.base import ProjectsTestsBase


def _projects_tenant_models():
    return [
        model
        for model in apps.get_app_config("projects").get_models()
        if issubclass(model, TenantScopedModel)
    ]


class ProjectsRlsCoverageTests(TestCase):
    def test_every_projects_model_is_tenant_scoped(self):
        models = _projects_tenant_models()
        all_models = list(apps.get_app_config("projects").get_models())
        self.assertGreater(len(models), 0)
        self.assertEqual(
            sorted(m.__name__ for m in models),
            sorted(m.__name__ for m in all_models),
            "every projects model must inherit TenantScopedModel",
        )

    def test_every_projects_table_has_rls_enabled_and_forced(self):
        tables = [model._meta.db_table for model in _projects_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(%s)",
                [tables],
            )
            state = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

        self.assertEqual([t for t in tables if t not in state], [])
        unprotected = [table for table, (enabled, forced) in state.items() if not (enabled and forced)]
        self.assertEqual(
            unprotected, [],
            f"these projects tables lack ENABLE/FORCE ROW LEVEL SECURITY: {unprotected}",
        )

    def test_every_projects_table_has_a_policy(self):
        tables = [model._meta.db_table for model in _projects_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(sorted(set(tables) - with_policy), [])


class ProjectsFailClosedTests(ProjectsTestsBase):
    def test_queries_return_nothing_with_no_tenant_context(self):
        from projects.models.project import Project
        from projects.models.time_entry import TimeEntry

        with tenant_context(organization_id=self.org_a.id):
            self._log()
            self.assertEqual(Project.objects.count(), 1)
            self.assertEqual(TimeEntry.objects.count(), 1)

        clear_tenant_context()
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(TimeEntry.objects.count(), 0)
        # The unscoped manager skips the app filter but not the DB policy.
        self.assertEqual(Project.all_objects.count(), 0)
        self.assertEqual(TimeEntry.all_objects.count(), 0)
