from django.db import migrations

from core.rls import enable_rls_org_scoped, enable_rls_self_or_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("accounts_fiscalyear"),
        enable_rls_org_scoped("accounts_numbersequence"),
        enable_rls_self_or_org_scoped("accounts_membership"),
    ]
