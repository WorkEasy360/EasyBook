"""Row Level Security for every automation table.

Every model in this app is a TenantScopedModel, so every one of its tables
needs a matching policy — the TenantManager filter is defense in depth, not
the primary control (see core/CLAUDE.md). A new automation model without an
entry here is a bug (automation/tests/test_rls.py asserts this exhaustively,
mirroring documents/tests/test_rls.py).
"""

from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("automation", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("automation_automationrule"),
        enable_rls_org_scoped("automation_automationcondition"),
        enable_rls_org_scoped("automation_automationactionconfig"),
    ]
