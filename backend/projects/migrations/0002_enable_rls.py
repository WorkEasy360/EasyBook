"""Row Level Security for every projects table.

Every model in this app is a TenantScopedModel, so every one of its tables
needs a matching policy — the TenantManager filter is defense in depth, not
the primary control (see core/CLAUDE.md). A new projects model without an
entry here is a bug, and projects/tests/test_rls.py fails if one appears.
"""

from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("projects_project"),
        enable_rls_org_scoped("projects_projectmember"),
        enable_rls_org_scoped("projects_task"),
        enable_rls_org_scoped("projects_timeentry"),
    ]
