from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0009_invoice_invoiceline_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("sales_invoice"),
        enable_rls_org_scoped("sales_invoiceline"),
    ]
