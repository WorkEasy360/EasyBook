from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0007_deliverychallan_deliverychallanline_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("sales_deliverychallan"),
        enable_rls_org_scoped("sales_deliverychallanline"),
    ]
