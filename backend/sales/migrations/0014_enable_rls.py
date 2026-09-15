from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0013_creditnote_creditnoteline_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("sales_creditnote"),
        enable_rls_org_scoped("sales_creditnoteline"),
    ]
