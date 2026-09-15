from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0003_journalentry_journalline_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("accounting_journalentry"),
        enable_rls_org_scoped("accounting_journalline"),
    ]
