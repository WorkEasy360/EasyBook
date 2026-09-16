from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("automation", "0003_automationevent"),
    ]

    operations = [
        enable_rls_org_scoped("automation_automationevent"),
    ]
