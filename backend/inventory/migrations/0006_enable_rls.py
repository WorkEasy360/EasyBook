from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0005_stockadjustment_stockadjustmentline_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("inventory_stockadjustment"),
        enable_rls_org_scoped("inventory_stockadjustmentline"),
    ]
