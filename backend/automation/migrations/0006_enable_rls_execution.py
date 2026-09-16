from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("automation", "0005_automationexecution_automationnotification_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("automation_automationexecution"),
        enable_rls_org_scoped("automation_automationstepexecution"),
        enable_rls_org_scoped("automation_automationnotification"),
    ]
