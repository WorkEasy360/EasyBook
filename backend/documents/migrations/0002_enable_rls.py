"""Row Level Security for every documents table.

Every model in this app is a TenantScopedModel, so every one of its tables
needs a matching policy — the TenantManager filter is defense in depth, not
the primary control (see core/CLAUDE.md). A new documents model without an
entry here is a bug (documents/tests/test_rls.py asserts this exhaustively).
"""

from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("documents_document"),
        enable_rls_org_scoped("documents_documentfolder"),
        enable_rls_org_scoped("documents_documenttag"),
        enable_rls_org_scoped("documents_documenttagassignment"),
        enable_rls_org_scoped("documents_documentlink"),
        enable_rls_org_scoped("documents_ocrresult"),
        enable_rls_org_scoped("documents_documentreview"),
    ]
