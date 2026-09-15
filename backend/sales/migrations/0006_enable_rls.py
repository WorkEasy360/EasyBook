from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0005_salesorder_salesorderline_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("sales_salesorder"),
        enable_rls_org_scoped("sales_salesorderline"),
    ]
