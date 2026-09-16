from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("automation", "0007_automationrule_cooldown_days_and_more"),
    ]

    operations = [
        enable_rls_org_scoped("automation_automationschedule"),
        enable_rls_org_scoped("automation_automationscheduleoccurrence"),
        enable_rls_org_scoped("automation_automationscanoccurrence"),
    ]
