from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0016_recurringinvoicetemplate_recurringinvoicerun_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("sales_recurringinvoicetemplate"),
        enable_rls_org_scoped("sales_recurringinvoicetemplateline"),
        enable_rls_org_scoped("sales_recurringinvoicerun"),
    ]
