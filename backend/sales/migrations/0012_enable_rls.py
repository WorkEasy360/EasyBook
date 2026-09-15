from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0011_customerpayment_paymentallocation_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("sales_customerpayment"),
        enable_rls_org_scoped("sales_paymentallocation"),
    ]
