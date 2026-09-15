from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("items_unitofmeasure"),
        enable_rls_org_scoped("items_hsnsaccode"),
        enable_rls_org_scoped("items_item"),
    ]
