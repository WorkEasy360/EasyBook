from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0003_inventorysettings_stockmovement"),
    ]

    operations = [
        enable_rls_org_scoped("inventory_inventorysettings"),
        enable_rls_org_scoped("inventory_stockmovement"),
    ]
