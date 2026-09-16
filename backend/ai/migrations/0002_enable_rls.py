from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):
    """Every ai table is organization-owned and gets the standard tenant
    policy (core/CLAUDE.md). ai/tests/test_rls.py asserts coverage for every
    model in this app, so a new model without an entry here fails CI."""

    dependencies = [("ai", "0001_initial")]

    operations = [
        enable_rls_org_scoped("ai_documentindex"),
        enable_rls_org_scoped("ai_documentchunk"),
        enable_rls_org_scoped("ai_aiconversation"),
        enable_rls_org_scoped("ai_aimessage"),
        enable_rls_org_scoped("ai_airequestlog"),
    ]
