from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0003_quote_quoteline_quote_sales_quote_organiz_f2f7fb_idx_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("sales_quote"),
        enable_rls_org_scoped("sales_quoteline"),
    ]
